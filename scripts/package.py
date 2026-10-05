"""Сборка архива для переноса во внутренний контур.

    python scripts/package.py              # dist/rooms-bot.tar.gz и dist/rooms-bot.zip
    python scripts/package.py --format tar # только tar.gz
    python scripts/package.py --name rooms-bot-1.1.0

В архив попадает только исходный код и согласованное содержание. Не попадают:
.env и любые секреты, журнал data/, слепки документов и очередь проверки, кэши,
резервные копии, выгрузки. Файл content/participants.yaml попадает как шаблон —
скрипт предупредит, если в нём уже заполнены подписи участников пилота. Внутри архива всё лежит в папке rooms_bot/, поэтому
распаковка не засоряет текущий каталог.
"""

import argparse
import hashlib
import tarfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_DIRS = {"app", "content", "scripts", "tests", "docs", "wheels", "deploy", "vendor"}
ALLOWED_FILES = {
    "README.md",
    "QUICKSTART.md",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    ".gitlab-ci.yml",
    "requirements.txt",
    "requirements-dev.txt",
    "requirements.lock",
    "Makefile",
    "pytest.ini",
    "ruff.toml",
    "install.py",
    "run.py",
    "setup.sh",
    "start.sh",
    "update.sh",
    "setup.cmd",
    "start.cmd",
}
# Рабочие данные конкретной установки: в архив не кладём
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", "review", "sources", ".validate"}
EXCLUDED_NAMES = set()
# Архив нужен, чтобы запустить бота. Внутренние материалы (бизнес-кейс, пакет решений)
# в него не кладём: их раздают отдельно и не через релиз репозитория.
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".sqlite3", ".docx", ".xlsx", ".pptx", ".pdf", ".heic"}


def included():
    """Файлы архива в стабильном порядке."""
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if set(relative.parts) & EXCLUDED_PARTS or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if any(part.startswith("._") or part in (".DS_Store", "Thumbs.db") for part in relative.parts):
            continue  # служебные файлы macOS/Windows
        # .env.example нужен, а заполненный .env и его варианты — нет
        if path.name in EXCLUDED_NAMES or (path.name.startswith(".env") and path.name != ".env.example"):
            continue
        top = relative.parts[0]
        if (len(relative.parts) == 1 and path.name in ALLOWED_FILES) or top in ALLOWED_DIRS:
            yield path, relative


def write_tar(target, name):
    with tarfile.open(target, "w:gz") as archive:
        for path, relative in included():
            info = archive.gettarinfo(str(path), arcname=f"{name}/{relative}")
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mode = 0o755 if path.suffix in (".sh", ".cmd") else 0o644
            with path.open("rb") as handle:
                archive.addfile(info, handle)


def write_zip(target, name):
    with ZipFile(target, "w", ZIP_DEFLATED) as archive:
        for path, relative in included():
            archive.write(path, f"{name}/{relative}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--format", choices=("tar", "zip", "both"), default="both")
    parser.add_argument("--name", default="rooms-bot", help="имя архива и корневой папки внутри него")
    args = parser.parse_args()

    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    made = []
    if args.format in ("tar", "both"):
        target = dist / f"{args.name}.tar.gz"
        write_tar(target, args.name)
        made.append(target)
    if args.format in ("zip", "both"):
        target = dist / f"{args.name}.zip"
        write_zip(target, args.name)
        made.append(target)

    labels = ROOT / "content" / "participants.yaml"
    if labels.exists() and any(
        line.strip() and not line.lstrip().startswith("#") for line in labels.read_text(encoding="utf-8").splitlines()
    ):
        print("ВНИМАНИЕ: content/participants.yaml содержит подписи участников пилота — "
              "очистите файл, если архив уходит за пределы команды.")

    files = sum(1 for _ in included())
    for target in made:
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        (target.parent / (target.name + ".sha256")).write_text(f"{digest}  {target.name}\n", encoding="utf-8")
        print(f"{target.relative_to(ROOT)}  {target.stat().st_size // 1024} КБ  файлов: {files}")
        print(f"  sha256 {digest}")


if __name__ == "__main__":
    main()
