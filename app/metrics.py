"""Продуктовые метрики бота по разделу 8 MVP («Журнал и измерение пользы»).

Считаются по обезличенному журналу событий (таблица events). Показатели «на одну ПИ»
(использование, отвлечения ревизора, качество ПИ) в журнале посчитать нельзя — они
собираются вручную на пилоте; здесь даётся сырьё: сеансы, обращения, оценки, пробелы базы.
"""

import datetime as dt
import time
from collections import Counter, defaultdict

TARGETS = {"feedback_coverage": 0.5, "helpfulness": 0.7}
REASON_LABELS = {"wrong": "Не то, что искал", "details": "Не хватает деталей", "failed": "Сделал, не сработало"}


def _pct(num, den):
    return round(num / den, 3) if den else None


def product_metrics(engine, days=30, now=None):
    now = time.time() if now is None else now
    days = max(1, min(int(days), 365))
    since = now - days * 86400
    with engine.connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT time,kind,topic,version,value,interaction,participant FROM events e WHERE time>=? AND delivered=1 "
                "AND (kind NOT IN ('rating','reason') OR EXISTS (SELECT 1 FROM events v "
                "WHERE v.kind='card' AND v.interaction=e.interaction AND v.delivered=1)) ORDER BY id",
                (since,),
            )
        ]
    catalog = engine.catalog
    sections = catalog.settings["sections"]

    by_kind = Counter(r["kind"] for r in rows)
    views = [r for r in rows if r["kind"] == "card"]
    ratings = [r for r in rows if r["kind"] == "rating"]
    reasons = [r for r in rows if r["kind"] == "reason"]
    helped = sum(1 for r in ratings if r["value"] == "yes")
    not_helped = len(ratings) - helped

    # Карточки: показы, оценки, полезность, причины
    per_card = defaultdict(lambda: {"views": 0, "yes": 0, "no": 0, "reasons": Counter()})
    for r in views:
        per_card[r["topic"]]["views"] += 1
    for r in ratings:
        per_card[r["topic"]]["yes" if r["value"] == "yes" else "no"] += 1
    for r in reasons:
        per_card[r["topic"]]["reasons"][r["value"]] += 1
    cards = []
    for cid, c in per_card.items():
        rated = c["yes"] + c["no"]
        card = catalog.cards.get(cid, {})
        cards.append(
            {
                "id": cid,
                "title": card.get("title", cid),
                "section": cid[0],
                "views": c["views"],
                "rated": rated,
                "helpfulness": _pct(c["yes"], rated),
                "not_helped": c["no"],
                "reasons": {REASON_LABELS.get(k, k): v for k, v in c["reasons"].most_common()},
            }
        )
    cards.sort(key=lambda c: (-c["views"], c["id"]))

    # Разделы
    per_section = []
    for key, name in sections.items():
        sv = [c for c in cards if c["section"] == key]
        yes = sum(per_card[c["id"]]["yes"] for c in sv)
        rated = sum(c["rated"] for c in sv)
        per_section.append(
            {"section": key, "name": name, "views": sum(c["views"] for c in sv), "rated": rated, "helpfulness": _pct(yes, rated)}
        )

    # Динамика по дням (UTC)
    daily = defaultdict(lambda: {"sessions": 0, "views": 0, "ratings": 0, "helped": 0})
    for r in rows:
        day = dt.datetime.fromtimestamp(r["time"], dt.timezone.utc).date().isoformat()
        if r["kind"] == "session_start":
            daily[day]["sessions"] += 1
        elif r["kind"] == "card":
            daily[day]["views"] += 1
        elif r["kind"] == "rating":
            daily[day]["ratings"] += 1
            daily[day]["helped"] += r["value"] == "yes"
    start = dt.datetime.fromtimestamp(since, dt.timezone.utc).date()
    series = []
    for i in range(days):
        day = (start + dt.timedelta(days=i + 1)).isoformat()
        series.append({"date": day, **daily.get(day, {"sessions": 0, "views": 0, "ratings": 0, "helped": 0})})

    # Участники: псевдоним -> активность; подпись из content/participants.yaml
    per_user = defaultdict(lambda: {"sessions": 0, "views": 0, "yes": 0, "no": 0, "last": 0.0, "first": None})
    for r in rows:
        pid = r["participant"]
        if not pid:
            continue
        u = per_user[pid]
        u["last"] = max(u["last"], r["time"])
        u["first"] = r["time"] if u["first"] is None else min(u["first"], r["time"])
        if r["kind"] == "session_start":
            u["sessions"] += 1
        elif r["kind"] == "card":
            u["views"] += 1
        elif r["kind"] == "rating":
            u["yes" if r["value"] == "yes" else "no"] += 1
    labels = getattr(catalog, "participants", {}) or {}
    participants = []
    for pid, u in per_user.items():
        rated = u["yes"] + u["no"]
        participants.append(
            {
                "id": pid,
                "label": labels.get(pid, ""),
                "sessions": u["sessions"],
                "appeals": u["views"],
                "rated": rated,
                "helpfulness": _pct(u["yes"], rated),
                "first_seen": dt.datetime.fromtimestamp(u["first"], dt.timezone.utc).isoformat(timespec="minutes"),
                "last_seen": dt.datetime.fromtimestamp(u["last"], dt.timezone.utc).isoformat(timespec="minutes"),
            }
        )
    participants.sort(key=lambda u: (-u["appeals"], -u["sessions"], u["id"]))

    reason_totals = Counter(r["value"] for r in reasons)
    gaps = [c for c in cards if c["not_helped"] >= 1]
    gaps.sort(key=lambda c: (-c["not_helped"], -c["views"]))

    coverage = _pct(len(ratings), len(views))
    helpfulness = _pct(helped, len(ratings))
    return {
        "period": {"days": days, "since": dt.datetime.fromtimestamp(since, dt.timezone.utc).isoformat(timespec="seconds"),
                   "until": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds")},
        "targets": TARGETS,
        "summary": {
            "sessions": by_kind["session_start"],
            "appeals": len(views),
            "ratings": len(ratings),
            "helped": helped,
            "not_helped": not_helped,
            "feedback_coverage": coverage,
            "helpfulness": helpfulness,
            "coverage_ok": None if coverage is None else coverage >= TARGETS["feedback_coverage"],
            "helpfulness_ok": None if helpfulness is None else helpfulness >= TARGETS["helpfulness"],
            "search": by_kind["search"],
            "search_miss": by_kind["search_miss"],
            "unknown_input": by_kind["unknown_input"],
            "attachments": by_kind["attachment"],
            "material_errors": by_kind["material_error"],
            "delivery_expired": by_kind["delivery_expired"],
            "appeals_per_session": _pct(len(views), by_kind["session_start"]),
            "participants": len(participants),
            "participants_labeled": sum(1 for u in participants if u["label"]),
        },
        "participants": participants,
        "reasons": [{"reason": k, "name": v, "count": reason_totals.get(k, 0)} for k, v in REASON_LABELS.items()],
        "sections": per_section,
        "top_cards": cards[:10],
        "gaps": gaps[:10],
        "series": series,
        "note": "Использование на одну ПИ, отвлечения ревизора и качество ПИ считаются вручную на пилоте.",
    }
