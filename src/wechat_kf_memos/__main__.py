import argparse
import json
import os
import secrets
import sqlite3
from pathlib import Path

from .clients import WeChat
from .config import Settings


def private_export(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.write("\n")
    print("Private export saved locally.")


def main():
    parser = argparse.ArgumentParser(
        description="Local bridge operations; never prints message bodies or secrets"
    )
    parser.add_argument(
        "command", choices=["init-env", "status", "retry", "discover-users", "export-info", "export-events"]
    )
    parser.add_argument("--job-id")
    parser.add_argument("--output", help="New private JSON export file; never overwritten")
    args = parser.parse_args()
    if args.command == "init-env":
        # Generate only local callback secrets, never print them or overwrite an existing configuration.
        sample = Path(".env.example").read_text()
        import base64

        # The independent KF console rejects + and / despite accepting an AES base64 key.
        while True:
            key = base64.b64encode(secrets.token_bytes(32)).decode().rstrip("=")
            if key.isalnum():
                break
        sample = sample.replace("WECHAT_CALLBACK_TOKEN=", "WECHAT_CALLBACK_TOKEN=" + secrets.token_hex(16))
        sample = sample.replace("WECHAT_ENCODING_AES_KEY=", "WECHAT_ENCODING_AES_KEY=" + key)
        fd = os.open(".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(sample)
        print("Created private .env; fill the remaining fields locally.")
        return
    s = Settings.from_env()
    if args.command == "export-info":
        if not args.output or not all((s.corp_id, s.secret)):
            parser.error("export-info requires --output, WECHAT_CORP_ID and WECHAT_SECRET")
        wc = WeChat(s)
        try:
            info = {"accounts": wc.accounts()}
            if s.allowed_users:
                info["customers"] = wc.customers(sorted(s.allowed_users))
            private_export(args.output, info)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"error_type": type(exc).__name__}))
            raise SystemExit(1) from None
        finally:
            wc.close()
        return
    if args.command == "discover-users":
        if not all((s.corp_id, s.secret, s.kf_id)):
            parser.error("Set WECHAT_CORP_ID, WECHAT_SECRET, WECHAT_KF_ID first")
        wc = WeChat(s)
        try:
            # Deliberately no cursor updates: normal ingestion can subsequently read these messages.
            cursor, users = "", set()
            for _ in range(20):
                data = wc.sync(cursor)
                users.update(
                    m["external_userid"]
                    for m in data.get("msg_list", [])
                    if m.get("origin") == 3 and m.get("external_userid") and m.get("open_kfid") == s.kf_id
                )
                cursor = data["next_cursor"]
                if not data.get("has_more"):
                    break
            print(
                json.dumps(
                    {
                        "candidate_external_userids": sorted(users),
                        "note": "Identify your own account; this does not authorize any sender.",
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001 -- CLI boundary deliberately redacts HTTP exception URLs
            print(json.dumps({"error_type": type(exc).__name__}))
            raise SystemExit(1) from None
        finally:
            wc.close()
        return
    path = s.data_dir / "state.db"
    if not path.exists():
        parser.error("State database does not exist; start the service first")
    with sqlite3.connect(path) as db:
        if args.command == "export-events":
            if not args.output:
                parser.error("export-events requires --output")
            private_export(
                args.output,
                [json.loads(row[0]) for row in db.execute("SELECT payload FROM events ORDER BY received_at")],
            )
        elif args.command == "status":
            print(
                json.dumps(
                    {
                        "replies": dict(db.execute("SELECT state,count(*) FROM replies GROUP BY state")),
                        "events": dict(
                            db.execute("SELECT event_type,count(*) FROM events GROUP BY event_type")
                        ),
                        "counts": dict(db.execute("SELECT state,count(*) FROM jobs GROUP BY state")),
                        "failed": [
                            dict(zip(("id", "attempts", "error"), row))
                            for row in db.execute(
                                "SELECT id,attempts,error FROM jobs WHERE state='failed' LIMIT 100"
                            )
                        ],
                    }
                )
            )
        else:
            if not args.job_id:
                parser.error("retry requires --job-id")
            result = db.execute(
                "UPDATE jobs SET state='pending',attempts=0,next_at=0 WHERE id=? AND state='failed'",
                (args.job_id,),
            )
            print(json.dumps({"requeued": result.rowcount}))


if __name__ == "__main__":
    main()
