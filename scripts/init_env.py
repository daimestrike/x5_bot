"""Create a local configuration without exposing secrets in command output."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = (root / ".env.example").read_text(encoding="utf-8")
for name in ("BOT_STATE_SECRET", "BOT_API_TOKEN", "METRICS_TOKEN"):
    content = content.replace(name + "=GENERATE", name + "=" + secrets.token_urlsafe(32))
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    raise SystemExit(".env already exists; it was not changed") from None
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(content)
print("Created .env. Run make demo for the optional developer chat. Do not commit this file.")
