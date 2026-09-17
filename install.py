"""Prepare a local installation after extracting the repository archive.

The installer deliberately does not fall back to public PyPI. Dependencies come
from wheels/ or from an explicitly configured corporate package index.
"""

import argparse
import os
import struct
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"


def venv_python():
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def wheelhouse_ready():
    return any((ROOT / "wheels").glob("*.whl"))


def pip_command(python, requirements, index_url=None, offline=False):
    command = [str(python), "-m", "pip", "install"]
    if offline:
        command += ["--no-index", "--find-links", str(ROOT / "wheels")]
    elif index_url:
        command += ["--index-url", index_url]
    else:
        raise ValueError("Укажите внутренний PyPI через --index-url или положите wheel-файлы в wheels/")
    return command + ["-r", str(ROOT / requirements)]


def main(argv=None):
    parser = argparse.ArgumentParser(description="Подготовить Rooms-бот к запуску без Docker")
    parser.add_argument("--index-url", help="URL внутреннего PyPI (также читается из PIP_INDEX_URL)")
    parser.add_argument("--offline", action="store_true", help="устанавливать только из каталога wheels/")
    parser.add_argument("--dev", action="store_true", help="добавить pytest, Ruff и openpyxl")
    parser.add_argument("--secure", action="store_true", help="создать .env со случайными секретами")
    args = parser.parse_args(argv)

    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise SystemExit("Нужен Python 3.12 (64-bit). Текущая версия: " + sys.version.split()[0])

    index_url = args.index_url or os.getenv("PIP_INDEX_URL")
    # Явный внутренний индекс имеет приоритет над случайно оставшимся неполным wheelhouse.
    offline = args.offline or (not index_url and wheelhouse_ready())
    if args.offline and not wheelhouse_ready():
        raise SystemExit("В wheels/ нет .whl файлов. Скопируйте полный wheelhouse для Python 3.12 и этой ОС.")
    try:
        command = pip_command(venv_python(), "requirements-dev.txt" if args.dev else "requirements.txt", index_url, offline)
    except ValueError as error:
        raise SystemExit(str(error) + ". Публичный PyPI автоматически не используется.") from None

    if not venv_python().exists():
        print("Создаю .venv …")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV)
    print("Устанавливаю зависимости " + ("из wheels/ …" if offline else "из внутреннего PyPI …"))
    subprocess.run(command, cwd=ROOT, check=True)

    env_file = ROOT / ".env"
    if not env_file.exists():
        init = [str(venv_python()), str(ROOT / "scripts/init_env.py")]
        if args.secure:
            init.append("--secure")
        subprocess.run(init, cwd=ROOT, check=True)
    else:
        print(".env уже существует — оставляю без изменений.")

    subprocess.run([str(venv_python()), str(ROOT / "scripts/validate_content.py")], cwd=ROOT, check=True)
    print("\nГотово. Запуск: python run.py")
    print("Демо-консоль: http://127.0.0.1:8080/dev/chat")


if __name__ == "__main__":
    main()
