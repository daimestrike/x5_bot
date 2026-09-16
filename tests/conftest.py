import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from app.config import Settings  # noqa: E402
from app.content import Catalog  # noqa: E402
from app.engine import Engine  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings(tmp_path):
    return Settings(
        enable_dev_console=True,
        content_dir=ROOT / "content",
        db_path=tmp_path / "bot.sqlite3",
        state_secret="s" * 40,
        api_token="a" * 40,
        metrics_token="m" * 40,
    )


@pytest.fixture
def engine(settings):
    return Engine(Catalog(settings.content_dir), settings.db_path, settings.state_secret)


@pytest.fixture
def event():
    import itertools

    ids = itertools.count()

    def make(kind="action", **fields):
        return dict(event_id=str(next(ids)), user_id="test-user", conversation_id="test-chat", type=kind, **fields)

    return make
