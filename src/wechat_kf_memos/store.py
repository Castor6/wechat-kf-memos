import hashlib
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(directory / "state.db", check_same_thread=False)
        os.chmod(directory / "state.db", 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS sync (
                kf TEXT PRIMARY KEY, cursor TEXT NOT NULL DEFAULT '',
                token TEXT NOT NULL DEFAULT '', token_time REAL NOT NULL DEFAULT 0,
                generation INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS reply_windows (
                user TEXT PRIMARY KEY, latest REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS replies (
                id TEXT PRIMARY KEY, user TEXT NOT NULL, content TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                next_at REAL NOT NULL DEFAULT 0, created REAL NOT NULL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload TEXT NOT NULL,
                received_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, payload TEXT, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, next_at REAL NOT NULL DEFAULT 0,
                error TEXT, memo TEXT
            );
        """)

    @contextmanager
    def tx(self):
        with self.lock, self.db:
            yield self.db

    def close(self):
        with self.lock:
            self.db.close()

    def signal(self, kf: str, token: str = ""):
        with self.tx() as db:
            db.execute(
                """INSERT INTO sync(kf, token, token_time) VALUES(?,?,?)
                ON CONFLICT(kf) DO UPDATE SET token=excluded.token,token_time=excluded.token_time,
                generation=generation+1,next_at=0""",
                (kf, token, time.time()),
            )

    def due_sync(self):
        with self.tx() as db:
            return db.execute("SELECT * FROM sync WHERE next_at<=? LIMIT 1", (time.time(),)).fetchone()

    def page(self, kf: str, cursor: str, messages: list, allowed_users: frozenset):
        # Queue authorized content and advance its cursor in the SAME transaction.
        with self.tx() as db:
            # Events are diagnostics, not new clips; retain at most 30 days.
            db.execute("DELETE FROM events WHERE received_at<?", (time.time() - 30 * 86400,))
            for msg in messages:
                if msg.get("msgtype") == "event":
                    event = msg.get("event", {})
                    if not isinstance(event, dict):
                        raise ValueError("Invalid event")
                    if event.get("external_userid") in allowed_users and event.get("open_kfid") == kf:
                        # welcome_code is an outbound capability, not archived conversation content.
                        clean = {k: v for k, v in event.items() if k != "welcome_code"}
                        payload = json.dumps({**msg, "event": clean}, ensure_ascii=False, sort_keys=True)
                        event_id = msg.get("msgid") or hashlib.sha256(payload.encode()).hexdigest()
                        db.execute(
                            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
                            (event_id, str(event.get("event_type", "unknown")), payload, time.time()),
                        )
                        if event.get("event_type") == "msg_send_fail":
                            db.execute(
                                "UPDATE replies SET state='delivery_failed',error=? WHERE id=? AND user=?",
                                (
                                    "wechat_fail_type_" + str(int(event.get("fail_type", 0))),
                                    event.get("fail_msgid", ""),
                                    event["external_userid"],
                                ),
                            )
                    continue
                if (
                    msg.get("origin") == 3
                    and msg.get("msgtype") != "event"
                    and msg.get("external_userid") in allowed_users
                    and msg.get("open_kfid") == kf
                ):
                    if not isinstance(msg.get("msgid"), str) or not msg["msgid"]:
                        raise ValueError("Missing message ID")
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO jobs(id,payload) VALUES(?,?)",
                        (msg["msgid"], json.dumps(msg, ensure_ascii=False)),
                    )
                    if inserted.rowcount:
                        db.execute(
                            """INSERT INTO reply_windows(user,latest) VALUES(?,?)
                            ON CONFLICT(user) DO UPDATE SET used=CASE WHEN excluded.latest >= latest THEN 0 ELSE used END,
                            latest=max(latest,excluded.latest)""",
                            (msg["external_userid"], msg.get("send_time", 0)),
                        )

            db.execute("UPDATE sync SET cursor=? WHERE kf=?", (cursor, kf))

    def synced(self, row, delay: int):
        with self.tx() as db:
            db.execute(
                "UPDATE sync SET next_at=? WHERE kf=? AND generation=?",
                (time.time() + delay, row["kf"], row["generation"]),
            )

    def due_job(self):
        with self.tx() as db:
            return db.execute(
                "SELECT * FROM jobs WHERE state='pending' AND next_at<=? LIMIT 1", (time.time(),)
            ).fetchone()

    def done(self, job_id: str, memo: str, reply=None):
        with self.tx() as db:
            db.execute(
                "UPDATE jobs SET state='done',payload=NULL,error=NULL,memo=? WHERE id=?", (memo, job_id)
            )
            if reply:
                self._reply(db, job_id, *reply)

    def failed(self, row, error: str, reply=None):
        attempts = row["attempts"] + 1
        with self.tx() as db:
            db.execute(
                "UPDATE jobs SET attempts=?,state=?,next_at=?,error=? WHERE id=?",
                (
                    attempts,
                    "failed" if attempts >= 12 else "pending",
                    time.time() + min(3600, 10 * 2 ** min(attempts, 9)),
                    error,
                    row["id"],
                ),
            )

            if attempts >= 12 and reply:
                self._reply(db, row["id"], *reply)

    def draft_saved(self, job_id, memo):
        with self.tx() as db:
            db.execute("UPDATE jobs SET memo=? WHERE id=?", (memo, job_id))

    @staticmethod
    def _reply(db, job_id, user, content):
        # Tencent IDs must be <=32 bytes; independent of the 36-byte Memos IDs.
        rid = hashlib.sha256(("result:" + job_id).encode()).hexdigest()[:32]
        db.execute(
            "INSERT OR IGNORE INTO replies(id,user,content,created) VALUES(?,?,?,?)",
            (rid, user, content, time.time()),
        )

    def recover_replies(self):
        # A crash after dispatch may have sent the reply. Do not blindly send it twice.
        with self.tx() as db:
            db.execute(
                "UPDATE replies SET state='unknown',error='process_interrupted_during_send' WHERE state='sending'"
            )

    def reserve_reply(self):
        with self.tx() as db:
            db.execute(
                """UPDATE replies SET state='expired',error='reply_window_expired'
                WHERE state='pending' AND (created<? OR user IN (SELECT user FROM reply_windows WHERE latest<?))""",
                (time.time() - 48 * 3600, time.time() - 48 * 3600),
            )
            row = db.execute(
                """SELECT r.* FROM replies r JOIN reply_windows w ON r.user=w.user
                WHERE r.state='pending' AND r.next_at<=? AND w.used<5 AND w.latest>?
                ORDER BY r.created LIMIT 1""",
                (time.time(), time.time() - 48 * 3600),
            ).fetchone()
            if row:
                db.execute("UPDATE reply_windows SET used=used+1 WHERE user=?", (row["user"],))
                db.execute("UPDATE replies SET state='sending',attempts=attempts+1 WHERE id=?", (row["id"],))
            return row

    def reply_result(self, row, state, error=None):
        with self.tx() as db:
            db.execute(
                "UPDATE replies SET state=?,error=?,next_at=? WHERE id=?",
                (state, error, time.time() + min(3600, 60 * 2 ** min(row["attempts"], 6)), row["id"]),
            )
