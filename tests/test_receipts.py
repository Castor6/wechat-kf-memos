import json
import time
from dataclasses import replace

import httpx
import pytest
from test_bridge import message

from wechat_kf_memos.clients import RemoteError, WeChat
from wechat_kf_memos.receipts import limitations, reason
from wechat_kf_memos.store import Store
from wechat_kf_memos.worker import Worker


class FakeMemos:
    def __init__(self):
        self.notes = {}
        self.failure = None

    def ensure_note(self, uid, content, tags):
        if self.failure:
            raise self.failure
        self.notes.setdefault("memos/" + uid, content)

    def finish(self, name, content, tags):
        self.notes[name] = content


class FakeWeChat:
    def __init__(self):
        self.sent = []
        self.failure = None

    def send_text(self, user, mid, content):
        self.sent.append((user, mid, content))
        if self.failure:
            raise self.failure


@pytest.fixture
def rig(settings):
    store = Store(settings.data_dir)
    wc, memos = FakeWeChat(), FakeMemos()
    worker = Worker(replace(settings, receipts_enabled=True), store, wc, memos)
    store.signal("test-kf")
    store.synced(store.due_sync(), 3600)
    yield store, wc, memos, worker
    store.close()


def enqueue(store, msg):
    store.page("test-kf", "cursor", [dict(msg, send_time=int(time.time()))], frozenset({"owner"}))


def test_one_success_and_duplicate_ingestion(rig):
    store, wc, memos, worker = rig
    enqueue(store, message())
    row = store.due_job()
    worker.clip(row)
    worker.clip(row)
    enqueue(store, message())
    worker.send_reply()
    worker.send_reply()
    assert len(memos.notes) == 1 and len(wc.sent) == 1
    assert wc.sent[0][2] == "文字保存成功" and len(wc.sent[0][1]) == 32
    assert store.due_job() is None


def test_note_never_creates_memo(rig):
    store, wc, memos, worker = rig
    enqueue(store, message(kind="note"))
    worker.tick()
    assert not memos.notes
    assert wc.sent[0][2] == "微信笔记保存失败：微信接口未提供笔记内容"


def test_terminal_failure_only_and_sanitized_reason(rig):
    store, wc, memos, worker = rig
    memos.failure = RemoteError("memos_http_401")
    enqueue(store, message())
    for i in range(12):
        with store.tx() as db:
            db.execute("UPDATE jobs SET next_at=0")
        worker.tick()
        assert len(wc.sent) == (1 if i == 11 else 0)
    assert wc.sent[0][2] == "文字保存失败：Memos 创建笔记：认证失败，HTTP 401，已尝试 12 次"
    assert "SECRET" not in reason(httpx.ReadTimeout("https://example/?access_token=SECRET"))
    assert "SECRET" not in reason(RemoteError("unsafe_SECRET"))


def test_ambiguous_delivery_and_crash_do_not_resend(rig):
    store, wc, memos, worker = rig
    enqueue(store, message())
    wc.failure = httpx.ReadTimeout("response lost")
    worker.tick()
    worker.tick()
    assert len(wc.sent) == 1 and len(memos.notes) == 1
    with store.tx() as db:
        assert db.execute("SELECT state FROM replies").fetchone()[0] == "unknown"
        db.execute("UPDATE replies SET state='sending'")
    store.recover_replies()
    worker.tick()
    assert len(wc.sent) == 1


def test_quota_window_duplicate_does_not_reset(rig):
    store, wc, _, worker = rig
    for i in range(7):
        enqueue(store, message(str(i)))
        worker.clip(store.due_job())
    for _ in range(7):
        worker.send_reply()
    assert len(wc.sent) == 5
    enqueue(store, message("6"))
    worker.send_reply()
    assert len(wc.sent) == 5
    enqueue(store, message("new-input"))
    worker.send_reply()
    assert len(wc.sent) == 6
    with store.tx() as db:
        db.execute("UPDATE reply_windows SET latest=0")
    worker.send_reply()
    assert len(wc.sent) == 6
    with store.tx() as db:
        assert db.execute("SELECT count(*) FROM replies WHERE state='expired'").fetchone()[0] == 1


def test_delivery_failure_event_recorded(rig):
    store, wc, _, worker = rig
    enqueue(store, message())
    worker.tick()
    event = {
        "msgtype": "event",
        "event": {
            "event_type": "msg_send_fail",
            "external_userid": "owner",
            "open_kfid": "test-kf",
            "fail_msgid": wc.sent[0][1],
            "fail_type": 10,
        },
    }
    store.page("test-kf", "next", [event], frozenset({"owner"}))
    with store.tx() as db:
        row = db.execute("SELECT state,error FROM replies").fetchone()
        assert tuple(row) == ("delivery_failed", "wechat_fail_type_10")


def test_nested_unsupported_is_partial():
    m = message(
        kind="merged_msg",
        merged_msg={"item": [{"msgtype": "note", "msg_content": json.dumps({"msgtype": "note"})}]},
    )
    assert limitations(m) == ["微信接口未提供微信笔记内容"]


def test_send_endpoint_and_allowlist(settings):
    def remote(req):
        if req.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 7200})
        assert req.url.path == "/cgi-bin/kf/send_msg"
        assert json.loads(req.content) == {
            "touser": "owner",
            "open_kfid": "test-kf",
            "msgid": "r" * 32,
            "msgtype": "text",
            "text": {"content": "图片保存成功"},
        }
        return httpx.Response(200, json={"errcode": 0, "msgid": "r" * 32})

    wc = WeChat(settings, httpx.Client(base_url="https://wechat", transport=httpx.MockTransport(remote)))
    assert wc.send_text("owner", "r" * 32, "图片保存成功")["errcode"] == 0
    with pytest.raises(ValueError):
        wc.send_text("stranger", "r" * 32, "图片保存成功")
    wc.close()


def test_partial_media_failure_keeps_specific_wechat_cause(rig):
    store, wc, memos, worker = rig

    def missing_media(*args):
        raise RemoteError("wechat_media_api_40007")

    wc.media = missing_media
    memos.attachment = lambda uid, mid, kind, download, memo: download(mid)
    enqueue(store, message(kind="image", image={"media_id": "media"}))
    for _ in range(12):
        with store.tx() as db:
            db.execute("UPDATE jobs SET next_at=0")
        worker.tick()
    assert len(memos.notes) == 1 and len(wc.sent) == 1
    assert (
        wc.sent[0][2]
        == "图片部分保存：笔记已创建，后续处理失败；微信下载图片：微信错误码 40007，已尝试 12 次"
    )


def test_disabled_receipts_do_not_queue_messages(settings):
    store = Store(settings.data_dir)
    worker = Worker(settings, store, FakeWeChat(), FakeMemos())
    enqueue(store, message())
    worker.clip(store.due_job())
    with store.tx() as db:
        assert db.execute("SELECT count(*) FROM replies").fetchone()[0] == 0
    store.close()
