import json
from dataclasses import replace

import httpx
import pytest
from test_bridge import message

from wechat_kf_memos.clients import WeChat
from wechat_kf_memos.render import note, note_tags, render
from wechat_kf_memos.store import Store
from wechat_kf_memos.tags import extract_tags, strip_tags


def test_tag_intent_and_history(settings):
    text = "记录 #工作/项目 #读书 #工作/项目，#science&tech #家庭👨‍👩‍👧‍👦 https://host/#fragment C#code \\#escaped ##heading # heading `#code`\n```\n#hidden\n```"
    assert extract_tags(text) == ["工作/项目", "读书", "science&tech", "家庭👨‍👩‍👧‍👦"]
    content = note(message(text={"content": text}), None, settings)
    assert content.endswith("#微信剪藏 #工作/项目 #读书 #science&tech #家庭👨‍👩‍👧‍👦")
    merged = message(
        kind="merged_msg",
        merged_msg={
            "item": [{"msgtype": "text", "msg_content": json.dumps({"text": {"content": "#别人的标签"}})}]
        },
    )
    content = note(merged, None, replace(settings, default_tags=(), chat_record_tag="归档/聊天"))
    assert content.endswith("#归档/聊天")
    assert "\\#别人的标签" in content


@pytest.mark.parametrize("tag", ["bad tag", "#bad", "", "a" * 101, "<html>"])
def test_invalid_tag_config(settings, tag):
    with pytest.raises(ValueError):
        replace(settings, chat_record_tag=tag).validate()


@pytest.mark.parametrize(
    "kind,data,expected",
    [
        ("text", {"content": "你好"}, "你好"),
        ("image", {"media_id": "img"}, "download:img:image"),
        ("voice", {"media_id": "voice"}, "download:voice:voice"),
        ("video", {"media_id": "vid"}, "download:vid:video"),
        ("file", {"media_id": "doc"}, "download:doc:file"),
        ("location", {"name": "会场", "latitude": 30, "longitude": 120}, "会场"),
        (
            "miniprogram",
            {"title": "小程序标题", "appid": "wx123", "pagepath": "page", "thumb_media_id": "thumb"},
            "download:thumb:image",
        ),
        ("channels_shop_product", {"title": "商品标题", "sales_price": 1234}, "1234"),
        (
            "channels_shop_order",
            {"order_id": "123", "product_titles": ["甲", "乙"], "state": "待付款"},
            "待付款",
        ),
        ("channels", {"sub_type": 2, "nickname": "作者", "title": "直播"}, "直播"),
        ("note", {}, "官方接口不提供笔记正文"),
        ("link", {"title": "链接", "url": "https://example.com/x"}, "<https://example.com/x>"),
    ],
)
def test_official_message_matrix(kind, data, expected):
    # Protocol bookkeeping must not pollute a user-facing note.
    data["future_field"] = "保留内容"
    content = render({"msgtype": kind, kind: data}, lambda mid, typ: f"download:{mid}:{typ}")
    assert expected in content and "future_field" not in content and "保留内容" not in content


def test_unknown_malformed_depth_and_safe_url():
    for msg in [{"msgtype": "future", "future": {"x": "```\n#secret"}}, {"msgtype": "text", "text": []}]:
        result = render(msg, None)
        assert "保留" in result and "```" in result
    msg = {"msgtype": "merged_msg", "merged_msg": {"item": []}}
    assert "保留原始内容" in render(msg, None, depth=8)
    assert "<javascript:" not in render({"msgtype": "link", "link": {"url": "javascript:alert(1)"}}, None)


