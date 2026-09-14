"""HTTP: webhook, демо-консоль, метрики и выгрузки, health."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    s = Settings(db_path=tmp_path / "j.sqlite3", rooms_webhook_secret="s3cret", journal_salt="t")
    app = create_app(s)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["cards"] == 35 and r.json()["search_enabled"] is False


def test_webhook_requires_secret(client):
    assert client.post("/webhook/rooms", json={"user_id": "1", "text": "меню"}).status_code == 401


def test_webhook_flow(client):
    h = {"X-Rooms-Token": "s3cret"}
    r = client.post("/webhook/rooms", json={"message": {"from": {"id": 7}, "chat": {"id": 70}, "text": "/start"}}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["screen"] == "S1" and body["messages"][0]["chat_id"] == "70"
    assert body["messages"][0]["text"].startswith("Я справочник") and "buttons" in body["messages"][1]
    cb = {"callback_query": {"from": {"id": 7}, "chat_id": 70, "callback_data": "card:B-05"}}
    r = client.post("/webhook/rooms", json=cb, headers=h)
    assert r.json()["screen"] == "S3"
    assert r.json()["messages"][0]["buttons"][0][0]["callback_data"] == "ok:B-05"
    assert client.post("/webhook/rooms", json={"event": "typing"}, headers=h).json().get("ignored") is True


def test_console_metrics_and_exports(client):
    r = client.post("/api/console", json={"user_id": "d", "callback": "card:A-07"})
    assert r.status_code == 200 and r.json()["screen"] == "S3"
    client.post("/api/console", json={"user_id": "d", "callback": "ok:A-07"})
    client.post("/api/console", json={"user_id": "d", "text": "почему не работает"})
    m = client.get("/metrics/summary").json()
    assert m["sessions"] == 1 and m["answers_issued"] == 1 and m["usefulness_pct"] == 100.0
    assert m["gaps"]["questions_without_answer"][0]["text"] == "почему не работает"
    assert "card_view" in client.get("/metrics/journal.csv").text
    appeals = client.get("/metrics/appeals.csv").text.splitlines()
    assert appeals[0].startswith("user_ref;session_id") and appeals[1].startswith("d;") and ";helped;" in appeals[1]
    assert client.get("/").status_code == 200
    cards = client.get("/content/cards").json()
    assert len(cards["types"]) == 3 and cards["versions"]["A-07"] == [{"version": "1.0", "status": "published"}]


def test_text_buttons_mode(tmp_path):
    s = Settings(db_path=tmp_path / "j.sqlite3", rooms_buttons_mode="text", journal_salt="t")
    with TestClient(create_app(s)) as c:
        r = c.post("/webhook/rooms", json={"user_id": "9", "text": "меню"})
        txt = r.json()["messages"][-1]["text"]
        assert "1. Типовые вопросы" in txt and "buttons" not in r.json()["messages"][-1]
        assert c.post("/webhook/rooms", json={"user_id": "9", "text": "2"}).json()["screen"] == "S2"
