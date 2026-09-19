import base64

import pytest

from wechat_kf_memos.config import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(
        "test-corp",
        "test-token",
        base64.b64encode(b"a" * 32).decode().rstrip("="),
        data_dir=tmp_path,
        kf_id="test-kf",
        allowed_users=frozenset({"owner"}),
    )
