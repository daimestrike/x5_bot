"""Заглушка OpenAI-совместимого API: позволяет проверить ИИ-режим без настоящей модели.

Отвечает по фрагментам, которые бот сам же и прислал в контексте, — то есть показывает,
что именно нашёл RAG. Это не модель и не генерация: текст собирается из базы знаний.

    python scripts/mock_llm.py --port 8011
    AI_ENABLED=true AI_URL=http://127.0.0.1:8011/v1 AI_MODEL=mock make demo
"""

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CARD_RE = re.compile(r"\[([A-E]-\d{2})\]\s*(.+)")


def answer_from_context(messages):
    context = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
    question = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    for block in context.split("---"):
        lines = [line for line in block.splitlines() if line.strip()]
        for i, line in enumerate(lines):
            head = CARD_RE.match(line.strip())
            if head:
                rest = lines[i + 1:]
                break
        else:
            continue
        break
    else:
        return "В справочнике нет ответа на этот вопрос. Откройте меню или спросите ревизора."
    code, title = head.group(1), head.group(2)
    steps = [line for line in rest if re.match(r"\s*\d+\.", line)][:4]
    body = "\n".join(steps) if steps else "\n".join(rest[:3])
    return f"{title}\n{body}\nПодробнее: [{code}]" + (f"\n(заглушка модели, вопрос: {question[:60]})" if question else "")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, payload, status=200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send({"object": "list", "data": [{"id": "mock", "object": "model"}]})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except ValueError:
            return self._send({"error": "bad json"}, 400)
        path = self.path.rstrip("/")
        if path.endswith("/chat/completions"):
            text = answer_from_context(body.get("messages") or [])
            self._send({
                "id": "mock-" + str(int(time.time())),
                "object": "chat.completion",
                "model": body.get("model", "mock"),
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
        elif path.endswith("/embeddings"):
            inputs = body.get("input") or []
            if isinstance(inputs, str):
                inputs = [inputs]
            vectors = [{"index": i, "embedding": _toy_vector(t)} for i, t in enumerate(inputs)]
            self._send({"object": "list", "model": body.get("model", "mock"), "data": vectors})
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *args):
        pass


def _toy_vector(text, size=64):
    """Детерминированный «вектор» из хеша слов — только чтобы проверить код гибридного поиска."""
    vector = [0.0] * size
    for word in re.findall(r"[a-zа-я0-9]+", (text or "").lower()):
        vector[hash(word) % size] += 1.0
    norm = sum(v * v for v in vector) ** 0.5 or 1.0
    return [v / norm for v in vector]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"Заглушка модели: http://{args.host}:{args.port}/v1 (Ctrl+C — выход)")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
