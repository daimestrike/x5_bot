import json
import time
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import jwt
import pytest
import yaml
from fastapi.testclient import TestClient

from app.main import create_app
from app.transports.rooms import DeliveryError


def header(settings):
    return {"Authorization": "Bearer " + settings.api_token}


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_demo_requires_token(client, settings, event):
    assert client.get("/dev/chat").status_code == 200
    e = event(action="menu")
    assert client.post("/api/console", json=e).status_code == 401
    assert client.post("/api/console", json=e, headers=header(settings)).status_code == 200
    assert client.get("/metrics/summary").status_code == 401
    assert client.get("/metrics/summary", headers=header(settings)).status_code == 401
    assert client.get("/metrics/summary", headers={"Authorization": "Bearer " + settings.metrics_token}).status_code == 200
    assert client.post("/api/console/reset", json={"user_id": "x"}).status_code == 404


def test_demo_never_sends_to_rooms(client, settings, event):
    assert client.post("/command", json=event(action="menu"), headers=header(settings)).status_code == 503


@pytest.mark.parametrize("payload", [[], {}, {"type": "message"}, {"x": "y"}, None])
def test_bad_events(client, settings, payload):
    assert (
        client.post(
            "/api/console", content=json.dumps(payload), headers={**header(settings), "Content-Type": "application/json"}
        ).status_code
        == 400
    )


def test_size_limit_and_security_headers(client, settings):
    assert client.post("/api/console", content="x" * 17000, headers=header(settings)).status_code == 413
    assert client.get("/dev/chat").headers["x-content-type-options"] == "nosniff"
    assert "script-src 'self'" in client.get("/dev/chat").headers["content-security-policy"]
    assert client.get("/openapi.json").status_code == 404


def test_event_conflict(client, settings, event):
    e = event(action="menu")
    assert client.post("/api/console", json=e, headers=header(settings)).status_code == 200
    e["action"] = "help"
    assert client.post("/api/console", json=e, headers=header(settings)).status_code == 409


def production_settings(settings, tmp_path):
    target = tmp_path / "approved"
    target.mkdir()
    cards = yaml.safe_load((settings.content_dir / "cards.yaml").read_text())
    for card in cards:
        card.update(approved=True, status="published", owner="Test owner", url="https://docs.invalid/source")
    content = yaml.safe_load((settings.content_dir / "settings.yaml").read_text())
    content.update(allowed_link_hosts=["docs.invalid"], fallback_url="https://docs.invalid/source", channel_approved=True)
    for route in content["help_routes"].values():
        route.update(text="Test support route", owner="Test owner")
    (target / "cards.yaml").write_text(yaml.safe_dump(cards, allow_unicode=True))
    (target / "settings.yaml").write_text(yaml.safe_dump(content, allow_unicode=True))
    return replace(
        settings,
        mode="production",
        content_dir=target,
        rooms_contract_confirmed=True,
        rooms_api_base_url="https://rooms.invalid",
        rooms_bot_id="11111111-1111-4111-8111-111111111111",
        rooms_issuer="rooms.invalid",
        rooms_secret_key="k" * 40,
        enable_dev_console=False,
    )


def signed(settings, **overrides):
    now = int(time.time())
    claims = dict(iss=settings.rooms_issuer, aud=[settings.rooms_bot_id], iat=now, nbf=now, exp=now + 60, jti=str(uuid4()))
    claims.update(overrides)
    return {"Authorization": "Bearer " + jwt.encode(claims, settings.rooms_secret_key, algorithm="HS256")}


def update(settings, text="/menu", data=None):
    return {
        "sync_id": str(uuid4()),
        "proto_version": 4,
        "bot_id": settings.rooms_bot_id,
        "command": {"body": text, "data": data or {}, "command_type": "user"},
        "from": {
            "user_huid": "22222222-2222-4222-8222-222222222222",
            "group_chat_id": "33333333-3333-4333-8333-333333333333",
            "host": "rooms.invalid",
            "chat_type": "chat",
        },
    }


