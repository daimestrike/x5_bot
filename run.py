"""Run the service without Docker from the extracted project directory."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")


def use_vendor():
    """Зависимости из vendor/ подходят этому Python — запускаемся без venv и pip."""
    sys.path.insert(0, str(ROOT))
    from app import _bootstrap  # импорт пакета app сам подключает vendor/

    return _bootstrap.status


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


def check_ai(app):
    """Запрос к модели с сервера: видно сразу, верны ли адрес, имя модели и ключ."""
    settings = app.state.settings
    assistant = app.state.engine.assistant
    if not settings.ai_enabled:
        print("ИИ выключен: в .env стоит AI_ENABLED=false. Включите и задайте AI_URL, AI_MODEL, AI_API_KEY.")
        return 1
    from app.llm import LLMError

    print(f"Модель: {settings.ai_model}  адрес: {settings.ai_url}  ключ: {'задан' if settings.ai_api_key else 'не задан'}")
    try:
        result = assistant.llm.check()
    except LLMError as error:
        hint = {
            "ai_auth": "ключ не подошёл — проверьте AI_API_KEY",
            "ai_not_found": "адрес или имя модели неверны — AI_URL должен оканчиваться на /v1, AI_MODEL — как в списке моделей",
            "ai_unreachable": "сервер модели недоступен — адрес, сеть, прокси (AI_USE_SYSTEM_PROXY), сертификат (AI_CA_FILE)",
            "ai_rate_limited": "модель перегружена или исчерпан лимит — повторите позже",
            "ai_truncated": "модель ушла в рассуждения: AI_DISABLE_THINKING=true (по умолчанию) или больше AI_MAX_TOKENS",
        }.get(error.code, "см. текст ошибки")
        print(f"ОШИБКА: {error}\nЧто делать: {hint}")
        return 1
    models = result.get("models") or []
    if models:
        print("Модели на сервере: " + ", ".join(models[:10]))
        if settings.ai_model not in models:
            print(f"ВНИМАНИЕ: {settings.ai_model} нет в этом списке — проверьте AI_MODEL")
    print("Тестовый ответ модели: " + (result.get("answer") or "—"))
    seconds = result.get("seconds")
    if seconds is not None:
        print(f"Время ответа: {seconds} с" + ("  — долго: Аватар ждёт ответа в чате" if seconds > 15 else ""))
    if result.get("thinking"):
        print("Модель рассуждала (<think>) — это медленно. Для Qwen3 оставьте AI_DISABLE_THINKING=true; "
              "если шлюз параметр не понял, задайте AI_EXTRA_JSON по документации шлюза.")
    elif result.get("thinking_off_sent"):
        print("Рассуждения отключены (enable_thinking=false) — шлюз параметр принял.")
    chunks = len(assistant.rag.get_index().chunks)
    print(f"База знаний: {chunks} фрагментов (карточки и загруженные документы)")
    print("Связь с моделью есть — ИИ-ответы в боте работают.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Запустить Rooms-бот без Docker")
    parser.add_argument("--host", help="адрес прослушивания; по умолчанию 127.0.0.1 для demo, 0.0.0.0 для production")
    parser.add_argument("--port", type=int, help="порт; по умолчанию PORT из .env или 8080")
    parser.add_argument("--check", action="store_true", help="проверить конфигурацию и завершить работу")
    parser.add_argument("--check-ai", action="store_true",
                        help="проверить связь с моделью ИИ из .env (AI_URL, AI_MODEL, AI_API_KEY) и завершить работу")
    args = parser.parse_args(argv)

    vendor = use_vendor()
    if not vendor["active"]:
        if not VENV_PYTHON.exists():
            raise SystemExit(
                "Зависимости не найдены: " + vendor["reason"] + ".\n"
                "Нужен архив с vendor/ под этот Python (scripts/vendor.py) либо окружение: "
                "./setup.sh --index-url https://ВНУТРЕННИЙ-PYPI/simple"
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

    try:
        app = create_app()
    except ValueError as error:  # неверное значение в .env — понятное сообщение вместо трассировки
        raise SystemExit("Ошибка в настройках .env: " + str(error)) from None
    mode = app.state.settings.mode
    host = args.host or ("127.0.0.1" if mode == "demo" else "0.0.0.0")
    port = args.port or int(os.getenv("PORT", "8080"))
    if not 1 <= port <= 65535:
        raise SystemExit("PORT должен быть от 1 до 65535")
    if args.check_ai:
        raise SystemExit(check_ai(app))
    if args.check:
        from app import __version__

        print(f"Версия {__version__}. Конфигурация корректна: mode={mode}, cards={len(app.state.engine.catalog.cards)}")
        return

    import uvicorn

    print(f"Rooms-бот запущен: http://{host}:{port} (mode={mode})")
    if mode == "demo" and app.state.settings.enable_dev_console:
        print(f"Демо-консоль: http://{host}:{port}/dev/chat")
        print(f"Дашборд: http://{host}:{port}/metrics/dashboard")
    uvicorn.run(app, host=host, port=port, workers=1, limit_concurrency=32, access_log=False)


if __name__ == "__main__":
    main()
