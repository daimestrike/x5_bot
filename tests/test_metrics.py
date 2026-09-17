"""Продуктовые метрики: расчёт по журналу и страница дашборда."""
from fastapi.testclient import TestClient

from app.main import create_app
from app.metrics import product_metrics


def rate_button(result, value):
    buttons = result["messages"][-1]["buttons"]
    return next(b["action"] for b in buttons if b["action"].startswith("rate:") and b["action"].endswith(value))


def test_product_metrics_from_journal(engine, event):
    r = engine.handle(event(action="card:A-07"))
    engine.handle(event(action=rate_button(r, "yes")))
    r = engine.handle(event(action="card:B-02"))
    no = rate_button(r, "no")
    engine.handle(event(action=no))
    engine.handle(event(action="reason:" + no.split(":")[1] + ":details"))
    engine.handle(event(action="card:B-02"))  # повторный показ в том же сеансе — то же обращение
    engine.handle(event("message", text="не грузит"))
    engine.handle(event("message", text="а что делать если модем не ловит сеть"))

    m = product_metrics(engine, days=7)
    s = m["summary"]
    assert s["sessions"] == 1 and s["appeals"] == 2 and s["ratings"] == 2
    assert s["helped"] == 1 and s["not_helped"] == 1
    assert s["feedback_coverage"] == 1.0 and s["coverage_ok"] is True
    assert s["helpfulness"] == 0.5 and s["helpfulness_ok"] is False
    assert s["search_miss"] == 1 and s["unknown_input"] == 1
    assert m["gaps"][0]["id"] == "B-02" and m["gaps"][0]["reasons"] == {"Не хватает деталей": 1}
    assert next(x for x in m["sections"] if x["section"] == "B")["views"] == 1
    assert [c["id"] for c in m["top_cards"]] == ["A-07", "B-02"]
    assert len(m["series"]) == 7 and sum(d["views"] for d in m["series"]) == 2


def test_product_metrics_empty(engine):
    m = product_metrics(engine, days=30)
    assert m["summary"]["feedback_coverage"] is None and m["summary"]["coverage_ok"] is None
    assert m["gaps"] == [] and m["top_cards"] == []


def test_dashboard_page_and_api(settings):
    with TestClient(create_app(settings)) as c:
        page = c.get("/metrics/dashboard")
        assert page.status_code == 200 and "Справочник Аватара" in page.text
        assert c.get("/metrics/dashboard.js").status_code == 200
        assert c.get("/metrics/dashboard.css").status_code == 200
        assert c.get("/metrics/product").status_code == 401
        h = {"Authorization": "Bearer " + settings.metrics_token}
        assert c.get("/metrics/product?days=abc", headers=h).status_code == 400
        body = c.get("/metrics/product?days=7", headers=h).json()
        assert body["period"]["days"] == 7 and "delivery" in body and body["mode"] == "demo"


def test_participants_are_pseudonymous_and_labelled(settings, tmp_path, event):
    import shutil

    from app.content import Catalog
    from app.engine import Engine

    content = tmp_path / "content"
    shutil.copytree(settings.content_dir, content)
    engine = Engine(Catalog(content), settings.db_path, settings.state_secret)
    pid = engine.participant("test-user")
    (content / "participants.yaml").write_text(f'{pid}: "Магазин 4471, ДМ"\n', encoding="utf-8")
    engine = Engine(Catalog(content), settings.db_path, settings.state_secret)
    engine.handle(event(action="card:A-07"))
    engine.handle(dict(event_id="x1", user_id="other", conversation_id="c2", type="action", action="card:B-01"))

    m = product_metrics(engine, days=7)
    assert m["summary"]["participants"] == 2 and m["summary"]["participants_labeled"] == 1
    first = next(u for u in m["participants"] if u["id"] == pid)
    assert first["label"] == "Магазин 4471, ДМ" and first["appeals"] == 1 and first["sessions"] == 1
    assert len(pid) == 10 and "test-user" not in pid
    with engine.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "test-user" not in dump and "other" not in dump


def test_v2_database_migrates_to_v3(settings, tmp_path):
    import sqlite3

    from app.content import Catalog
    from app.engine import Engine

    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE sessions (sid TEXT PRIMARY KEY, last_seen REAL NOT NULL);"
        "CREATE TABLE events (id INTEGER PRIMARY KEY, time REAL NOT NULL, kind TEXT NOT NULL, topic TEXT, version TEXT,"
        " value TEXT, delivery TEXT, delivered INTEGER NOT NULL DEFAULT 1, interaction TEXT);"
        "INSERT INTO events(time,kind) VALUES (1, 'session_start'); PRAGMA user_version=2;"
    )
    conn.close()
    engine = Engine(Catalog(settings.content_dir), db_path, settings.state_secret)
    with engine.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT participant FROM events").fetchone()[0] is None
