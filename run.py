"""Run the service without Docker from the extracted project directory."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
VENDOR = ROOT / "vendor"


def use_vendor():
    """Зависимости сложены в vendor/ — запускаемся текущим Python без venv и pip."""
    if not VENDOR.is_dir():
        return False
    sys.path.insert(0, str(VENDOR))
    return True


def read_env(path):
    values = {}
    if not path.exists():
        return values
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Некорректная строка {number} в {path.name}")
        name, value = line.split("=", 1)
        name = name.strip()
        if not name or not name.replace("_", "A").isalnum():
            raise ValueError(f"Некорректное имя переменной в строке {number}: {name}")
        values[name] = value.strip().strip('"').strip("'")
    return values


def main(argv=None):
    parser = argparse.ArgumentParser(description="Запустить Rooms-бот без Docker")
    parser.add_argument("--host", help="адрес прослушивания; по умолчанию 127.0.0.1 для demo, 0.0.0.0 для production")
    parser.add_argument("--port", type=int, help="порт; по умолчанию PORT из .env или 8080")
    parser.add_argument("--check", action="store_true", help="проверить конфигурацию и завершить работу")
    args = parser.parse_args(argv)

    if not use_vendor():
        if not VENV_PYTHON.exists():
            raise SystemExit(
                "Зависимости не найдены. Либо в поставке должен быть каталог vendor/, "
                "либо подготовьте окружение: python install.py --index-url https://ВНУТРЕННИЙ-PYPI/simple"
            )
        if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
            result = subprocess.run([str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]], cwd=ROOT)
            raise SystemExit(result.returncode)

    env_file = ROOT / ".env"
    if not env_file.exists():
        raise SystemExit("Нет .env. Выполните ./setup.sh или скопируйте .env.example в .env")
    try:
        for name, value in read_env(env_file).items():
            os.environ.setdefault(name, value)
    except ValueError as error:
        raise SystemExit(str(error)) from None

    os.chdir(ROOT)
    from app.main import create_app

    app = create_app()
    mode = app.state.settings.mode
    host = args.host or ("127.0.0.1" if mode == "demo" else "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise SystemExit("PORT должен быть от 1 до 65535")
    if args.check:
        print(f"Конфигурация корректна: mode={mode}, cards={len(app.state.engine.catalog.cards)}")
        return

    import uvicorn

    print(f"Rooms-бот запущен: http://{host}:{port} (mode={mode})")
    if mode == "demo" and app.state.settings.enable_dev_console:
        print(f"Демо-консоль: http://{host}:{port}/dev/chat")
        print(f"Дашборд: http://{host}:{port}/metrics/dashboard")
    uvicorn.run(app, host=host, port=port, workers=1, limit_concurrency=32, access_log=False)


if __name__ == "__main__":
    main()
