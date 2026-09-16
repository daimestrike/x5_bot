"""Consistent SQLite backup (including committed WAL contents)."""

import argparse
import os
import sqlite3
from pathlib import Path

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("destination")
    ap.add_argument("--db", default="data/bot-v2.sqlite3")
    args = ap.parse_args()
    target = Path(args.destination)
    os.umask(0o077)
    fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as src:
        with sqlite3.connect(target) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit("Backup integrity check failed")
    print("Backup completed")
