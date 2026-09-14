"""Транспорт X5 Rooms: разбор входящего webhook и отправка ответов через Bot API.

ВАЖНО. Формат Bot API Rooms задаётся в одном месте — здесь. При подключении к реальному
контуру сверьте с документацией Rooms два метода:
  * parse_update(payload)  — какие поля несут id пользователя/чата, текст, callback и вложения;
  * build_payload(...)     — как выглядит тело запроса на отправку сообщения с кнопками.
Остальной код бота от формата не зависит.

Режимы кнопок (ROOMS_BUTTONS_MODE):
  inline — кнопки передаются в теле сообщения (поле "buttons"), callback приходит в webhook;
  text   — кнопки печатаются нумерованным списком, пользователь отвечает номером или подписью.
           Работает в любом мессенджере, даже без поддержки кнопок.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from ..config import Settings
from ..models import Incoming, Message, Reply

log = logging.getLogger(__name__)

# Возможные имена полей во входящем событии — берётся первое найденное.
_USER_KEYS = ("user_id", "sender_id", "from_id", "userId", "senderId")
_CHAT_KEYS = ("chat_id", "room_id", "dialog_id", "chatId", "roomId")
_TEXT_KEYS = ("text", "body", "message")
_CALLBACK_KEYS = ("callback_data", "callback", "data", "payload", "button_data")
_ATTACH_KEYS = ("attachment_type", "attachments", "files", "voice", "photo", "document", "video", "sticker")


def _first(d: Dict[str, Any], keys: tuple) -> Optional[Any]:
    for k in keys:
        if k in d and d[k] not in (None, "", [], {}):
            return d[k]
    return None


def parse_update(payload: Dict[str, Any]) -> Optional[tuple]:
    """Возвращает (chat_id, Incoming) или None, если событие не про сообщение пользователя."""
    msg = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    cb = payload.get("callback_query") if isinstance(payload.get("callback_query"), dict) else None
    src = cb or msg
    sender = next((src[k] for k in ("from", "sender", "user") if isinstance(src.get(k), dict)), {})
    user_id = _first(src, _USER_KEYS) or _first(sender or {}, ("id",) + _USER_KEYS)
    chat = src.get("chat") if isinstance(src.get("chat"), dict) else {}
    chat_id = _first(src, _CHAT_KEYS) or _first(chat or {}, ("id",) + _CHAT_KEYS) or user_id
    if user_id is None:
        return None
    if cb is not None:
        callback = _first(cb, _CALLBACK_KEYS)
        return str(chat_id), Incoming(user_id=str(user_id), callback=str(callback) if callback else None)

    text = _first(msg, _TEXT_KEYS)
    if isinstance(text, dict):
        text = text.get("text")
    callback = _first(msg, _CALLBACK_KEYS)
    attachment: Optional[str] = None
    for k in _ATTACH_KEYS:
        v = msg.get(k)
        if v:
            attachment = v if isinstance(v, str) and k == "attachment_type" else k
            if isinstance(v, list) and v and isinstance(v[0], dict):
                attachment = str(v[0].get("type") or k)
            break
    return str(chat_id), Incoming(
        user_id=str(user_id),
        text=str(text) if text is not None else None,
        callback=str(callback) if callback else None,
        attachment_type=attachment,
    )


def build_payload(chat_id: str, message: Message, buttons_mode: str) -> Dict[str, Any]:
    text = message.text
    payload: Dict[str, Any] = {"chat_id": chat_id}
    if buttons_mode == "text" and message.buttons:
        lines = [text, ""]
        n = 0
        for row in message.buttons:
            for b in row:
                n += 1
                lines.append(f"{n}. {b.label}")
        lines.append("")
        lines.append("Ответьте номером или названием кнопки.")
        payload["text"] = "\n".join(lines)
    else:
        payload["text"] = text
        if message.buttons:
            payload["buttons"] = [[{"text": b.label, "callback_data": b.data} for b in row] for row in message.buttons]
    return payload


class RoomsClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._client = httpx.AsyncClient(
            base_url=settings.rooms_api_base_url,
            timeout=settings.rooms_timeout_s,
            verify=settings.rooms_verify_tls,
            headers={"Authorization": f"Bearer {settings.rooms_bot_token}"} if settings.rooms_bot_token else {},
        )

    @property
    def configured(self) -> bool:
        return bool(self.s.rooms_api_base_url)

    async def send_reply(self, chat_id: str, reply: Reply) -> List[Dict[str, Any]]:
        sent = []
        for m in reply.messages:
            payload = build_payload(chat_id, m, self.s.rooms_buttons_mode)
            if not self.configured:
                log.info("ROOMS_API_BASE_URL не задан — сообщение не отправлено: %s", payload["text"][:80])
                sent.append(payload)
                continue
            try:
                r = await self._client.post(self.s.rooms_send_path, json=payload)
                r.raise_for_status()
            except httpx.HTTPError as e:  # сбой бота не должен мешать ПИ — только лог
                log.error("Rooms send failed: %s", e)
            sent.append(payload)
        return sent

    async def aclose(self) -> None:
        await self._client.aclose()
