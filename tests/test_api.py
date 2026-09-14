"""HTTP: webhook, демо-консоль, метрики, health."""
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
    assert r.status_code == 200 and r.json()["cards"] == 35


def test_webhook_requires_secret(client):
    assert client.post("/webhook/rooms", json={"user_id": "1", "text": "модем"}).status_code == 401


def test_webhook_flow(client):
    h = {"X-Rooms-Token": "s3cret"}
    r = client.post("/webhook/rooms", json={"message": {"from": {"id": 7}, "chat": {"id": 70}, "text": "/start"}}, headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["screen"] == "S1" and body["messages"][0]["chat_id"] == "70"
    assert body["messages"][0]["text"].startswith("Я справочник")
    cb = {"callback_query": {"from": {"id": 7}, "chat_id": 70, "callback_data": "card:B-05"}}
    r = client.post("/webhook/rooms", json=cb, headers=h)
    assert r.json()["screen"] == "S3"
    assert r.json()["messages"][0]["buttons"][0][0]["callback_data"] == "ok:B-05"
    r = client.post("/webhook/rooms", json={"event": "typing"}, headers=h)
    assert r.json().get("ignored") is True


def test_console_and_metrics(client):
    r = client.post("/api/console", json={"user_id": "d", "text": "модем"})
    assert r.status_code == 200 and r.json()["screen"] == "S5a"
    first = r.json()["messages"][-1]["buttons"][0][0]["data"]
    client.post("/api/console", json={"user_id": "d", "callback": first})
    client.post("/api/console", json={"user_id": "d", "callback": "ok:" + first.split(":")[1]})
    m = client.get("/metrics/summary").json()
    assert m["sessions"] == 1 and m["appeals"] == 1 and m["usefulness"] == 100.0 and m["rating_coverage"] == 100.0
    csv = client.get("/metrics/journal.csv").text
    assert "card_view" in csv and "rating" in csv
    assert client.get("/").status_code == 200
    assert len(client.get("/content/cards").json()["sections"]) == 5


def test_text_buttons_mode(tmp_path):
    s = Settings(db_path=tmp_path / "j.sqlite3", rooms_buttons_mode="text", journal_salt="t")
    with TestClient(create_app(s)) as c:
        r = c.post("/webhook/rooms", json={"user_id": "9", "text": "меню"})
        txt = r.json()["messages"][-1]["text"]
        assert "1. Подготовка до ПИ" in txt and "buttons" not in r.json()["messages"][-1]
        r = c.post("/webhook/rooms", json={"user_id": "9", "text": "2"})
        assert r.json()["screen"] == "S2"
