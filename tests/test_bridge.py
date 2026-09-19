import base64
import hashlib
import json
import struct
import time
from dataclasses import replace

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from wechat_kf_memos.app import create_app
from wechat_kf_memos.clients import Memos, RemoteError, WeChat
from wechat_kf_memos.crypto import CallbackCrypto
from wechat_kf_memos.receipts import ClipFailure
from wechat_kf_memos.render import note
from wechat_kf_memos.store import Store
from wechat_kf_memos.worker import Worker


def envelope(s, plain, receiver=None, timestamp=None):
    key = base64.b64decode(s.aes_key + "=")
    data = b"r" * 16 + struct.pack("!I", len(plain)) + plain + (receiver or s.corp_id).encode()
    n = 32 - len(data) % 32
    encoder = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = base64.b64encode(encoder.update(data + bytes([n]) * n) + encoder.finalize()).decode()
    ts, nonce = str(timestamp or int(time.time())), "test-nonce"
    sig = hashlib.sha1("".join(sorted([s.callback_token, ts, nonce, encrypted])).encode()).hexdigest()
    return {"timestamp": ts, "nonce": nonce, "msg_signature": sig}, encrypted


def message(msgid="m1", user="owner", kind="text", **extra):
    return {
        "msgid": msgid,
        "external_userid": user,
        "open_kfid": "test-kf",
        "origin": 3,
        "send_time": 1700000000,
        "msgtype": kind,
        "text": {"content": "原文"},
        **extra,
    }


def test_official_crypto_vector():
    # Public example from https://developer.work.weixin.qq.com/document/path/90968
    crypto = CallbackCrypto("QDG6eK", "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C", "wx5823bf96d3bd56c7")
    cipher = "RypEvHKD8QQKFhvQ6QleEB4J58tiPdvo+rtK1I9qca6aM/wvqnLSV5zEPeusUiX5L5X/0lWfrf0QADHHhGd3QczcdCUpj911L3vg3W/sYYvuJTs3TUUkSUXxaccAS0qhxchrRYt66wiSpGLYL42aM6A8dTT+6k4aSknmPj48kzJs8qLjvd4Xgpue06DOdnLxAUHzM6+kDZ+HMZfJYuR+LtwGc2hgf5gsijff0ekUNXZiqATP7PF5mZxZ3Izoun1s4zG4LUMnvw2r+KqCKIw+3IQH03v+BCA9nMELNqbSf6tiWSrXJB3LAVGUcallcrw8V2t9EL4EhzJWrQUax5wLVMNS0+rUPA3k22Ncx4XXZS9o0MBH27Bo6BpNelZpS+/uh9KsNlY6bHCmJU9p8g7m3fVKn28H3KDYA5Pl/T8Z1ptDAVe0lXdQ2YoyyH2uyPIGHBZZIs2pDBS8R07+qN+E7Q=="
    result = crypto.decrypt("477715d11cdb4164915debcba66cb864d751f3e6", "1409659813", "1372623149", cipher)
    assert b"<![CDATA[hello]]>" in result


def test_get_verification_exact_echo_and_reject_forgery(settings):
    with TestClient(create_app(settings)) as client:
        q, cipher = envelope(settings, b"exact-no-newline")
        assert client.get("/wechat/callback", params={**q, "echostr": cipher}).content == b"exact-no-newline"
        q["msg_signature"] = "0" * 40
        assert client.get("/wechat/callback", params={**q, "echostr": cipher}).status_code == 403


@pytest.mark.parametrize("receiver,offset", [("wrong-corp", 0), (None, -3600)])
def test_reject_receiver_and_replay(settings, receiver, offset):
    with TestClient(create_app(settings)) as client:
        q, cipher = envelope(settings, b"test", receiver, int(time.time()) + offset)
        assert client.get("/wechat/callback", params={**q, "echostr": cipher}).status_code == 403


