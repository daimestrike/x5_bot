"""Поиск по ключевым словам: подстрока по заголовку, синонимам и тексту карточек.

Не распознавание смысла. Запрос — одно-два слова. Токены короче 3 знаков не учитываются.
"""
from __future__ import annotations

import re
from typing import List

from .content import Card, Content

MAX_RESULTS = 5
MAX_QUERY_WORDS = 2
MIN_TOKEN_LEN = 3

_PUNCT_RE = re.compile(r"[^\w\s-]", re.UNICODE)


def normalize(text: str) -> str:
    text = (text or "").lower().replace("ё", "е")
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def tokens(query: str) -> List[str]:
    return [t for t in normalize(query).split() if len(t) >= MIN_TOKEN_LEN]


def is_search_query(text: str) -> bool:
    """Одно-два слова — это поиск; длиннее — свободный текст (S7)."""
    words = normalize(text).split()
    return 1 <= len(words) <= MAX_QUERY_WORDS


def search(content: Content, query: str, limit: int = MAX_RESULTS) -> List[Card]:
    toks = tokens(query)
    if not toks:
        return []
    scored = []
    for card in content.cards.values():
        blob = {k: v.replace("ё", "е") for k, v in card.search_blob().items()}
        score = 0
        matched = 0
        for t in toks:
            hit = 0
            if t in blob["synonyms"]:
                hit = max(hit, 3)
            if t in blob["title"]:
                hit = max(hit, 2)
            if t in blob["text"]:
                hit = max(hit, 1)
            if hit:
                matched += 1
                score += hit
        if matched:
            # совпадение по всем словам запроса важнее совпадения по одному
            score += 10 * matched
            scored.append((score, card.id, card))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _, _, c in scored[:limit]]
