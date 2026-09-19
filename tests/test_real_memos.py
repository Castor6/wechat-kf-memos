"""Opt-in contract test: builds no software and NEVER connects to a supplied remote URL."""

import base64
import json
import os
import secrets
import socket
import subprocess
import time
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from test_bridge import message

from wechat_kf_memos.clients import Memos, stable_id
from wechat_kf_memos.store import Store
from wechat_kf_memos.worker import Worker


@pytest.mark.skipif(
    not os.getenv("TEST_MEMOS_BINARY"), reason="Set TEST_MEMOS_BINARY to a local Memos executable"
)
@pytest.mark.parametrize("tag_mode", ["markdown", "explicit"])
def test_real_memos_contract(tmp_path, settings, tag_mode):
    binary = Path(os.environ["TEST_MEMOS_BINARY"]).resolve(strict=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    data = tmp_path / "memos"
    data.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("MEMOS_")}
    base = f"http://127.0.0.1:{port}"
    with (tmp_path / "memos.log").open("w") as log:
        process = subprocess.Popen(
            [
                str(binary),
                "--addr",
                "127.0.0.1",
                "--port",
                str(port),
                "--driver",
                "sqlite",
                "--data",
                str(data),
            ],
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=10) as http:
                for _ in range(100):
                    assert process.poll() is None, "Disposable Memos exited during startup"
                    try:
                        if http.get("/").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("Disposable Memos did not start")
                password = secrets.token_urlsafe(32)
                response = http.post("/api/v1/users", json={"username": "bridge-test", "password": password})
                assert response.status_code == 200, response.status_code
                response = http.post(
                    "/api/v1/auth/signin",
                    json={"passwordCredentials": {"username": "bridge-test", "password": password}},
                )
                assert response.status_code == 200
                http.headers["Authorization"] = "Bearer " + response.json()["accessToken"]
                response = http.post(
                    "/api/v1/users/bridge-test/personalAccessTokens",
                    json={"description": "Disposable bridge test", "expiresInDays": 1},
                )
                assert response.status_code == 200, response.status_code
                configured = replace(
                    settings,
                    memos_url=base,
                    memos_tag_mode=tag_mode,
                    memos_token=response.json()["token"],
                    data_dir=tmp_path / "bridge",
                )
                memos = Memos(configured)
                store = Store(configured.data_dir)
                # Valid 1px PNG, no real user content.
                png = base64.b64decode(
                    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aXioAAAAASUVORK5CYII="
                )

                class FakeWeChat:
                    def media(self, media_id):
                        return png, "image/png", "original.png"

                worker = Worker(configured, store, FakeWeChat(), memos)
                messages = [
                    message("tags", text={"content": "测试 #工作/项目 #science&tech #标签🚀"}),
                    message(
                        "history",
                        kind="merged_msg",
                        merged_msg={
                            "title": "测试聊天",
                            "item": [
                                {
                                    "sender_name": "Alice",
                                    "msgtype": "text",
                                    "msg_content": json.dumps({"text": {"content": "#历史标签"}}),
                                },
                                {
                                    "sender_name": "Bob",
                                    "msgtype": "image",
                                    "msg_content": json.dumps({"image": {"media_id": "image1"}}),
                                },
                            ],
                        },
                    ),
                ]
                store.signal("test-kf")
                worker.wechat.sync = lambda cursor, token: {
                    "next_cursor": "cursor",
                    "has_more": 0,
                    "msg_list": messages,
                }
                worker.sync(store.due_sync())
                for _ in messages:
                    row = store.due_job()
                    worker.clip(row)
                    worker.clip(row)  # crash/retry simulation with the original payload
                assert store.due_job() is None
                for source in messages:
                    name = "memos/" + stable_id(configured.corp_id, configured.kf_id, source["msgid"])
                    memo = memos.get(name)
                    assert memo["visibility"] == "PRIVATE"
                    assert memo.get("space", "") == ""
                    if tag_mode == "explicit":
                        assert memo["explicitTags"] is True
                        assert not memo["content"].startswith("# 微信剪藏")
                    tags = set(memo["tags"])
                    if source["msgid"] == "tags":
                        if tag_mode == "explicit":
                            assert memo["content"] == "测试"
                        assert tags == {"微信剪藏", "工作/项目", "science&tech", "标签🚀"}, tags
                    else:
                        assert tags == {"微信剪藏", "微信聊天记录"}, tags
                        assert len(memo["attachments"]) == 1
                        attachment = memo["attachments"][0]
                        assert attachment["memo"] == name and attachment["filename"] == "original.png"
                        path = "/file/" + attachment["name"] + "/original.png"
                        assert http.get(path).content == png
                        with httpx.Client(base_url=base, trust_env=False) as anon:
                            assert anon.get(path).status_code in (401, 403, 404)
                assert len(http.get("/api/v1/memos").json()["memos"]) == 2
                assert len(http.get("/api/v1/attachments").json()["attachments"]) == 1
                store.close()
                memos.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
