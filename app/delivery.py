"""Durable reply queue. Operational routing is encrypted and expires after one hour."""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sqlite3
import time

from cryptography.fernet import Fernet, InvalidToken

from .transports.rooms import DeliveryError

LOG = logging.getLogger("rooms_bot")


class QueueFull(Exception):
    pass


class Delivery:
    def __init__(self, engine, rooms):
        self.engine, self.rooms = engine, rooms
        key = hmac.new(engine.secret, b"rooms-delivery-routing-v1", hashlib.sha256).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))
        self._lock = None
        with engine.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS outbox (rid TEXT PRIMARY KEY, lane TEXT NOT NULL, "
                "route BLOB NOT NULL, created REAL NOT NULL, next_at REAL NOT NULL, "
                "attempts INTEGER NOT NULL DEFAULT 0)"
            )

    def enqueue(self, chat_id):
        def save(db, result):
            rid = result["reply_id"]
            if db.execute("SELECT 1 FROM outbox WHERE rid=?", (rid,)).fetchone():
                return
            if db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] >= 1000:
                raise QueueFull()
            now = time.time()
            db.execute(
                "INSERT INTO outbox(rid,lane,route,created,next_at) VALUES(?,?,?,?,?)",
                (rid, self.engine.digest("route:" + chat_id), self.cipher.encrypt(chat_id.encode()), now, now),
            )

        return save

    def stats(self):
        with self.engine.connect() as db:
            row = db.execute(
                "SELECT COUNT(*) AS pending, COALESCE(MAX(attempts),0) AS max_attempts, MIN(created) AS oldest FROM outbox"
            ).fetchone()
        return {
            "pending": row["pending"],
            "max_attempts": row["max_attempts"],
            "oldest_seconds": max(0, time.time() - row["oldest"]) if row["oldest"] else 0,
        }

    @property
    def lock(self):
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def tick(self):
        async with self.lock:
            now = time.time()
            with self.engine.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for row in db.execute("SELECT rid FROM outbox WHERE created<?", (now - 3600,)).fetchall():
                    self.engine.log(db, now, "delivery_expired")
                    db.execute("UPDATE dedup SET completed=1 WHERE eid=?", (row["rid"],))
                    db.execute("DELETE FROM outbox WHERE rid=?", (row["rid"],))
                row = db.execute(
                    "SELECT q.*,d.response,d.sent_count,d.completed FROM outbox q "
                    "JOIN dedup d ON q.rid=d.eid WHERE q.next_at<=? AND NOT EXISTS "
                    "(SELECT 1 FROM outbox p WHERE p.lane=q.lane AND p.rowid<q.rowid) "
                    "ORDER BY q.rowid LIMIT 1",
                    (now,),
                ).fetchone()
            if row is None:
                return False
            rid = row["rid"]
            try:
                chat_id = self.cipher.decrypt(row["route"]).decode()
                result = json.loads(row["response"])
                if not row["completed"]:
                    for i, message in enumerate(result["messages"]):
                        if i < row["sent_count"]:
                            continue
                        await self.rooms.send(chat_id, message)
                        self.engine.mark_sent(rid, i + 1)
                    self.engine.mark_complete(rid)
                with self.engine.connect() as db:
                    db.execute("DELETE FROM outbox WHERE rid=?", (rid,))
            except (DeliveryError, InvalidToken):
                with self.engine.connect() as db:
                    db.execute(
                        "UPDATE outbox SET attempts=attempts+1,next_at=? WHERE rid=?",
                        (now + min(300, 2 ** min(row["attempts"] + 1, 9)), rid),
                    )
                LOG.warning("rooms_delivery_retry_pending")
            return True

    async def run(self):
        while True:
            try:
                worked = await self.tick()
            except sqlite3.Error:
                LOG.error("delivery_database_unavailable")
                worked = False
            await asyncio.sleep(0.1 if worked else 1)
