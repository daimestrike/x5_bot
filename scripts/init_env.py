"""Create .env from .env.example.

Default: copies the public demo-* values (anyone can open the dev console with them).
--secure: replaces every demo-* secret with a random one — required before production.
"""

import os
import secrets
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / ".env"
content = (root / ".env.example").read_text(encoding="utf-8")
if "--secure" in sys.argv:
    lines = []
    for line in content.splitlines():
        name, _, value = line.partition("=")
        if name in ("BOT_STATE_SECRET", "BOT_API_TOKEN", "METRICS_TOKEN") and value.startswith("demo-"):
            line = name + "=" + secrets.token_urlsafe(32)
        lines.append(line)
    content = "\n".join(lines) + "\n"
try:
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
except FileExistsError:
    raise SystemExit(".env already exists; it was not changed") from None
with os.fdopen(fd, "w", encoding="utf-8") as f:
    f.write(content)
print("Created .env" + (" with random secrets." if "--secure" in sys.argv else " with public demo secrets."))
