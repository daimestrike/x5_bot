"""Первоисточники карточек: слепки документов, сравнение версий, сопоставление изменений с карточками.

Бот не выводит карточки из документов автоматически (это делает человек). Задача модуля — заметить,
что документ изменился, показать «было / стало» с номером фрагмента и найти карточки, которых это
касается, чтобы владелец содержания их проверил.

Поддерживаемые форматы: .docx (абзацы и ячейки таблиц), .txt/.md (абзацы). PDF — через pypdf, если установлен.
Слепки лежат в content/sources/<key>.json; сами файлы документов не хранятся.
"""

import datetime as dt
import difflib
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .content import normalized

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOC_BYTES = 8 * 1024 * 1024
MIN_UNIT_CHARS = 20
STOP = set("это для того чтобы если или как что при после перед через все всех его её их они она оно был была было были".split())


class SourceError(ValueError):
    pass


# ------------------------------------------------------------------ извлечение текста
def extract_docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as e:
        raise SourceError("Не удалось прочитать .docx") from e
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise SourceError("Повреждённый document.xml") from e
    units = []
    for p in root.iter(W + "p"):
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if text:
            units.append(text)
    return units


def extract_text(data):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1251", errors="replace")
    return [" ".join(chunk.split()) for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]


def extract_pdf(data):
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise SourceError("PDF требует пакет pypdf (добавьте в wheels/)") from e
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001 — pypdf бросает разные типы
        raise SourceError("Не удалось прочитать PDF") from e
    units = []
    for n, page in enumerate(reader.pages, 1):
        for chunk in re.split(r"\n\s*\n", page.extract_text() or ""):
            chunk = " ".join(chunk.split())
            if chunk:
                units.append((n, chunk))
    return units


def extract(filename, data):
    """-> список фрагментов [{"n": порядковый, "page": стр|None, "text": ...}]."""
    if len(data) > MAX_DOC_BYTES:
        raise SourceError("Файл больше 8 МБ")
    ext = Path(filename).suffix.lower()
    if ext == ".docx":
        raw = [(None, t) for t in extract_docx(data)]
    elif ext in (".txt", ".md"):
        raw = [(None, t) for t in extract_text(data)]
    elif ext == ".pdf":
        raw = extract_pdf(data)
    else:
        raise SourceError("Поддерживаются .docx, .txt, .md, .pdf")
    units = [{"n": i, "page": page, "text": text} for i, (page, text) in enumerate(raw, 1)]
    if not units:
        raise SourceError("В документе не найден текст")
    return units


# ------------------------------------------------------------------ сравнение
_TOKEN_RE = re.compile(r"\w+|[^\w\s]+", re.UNICODE)


_OPEN = "«(["
_CLOSE = ".,;:!?»)]…"


def _join(tokens, prev=""):
    """Собирает токены в текст: пробел перед словом и открывающей скобкой, но не после них и не перед знаком."""
    out = ""
    for t in tokens:
        last = (out or prev)[-1:] if (out or prev) else ""
        need_space = bool(last) and last not in _OPEN and t[0] not in _CLOSE
        out += (" " if need_space else "") + t
    return out


def word_diff(old, new):
    """Пословный дифф (знаки препинания — отдельные токены) -> [(op, text)], op ∈ equal|delete|insert.
    Сегменты уже содержат нужные пробелы: их можно склеивать без разделителя."""
    a, b = _TOKEN_RE.findall(old), _TOKEN_RE.findall(new)
    out, prev = [], ""
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        parts = []
        if op == "equal":
            parts.append(("equal", a[i1:i2]))
        else:
            if i2 > i1:
                parts.append(("delete", a[i1:i2]))
            if j2 > j1:
                parts.append(("insert", b[j1:j2]))
        for kind, toks in parts:
            text = _join(toks, prev)
            out.append((kind, text))
            if kind != "delete":
                prev = text
    return out


def diff_units(old_units, new_units):
    """Изменённые фрагменты: added / removed / changed (пара старый-новый с пословным диффом)."""
    old_texts = [u["text"] for u in old_units]
    new_texts = [u["text"] for u in new_units]
    changes = []
    sm = difflib.SequenceMatcher(None, old_texts, new_texts, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            continue
        olds, news = old_units[i1:i2], new_units[j1:j2]
        # сопоставляем заменённые абзацы попарно по похожести
        used = set()
        for o in olds:
            best, score = None, 0.0
            for k, n in enumerate(news):
                if k in used:
                    continue
                r = difflib.SequenceMatcher(None, o["text"], n["text"], autojunk=False).ratio()
                if r > score:
                    best, score = k, r
            if best is not None and score >= 0.5:
                used.add(best)
                n = news[best]
                changes.append({"kind": "changed", "old": o, "new": n, "diff": word_diff(o["text"], n["text"])})
            else:
                changes.append({"kind": "removed", "old": o, "new": None, "diff": [("delete", o["text"])]})
        for k, n in enumerate(news):
            if k not in used:
                changes.append({"kind": "added", "old": None, "new": n, "diff": [("insert", n["text"])]})
    return changes


# ------------------------------------------------------------------ сопоставление с карточками
def tokens(text):
    return {t for t in normalized(text).split() if len(t) >= 4 and t not in STOP}


def card_signature(card):
    return tokens(card["title"] + " " + " ".join(card["steps"]) + " " + " ".join(card["synonyms"]))


def match_cards(change, cards, source_key, threshold=0.12, limit=5):
    """Карточки, которых, вероятно, касается изменение: доля общих значимых слов с фрагментом."""
    text = " ".join(x["text"] for x in (change.get("old"), change.get("new")) if x)
    ut = tokens(text)
    if not ut:
        return []
    scored = []
    for card in cards.values():
        sig = card_signature(card)
        if not sig:
            continue
        common = len(sig & ut)
        score = common / min(len(sig), len(ut))
        same_source = source_key and card.get("source_key") == source_key
        if common >= 3 and (score >= threshold or same_source and score >= threshold / 2):
            scored.append((round(score, 3), card["id"]))
    scored.sort(reverse=True)
    return [{"id": cid, "score": s} for s, cid in scored[:limit]]


# ------------------------------------------------------------------ слепки
class SourceStore:
    def __init__(self, content_dir):
        self.dir = Path(content_dir) / "sources"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        if not re.fullmatch(r"[a-z0-9_-]{1,32}", key):
            raise SourceError("Ключ источника: латиница, цифры, - и _")
        return self.dir / (key + ".json")

    def load(self, key):
        p = self.path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def list(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({k: d[k] for k in ("key", "title", "filename", "version", "sha256", "uploaded", "units_count")})
        return out

    def snapshot(self, key, title, filename, data):
        """Сохраняет новый слепок; возвращает (снимок, изменения относительно прошлого или None)."""
        units = extract(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        prev = self.load(key)
        changes = None
        if prev is not None:
            if prev["sha256"] == sha:
                return prev, []
            changes = diff_units(prev["units"], units)
        snap = {
            "key": key,
            "title": title or (prev or {}).get("title") or key,
            "filename": filename,
            "version": (prev["version"] + 1) if prev else 1,
            "sha256": sha,
            "uploaded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "units_count": len(units),
            "units": units,
        }
        self.path(key).write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        return snap, changes
