"""Validate draft structure or production readiness without creating a database."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.content import Catalog  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("content_dir", nargs="?", default="content")
    ap.add_argument("--production", action="store_true")
    args = ap.parse_args()
    try:
        c = Catalog(args.content_dir, production=args.production)
    except (ValueError, KeyError, TypeError) as error:
        raise SystemExit("Content validation failed: " + str(error)) from None
    print(
        json.dumps(
            {"cards": len(c.cards), "approved": sum(c["approved"] for c in c.cards.values()), "production": args.production},
            ensure_ascii=False,
        )
    )
