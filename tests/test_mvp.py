"""Acceptance based on the original Excel: screens, scenarios, registry and answer rules."""

import concurrent.futures
import json
import time

import pytest

from app.content import Catalog
from app.engine import Engine


def last(result):
    return result["messages"][-1]


def actions(result):
    return [b["action"] for b in last(result)["buttons"]]


def test_s0_once(engine, event):
    assert last(engine.handle(event("opened")))["screen"] == "S0"
    assert engine.handle(event("opened"))["messages"] == []
    assert len(engine.handle(event(action="menu"))["messages"]) == 1


def test_s1_exact_menu(engine, event):
    r = last(engine.handle(event(action="menu")))
    assert [b["label"] for b in r["buttons"]] == [
        "Подготовка до ПИ",
        "Связь и сбои",
        "Как снимать",
        "Порядок и после ПИ",
        "Этапы ПИ — справка",
        "Поиск",
    ]


def test_s2_all_35_cards_within_three_clicks(engine, event):
    reached = set()
    for section in "ABCDE":
        first = engine.handle(event(action=f"section:{section}:0"))
        assert len([a for a in actions(first) if a.startswith("card:")]) <= 8
        assert "menu" in actions(first)
        pages = [first]
        if f"section:{section}:1" in actions(first):
            pages.append(engine.handle(event(action=f"section:{section}:1")))
        for page in pages:
            for a in actions(page):
                if a.startswith("card:"):
                    reached.add(a[5:])
                    card = engine.handle(event(action=a))
                    assert last(card)["screen"] == "S3"
                    assert "menu" in actions(card)
    assert reached == set(engine.catalog.cards)
    assert len(reached) == 35


@pytest.mark.parametrize(
    "query,screen",
    [
        ("модем", "S5a"),
        ("интернет", "S5a"),
        ("наушники", "S5a"),
        ("ЭЦП", "S5a"),
        ("не грузит", "S5b"),
        ("модем жираф", "S5b"),
        ("почему не работает интернет", "S7"),
        ("а что делать если модем не ловит сеть", "S7"),
    ],
)
def test_search_direct_and_after_s5(engine, event, query, screen):
    for prompted in (False, True):
        if prompted:
            engine.handle(event(action="search"))
        r = engine.handle(event("message", text=query))
        assert last(r)["screen"] == screen
        assert len([a for a in actions(r) if a.startswith("card:")]) <= 5
        assert "menu" in actions(r)


def test_s4_and_s4a_three_reasons(engine, event):
    for key in ("wrong", "details", "failed"):
        # Each actor represents a separate appeal.
        e = event(action="card:A-07")
        e["user_id"] = key
        r = engine.handle(e)
        no = next(a for a in actions(r) if a.endswith(":no"))
        n = event(action=no)
        n["user_id"] = key
        choices = engine.handle(n)
        assert last(choices)["screen"] == "S4"
        assert [b["label"] for b in last(choices)["buttons"]][:3] == [
            "Не то, что искал",
            "Не хватает деталей",
            "Сделал, не сработало",
        ]
        reason = next(a for a in actions(choices) if a.endswith(":" + key))
        n = event(action=reason)
        n["user_id"] = key
        response = engine.handle(n)
        assert last(response)["screen"] == "S4a"
        assert "menu" in actions(response) and "help" not in actions(response)
    assert {r["value"] for r in engine.journal_rows() if r["kind"] == "reason"} == {"wrong", "details", "failed"}


def test_positive_rating_is_idempotent(engine, event):
    r = engine.handle(event(action="card:A-07"))
    yes = next(a for a in actions(r) if a.endswith(":yes"))
    engine.handle(event(action=yes))
    engine.handle(event(action=yes))
    engine.handle(event(action="card:A-07"))
    m = engine.metrics()
    assert m["card_views"] == 1 and m["ratings"] == 1
    assert m["helpfulness"] == 1 and m["feedback_coverage"] == 1


@pytest.mark.parametrize("kind", ["audio", "image", "file", "video", "malicious-user-string"])
def test_attachments_not_processed(engine, event, kind):
    r = engine.handle(event("attachment", attachment_type=kind))
    assert last(r)["screen"] == "S7"
    assert "menu" in actions(r)
    assert engine.journal_rows()[0]["value"] in ("audio", "image", "file", "video", "other")


def test_thanks_not_counted(engine, event):
    engine.handle(event("message", text="спасибо"))
    assert engine.metrics()["card_views"] == 0


