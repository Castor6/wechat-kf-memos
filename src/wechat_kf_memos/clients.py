import base64
import hashlib
import json
import mimetypes
import ssl
import time
from email.message import Message
from urllib.parse import quote

import httpx


class RemoteError(Exception):
    """Only a sanitized code, never response bodies, URLs, or credentials."""


def safe_filename(value):
    name = str(value).replace("\\", "/").split("/")[-1]
    name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip(" .")
    # Bound the UTF-8 bytes for common filesystem limits, preserving normal extensions.
    return name if len(name.encode()) <= 240 else ""


def stable_id(*parts):
    return "wkf-" + hashlib.sha256("\0".join(parts).encode()).hexdigest()[:32]


class WeChat:
    def __init__(self, settings, client=None):
        self.s = settings
        self.http = client or httpx.Client(
            base_url="https://qyapi.weixin.qq.com", timeout=30, trust_env=False
        )
        self.token, self.expires = "", 0

    def access_token(self):
        if time.time() >= self.expires:
            response = self.http.get(
                "/cgi-bin/gettoken", params={"corpid": self.s.corp_id, "corpsecret": self.s.secret}
            )
            result = self.decode(response)
            self.token = result["access_token"]
            self.expires = time.time() + max(1, int(result["expires_in"]) - 120)
        return self.token

    @staticmethod
    def decode(response):
        if not response.is_success:
            raise RemoteError(f"wechat_http_{response.status_code}")
        result = response.json()
        if result.get("errcode", 0):
            raise RemoteError(f"wechat_api_{int(result['errcode'])}")
        return result

    def sync(self, cursor, token=""):
        payload = {"cursor": cursor, "open_kfid": self.s.kf_id, "limit": 100, "voice_format": 0}
        if token:
            payload["token"] = token
        return self.request("/cgi-bin/kf/sync_msg", payload)

    def request(self, path, payload):
        for attempt in range(2):
            response = self.http.post(path, params={"access_token": self.access_token()}, json=payload)
            try:
                return self.decode(response)
            except RemoteError as exc:
                if attempt == 0 and str(exc) in {"wechat_api_40014", "wechat_api_42001"}:
                    self.expires = 0
                    continue
                raise

    def send_text(self, user, msgid, content):
        if user not in self.s.allowed_users:
            raise ValueError("Reply recipient is not authorized")
        return self.request(
            "/cgi-bin/kf/send_msg",
            {
                "touser": user,
                "open_kfid": self.s.kf_id,
                "msgid": msgid,
                "msgtype": "text",
                "text": {"content": content},
            },
        )

    def accounts(self):
        accounts = []
        for offset in range(0, 5000, 100):
            page = self.request("/cgi-bin/kf/account/list", {"offset": offset, "limit": 100})
            batch = page["account_list"]
            accounts.extend(batch)
            if len(batch) < 100:
                return accounts
        return accounts

    def customers(self, user_ids):
        # Call only for explicitly authorized senders; access may expire after 48 hours.
        if not user_ids or not set(user_ids) <= self.s.allowed_users:
            raise ValueError("Customer lookup requires authorized senders")
        return self.request(
            "/cgi-bin/kf/customer/batchget",
            {
                "external_userid_list": list(user_ids),
                "need_enter_session_context": 1,
            },
        )

    def media(self, media_id):
        for attempt in range(2):
            try:
                return self._media(media_id)
            except RemoteError as exc:
                if attempt == 0 and str(exc) in {"wechat_media_api_40014", "wechat_media_api_42001"}:
                    self.expires = 0
                    continue
                raise

    def _media(self, media_id):
        # No redirects or arbitrary URLs: only Tencent's documented download endpoint.
        with self.http.stream(
            "GET", "/cgi-bin/media/get", params={"access_token": self.access_token(), "media_id": media_id}
        ) as response:
            if not response.is_success:
                raise RemoteError(f"wechat_media_http_{response.status_code}")
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > self.s.max_media_bytes:
                    raise RemoteError("media_too_large")
                data.extend(chunk)
            mime = response.headers.get("content-type", "application/octet-stream").split(";")[0]
            header = Message()
            header["Content-Disposition"] = response.headers.get("content-disposition", "")
            filename = header.get_filename() or ""
            if "json" in mime and not filename:
                result = json.loads(data)
                if result.get("errcode"):
                    if result["errcode"] in {40014, 42001}:
                        self.expires = 0
                    raise RemoteError(f"wechat_media_api_{int(result['errcode'])}")
            return bytes(data), mime, safe_filename(filename)

    def close(self):
        self.http.close()


class Memos:
    def __init__(self, settings, client=None):
        self.s = settings
        verify = ssl.create_default_context(cafile=settings.ca_file) if settings.ca_file else True
        self.http = client or httpx.Client(
            base_url=settings.memos_url,
            timeout=60,
            verify=verify,
            headers={"Authorization": f"Bearer {settings.memos_token}"},
            trust_env=False,
        )

    def get(self, name):
        response = self.http.get("/api/v1/" + name)
        if response.status_code == 404:
            return None
        return self.decode(response)

    @staticmethod
    def decode(response):
        if not response.is_success:
            raise RemoteError(f"memos_http_{response.status_code}")
        return response.json()

    def ensure_note(self, uid, content, tags=()):
        existing = self.get("memos/" + uid)
        if existing:
            if existing.get("visibility") != "PRIVATE":
                raise RemoteError("existing_note_not_private")
            return existing
        # Client-supplied IDs reconcile a successful remote write followed by a lost response/crash.
        response = self.http.post(
            "/api/v1/memos",
            params={"memoId": uid},
            json={"content": content, "visibility": "PRIVATE", **self.tag_fields(tags)},
        )
        return self.decode(response)

    def attachment(self, uid, media_id, kind, download, memo):
        existing = self.get("attachments/" + uid)
        if existing:
            if existing.get("memo") != memo:
                raise RemoteError("attachment_owner_mismatch")
            return existing
        downloaded = download(media_id)
        data, mime = downloaded[:2]
        filename = safe_filename(downloaded[2]) if len(downloaded) > 2 else ""
        extension = mimetypes.guess_extension(mime) or {
            "image": ".img",
            "voice": ".amr",
            "video": ".mp4",
        }.get(kind, ".bin")
        response = self.http.post(
            "/api/v1/attachments",
            params={"attachmentId": uid},
            json={
                "filename": filename or uid + extension,
                "type": mime,
                "content": base64.b64encode(data).decode(),
                "memo": memo,
            },
        )
        return self.decode(response)

    def tag_fields(self, tags):
        if self.s.memos_tag_mode == "explicit":
            return {"tags": list(tags), "explicitTags": True}
        return {}

    def finish(self, name, content, tags=()):
        response = self.http.patch(
            "/api/v1/" + name,
            params={
                "updateMask": "content,visibility,tags"
                if self.s.memos_tag_mode == "explicit"
                else "content,visibility"
            },
            json={"name": name, "content": content, "visibility": "PRIVATE", **self.tag_fields(tags)},
        )
        return self.decode(response)

    def reference(self, attachment, kind):
        path = f"{self.s.public_url}/file/{attachment['name']}/{quote(attachment['filename'], safe='')}"
        label = {"image": "图片", "voice": "语音", "video": "视频", "file": "文件"}[kind]
        return f"{'!' if kind == 'image' else ''}[{label}]({path})"

    def close(self):
        self.http.close()
