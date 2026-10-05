"""Подключение поставочных зависимостей из vendor/ — до импорта сторонних библиотек.

Вызывается из app/__init__.py, поэтому срабатывает при любом входе в проект: сервер (run.py),
установщик, scripts/*.py, проверка конфигурации, тесты. Без этого каждый скрипт, запущенный
отдельным процессом, искал бы fastapi и yaml в системном Python — а на сервере их нет.

vendor/ подключается только если собран под этот самый Python, ОС и архитектуру (метка
vendor/VENDOR.json пишется scripts/vendor.py). Иначе двоичные модули не загрузятся, а часть
кода не разберётся синтаксически — понятнее сразу сказать, под что собран vendor/.
"""

import json
import platform
import sys
import sysconfig
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[1] / "vendor"
MARKER = VENDOR / "VENDOR.json"

status = {"active": False, "reason": "каталога vendor/ нет"}


def machine():
    name = platform.machine().lower()
    return {"amd64": "x86_64", "arm64": "aarch64"}.get(name, name)


def current():
    return {
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "system": platform.system().lower(),
        "arch": machine(),
    }


def _compatible():
    """(подходит ли vendor/, причина если нет)."""
    here = current()
    if MARKER.exists():
        try:
            meta = json.loads(MARKER.read_text(encoding="utf-8"))
        except ValueError:
            return False, "vendor/VENDOR.json повреждён"
        target = {key: str(meta.get(key, "")) for key in ("python", "system", "arch")}
        if target != here:
            return False, (
                f"vendor/ собран под Python {target['python']} {target['system']} {target['arch']}, "
                f"а запущен Python {here['python']} {here['system']} {here['arch']}"
            )
        return True, ""
    # vendor/ без метки (собран старой версией скрипта): сверяем двоичные модули
    suffix = sysconfig.get_config_var("EXT_SUFFIX") or ""
    natives = [p.name for p in VENDOR.rglob("*.so") if ".abi3." not in p.name]
    foreign = [name for name in natives if not name.endswith(suffix)]
    if foreign:
        return False, f"двоичные модули vendor/ собраны не под этот Python ({foreign[0]})"
    return True, ""


def activate():
    if not VENDOR.is_dir():
        return status
    ok, reason = _compatible()
    if not ok:
        status.update(active=False, reason=reason)
        return status
    path = str(VENDOR)
    if path not in sys.path:
        sys.path.insert(0, path)  # раньше site-packages: поставочные версии важнее системных
    status.update(active=True, reason="")
    return status