def test_verify_mode_never_silently_consumes_messages(settings):
    event = b"<xml><ToUserName>test-corp</ToUserName><Event>kf_msg_or_event</Event><OpenKfId>test-kf</OpenKfId></xml>"
    with TestClient(create_app(settings)) as client:
        q, cipher = envelope(settings, event)
        r = client.post("/wechat/callback", params=q, content=f"<xml><Encrypt>{cipher}</Encrypt></xml>")
        assert r.status_code == 503
        assert client.post("/wechat/callback", content=b"a" * (1024 * 1024 + 1)).status_code == 413
        assert (
            client.post(
                "/wechat/callback", content='<!DOCTYPE a [<!ENTITY x SYSTEM "file:///etc/passwd">]><a>&x;</a>'
            ).status_code
            == 400
        )


def test_store_atomic_cursor_allowlist_and_restart(settings):
    store = Store(settings.data_dir)
    store.signal("test-kf", "callback-token")
    store.page(
        "test-kf",
        "cursor-1",
        [message(), message(user="stranger", msgid="m2"), message()],
        settings.allowed_users,
    )
    store.close()
    store = Store(settings.data_dir)
    assert store.due_sync()["cursor"] == "cursor-1"
    assert store.due_job()["id"] == "m1"
    with pytest.raises(ValueError):
        store.page("test-kf", "bad-cursor", [message("m3"), message("")], settings.allowed_users)
    assert store.due_sync()["cursor"] == "cursor-1"
    with store.tx() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    store.done("m1", "memos/id")
    with store.tx() as db:
        assert db.execute("SELECT payload FROM jobs").fetchone()[0] is None
    store.close()


def test_callback_during_sync_is_not_lost(settings):
    store = Store(settings.data_dir)
    store.signal("test-kf")
    old = store.due_sync()
    store.signal("test-kf", "new-token")
    store.synced(old, 300)
    assert store.due_sync()["token"] == "new-token"
    store.close()


def test_empty_sync_page_with_has_more_continues(settings):
    class FakeWeChat:
        def sync(self, cursor, token):
            if not cursor:
                return {"msg_list": [], "next_cursor": "one", "has_more": 1}
            return {"msg_list": [message()], "next_cursor": "two", "has_more": 0}

    store = Store(settings.data_dir)
    store.signal("test-kf")
    Worker(settings, store, FakeWeChat(), object()).sync(store.due_sync())
    assert store.due_job()["id"] == "m1"
    assert store.due_sync() is None
    store.close()


def test_merged_text_media_and_untrusted_markdown():
    msg = message(
        kind="merged_msg",
        merged_msg={
            "title": "讨论",
            "item": [
                {
                    "sender_name": "Alice",
                    "send_time": 1700000000,
                    "msgtype": "text",
                    "msg_content": json.dumps(
                        {"msgtype": "text", "text": {"content": "<script>x</script>\n# 假标题"}}
                    ),
                },
                {
                    "sender_name": "Bob",
                    "send_time": 1700000001,
                    "msgtype": "image",
                    "msg_content": json.dumps({"msgtype": "image", "image": {"media_id": "media1"}}),
                },
                {"msg_content": "broken-json"},
            ],
        },
    )
    content = note(msg, lambda mid, kind: "![图片](/file/attachments/x/image.png)")
    assert content.index("Alice") < content.index("Bob")
    assert "2023-11-15 06:13:20" in content
    assert "<script>" not in content and "\\# 假标题" in content
    assert "/file/attachments/x/image.png" in content and "无法解析" in content


def test_lost_write_response_does_not_duplicate_note_or_attachment(settings):
    records, uploads = {}, []
    fail = [True]

    def remote(req):
        path = req.url.path.removeprefix("/api/v1/")
        if req.method == "GET":
            return httpx.Response(200, json=records[path]) if path in records else httpx.Response(404)
        body = json.loads(req.content)
        if req.method == "POST":
            uid = req.url.params.get("memoId") or req.url.params["attachmentId"]
            body["name"] = path + "/" + uid
            records[body["name"]] = body
            if path == "attachments":
                uploads.append(uid)
                if fail[0]:
                    fail[0] = False
                    raise httpx.ReadTimeout("simulated lost response")
            return httpx.Response(200, json=body)
        records[path].update(body)
        return httpx.Response(200, json=records[path])

    class FakeWeChat:
        def media(self, mid):
            return b"fake-image", "image/png"

    memos = Memos(settings, httpx.Client(base_url="http://memos", transport=httpx.MockTransport(remote)))
    store = Store(settings.data_dir)
    store.signal("test-kf")
    store.page(
        "test-kf", "cursor", [message(kind="image", image={"media_id": "media-1"})], settings.allowed_users
    )
    worker = Worker(settings, store, FakeWeChat(), memos)
    row = store.due_job()
    with pytest.raises(ClipFailure, match="Memos 保存附件：请求超时"):
        worker.clip(row)
    worker.clip(row)
    assert len(records) == 2 and len(uploads) == 1
    assert store.due_job() is None
    assert all(x.get("visibility", "PRIVATE") == "PRIVATE" for x in records.values())
    store.close()


