"""Поиск по базе знаний для ИИ-ответа (RAG).

База знаний = карточки справочника (content/cards.yaml) и слепки документов-источников
(content/sources/*.json, которые создаёт страница «Содержание»). Ничего внешнего:
основа — BM25 с лёгким стеммингом для русского, работает без моделей и сети.

Если задан AI_EMBED_MODEL, результаты BM25 объединяются с векторным поиском
(reciprocal rank fusion). Векторы считаются в фоне и кэшируются в SQLite,
поэтому при недоступности эмбеддингов поиск продолжает работать на BM25.
"""

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections import Counter
from pathlib import Path

_STOP = set("""а без более бы был была были было быть в вам вас весь во вот все всего всех вы где да даже для до
его ее ей ему если есть еще же за здесь и из или им их к как ко когда кто ли либо меня мне может мы на надо наш
не него нее нет ни них но ну о об однако он она они оно от очень по под при про с со так также такой там те тем
то того тоже той только том ты у уже хотя чего чей чем что чтобы чье чья эта эти это этот я можно нужно какие
какой каких какая будет the and for with of to in on is are""".split())

_SUFFIXES = sorted(set("""иями ями ами ого его ому ему ыми ими ая яя ое ее ые ие ый ий ой ую юю ов ев ей ам ям
ах ях ом ем ию ия ья ье ью ьи ться тся ть ти ешь ет ют ут им ит ат ят ал ил ла ли ло ость ости остью ение
ения ений ению ением ениями ание ания аний анию анием аниями ация ации ацию ацией аций ировать ирование
ирования ированный ированных а я о е ы и у ю ь й""".split()), key=len, reverse=True)

# сокращения и синонимы, которыми Аватары пишут вопросы
_EXPAND = {
    "тсд": "терминал сбора данных устройство",
    "пи": "инвентаризация пересчет",
    "гк": "главная касса сейф деньги",
    "кп": "контрольный пересчет",
    "мол": "материально ответственное лицо",
    "дм": "директор магазина",
    "эцп": "электронная подпись",
    "вайфай": "wifi сеть интернет модем",
    "wifi": "сеть интернет модем",
    "инет": "интернет сеть модем",
    "связь": "интернет сеть модем звонок",
    "сети": "сеть интернет",
    "сеть": "сеть интернет",
    "симка": "сим карта модем",
    "наушники": "гарнитура звук",
    "камера": "съемка видео изображение",
    "румс": "rooms приложение звонок",
    "rooms": "румс приложение звонок",
}


def stem(word):
    """Лёгкий стемминг. Короткие окончания (-ем, -ом, -а) срезаются только у длинных слов,
    иначе ключевые термины вроде «модем» превращаются в «мод» и перестают находиться."""
    if not re.search("[а-я]", word):
        return word[:-1] if len(word) > 4 and word.endswith("s") else word
    for suffix in _SUFFIXES:
        if not word.endswith(suffix):
            continue
        rest = len(word) - len(suffix)
        if rest >= (4 if len(suffix) <= 2 else 3):
            return word[:-len(suffix)]
    return word


def tokenize(text, expand=False):
    words = re.findall(r"[a-zа-я0-9]+", (text or "").lower().replace("ё", "е"))
    out = []
    for word in words:
        if expand and word in _EXPAND:
            out.extend(tokenize(_EXPAND[word]))
        if len(word) < 2 or word in _STOP:
            continue
        out.append(stem(word))
    return out


# ---------------------------------------------------------------- фрагменты
def card_chunk(card, section_name=""):
    lines = [f"[{card['id']}] {card['title']}"]
    if section_name:
        lines.append("Раздел: " + section_name)
    lines.extend(f"{i}. {s}" for i, s in enumerate(card["steps"], 1))
    if card.get("source"):
        lines.append("Источник: " + card["source"])
    if card.get("url"):
        lines.append("Полный документ: " + card["url"])
    return {
        "ref": card["id"],
        "kind": "card",
        "title": card["title"],
        "text": "\n".join(lines),
        "boost": f"{card['title']} {card['title']} {' '.join(card.get('synonyms') or [])}",
    }


