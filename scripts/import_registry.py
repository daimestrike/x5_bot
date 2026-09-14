"""Импорт реестра материалов из файла чек-листов (xlsx) в content/cards.yaml.

Использование:
    python scripts/import_registry.py "Чек-листы_бота_Rooms_v9.xlsx" [--out content/cards.yaml]

Берёт лист «Реестр материалов», разбивает текст ответа на пронумерованные шаги,
сопоставляет источник со ссылкой из content/params.yaml (links.*) и пишет YAML.
Поля версии/даты/владельца, если их нет в xlsx, заполняются значениями по умолчанию —
владелец содержания правит их прямо в YAML.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

import openpyxl
import yaml

SECTION_BY_LETTER = {
    "A": "A. Подготовка до ПИ",
    "B": "B. Связь и сбои",
    "C": "C. Как снимать",
    "D": "D. Порядок и после ПИ",
    "E": "E. Этапы ПИ — справка",
}

LINK_KEY_BY_SOURCE = [
    ("инструкц", "instruction"),
    ("памятк", "memo"),
    ("инфограф", "infographic"),
    ("чек-лист", "checklist"),
]

STEP_RE = re.compile(r"(?:^|\s)(\d)\.\s+")


def split_steps(text: str) -> list[str]:
    """«1. Снимите ... 2. Включите ...» -> ["Снимите ...", "Включите ..."]."""
    text = " ".join(text.split())
    marks = list(STEP_RE.finditer(text))
    if len(marks) < 2 or marks[0].start() != 0:
        return [text]
    steps = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        steps.append(text[m.end():end].strip())
    return steps


def link_key(source: str) -> str | None:
    s = (source or "").lower()
    for needle, key in LINK_KEY_BY_SOURCE:
        if needle in s:
            return key
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out", default="content/cards.yaml")
    ap.add_argument("--sheet", default="Реестр материалов")
    ap.add_argument("--version", default="1.0")
    ap.add_argument("--owner", default="не назначен")
    args = ap.parse_args()

    wb = openpyxl.load_workbook(args.xlsx, read_only=True)
    if args.sheet not in wb.sheetnames:
        print(f"Лист «{args.sheet}» не найден. Есть: {wb.sheetnames}", file=sys.stderr)
        return 1
    ws = wb[args.sheet]
    rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    header = [str(c).strip() if c else "" for c in rows[0]]

    def col(name_part: str) -> int:
        for i, h in enumerate(header):
            if name_part.lower() in h.lower():
                return i
        raise SystemExit(f"Колонка «{name_part}» не найдена в {header}")

    c_id, c_title, c_syn, c_text, c_src = (
        col("ID"), col("Заголовок"), col("Синоним"), col("Текст"), col("Источник"),
    )
    today = dt.date.today().isoformat()
    cards = []
    for r in rows[1:]:
        cid = str(r[c_id] or "").strip()
        if not re.fullmatch(r"[A-E]-\d{2}", cid):
            continue
        synonyms = [s.strip() for s in str(r[c_syn] or "").split(",") if s.strip()]
        card = {
            "id": cid,
            "section": cid[0],
            "title": str(r[c_title] or "").strip(),
            "synonyms": synonyms,
            "steps": split_steps(str(r[c_text] or "")),
            "source": str(r[c_src] or "").strip(),
            "link_key": link_key(str(r[c_src] or "")),
            "version": args.version,
            "updated": today,
            "owner": args.owner,
        }
        cards.append(card)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "sections": [{"id": k, "title": v} for k, v in SECTION_BY_LETTER.items()],
        "cards": cards,
    }
    with out.open("w", encoding="utf-8") as f:
        f.write("# Реестр карточек бота. Правится владельцем содержания без участия разработчика.\n")
        f.write("# Сгенерировано scripts/import_registry.py; после правок запустите scripts/validate_content.py.\n")
        yaml.safe_dump(doc, f, allow_unicode=True, sort_keys=False, width=1000)
    print(f"Записано {len(cards)} карточек в {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
