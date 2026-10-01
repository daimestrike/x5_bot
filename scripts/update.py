"""Обновление установленного бота из архива новой версии.

    cd /opt/rooms-bot
    ./update.sh ~/rooms-bot-1.2.0.tar.gz

Что делает: проверяет контрольную сумму, останавливает службу, делает резервную копию,
заменяет код, доустанавливает зависимости (из wheels/ или внутреннего PyPI), проверяет
конфигурацию и запускает службу обратно. При ошибке откатывает код из резервной копии.

Чего НЕ трогает — это данные установки, а не код:
    .env                      секреты и адреса
    data/                     журнал обращений и кэш эмбеддингов
    content/                  карточки, настройки, слепки документов, очередь проверки
Новые файлы содержания из архива добавляются, существующие остаются как есть; скрипт
покажет, в каких файлах содержание в архиве отличается от установленного.
"""

import argparse
import datetime as dt
import filecmp
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Код: полностью заменяется содержимым архива
CODE_DIRS = ("app", "scripts", "tests", "docs")
CODE_FILES = (
    "run.py", "install.py", "setup.sh", "start.sh", "update.sh", "setup.cmd", "start.cmd",
    "requirements.txt", "requirements-dev.txt", "pytest.ini", "ruff.toml", "Makefile",
    "Dockerfile", "docker-compose.yml", ".dockerignore", ".gitlab-ci.yml",
    "README.md", "QUICKSTART.md", ".env.example",
)
# Данные установки: сохраняются при обновлении
KEEP = (".env", "data", "content", ".venv", "wheels", "backups", "dist")


def say(message):
    print(message, flush=True)


def check_sha256(archive):
    expected_file = archive.with_name(archive.name + ".sha256")
    if not expected_file.exists():
        say("  контрольной суммы рядом нет — пропускаю проверку")
        return
    expected = expected_file.read_text(encoding="utf-8").split()[0]
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"Контрольная сумма не совпала.\n  ожидалось {expected}\n  получено  {actual}")
    say("  контрольная сумма совпала")


def unpack(archive, target):
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(target)
    else:
        with tarfile.open(archive) as handle:
            handle.extractall(target)
    entries = [p for p in target.iterdir() if p.is_dir()]
    if len(entries) == 1 and (entries[0] / "app").is_dir():
        return entries[0]
    if (target / "app").is_dir():
        return target
    raise SystemExit("В архиве не найдена папка проекта с каталогом app/")


def service_action(service, action):
    if not service:
        return False
    result = subprocess.run(["systemctl", action, service], capture_output=True, text=True)
    if result.returncode != 0:
        say(f"  systemctl {action} {service}: {result.stderr.strip() or 'не удалось'}")
        return False
    say(f"  служба {service}: {action}")
    return True


def backup(destination):
    destination.mkdir(parents=True, exist_ok=True)
    for name in (".env", "data", "content"):
        source = ROOT / name
        if not source.exists():
            continue
        target = destination / name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    for name in CODE_DIRS:
        source = ROOT / name
        if source.exists():
            shutil.copytree(source, destination / "code" / name, dirs_exist_ok=True)
    for name in CODE_FILES:
        source = ROOT / name
        if source.exists():
            (destination / "code").mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination / "code" / name)


def restore_code(backup_dir):
    code = backup_dir / "code"
    if not code.exists():
        return
    for name in CODE_DIRS:
        if (code / name).exists():
            shutil.rmtree(ROOT / name, ignore_errors=True)
            shutil.copytree(code / name, ROOT / name)
    for name in CODE_FILES:
        if (code / name).exists():
            shutil.copy2(code / name, ROOT / name)


def replace_code(new_root):
    for name in CODE_DIRS:
        source = new_root / name
        if not source.exists():
            continue
        shutil.rmtree(ROOT / name, ignore_errors=True)
        shutil.copytree(source, ROOT / name)
    for name in CODE_FILES:
        source = new_root / name
        if source.exists():
            shutil.copy2(source, ROOT / name)
            if source.suffix == ".sh":
                os.chmod(ROOT / name, 0o755)