def test_production_delivery_failure_retry_and_duplicate(settings, tmp_path):
    settings = production_settings(settings, tmp_path)
    app = create_app(settings)
    app.state.delivery.run = AsyncMock()
    app.state.rooms.send = AsyncMock(side_effect=[None, DeliveryError(), None])
    e = update(settings, "/action", {"action": "card:A-07"})
    with TestClient(app) as c:
        assert c.get("/").status_code == 404
        assert c.get("/dev/chat").status_code == 404
        assert c.post("/api/console", json=e, headers=header(settings)).status_code == 404
        assert c.post("/command", json=e, headers=signed(settings)).status_code == 202
        app.state.rooms.send.assert_not_called()  # ACK does not wait for outgoing requests.
        c.portal.call(app.state.delivery.tick)
        assert app.state.engine.metrics()["card_views"] == 0
        assert app.state.delivery.stats()["pending"] == 1
    # Restart resumes the card, preserving the successful welcome delivery.
    restarted = create_app(settings)
    restarted.state.delivery.run = AsyncMock()
    restarted.state.rooms.send = AsyncMock()
    with restarted.state.engine.connect() as db:
        db.execute("UPDATE outbox SET next_at=0")
    with TestClient(restarted) as c:
        c.portal.call(restarted.state.delivery.tick)
        assert restarted.state.rooms.send.await_count == 1
        assert restarted.state.engine.metrics()["card_views"] == 1
        assert restarted.state.delivery.stats()["pending"] == 0
        assert c.post("/command", json=e, headers=signed(settings)).status_code == 202
        c.portal.call(restarted.state.delivery.tick)
        assert restarted.state.rooms.send.await_count == 1


def test_native_jwt_status_privacy(settings, tmp_path):
    settings = production_settings(settings, tmp_path)
    app = create_app(settings)
    app.state.delivery.run = AsyncMock()
    with TestClient(app) as c:
        e = update(settings, "secret-inventory-text")
        e["from"]["username"] = "Private employee"
        assert c.post("/command", json=e, headers=header(settings)).status_code == 401
        for changes in [
            {"exp": 1},
            {"iss": "other.invalid"},
            {"aud": [str(uuid4())]},
            {"nbf": int(time.time()) + 600},
            {"exp": int(time.time()) + 600},
        ]:
            assert c.post("/command", json=e, headers=signed(settings, **changes)).status_code == 401
        assert c.post("/command", json=e, headers=signed(settings)).status_code == 202
        r = c.get("/status", params={"bot_id": settings.rooms_bot_id}, headers=signed(settings))
        assert [x["body"] for x in r.json()["result"]["commands"]] == ["/menu", "/search", "/help"]
        with app.state.engine.connect() as db:
            dump = "\n".join(db.iterdump())
        for private in [e["from"]["user_huid"], e["from"]["group_chat_id"], "Private employee", "secret-inventory-text"]:
            assert private not in dump


def test_console_opt_in(settings):
    with TestClient(create_app(replace(settings, enable_dev_console=False))) as c:
        assert c.get("/dev/chat").status_code == 404
        assert c.post("/api/console", json={}).status_code == 404


def test_production_unverified_blocked(settings):
    with pytest.raises(ValueError, match="contract"):
        create_app(replace(settings, mode="production"))


def test_bad_secrets_blocked(settings):
    with pytest.raises(ValueError, match="secrets"):
        create_app(replace(settings, api_token=""))


def test_rate_limit(settings, event):
    with TestClient(create_app(replace(settings, requests_per_minute=1))) as c:
        assert c.post("/api/console", json=event(action="menu"), headers=header(settings)).status_code == 200
        assert c.post("/api/console", json=event(action="menu"), headers=header(settings)).status_code == 429


def test_no_secret_file_served(client):
    for path in ("/.env", "/data/bot-v2.sqlite3", "/content/cards.yaml", "/content/cards"):
        assert client.get(path).status_code == 404


def test_production_rejects_public_demo_secrets(settings, tmp_path):
    from test_api import production_settings

    prod = production_settings(settings, tmp_path)
    with pytest.raises(ValueError, match="demo"):
        replace(prod, api_token="demo-api-token-for-local-console-only-00000").validate()
    replace(settings, api_token="demo-api-token-for-local-console-only-00000").validate()  # demo mode is fine