def split_units(units, size=900):
    """Абзацы документа -> фрагменты до size знаков, по границам абзацев."""
    chunks, current, first = [], [], None
    for unit in units:
        text = (unit.get("text") or "").strip()
        if not text:
            continue
        if current and sum(len(t) for t in current) + len(text) > size:
            chunks.append((first, current))
            current, first = [], None
        if first is None:
            first = unit
        current.append(text)
    if current:
        chunks.append((first, current))
    return chunks


def doc_chunks(source):
    title = source.get("title") or source.get("key")
    out = []
    parts = split_units(source.get("units") or [])
    for number, (first, texts) in enumerate(parts, 1):
        where = f"стр. {first.get('page')}" if first.get("page") else f"фрагмент {number}"
        header = f"[{source['key']}] {title} ({where})"
        out.append({
            "ref": source["key"],
            "kind": "doc",
            "title": title,
            "page": first.get("page"),
            "text": header + "\n" + "\n".join(texts),
            "boost": title,
        })
    return out


# ---------------------------------------------------------------- индекс
class BM25:
    def __init__(self, chunks, k1=1.4, b=0.75):
        self.chunks = chunks
        self.k1, self.b = k1, b
        self.tf = [Counter(tokenize(c["text"] + " " + c.get("boost", ""))) for c in chunks]
        self.len = [sum(t.values()) for t in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1
        df = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        n = len(chunks) or 1
        self.idf = {w: math.log(1 + (n - f + 0.5) / (f + 0.5)) for w, f in df.items()}

    def search(self, query, k):
        q = Counter(tokenize(query, expand=True))
        scores = []
        for i, tf in enumerate(self.tf):
            score = 0.0
            for word in q:
                f = tf.get(word)
                if f:
                    score += self.idf[word] * f * (self.k1 + 1) / (
                        f + self.k1 * (1 - self.b + self.b * self.len[i] / self.avg)
                    )
            if score > 0:
                scores.append((score, i))
        scores.sort(reverse=True)
        return scores[:k]


def cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def chunk_hash(model, text):
    return hashlib.sha1((model + "\n" + text).encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Отдельный файл SQLite: схема журнала бота не затрагивается."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS vectors (hash TEXT PRIMARY KEY, vector TEXT NOT NULL)")

    def _connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def load(self):
        with self._connect() as db:
            return {r["hash"]: json.loads(r["vector"]) for r in db.execute("SELECT hash,vector FROM vectors")}

    def save(self, vectors):
        with self._connect() as db:
            db.executemany("INSERT OR REPLACE INTO vectors VALUES (?,?)",
                           [(h, json.dumps(v)) for h, v in vectors.items()])


class Rag:
    def __init__(self, catalog, sources, llm, log, cache_path=None):
        self.catalog, self.sources, self.llm, self.log = catalog, sources, llm, log
        self.lock = threading.Lock()
        self.index = None
        self.dirty = True
        self.vectors = {}
        self.warming = False
        self.cache = EmbeddingCache(cache_path) if (cache_path and llm.embeddings_enabled) else None
        if self.cache:
            try:
                self.vectors = self.cache.load()
            except sqlite3.Error as e:
                self.log(f"RAG: кэш эмбеддингов недоступен ({e})")

    def invalidate(self):
        with self.lock:
            self.dirty = True
        self.warm_async()

    def _build(self):
        sections = self.catalog.settings.get("sections", {})
        chunks = [
            card_chunk(card, sections.get(card["section"], ""))
            for card in self.catalog.cards.values()
            if card.get("available", True)
        ]
        for source in self.sources.all():
            chunks.extend(doc_chunks(source))
        return BM25(chunks)

    def get_index(self):
        with self.lock:
            if self.dirty or self.index is None:
                self.index = self._build()
                self.dirty = False
            return self.index

    # -------- эмбеддинги (необязательно)
    def warm(self):
        if not (self.llm.embeddings_enabled and self.cache):
            return 0
        index = self.get_index()
        model = self.llm.embed_model
        todo = [(h, c["text"]) for c in index.chunks
                for h in [chunk_hash(model, c["text"])] if h not in self.vectors]
        if not todo:
            return 0
        vectors = self.llm.embed([t for _, t in todo])
        fresh = {h: v for (h, _), v in zip(todo, vectors)}
        self.vectors.update(fresh)
        self.cache.save(fresh)
        return len(fresh)

    def warm_async(self):
        if not (self.llm.embeddings_enabled and self.cache) or self.warming:
            return

        def run():
            self.warming = True
            try:
                added = self.warm()
                if added:
                    self.log(f"RAG: посчитаны эмбеддинги для {added} фрагментов")
            except Exception as e:  # noqa: BLE001 — модель/сеть не критичны, остаётся BM25
                self.log(f"RAG: эмбеддинги недоступны ({e}), работаю на BM25")
            finally:
                self.warming = False

        threading.Thread(target=run, daemon=True).start()

    # -------- поиск
    def search(self, query, k=5):
        index = self.get_index()
        if not index.chunks or not query.strip():
            return []
        ranked = {i: 1 / (60 + rank) for rank, (_, i) in enumerate(index.search(query, k * 3))}
        if self.llm.embeddings_enabled and self.vectors:
            try:
                qv = self.llm.embed([query])[0]
                model = self.llm.embed_model
                sims = []
                for i, chunk in enumerate(index.chunks):
                    vector = self.vectors.get(chunk_hash(model, chunk["text"]))
                    if vector:
                        sims.append((cosine(qv, vector), i))
                sims.sort(reverse=True)
                for rank, (score, i) in enumerate(sims[:k * 3]):
                    if score > 0.2:
                        ranked[i] = ranked.get(i, 0) + 1 / (60 + rank)
            except Exception as e:  # noqa: BLE001 — падение векторного поиска не ломает ответ
                self.log(f"RAG: векторный поиск пропущен ({e})")
        found, seen = [], set()
        for i, score in sorted(ranked.items(), key=lambda x: -x[1]):
            chunk = index.chunks[i]
            key = (chunk["kind"], chunk["ref"], chunk["text"][:60])
            if key in seen:
                continue
            seen.add(key)
            found.append(dict(chunk, score=round(score, 5)))
            if len(found) >= k:
                break
        return found


# ---------------------------------------------------------------- промпт
SYSTEM_PROMPT = """Ты — справочник Аватара на удалённой инвентаризации в магазине X5. Аватар — сотрудник \
с ТСД в нагрудном держателе; ревизор ведёт его голосом по видеосвязи. Ты помогаешь только с подготовкой, \
техникой и связью, правилами съёмки и порядком действий.

Правила:
- Отвечай ТОЛЬКО по фрагментам базы знаний ниже. Если ответа там нет — так и скажи и предложи открыть меню \
или спросить ревизора. Не придумывай шаги, кнопки, номера и контакты.
- Ссылайся на карточки их кодом в квадратных скобках: [A-07]. Коды бери только из контекста.
- Пиши коротко: 2–5 шагов повелительным наклонением, как в карточках («свайпните сверху вниз»). \
Максимум 600 знаков. Без вступлений и прощаний.
- Человек стоит в зале с занятыми руками: сначала действие, потом пояснение.
- Этапами инвентаризации руководит ревизор голосом — пошаговых инструкций по этапам не давай.
- Действий в GK, Inventa и Rooms ты не выполняешь: ничего не заносишь, не закрываешь, не отправляешь.
- Паролей, кодов и учётных данных не называй: пароль модема виден на его экране.
- Отвечай по-русски."""

NO_CONTEXT = "По базе знаний ничего не нашлось."


def build_context(found):
    if not found:
        return "ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ: ничего не найдено."
    parts = ["ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ (по убыванию релевантности):"]
    for chunk in found:
        parts.append(chunk["text"])
        parts.append("---")
    return "\n".join(parts)


def build_messages(question, found, history=()):
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "system", "content": build_context(found)}]
    for turn in list(history)[-4:]:
        if turn.get("role") in ("user", "assistant") and str(turn.get("content", "")).strip():
            messages.append({"role": turn["role"], "content": str(turn["content"])[:1500]})
    messages.append({"role": "user", "content": question[:500]})
    return messages


CITATION_RE = re.compile(r"\[([A-E]-\d{2})\]")


def cited_cards(answer, cards):
    """Коды карточек, на которые сослалась модель и которые реально есть в каталоге."""
    out = []
    for code in CITATION_RE.findall(answer or ""):
        if code in cards and code not in out:
            out.append(code)
    return out
