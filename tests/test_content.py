import sqlite3

import pytest
import yaml

from app.content import Catalog
from app.engine import Engine


def test_production_refuses_draft(settings):
    with pytest.raises(ValueError, match="Production"):
        Catalog(settings.content_dir, production=True)


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "x" * 61),
        ("steps", ["x"] * 7),
        ("steps", ["x" * 601]),
        ("synonyms", ["one"]),
        ("date", "wrong"),
        ("section", "Z"),
        ("status", "invalid"),
        ("url", "http://external.invalid"),
    ],
)
def test_content_validation(settings, tmp_path, field, value):
    cards = yaml.safe_load((settings.content_dir / "cards.yaml").read_text())
    cards[0][field] = value
    (tmp_path / "cards.yaml").write_text(yaml.safe_dump(cards, allow_unicode=True))
    (tmp_path / "settings.yaml").write_text((settings.content_dir / "settings.yaml").read_text())
    with pytest.raises(ValueError):
        Catalog(tmp_path)


def test_invalid_existing_database_not_migrated_silently(settings):
    with sqlite3.connect(settings.db_path) as db:
        db.execute("CREATE TABLE events(id INTEGER, user_ref TEXT)")
    with pytest.raises(ValueError, match="Legacy"):
        Engine(Catalog(settings.content_dir), settings.db_path, settings.state_secret)


def test_registry_import_creates_drafts(tmp_path):
    import openpyxl

    from scripts.import_registry import import_registry

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Реестр материалов"
    ws.append(["ID", "Заголовок", "Синонимы", "Текст", "Источник"])
    ws.append(["A-01", "Вопрос", "а, б, в", "1. Первый шаг. 2. Второй шаг.", "Инструкция"])
    target = tmp_path / "source.xlsx"
    wb.save(target)
    card = import_registry(target)[0]
    assert card["steps"] == ["Первый шаг.", "Второй шаг."]
    assert card["status"] == "draft" and not card["approved"] and not card["owner"]
