import hashlib
import hmac
import json
import re
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .content import normalized

WELCOME = (
    "Я справочник Аватара. Помогаю с подготовкой к ПИ, связью и сбоями, "
    "правилами съёмки и действиями после ПИ. По самим этапам вас ведёт ревизор голосом. "
    "Нажмите «Меню» — или напишите одно слово, например «модем»."
)
MENU = {"label": "Меню", "action": "menu"}
HELP = {"label": "Помощь человека", "action": "help"}
SEARCH = {"label": "Поиск", "action": "search"}
REASONS = {"wrong": "Не то, что искал", "details": "Не хватает деталей", "failed": "Сделал, не сработало"}


def screen(code, text, buttons=None, **fields):
    return dict(screen=code, text=text, buttons=buttons or [MENU], **fields)


def validate_event(e):
    if not isinstance(e, dict) or set(e) - {
        "event_id",
        "conversation_id",
        "user_id",
        "type",
        "text",
        "action",
        "attachment_type",
    }:
        raise ValueError("Unknown event fields")
    for key in ("event_id", "conversation_id", "user_id"):
        if not isinstance(e.get(key), str) or not 1 <= len(e[key]) <= 128:
            raise ValueError("Invalid " + key)
    if e.get("type") not in ("opened", "closed", "message", "action", "attachment"):
        raise ValueError("Invalid event type")
    for key, limit in [("text", 500), ("action", 128), ("attachment_type", 32)]:
        if key in e and (not isinstance(e[key], str) or len(e[key]) > limit):
            raise ValueError("Invalid " + key)
    if e["type"] == "message" and not e.get("text", "").strip():
        raise ValueError("Empty text")
    if e["type"] == "action" and not e.get("action", ""):
        raise ValueError("Empty action")


