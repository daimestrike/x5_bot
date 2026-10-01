"""ИИ-ответ по базе знаний: связывает поиск (RAG) и модель (LLM).

Ответ всегда опирается на фрагменты базы знаний и ссылается на карточки кодом [A-07].
Если модель не сослалась ни на одну карточку, ответ помечается как неподтверждённый —
бот показывает его с оговоркой и предлагает меню.

Ответ не содержит персональных данных: в модель уходит только вопрос Аватара и фрагменты
согласованных материалов. Текст вопроса в журнал не пишется (как и в остальном боте).
"""

import time

from . import rag
from .llm import LLMError


class Answer:
    def __init__(self, text, cards, found, grounded, elapsed_ms, model):
        self.text = text
        self.cards = cards
        self.found = found
        self.grounded = grounded
        self.elapsed_ms = elapsed_ms
        self.model = model


class Assistant:
    def __init__(self, settings, catalog, sources, llm, log, cache_path=None):
        self.settings, self.catalog, self.log = settings, catalog, log
        self.llm = llm
        self.rag = rag.Rag(catalog, sources, llm, log, cache_path=cache_path)
        if self.enabled:
            self.rag.warm_async()

    @property
    def enabled(self):
        return bool(self.settings.ai_enabled and self.llm.enabled)

    def reload(self, catalog):
        """Карточки изменились — пересобрать индекс при следующем запросе."""
        self.catalog = catalog
        self.rag.catalog = catalog
        self.rag.invalidate()

    def wants(self, text):
        """Нужен ли ИИ для этого сообщения: фраза длиннее поиска по словам."""
        if not self.enabled:
            return False
        words = [w for w in (text or "").split() if w]
        return len(words) >= self.settings.ai_min_words

    def answer(self, question, history=()):
        started = time.monotonic()
        found = self.rag.search(question)
        messages = rag.build_messages(question, found, history)
        text = self.llm.complete(messages)
        cards = rag.cited_cards(text, self.catalog.cards)
        elapsed = int((time.monotonic() - started) * 1000)
        return Answer(text, cards, found, bool(cards), elapsed, self.llm.model)

    def safe_answer(self, question, history=()):
        """Ответ или None, если модель недоступна: бот продолжает работать по меню."""
        try:
            return self.answer(question, history)
        except LLMError as e:
            self.log(f"AI: {e.code} — {e}")
            return None
        except Exception as e:  # noqa: BLE001 — сбой ИИ не должен ронять диалог
            self.log(f"AI: непредвиденная ошибка ({e})")
            return None
