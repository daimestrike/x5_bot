"""Загрузка и проверка содержания: карточки, экраны, маршрут помощи, параметры."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

log = logging.getLogger(__name__)

TITLE_MAX = 60
TEXT_MAX = 600
STEPS_MAX = 6
SYNONYMS_MIN = 3
SECRET_RE = re.compile(r"\b(пароль|код|password|логин\s*:)\b", re.IGNORECASE)
CARD_ID_RE = re.compile(r"^[A-E]-\d{2}$")


class ContentError(Exception):
    pass


@dataclass
class Card:
    id: str
    section: str
    title: str
    synonyms: List[str]
    steps: List[str]
    source: str = ""
    link_key: Optional[str] = None
    link: Optional[str] = None
    version: str = "1.0"
    updated: str = ""
    owner: str = ""

    @property
    def text(self) -> str:
        return " ".join(self.steps)

    def search_blob(self) -> Dict[str, str]:
        return {
            "title": self.title.lower(),
            "synonyms": " ".join(self.synonyms).lower(),
            "text": self.text.lower(),
        }


@dataclass
class Section:
    id: str
    title: str
    cards: List[Card] = field(default_factory=list)


@dataclass
class HelpRoute:
    key: str
    button: str
    text: str


@dataclass
class Content:
    sections: List[Section]
    cards: Dict[str, Card]
    texts: Dict[str, str]
    labels: Dict[str, str]
    thanks_words: List[str]
    action_words: List[str]
    help_routes: List[HelpRoute]
    params: dict
    warnings: List[str] = field(default_factory=list)

    def section(self, sid: str) -> Optional[Section]:
        for s in self.sections:
            if s.id == sid:
                return s
        return None

    def link_for(self, card: Optional[Card]) -> str:
        """URL полной инструкции для карточки (или инструкция по умолчанию)."""
        links = self.params.get("links", {}) or {}
        if card is not None:
            if card.link:
                return card.link
            if card.link_key and links.get(card.link_key):
                return str(links[card.link_key])
        return str(links.get("instruction", ""))

    def render_steps(self, card: Card) -> List[str]:
        """Шаги карточки с подстановкой параметров и ссылок."""
        out = []
        link = self.link_for(card)
        links = self.params.get("links", {}) or {}
        for s in card.steps:
            s = s.replace("<ссылка>", links.get("holder_video", link) if card.id == "A-06" else link)
            s = _substitute(s, self.params)
            out.append(s)
        return out

    def help_text(self, key: str) -> Optional[str]:
        for r in self.help_routes:
            if r.key == key:
                return _substitute(r.text.strip(), {"contacts": self.params.get("contacts", {})})
        return None


def _substitute(text: str, params: dict) -> str:
    """{contacts.pi_curator} / {corp_network_name} -> значения из params."""

    def repl(m: "re.Match[str]") -> str:
        path = m.group(1).split(".")
        cur: object = params
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                return m.group(0)
        return str(cur)

    return re.sub(r"\{([a-z_][a-z0-9_.]*)\}", repl, text)


def _read_yaml(path: Path) -> dict:
    if not path.exists():
        raise ContentError(f"Файл содержания не найден: {path}")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ContentError(f"Ожидался словарь в {path}")
    return data


def validate_cards(cards: List[Card]) -> tuple[List[str], List[str]]:
    """Возвращает (ошибки, предупреждения) по правилам листа «Правила ответов»."""
    errors: List[str] = []
    warnings: List[str] = []
    seen = set()
    for c in cards:
        if not CARD_ID_RE.match(c.id):
            errors.append(f"{c.id}: неверный формат ID (ожидается A-07)")
        if c.id in seen:
            errors.append(f"{c.id}: дубликат ID")
        seen.add(c.id)
        if not c.title:
            errors.append(f"{c.id}: пустой заголовок")
        if len(c.title) > TITLE_MAX:
            errors.append(f"{c.id}: заголовок длиннее {TITLE_MAX} знаков ({len(c.title)})")
        if not c.steps or not any(s.strip() for s in c.steps):
            errors.append(f"{c.id}: пустой текст ответа")
        if len(c.steps) > STEPS_MAX:
            errors.append(f"{c.id}: больше {STEPS_MAX} шагов ({len(c.steps)})")
        if len(c.text) > TEXT_MAX:
            errors.append(f"{c.id}: текст длиннее {TEXT_MAX} знаков ({len(c.text)})")
        if len(c.synonyms) < SYNONYMS_MIN:
            warnings.append(f"{c.id}: меньше {SYNONYMS_MIN} синонимов")
        if not c.version:
            errors.append(f"{c.id}: не указана версия")
        if not c.owner:
            warnings.append(f"{c.id}: не указан владелец содержания")
        if not (c.link or c.link_key):
            warnings.append(f"{c.id}: нет ссылки на источник")
        m = SECRET_RE.search(c.title + " " + c.text)
        if m:
            warnings.append(f"{c.id}: встречается слово «{m.group(1)}» — проверьте, что в тексте нет секретов")
    return errors, warnings


def load_content(content_dir: Path, strict: bool = True) -> Content:
    cards_doc = _read_yaml(content_dir / "cards.yaml")
    screens_doc = _read_yaml(content_dir / "screens.yaml")
    help_doc = _read_yaml(content_dir / "help_routes.yaml")
    params = _read_yaml(content_dir / "params.yaml")

    sections = [Section(id=str(s["id"]), title=str(s["title"])) for s in cards_doc.get("sections", [])]
    by_id: Dict[str, Section] = {s.id: s for s in sections}
    cards: List[Card] = []
    for raw in cards_doc.get("cards", []):
        card = Card(
            id=str(raw.get("id", "")).strip(),
            section=str(raw.get("section", "")).strip(),
            title=str(raw.get("title", "")).strip(),
            synonyms=[str(s).strip() for s in (raw.get("synonyms") or []) if str(s).strip()],
            steps=[str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()],
            source=str(raw.get("source") or ""),
            link_key=raw.get("link_key") or None,
            link=raw.get("link") or None,
            version=str(raw.get("version") or ""),
            updated=str(raw.get("updated") or ""),
            owner=str(raw.get("owner") or ""),
        )
        if card.section not in by_id:
            raise ContentError(f"{card.id}: неизвестный раздел «{card.section}»")
        by_id[card.section].cards.append(card)
        cards.append(card)

    errors, warnings = validate_cards(cards)
    for w in warnings:
        log.warning("content: %s", w)
    if errors:
        msg = "Ошибки содержания:\n  " + "\n  ".join(errors)
        if strict:
            raise ContentError(msg)
        log.error(msg)

    routes_raw = help_doc.get("routes", {}) or {}
    help_routes = [HelpRoute(key=str(k), button=str(v.get("button", k)), text=str(v.get("text", "")))
                   for k, v in routes_raw.items()]
    if not help_routes:
        raise ContentError("help_routes.yaml: нет ни одного маршрута помощи")
    params = dict(params)
    params["contacts"] = help_doc.get("contacts", {}) or {}

    texts = {str(k): str(v) for k, v in (screens_doc.get("texts") or {}).items()}
    labels = {str(k): str(v) for k, v in (screens_doc.get("labels") or {}).items()}
    required_texts = ["S0", "S1", "S2", "S3_more", "S3a", "S4", "S4a", "S5", "S5a", "S5b", "S6", "S7", "S8"]
    missing = [t for t in required_texts if t not in texts]
    if missing:
        raise ContentError(f"screens.yaml: нет текстов {missing}")

    return Content(
        sections=sections,
        cards={c.id: c for c in cards},
        texts=texts,
        labels=labels,
        thanks_words=[str(w).lower() for w in screens_doc.get("thanks_words", [])],
        action_words=[str(w).lower() for w in screens_doc.get("action_words", [])],
        help_routes=help_routes,
        params=params,
        warnings=warnings,
    )
