"""Проверка содержания без запуска бота: python scripts/validate_content.py [content_dir]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.content import ContentError, load_content  # noqa: E402


def main() -> int:
    content_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "content")
    try:
        c = load_content(content_dir, strict=True)
    except ContentError as e:
        print(e)
        return 1
    print(f"OK: {len(c.cards)} карточек, {len(c.sections)} разделов, {len(c.help_routes)} маршрутов помощи")
    for s in c.sections:
        print(f"  {s.title}: {len(s.cards)}")
    if c.warnings:
        print("Предупреждения:")
        for w in c.warnings:
            print("  -", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
