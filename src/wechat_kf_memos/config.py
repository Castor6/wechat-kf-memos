import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .tags import valid_tag


@dataclass(frozen=True)
class Settings:
    corp_id: str
    callback_token: str = field(repr=False)
    aes_key: str = field(repr=False)
    mode: str = "verify"
    secret: str = field(default="", repr=False)
    kf_id: str = ""
    allowed_users: frozenset[str] = frozenset()
    memos_url: str = ""
    memos_token: str = field(default="", repr=False)
    public_url: str = ""
    ca_file: str = ""
    data_dir: Path = Path("data")
    poll_seconds: int = 300
    max_media_bytes: int = 20 * 1024 * 1024

    default_tags: tuple[str, ...] = ("微信剪藏",)
    chat_record_tag: str = "微信聊天记录"
    memos_tag_mode: str = "markdown"
    receipts_enabled: bool = False

    def validate(self):
        if self.memos_tag_mode not in {"markdown", "explicit"}:
            raise ValueError("MEMOS_TAG_MODE must be markdown or explicit")
        if not self.chat_record_tag or not all(
            valid_tag(t) for t in (*self.default_tags, self.chat_record_tag)
        ):
            raise ValueError("Invalid default tag: use 1-100 Memos tag characters without #")
        if self.mode not in {"verify", "clip"}:
            raise ValueError("MODE must be verify or clip")
        if not all((self.corp_id, self.callback_token, self.aes_key)):
            raise ValueError("Missing callback configuration")
        if self.mode == "clip":
            if not all((self.secret, self.kf_id, self.allowed_users, self.memos_url, self.memos_token)):
                raise ValueError("clip mode requires KF, sender allowlist, and Memos configuration")
            parsed = urlparse(self.memos_url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
                raise ValueError("Invalid MEMOS_URL")
        if self.poll_seconds < 60 or not 1 <= self.max_media_bytes <= 20 * 1024 * 1024:
            raise ValueError("Invalid poll interval or media limit")

    @classmethod
    def from_env(cls):
        return cls(
            receipts_enabled=os.getenv("RECEIPTS_ENABLED", "false").lower() == "true",
            memos_tag_mode=os.getenv("MEMOS_TAG_MODE", "markdown"),
            corp_id=os.getenv("WECHAT_CORP_ID", ""),
            callback_token=os.getenv("WECHAT_CALLBACK_TOKEN", ""),
            aes_key=os.getenv("WECHAT_ENCODING_AES_KEY", ""),
            mode=os.getenv("MODE", "verify"),
            secret=os.getenv("WECHAT_SECRET", ""),
            kf_id=os.getenv("WECHAT_KF_ID", ""),
            allowed_users=frozenset(
                x.strip() for x in os.getenv("WECHAT_ALLOWED_USERS", "").split(",") if x.strip()
            ),
            memos_url=os.getenv("MEMOS_URL", "").rstrip("/"),
            memos_token=os.getenv("MEMOS_TOKEN", ""),
            public_url=os.getenv("MEMOS_PUBLIC_URL", "").rstrip("/"),
            ca_file=os.getenv("MEMOS_CA_FILE", ""),
            data_dir=Path(os.getenv("DATA_DIR", "data")),
            poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
            default_tags=tuple(
                t.strip() for t in os.getenv("DEFAULT_TAGS", "微信剪藏").split(",") if t.strip()
            ),
            chat_record_tag=os.getenv("CHAT_RECORD_TAG", "微信聊天记录").strip(),
            max_media_bytes=int(os.getenv("MAX_MEDIA_BYTES", str(20 * 1024 * 1024))),
        )
