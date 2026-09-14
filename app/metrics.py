"""Сводка по журналу — лист «Метрики» (MVP v11).

Правила учёта: одна тема в одном сеансе — одно обращение; итоговая оценка обращения — последняя;
повторное нажатие счётчик не увеличивает; при нулевом знаменателе — «нет данных» (null).
Показатели «на одну ПИ» (использование, нагрузка ревизора) считаются вручную после привязки сеансов к ПИ.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from . import journal as J


def summary(journal: J.Journal, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
    rows = journal.rows(since, until)
    by_event = Counter(r["event"] for r in rows)
    sessions = {r["session_id"] for r in rows}

    # обращение = (сеанс, тема); повторный показ той же темы в сеансе — то же обращение
    appeals: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        if r["event"] == J.CARD_VIEW:
            key = (r["session_id"], r["card_id"])
            a = appeals.setdefault(key, {"views": 0, "rating": None, "help": False, "version": r["card_version"],
                                         "first_ts": r["ts"], "user_ref": r["user_ref"]})
            a["views"] += 1
            a["version"] = r["card_version"]
    for r in rows:
        key = (r["session_id"], r["card_id"])
        if r["event"] == J.RATING and key in appeals:
            appeals[key]["rating"] = r["rating"]  # последняя оценка — итоговая
        elif r["event"] == J.HELP_REQUEST and r["card_id"] and key in appeals:
            appeals[key]["help"] = True

    rated = [a for a in appeals.values() if a["rating"]]
    helped = sum(1 for a in rated if a["rating"] == "helped")
    with_help = sum(1 for a in appeals.values() if a["help"])
    help_without_topic = sum(1 for r in rows if r["event"] == J.HELP_REQUEST and not r["card_id"])

    not_helped_by_card = Counter(cid for (_, cid), a in appeals.items() if a["rating"] == "not_helped")
    views_by_card = Counter(cid for (_, cid) in appeals)
    questions = Counter((r["query"] or "").strip().lower() for r in rows if r["event"] == J.QUESTION and r["query"])
    missed = Counter((r["query"] or "").lower() for r in rows if r["event"] == J.SEARCH_MISS and r["query"])
    help_types = Counter(r["help_type"] for r in rows if r["event"] == J.HELP_REQUEST)

    return {
        "period": {"since": since, "until": until},
        "sessions": len(sessions),
        "participants": len({a["user_ref"] for a in appeals.values()}),
        "answers_issued": len(appeals),                      # выданные ответы: уникальные обращения с показом
        "card_views_total": sum(a["views"] for a in appeals.values()),
        "rating_coverage_pct": _pct(len(rated), len(appeals)),   # охват оценкой
        "usefulness_pct": _pct(helped, len(rated)),              # полезность
        "help_share_pct": _pct(with_help, len(appeals)),         # запрос помощи после темы
        "help_without_topic": help_without_topic,
        "helped": helped,
        "not_helped": len(rated) - helped,
        "help_requests_by_type": dict(help_types),
        "gaps": {
            "cards_not_helped": [{"card_id": c, "not_helped": n, "appeals": views_by_card[c]}
                                 for c, n in not_helped_by_card.most_common(10)],
            "questions_without_answer": [{"text": q, "count": n} for q, n in questions.most_common(30)],
            "search_misses": [{"query": q, "count": n} for q, n in missed.most_common(20)],
        },
        "events": dict(by_event),
        "note": "Использование и нагрузка на одну ПИ считаются вручную после привязки сеансов к ПИ "
                "(см. /metrics/appeals.csv: участник, сеанс, время).",
    }


def appeals_table(journal: J.Journal, since: Optional[str] = None, until: Optional[str] = None) -> List[Dict[str, Any]]:
    """Таблица обращений для ручной привязки к ПИ: участник, сеанс, время, тема, версия, итоговая оценка, помощь."""
    rows = journal.rows(since, until)
    table: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        key = (r["session_id"], r["card_id"])
        if r["event"] == J.CARD_VIEW:
            t = table.setdefault(key, {"user_ref": r["user_ref"], "session_id": r["session_id"], "first_ts": r["ts"],
                                       "last_ts": r["ts"], "card_id": r["card_id"], "card_version": r["card_version"],
                                       "views": 0, "final_rating": "", "help": ""})
            t["views"] += 1
            t["last_ts"] = r["ts"]
        elif key in table and r["event"] == J.RATING:
            table[key]["final_rating"] = r["rating"]
            table[key]["last_ts"] = r["ts"]
        elif key in table and r["event"] == J.HELP_REQUEST:
            table[key]["help"] = r["help_type"]
            table[key]["last_ts"] = r["ts"]
    return list(table.values())


def _pct(num: int, den: int) -> Optional[float]:
    return None if not den else round(num * 100.0 / den, 1)