def test_event_dedup_allowlist_and_atomic_cursor(settings):
    store = Store(settings.data_dir)
    store.signal("test-kf")
    events = [
        {
            "msgtype": "event",
            "event": {
                "event_type": kind,
                "external_userid": "owner",
                "open_kfid": "test-kf",
                "welcome_code": "short-lived-secret",
            },
        }
        for kind in ["enter_session", "user_recall_msg", "msg_send_fail", "future_event"]
    ]
    outsider = {
        "msgtype": "event",
        "event": {"event_type": "enter_session", "external_userid": "outsider", "open_kfid": "test-kf"},
    }
    store.page("test-kf", "cursor", events + events + [outsider], settings.allowed_users)
    with store.tx() as db:
        rows = db.execute("SELECT * FROM events").fetchall()
        assert len(rows) == 4 and all("short-lived-secret" not in r["payload"] for r in rows)
    assert store.due_job() is None
    with pytest.raises(ValueError):
        store.page("test-kf", "bad", [message(), {"msgtype": "event", "event": []}], settings.allowed_users)
    assert store.due_sync()["cursor"] == "cursor" and store.due_job() is None
    store.close()


def test_media_filename_refresh_and_json_file(settings):
    calls = []

    def remote(req):
        if req.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 7200})
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(200, json={"errcode": 42001})
        return httpx.Response(
            200,
            content=b'{"errcode":42}',
            headers={
                "content-type": "application/json",
                "content-disposition": 'attachment; filename="../data.json"',
            },
        )

    wc = WeChat(settings, httpx.Client(base_url="https://wechat", transport=httpx.MockTransport(remote)))
    assert wc.media("media") == (b'{"errcode":42}', "application/json", "data.json")
    wc.close()


def test_metadata_read_endpoints(settings):
    def remote(req):
        if req.url.path.endswith("gettoken"):
            return httpx.Response(200, json={"access_token": "fake", "expires_in": 7200})
        payload = json.loads(req.content)
        if req.url.path.endswith("account/list"):
            return httpx.Response(
                200,
                json={
                    "account_list": [{"open_kfid": str(i)} for i in range(100)]
                    if payload["offset"] == 0
                    else []
                },
            )
        assert payload == {"external_userid_list": ["owner"], "need_enter_session_context": 1}
        return httpx.Response(200, json={"customer_list": [{"external_userid": "owner"}]})

    wc = WeChat(settings, httpx.Client(base_url="https://wechat", transport=httpx.MockTransport(remote)))
    assert len(wc.accounts()) == 100
    assert wc.customers(["owner"])["customer_list"][0]["external_userid"] == "owner"
    with pytest.raises(ValueError):
        wc.customers(["stranger"])
    wc.close()


def test_init_env_generates_console_compatible_key(tmp_path, monkeypatch):
    import base64
    import sys

    from wechat_kf_memos.__main__ import main

    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("WECHAT_CALLBACK_TOKEN=\nWECHAT_ENCODING_AES_KEY=\n")
    monkeypatch.setattr(sys, "argv", ["bridge", "init-env"])
    keys = iter([b"\xff" * 32, b"a" * 32])
    monkeypatch.setattr("secrets.token_bytes", lambda size: next(keys))
    monkeypatch.setattr("secrets.token_hex", lambda size: "a" * (size * 2))
    main()
    values = dict(line.split("=", 1) for line in (tmp_path / ".env").read_text().splitlines())
    key = values["WECHAT_ENCODING_AES_KEY"]
    assert key.isalnum() and len(key) == 43 and len(base64.b64decode(key + "=")) == 32
    assert len(values["WECHAT_CALLBACK_TOKEN"]) == 32
    assert (tmp_path / ".env").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        # It may generate a new key first, but must never overwrite configuration.
        monkeypatch.setattr("secrets.token_bytes", lambda size: b"a" * 32)
        main()


def test_explicit_tags_keep_body_clean(settings):
    settings = replace(settings, memos_tag_mode="explicit")
    msg = message(text={"content": "测试内容 123456 #测试标签", "mentioned_list": []})
    assert note(msg, None, settings) == "测试内容 123456"
    assert note_tags(msg, settings) == ["微信剪藏", "测试标签"]
    assert note(message(text={"content": "#只有标签"}), None, settings) == ""
    assert (
        strip_tags("原文 #标签\n\nhttps://example.com/#fragment `#代码`\n保留下一段")
        == "原文\n\nhttps://example.com/#fragment `#代码`\n保留下一段"
    )
    assert strip_tags("#重复 内容 #重复") == "内容"
