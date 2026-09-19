import asyncio
import fcntl
import logging
import time
from contextlib import asynccontextmanager
from xml.etree.ElementTree import ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .config import Settings
from .crypto import CallbackCrypto
from .store import Store
from .worker import Worker

log = logging.getLogger(__name__)
MAX_CALLBACK = 1024 * 1024


def create_app(settings=None):
    s = settings or Settings.from_env()
    s.validate()
    crypto = CallbackCrypto(s.callback_token, s.aes_key, s.corp_id)

    @asynccontextmanager
    async def lifespan(app):
        s.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock = (s.data_dir / "process.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("Only one bridge process may use this data directory") from None
        store = Store(s.data_dir)
        app.state.store = store
        worker = Worker(s, store) if s.mode == "clip" else None
        stop = asyncio.Event()

        async def run():
            while not stop.is_set():
                await asyncio.to_thread(worker.tick)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1)
                except TimeoutError:
                    pass

        task = None
        if worker:
            store.signal(s.kf_id)
            task = asyncio.create_task(run())
        try:
            yield
        finally:
            stop.set()
            if task:
                await task
            if worker:
                worker.close()
            store.close()
            lock.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def health():
        # Liveness only, not proof of remote connectivity or successful clipping.
        return {"status": "ok", "mode": s.mode}

    def decrypt(request, encrypted):
        q = request.query_params
        try:
            if any(len(q.get(k, "")) > 256 for k in ("msg_signature", "timestamp", "nonce")):
                raise ValueError()
            if abs(time.time() - int(q["timestamp"])) > 600:
                raise ValueError()
            return crypto.decrypt(q["msg_signature"], q["timestamp"], q["nonce"], encrypted)
        except (ValueError, KeyError):
            raise HTTPException(403, "Invalid callback") from None

    @app.get("/wechat/callback", response_class=PlainTextResponse)
    async def verify(request: Request):
        echo = request.query_params.get("echostr", "")
        if len(echo) > MAX_CALLBACK:
            raise HTTPException(413, "Too large")
        return PlainTextResponse(decrypt(request, echo))

    @app.post("/wechat/callback", response_class=PlainTextResponse)
    async def receive(request: Request):
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > MAX_CALLBACK:
                raise HTTPException(413, "Too large")
        try:
            encrypted = fromstring(body).findtext("Encrypt", "")
        except (ParseError, DefusedXmlException):
            raise HTTPException(400, "Invalid XML") from None
        plain = decrypt(request, encrypted)
        try:
            event = fromstring(plain)
        except (ParseError, DefusedXmlException):
            raise HTTPException(400, "Invalid event") from None
        if event.findtext("ToUserName") != s.corp_id:
            raise HTTPException(403, "Invalid receiver")
        if event.findtext("Event") == "kf_msg_or_event":
            if s.mode == "verify":
                # Do not acknowledge message events we cannot durably retrieve yet.
                raise HTTPException(503, "Clipping not configured")
            if event.findtext("OpenKfId") == s.kf_id:
                token = event.findtext("Token", "")
                if len(token) > 128:
                    raise HTTPException(400, "Invalid event token")
                request.app.state.store.signal(s.kf_id, token)
        return PlainTextResponse("success")

    return app
