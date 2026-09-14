"""Импорт материалов из файла чек-листов (xlsx) в content/cards.yaml.

Поддерживает два формата:
  * v11 — листы-карточки по шаблону «Карточка чек-листа» (колонки: Поле | Что указать | Для заполнения).
          Каждый лист с A1 = «Поле» и заполненной третьей колонкой считается одним материалом.
  * v9  — лист «Реестр материалов» с колонками ID | Раздел | Заголовок | Синонимы | Текст | Источник.

Использование:
    python scripts/import_registry.py "Чек-листы_бота_Rooms_v11.xlsx" [--out content/cards.yaml] [--merge]

--merge: карточки из xlsx добавляются/обновляются в существующем YAML (по id+version), остальные сохраняются.
Без --merge файл перезаписывается. Тексты по типам/разделам (types/groups) берутся из существующего YAML,
если он есть, иначе — значения по умолчанию.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

import openpyxl
import yaml

DEFAULT_TYPES = [{"id": "answer", "title": "Типовые вопросы"}, {"id": "instruction", "title": "Инструкции"},
                 {"id": "checklist", "title": "Чек-листы"}]
DEFAULT_GROUPS = [{"id": "A", "title": "Подготовка до ПИ"}, {"id": "B", "title": "Связь и сбои"},
                  {"id": "C", "title": "Как снимать"}, {"id": "D", "title": "Порядок и после ПИ"},
                  {"id": "E", "title": "Этапы ПИ"}]
TYPE_WORDS = {"ответ": "answer", "инструкц": "instruction", "чек": "checklist"}
STATUS_WORDS = {"черновик": "draft", "согласован": "approved", "опубликован": "published", "снят": "retired"}
LINK_KEY_BY_SOURCE = [("инструкц", "instruction"), ("памятк", "memo"), ("инфограф", "infographic"), ("чек-лист", "checklist")]
STEP_RE = re.compile(r"(?:^|\s)(\d)[.)]\s+")


def split_steps(text: str) -> List[str]:
    """«1. Снимите ... 2. Включите ...» или построчно -> список пунктов."""
    lines = [ln.strip(" -•") for ln in text.splitlines() if ln.strip(" -•")]
    if len(lines) > 1:
        return [re.sub(r"^\d+[.)]\s*", "", ln) for ln in lines]
    text = " ".join(text.split())
    marks = list(STEP_RE.finditer(text))
    if len(marks) < 2 or marks[0].start() != 0:
        return [text] if text else []
    return [text[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(text))].strip()
            for i, m in enumerate(marks)]


def link_key(source: str) -> Optional[str]:
    s = (source or "").lower()
    return next((k for needle, k in LINK_KEY_BY_SOURCE if needle in s), None)


def map_word(value: str, table: Dict[str, str], default: str) -> str:
    v = (value or "").lower()
    return next((code for word, code in table.items() if word in v), default)


# ----------------------------------------------------------------------------- v11
def parse_v11_sheet(ws, today: str) -> Optional[dict]:
    rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    if not rows or str(rows[0][0] or "").strip().lower() != "поле":
        return None
    fields: Dict[str, str] = {}
    for r in rows[1:]:
        name = str(r[0] or "").strip().lower()
        value = str(r[2] or "").strip() if len(r) > 2 and r[2] is not None else ""
        if name and value:
            fields[name] = value
    if not fields:
        return None

    def get(*keys: str) -> str:
        for k in fields:
            if any(k.startswith(p) for p in keys):
                return fields[k]
        return ""

    id_name = get("id")
    m = re.match(r"\s*([A-Z]-\d{2})\s*[.:—-]?\s*(.*)", id_name)
    if not m:
        print(f"  лист «{ws.title}»: не разобрано поле «ID и название» = {id_name!r}", file=sys.stderr)
        return None
    cid, title = m.group(1), m.group(2).strip() or id_name
    source = get("источник")
    ver_date = get("версия")
    ver = re.search(r"\d+(?:\.\d+)?", ver_date)
    date = re.search(r"\d{4}-\d{2}-\d{2}|\d{2}\.\d{2}\.\d{4}", ver_date)
    return {
        "id": cid, "type": map_word(get("тип"), TYPE_WORDS, "answer"), "group": cid[0], "title": title,
        "when": get("когда"), "prepare": get("что подготовить"),
        "steps": split_steps(get("текст")), "result": get("ожидаемый"), "help": get("если не получается"),
        "synonyms": [s.strip() for s in get("синоним").split(",") if s.strip()],
        "source": {"doc": source.split(",")[0].strip() if source else "", "section": source,
                   "link_key": link_key(source), "version": ""},
        "owner": get("ответствен"), "reviewer": get("проверяющ"),
        "version": ver.group(0) if ver else "1.0", "checked": date.group(0) if date else today,
        "status": map_word(get("статус"), STATUS_WORDS, "draft"),
    }


# ------------------------------------------------------------------------------ v9
def parse_v9_registry(ws, today: str) -> List[dict]:
    rows = [r for r in ws.iter_rows(values_only=True) if any(c is not None for c in r)]
    header = [str(c).strip().lower() if c else "" for c in rows[0]]

    def col(part: str) -> Optional[int]:
        return next((i for i, h in enumerate(header) if part in h), None)

    c_id, c_title, c_syn, c_text, c_src = col("id"), col("заголовок"), col("синоним"), col("текст"), col("источник")
    if None in (c_id, c_title, c_text):
        return []
    cards = []
    for r in rows[1:]:
        cid = str(r[c_id] or "").strip()
        if not re.fullmatch(r"[A-Z]-\d{2}", cid):
            continue
        src = str(r[c_src] or "") if c_src is not None else ""
        cards.append({
            "id": cid, "type": "answer", "group": cid[0], "title": str(r[c_title] or "").strip(),
            "when": "", "prepare": "", "steps": split_steps(str(r[c_text] or "")), "result": "", "help": "",
            "synonyms": [s.strip() for s in str(r[c_syn] or "").split(",") if s.strip()] if c_syn is not None else [],
            "source": {"doc": "", "section": src, "link_key": link_key(src), "version": ""},
            "owner": "", "reviewer": "", "version": "1.0", "checked": today, "status": "draft",
        })
    return cards


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out", default="content/cards.yaml")
    ap.add_argument("--merge", action="store_true")
    args = ap.parse_args()
    today = dt.date.today().isoformat()

    wb = openpyxl.load_workbook(args.xlsx, read_only=True)
    cards: List[dict] = []
    for ws in wb.worksheets:
        card = parse_v11_sheet(ws, today)
        if card:
            cards.append(card)
    mode = "v11"
    if not cards and "Реестр материалов" in wb.sheetnames:
        cards = parse_v9_registry(wb["Реестр материалов"], today)
        mode = "v9"
    if not cards:
        print("В файле не найдено ни одной карточки (ни листов-карточек v11, ни реестра v9).", file=sys.stderr)
        return 1

    out = Path(args.out)
    existing = yaml.safe_load(out.read_text(encoding="utf-8")) if out.exists() else {}
    existing = existing or {}
    types = existing.get("types") or DEFAULT_TYPES
    groups = existing.get("groups") or DEFAULT_GROUPS
    if args.merge:
        by_key = {(c["id"], str(c.get("version"))): c for c in existing.get("cards", [])}
        for c in cards:
            by_key[(c["id"], str(c["version"]))] = c
        cards = sorted(by_key.values(), key=lambda c: (c["id"], str(c.get("version"))))

    out.parent.mkdir(parents=True, exist_ok=True)
    header = out.read_text(encoding="utf-8").split("\n") if out.exists() else []
    comment = "\n".join(ln for ln in header if ln.startswith("#"))
    with out.open("w", encoding="utf-8") as f:
        if comment:
            f.write(comment + "\n")
        else:
            f.write("# Реестр материалов бота (формат карточки v11). Правится без участия разработчика.\n")
        yaml.safe_dump({"types": types, "groups": groups, "cards": cards}, f, allow_unicode=True, sort_keys=False, width=1000)
    print(f"[{mode}] записано {len(cards)} карточек в {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
