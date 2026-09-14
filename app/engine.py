"""Диалоговый движок: экраны S0–S8 и переходы между ними (лист «Экраны и кнопки»).

Не зависит от мессенджера: на вход Incoming, на выход Reply с текстами и кнопками.
Callback-данные кнопок:
    menu | back | search | help | help:<pi|tech|org>
    sec:<A-E>:<page> | card:<ID> | ok:<ID> | no:<ID> | why:<notfound|details|failed>:<ID>
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
REASONS = {"notfound": "why_notfound", "details": "why_details", "failed": "why_failed"}
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
        uh = J.user_hash(inc.user_id, self.settings.journal_salt)
        reply = Reply()
        if is_new:
            self.journal.log(J.SESSION_START, session.id, uh)
            reply.messages.append(self._s0())
            reply.screen = "S0"

        try:
            action = self._resolve_action(session, inc)
            if action is None:
                if is_new:
                    # первое открытие чата без ввода — достаточно приветствия
                    self._remember(session, reply)
                    return reply
                action = "menu"
            self._dispatch(action, session, inc, uh, reply)
        except Exception:  # noqa: BLE001 — любой сбой выдачи -> S8, ПИ не останавливается
            log.exception("material error for user session %s", session.id)
            self.journal.log(J.MATERIAL_ERROR, session.id, uh, card_id=session.card_id)
            reply.messages.append(self._s8(session))
            reply.screen = "S8"
        self._remember(session, reply)
        return reply

    # ------------------------------------------------------------- resolution
    def _resolve_action(self, session: Session, inc: Incoming) -> Optional[str]:
        """Превращает вход (кнопка / текст / вложение) в действие движка."""
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
        # ответ номером кнопки: «2» -> вторая из последних показанных кнопок
        if re.fullmatch(r"\d{1,2}", low):
            idx = int(low) - 1
            if 0 <= idx < len(session.last_buttons):
                return session.last_buttons[idx].data
        # ответ подписью кнопки
        for b in session.last_buttons:
            if normalize(b.label) == low:
                return b.data
        for key, label in self.content.labels.items():
            if normalize(label) == low:
                return {"menu": "menu", "search": "search", "help": "help", "back": "menu"}.get(key, "menu")
        if low in self.content.thanks_words:
            return "thanks"
        if session.awaiting_search or is_search_query(text):
            return f"query:{text}"
        return f"free:{text}"

    def _dispatch(self, action: str, s: Session, inc: Incoming, uh: str, reply: Reply) -> None:
        parts = action.split(":", 2)
        kind = parts[0]
        s.awaiting_search = False

        if kind in ("menu", "back"):
            self._goto_menu(s, reply)
        elif kind == "sec" and len(parts) >= 2:
            page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
            self._show_section(s, parts[1], page, reply)
        elif kind == "card" and len(parts) >= 2:
            self._show_card(s, parts[1], uh, reply)
        elif kind == "ok" and len(parts) >= 2:
            self._rate(s, parts[1], "helped", uh, reply)
        elif kind == "no" and len(parts) >= 2:
            self._rate(s, parts[1], "not_helped", uh, reply)
        elif kind == "why" and len(parts) >= 3:
            self._clarify(s, parts[2], parts[1], uh, reply)
        elif kind == "search":
            s.screen = "S5"
            s.awaiting_search = True
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
            # неизвестный callback (устаревшая кнопка) — возвращаем в меню
            self._goto_menu(s, reply)

    # ---------------------------------------------------------------- screens
    def _s0(self) -> Message:
        return Message(self.t("S0"), [[self.btn("menu", "menu")]])

    def _goto_menu(self, s: Session, reply: Reply) -> None:
        s.screen, s.section, s.page, s.card_id = "S1", None, 0, None
        rows: List[List[Button]] = [[Button(sec.title.split(". ", 1)[-1], f"sec:{sec.id}:0")] for sec in self.content.sections]
        rows.append([self.btn("search", "search"), self.btn("help", "help")])
        reply.messages.append(Message(self.t("S1"), rows))
        reply.screen = "S1"

    def _show_section(self, s: Session, sid: str, page: int, reply: Reply) -> None:
        section = self.content.section(sid)
        if section is None:
            self._goto_menu(s, reply)
            return
        pages = max(1, (len(section.cards) + TOPICS_PER_PAGE - 1) // TOPICS_PER_PAGE)
        page = max(0, min(page, pages - 1))
        s.screen, s.section, s.page, s.card_id = "S2", sid, page, None
        chunk = section.cards[page * TOPICS_PER_PAGE:(page + 1) * TOPICS_PER_PAGE]
        rows = [[Button(c.title, f"card:{c.id}")] for c in chunk]
        nav: List[Button] = []
        if pages > 1:
            nav.append(self.btn("more_topics", f"sec:{sid}:{(page + 1) % pages}"))
        nav.append(self.btn("back", "menu"))
        rows.append(nav)
        title = section.title.split(". ", 1)[-1]
        reply.messages.append(Message(self.t("S2", section=title), rows))
        reply.screen = "S2"

    def _show_card(self, s: Session, cid: str, uh: str, reply: Reply) -> None:
        card = self.content.cards.get(cid)
        if card is None:
            raise KeyError(f"card {cid} not found")
        s.screen, s.section, s.card_id = "S3", card.section, cid
        self.journal.log(J.CARD_VIEW, s.id, uh, card_id=cid, card_version=card.version)
        reply.messages.append(Message(self.render_card(card), self._card_buttons(card)))
        reply.screen = "S3"

    def _card_buttons(self, card: Card) -> List[List[Button]]:
        return [
            [self.btn("helped", f"ok:{card.id}"), self.btn("not_helped", f"no:{card.id}")],
            [self.btn("other_topics", f"sec:{card.section}:0"), self.btn("menu", "menu")],
        ]

    def render_card(self, card: Card) -> str:
        steps = self.content.render_steps(card)
        lines = [card.title]
        if len(steps) == 1:
            lines.append(steps[0])
        else:
            lines.extend(f"{i}. {step}" for i, step in enumerate(steps, 1))
        link = self.content.link_for(card)
        if link:
            src = f"{card.source} — {link}" if card.source else link
            lines.append(self.t("S3_more", link=src))
        return "\n".join(lines)

    def _rate(self, s: Session, cid: str, rating: str, uh: str, reply: Reply) -> None:
        card = self.content.cards.get(cid)
        s.card_id = cid
        self.journal.log(J.RATING, s.id, uh, card_id=cid, card_version=card.version if card else None, rating=rating)
        if rating == "helped":
            s.screen = "S3a"
            reply.messages.append(Message(self.t("S3a"), [[self.btn("menu", "menu")]]))
            reply.screen = "S3a"
        else:
            s.screen = "S4"
            rows = [[self.btn(label_key, f"why:{reason}:{cid}")] for reason, label_key in REASONS.items()]
            reply.messages.append(Message(self.t("S4"), rows))
            reply.screen = "S4"

    def _clarify(self, s: Session, cid: str, reason: str, uh: str, reply: Reply) -> None:
        card = self.content.cards.get(cid)
        if reason not in REASONS:
            reason = "notfound"
        s.screen, s.card_id = "S4a", cid
        self.journal.log(J.CLARIFY, s.id, uh, card_id=cid, card_version=card.version if card else None, reason=reason)
        link = self.content.link_for(card)
        sec = card.section if card else None
        rows = [[self.btn("help", "help")]]
        rows.append([self.btn("other_topics", f"sec:{sec}:0")] if sec else [])
        rows[-1].append(self.btn("menu", "menu"))
        reply.messages.append(Message(self.t("S4a", link=link), rows))
        reply.screen = "S4a"

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
            self.journal.log(J.UNRECOGNIZED, s.id, uh, query=self._safe_text(phrase or ""))
            text = self.t("S7")
            low = normalize(phrase or "")
            if any(w in low for w in self.content.action_words):
                text = self.t("S7_action") + "\n" + text
        reply.messages.append(Message(text, [[self.btn("menu", "menu"), self.btn("search", "search")]]))
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

    def _safe_text(self, text: str) -> Optional[str]:
        if not self.settings.log_free_text:
            return None
        return text[: self.settings.free_text_max_len]

    @staticmethod
    def _remember(session: Session, reply: Reply) -> None:
        if reply.messages:
            session.last_buttons = reply.messages[-1].flat_buttons()
