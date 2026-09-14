"""Импорт карточек из xlsx: формат v11 (листы-карточки) и v9 (реестр)."""
import subprocess
import sys
from pathlib import Path

import openpyxl
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["ID и название", "Тип", "Когда применять", "Что подготовить", "Текст / действия", "Ожидаемый результат",
          "Если не получается", "Источник", "Ответственный", "Проверяющий", "Версия и дата", "Статус"]


def _v11_book(path: Path, rows: dict, sheet="B-09 модем"):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Карточка чек-листа"
    ws.append(["Поле", "Что указать", "Для заполнения"])
    for f in FIELDS:
        ws.append([f, "…", ""])
    ws2 = wb.create_sheet(sheet)
    ws2.append(["Поле", "Что указать", "Для заполнения"])
    for f in FIELDS:
        ws2.append([f, "…", rows.get(f, "")])
    wb.save(path)


def test_import_v11_card(tmp_path):
    xlsx = tmp_path / "v11.xlsx"
    _v11_book(xlsx, {
        "ID и название": "B-09. Модем греется", "Тип": "Чек-лист", "Когда применять": "Модем горячий",
        "Что подготовить": "Модем", "Текст / действия": "1. Выключите модем\n2. Подождите минуту\n3. Включите",
        "Ожидаемый результат": "Модем работает", "Если не получается": "Поддержка ТСД",
        "Источник": "Инструкция, стр. 7", "Ответственный": "Иванов", "Проверяющий": "Петров",
        "Версия и дата": "1.2 от 2026-09-20", "Статус": "Опубликован",
    })
    out = tmp_path / "cards.yaml"
    r = subprocess.run([sys.executable, str(ROOT / "scripts/import_registry.py"), str(xlsx), "--out", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    c = doc["cards"][0]
    assert c["id"] == "B-09" and c["title"] == "Модем греется" and c["type"] == "checklist"
    assert c["steps"] == ["Выключите модем", "Подождите минуту", "Включите"]
    assert c["result"] == "Модем работает" and c["status"] == "published"
    assert c["version"] == "1.2" and c["checked"] == "2026-09-20" and c["source"]["link_key"] == "instruction"


def test_import_merge_keeps_existing(tmp_path):
    xlsx = tmp_path / "v11.xlsx"
    _v11_book(xlsx, {"ID и название": "A-01 Новая версия", "Тип": "Ответ", "Текст / действия": "Текст",
                     "Версия и дата": "2.0", "Статус": "Черновик"})
    out = tmp_path / "cards.yaml"
    out.write_text((ROOT / "content/cards.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    r = subprocess.run([sys.executable, str(ROOT / "scripts/import_registry.py"), str(xlsx), "--out", str(out), "--merge"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    doc = yaml.safe_load(out.read_text(encoding="utf-8"))
    versions = [(c["version"], c["status"]) for c in doc["cards"] if c["id"] == "A-01"]
    assert versions == [("1.0", "published"), ("2.0", "draft")] and len(doc["cards"]) == 36
