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
import uuid
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from .content import normalized
from .source_matching import CardMatcher, match_cards, tokens  # noqa: F401 — public matching API

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_DOC_BYTES = 8 * 1024 * 1024
MAX_UNITS = 1500
MAX_UNIT_CHARS = 12000


class SourceError(ValueError):
    pass


# ------------------------------------------------------------------ извлечение текста
def extract_docx(data):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            if sum(i.file_size for i in z.infolist()) > 40 * 1024 * 1024:
                raise SourceError('Слишком большой распакованный DOCX (более 40 МБ)')
            xml = z.read("word/document.xml")
            styles_xml = z.read('word/styles.xml') if 'word/styles.xml' in z.namelist() else None
    except (zipfile.BadZipFile, KeyError) as e:
        raise SourceError("Не удалось прочитать .docx") from e
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as e:
        raise SourceError("Повреждённый document.xml") from e
    styles = {}
    if styles_xml:
        try:
            for style in ElementTree.fromstring(styles_xml).iter(W + 'style'):
                styles[style.get(W + 'styleId')] = style
        except ElementTree.ParseError as e:
            raise SourceError('Повреждённый styles.xml') from e

    def level(p):
        outline = p.find('./' + W + 'pPr/' + W + 'outlineLvl')
        if outline is not None:
            value = outline.get(W + 'val', '')
            return int(value) + 1 if value.isdigit() and int(value) < 9 else None
        ps = p.find('./' + W + 'pPr/' + W + 'pStyle')
        name = ps.get(W + 'val', '') if ps is not None else ''
        seen = set()
        while name and name not in seen:
            seen.add(name)
            match = re.fullmatch(r'(?:heading|заголовок)\s*([1-9])', name, re.I)
            if match:
                return int(match[1])
            style = styles.get(name)
            if style is None:
                break
            outline = style.find('./' + W + 'pPr/' + W + 'outlineLvl')
            if outline is not None:
                value = outline.get(W + 'val', '')
                return int(value) + 1 if value.isdigit() and int(value) < 9 else None
            named = style.find(W + 'name')
            style_name = named.get(W + 'val', '') if named is not None else ''
            match = re.fullmatch(r'(?:heading|заголовок)\s*([1-9])', style_name, re.I)
            if match:
                return int(match[1])
            parent = style.find(W + 'basedOn')
            name = parent.get(W + 'val', '') if parent is not None else ''
        return None

    def text_of(p):
        return ' '.join(''.join(t.text or '' for t in p.iter(W + 't')).split())

    units, headings = [], []
    body = root.find(W + 'body')
    if body is None:
        raise SourceError('В DOCX нет основного текста')
    for block in body:
        if block.tag == W + 'p':
            text = text_of(block)
            depth = level(block)
            if not text:
                continue
            # Plain short labels ending with a colon are useful even without Word styles.
            if depth is None and len(text) <= 100 and text.endswith(':'):
                depth = 1
            if depth:
                headings = [(d, h) for d, h in headings if d < depth] + [(depth, text)]
            units.append(dict(text=text, section=[h for _, h in headings], kind='heading' if depth else 'paragraph'))
        elif block.tag == W + 'tbl':
            rows = []
            for row in block.findall(W + 'tr'):
                cells = [' / '.join(text_of(p) for p in cell.iter(W + 'p') if text_of(p))
                         for cell in row.findall(W + 'tc')]
                if any(cells):
                    rows.append(cells)
            for n, cells in enumerate(rows):
                units.append(dict(text=' | '.join(cells), section=[h for _, h in headings], kind='table_row',
                                  cells=cells, table_header=rows[0] if n else []))
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
        units = [dict(u, n=i, page=None) for i, u in enumerate(extract_docx(data), 1)]
    elif ext in (".txt", ".md"):
        raw = [(None, t) for t in extract_text(data)]
        units = [dict(n=i, page=page, text=text) for i, (page, text) in enumerate(raw, 1)]
    elif ext == ".pdf":
        raw = extract_pdf(data)
        units = [dict(n=i, page=page, text=text) for i, (page, text) in enumerate(raw, 1)]
    else:
        raise SourceError("Поддерживаются .docx, .txt, .md, .pdf")
    if not units:
        raise SourceError("В документе не найден текст")
    if len(units) > MAX_UNITS or any(len(u['text']) > MAX_UNIT_CHARS for u in units):
        raise SourceError('Документ слишком большой для сравнения: максимум 1500 фрагментов по 12000 знаков')
    for i, u in enumerate(units):
        adjacent = [v['text'][:500] for v in units[max(0, i - 1):i + 2]
                    if v is not u and v.get('section', []) == u.get('section', []) and v.get('kind') != 'heading']
        u['context'] = ' '.join(u.get('table_header', []) + adjacent)
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


