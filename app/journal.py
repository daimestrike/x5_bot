"""Журнал обращений (SQLite). Персональные данные не хранятся: пользователь — солёный хеш."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT NOT NULL,
    user_hash TEXT NOT NULL,
    event TEXT NOT NULL,
    card_id TEXT,
    card_version TEXT,
    rating TEXT,
    reason TEXT,
    query TEXT,
    help_type TEXT,
    extra TEXT
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS ix_events_event ON events(event);
CREATE INDEX IF NOT EXISTS ix_events_session ON events(session_id);
"""

COLUMNS = ["ts", "session_id", "user_hash", "event", "card_id", "card_version", "rating", "reason", "query",
           "help_type", "extra"]

# Типы событий журнала
SESSION_START = "session_start"
CARD_VIEW = "card_view"            # обращение, по которому выдан ответ
RATING = "rating"                  # helped / not_helped
CLARIFY = "clarify"                # причина «Не помогло»
SEARCH = "search"                  # запрос с результатом
SEARCH_MISS = "search_miss"        # запрос без результата — пробел базы
HELP_REQUEST = "help_request"      # обращение к маршруту помощи
UNRECOGNIZED = "unrecognized"      # свободный текст (S7)
ATTACHMENT = "attachment"          # голосовое / фото / файл
THANKS = "thanks"                  # «спасибо» — не обращение
MATERIAL_ERROR = "material_error"  # сбой выдачи (S8)


def user_hash(user_id: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{user_id}".encode("utf-8")).hexdigest()[:16]


class Journal:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def log(self, event: str, session_id: str, user_hash_: str, *, card_id: Optional[str] = None,
            card_version: Optional[str] = None, rating: Optional[str] = None, reason: Optional[str] = None,
            query: Optional[str] = None, help_type: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> None:
        ts = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._conn.execute(
                "INSERT INTO events (ts, session_id, user_hash, event, card_id, card_version,"
                " rating, reason, query, help_type, extra) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (ts, session_id, user_hash_, event, card_id, card_version, rating, reason, query, help_type,
                 json.dumps(extra, ensure_ascii=False) if extra else None),
            )
            self._conn.commit()

    def rows(self, since: Optional[str] = None, until: Optional[str] = None) -> List[sqlite3.Row]:
        sql = "SELECT * FROM events"
        args: list = []
        conds = []
        if since:
            conds.append("ts >= ?")
            args.append(since)
        if until:
            conds.append("ts < ?")
            args.append(until)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY id"
        with self._lock:
            return list(self._conn.execute(sql, args).fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
