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
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from .assistant import Assistant
from .config import Settings
from .content import Catalog
from .delivery import Delivery, QueueFull
from .engine import Engine, validate_event
from .llm import LLM, LLMError
from .metrics import product_metrics
from .review import ContentStore, ReviewError, source_change_items
from .sources import MAX_DOC_BYTES, SourceError, SourceStore, match_cards
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
        limit = MAX_DOC_BYTES if scope["path"] == "/content/api/sources/upload" else 16384
        try:
            while True:
                message = await asyncio.wait_for(receive(), 10 if limit > 16384 else 2)
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > limit:
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
    catalog = Catalog(settings.content_dir, production=settings.mode == "production")
    sources = SourceStore(settings.content_dir)
    llm = LLM(settings)
    assistant = Assistant(settings, catalog, sources, llm, LOG.info,
                          cache_path=Path(settings.db_path).with_name("embeddings.sqlite3"))
    engine = Engine(
        catalog,
        settings.db_path,
        settings.state_secret,
        settings.session_ttl,
        settings.retention_days,
        assistant=assistant,
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
        return {"status": "ok", "mode": settings.mode, "rooms_verified": settings.rooms_contract_confirmed,
                "ai": {"enabled": assistant.enabled, "model": llm.model if assistant.enabled else "",
                       "embeddings": llm.embeddings_enabled}}

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
                await run_in_threadpool(
                    engine.handle, event, deferred=True, enqueue=delivery.enqueue(event["conversation_id"])
                )
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
                return await run_in_threadpool(engine.handle, event)
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

    # ------------------------------------------------------------------ содержание
    store = ContentStore(settings.content_dir)

    def reload_catalog():
        """Перечитать карточки. В production бот остаётся на прежней версии, пока новая не утверждена."""
        try:
            engine.catalog = Catalog(settings.content_dir, production=settings.mode == "production")
            assistant.reload(engine.catalog)  # индекс базы знаний ИИ пересоберётся при следующем вопросе
            return True
        except (ValueError, KeyError, TypeError) as e:
            LOG.warning("content_not_reloaded: %s", e)
            return False

    def review_error(e):
        raise HTTPException(400, str(e)) from None

    @app.get("/content/")
    async def content_page():
        return FileResponse(STATIC / "content.html")

    @app.get("/content/app.js")
    async def content_js():
        return FileResponse(STATIC / "content.js", media_type="text/javascript")

    @app.get("/content/app.css")
    async def content_css():
        return FileResponse(STATIC / "content.css", media_type="text/css")

    @app.get("/content/api/ai/check")
    async def ai_check(request: Request):
        """Проверка связи с моделью со страницы «Содержание»: URL, модель, тестовый ответ."""
        authorize(request, admin=True)
        if not settings.ai_enabled:
            return {"enabled": False, "reason": "AI_ENABLED=false — бот отвечает строго по меню"}
        try:
            result = await run_in_threadpool(llm.check)
        except LLMError as e:
            return {"enabled": True, "ok": False, "code": e.code, "error": str(e)}
        chunks = len(assistant.rag.get_index().chunks)
        return {**result, "ok": True, "chunks": chunks, "vectors": len(assistant.rag.vectors)}

    @app.get("/content/api/state")
    async def content_state(request: Request):
        authorize(request, admin=True)
        cards = store.read_cards()
        live = {c["id"]: c for c in engine.catalog.cards.values()}
        return {
            "mode": settings.mode,
            "sections": engine.catalog.settings["sections"],
            "cards": [
                {k: c.get(k) for k in ("id", "section", "title", "version", "date", "status", "approved", "owner", "url",
                                       "source", "source_key", "available", "edited_by", "approved_at")}
                | {"live_version": live.get(c["id"], {}).get("version"), "history": len(c.get("history", []))}
                for c in cards
            ],
            "sources": sources.list(),
            "ai": {"enabled": assistant.enabled, "model": llm.model, "url": llm.url,
                   "embeddings": llm.embeddings_enabled,
                   "chunks": len(assistant.rag.get_index().chunks) if assistant.enabled else None},
            "queue": store.open_items(),
            "resolved": [i for i in store.read_queue() if i["status"] != "open"][-50:],
        }

    @app.get("/content/api/cards/{card_id}")
    async def content_card(card_id: str, request: Request):
        authorize(request, admin=True)
        card = next((c for c in store.read_cards() if c["id"] == card_id), None)
        if card is None:
            raise HTTPException(404, "no_card")
        return card

    @app.post("/content/api/sources/upload")
    async def content_upload(request: Request):
        authorize(request, admin=True)
        key = request.query_params.get("key", "")
        title = request.query_params.get("title", "")
        filename = request.query_params.get("filename", "")
        data = await request.body()
        try:
            snap, changes = sources.snapshot(key, title, filename, data, engine.catalog.cards)
        except SourceError as e:
            review_error(e)
        items = []
        if changes:
            items = store.enqueue(source_change_items(snap, changes, engine.catalog.cards, match_cards))
        return {"source": {k: snap[k] for k in ("key", "title", "filename", "version", "units_count", "uploaded",
                                              "linked_units", "linked_cards")},
                "changes": len(changes or []), "queued": len(items), "first_upload": changes is None}

    @app.post("/content/api/queue/resolve")
    async def content_resolve(request: Request):
        authorize(request, admin=True)
        body = await request.json()
        try:
            return store.resolve(str(body.get("id", "")), str(body.get("decision", "")), str(body.get("who", ""))[:100],
                                 str(body.get("note", "")))
        except ReviewError as e:
            review_error(e)

    @app.post("/content/api/cards/update")
    async def content_update(request: Request):
        authorize(request, admin=True)
        body = await request.json()
        fields = body.get("fields")
        if not isinstance(fields, dict):
            raise HTTPException(400, "fields_required")
        try:
            card = store.update_card(str(body.get("id", "")), fields, str(body.get("editor", ""))[:100])
        except ReviewError as e:
            review_error(e)
        return {"card": card, "reloaded": reload_catalog()}

    @app.post("/content/api/cards/approve")
    async def content_approve(request: Request):
        authorize(request, admin=True)
        body = await request.json()
        try:
            card = store.approve(str(body.get("id", "")), str(body.get("reviewer", ""))[:100], bool(body.get("approved", True)))
        except ReviewError as e:
            review_error(e)
        return {"card": card, "reloaded": reload_catalog()}

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
