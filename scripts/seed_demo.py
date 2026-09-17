"""Заполнить демо-журнал синтетическими обращениями, чтобы дашборд метрик было на что смотреть.

Только для BOT_MODE=demo: скрипт отказывается работать с production-конфигурацией.
    python scripts/seed_demo.py [--days 14] [--sessions 60] [--seed 1]
"""

import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings  # noqa: E402
from app.content import Catalog  # noqa: E402
from app.engine import Engine  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--sessions", type=int, default=60)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    settings = Settings.from_env()
    if settings.mode != "demo":
        raise SystemExit("seed_demo.py works only with BOT_MODE=demo")
    rng = random.Random(args.seed)
    engine = Engine(
        Catalog(settings.content_dir), settings.db_path, settings.state_secret, settings.session_ttl, settings.retention_days
    )
    cards = list(engine.catalog.cards)
    weights = [3 if c.startswith(("A", "B")) else 1 for c in cards]  # подготовка и сбои спрашивают чаще
    now = time.time()
    counter = 0

    def send(user, t, **fields):
        nonlocal counter
        counter += 1
        event = dict(event_id=f"seed-{args.seed}-{counter}", conversation_id=f"chat-{user}", user_id=f"user-{user}", **fields)
        return engine.handle(event, now=t)

    for _ in range(args.sessions):
        user = rng.randint(1, 25)
        t = now - rng.uniform(0, args.days * 86400)
        send(user, t, type="opened")
        t += 5
        if rng.random() < 0.15:
            words = ["модем", "наушники", "эцп", "сейф", "зонирование", "не грузит", "вайфай"]
            send(user, t, type="message", text=rng.choice(words))
            t += 8
        if rng.random() < 0.1:
            send(user, t, type="message", text="а что делать если модем не ловит сеть")
            t += 8
        for _ in range(rng.choice([1, 1, 2, 3])):
            cid = rng.choices(cards, weights)[0]
            result = send(user, t, type="action", action="card:" + cid)
            t += rng.uniform(10, 60)
            buttons = result["messages"][-1]["buttons"]
            rate = [b for b in buttons if b["action"].startswith("rate:")]
            if rate and rng.random() < 0.65:
                yes = rng.random() < (0.55 if cid.startswith("B") else 0.8)
                send(user, t, type="action", action=rate[0 if yes else 1]["action"])
                t += 5
                if not yes:
                    view = rate[1]["action"].split(":")[1]
                    send(user, t, type="action", action=f"reason:{view}:" + rng.choice(["wrong", "details", "details", "failed"]))
                    t += 5
                    if rng.random() < 0.4:
                        send(user, t, type="action", action="help:" + rng.choice(["tech", "tech", "pi", "org"]))
                        t += 5
        if rng.random() < 0.05:
            send(user, t, type="attachment", attachment_type="audio")
        send(user, t + 30, type="closed")
    print(f"Сгенерировано {counter} событий за {args.days} дн. -> {settings.db_path}. Откройте /metrics/dashboard")


if __name__ == "__main__":
    main()