def align_units(old_units, new_units):
    """One-to-one alignment independent of paragraph position. Ties stay unaligned."""
    def signature(u):
        return (normalized(u['text']), u.get('section', []), u.get('kind', 'paragraph'))

    if [signature(u) for u in old_units] == [signature(u) for u in new_units]:
        return {i: i for i in range(len(new_units))}
    pairs, used = {}, set()
    for j, new in enumerate(new_units):
        exact = [i for i, old in enumerate(old_units)
                 if i not in used and normalized(old['text']) == normalized(new['text'])]
        same_section = [i for i in exact if old_units[i].get('section', []) == new.get('section', [])]
        candidates = same_section or exact
        if len(candidates) > 1:
            contextual = [i for i in candidates if old_units[i].get('context') == new.get('context')]
            candidates = contextual if len(contextual) == 1 else []
        if len(candidates) == 1:
            pairs[j] = candidates[0]
            used.add(candidates[0])
    old_tokens = [tokens(u['text']) for u in old_units]
    proposed = {}
    for j, new in enumerate(new_units):
        if j in pairs:
            continue
        nt = tokens(new['text'])
        shortlist = []
        for i, old in enumerate(old_units):
            if i in used or old.get('kind', 'paragraph') != new.get('kind', 'paragraph'):
                continue
            common = len(nt & old_tokens[i])
            if common < 2:
                continue
            overlap = common / max(len(nt | old_tokens[i]), 1)
            shortlist.append((overlap, i))
        ranked = []
        for overlap, i in sorted(shortlist, reverse=True)[:24]:
            old = old_units[i]
            ratio = difflib.SequenceMatcher(None, old['text'], new['text'], autojunk=False).ratio()
            score = 0.7 * ratio + 0.3 * overlap
            if old.get('section') and old.get('section') == new.get('section'):
                score += 0.06
            if score >= 0.55:
                ranked.append((score, i))
        ranked.sort(reverse=True)
        if ranked and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.08):
            proposed[j] = ranked[0]
    # Mutual uniqueness avoids stealing a fragment when one paragraph was split into two.
    for j, (score, i) in proposed.items():
        competitors = [s for k, (s, oi) in proposed.items() if k != j and oi == i]
        if not competitors or score - max(competitors) >= 0.08:
            pairs[j] = i
    return pairs


def diff_units(old_units, new_units, pairs=None):
    pairs = align_units(old_units, new_units) if pairs is None else pairs
    changes = []
    for j, new in enumerate(new_units):
        old = old_units[pairs[j]] if j in pairs else None
        context_changed = old is not None and (
            old.get('section', []) != new.get('section', [])
            or old.get('table_header', []) != new.get('table_header', []))
        if old is None:
            changes.append(dict(kind='added', old=None, new=new, diff=[('insert', new['text'])]))
        elif old['text'].split() != new['text'].split() or context_changed:
            changes.append(dict(kind='changed', old=old, new=new, diff=word_diff(old['text'], new['text']),
                                context_changed=context_changed))
    for i, old in enumerate(old_units):
        if i not in pairs.values():
            changes.append(dict(kind='removed', old=old, new=None, diff=[('delete', old['text'])]))
    return changes


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

    def all(self):
        """Полные слепки с текстом — для индекса базы знаний ИИ."""
        out = []
        for path in sorted(self.dir.glob("*.json")):
            try:
                out.append(json.loads(path.read_text(encoding="utf-8")))
            except ValueError:
                continue
        return out

    def list(self):
        out = []
        for p in sorted(self.dir.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({**{k: d[k] for k in ("key", "title", "filename", "version", "sha256", "uploaded", "units_count")},
                        'linked_units': d.get('linked_units'), 'linked_cards': d.get('linked_cards')})
        return out

    def snapshot(self, key, title, filename, data, cards=None):
        """Сохраняет новый слепок; возвращает (снимок, изменения относительно прошлого или None)."""
        path = self.path(key)
        units = extract(filename, data)
        sha = hashlib.sha256(data).hexdigest()
        prev = self.load(key)
        old_units = prev['units'] if prev else []
        # Old snapshots had no structure. Bootstrap from a byte-identical reupload
        # without pretending that newly discovered headings are document edits.
        if prev and prev['sha256'] == sha and prev.get('algorithm_version') != 2:
            old_units = units
        pairs = align_units(old_units, units)
        matcher = CardMatcher(cards or {})
        for j, unit in enumerate(units):
            old = old_units[pairs[j]] if j in pairs else None
            unit['uid'] = old.get('uid', uuid.uuid4().hex) if old else uuid.uuid4().hex
            # Persisted history is accepted only through an unambiguous alignment.
            if old:
                unit['links'] = old.get('links', [])
            unit['links'] = matcher.match({'new': unit}, key)
        changes = diff_units(old_units, units, pairs) if prev else None
        for change in changes or []:
            change['matches'] = matcher.match(change, key)
        changed = bool(changes)
        snap = {
            "key": key,
            "title": title or (prev or {}).get("title") or key,
            "filename": filename,
            "version": (prev["version"] + int(changed)) if prev else 1,
            "sha256": sha,
            "uploaded": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "units_count": len(units),
            "units": units,
            "algorithm_version": 2,
            "linked_units": sum(bool(u['links']) for u in units),
            "linked_cards": len({m['id'] for u in units for m in u['links']}),
        }
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)
        return snap, changes
