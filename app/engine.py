"""Диалоговый движок (MVP v11): меню -> раздел (тип материала) -> тема -> материал; оценка; помощь.

Не зависит от мессенджера: на вход Incoming, на выход Reply с текстами и кнопками.
Callback-данные кнопок:
    menu | search | help | help:<pi|tech|org>
    type:<answer|instruction|checklist>            список тем или разделов типа
    type:<tid>:<group>:<page>                      список тем раздела внутри типа
    card:<ID> | ok:<ID> | no:<ID>
Правила v11: бот показывает только опубликованные материалы; повтор темы в сеансе — то же обращение;
последняя оценка — итоговая; свободный текст не разбирается по смыслу, а записывается для ответственного.
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from . import journal as J
from .config import Settings
from .content import Card, Content
from .models import Button, Incoming, Message, Reply
from .search import is_search_query, normalize, search
from .sessions import Session, SessionStore

log = logging.getLogger(__name__)

TOPICS_PER_PAGE = 8
MENU_COMMANDS = {"/start", "start", "старт", "/menu", "меню", "menu", "начать"}


class Engine:
    def __init__(self, content: Content, journal: J.Journal, sessions: SessionStore, settings: Settings):
        self.content = content
        self.journal = journal
        self.sessions = sessions
        self.settings = settings

    # ------------------------------------------------------------------ public
    def handle(self, inc: Incoming) -> Reply:
        session, is_new = self.sessions.get_or_create(inc.user_id)
        uh = self._user_ref(inc.user_id)
        reply = Reply()
        if is_new:
            self.journal.log(J.SESSION_START, session.id, uh)
            reply.messages.append(Message(self.t("S0")))
            reply.screen = "S0"
        try:
            action = self._resolve_action(session, inc)
            if action is None:
                action = "menu"  # открытие бота = главное меню (v11 «Логика ответов»)
            self._dispatch(action, session, uh, reply)
        except Exception:  # noqa: BLE001 — любой сбой выдачи -> S8, ПИ не останавливается
            log.exception("material error, session %s", session.id)
            self.journal.log(J.MATERIAL_ERROR, session.id, uh, card_id=session.card_id)
            reply.messages.append(self._s8(session))
            reply.screen = "S8"
        self._remember(session, reply)
        return reply

    # ------------------------------------------------------------- resolution
    def _resolve_action(self, session: Session, inc: Incoming) -> Optional[str]:
        if inc.callback:
            return inc.callback.strip()
        if inc.attachment_type and not (inc.text or "").strip():
            return f"attachment:{inc.attachment_type}"
        text = (inc.text or "").strip()
        if not text:
            return None
        low = normalize(text)
        if low in MENU_COMMANDS:
            return "menu"
        if re.fullmatch(r"\d{1,2}", low):  # ответ номером кнопки
            idx = int(low) - 1
            if 0 <= idx < len(session.last_buttons):
                return session.last_buttons[idx].data
        for b in session.last_buttons:  # ответ подписью кнопки
            if normalize(b.label) == low:
                return b.data
        for key, label in self.content.labels.items():
            if normalize(label) == low:
                return {"search": "search", "help": "help"}.get(key, "menu")
        if low in self.content.thanks_words:
            return "thanks"
        if self.settings.search_enabled and (session.awaiting_search or is_search_query(text)):
            return f"query:{text}"
        return f"free:{text}"

    def _dispatch(self, action: str, s: Session, uh: str, reply: Reply) -> None:
        parts = action.split(":")
        kind = parts[0]
        s.awaiting_search = False

        if kind in ("menu", "back"):
            self._goto_menu(s, reply)
        elif kind == "type" and len(parts) >= 2:
            group = parts[2] if len(parts) > 2 and parts[2] else None
            page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
            self._show_type(s, parts[1], group, page, reply)
        elif kind == "card" and len(parts) >= 2:
            self._show_card(s, parts[1], uh, reply)
        elif kind in ("ok", "no") and len(parts) >= 2:
            self._rate(s, parts[1], "helped" if kind == "ok" else "not_helped", uh, reply)
        elif kind == "search" and self.settings.search_enabled:
            s.screen, s.awaiting_search = "S5", True
            reply.messages.append(Message(self.t("S5"), [[self.btn("menu", "menu")]]))
            reply.screen = "S5"
        elif kind == "query":
            self._search(s, action.split(":", 1)[1], uh, reply)
        elif kind == "help":
            if len(parts) >= 2:
                self._help_route(s, parts[1], uh, reply)
            else:
                self._help_menu(s, reply)
        elif kind == "attachment":
            self._unrecognized(s, uh, reply, attachment=parts[1] if len(parts) > 1 else "file")
        elif kind == "thanks":
            self.journal.log(J.THANKS, s.id, uh)
            s.screen = "S7"
            reply.messages.append(Message(self.t("S7_thanks"), [[self.btn("menu", "menu")]]))
            reply.screen = "S7"
        elif kind == "free":
            self._unrecognized(s, uh, reply, phrase=action.split(":", 1)[1])
        else:
            self._goto_menu(s, reply)  # неизвестная/устаревшая кнопка

    # ---------------------------------------------------------------- screens
    def _goto_menu(self, s: Session, reply: Reply) -> None:
        s.screen, s.type, s.group, s.page, s.card_id = "S1", None, None, 0, None
        rows: List[List[Button]] = [[Button(t.title, f"type:{t.id}")] for t in self.content.types]
        last = [self.btn("help", "help")]
        if self.settings.search_enabled:
            last.insert(0, self.btn("search", "search"))
        rows.append(last)
        reply.messages.append(Message(self.t("S1"), rows))
        reply.screen = "S1"

    def _show_type(self, s: Session, tid: str, group: Optional[str], page: int, reply: Reply) -> None:
        title = self.content.type_title(tid)
        if title is None:
            self._goto_menu(s, reply)
            return
        cards = self.content.cards_of_type(tid)
        groups = self.content.groups_of_type(tid)
        # Больше одной страницы тем -> сначала разделы, чтобы до любой темы было <= 3 нажатий
        if group is None and len(cards) > TOPICS_PER_PAGE and len(groups) > 1:
            s.screen, s.type, s.group, s.page, s.card_id = "S2", tid, None, 0, None
            rows = [[Button(g.title, f"type:{tid}:{g.id}:0")] for g in groups]
            rows.append([self.btn("back", "menu")])
            reply.messages.append(Message(self.t("S2_groups", section=title), rows))
            reply.screen = "S2"
            return
        if group is not None:
            cards = [c for c in cards if c.group == group]
            gtitle = self.content.group_title(group)
            title = f"{title} — {gtitle}" if gtitle else title
        pages = max(1, (len(cards) + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        s.screen, s.type, s.group, s.page, s.card_id = "S2", tid, group, page, None
        chunk = cards[page * TOPICS_PER_PAGE:(page + 1) * TOPICS_PER_PAGE]
        rows = [[Button(c.title, f"card:{c.id}")] for c in chunk]
        nav: List[Button] = []
        if pages > 1:
            nav.append(self.btn("more_topics", f"type:{tid}:{group or ''}:{(page + 1) % pages}"))
        nav.append(self.btn("back", f"type:{tid}" if group is not None and len(groups) > 1 else "menu"))
        nav.append(self.btn("menu", "menu"))
        rows.append(nav)
        reply.messages.append(Message(self.t("S2", section=title), rows))
        reply.screen = "S2"

    def _show_card(self, s: Session, cid: str, uh: str, reply: Reply) -> None:
        card = self.content.cards.get(cid)
        if card is None:  # черновик, снятая версия или неизвестный id — материал не выдаём
            raise KeyError(f"card {cid} is not published")
        s.screen, s.type, s.group, s.card_id = "S3", card.type, card.group, cid
        self.journal.log(J.CARD_VIEW, s.id, uh, card_id=cid, card_version=card.version,
                         extra={"type": card.type})
        reply.messages.append(Message(self.render_card(card), self._card_buttons(card)))
        reply.screen = "S3"

    def _card_buttons(self, card: Card) -> List[List[Button]]:
        return [
            [self.btn("helped", f"ok:{card.id}"), self.btn("not_helped", f"no:{card.id}")],
            [self.btn("other_topics", self._topics_data(card)), self.btn("menu", "menu")],
        ]

    def _topics_data(self, card: Card) -> str:
        cards = self.content.cards_of_type(card.type)
        if len(cards) > TOPICS_PER_PAGE and len(self.content.groups_of_type(card.type)) > 1:
            return f"type:{card.type}:{card.group}:0"
        return f"type:{card.type}"

    def render_card(self, card: Card) -> str:
        lines = [card.title]
        if card.type == "checklist":
            if card.when:
                lines.append(self.t("S3_when", when=card.when))
            if card.prepare:
                lines.append(self.t("S3_prepare", prepare=card.prepare))
        steps = self.content.render_steps(card)
        if len(steps) == 1:
            lines.append(steps[0])
        else:
            lines.extend(f"{i}. {step}" for i, step in enumerate(steps, 1))
        if card.type == "checklist" and card.result:
            lines.append(self.t("S3_result", result=card.result))
        if card.help:
            lines.append(self.t("S3_help", help=card.help))
        link = self.content.link_for(card)
        if link:
            src = " — ".join(x for x in (card.source.doc, card.source.section) if x)
            lines.append(self.t("S3_more", link=f"{src} — {link}" if src else link))
        return "\n".join(lines)

    def _rate(self, s: Session, cid: str, rating: str, uh: str, reply: Reply) -> None:
        card = self.content.cards.get(cid)
        s.card_id = cid
        # каждое нажатие пишется в журнал; в метриках итоговой считается последняя оценка обращения
        self.journal.log(J.RATING, s.id, uh, card_id=cid, card_version=card.version if card else None, rating=rating)
        if rating == "helped":
            s.screen = "S3a"
            reply.messages.append(Message(self.t("S3a"), [[self.btn("menu", "menu")]]))
            reply.screen = "S3a"
        else:
            s.screen = "S4"
            topics = self._topics_data(card) if card else "menu"
            rows = [[self.btn("other_topics", topics), self.btn("help", "help")], [self.btn("menu", "menu")]]
            reply.messages.append(Message(self.t("S4"), rows))
            reply.screen = "S4"

    def _search(self, s: Session, query: str, uh: str, reply: Reply) -> None:
        q = query.strip()
        results = search(self.content, q)
        if results:
            s.screen = "S5a"
            self.journal.log(J.SEARCH, s.id, uh, query=self._safe_text(q), extra={"results": [c.id for c in results]})
            rows = [[Button(c.title, f"card:{c.id}")] for c in results]
            rows.append([self.btn("new_search", "search"), self.btn("menu", "menu")])
            reply.messages.append(Message(self.t("S5a", query=q), rows))
            reply.screen = "S5a"
        else:
            s.screen = "S5b"
            self.journal.log(J.SEARCH_MISS, s.id, uh, query=self._safe_text(q))
            reply.messages.append(Message(self.t("S5b", query=q), [[self.btn("menu", "menu"), self.btn("help", "help")]]))
            reply.screen = "S5b"

    def _help_menu(self, s: Session, reply: Reply) -> None:
        s.screen = "S6"
        rows = [[Button(r.button, f"help:{r.key}")] for r in self.content.help_routes]
        rows.append([self.btn("menu", "menu")])
        reply.messages.append(Message(self.t("S6"), rows))
        reply.screen = "S6"

    def _help_route(self, s: Session, key: str, uh: str, reply: Reply) -> None:
        text = self.content.help_text(key)
        if text is None:
            self._help_menu(s, reply)
            return
        s.screen = "S6a"
        # card_id заполнен, если помощь запрошена после темы (в т.ч. после «Не помогло»); иначе — помощь без темы
        self.journal.log(J.HELP_REQUEST, s.id, uh, help_type=key, card_id=s.card_id)
        reply.messages.append(Message(text, [[self.btn("menu", "menu")]]))
        reply.screen = "S6a"

    def _unrecognized(self, s: Session, uh: str, reply: Reply, phrase: Optional[str] = None,
                      attachment: Optional[str] = None) -> None:
        s.screen = "S7"
        if attachment:
            self.journal.log(J.ATTACHMENT, s.id, uh, extra={"type": attachment})
            text = self.t("S7_attachment")
        else:
            # вопрос без ответа — попадает в журнал для ответственного за содержание (канал сбора вопросов)
            self.journal.log(J.QUESTION, s.id, uh, query=self._safe_text(phrase or ""), card_id=s.card_id)
            text = self.t("S7")
            if any(w in normalize(phrase or "") for w in self.content.action_words):
                text = self.t("S7_action") + "\n" + text
        rows = [[self.btn("menu", "menu"), self.btn("help", "help")]]
        reply.messages.append(Message(text, rows))
        reply.screen = "S7"

    def _s8(self, s: Session) -> Message:
        card = self.content.cards.get(s.card_id) if s.card_id else None
        s.screen = "S8"
        return Message(self.t("S8", link=self.content.link_for(card)),
                       [[self.btn("help", "help"), self.btn("menu", "menu")]])

    # ---------------------------------------------------------------- helpers
    def t(self, key: str, **kw: object) -> str:
        text = self.content.texts[key]
        for k, v in kw.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def btn(self, label_key: str, data: str) -> Button:
        return Button(self.content.labels.get(label_key, label_key), data)

    def _user_ref(self, user_id: str) -> str:
        """Идентификатор участника в журнале: как есть (для ручной привязки к ПИ) или хеш."""
        if self.settings.journal_user_mode == "hash":
            return J.user_hash(user_id, self.settings.journal_salt)
        return user_id

    def _safe_text(self, text: str) -> Optional[str]:
        if not self.settings.log_free_text:
            return None
        return text[: self.settings.free_text_max_len]

    @staticmethod
    def _remember(session: Session, reply: Reply) -> None:
        if reply.messages:
            session.last_buttons = reply.messages[-1].flat_buttons()
