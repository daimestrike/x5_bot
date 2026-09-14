"""Загрузка и проверка содержания (формат карточки v11): карточки, экраны, маршрут помощи, параметры."""
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
SECRET_RE = re.compile(r"\b(пароль|код|password|логин\s*:)\b", re.IGNORECASE)
CARD_ID_RE = re.compile(r"^[A-Z]-\d{2}$")
TYPES = ("answer", "instruction", "checklist")
STATUSES = ("draft", "approved", "published", "retired")


class ContentError(Exception):
    pass


@dataclass
class Source:
    doc: str = ""
    section: str = ""
    link_key: Optional[str] = None
    url: Optional[str] = None
    version: str = ""


@dataclass
class Card:
    id: str
    type: str
    group: str
    title: str
    steps: List[str]
    when: str = ""
    prepare: str = ""
    result: str = ""
    help: str = ""
    synonyms: List[str] = field(default_factory=list)
    source: Source = field(default_factory=Source)
    owner: str = ""
    reviewer: str = ""
    version: str = "1.0"
    checked: str = ""
    status: str = "draft"

    @property
    def text(self) -> str:
        return " ".join(self.steps)

    @property
    def published(self) -> bool:
        return self.status == "published"

    def search_blob(self) -> Dict[str, str]:
        return {
            "title": self.title.lower(),
            "synonyms": " ".join(self.synonyms).lower(),
            "text": (self.text + " " + self.when).lower(),
        }


@dataclass
class Named:
    id: str
    title: str


@dataclass
class HelpRoute:
    key: str
    button: str
    text: str


@dataclass
class Content:
    types: List[Named]
    groups: List[Named]
    cards: Dict[str, Card]                      # только опубликованные, по id
    all_versions: Dict[str, List[Card]]         # все записи по id (черновики, снятые) — для разбора обращений
    texts: Dict[str, str]
    labels: Dict[str, str]
    thanks_words: List[str]
    action_words: List[str]
    help_routes: List[HelpRoute]
    params: dict
    warnings: List[str] = field(default_factory=list)

    def type_title(self, tid: str) -> Optional[str]:
        return next((t.title for t in self.types if t.id == tid), None)

    def group_title(self, gid: str) -> Optional[str]:
        return next((g.title for g in self.groups if g.id == gid), None)

    def cards_of_type(self, tid: str) -> List[Card]:
        return [c for c in self.cards.values() if c.type == tid]

    def groups_of_type(self, tid: str) -> List[Named]:
        present = {c.group for c in self.cards_of_type(tid)}
        return [g for g in self.groups if g.id in present]

    def link_for(self, card: Optional[Card]) -> str:
        """URL полного документа для карточки (или инструкция по умолчанию)."""
        links = self.params.get("links", {}) or {}
        if card is not None:
            if card.source.url:
                return card.source.url
            if card.source.link_key and links.get(card.source.link_key):
                return str(links[card.source.link_key])
        return str(links.get("instruction", ""))

    def render_steps(self, card: Card) -> List[str]:
        link = self.link_for(card)
        links = self.params.get("links", {}) or {}
        out = []
        for s in card.steps:
            s = s.replace("<ссылка>", links.get("holder_video", link) if card.id == "A-06" else link)
            out.append(_substitute(s, self.params))
        return out

    def help_text(self, key: str) -> Optional[str]:
        for r in self.help_routes:
            if r.key == key:
                return _substitute(r.text.strip(), {"contacts": self.params.get("contacts", {})})
        return None


def _substitute(text: str, params: dict) -> str:
    """{contacts.pi_curator} / {corp_network_name} -> значения из params."""

    def repl(m: "re.Match[str]") -> str:
        cur: object = params
        for p in m.group(1).split("."):
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


def _card_from_raw(raw: dict) -> Card:
    src_raw = raw.get("source") or {}
    if isinstance(src_raw, str):
        src_raw = {"section": src_raw}
    return Card(
        id=str(raw.get("id", "")).strip(),
        type=str(raw.get("type") or "answer").strip(),
        group=str(raw.get("group", "")).strip(),
        title=str(raw.get("title", "")).strip(),
        steps=[str(s).strip() for s in (raw.get("steps") or []) if str(s).strip()],
        when=str(raw.get("when") or "").strip(),
        prepare=str(raw.get("prepare") or "").strip(),
        result=str(raw.get("result") or "").strip(),
        help=str(raw.get("help") or "").strip(),
        synonyms=[str(s).strip() for s in (raw.get("synonyms") or []) if str(s).strip()],
        source=Source(
            doc=str(src_raw.get("doc") or ""), section=str(src_raw.get("section") or ""),
            link_key=src_raw.get("link_key") or None, url=src_raw.get("url") or None,
            version=str(src_raw.get("version") or ""),
        ),
        owner=str(raw.get("owner") or ""),
        reviewer=str(raw.get("reviewer") or ""),
        version=str(raw.get("version") or ""),
        checked=str(raw.get("checked") or ""),
        status=str(raw.get("status") or "draft").strip(),
    )


