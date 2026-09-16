"""eXpress BotX v4 adapter with the optional X5 Keycloak gateway in front of it.

Outbound scheme confirmed by the X5 wiki page «Под капотом у ROOMS bot-а» (DPP space):
  1. Keycloak: POST KC_URL, client_credentials (client_id + client_secret) -> access_token;
  2. Bot token: GET TOKEN_URL?signature=HMAC-SHA256(BOT_SECRET, BOT_ID) with the Keycloak token -> result;
  3. Send: POST SEND_URL {"group_chat_id", "notification": {"status": "ok", "body", ...}} with both tokens
     in headers. The exact header name for the Keycloak token is not shown on the page: ROOMS_KC_HEADER.
When ROOMS_KC_URL is empty the adapter behaves as plain BotX (no gateway).

No URLs, credentials, directory attributes or attachment bodies from incoming
commands are retained. Only the configured servers can receive outbound requests.
"""

import hashlib
import hmac
import ssl
from urllib.parse import urlsplit
from uuid import UUID

import httpx
import jwt

from ..engine import validate_event


class DeliveryError(Exception):
    pass


def uuid_text(value):
    if not isinstance(value, str):
        raise ValueError("Expected UUID")
    return str(UUID(value))


def verify_token(token, settings):
    claims = jwt.decode(
        token,
        settings.rooms_secret_key,
        algorithms=["HS256"],
        audience=settings.rooms_bot_id,
        issuer=settings.rooms_issuer,
        options={"require": ["iss", "aud", "exp", "nbf", "iat", "jti"]},
    )
    if (
        not isinstance(claims["jti"], str)
        or not claims["jti"]
        or claims["exp"] - claims["iat"] > 60
        or claims["exp"] <= claims["iat"]
        or claims["nbf"] != claims["iat"]
    ):
        raise jwt.InvalidTokenError("Invalid token lifetime")
    return claims


def parse_update(payload, settings):
    if not isinstance(payload, dict) or payload.get("proto_version") != 4:
        raise ValueError("Expected Bot API v4")
    if uuid_text(payload.get("bot_id")) != settings.rooms_bot_id:
        raise ValueError("Wrong bot")
    eid = uuid_text(payload.get("sync_id"))
    command, sender = payload.get("command"), payload.get("from")
    if not isinstance(command, dict) or not isinstance(sender, dict):
        raise ValueError("Invalid command")
    # The issuer may be a separate BotX hostname. Sender host is the configured CTS.
    if sender.get("host") != urlsplit(settings.rooms_api_base_url).hostname:
        raise ValueError("Wrong CTS host")
    body, kind = command.get("body"), command.get("command_type")
    if not isinstance(body, str) or kind not in ("user", "system"):
        raise ValueError("Invalid command body")
    if kind == "system":
        if body != "system:chat_created":
            return None
        data = command.get("data", {})
        if not isinstance(data, dict) or data.get("chat_type") != "chat":
            return None
        event = dict(
            event_id=eid,
            conversation_id=uuid_text(data.get("group_chat_id")),
            user_id=uuid_text(data.get("creator")),
            type="opened",
        )
    else:
        # MVP is a personal reference chat, never broadcast employee queries to groups.
        if sender.get("chat_type") != "chat":
            return None
        event = dict(
            event_id=eid, conversation_id=uuid_text(sender.get("group_chat_id")), user_id=uuid_text(sender.get("user_huid"))
        )
        if payload.get("attachments") or payload.get("async_files"):
            event.update(type="attachment", attachment_type="other")
        elif body == "/action":
            data = command.get("data")
            if not isinstance(data, dict) or not isinstance(data.get("action"), str):
                raise ValueError("Invalid button data")
            event.update(type="action", action=data["action"])
        elif body.strip() == "/end":
            event.update(type="closed")
        elif len(body) > 500 or not body.strip():
            event.update(type="action", action="unsupported")
        else:
            event.update(type="message", text=body)
    validate_event(event)
    return event


def button(label, action):
    return {"command": "/action", "label": label, "data": {"action": action}, "opts": {"silent": True}}


def build_payload(chat_id, message):
    rows = [[button(b["label"], b["action"])] for b in message["buttons"]]
    if message["screen"] == "S3" and len(rows) == 4:
        rows = [rows[0] + rows[1], rows[2] + rows[3]]
    return {
        "group_chat_id": uuid_text(chat_id),
        "notification": {
            "status": "ok",
            "body": message["text"],
            "bubble": rows,
            "keyboard": [[button("Меню", "menu"), button("Поиск", "search")]],
            "opts": {"silent_response": False, "buttons_auto_adjust": True},
        },
        "opts": {"notification_opts": {"send": False, "force_dnd": False}},
    }


class RoomsClient:
    def __init__(self, settings):
        self.settings, self.token, self.kc_token = settings, None, None
        if settings.rooms_verify_tls:
            verify = ssl.create_default_context(cafile=settings.rooms_ca_file or None)
        else:
            verify = False  # internal networks with self-signed certificates (VERIFY_SSL=False on the wiki page)
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(15, connect=5), verify=verify, trust_env=False, follow_redirects=False
        )

    def headers(self, with_bot_token=True):
        h = {}
        if self.kc_token:
            h[self.settings.rooms_kc_header] = self.kc_token
        if with_bot_token and self.token:
            h["Authorization"] = "Bearer " + self.token
        return h

    async def authenticate_keycloak(self):
        s = self.settings
        if not s.rooms_kc_url:
            return
        response = await self.client.post(
            s.rooms_kc_url,
            data={"grant_type": "client_credentials", "client_id": s.rooms_kc_client_id,
                  "client_secret": s.rooms_kc_client_secret},
        )
        response.raise_for_status()
        token = response.json().get("access_token")
        if not isinstance(token, str) or not token:
            raise DeliveryError("Keycloak authentication rejected")
        self.kc_token = token

    async def authenticate(self):
        s = self.settings
        await self.authenticate_keycloak()
        signature = hmac.new(s.rooms_secret_key.encode(), s.rooms_bot_id.encode(), hashlib.sha256).hexdigest().upper()
        response = await self.client.get(s.token_url, params={"signature": signature}, headers=self.headers(False))
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "ok" or not isinstance(data.get("result"), str) or not data["result"]:
            raise DeliveryError("BotX authentication rejected")
        self.token = data["result"]

    async def send(self, chat_id, message):
        if self.settings.mode != "production":
            raise DeliveryError("BotX delivery disabled")
        try:
            for attempt in range(2):
                if self.token is None:
                    await self.authenticate()
                response = await self.client.post(
                    self.settings.send_url, headers=self.headers(), json=build_payload(chat_id, message)
                )
                if response.status_code == 401 and attempt == 0:
                    self.token = self.kc_token = None
                    continue
                response.raise_for_status()
                data = response.json()
                if response.status_code != 200 or data.get("status") != "ok":
                    raise DeliveryError("BotX delivery rejected")
                uuid_text(data["result"]["sync_id"])
                return
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise DeliveryError("BotX delivery failed") from None

    async def aclose(self):
        await self.client.aclose()
