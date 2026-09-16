import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from test_api import production_settings, update

from app.delivery import Delivery, QueueFull
from app.transports.rooms import DeliveryError, RoomsClient, build_payload, parse_update


def test_native_buttons_round_trip(settings, tmp_path, engine, event):
    settings = production_settings(settings, tmp_path)
    result = engine.handle(event(action="card:A-07"))
    message = result["messages"][-1]
    outgoing = build_payload(str(uuid4()), message)
    notice = outgoing["notification"]
    assert len(notice["bubble"][0]) == 2
    assert notice["keyboard"][0][0]["label"] == "Меню"
    for row in notice["bubble"] + notice["keyboard"]:
        for b in row:
            e = parse_update(update(settings, b["command"], b["data"]), settings)
            assert e["action"] == b["data"]["action"]
            assert b["opts"]["silent"] is True
    assert outgoing["opts"]["notification_opts"]["force_dnd"] is False


def test_command_normalization_and_ignored_events(settings, tmp_path):
    settings = production_settings(settings, tmp_path)
    e = update(settings)
    e["command"].update(command_type="system", body="system:cts_login")
    e["from"]["user_huid"] = None
    assert parse_update(e, settings) is None
    e = update(settings)
    e["from"]["chat_type"] = "group_chat"
    assert parse_update(e, settings) is None
    e = update(settings)
    e["async_files"] = [{"file": "https://evil.invalid/file", "caption": "private"}]
    out = parse_update(e, settings)
    assert out["type"] == "attachment"
    assert "private" not in json.dumps(out) and "evil" not in json.dumps(out)
    e["from"]["host"] = "evil.invalid"
    with pytest.raises(ValueError):
        parse_update(e, settings)


def test_botx_token_refresh_and_delivery_contract(settings, tmp_path):
    settings = production_settings(settings, tmp_path)
    calls = []

    def respond(request):
        calls.append(request)
        if request.method == "GET":
            signature = request.url.params["signature"]
            assert len(signature) == 64 and signature == signature.upper()
            return httpx.Response(200, json={"status": "ok", "result": "test-token"})
        assert request.url.path == "/api/v4/botx/notifications/direct/sync"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert json.loads(request.content)["notification"]["body"] == "Меню"
        if len(calls) == 2:
            return httpx.Response(401)
        return httpx.Response(200, json={"status": "ok", "result": {"sync_id": str(uuid4())}})

    async def run():
        client = RoomsClient(settings)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await client.send(str(uuid4()), {"screen": "S1", "text": "Меню", "buttons": []})
        await client.aclose()

    asyncio.run(run())
    assert [c.method for c in calls] == ["GET", "POST", "GET", "POST"]


@pytest.mark.parametrize(
    "status,payload",
    [(202, {"status": "ok"}), (200, {"status": "error"}), (200, {"status": "ok", "result": {"sync_id": "bad"}}), (503, {})],
)
def test_delivery_does_not_count_acceptance_as_success(settings, tmp_path, status, payload):
    settings = production_settings(settings, tmp_path)

    async def run():
        c = RoomsClient(settings)
        await c.client.aclose()
        c.token = "test"
        c.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=payload)))
        with pytest.raises(DeliveryError):
            await c.send(str(uuid4()), {"screen": "S1", "text": "Меню", "buttons": []})
        await c.aclose()

    asyncio.run(run())


def test_queue_atomic_and_expires(engine, event):
    rooms = AsyncMock()
    delivery = Delivery(engine, rooms)
    e = event(action="card:A-07")

    def failed(db, result):
        delivery.enqueue(e["conversation_id"])(db, result)
        raise QueueFull()

    with pytest.raises(QueueFull):
        engine.handle(e, deferred=True, enqueue=failed)
    assert delivery.stats()["pending"] == 0
    assert engine.metrics()["card_views"] == 0
    result = engine.handle(e, deferred=True, enqueue=delivery.enqueue(e["conversation_id"]))
    with engine.connect() as db:
        db.execute("UPDATE outbox SET created=0")
    asyncio.run(delivery.tick())
    rooms.send.assert_not_called()
    assert delivery.stats()["pending"] == 0
    assert engine.delivery_state(result["reply_id"])["completed"] == 1
    assert engine.metrics()["card_views"] == 0


def test_no_unverified_gateway_can_start(settings):
    with pytest.raises(ValueError):
        replace(settings, mode="production", rooms_contract_confirmed=False).validate()


def test_keycloak_gateway_flow(settings, tmp_path):
    """Схема со страницы wiki X5: Keycloak client_credentials -> bot token по signature -> отправка с двумя токенами."""
    settings = replace(
        production_settings(settings, tmp_path),
        rooms_kc_url="https://kc.invalid/realms/x5/protocol/openid-connect/token",
        rooms_kc_client_id="rooms-bot",
        rooms_kc_client_secret="kc-secret-" + "x" * 30,
        rooms_kc_header="X-KC-Token",
        rooms_token_url="https://gw.invalid/rooms/bot/token",
        rooms_send_url="https://gw.invalid/rooms/send_message",
    )
    settings.validate()
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.host == "kc.invalid":
            body = dict(x.split("=") for x in request.content.decode().split("&"))
            assert body["grant_type"] == "client_credentials" and body["client_id"] == "rooms-bot"
            return httpx.Response(200, json={"access_token": "kc-token", "expires_in": 300})
        if request.method == "GET":
            assert str(request.url).startswith("https://gw.invalid/rooms/bot/token?signature=")
            assert request.headers["X-KC-Token"] == "kc-token" and "Authorization" not in request.headers
            return httpx.Response(200, json={"status": "ok", "result": "bot-token"})
        assert str(request.url) == "https://gw.invalid/rooms/send_message"
        assert request.headers["X-KC-Token"] == "kc-token"
        assert request.headers["Authorization"] == "Bearer bot-token"
        payload = json.loads(request.content)
        assert payload["notification"]["status"] == "ok" and payload["notification"]["body"] == "Меню"
        return httpx.Response(200, json={"status": "ok", "result": {"sync_id": str(uuid4())}})

    async def run():
        client = RoomsClient(settings)
        await client.client.aclose()
        client.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        await client.send(str(uuid4()), {"screen": "S1", "text": "Меню", "buttons": []})
        await client.aclose()

    asyncio.run(run())
    assert [(c.method, c.url.host) for c in calls] == [("POST", "kc.invalid"), ("GET", "gw.invalid"), ("POST", "gw.invalid")]


def test_keycloak_requires_client_credentials(settings, tmp_path):
    settings = production_settings(settings, tmp_path)
    with pytest.raises(ValueError, match="ROOMS_KC_CLIENT"):
        replace(settings, rooms_kc_url="https://kc.invalid/token").validate()
    with pytest.raises(ValueError, match="HTTPS"):
        replace(settings, rooms_send_url="http://gw.invalid/send").validate()
