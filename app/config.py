from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    content_dir: Path = Path(os.getenv("CONTENT_DIR", "content"))
    db_path: Path = Path(os.getenv("DB_PATH", "data/journal.sqlite3"))
    session_ttl_min: int = int(os.getenv("SESSION_TTL_MIN", "240"))
    journal_salt: str = os.getenv("JOURNAL_SALT", "change-me")
    log_free_text: bool = _env_bool("LOG_FREE_TEXT", True)
    free_text_max_len: int = int(os.getenv("FREE_TEXT_MAX_LEN", "200"))
    content_strict: bool = _env_bool("CONTENT_STRICT", True)
    search_enabled: bool = _env_bool("SEARCH_ENABLED", False)          # v11: по умолчанию только меню
    journal_user_mode: str = os.getenv("JOURNAL_USER_MODE", "plain")     # plain — участник как есть; hash — хеш

    # Транспорт Rooms
    rooms_api_base_url: str = os.getenv("ROOMS_API_BASE_URL", "")
    rooms_bot_token: str = os.getenv("ROOMS_BOT_TOKEN", "")
    rooms_webhook_secret: str = os.getenv("ROOMS_WEBHOOK_SECRET", "")
    rooms_send_path: str = os.getenv("ROOMS_SEND_PATH", "/api/v1/bot/messages")
    rooms_buttons_mode: str = os.getenv("ROOMS_BUTTONS_MODE", "inline")  # inline | text
    rooms_timeout_s: float = float(os.getenv("ROOMS_TIMEOUT_S", "10"))
    rooms_verify_tls: bool = _env_bool("ROOMS_VERIFY_TLS", True)

    demo_console: bool = _env_bool("DEMO_CONSOLE", True)
    metrics_token: str = os.getenv("METRICS_TOKEN", "")


def load_settings() -> Settings:
    return Settings()
