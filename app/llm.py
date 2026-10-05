"""Клиент OpenAI-совместимого API на стандартной библиотеке.

Подходит для X5 CoPilot API, vLLM, Ollama, LiteLLM, TGI — всего, что отвечает на
/v1/chat/completions и, необязательно, /v1/embeddings. Никаких внешних пакетов:
модуль работает в закрытом контуре без доступа к PyPI.

Сеть: системный прокси по умолчанию отключён (API внутри контура), TLS проверяется,
корпоративный CA задаётся LLM_CA_FILE. Ответы reasoning-моделей очищаются от <think>.
"""

import json
import re
import ssl
import time
import urllib.error
import urllib.request

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
MAX_ANSWER_CHARS = 1500


class LLMError(Exception):
    def __init__(self, code, message, status=None):
        super().__init__(message)
        self.code = code
        self.status = status


def strip_think(text):
    """Убирает блоки рассуждений <think>…</think> у reasoning-моделей (Qwen3 в шлюзе X5 и др.).

    Если лимит токенов кончился посреди рассуждения, закрывающего тега нет — такой хвост тоже
    вырезается целиком: недодуманные рассуждения не должны попасть Аватару вместо ответа.
    """
    text = _THINK_RE.sub("", text or "")
    if "</think>" in text:  # шаблон открыл <think> сам, в ответе только закрывающий тег
        text = text.split("</think>", 1)[1]
    if "<think>" in text:  # рассуждение оборвано лимитом — ответа после него нет
        text = text.split("<think>", 1)[0]
    return text.strip()


THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


class LLM:
    """Минимальный клиент: проверка доступности, список моделей, ответ, эмбеддинги."""

    def __init__(self, settings):
        self.url = (settings.ai_url or "").rstrip("/")
        self.model = settings.ai_model or ""
        self.key = settings.ai_api_key or ""
        self.auth_header = getattr(settings, "ai_auth_header", "") or "Authorization"
        self.timeout = settings.ai_timeout
        self.temperature = settings.ai_temperature
        self.max_tokens = settings.ai_max_tokens
        self.embed_model = settings.ai_embed_model or ""
        self.embed_url = (settings.ai_embed_url or self.url).rstrip("/")
        try:
            self.extra = json.loads(settings.ai_extra_json) if settings.ai_extra_json.strip() else {}
        except ValueError as e:
            raise ValueError("AI_EXTRA_JSON: некорректный JSON") from e
        if not isinstance(self.extra, dict):
            raise ValueError("AI_EXTRA_JSON: ожидается объект JSON")
        # Qwen3 в шлюзе X5 по умолчанию «рассуждает»: долго и съедает лимит токенов. Как в innolib,
        # рассуждения отключаются; если сервер параметр не принимает — повторяем без него и запоминаем.
        self.thinking_off = bool(getattr(settings, "ai_disable_thinking", True)) and \
            "chat_template_kwargs" not in self.extra
        self.last = {}

        context = ssl.create_default_context(cafile=settings.ai_ca_file or None)
        if not settings.ai_verify_tls:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        handlers = [urllib.request.HTTPSHandler(context=context)]
        if not settings.ai_use_system_proxy:
            handlers.append(urllib.request.ProxyHandler({}))  # контурный API — мимо прокси
        self.opener = urllib.request.build_opener(*handlers)

    @property
    def enabled(self):
        return bool(self.url and self.model)

    @property
    def embeddings_enabled(self):
        return bool(self.embed_url and self.embed_model)

    # ------------------------------------------------------------ транспорт
    def _open(self, base, path, body=None, timeout=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = urllib.request.Request(base + path, data=data, method="POST" if data else "GET")
        request.add_header("Content-Type", "application/json")
        request.add_header("Accept", "application/json")
        if self.key:
            # Стандарт OpenAI — «Authorization: Bearer ключ»; свой заголовок (api-key, X-API-Key) — ключ как есть
            if self.auth_header.lower() == "authorization":
                request.add_header("Authorization", "Bearer " + self.key)
            else:
                request.add_header(self.auth_header, self.key)
        try:
            return self.opener.open(request, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            code = {401: "ai_auth", 403: "ai_auth", 404: "ai_not_found", 429: "ai_rate_limited"}.get(e.code, "ai_http")
            raise LLMError(code, f"LLM вернул HTTP {e.code}: {detail}", e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise LLMError("ai_unreachable", f"LLM недоступен: {e}") from None

    def _json(self, base, path, body=None, timeout=None):
        with self._open(base, path, body, timeout=timeout) as response:
            try:
                return json.loads(response.read().decode("utf-8"))
            except ValueError:
                raise LLMError("ai_bad_response", "LLM вернул не JSON") from None

    # ------------------------------------------------------------ API
    def models(self):
        data = self._json(self.url, "/models", timeout=min(self.timeout, 15))
        return [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]

    def _body(self, messages, temperature):
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
        }
        if self.thinking_off:
            body.update(THINKING_OFF)
        body.update(self.extra)
        return body

    def complete(self, messages, temperature=None):
        started = time.monotonic()
        try:
            data = self._json(self.url, "/chat/completions", self._body(messages, temperature))
        except LLMError as error:
            if not (self.thinking_off and error.status in (400, 422)):
                raise
            self.thinking_off = False  # сервер не знает chat_template_kwargs — больше не отправляем
            data = self._json(self.url, "/chat/completions", self._body(messages, temperature))
        try:
            choice = data["choices"][0]
            message = choice["message"]
            raw = message.get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            raise LLMError("ai_bad_response", "В ответе LLM нет choices[0].message.content") from None
        text = strip_think(raw)
        thinking = "<think>" in raw or "</think>" in raw or bool(message.get("reasoning_content") or message.get("reasoning"))
        self.last = {
            "seconds": round(time.monotonic() - started, 1),
            "thinking": thinking,
            "finish_reason": choice.get("finish_reason"),
            "thinking_off_sent": self.thinking_off,
        }
        if not text:
            if thinking or choice.get("finish_reason") == "length":
                raise LLMError("ai_truncated", "Модель потратила весь лимит токенов на рассуждения и не дала ответа. "
                               'Отключите рассуждения: AI_DISABLE_THINKING=true или AI_EXTRA_JSON='
                               '{"chat_template_kwargs":{"enable_thinking":false}}; либо увеличьте AI_MAX_TOKENS')
            raise LLMError("ai_bad_response", "LLM вернул пустой ответ")
        return text[:MAX_ANSWER_CHARS]

    def embed(self, texts, batch=32):
        out = []
        for i in range(0, len(texts), batch):
            part = texts[i:i + batch]
            data = self._json(self.embed_url, "/embeddings", {"model": self.embed_model, "input": part})
            items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
            if len(items) != len(part):
                raise LLMError("ai_bad_response", "Эмбеддинги: число векторов не совпадает с числом текстов")
            out.extend(d["embedding"] for d in items)
        return out

    def check(self):
        """Короткая проверка связи для страницы «Содержание»: модель отвечает?"""
        if not self.enabled:
            return {"enabled": False, "reason": "AI_URL или AI_MODEL не заданы"}
        result = {"enabled": True, "url": self.url, "model": self.model, "embeddings": self.embeddings_enabled}
        try:
            result["models"] = self.models()[:20]
        except LLMError as e:
            result["models_error"] = str(e)
        answer = self.complete(
            [{"role": "system", "content": "Отвечай одним словом."}, {"role": "user", "content": "Скажи: готов"}],
            temperature=0,
        )
        result["answer"] = answer[:100]
        result.update(self.last)
        return result
