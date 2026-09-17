import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


installer = module("rooms_installer", "install.py")
runner = module("rooms_runner", "run.py")


def test_offline_install_command():
    cmd = installer.pip_command(Path("python"), "requirements.txt", offline=True)
    assert "--no-index" in cmd and "--find-links" in cmd and cmd[-1].endswith("requirements.txt")


def test_internal_index_install_command():
    cmd = installer.pip_command(Path("python"), "requirements.txt", index_url="https://art.example/pypi/simple")
    assert cmd[cmd.index("--index-url") + 1] == "https://art.example/pypi/simple"
    assert "--no-index" not in cmd


def test_no_public_index_fallback():
    with pytest.raises(ValueError, match="внутренний PyPI"):
        installer.pip_command(Path("python"), "requirements.txt")


def test_read_env(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nPORT=8081\nNAME='rooms bot'\nEMPTY=\n", encoding="utf-8")
    assert runner.read_env(path) == {"PORT": "8081", "NAME": "rooms bot", "EMPTY": ""}


def test_read_env_rejects_bad_line(tmp_path):
    path = tmp_path / ".env"
    path.write_text("BROKEN\n", encoding="utf-8")
    with pytest.raises(ValueError, match="строка 1"):
        runner.read_env(path)
