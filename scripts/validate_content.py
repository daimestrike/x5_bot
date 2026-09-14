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
    total = sum(len(v) for v in c.all_versions.values())
    print(f"OK: опубликовано {len(c.cards)} материалов (всего записей {total}), {len(c.help_routes)} маршрутов помощи")
    for t in c.types:
        print(f"  {t.title}: {len(c.cards_of_type(t.id))}")
    if c.warnings:
        print("Предупреждения:")
        for w in c.warnings:
            print("  -", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