def merge_content(new_root):
    """Новые файлы содержания добавляет, существующие не трогает; расхождения показывает."""
    source = new_root / "content"
    if not source.exists():
        return
    added, differs = [], []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        target = ROOT / "content" / relative
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            added.append(str(relative))
        elif not filecmp.cmp(path, target, shallow=False):
            differs.append(str(relative))
    if added:
        say("  добавлены файлы содержания: " + ", ".join(added))
    if differs:
        say("  содержание установки отличается от архива (оставлено ваше): " + ", ".join(differs))


def install_dependencies(python, index_url, offline):
    command = [str(python), str(ROOT / "install.py")]
    if offline:
        command.append("--offline")
    elif index_url:
        command += ["--index-url", index_url]
    else:
        say("  зависимости не переустанавливаю: не задан ни --offline, ни --index-url")
        return
    subprocess.run(command, cwd=ROOT, check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Обновить установленный Rooms-бот из архива")
    parser.add_argument("archive", help="путь к rooms-bot-<версия>.tar.gz или .zip")
    parser.add_argument("--service", default=os.getenv("ROOMS_SERVICE", ""),
                        help="имя systemd-службы для остановки и запуска (например rooms-bot)")
    parser.add_argument("--index-url", help="внутренний PyPI для доустановки зависимостей")
    parser.add_argument("--offline", action="store_true", help="ставить зависимости только из wheels/")
    parser.add_argument("--skip-deps", action="store_true", help="не трогать зависимости")
    parser.add_argument("--keep-backups", type=int, default=5, help="сколько резервных копий хранить")
    args = parser.parse_args(argv)

    archive = Path(args.archive).expanduser().resolve()
    if not archive.exists():
        raise SystemExit("Архив не найден: " + str(archive))
    if not (ROOT / "app").is_dir():
        raise SystemExit("Запускайте из каталога установленного бота: " + str(ROOT))

    say(f"Обновление установки {ROOT}")
    say(f"Архив: {archive.name}")
    check_sha256(archive)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = ROOT / "backups" / ("pre-update-" + stamp)
    say(f"Резервная копия: {backup_dir.relative_to(ROOT)}")
    backup(backup_dir)

    was_running = service_action(args.service, "stop")

    with tempfile.TemporaryDirectory(dir=str(ROOT)) as temporary:
        new_root = unpack(archive, Path(temporary))
        say("Заменяю код")
        try:
            replace_code(new_root)
            if not args.skip_deps:
                install_dependencies(sys.executable, args.index_url, args.offline)
            python = ROOT / ".venv/bin/python"
            if python.exists():
                check = subprocess.run([str(python), str(ROOT / "run.py"), "--check"],
                                       cwd=ROOT, capture_output=True, text=True)
                if check.returncode != 0:
                    raise RuntimeError((check.stderr or check.stdout).strip())
                say("  " + check.stdout.strip())
            else:
                # установка не на .venv (например, Docker) — проверить нечем, но код уже заменён
                say("  .venv не найден: проверку конфигурации пропускаю")
            # Содержание доливаем только после успешной проверки, чтобы откат был полным.
            merge_content(new_root)
        except (subprocess.CalledProcessError, RuntimeError, OSError) as error:
            say("ОШИБКА: " + str(error))
            say("Откатываю код из резервной копии")
            restore_code(backup_dir)
            if was_running:
                service_action(args.service, "start")
            raise SystemExit("Обновление отменено. Данные и .env не пострадали.") from None

    if args.service:
        service_action(args.service, "start")
    else:
        say("Служба не задана — запустите вручную: ./start.sh (или systemctl start <служба>)")

    copies = sorted((ROOT / "backups").glob("pre-update-*"))
    for old in copies[:-args.keep_backups] if args.keep_backups > 0 else []:
        shutil.rmtree(old, ignore_errors=True)

    say("Готово.")


if __name__ == "__main__":
    main()
