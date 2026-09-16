"""Export only the anonymized analytical fields. Does not open or migrate a legacy journal."""

import argparse
import csv
import sqlite3
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/bot-v2.sqlite3")
    parser.add_argument("--csv", default="journal.csv")
    args = parser.parse_args()
    with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT time,kind,topic,version,value,delivered FROM events ORDER BY id")
        with open(args.csv, "x", encoding="utf-8-sig", newline="") as out:
            writer = csv.writer(out, delimiter=";")
            writer.writerow(["time", "kind", "topic", "version", "value", "delivered"])
            writer.writerows(rows)
    print("Export completed. Apply the journal retention policy to exports too.")
