import json
import logging
import re
import time

import httpx

from .clients import Memos, RemoteError, WeChat, stable_id
from .receipts import ClipFailure, label, limitations, operation, reason
from .render import note, note_tags

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, settings, store, wechat=None, memos=None):
        self.s, self.store = settings, store
        self.wechat = wechat or WeChat(settings)
        self.memos = memos or Memos(settings)
        self.store.recover_replies()

    def sync(self, row):
        cursor = row["cursor"]
        token = row["token"] if time.time() - row["token_time"] < 540 else ""
        # Cap each cycle for fairness; has_more (not message count) controls continuation.
        for _ in range(20):
            result = self.wechat.sync(cursor, token)
            next_cursor = result["next_cursor"]
            messages = result.get("msg_list", [])
            if not isinstance(messages, list) or not isinstance(next_cursor, str):
                raise TypeError("Invalid sync response")
            if result.get("has_more") and next_cursor == cursor:
                raise ValueError("Cursor did not advance")
            self.store.page(row["kf"], next_cursor, messages, self.s.allowed_users)
            cursor = next_cursor
            if not result.get("has_more"):
                self.store.synced(row, self.s.poll_seconds)
                return

    def clip(self, row):
        msg = json.loads(row["payload"])
        # Recheck after configuration changes: queued messages must still be authorized.
        if msg.get("external_userid") not in self.s.allowed_users or msg.get("open_kfid") != self.s.kf_id:
            self.store.done(row["id"], "ignored")
            return
        if msg.get("msgtype") == "note":
            reply = self.outcome(msg, "微信笔记保存失败：微信接口未提供笔记内容")
            self.store.done(row["id"], "unsupported:note", reply)
            return
        uid = stable_id(self.s.corp_id, self.s.kf_id, row["id"])
        memo = "memos/" + uid
        # First save the full text with honest pending markers, so media failure never hides the text.
        draft = note(msg, lambda _id, kind: f"[{kind}：附件处理中]", self.s)
        operation("Memos 创建笔记", lambda: self.memos.ensure_note(uid, draft, note_tags(msg, self.s)))
        self.store.draft_saved(row["id"], memo)

        def attach(media_id, kind):
            aid = stable_id(uid, media_id)
            attachment = operation(
                "Memos 保存附件",
                lambda: self.memos.attachment(
                    aid,
                    media_id,
                    kind,
                    lambda mid: operation(
                        "微信下载" + label({"msgtype": kind}), lambda: self.wechat.media(mid)
                    ),
                    memo,
                ),
            )
            return self.memos.reference(attachment, kind)

        content = note(msg, attach, self.s)
        operation("Memos 更新正文和标签", lambda: self.memos.finish(memo, content, note_tags(msg, self.s)))
        issues = limitations(msg)
        text = label(msg) + ("部分保存：" + "；".join(issues) if issues else "保存成功")
        self.store.done(row["id"], memo, self.outcome(msg, text))
        log.info("clip_completed")

    def tick(self):
        row = self.store.due_sync()
        if row:
            try:
                self.sync(row)
            except Exception as exc:  # noqa: BLE001 -- worker boundary: retry, sanitize, never expose tokens
                self.store.synced(row, 60)
                log.warning("sync_failed type=%s", type(exc).__name__)
        row = self.store.due_job()
        if row:
            try:
                self.clip(row)
            except Exception as exc:  # noqa: BLE001 -- worker boundary: persist failure and continue other jobs
                # Map only allowlisted codes and operation stages; never persist HTTP URLs.
                msg = json.loads(row["payload"])
                detail = exc.detail if isinstance(exc, ClipFailure) else reason(exc)
                with self.store.tx() as db:
                    saved = db.execute("SELECT memo FROM jobs WHERE id=?", (row["id"],)).fetchone()[0]
                prefix = "部分保存：笔记已创建，后续处理失败；" if saved else "保存失败："
                text = label(msg) + prefix + detail + f"，已尝试 {row['attempts'] + 1} 次"
                self.store.failed(row, detail, self.outcome(msg, text))
                log.warning("clip_failed type=%s", type(exc).__name__)

        if self.s.receipts_enabled:
            self.send_reply()

    def outcome(self, msg, text):
        if self.s.receipts_enabled:
            return (msg["external_userid"], text)
        return None

    def send_reply(self):
        row = self.store.reserve_reply()
        if not row:
            return
        if row["user"] not in self.s.allowed_users:
            self.store.reply_result(row, "cancelled", "sender_no_longer_authorized")
            return
        try:
            self.wechat.send_text(row["user"], row["id"], row["content"])
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            self.store.reply_result(row, "pending", reason(exc))
        except Exception as exc:  # noqa: BLE001 -- never expose HTTP URLs or credentials
            # Explicit API rejection is safe to retry within the existing quota/window.
            # Transport failures and crashes can have delivered a reply: keep them unknown.
            safe_retry = isinstance(exc, RemoteError) and re.fullmatch(r"wechat_api_\d+", str(exc))
            state = "pending" if safe_retry and row["attempts"] < 4 else "unknown"
            self.store.reply_result(row, state, reason(exc))
        else:
            self.store.reply_result(row, "sent")

    def close(self):
        self.wechat.close()
        self.memos.close()