def test_media_limit_and_token_refresh(settings):
    calls = []

    def remote(req):
        calls.append(req.url.path)
        if req.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 7200})
        if req.url.path.endswith("media/get"):
            return httpx.Response(200, content=b"123456", headers={"content-type": "image/png"})
        if calls.count("/cgi-bin/kf/sync_msg") == 1:
            return httpx.Response(200, json={"errcode": 42001})
        return httpx.Response(200, json={"next_cursor": "ok", "has_more": 0})

    wc = WeChat(
        replace(settings, max_media_bytes=5),
        httpx.Client(base_url="https://wechat", transport=httpx.MockTransport(remote)),
    )
    assert wc.sync("")["next_cursor"] == "ok"
    assert calls.count("/cgi-bin/gettoken") == 2
    with pytest.raises(RemoteError, match="media_too_large"):
        wc.media("media1")


def test_clip_mode_fails_closed_without_allowlist(settings):
    with pytest.raises(ValueError):
        replace(
            settings,
            mode="clip",
            secret="test",
            memos_url="http://memos",
            memos_token="test",
            allowed_users=frozenset(),
        ).validate()


def test_post_notification_is_durable_before_ack(settings, monkeypatch):
    import wechat_kf_memos.app as app_module

    class IdleWorker:
        def __init__(self, *args):
            pass

        def tick(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(app_module, "Worker", IdleWorker)
    configured = replace(settings, mode="clip", secret="test", memos_url="http://memos", memos_token="test")
    event = (
        b"<xml><ToUserName>test-corp</ToUserName><Event>kf_msg_or_event</Event>"
        b"<OpenKfId>test-kf</OpenKfId><Token>notice-token</Token></xml>"
    )
    with TestClient(create_app(configured)) as client:
        q, cipher = envelope(settings, event)
        assert (
            client.post("/wechat/callback", params=q, content=f"<xml><Encrypt>{cipher}</Encrypt></xml>").text
            == "success"
        )
    reopened = Store(settings.data_dir)
    assert reopened.due_sync()["token"] == "notice-token"
    reopened.close()


def test_failed_jobs_stop_retrying_but_preserve_content(settings):
    store = Store(settings.data_dir)
    store.signal("test-kf")
    store.page("test-kf", "cursor", [message()], settings.allowed_users)
    for _ in range(12):
        with store.tx() as db:
            db.execute("UPDATE jobs SET next_at=0")
        store.failed(store.due_job(), "RemoteError")
    assert store.due_job() is None
    with store.tx() as db:
        row = db.execute("SELECT * FROM jobs").fetchone()
        assert row["state"] == "failed" and json.loads(row["payload"])["text"]["content"] == "原文"
    store.close()


def test_verification_probe_get_only(settings):
    import runpy
    import threading
    from http.server import HTTPServer
    from pathlib import Path

    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts" / "verify_callback.py"))
    server = HTTPServer(
        ("127.0.0.1", 0),
        probe["handler"](CallbackCrypto(settings.callback_token, settings.aes_key, settings.corp_id)),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as client:
            q, cipher = envelope(settings, b"probe-exact-echo")
            assert (
                client.get("/wechat/callback", params={**q, "echostr": cipher}).content == b"probe-exact-echo"
            )
            assert client.get("/wechat/callback").status_code == 403
            assert client.post("/wechat/callback", content="ignored").status_code == 503
            assert client.get("/").status_code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
