import datetime as dt
import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml


def normalized(value):
    return " ".join(value.lower().replace("ё", "е").split())


class Catalog:
    def __init__(self, path, production=False):
        path = Path(path)
        self.settings = yaml.safe_load((path / "settings.yaml").read_text(encoding="utf-8"))
        # Подписи участников для дашборда: псевдоним -> «Магазин 4471, ДМ». Заполняет владелец пилота вручную.
        labels = path / "participants.yaml"
        self.participants = (yaml.safe_load(labels.read_text(encoding="utf-8")) or {}) if labels.exists() else {}
        if not isinstance(self.participants, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) for k, v in self.participants.items()
        ):
            raise ValueError("participants.yaml must map pseudonym -> label")
        rows = yaml.safe_load((path / "cards.yaml").read_text(encoding="utf-8"))
        self.cards = {}
        for card in rows:
            cid = card["id"]
            if not re.fullmatch(r"[A-E]-\d{2}", cid) or cid in self.cards:
                raise ValueError("Invalid or duplicate card ID: " + cid)
            if card["section"] != cid[0] or card["section"] not in self.settings["sections"]:
                raise ValueError("Invalid section: " + cid)
            if not 1 <= len(card["title"]) <= 60:
                raise ValueError("Title must have 1–60 characters: " + cid)
            steps = card["steps"]
            if not 1 <= len(steps) <= 6 or any(not isinstance(x, str) or not x.strip() for x in steps):
                raise ValueError("Expected 1–6 nonempty steps: " + cid)
            if len("\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))) > 600:
                raise ValueError("Answer exceeds 600 characters: " + cid)
            if len(set(card["synonyms"])) < 3 or any(not isinstance(s, str) or not s.strip() for s in card["synonyms"]):
                raise ValueError("Expected at least 3 synonyms: " + cid)
            dt.date.fromisoformat(card["date"])
            if not card["version"].strip() or not isinstance(card["available"], bool) or not isinstance(card["approved"], bool):
                raise ValueError("Invalid card metadata: " + cid)
            if card["url"]:
                self.validate_url(card["url"])
            if production and (
                card["status"] != "published" or not card["approved"] or not card["owner"].strip() or not card["url"]
            ):
                raise ValueError("Production requires approval, owner and source URL: " + cid)
            if card["status"] not in ("draft", "published", "retired"):
                raise ValueError("Invalid card status: " + cid)
            if card["status"] == "retired":
                card["available"] = False
            self.cards[cid] = card
        expected = {
            f"{s}-{n:02}" for s, count in [("A", 10), ("B", 8), ("C", 6), ("D", 8), ("E", 3)] for n in range(1, count + 1)
        }
        if set(self.cards) != expected:
            raise ValueError("Catalog must contain the 35 MVP card IDs")
        if self.settings["fallback_url"]:
            self.validate_url(self.settings["fallback_url"])
        if production:
            if not self.settings["channel_approved"] or self.settings["channel_device"] != "phone":
                raise ValueError("Confirm Rooms phone channel before production")
            if not self.settings["fallback_url"]:
                raise ValueError("Set fallback_url")
            for route in self.settings["help_routes"].values():
                if not route["text"].strip() or not route["owner"].strip():
                    raise ValueError("Help routes require text and owner")

    def validate_url(self, url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.hostname not in self.settings["allowed_link_hosts"]
        ):
            raise ValueError("Source URL must use HTTPS and an explicitly allowed internal hostname")

    def search(self, query):
        q = normalized(query)
        # Literal substring of the complete query; no stemming, intent detection or AI.
        ranked = []
        for card in self.cards.values():
            title = normalized(card["title"])
            synonyms = [normalized(x) for x in card["synonyms"]]
            body = normalized(" ".join(card["steps"]))
            if q in title or any(q in x for x in synonyms) or q in body:
                score = 0 if q in synonyms else 1 if q in title else 2
                ranked.append((score, card["id"], card))
        return [c for _, _, c in sorted(ranked)[:5]]