def test_no_gk_actions(engine, event):
    r = engine.handle(event("message", text="занеси паллеты в S9999"))
    assert "GK и Inventa" in last(r)["text"]
    assert last(r)["screen"] == "S7"


def test_no_human_help_and_unavailable(engine, event):
    assert last(engine.handle(event(action="help")))["screen"] == "S7"  # маршрут помощи убран из MVP
    assert last(engine.handle(event(action="help:tech")))["screen"] == "S7"
    assert "help" not in actions(engine.handle(event(action="menu")))
    assert last(engine.handle(event(action="card:Z-99")))["screen"] == "S8"
    engine.catalog.cards["B-07"]["available"] = False
    assert last(engine.handle(event(action="card:B-07")))["screen"] == "S8"


def test_close_and_expiry_no_messages(engine, event):
    engine.handle(event("opened"), now=100000)
    assert engine.handle(event("closed"), now=100001)["messages"] == []
    assert last(engine.handle(event("opened"), now=100002))["screen"] == "S0"
    assert last(engine.handle(event("opened"), now=200000))["screen"] == "S0"


def test_no_raw_text_or_ids_in_database(engine, event):
    raw = "ПИ магазина 1234: недостача 5000. Иван Тестов."
    engine.handle(event("message", text=raw))
    engine.handle(event("message", text="Сидоров"))
    with engine.connect() as db:
        dump = "\n".join(db.iterdump())
    assert raw not in dump and "Сидоров" not in dump
    assert "test-user" not in dump and "test-chat" not in dump


def test_duplicate_event_survives_restart(engine, event, settings):
    e = event(action="card:A-01")
    first = engine.handle(e)
    other = Engine(Catalog(settings.content_dir), settings.db_path, settings.state_secret)
    assert other.handle(e) == first
    assert other.metrics()["card_views"] == 1
    assert other.handle(event("opened"))["messages"] == []


def test_conflicting_duplicate_rejected(engine, event):
    e = event(action="menu")
    engine.handle(e)
    e["action"] = "help"
    with pytest.raises(ValueError):
        engine.handle(e)


def test_concurrent_duplicates(engine, event):
    e = event(action="card:A-01")
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: engine.handle(e), range(8)))
    assert all(r == results[0] for r in results)
    assert engine.metrics()["card_views"] == 1


def test_old_version_rating_uses_shown_version(engine, event):
    r = engine.handle(event(action="card:A-07"))
    yes = next(a for a in actions(r) if a.endswith(":yes"))
    engine.catalog.cards["A-07"]["version"] = "2.0"
    engine.handle(event(action=yes))
    row = next(r for r in engine.journal_rows() if r["kind"] == "rating")
    assert row["version"] == "0.1.0"


def test_rating_cannot_be_used_in_other_chat(engine, event):
    yes = next(a for a in actions(engine.handle(event(action="card:A-07"))) if a.endswith(":yes"))
    e = event(action=yes)
    e["conversation_id"] = "another-chat"
    assert last(engine.handle(e))["screen"] == "S7"
    engine.handle(event("closed"))
    assert last(engine.handle(event(action=yes)))["screen"] == "S7"
    assert engine.metrics()["ratings"] == 0


def test_retention(engine, event):
    engine.handle(event("message", text="модем"), now=time.time() - 40 * 86400)
    assert engine.metrics()["groups"] == []


def test_card_content_not_generated(engine):
    sources = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] / "docs/source-scenarios.json").read_text())
    assert "Диалог сценарии" in sources
    assert len(engine.catalog.cards) == 35
    for card in engine.catalog.cards.values():
        assert len(card["title"]) <= 60
        assert 1 <= len(card["steps"]) <= 6
        assert len("\n".join(f"{i}. {s}" for i, s in enumerate(card["steps"], 1))) <= 600
        assert len(card["synonyms"]) >= 3
        assert card["status"] == "draft" and not card["approved"]


def test_new_request_can_complete_previously_failed_appeal(engine, event):
    first = engine.handle(event(action="card:A-07"), deferred=True)
    assert engine.metrics()["card_views"] == 0
    second = engine.handle(event(action="card:A-07"), deferred=True)
    engine.mark_complete(second["reply_id"])
    assert engine.metrics()["card_views"] == 1
    engine.mark_complete(first["reply_id"])
    assert engine.metrics()["card_views"] == 1