class Engine:
    def __init__(self, catalog, db_path, secret, session_ttl=14400, retention_days=30):
        self.catalog, self.db_path, self.secret = catalog, str(db_path), secret.encode()
        self.session_ttl, self.retention = session_ttl, retention_days * 86400
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if tables and version not in (2, 3):
                raise ValueError("Legacy database detected. Use a new DB_PATH; retain the old journal separately.")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions (
                    sid TEXT PRIMARY KEY, last_seen REAL NOT NULL, participant TEXT);
                CREATE TABLE IF NOT EXISTS dedup (
                    eid TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, response TEXT NOT NULL,
                    created REAL NOT NULL, sent_count INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS views (
                    id TEXT PRIMARY KEY, sid TEXT NOT NULL, card TEXT NOT NULL, version TEXT NOT NULL,
                    section TEXT NOT NULL, url TEXT NOT NULL, created REAL NOT NULL,
                    rating TEXT, reason TEXT);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY, time REAL NOT NULL, kind TEXT NOT NULL,
                    topic TEXT, version TEXT, value TEXT, delivery TEXT, delivered INTEGER NOT NULL DEFAULT 1,
                    interaction TEXT, participant TEXT);
                CREATE INDEX IF NOT EXISTS events_time ON events(time);
                CREATE INDEX IF NOT EXISTS views_sid ON views(sid);
                CREATE INDEX IF NOT EXISTS views_created ON views(created);
                CREATE INDEX IF NOT EXISTS dedup_created ON dedup(created);
                CREATE INDEX IF NOT EXISTS sessions_seen ON sessions(last_seen);
            """)
            if tables and version == 2:  # v2 -> v3: псевдоним участника
                db.execute("ALTER TABLE events ADD COLUMN participant TEXT")
                db.execute("ALTER TABLE sessions ADD COLUMN participant TEXT")
            db.execute("PRAGMA user_version=3")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.db_path, timeout=1)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def digest(self, value):
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def log(self, db, now, kind, topic=None, version=None, value=None, interaction=None, participant=None):
        db.execute(
            "INSERT INTO events(time,kind,topic,version,value,interaction,participant) VALUES(?,?,?,?,?,?,?)",
            (now, kind, topic, version, value, interaction, participant),
        )

    def participant(self, user_id):
        """Стабильный псевдоним участника: HMAC от id пользователя, необратим, одинаков между сеансами."""
        return self.digest("participant:" + user_id)[:10]

    def cleanup(self, db, now):
        stale = list(db.execute("SELECT * FROM sessions WHERE last_seen < ?", (now - self.session_ttl,)))
        for session in stale:
            db.execute("DELETE FROM views WHERE sid=?", (session["sid"],))
            self.log(db, session["last_seen"] + self.session_ttl, "session_end", value="timeout",
                     participant=session["participant"])
        db.execute("DELETE FROM sessions WHERE last_seen < ?", (now - self.session_ttl,))
        db.execute("DELETE FROM views WHERE created < ?", (now - self.session_ttl,))
        db.execute("DELETE FROM dedup WHERE created < ?", (now - 86400,))
        db.execute("DELETE FROM events WHERE time < ?", (now - self.retention,))

    def handle(self, event, now=None, deferred=False, enqueue=None):
        validate_event(event)
        now = time.time() if now is None else now
        sid = self.digest(json.dumps([event["conversation_id"], event["user_id"]]))
        pid = self.participant(event["user_id"])
        eid = self.digest(json.dumps([sid, event["event_id"]]))
        fingerprint = self.digest(json.dumps(event, sort_keys=True, ensure_ascii=False))
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.cleanup(db, now)
            event_start = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
            old = db.execute("SELECT * FROM dedup WHERE eid=?", (eid,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise ValueError("Event ID reused with a different payload")
                result = json.loads(old["response"])
                if enqueue and not old["completed"]:
                    enqueue(db, result)
                return result
            active = db.execute("SELECT * FROM sessions WHERE sid=?", (sid,)).fetchone()
            if event["type"] == "closed":
                if active:
                    self.log(db, now, "session_end", value="closed")
                    db.execute("DELETE FROM sessions WHERE sid=?", (sid,))
                    db.execute("DELETE FROM views WHERE sid=?", (sid,))
                messages = []
            else:
                db.execute("INSERT OR REPLACE INTO sessions VALUES(?,?,?)", (sid, now, pid))
                if not active:
                    self.log(db, now, "session_start")
                if event["type"] == "opened":
                    messages = [] if active else [screen("S0", WELCOME)]
                else:
                    messages = ([] if active else [screen("S0", WELCOME)]) + [self.respond(db, sid, event, now)]
            # A new delivery attempt for a still-undelivered appeal must confirm the same
            # appeal, rather than leaving its metric bound to a failed earlier request.
            if deferred:
                for message in messages:
                    if message.get("card_id"):
                        db.execute(
                            "UPDATE events SET delivery=? WHERE kind='card' AND delivered=0 "
                            "AND interaction IN (SELECT id FROM views WHERE sid=? AND card=? AND version=?)",
                            (eid, sid, message["card_id"], message["version"]),
                        )
            db.execute("UPDATE events SET participant=? WHERE id>? AND participant IS NULL", (pid, event_start))
            # Responses never echo raw user text, so replay cache contains no free-form input.
            result = {"messages": messages, "reply_id": eid}
            if deferred:
                db.execute("UPDATE events SET delivery=?,delivered=0 WHERE id>? AND kind='card'", (eid, event_start))
            db.execute(
                "INSERT INTO dedup(eid,fingerprint,response,created) VALUES(?,?,?,?)",
                (eid, fingerprint, json.dumps(result, ensure_ascii=False), now),
            )
            if enqueue:
                enqueue(db, result)
            return result

    def menu(self):
        return screen(
            "S1",
            "Что нужно?",
            [{"label": name, "action": "section:" + key + ":0"} for key, name in self.catalog.settings["sections"].items()]
            + [SEARCH, HELP],
        )

    def section(self, section, page):
        if section not in self.catalog.settings["sections"] or page not in (0, 1):
            return self.unknown()
        cards = [c for c in self.catalog.cards.values() if c["section"] == section]
        chunk = cards[page * 8 : page * 8 + 8]
        if not chunk:
            return self.unknown()
        buttons = [{"label": c["title"], "action": "card:" + c["id"]} for c in chunk]
        if len(cards) > 8:
            buttons.append({"label": "Ещё темы" if page == 0 else "Первые темы", "action": f"section:{section}:{1 - page}"})
        return screen("S2", self.catalog.settings["sections"][section] + ". Выберите тему:", buttons + [MENU])

    def unknown(self):
        return screen(
            "S7",
            "Я отвечаю по темам из меню и ищу по отдельным словам. Целиком вопрос понять не смогу. "
            "Действий в GK и Inventa бот не выполняет — обратитесь к ревизору.",
            [MENU, SEARCH],
        )

    def unavailable(self, db, now, cid):
        self.log(db, now, "material_error", topic=cid if cid in self.catalog.cards else None)
        url = self.catalog.settings["fallback_url"]
        text = "Не могу открыть материал. "
        text += "Полная инструкция: " + url if url else "Ссылка на инструкцию ещё не настроена."
        return screen("S8", text, [HELP, MENU])

    def respond(self, db, sid, e, now):
        if e["type"] == "attachment":
            kind = e.get("attachment_type")
            self.log(db, now, "attachment", value=kind if kind in ("audio", "image", "file", "video") else "other")
            return screen("S7", "Работаю с меню и текстовыми словами. Голосовые сообщения, фото и файлы не обрабатываю.")
        if e["type"] == "message":
            text = normalized(e["text"])
            if text in ("меню", "/menu", "/start"):
                return self.menu()
            if text in ("спасибо", "ок", "спс", "благодарю"):
                return screen("S7", "Пожалуйста. Если понадобится что-то ещё — «Меню».")
            if text in ("поиск", "/search"):
                return screen("S5", "Напишите одно-два слова: модем, пароль, зонирование, наушники, ЭЦП.")
            if text in ("помощь", "/help"):
                return self.help_menu()
            if len(text.split()) > 2 or not re.fullmatch(r"[а-яa-z -]{1,60}", text):
                self.log(db, now, "unknown_input", value="redacted")
                return self.unknown()
            matches = self.catalog.search(text)
            self.log(db, now, "search" if matches else "search_miss", value="matched" if matches else "redacted")
            if not matches:
                return screen(
                    "S5b",
                    "Ничего не нашёл. Выберите раздел в меню или запросите помощь. "
                    "Отсутствие ответа отмечено; текст запроса не сохраняется.",
                    [MENU, HELP],
                )
            return screen(
                "S5a",
                "Найдены темы:",
                [{"label": c["title"], "action": "card:" + c["id"]} for c in matches]
                + [{"label": "Другой запрос", "action": "search"}, MENU],
            )
        a = e.get("action", "")
        if a == "menu":
            return self.menu()
        if a == "search":
            return screen("S5", "Напишите одно-два слова: модем, пароль, зонирование, наушники, ЭЦП.")
        if a == "help":
            return self.help_menu()
        if a.startswith("help:"):
            key = a[5:]
            route = self.catalog.settings["help_routes"].get(key)
            if not route:
                return self.unknown()
            self.log(db, now, "help", value=key)
            return screen("S6a", route["text"] or "Контакт ещё не настроен. Свяжитесь с ревизором по обычному рабочему каналу.")
        match = re.fullmatch(r"section:([A-E]):([01])", a)
        if match:
            return self.section(match[1], int(match[2]))
        if a.startswith("card:"):
            cid = a[5:]
            card = self.catalog.cards.get(cid)
            if not card or not card["available"]:
                return self.unavailable(db, now, cid)
            old_view = db.execute(
                "SELECT * FROM views WHERE sid=? AND card=? AND version=?", (sid, cid, card["version"])
            ).fetchone()
            view = old_view["id"] if old_view else uuid.uuid4().hex
            if not old_view:
                db.execute(
                    "INSERT INTO views(id,sid,card,version,section,url,created) VALUES(?,?,?,?,?,?,?)",
                    (view, sid, cid, card["version"], card["section"], card["url"], now),
                )
                self.log(db, now, "card", cid, card["version"], interaction=view)
            body = card["title"] + "\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(card["steps"], 1))
            body += "\nПодробнее: " + (card["url"] or "ссылка ещё не настроена")
            if not card["approved"]:
                body = "ЧЕРНОВИК ДЛЯ ПРОВЕРКИ\n" + body
            return screen(
                "S3",
                body,
                [
                    {"label": "Помогло", "action": "rate:" + view + ":yes"},
                    {"label": "Не помогло", "action": "rate:" + view + ":no"},
                    {"label": "Другие темы", "action": "section:" + card["section"] + ":0"},
                    MENU,
                ],
                card_id=cid,
                version=card["version"],
            )
        match = re.fullmatch(r"(rate|reason):([0-9a-f]{32}):(yes|no|wrong|details|failed)", a)
        if match:
            kind, view, value = match.groups()
            row = db.execute("SELECT * FROM views WHERE id=? AND sid=?", (view, sid)).fetchone()
            if not row:
                return screen("S7", "Эта карточка из другого или завершённого сеанса. Откройте тему заново.")
            if kind == "rate" and value in ("yes", "no"):
                if row["rating"] is None:
                    db.execute("UPDATE views SET rating=? WHERE id=?", (value, view))
                    self.log(db, now, "rating", row["card"], row["version"], value, interaction=view)
                elif row["rating"] != value:
                    return screen("S3a", "Оценка этой карточки уже принята.")
                if value == "yes":
                    return screen("S3a", "Понял, спасибо. Если понадобится что-то ещё — «Меню».")
                if row["reason"] is not None:
                    return screen("S3a", "Оценка и причина уже приняты.")
                return screen(
                    "S4",
                    "Что не так?",
                    [{"label": label, "action": f"reason:{view}:{key}"} for key, label in REASONS.items()] + [MENU],
                )
            if kind == "reason" and value in REASONS and row["rating"] == "no":
                if row["reason"] is None:
                    db.execute("UPDATE views SET reason=? WHERE id=?", (value, view))
                    self.log(db, now, "reason", row["card"], row["version"], value, interaction=view)
                text = (
                    "Записал. "
                    + ("Полная инструкция: " + row["url"] + ". " if row["url"] else "")
                    + "Если нужно решить сейчас — запросите помощь."
                )
                return screen("S4a", text, [HELP, {"label": "Другие темы", "action": "section:" + row["section"] + ":0"}, MENU])
        return self.unknown()

    def help_menu(self):
        return screen(
            "S6",
            "С чем нужна помощь?",
            [{"label": v["title"], "action": "help:" + k} for k, v in self.catalog.settings["help_routes"].items()] + [MENU],
        )

    def metrics(self):
        with self.connect() as db:
            self.cleanup(db, time.time())
            rows = db.execute(
                "SELECT kind,topic,version,value,COUNT(*) AS count FROM events e WHERE delivered=1 "
                "AND (kind NOT IN ('rating','reason') OR EXISTS (SELECT 1 FROM events v "
                "WHERE v.kind='card' AND v.interaction=e.interaction AND v.delivered=1)) "
                "GROUP BY kind,topic,version,value"
            ).fetchall()
        groups = [dict(r) for r in rows]

        def count(kind, value=None):
            return sum(r["count"] for r in groups if r["kind"] == kind and (value is None or r["value"] == value))

        views = count("card")
        rated = count("rating")
        return dict(
            groups=groups,
            card_views=views,
            ratings=rated,
            feedback_coverage=rated / views if views else None,
            helpfulness=count("rating", "yes") / rated if rated else None,
            note="PI identifiers are not collected. Usage per PI requires an external pilot baseline.",
        )

    def delivery_state(self, reply_id):
        with self.connect() as db:
            row = db.execute("SELECT sent_count,completed FROM dedup WHERE eid=?", (reply_id,)).fetchone()
            if row is None:
                raise ValueError("Delivery expired")
            return dict(row)

    def mark_sent(self, reply_id, count):
        with self.connect() as db:
            db.execute("UPDATE dedup SET sent_count=MAX(sent_count,?) WHERE eid=?", (count, reply_id))

    def mark_complete(self, reply_id):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE dedup SET completed=1 WHERE eid=?", (reply_id,))
            db.execute("UPDATE events SET delivered=1 WHERE delivery=?", (reply_id,))

    def journal_rows(self, limit=10000):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT time,kind,topic,version,value,delivered,participant FROM events ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
            ]
