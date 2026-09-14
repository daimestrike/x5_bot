"""Сеансы пользователей в памяти: экран, контекст, последние показанные кнопки."""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .models import Button


@dataclass
class Session:
    id: str
    user_id: str
    started_at: float
    last_seen: float
    screen: str = "S0"
    type: Optional[str] = None
    group: Optional[str] = None
    page: int = 0
    card_id: Optional[str] = None
    awaiting_search: bool = False
    last_buttons: List[Button] = field(default_factory=list)


class SessionStore:
    def __init__(self, ttl_seconds: int):
        self._ttl = ttl_seconds
        self._by_user: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, user_id: str) -> tuple:
        """Возвращает (session, is_new). Сеанс новый, если его не было или он истёк."""
        now = time.time()
        with self._lock:
            s = self._by_user.get(user_id)
            if s is not None and now - s.last_seen <= self._ttl:
                s.last_seen = now
                return s, False
            s = Session(id=uuid.uuid4().hex[:12], user_id=user_id, started_at=now, last_seen=now)
            self._by_user[user_id] = s
            return s, True

    def reset(self, user_id: str) -> None:
        with self._lock:
            self._by_user.pop(user_id, None)

    def sweep(self) -> int:
        now = time.time()
        with self._lock:
            stale = [u for u, s in self._by_user.items() if now - s.last_seen > self._ttl]
            for u in stale:
                del self._by_user[u]
        return len(stale)
