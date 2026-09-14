from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.content import load_content  # noqa: E402
from app.engine import Engine  # noqa: E402
from app.journal import Journal  # noqa: E402
from app.models import Incoming  # noqa: E402
from app.sessions import SessionStore  # noqa: E402


@pytest.fixture(scope="session")
def content():
    return load_content(ROOT / "content", strict=True)


@pytest.fixture
def settings(tmp_path):
    return Settings(content_dir=ROOT / "content", db_path=tmp_path / "j.sqlite3", journal_salt="test")


@pytest.fixture
def journal(settings):
    j = Journal(settings.db_path)
    yield j
    j.close()


@pytest.fixture
def engine(content, journal, settings):
    return Engine(content, journal, SessionStore(3600), settings)


class Bot:
    """Удобная обёртка: bot.text("модем"), bot.click("card:A-07")."""

    def __init__(self, engine: Engine, user_id: str = "u1"):
        self.engine = engine
        self.user_id = user_id

    def text(self, text: str):
        return self.engine.handle(Incoming(user_id=self.user_id, text=text))

    def click(self, data: str):
        return self.engine.handle(Incoming(user_id=self.user_id, callback=data))

    def attach(self, kind: str):
        return self.engine.handle(Incoming(user_id=self.user_id, attachment_type=kind))


@pytest.fixture
def bot(engine):
    b = Bot(engine)
    b.text("/start")  # открываем сеанс, получаем S0+S1
    return b


def labels(reply):
    return [b.label for m in reply.messages for row in m.buttons for b in row]


def datas(reply):
    return [b.data for m in reply.messages for row in m.buttons for b in row]


def last_text(reply):
    return reply.messages[-1].text
