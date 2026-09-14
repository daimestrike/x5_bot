"""Выгрузка журнала и сводки метрик: python scripts/export_journal.py [--db data/journal.sqlite3] [--since ISO] [--until ISO]"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.journal import COLUMNS, Journal  # noqa: E402
from app.metrics import summary  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/journal.sqlite3")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--csv", default="journal.csv")
    args = ap.parse_args()
    j = Journal(Path(args.db))
    rows = j.rows(args.since, args.until)
    cols = COLUMNS
    with open(args.csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
    print(f"{len(rows)} записей -> {args.csv}")
    print(json.dumps(summary(j, args.since, args.until), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
