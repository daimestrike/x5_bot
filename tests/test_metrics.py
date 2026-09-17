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
    engine.handle(event(action="help:tech"))
    engine.handle(event(action="card:B-02"))  # повторный показ в том же сеансе — то же обращение
    engine.handle(event("message", text="не грузит"))
    engine.handle(event("message", text="а что делать если модем не ловит сеть"))

    m = product_metrics(engine, days=7)
    s = m["summary"]
    assert s["sessions"] == 1 and s["appeals"] == 2 and s["ratings"] == 2
    assert s["helped"] == 1 and s["not_helped"] == 1
    assert s["feedback_coverage"] == 1.0 and s["coverage_ok"] is True
    assert s["helpfulness"] == 0.5 and s["helpfulness_ok"] is False
    assert s["help_requests"] == 1 and s["search_miss"] == 1 and s["unknown_input"] == 1
    assert m["gaps"][0]["id"] == "B-02" and m["gaps"][0]["reasons"] == {"Не хватает деталей": 1}
    assert next(x for x in m["sections"] if x["section"] == "B")["views"] == 1
    assert [c["id"] for c in m["top_cards"]] == ["A-07", "B-02"]
    assert next(x for x in m["help_by_type"] if x["type"] == "tech")["count"] == 1
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
