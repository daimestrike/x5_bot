"""Environment configuration loaded at startup, never frozen at import time."""

import os
from dataclasses import dataclass
from pathlib import Path


def boolean(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    if value.lower() not in ("true", "false"):
        raise ValueError(name + " must be true or false")
    return value.lower() == "true"


@dataclass
class Settings:
    mode: str = "demo"
    content_dir: Path = Path("content")
    db_path: Path = Path("data/bot-v2.sqlite3")
    state_secret: str = ""
    api_token: str = ""
    metrics_token: str = ""
    session_ttl: int = 14400
    retention_days: int = 30
    requests_per_minute: int = 300
    rooms_api_base_url: str = ""
    rooms_bot_id: str = ""
    rooms_issuer: str = ""
    rooms_secret_key: str = ""
    enable_dev_console: bool = False
    rooms_ca_file: str = ""
    rooms_verify_tls: bool = True
    rooms_contract_confirmed: bool = False
    # Шлюз X5 (wiki DPP «Под капотом у ROOMS bot-а»): Keycloak client_credentials перед BotX-токеном.
    rooms_kc_url: str = ""
    rooms_kc_client_id: str = ""
    rooms_kc_client_secret: str = ""
    rooms_kc_header: str = "X-Keycloak-Token"
    rooms_token_url: str = ""
    rooms_send_url: str = ""

    @classmethod
    def from_env(cls):
        s = cls(
            mode=os.getenv("BOT_MODE", "demo"),
            content_dir=Path(os.getenv("CONTENT_DIR", "content")),
            db_path=Path(os.getenv("DB_PATH", "data/bot-v2.sqlite3")),
            state_secret=os.getenv("BOT_STATE_SECRET", ""),
            api_token=os.getenv("BOT_API_TOKEN", ""),
            metrics_token=os.getenv("METRICS_TOKEN", ""),
            session_ttl=int(os.getenv("SESSION_TTL_SECONDS", "14400")),
            retention_days=int(os.getenv("RETENTION_DAYS", "30")),
            requests_per_minute=int(os.getenv("REQUESTS_PER_MINUTE", "300")),
            rooms_api_base_url=os.getenv("ROOMS_API_BASE_URL", ""),
            rooms_bot_id=os.getenv("ROOMS_BOT_ID", ""),
            rooms_issuer=os.getenv("ROOMS_ISSUER", ""),
            rooms_secret_key=os.getenv("ROOMS_SECRET_KEY", ""),
            enable_dev_console=boolean("ENABLE_DEV_CONSOLE", False),
            rooms_ca_file=os.getenv("ROOMS_CA_FILE", ""),
            rooms_verify_tls=boolean("ROOMS_VERIFY_TLS", True),
            rooms_contract_confirmed=boolean("ROOMS_CONTRACT_CONFIRMED", False),
            rooms_kc_url=os.getenv("ROOMS_KC_URL", ""),
            rooms_kc_client_id=os.getenv("ROOMS_KC_CLIENT_ID", ""),
            rooms_kc_client_secret=os.getenv("ROOMS_KC_CLIENT_SECRET", ""),
            rooms_kc_header=os.getenv("ROOMS_KC_HEADER", "X-Keycloak-Token"),
            rooms_token_url=os.getenv("ROOMS_TOKEN_URL", ""),
            rooms_send_url=os.getenv("ROOMS_SEND_URL", ""),
        )
        s.validate()
        return s

    def validate(self):
        if self.mode not in ("demo", "production"):
            raise ValueError("BOT_MODE must be demo or production")
        secrets = [self.state_secret, self.api_token, self.metrics_token]
        if any(len(s) < 32 or not s.isascii() or any(c.isspace() for c in s) for s in secrets):
            raise ValueError("Generate three ASCII secrets of at least 32 characters: run scripts/init_env.py")
        if len(set(secrets)) != 3:
            raise ValueError("State, API and metrics secrets must be different")
        if not 60 <= self.session_ttl <= 86400 or not 1 <= self.retention_days <= 365:
            raise ValueError("Invalid session TTL or retention")
        if not 1 <= self.requests_per_minute <= 100000:
            raise ValueError("Invalid rate limit")
        if self.mode == "production":
            from urllib.parse import urlsplit

            u = urlsplit(self.rooms_api_base_url)
            if not self.rooms_contract_confirmed:
                raise ValueError("Actual Rooms API contract must be confirmed before production")
            if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError("ROOMS_API_BASE_URL must be an HTTPS origin/base path without credentials")
            from uuid import UUID

            if str(UUID(self.rooms_bot_id)) != self.rooms_bot_id:
                raise ValueError("ROOMS_BOT_ID must be a canonical UUID")
            if u.path or not self.rooms_issuer or "/" in self.rooms_issuer:
                raise ValueError("Configure CTS HTTPS origin and ROOMS_ISSUER FQDN")
            if len(self.rooms_secret_key) < 32:
                raise ValueError("Configure ROOMS_SECRET_KEY from the administrator")
            if self.enable_dev_console:
                raise ValueError("Developer console is prohibited in production")
            if self.rooms_kc_url and not (self.rooms_kc_client_id and self.rooms_kc_client_secret):
                raise ValueError("Configure ROOMS_KC_CLIENT_ID and ROOMS_KC_CLIENT_SECRET for the Keycloak gateway")
            for name in ("rooms_kc_url", "rooms_token_url", "rooms_send_url"):
                value = getattr(self, name)
                if value and urlsplit(value).scheme != "https":
                    raise ValueError(name.upper() + " must be an HTTPS URL")

    @property
    def token_url(self):
        return self.rooms_token_url or self.rooms_api_base_url + "/api/v2/botx/bots/" + self.rooms_bot_id + "/token"

    @property
    def send_url(self):
        return self.rooms_send_url or self.rooms_api_base_url + "/api/v4/botx/notifications/direct/sync"
