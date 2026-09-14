"""Приёмка п. 6: поиск по словам."""
import pytest

from app.search import is_search_query, search


@pytest.mark.parametrize("q,expected", [
    ("модем", "A-07"),
    ("пароль", "B-02"),
    ("зонирование", "A-10"),
    ("наушники", "B-04"),
    ("ЭЦП", "A-02"),
    ("интернет", "B-01"),
    ("Ёлка", None),
])
def test_keywords(content, q, expected):
    ids = [c.id for c in search(content, q)]
    if expected is None:
        assert ids == []
    else:
        assert expected in ids, ids
        assert len(ids) <= 5


def test_modem_scenario_3(content):
    ids = [c.id for c in search(content, "модем")]
    assert "A-07" in ids and "B-02" in ids and "B-01" in ids


def test_two_words_rank_both(content):
    ids = [c.id for c in search(content, "сим модем")]
    assert ids[0] == "A-07"


def test_short_tokens_ignored(content):
    assert search(content, "не грузит") == []
    assert search(content, "не") == []


def test_query_vs_free_text():
    assert is_search_query("модем")
    assert is_search_query("не грузит")
    assert not is_search_query("а что делать если модем не ловит сеть")
