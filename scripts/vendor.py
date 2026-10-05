"""Сложить зависимости в vendor/ — чтобы на сервере не нужны были ни сеть, ни pip, ни venv.

Запускается на машине С доступом в интернет (например, ноутбук), результат уезжает в архив:

    python scripts/vendor.py                       # Linux x86_64, Python 3.10
    python scripts/vendor.py --python-version 3.11
    python scripts/vendor.py --arch aarch64        # ARM-сервер
    python scripts/vendor.py --clean               # убрать vendor/ перед сборкой

Пакеты скачиваются под целевую платформу, а не под текущую: на macOS и Windows получится
тот же набор для Linux. Двоичные модули (cryptography, pydantic-core, PyYAML, cffi) собраны
под указанную версию Python — на сервере должна стоять она же.

Проверить, что получилось, можно прямо здесь:
    python scripts/vendor.py --verify

Точные версии фиксируются в requirements.lock: повторная сборка даёт тот же набор, что
проверялся тестами. Обновить версии осознанно — флаг --refresh. Рядом с пакетами пишется
vendor/VENDOR.json: под какой Python, ОС и архитектуру собран набор. По этой метке бот
решает, подключать ли vendor/ (app/_bootstrap.py).
"""

import argparse
import datetime as dt
import json
import re
import shutil
import struct
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor"
LOCK = ROOT / "requirements.lock"
MARKER = VENDOR / "VENDOR.json"
PLATFORMS = {
    "x86_64": ["manylinux2014_x86_64", "manylinux_2_17_x86_64", "manylinux_2_28_x86_64"],
    "aarch64": ["manylinux2014_aarch64", "manylinux_2_17_aarch64", "manylinux_2_28_aarch64"],
}
# Служебное из wheel-пакетов: на сервере не нужно и только занимает место
JUNK_DIRS = ("bin", "__pycache__")


def installed_versions():
    out = {}
    for info in VENDOR.glob("*.dist-info"):
        name, _, version = info.name[: -len(".dist-info")].partition("-")
        out[name.lower().replace("_", "-")] = version
    return dict(sorted(out.items()))


def write_lock(versions):
    lines = ["# Точные версии поставки vendor/. Собрано scripts/vendor.py; не правьте вручную.",
             "# Обновить: python scripts/vendor.py --refresh"]
    lines += [f"{name}=={version}" for name, version in versions.items()]
    LOCK.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_marker(python_version, arch, versions):
    MARKER.write_text(json.dumps({
        "python": python_version,
        "system": "linux",
        "arch": arch,
        "built": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "packages": versions,
    }, ensure_ascii=False, indent=1), encoding="utf-8")


def build(python_version, arch, requirements):
    abi = "cp" + python_version.replace(".", "")
    command = [
        sys.executable, "-m", "pip", "install",
        "--target", str(VENDOR),
        "--only-binary=:all:",
        "--implementation", "cp",
        "--python-version", python_version,
        "--abi", abi,
        "--upgrade",
    ]
    for platform in PLATFORMS[arch]:
        command += ["--platform", platform]
    command += ["-r", str(requirements)]
    subprocess.run(command, cwd=ROOT, check=True)


def tidy():
    removed = 0
    for junk in JUNK_DIRS:
        for path in VENDOR.rglob(junk):
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
    return removed


def size_mb(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1024 / 1024


ELF_MACHINE = {0x3E: "x86_64", 0xB7: "aarch64"}


def binary_report():
    """Проверяет, что .so действительно собраны под Linux и нужную архитектуру."""
    machines, broken = set(), []
    for library in VENDOR.rglob("*.so"):
        head = library.open("rb").read(20)
        if head[:4] != b"\x7fELF":
            broken.append(library.name)
            continue
        machines.add(ELF_MACHINE.get(struct.unpack_from("<H", head, 18)[0], "неизвестная"))
    return machines, broken


def verify():
    """Проверяет, что все пакеты из requirements.txt лежат в vendor/."""
    if not VENDOR.is_dir():
        raise SystemExit("vendor/ нет — сначала соберите: python scripts/vendor.py")
    names = {p.name.split("-")[0].lower().replace("_", "-") for p in VENDOR.glob("*.dist-info")}
    missing = []
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        package = line.split(">")[0].split("<")[0].split("=")[0].strip().lower().replace("_", "-")
        if package not in names:
            missing.append(package)
    if missing:
        raise SystemExit("В vendor/ не хватает пакетов: " + ", ".join(missing))
    tags = {p.name.split(".")[1] for p in VENDOR.rglob("*.so") if "." in p.name}
    versions = sorted({f"{m.group(1)}.{m.group(2)}" for tag in tags
                       for m in [re.match(r"cpython-(\d)(\d+)", tag)] if m})
    print(f"vendor/: {len(names)} пакетов, {size_mb(VENDOR):.1f} МБ")
    if versions:
        print("  двоичные модули собраны под Python " + ", ".join(versions) +
              " — на сервере должна стоять эта версия")
    if any(t.startswith("abi3") for t in tags):
        print("  часть модулей в формате abi3 — они работают на любом Python 3.9+")
    machines, broken = binary_report()
    if broken:
        raise SystemExit("Не ELF-библиотеки (собрано не под Linux): " + ", ".join(broken))
    if machines:
        print("  формат двоичных файлов: Linux ELF " + ", ".join(sorted(machines)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Собрать зависимости в vendor/ для переноса в контур")
    parser.add_argument("--python-version", default="3.10", help="версия Python на сервере (по умолчанию 3.10)")
    parser.add_argument("--arch", choices=sorted(PLATFORMS), default="x86_64", help="архитектура сервера")
    parser.add_argument("--dev", action="store_true", help="добавить pytest, Ruff и openpyxl")
    parser.add_argument("--clean", action="store_true", help="очистить vendor/ перед сборкой")
    parser.add_argument("--verify", action="store_true", help="только проверить уже собранный vendor/")
    parser.add_argument("--refresh", action="store_true",
                        help="взять свежие версии по requirements.txt вместо requirements.lock")
    args = parser.parse_args(argv)

    if args.verify:
        return verify()

    if args.clean and VENDOR.exists():
        shutil.rmtree(VENDOR)
    print(f"Скачиваю зависимости: Linux {args.arch}, Python {args.python_version}")
    print(f"  текущая платформа {sysconfig.get_platform()} — для сборки это неважно")
    if args.dev:
        source = ROOT / "requirements-dev.txt"
    elif LOCK.exists() and not args.refresh:
        source = LOCK
        print("  версии из requirements.lock — тот же набор, что проверялся тестами")
    else:
        source = ROOT / "requirements.txt"
    build(args.python_version, args.arch, source)
    tidy()
    versions = installed_versions()
    write_marker(args.python_version, args.arch, versions)
    if source != LOCK and not args.dev:
        write_lock(versions)
        print(f"  точные версии записаны в {LOCK.name}")
    verify()
    print("\nГотово. Теперь соберите архив: python scripts/package.py --name rooms-bot-<версия>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
