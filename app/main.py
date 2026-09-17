"""Authenticated API. Core state and retry progress are persisted in SQLite."""

import asyncio
import csv
import hmac
import io
import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path

import jwt
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .config import Settings
from .content import Catalog
from .delivery import Delivery, QueueFull
from .engine import Engine, validate_event
from .metrics import product_metrics
from .transports.rooms import RoomsClient, parse_update, verify_token

STATIC = Path(__file__).parent / "static"
LOG = logging.getLogger("rooms_bot")


class BodyLimit:
    """Bound buffering before Starlette parses the request body."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        try:
            while True:
                message = await asyncio.wait_for(receive(), 2)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > 16384:
                    return await JSONResponse({"error": "body_too_large"}, status_code=413)(scope, receive, send)
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
        except asyncio.TimeoutError:
            return await JSONResponse({"error": "request_timeout"}, status_code=408)(scope, receive, send)

        async def buffered():
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, buffered, send)


def create_app(settings=None):
    # uvicorn uses --factory, so importing this module does not create a DB or client.
    settings = settings or Settings.from_env()
    settings.validate()
    os.umask(0o077)
    engine = Engine(
        Catalog(settings.content_dir, production=settings.mode == "production"),
        settings.db_path,
        settings.state_secret,
        settings.session_ttl,
        settings.retention_days,
    )
    rooms = RoomsClient(settings)
    delivery = Delivery(engine, rooms)
    # HTTP request URLs may contain BotX signatures or user attributes.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    buckets = {}

    async def cleanup_loop():
        while True:
            await asyncio.sleep(60)
            try:
                with engine.connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    engine.cleanup(db, time.time())
            except sqlite3.Error:
                LOG.error("cleanup_failed")

    @asynccontextmanager
    async def lifespan(_):
        tasks = [asyncio.create_task(cleanup_loop())]
        if settings.mode == "production":
            tasks.append(asyncio.create_task(delivery.run()))
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task
        await rooms.aclose()

    app = FastAPI(title="Справочник Аватара", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit)
    app.state.engine, app.state.rooms, app.state.settings = engine, rooms, settings
    app.state.delivery = delivery

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        return response

    def limit(request):
        now = time.monotonic()
        for key in list(buckets):
            if buckets[key][0] < now - 60:
                del buckets[key]
        key = request.client.host if request.client else "unknown"
        if key not in buckets:
            if len(buckets) >= 4096:
                raise HTTPException(429, "rate_limited")
            buckets[key] = [now, 0]
        buckets[key][1] += 1
        if buckets[key][1] > settings.requests_per_minute:
            raise HTTPException(429, "rate_limited")

    def authorize(request, admin=False):
        limit(request)
        token = settings.metrics_token if admin else settings.api_token
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not hmac.compare_digest(values[0].encode(), ("Bearer " + token).encode()):
            raise HTTPException(401, "unauthorized")

    def botx_auth(request):
        limit(request)
        if settings.mode != "production":
            raise HTTPException(503, "rooms_not_configured")
        values = request.headers.getlist("authorization")
        if len(values) != 1 or not values[0].startswith("Bearer "):
            raise HTTPException(401, "unauthorized")
        try:
            verify_token(values[0][7:], settings)
        except (jwt.InvalidTokenError, ValueError, TypeError):
            raise HTTPException(401, "unauthorized") from None

    async def body(request, console=False):
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "expected_json")
        try:
            payload = await request.json()
            if console:
                validate_event(payload)
                return payload
            return parse_update(payload, settings)
        except (ValueError, TypeError, RecursionError):
            raise HTTPException(400, "invalid_event") from None

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, error):
        LOG.error("database_unavailable")
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)

    @app.get("/health")
    async def health():
        return {"status": "ok", "mode": settings.mode, "rooms_verified": settings.rooms_contract_confirmed}

    @app.get("/ready")
    async def ready():
        with engine.connect() as db:
            db.execute("SELECT 1 FROM sessions LIMIT 1")
        return {"status": "ready", "cards": len(engine.catalog.cards)}

    @app.get("/status")
    async def status(request: Request):
        botx_auth(request)
        if request.query_params.get("bot_id") != settings.rooms_bot_id:
            raise HTTPException(400, "wrong_bot")
        return {
            "status": "ok",
            "result": {
                "enabled": True,
                "status_message": "Справочник Аватара",
                "commands": [
                    {"body": cmd, "name": label, "description": label}
                    for cmd, label in [("/menu", "Меню"), ("/search", "Поиск"), ("/help", "Помощь человека")]
                ],
            },
        }

    @app.post("/command", status_code=202)
    async def command(request: Request):
        botx_auth(request)
        event = await body(request)
        if event is not None:
            try:
                engine.handle(event, deferred=True, enqueue=delivery.enqueue(event["conversation_id"]))
            except QueueFull:
                raise HTTPException(503, "queue_full_retry_later") from None
            except ValueError:
                raise HTTPException(409, "event_conflict") from None
        # No external I/O before ACK. Queue and dialog state commit in one transaction.
        return {"result": "accepted"}

    @app.post("/notification/callback", status_code=202)
    async def callback(request: Request):
        botx_auth(request)
        # Outgoing /direct/sync returns the result inline. Unsolicited callbacks
        # cannot confirm or modify delivery progress.
        return {"result": "accepted"}

    if settings.mode == "demo" and settings.enable_dev_console:

        @app.get("/dev/chat")
        async def index():
            return FileResponse(STATIC / "index.html")

        @app.get("/dev/app.js")
        async def javascript():
            return FileResponse(STATIC / "app.js", media_type="text/javascript")

        @app.get("/dev/style.css")
        async def stylesheet():
            return FileResponse(STATIC / "style.css", media_type="text/css")

        @app.post("/api/console")
        async def console(request: Request):
            authorize(request)
            event = await body(request, console=True)
            try:
                return engine.handle(event)
            except ValueError:
                raise HTTPException(409, "event_conflict") from None

    @app.get("/metrics/summary")
    async def metrics(request: Request):
        authorize(request, admin=True)
        return {**engine.metrics(), "delivery": delivery.stats()}

    @app.get("/metrics/product")
    async def metrics_product(request: Request):
        authorize(request, admin=True)
        try:
            days = int(request.query_params.get("days", "30"))
        except ValueError:
            raise HTTPException(400, "bad_days") from None
        return {**product_metrics(engine, days), "delivery": delivery.stats(), "mode": settings.mode}

    # Страница дашборда: статический HTML без данных; данные запрашивает JS по METRICS_TOKEN.
    @app.get("/metrics/dashboard")
    async def dashboard():
        return FileResponse(STATIC / "dashboard.html")

    @app.get("/metrics/dashboard.js")
    async def dashboard_js():
        return FileResponse(STATIC / "dashboard.js", media_type="text/javascript")

    @app.get("/metrics/dashboard.css")
    async def dashboard_css():
        return FileResponse(STATIC / "dashboard.css", media_type="text/css")

    @app.get("/metrics/journal.csv")
    async def journal_csv(request: Request):
        authorize(request, admin=True)
        rows = engine.journal_rows()
        buffer = io.StringIO()
        cols = ["time", "kind", "topic", "version", "value", "delivered", "participant"]
        writer = csv.DictWriter(buffer, fieldnames=cols, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
        return PlainTextResponse(buffer.getvalue(), media_type="text/csv; charset=utf-8")

    return app
