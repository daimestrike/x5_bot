"""Import the original Excel registry as drafts into a NEW YAML file, preserving source text."""

import argparse
import datetime
import re
from pathlib import Path

import openpyxl
import yaml


def split_steps(text):
    return [s.strip() for s in re.split(r"(?:^|\s)\d+[.)]\s+", text) if s.strip()]


def import_registry(path):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        ws = wb["Реестр материалов"]
        rows = iter(ws.values)
        headings = [str(c or "").lower() for c in next(rows)]

        def index(fragment):
            return next(i for i, h in enumerate(headings) if fragment in h)

        cols = [index(v) for v in ("id", "заголовок", "синоним", "текст", "источник")]
        cards = []
        for row in rows:
            if not row[cols[0]]:
                continue
            cid, title, synonyms, text, source = [str(row[i] or "") for i in cols]
            if not re.fullmatch(r"[A-E]-\d{2}", cid):
                raise ValueError("Invalid card ID")
            cards.append(
                dict(
                    id=cid,
                    section=cid[0],
                    title=title,
                    synonyms=[s.strip() for s in synonyms.split(",") if s.strip()],
                    steps=split_steps(text),
                    source=source,
                    url="",
                    owner="",
                    version="0.1.0",
                    date=datetime.date.today().isoformat(),
                    status="draft",
                    approved=False,
                    available=True,
                )
            )
        return cards
    finally:
        wb.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--out", required=True, help="New review file; existing files are never overwritten")
    args = ap.parse_args()
    cards = import_registry(args.xlsx)
    with Path(args.out).open("x", encoding="utf-8") as f:
        yaml.safe_dump(cards, f, allow_unicode=True, sort_keys=False)
    print(f"Imported {len(cards)} drafts. Fill metadata and review before replacing the active catalog.")
