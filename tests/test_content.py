"""Содержание: формат карточки v11, правила листа «Подготовка чек-листов», версии и статусы."""
from pathlib import Path

import pytest
import yaml

from app.content import STEPS_MAX, TEXT_MAX, TITLE_MAX, ContentError, load_content


def test_all_cards_published(content):
    assert len(content.cards) == 35
    assert {t.id: len(content.cards_of_type(t.id)) for t in content.types} == {"answer": 18, "instruction": 6, "checklist": 11}


def test_card_rules(content):
    for c in content.cards.values():
        assert len(c.title) <= TITLE_MAX, c.id
        assert len(c.text) <= TEXT_MAX, c.id
        assert 1 <= len(c.steps) <= STEPS_MAX, c.id
        assert c.version and c.checked and c.help, c.id
        assert content.link_for(c), c.id
        if c.type == "checklist":
            assert c.when and c.result, c.id


def test_no_secret_values(content):
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
        assert "<ссылка>" not in " ".join(steps) and "http" in " ".join(steps)


def _write_content(tmp_path: Path, cards: list) -> Path:
    src = Path(__file__).resolve().parents[1] / "content"
    for name in ("screens.yaml", "help_routes.yaml", "params.yaml"):
        (tmp_path / name).write_text((src / name).read_text(encoding="utf-8"), encoding="utf-8")
    base = yaml.safe_load((src / "cards.yaml").read_text(encoding="utf-8"))
    (tmp_path / "cards.yaml").write_text(
        yaml.safe_dump({"types": base["types"], "groups": base["groups"], "cards": cards}, allow_unicode=True),
        encoding="utf-8")
    return tmp_path


def _card(**over):
    c = {"id": "A-01", "type": "answer", "group": "A", "title": "Тема", "steps": ["Шаг"], "help": "Ревизор",
         "version": "1.0", "checked": "2026-09-14", "owner": "И.", "reviewer": "П.", "status": "published"}
    c.update(over)
    return c


def test_only_published_is_served_old_versions_kept(tmp_path):
    d = _write_content(tmp_path, [
        _card(version="1.0", status="retired", steps=["Старый текст"]),
        _card(version="2.0", status="published", steps=["Новый текст"]),
        _card(id="A-02", status="draft"),
    ])
    c = load_content(d)
    assert list(c.cards) == ["A-01"] and c.cards["A-01"].version == "2.0"
    assert [v.status for v in c.all_versions["A-01"]] == ["retired", "published"]
    assert "A-02" in c.all_versions and "A-02" not in c.cards


def test_two_published_versions_is_error(tmp_path):
    d = _write_content(tmp_path, [_card(version="1.0"), _card(version="2.0")])
    with pytest.raises(ContentError, match="опубликовано 2 версий"):
        load_content(d)


def test_checklist_requires_result(tmp_path):
    d = _write_content(tmp_path, [_card(type="checklist", when="Когда", result="")])
    with pytest.raises(ContentError, match="ожидаемый результат"):
        load_content(d)
