"""Источники и очередь верификации: слепки docx/txt, дифф, сопоставление с карточками, правка и утверждение."""
import io
import shutil
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.review import ContentStore, ReviewError, bump
from app.sources import SourceError, diff_units, extract, match_cards, word_diff

MODEM_OLD = "Включите модем долгим нажатием кнопки питания. На экране появятся имя сети и пароль."
MODEM_NEW = "Включите модем долгим нажатием кнопки питания и дождитесь зелёного индикатора. На экране появятся имя сети и пароль."


def docx(paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
        z.writestr("[Content_Types].xml", "<Types/>")
    return buf.getvalue()


@pytest.fixture
def content_dir(settings, tmp_path):
    d = tmp_path / "content"
    shutil.copytree(settings.content_dir, d)
    shutil.rmtree(d / "sources", ignore_errors=True)
    shutil.rmtree(d / "review", ignore_errors=True)
    return d


def test_extract_docx_and_txt():
    units = extract("doc.docx", docx(["Первый абзац документа.", "", "Второй абзац документа."]))
    assert [u["text"] for u in units] == ["Первый абзац документа.", "Второй абзац документа."]
    units = extract("doc.txt", "Абзац один\nпродолжение\n\nАбзац два".encode())
    assert [u["text"] for u in units] == ["Абзац один продолжение", "Абзац два"]
    with pytest.raises(SourceError):
        extract("doc.xlsx", b"x")
    with pytest.raises(SourceError):
        extract("doc.docx", b"not a zip")


def test_diff_and_matching(engine):
    old = extract("a.txt", ("Вступление.\n\n" + MODEM_OLD + "\n\nУдалённый абзац про зоны и зонирование склада.").encode())
    new_text = "Вступление.\n\n" + MODEM_NEW + "\n\nНовый абзац про наушники и гарнитуру в порт зарядки ТСД."
    new = extract("a.txt", new_text.encode())
    changes = diff_units(old, new)
    kinds = sorted(c["kind"] for c in changes)
    assert kinds == ["added", "changed", "removed"]
    changed = next(c for c in changes if c["kind"] == "changed")
    assert ("insert", " и дождитесь зелёного индикатора") in [(op, t) for op, t in changed["diff"]]
    ids = [m["id"] for m in match_cards(changed, engine.catalog.cards, "instruction")]
    assert "A-07" in ids
    added = next(c for c in changes if c["kind"] == "added")
    assert "B-04" in [m["id"] for m in match_cards(added, engine.catalog.cards, "instruction")]
    assert word_diff("a b c.", "a x c.") == [("equal", "a"), ("delete", " b"), ("insert", " x"), ("equal", " c.")]
    kept = "".join(t for op, t in word_diff("Нажмите (кнопку).", "Нажмите (кнопку) снова.") if op != "delete")
    assert kept == "Нажмите (кнопку) снова."


def test_bump():
    assert bump("0.1.0") == "0.2.0" and bump("1.2.3") == "1.2.4" and bump("v7") == "v7.1"


def test_update_and_approve_card(content_dir):
    store = ContentStore(content_dir)
    card = store.update_card("A-07", {"steps": ["Снимите крышку.", "Вставьте сим-карту.", "Включите модем."]}, "Иванов")
    assert card["version"] == "0.2.0" and card["approved"] is False and card["history"][0]["version"] == "0.1.0"
    assert store.update_card("A-07", {"steps": card["steps"]}, "Иванов")["version"] == "0.2.0"  # без изменений — версия та же
    with pytest.raises(ReviewError):
        store.update_card("A-07", {"title": "x" * 61}, "Иванов")
    with pytest.raises(ReviewError):
        store.update_card("A-07", {"id": "Z-01"}, "Иванов")
    with pytest.raises(ReviewError):
        store.approve("A-07", "", True)
    card = store.approve("A-07", "Петров", True)
    assert card["approved"] and card["owner"] == "Петров" and card["status"] == "published"
    assert next(c for c in store.read_cards() if c["id"] == "A-07")["version"] == "0.2.0"


def test_upload_compare_queue_and_edit_flow(settings, content_dir):
    s = replace(settings, content_dir=content_dir)
    h = {"Authorization": "Bearer " + s.metrics_token}
    with TestClient(create_app(s)) as c:
        assert c.get("/content/").status_code == 200
        assert c.get("/content/api/state").status_code == 401
        up = lambda data: c.post("/content/api/sources/upload?key=instruction&title=Инструкция&filename=i.docx",  # noqa: E731
                                 content=data, headers={**h, "Content-Type": "application/octet-stream"})
        r = up(docx(["Вступление.", MODEM_OLD]))
        assert r.status_code == 200 and r.json()["first_upload"] is True and r.json()["queued"] == 0
        assert up(docx(["Вступление.", MODEM_OLD])).json()["changes"] == 0
        r = up(docx(["Вступление.", MODEM_NEW]))
        assert r.json()["source"]["version"] == 2 and r.json()["queued"] >= 1
        state = c.get("/content/api/state", headers=h).json()
        item = next(i for i in state["queue"] if i["card_id"] == "A-07")
        assert item["change"] == "changed" and item["unit"] == 2 and any(op == "insert" for op, _ in item["diff"])
        assert state["sources"][0]["version"] == 2

        fields = {"steps": ["Снимите крышку и вставьте сим-карту.", "Включите модем и дождитесь зелёного индикатора."]}
        r = c.post("/content/api/cards/update", json={"id": "A-07", "editor": "Иванов", "fields": fields}, headers=h)
        assert r.status_code == 200 and r.json()["card"]["version"] == "0.2.0" and r.json()["reloaded"] is True
        r = c.post("/content/api/queue/resolve", json={"id": item["id"], "decision": "edited", "who": "Иванов"}, headers=h)
        assert r.json()["status"] == "edited"
        r = c.post("/content/api/cards/approve", json={"id": "A-07", "reviewer": "Петров"}, headers=h)
        assert r.json()["card"]["approved"] is True
        state = c.get("/content/api/state", headers=h).json()
        a07 = next(x for x in state["cards"] if x["id"] == "A-07")
        assert a07["version"] == "0.2.0" and a07["live_version"] == "0.2.0" and a07["owner"] == "Петров"
        assert not any(i["card_id"] == "A-07" for i in state["queue"])
        # бот выдаёт новую версию
        event = {"event_id": "e1", "conversation_id": "c", "user_id": "u", "type": "action", "action": "card:A-07"}
        r = c.post("/api/console", json=event, headers={"Authorization": "Bearer " + s.api_token})
        assert "зелёного индикатора" in r.json()["messages"][-1]["text"]
        assert c.post("/content/api/sources/upload?key=BAD KEY&filename=x.txt", content=b"abc", headers=h).status_code == 400
