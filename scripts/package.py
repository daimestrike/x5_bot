"""Package source files only, excluding runtime data, caches and credentials."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parents[1]
(root / "dist").mkdir(exist_ok=True)
allowed_dirs = {"app", "content", "scripts", "tests", "docs", "wheels"}
allowed_files = {
    "README.md",
    "Dockerfile",
    "docker-compose.yml",
    ".env.example",
    ".gitignore",
    ".dockerignore",
    ".gitlab-ci.yml",
    "requirements.txt",
    "requirements-dev.txt",
    "Makefile",
    "pytest.ini",
    "ruff.toml",
}
with ZipFile(root / "dist/rooms-bot.zip", "w", ZIP_DEFLATED) as archive:
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if not path.is_file() or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if (len(relative.parts) == 1 and path.name in allowed_files) or relative.parts[0] in allowed_dirs:
            archive.write(path, "rooms_bot/" + str(relative))
print("dist/rooms-bot.zip")
