"""HTTP-приложение: webhook Rooms, демо-консоль для приёмки, метрики, healthcheck."""
from __future__ import annotations

import csv
import io
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

from . import __version__
from .config import Settings, load_settings
from .content import ContentError, load_content
from .engine import Engine
from .journal import COLUMNS, Journal
from .metrics import summary
from .models import Incoming
from .sessions import SessionStore
from .transports.rooms import RoomsClient, parse_update

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or load_settings()
    try:
        content = load_content(settings.content_dir, strict=settings.content_strict)
    except ContentError as e:
        log.error("%s", e)
        raise
    journal = Journal(settings.db_path)
    sessions = SessionStore(settings.session_ttl_min * 60)
    engine = Engine(content, journal, sessions, settings)
    rooms = RoomsClient(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        log.info("bot started: %d cards, rooms_configured=%s", len(content.cards), rooms.configured)
        yield
        await rooms.aclose()
        journal.close()

    app = FastAPI(title="Rooms bot — справочник Аватара", version=__version__, docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.journal = journal
    app.state.rooms = rooms

    # ---------------------------------------------------------------- health
    @app.get("/health")
    async def health() -> Dict[str, Any]:
        return {"status": "ok", "version": __version__, "cards": len(content.cards),
                "rooms_configured": rooms.configured, "content_warnings": len(content.warnings)}

    # ------------------------------------------------------------ webhook Rooms
    def _check_webhook_secret(x_rooms_token: Optional[str] = Header(default=None),
                              authorization: Optional[str] = Header(default=None)) -> None:
        if not settings.rooms_webhook_secret:
            return
        token = x_rooms_token or (authorization or "").replace("Bearer ", "").strip()
        if token != settings.rooms_webhook_secret:
            raise HTTPException(status_code=401, detail="bad webhook token")

    @app.post("/webhook/rooms", dependencies=[Depends(_check_webhook_secret)])
    async def rooms_webhook(request: Request) -> Dict[str, Any]:
        try:
            payload = await request.json()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="invalid json") from e
        parsed = parse_update(payload if isinstance(payload, dict) else {})
        if parsed is None:
            return {"ok": True, "ignored": True}
        chat_id, inc = parsed
        reply = engine.handle(inc)
        sent = await rooms.send_reply(chat_id, reply)
        # Ответ в теле пригодится, если Rooms поддерживает синхронный ответ на webhook.
        return {"ok": True, "screen": reply.screen, "messages": sent}

    # ------------------------------------------------------- демо-консоль (приёмка)
    if settings.demo_console:
        @app.get("/", response_class=HTMLResponse)
        async def index() -> str:
            return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

        @app.post("/api/console")
        async def console(body: Dict[str, Any]) -> Dict[str, Any]:
            inc = Incoming(
                user_id=str(body.get("user_id") or "demo"),
                text=body.get("text"),
                callback=body.get("callback"),
                attachment_type=body.get("attachment_type"),
            )
            reply = engine.handle(inc)
            return {
                "screen": reply.screen,
                "messages": [
                    {"text": m.text, "buttons": [[{"label": b.label, "data": b.data} for b in row] for row in m.buttons]}
                    for m in reply.messages
                ],
            }

        @app.post("/api/console/reset")
        async def console_reset(body: Dict[str, Any]) -> Dict[str, Any]:
            sessions.reset(str(body.get("user_id") or "demo"))
            return {"ok": True}

    # ---------------------------------------------------------------- метрики
    def _check_metrics_token(x_metrics_token: Optional[str] = Header(default=None)) -> None:
        if settings.metrics_token and x_metrics_token != settings.metrics_token:
            raise HTTPException(status_code=401, detail="bad metrics token")

    @app.get("/metrics/summary", dependencies=[Depends(_check_metrics_token)])
    async def metrics_summary(since: Optional[str] = None, until: Optional[str] = None) -> JSONResponse:
        return JSONResponse(summary(journal, since, until))

    @app.get("/metrics/journal.csv", dependencies=[Depends(_check_metrics_token)])
    async def journal_csv(since: Optional[str] = None, until: Optional[str] = None) -> PlainTextResponse:
        rows = journal.rows(since, until)
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(COLUMNS)
        for r in rows:
            w.writerow([r[c] for c in COLUMNS])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv; charset=utf-8")

    @app.get("/content/cards")
    async def cards_list() -> Dict[str, Any]:
        return {
            "sections": [{"id": s.id, "title": s.title, "cards": [
                {"id": c.id, "title": c.title, "version": c.version, "updated": c.updated, "owner": c.owner,
                 "link": content.link_for(c)} for c in s.cards]} for s in content.sections],
            "warnings": content.warnings,
        }

    return app


app = create_app()
