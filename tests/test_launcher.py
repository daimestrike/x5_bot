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


updater = module("rooms_updater", "scripts/update.py")


def build_archive(tmp_path, root_name="rooms-bot-9.9.9", version="9.9.9", extra_content=None):
    """Собирает архив «новой версии» так же, как scripts/package.py."""
    import tarfile

    staging = tmp_path / "staging" / root_name
    (staging / "app").mkdir(parents=True)
    (staging / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (staging / "app" / "new_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (staging / "content").mkdir()
    (staging / "content" / "cards.yaml").write_text("из архива\n", encoding="utf-8")
    for name, text in (extra_content or {}).items():
        (staging / "content" / name).write_text(text, encoding="utf-8")
    (staging / "run.py").write_text("print('new')\n", encoding="utf-8")
    archive = tmp_path / (root_name + ".tar.gz")
    with tarfile.open(archive, "w:gz") as handle:
        handle.add(staging, arcname=root_name)
    return archive


def install(tmp_path):
    """Имитирует установленный бот с данными конкретной установки."""
    root = tmp_path / "install"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text('__version__ = "1.0.0"\n', encoding="utf-8")
    (root / "app" / "stale.py").write_text("OLD = True\n", encoding="utf-8")
    (root / "content" / "sources").mkdir(parents=True)
    (root / "content" / "cards.yaml").write_text("правка владельца\n", encoding="utf-8")
    (root / "content" / "sources" / "instruction.json").write_text("{}", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "bot.sqlite3").write_text("журнал", encoding="utf-8")
    (root / ".env").write_text("BOT_API_TOKEN=secret\n", encoding="utf-8")
    (root / "run.py").write_text("print('old')\n", encoding="utf-8")
    return root


def run_update(monkeypatch, root, archive, *args):
    monkeypatch.setattr(updater, "ROOT", root)
    updater.main([str(archive), "--skip-deps", *args])


def test_update_replaces_code_and_keeps_installation_data(tmp_path, monkeypatch):
    root = install(tmp_path)
    archive = build_archive(tmp_path, extra_content={"NEW.yaml": "новый файл\n"})
    run_update(monkeypatch, root, archive)

    assert '9.9.9' in (root / "app" / "__init__.py").read_text(encoding="utf-8")
    assert (root / "app" / "new_module.py").exists()
    assert not (root / "app" / "stale.py").exists()  # удалённые файлы кода не остаются
    assert (root / "run.py").read_text(encoding="utf-8") == "print('new')\n"
    # данные установки нетронуты
    assert (root / ".env").read_text(encoding="utf-8") == "BOT_API_TOKEN=secret\n"
    assert (root / "data" / "bot.sqlite3").read_text(encoding="utf-8") == "журнал"
    assert (root / "content" / "cards.yaml").read_text(encoding="utf-8") == "правка владельца\n"
    assert (root / "content" / "sources" / "instruction.json").exists()
    # новые файлы содержания добавляются
    assert (root / "content" / "NEW.yaml").read_text(encoding="utf-8") == "новый файл\n"
    assert list((root / "backups").glob("pre-update-*"))


def test_update_rejects_wrong_checksum(tmp_path, monkeypatch):
    root = install(tmp_path)
    archive = build_archive(tmp_path)
    archive.with_name(archive.name + ".sha256").write_text("0" * 64 + "  archive\n", encoding="utf-8")
    monkeypatch.setattr(updater, "ROOT", root)
    with pytest.raises(SystemExit, match="Контрольная сумма"):
        updater.main([str(archive), "--skip-deps"])
    assert '1.0.0' in (root / "app" / "__init__.py").read_text(encoding="utf-8")  # ничего не заменено


def test_update_rolls_back_when_check_fails(tmp_path, monkeypatch):
    root = install(tmp_path)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    archive = build_archive(tmp_path)
    monkeypatch.setattr(updater, "ROOT", root)
    monkeypatch.setattr(updater.subprocess, "run",
                        lambda *a, **k: updater.subprocess.CompletedProcess(a, 1, "", "конфигурация битая"))
    with pytest.raises(SystemExit, match="Обновление отменено"):
        updater.main([str(archive), "--skip-deps"])
    assert '1.0.0' in (root / "app" / "__init__.py").read_text(encoding="utf-8")
    assert (root / "app" / "stale.py").exists()
    assert (root / ".env").read_text(encoding="utf-8") == "BOT_API_TOKEN=secret\n"
    assert not (root / "content" / "NEW.yaml").exists()  # содержание не доливалось


def test_update_requires_installed_bot(tmp_path, monkeypatch):
    empty = tmp_path / "empty"
    empty.mkdir()
    archive = build_archive(tmp_path)
    monkeypatch.setattr(updater, "ROOT", empty)
    with pytest.raises(SystemExit, match="каталога установленного бота"):
        updater.main([str(archive), "--skip-deps"])
