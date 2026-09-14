"""Сводка по журналу для листа «Метрики». Показатели «на одну ПИ» считаются вручную на пилоте."""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional

from . import journal as J


def summary(journal: J.Journal, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    rows = journal.rows(since, until)
    by_event = Counter(r["event"] for r in rows)
    sessions = {r["session_id"] for r in rows}

    # обращение = показ карточки; оценка привязывается к карточке в рамках сеанса
    appeals = [r for r in rows if r["event"] == J.CARD_VIEW]
    ratings = [r for r in rows if r["event"] == J.RATING]
    rated_pairs = {(r["session_id"], r["card_id"]) for r in ratings}
    appeal_pairs = {(r["session_id"], r["card_id"]) for r in appeals}
    helped = sum(1 for r in ratings if r["rating"] == "helped")
    not_helped = sum(1 for r in ratings if r["rating"] == "not_helped")

    not_helped_by_card = Counter(r["card_id"] for r in ratings if r["rating"] == "not_helped")
    views_by_card = Counter(r["card_id"] for r in appeals)
    missed_queries = Counter((r["query"] or "").lower() for r in rows if r["event"] == J.SEARCH_MISS)
    free_phrases = Counter((r["query"] or "").lower() for r in rows if r["event"] == J.UNRECOGNIZED)
    help_types = Counter(r["help_type"] for r in rows if r["event"] == J.HELP_REQUEST)
    clarify = Counter(r["reason"] for r in rows if r["event"] == J.CLARIFY)

    rated_cov = len(rated_pairs & appeal_pairs) / len(appeal_pairs) if appeal_pairs else None
    usefulness = helped / (helped + not_helped) if (helped + not_helped) else None
    return {
        "period": {"since": since, "until": until},
        "sessions": len(sessions),
        "appeals": len(appeal_pairs),
        "card_views_total": len(appeals),
        "rating_coverage": _pct(rated_cov),
        "usefulness": _pct(usefulness),
        "helped": helped,
        "not_helped": not_helped,
        "clarify_reasons": dict(clarify),
        "help_requests": dict(help_types),
        "gaps": {
            "cards_not_helped": [{"card_id": c, "not_helped": n, "views": views_by_card[c]}
                                 for c, n in not_helped_by_card.most_common(10)],
            "search_misses": [{"query": q, "count": n} for q, n in missed_queries.most_common(20)],
            "free_text": [{"phrase": q, "count": n} for q, n in free_phrases.most_common(20)],
        },
        "events": dict(by_event),
    }


def _pct(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v * 100, 1)
