"""Очередь верификации содержания и правка карточек с версионированием.

Ничего из очереди не попадает в бот само: владелец содержания либо подтверждает, что карточка
актуальна, либо правит текст (новая версия, approved=false), затем проверяющий утверждает.
Очередь — content/review/pending.json; карточки — content/cards.yaml (та же схема, что читает Catalog).
"""

import datetime as dt
import json
import re
import uuid
from pathlib import Path

import yaml

from .content import Catalog

VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ReviewError(ValueError):
    pass


def bump(version):
    m = VERSION_RE.match(version.strip())
    if not m:
        return version.strip() + ".1"
    major, minor, patch = (int(x) for x in m.groups())
    return f"{major}.{minor + 1}.0" if patch == 0 else f"{major}.{minor}.{patch + 1}"


class ContentStore:
    def __init__(self, content_dir):
        self.dir = Path(content_dir)
        self.cards_path = self.dir / "cards.yaml"
        self.queue_path = self.dir / "review" / "pending.json"
        self.queue_path.parent.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- карточки
    def read_cards(self):
        return yaml.safe_load(self.cards_path.read_text(encoding="utf-8"))

    def write_cards(self, cards):
        """Проверяет через Catalog и пишет атомарно; при ошибке файл не меняется."""
        tmp_dir = self.dir / "review" / ".validate"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        (tmp_dir / "cards.yaml").write_text(yaml.safe_dump(cards, allow_unicode=True, sort_keys=False), encoding="utf-8")
        for name in ("settings.yaml", "participants.yaml"):
            src = self.dir / name
            if src.exists():
                (tmp_dir / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        try:
            Catalog(tmp_dir)
        except (ValueError, KeyError, TypeError) as e:
            raise ReviewError("Карточка не прошла проверку: " + str(e)) from e
        tmp = self.cards_path.with_suffix(".yaml.tmp")
        tmp.write_text(yaml.safe_dump(cards, allow_unicode=True, sort_keys=False), encoding="utf-8")
        tmp.replace(self.cards_path)

    def update_card(self, card_id, fields, editor):
        """Новая версия карточки: меняются только переданные поля, approved сбрасывается."""
        cards = self.read_cards()
        card = next((c for c in cards if c["id"] == card_id), None)
        if card is None:
            raise ReviewError("Нет карточки " + card_id)
        allowed = {"title", "steps", "synonyms", "url", "source", "source_key", "available"}
        bad = set(fields) - allowed
        if bad:
            raise ReviewError("Нельзя менять поля: " + ", ".join(sorted(bad)))
        changed = {k: v for k, v in fields.items() if card.get(k) != v}
        if not changed:
            return card
        history = card.setdefault("history", [])
        history.append({"version": card["version"], "date": card["date"], "approved": card.get("approved", False),
                        "owner": card.get("owner", ""), "changed": sorted(changed)})
        card.update(changed)
        card["version"] = bump(card["version"])
        card["date"] = dt.date.today().isoformat()
        card["approved"] = False
        card["status"] = "draft" if card["status"] == "retired" and fields.get("available") else card["status"]
        card["edited_by"] = editor
        self.write_cards(cards)
        return card

    def approve(self, card_id, reviewer, approved=True):
        cards = self.read_cards()
        card = next((c for c in cards if c["id"] == card_id), None)
        if card is None:
            raise ReviewError("Нет карточки " + card_id)
        if approved and not reviewer.strip():
            raise ReviewError("Укажите, кто утверждает")
        card["approved"] = bool(approved)
        card["owner"] = reviewer.strip() if approved else card.get("owner", "")
        card["approved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds") if approved else ""
        if approved:
            card["status"] = "published"
        self.write_cards(cards)
        return card

    # ---------------------------------------------------------------- очередь
    def read_queue(self):
        if not self.queue_path.exists():
            return []
        return json.loads(self.queue_path.read_text(encoding="utf-8"))

    def write_queue(self, items):
        tmp = self.queue_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.queue_path)

    def enqueue(self, items):
        queue = self.read_queue()
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for it in items:
            it.setdefault("id", uuid.uuid4().hex[:12])
            it.setdefault("created", now)
            it.setdefault("status", "open")
        queue.extend(items)
        self.write_queue(queue)
        return items

    def resolve(self, item_id, decision, who, note=""):
        if decision not in ("confirmed", "edited", "dismissed"):
            raise ReviewError("decision: confirmed | edited | dismissed")
        queue = self.read_queue()
        item = next((i for i in queue if i["id"] == item_id), None)
        if item is None:
            raise ReviewError("Нет элемента очереди " + item_id)
        item.update(status=decision, resolved_by=who, resolved_at=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                    note=note[:500])
        self.write_queue(queue)
        return item

    def open_items(self):
        return [i for i in self.read_queue() if i["status"] == "open"]


def source_change_items(source, changes, cards, match):
    """Изменения документа -> элементы очереди: по одному на карточку, плюс «без карточки» для остального."""
    items = []
    for ch in changes:
        matched = match(ch, cards, source["key"])
        base = {
            "kind": "source_changed",
            "source": source["key"],
            "source_title": source["title"],
            "source_version": source["version"],
            "change": ch["kind"],
            "page": (ch.get("new") or ch.get("old") or {}).get("page"),
            "unit": (ch.get("new") or ch.get("old") or {}).get("n"),
            "old": (ch.get("old") or {}).get("text"),
            "new": (ch.get("new") or {}).get("text"),
            "diff": ch["diff"],
        }
        if matched:
            for m in matched:
                items.append({**base, "card_id": m["id"], "score": m["score"]})
        else:
            items.append({**base, "card_id": None, "score": None})
    return items