def validate_cards(cards: List[Card], groups: List[str]) -> "tuple[List[str], List[str]]":
    """(ошибки, предупреждения) по листам «Подготовка чек-листов» и «Карточка чек-листа»."""
    errors: List[str] = []
    warnings: List[str] = []
    published_ids: Dict[str, int] = {}
    seen_versions = set()
    for c in cards:
        tag = f"{c.id} v{c.version}"
        if not CARD_ID_RE.match(c.id):
            errors.append(f"{tag}: неверный формат ID (ожидается A-07)")
        if (c.id, c.version) in seen_versions:
            errors.append(f"{tag}: дубликат id+version")
        seen_versions.add((c.id, c.version))
        if c.type not in TYPES:
            errors.append(f"{tag}: неизвестный тип «{c.type}» (answer/instruction/checklist)")
        if c.status not in STATUSES:
            errors.append(f"{tag}: неизвестный статус «{c.status}» (draft/approved/published/retired)")
        if c.group not in groups:
            errors.append(f"{tag}: неизвестная тема «{c.group}»")
        if c.published:
            published_ids[c.id] = published_ids.get(c.id, 0) + 1
        if not c.title:
            errors.append(f"{tag}: пустое название")
        if len(c.title) > TITLE_MAX:
            errors.append(f"{tag}: название длиннее {TITLE_MAX} знаков ({len(c.title)})")
        if not c.steps:
            errors.append(f"{tag}: пустой текст")
        if len(c.steps) > STEPS_MAX:
            errors.append(f"{tag}: больше {STEPS_MAX} пунктов ({len(c.steps)})")
        if len(c.text) > TEXT_MAX:
            errors.append(f"{tag}: текст длиннее {TEXT_MAX} знаков ({len(c.text)})")
        if not c.version:
            errors.append(f"{tag}: не указана версия")
        if c.published:
            if c.type == "checklist" and not c.result:
                errors.append(f"{tag}: для чек-листа обязателен ожидаемый результат (result)")
            if c.type == "checklist" and not c.when:
                warnings.append(f"{tag}: у чек-листа не указано, когда применять (when)")
            if not c.help:
                warnings.append(f"{tag}: не указано, к кому обратиться (help)")
            if not c.checked:
                warnings.append(f"{tag}: нет даты проверки (checked)")
            if not c.owner or c.owner.lower().startswith("не назнач"):
                warnings.append(f"{tag}: не назначен ответственный (owner)")
            if not c.reviewer or c.reviewer.lower().startswith("не назнач"):
                warnings.append(f"{tag}: не назначен проверяющий (reviewer)")
            if not (c.source.doc or c.source.url or c.source.link_key):
                warnings.append(f"{tag}: не указан источник")
            m = SECRET_RE.search(c.title + " " + c.text)
            if m:
                warnings.append(f"{tag}: встречается слово «{m.group(1)}» — проверьте, что в тексте нет секретов")
    for cid, n in published_ids.items():
        if n > 1:
            errors.append(f"{cid}: опубликовано {n} версий, должна быть одна")
    return errors, warnings


def load_content(content_dir: Path, strict: bool = True) -> Content:
    cards_doc = _read_yaml(content_dir / "cards.yaml")
    screens_doc = _read_yaml(content_dir / "screens.yaml")
    help_doc = _read_yaml(content_dir / "help_routes.yaml")
    params = _read_yaml(content_dir / "params.yaml")

    types = [Named(str(t["id"]), str(t["title"])) for t in cards_doc.get("types", [])]
    groups = [Named(str(g["id"]), str(g["title"])) for g in cards_doc.get("groups", [])]
    if not types or not groups:
        raise ContentError("cards.yaml: нужны списки types и groups")
    all_cards = [_card_from_raw(raw) for raw in cards_doc.get("cards", [])]

    errors, warnings = validate_cards(all_cards, [g.id for g in groups])
    for w in warnings:
        log.warning("content: %s", w)
    if errors:
        msg = "Ошибки содержания:\n  " + "\n  ".join(errors)
        if strict:
            raise ContentError(msg)
        log.error(msg)

    all_versions: Dict[str, List[Card]] = {}
    for c in all_cards:
        all_versions.setdefault(c.id, []).append(c)
    published = {c.id: c for c in all_cards if c.published}
    if not published:
        log.warning("content: нет ни одного опубликованного материала")

    routes_raw = help_doc.get("routes", {}) or {}
    help_routes = [HelpRoute(key=str(k), button=str(v.get("button", k)), text=str(v.get("text", "")))
                   for k, v in routes_raw.items()]
    if not help_routes:
        raise ContentError("help_routes.yaml: нет ни одного маршрута помощи")
    params = dict(params)
    params["contacts"] = help_doc.get("contacts", {}) or {}

    texts = {str(k): str(v) for k, v in (screens_doc.get("texts") or {}).items()}
    labels = {str(k): str(v) for k, v in (screens_doc.get("labels") or {}).items()}
    required = ["S0", "S1", "S2", "S2_groups", "S3_when", "S3_prepare", "S3_result", "S3_help", "S3_more",
                "S3a", "S4", "S5", "S5a", "S5b", "S6", "S7", "S7_attachment", "S7_action", "S7_thanks", "S8"]
    missing = [t for t in required if t not in texts]
    if missing:
        raise ContentError(f"screens.yaml: нет текстов {missing}")

    return Content(
        types=types, groups=groups, cards=published, all_versions=all_versions,
        texts=texts, labels=labels,
        thanks_words=[str(w).lower() for w in screens_doc.get("thanks_words", [])],
        action_words=[str(w).lower() for w in screens_doc.get("action_words", [])],
        help_routes=help_routes, params=params, warnings=warnings,
    )
