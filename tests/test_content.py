"""Приёмка: содержание (п. 12, 13), правила ответов."""
from app.content import STEPS_MAX, TEXT_MAX, TITLE_MAX


def test_all_cards_loaded(content):
    assert len(content.cards) == 35
    assert {s.id: len(s.cards) for s in content.sections} == {"A": 10, "B": 8, "C": 6, "D": 8, "E": 3}


def test_card_rules(content):
    for c in content.cards.values():
        assert len(c.title) <= TITLE_MAX, c.id
        assert len(c.text) <= TEXT_MAX, c.id
        assert 1 <= len(c.steps) <= STEPS_MAX, c.id
        assert len(c.synonyms) >= 3, c.id
        assert c.version and c.owner, c.id
        assert content.link_for(c), c.id


def test_no_secret_values(content):
    """Слово «пароль» допустимо только как указание, где смотреть; значений вида ключ=значение нет."""
    import re
    for c in content.cards.values():
        assert not re.search(r"(пароль|password|pin)\s*[:=]\s*\S", c.text, re.I), c.id


def test_help_routes(content):
    assert [r.key for r in content.help_routes] == ["pi", "tech", "org"]
    for r in content.help_routes:
        txt = content.help_text(r.key)
        assert txt and "{contacts." not in txt


def test_placeholder_links_rendered(content):
    for cid in ("A-06", "E-01"):
        steps = content.render_steps(content.cards[cid])
        assert "<ссылка>" not in " ".join(steps)
        assert "http" in " ".join(steps)
