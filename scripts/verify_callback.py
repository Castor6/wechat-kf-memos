"""Temporary GET-only probe. No database, message downloads, or Memos client.

Needs Python 3.12 and cryptography; set PYTHONPATH to the project's src directory.
Secrets are supplied by systemd EnvironmentFile, never command-line arguments.
"""

import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from wechat_kf_memos.crypto import CallbackCrypto


def handler(crypto):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            # BaseHTTPRequestHandler normally logs the full query string.
            pass

        def reply(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            url = urlsplit(self.path)
            if url.path != "/wechat/callback":
                self.reply(404, b"Not found")
                return
            try:
                if len(self.path) > 16384:
                    raise ValueError("Too large")
                q = parse_qs(url.query, strict_parsing=True)
                timestamp = q["timestamp"][0]
                if abs(time.time() - int(timestamp)) > 600:
                    raise ValueError("Expired")
                plain = crypto.decrypt(q["msg_signature"][0], timestamp, q["nonce"][0], q["echostr"][0])
            except (ValueError, KeyError):
                print("callback_verification_rejected", flush=True)
                self.reply(403, b"Invalid callback")
                return
            print("callback_verification_succeeded", flush=True)
            self.reply(200, plain)

        def do_POST(self):
            # Probe cannot process incoming messages. Never claim they were saved.
            self.reply(503, b"Verification-only service")

    return Handler


if __name__ == "__main__":
    crypto = CallbackCrypto(
        os.environ["WECHAT_CALLBACK_TOKEN"],
        os.environ["WECHAT_ENCODING_AES_KEY"],
        os.environ["WECHAT_CORP_ID"],
    )
    server = HTTPServer(("127.0.0.1", 18080), handler(crypto))
    print("verification_probe_listening", flush=True)
    server.serve_forever()
