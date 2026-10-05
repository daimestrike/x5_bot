"""ИИ-ответ по базе знаний: клиент модели, поиск (RAG) и поведение бота."""

import json
from dataclasses import replace

import pytest

from app.assistant import Assistant
from app.content import Catalog
from app.engine import Engine
from app.llm import LLM, LLMError, strip_think
from app.rag import BM25, Rag, card_chunk, cited_cards, doc_chunks, stem, tokenize
from app.sources import SourceStore


class FakeLLM:
    """Подменяет модель: возвращает заданный ответ и запоминает, что ей прислали."""

    def __init__(self, answer="Готово [A-07].", model="test-model", fail=None):
        self.answer, self.model, self.fail = answer, model, fail
        self.calls = []
        self.embed_model = ""

    enabled = True
    embeddings_enabled = False

    def complete(self, messages, temperature=None):
        self.calls.append(messages)
        if self.fail:
            raise self.fail
        return self.answer

    def embed(self, texts, batch=32):
        raise AssertionError("эмбеддинги выключены")


@pytest.fixture
def ai_settings(settings):
    return replace(settings, ai_enabled=True, ai_url="https://llm.invalid/v1", ai_model="test-model")


@pytest.fixture
def make_engine(ai_settings, tmp_path):
    def build(llm, content_dir=None):
        catalog = Catalog(content_dir or ai_settings.content_dir)
        sources = SourceStore(content_dir or ai_settings.content_dir)
        assistant = Assistant(ai_settings, catalog, sources, llm, lambda m: None)
        return Engine(catalog, ai_settings.db_path, ai_settings.state_secret, assistant=assistant)

    return build


# ---------------------------------------------------------------- поиск
def test_stem_and_tokenize():
    # падежи сводятся к одной основе, но ключевой термин не обрезается до «мод»
    assert stem("модема") == stem("модемом") == stem("модем") == "модем"
    assert stem("подключения") == "подключ"
    assert "модем" in tokenize("Модем не подключается")
    assert "это" not in tokenize("это модем")  # стоп-слово
    assert stem("интернет") in tokenize("вайфай", expand=True)  # раскрытие сокращений


def test_bm25_finds_card(settings):
    catalog = Catalog(settings.content_dir)
    chunks = [card_chunk(c, "Связь и сбои") for c in catalog.cards.values()]
    index = BM25(chunks)
    top = [chunks[i]["ref"] for _, i in index.search("модем не подключается", 3)]
    assert "B-02" in top
    top = [chunks[i]["ref"] for _, i in index.search("наушники не слышно", 3)]
    assert "B-04" in top


def test_doc_chunks_keep_page_reference():
    source = {"key": "memo", "title": "Памятка", "units": [
        {"n": 1, "page": 4, "text": "Подносите микрофон гарнитуры ко рту."},
        {"n": 2, "page": 4, "text": "Говорите громко и чётко."},
    ]}
    chunks = doc_chunks(source)
    assert len(chunks) == 1 and chunks[0]["kind"] == "doc" and chunks[0]["page"] == 4
    assert "стр. 4" in chunks[0]["text"] and "микрофон" in chunks[0]["text"]


def test_rag_search_mixes_cards_and_documents(ai_settings, tmp_path):
    content = tmp_path / "content"
    content.mkdir()
    for name in ("cards.yaml", "settings.yaml"):
        (content / name).write_text((ai_settings.content_dir / name).read_text(encoding="utf-8"), encoding="utf-8")
    sources = SourceStore(content)
    (sources.dir / "memo.json").write_text(json.dumps({
        "key": "memo", "title": "Памятка по видеофиксации",
        "units": [{"n": 1, "page": 2, "text": "Запись ревизии ГК идёт одним дублем без остановки."}],
    }, ensure_ascii=False), encoding="utf-8")
    rag = Rag(Catalog(content), sources, FakeLLM(), lambda m: None)
    found = rag.search("ревизия гк одним дублем")
    assert found and {f["kind"] for f in found} & {"card", "doc"}
    assert any(f["ref"] == "memo" for f in found) or any(f["ref"] == "C-06" for f in found)


def test_cited_cards_filters_unknown_codes(settings):
    cards = Catalog(settings.content_dir).cards
    assert cited_cards("Смотри [A-07] и [B-02].", cards) == ["A-07", "B-02"]
    assert cited_cards("Выдумка [Z-99] и повтор [A-07] [A-07].", cards) == ["A-07"]


# ---------------------------------------------------------------- клиент модели
def test_strip_think():
    assert strip_think("<think>рассуждения</think>Ответ") == "Ответ"
    assert strip_think("хвост</think>Ответ") == "Ответ"


def test_llm_parses_response(ai_settings, monkeypatch):
    llm = LLM(ai_settings)
    sent = {}

    def fake_json(base, path, body=None, timeout=None):
        sent.update(path=path, body=body)
        return {"choices": [{"message": {"content": "<think>ход мысли</think>Включите модем [A-07]."}}]}

    monkeypatch.setattr(llm, "_json", fake_json)
    assert llm.complete([{"role": "user", "content": "как включить модем"}]) == "Включите модем [A-07]."
    assert sent["path"] == "/chat/completions" and sent["body"]["model"] == "test-model"


def test_llm_empty_answer_is_error(ai_settings, monkeypatch):
    llm = LLM(ai_settings)
    monkeypatch.setattr(llm, "_json", lambda *a, **k: {"choices": [{"message": {"content": "   "}}]})
    with pytest.raises(LLMError):
        llm.complete([{"role": "user", "content": "x"}])


def test_llm_disabled_without_url(settings):
    assert LLM(settings).enabled is False


def test_extra_json_is_validated(ai_settings):
    with pytest.raises(ValueError):
        LLM(replace(ai_settings, ai_extra_json="{not json"))


# ---------------------------------------------------------------- поведение бота
def test_free_text_gets_grounded_ai_answer(make_engine, event):
    llm = FakeLLM("Снимите крышку, вставьте сим-карту, включите долгим нажатием [A-07].")
    engine = make_engine(llm)
    response = engine.handle(event("message", text="как включить модем и вставить симку"))
    screen = response["messages"][-1]
    assert screen["screen"] == "S9"
    assert "[A-07]" in screen["text"]
    labels = [b["label"] for b in screen["buttons"]]
    assert labels[0] == engine.catalog.cards["A-07"]["title"]
    assert "Помогло" in labels and "Меню" in labels
    # в модель ушли фрагменты базы знаний и сам вопрос
    system = "\n".join(m["content"] for m in llm.calls[0] if m["role"] == "system")
    assert "[A-07]" in system and "ФРАГМЕНТЫ БАЗЫ ЗНАНИЙ" in system
    assert llm.calls[0][-1]["content"] == "как включить модем и вставить симку"
    kinds = {r["kind"]: r for r in engine.journal_rows()}
    assert kinds["ai_answer"]["value"] == "grounded" and kinds["ai_answer"]["topic"] == "A-07"


def test_ungrounded_answer_is_marked(make_engine, event):
    engine = make_engine(FakeLLM("В справочнике такого нет."))
    screen = engine.handle(event("message", text="какая завтра будет погода в москве"))["messages"][-1]
    assert screen["screen"] == "S9" and "спросите ревизора" in screen["text"]
    assert [r["value"] for r in engine.journal_rows() if r["kind"] == "ai_answer"] == ["ungrounded"]


def test_ai_answer_can_be_rated(make_engine, event):
    engine = make_engine(FakeLLM())
    screen = engine.handle(event("message", text="как включить модем и вставить симку"))["messages"][-1]
    rate = next(b["action"] for b in screen["buttons"] if b["action"].endswith(":yes"))
    assert engine.handle(event(action=rate))["messages"][-1]["screen"] == "S3a"
    assert engine.metrics()["ratings"] == 1 and engine.metrics()["helpfulness"] == 1.0


def test_short_query_still_uses_keyword_search(make_engine, event):
    llm = FakeLLM()
    engine = make_engine(llm)
    screen = engine.handle(event("message", text="модем"))["messages"][-1]
    assert screen["screen"] == "S5a" and llm.calls == []  # модель не вызывалась


def test_commands_never_reach_model(make_engine, event):
    llm = FakeLLM()
    engine = make_engine(llm)
    for text in ("меню", "поиск", "спасибо", "помощь"):
        engine.handle(event("message", text=text))
    assert llm.calls == []


def test_single_unknown_word_answers_without_model(make_engine, event):
    """Одно слово мимо справочника — прежний быстрый ответ S5b, модель не дёргаем."""
    llm = FakeLLM()
    engine = make_engine(llm)
    screen = engine.handle(event("message", text="абракадабра"))["messages"][-1]
    assert screen["screen"] == "S5b" and llm.calls == []


def test_two_word_miss_goes_to_ai(make_engine, event):
    llm = FakeLLM("По этому вопросу в справочнике ничего нет.")
    engine = make_engine(llm)
    screen = engine.handle(event("message", text="не грузит"))["messages"][-1]
    assert screen["screen"] == "S9" and len(llm.calls) == 1


def test_model_failure_keeps_bot_working(make_engine, event):
    engine = make_engine(FakeLLM(fail=LLMError("ai_unreachable", "нет связи")))
    screen = engine.handle(event("message", text="как включить модем и вставить симку"))["messages"][-1]
    assert screen["screen"] == "S7"  # обычный ответ «понимаю только меню»
    assert [r["kind"] for r in engine.journal_rows() if r["kind"] == "ai_answer"] == []


def test_ai_disabled_keeps_mvp_behaviour(settings, event):
    engine = Engine(Catalog(settings.content_dir), settings.db_path, settings.state_secret)
    screen = engine.handle(event("message", text="а что делать если модем не ловит сеть"))["messages"][-1]
    assert screen["screen"] == "S7"


def test_repeat_event_does_not_ask_model_twice(make_engine, event):
    llm = FakeLLM()
    engine = make_engine(llm)
    e = event("message", text="как включить модем и вставить симку")
    first = engine.handle(e)
    again = engine.handle(dict(e))
    assert first == again and len(llm.calls) == 1


def test_journal_keeps_no_question_text(make_engine, event):
    engine = make_engine(FakeLLM())
    engine.handle(event("message", text="секретная фраза про модем и симку"))
    with engine.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "секретная фраза" not in dump


@pytest.mark.parametrize("header,expected_name,expected_value", [
    ("Authorization", "Authorization", "Bearer k-123"),
    ("api-key", "Api-key", "k-123"),
])
def test_llm_auth_header(ai_settings, monkeypatch, header, expected_name, expected_value):
    llm = LLM(replace(ai_settings, ai_api_key="k-123", ai_auth_header=header))
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_open(request, timeout=None):
        seen.update(request.header_items())
        return Response()

    monkeypatch.setattr(llm.opener, "open", fake_open)
    llm.complete([{"role": "user", "content": "x"}])
    assert seen.get(expected_name) == expected_value


# ---------------------------------------------------------------- модели шлюза X5 (Qwen3, как в innolib)
def test_truncated_reasoning_never_reaches_user():
    assert strip_think("<think>думаю, и лимит кончился") == ""
    assert strip_think("<think>думаю</think>Ответ [A-07]") == "Ответ [A-07]"


def fake_gateway(monkeypatch, llm, replies):
    """replies: список (status, json); запоминает тела запросов."""
    sent = []

    def fake_json(base, path, body=None, timeout=None):
        sent.append(body)
        status, data = replies[len(sent) - 1]
        if status != 200:
            raise LLMError("ai_http", "HTTP %d" % status, status)
        return data

    monkeypatch.setattr(llm, "_json", fake_json)
    return sent


def answer(text, finish="stop", **message):
    return {"choices": [{"message": {"content": text, **message}, "finish_reason": finish}]}


def test_thinking_disabled_by_default(ai_settings, monkeypatch):
    llm = LLM(ai_settings)
    sent = fake_gateway(monkeypatch, llm, [(200, answer("Ок [A-07]"))])
    assert llm.complete([{"role": "user", "content": "x"}]) == "Ок [A-07]"
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": False}


def test_strict_gateway_retried_without_thinking_flag(ai_settings, monkeypatch):
    llm = LLM(ai_settings)
    sent = fake_gateway(monkeypatch, llm, [(400, None), (200, answer("Ок")), (200, answer("Ещё"))])
    assert llm.complete([{"role": "user", "content": "x"}]) == "Ок"
    assert "chat_template_kwargs" not in sent[1]
    llm.complete([{"role": "user", "content": "x"}])
    assert "chat_template_kwargs" not in sent[2]  # запомнил, больше не отправляет


def test_reasoning_eats_token_limit(ai_settings, monkeypatch):
    llm = LLM(replace(ai_settings, ai_disable_thinking=False))
    fake_gateway(monkeypatch, llm, [(200, answer("<think>долго думаю", finish="length"))])
    with pytest.raises(LLMError) as error:
        llm.complete([{"role": "user", "content": "x"}])
    assert error.value.code == "ai_truncated"


def test_user_extra_json_wins(ai_settings, monkeypatch):
    llm = LLM(replace(ai_settings, ai_extra_json='{"chat_template_kwargs":{"enable_thinking":true},"top_p":0.8}'))
    sent = fake_gateway(monkeypatch, llm, [(200, answer("Ок"))])
    llm.complete([{"role": "user", "content": "x"}])
    assert sent[0]["chat_template_kwargs"] == {"enable_thinking": True} and sent[0]["top_p"] == 0.8


def test_innolib_env_names_are_accepted(monkeypatch):
    from app.config import Settings

    for key, value in {
        "BOT_STATE_SECRET": "s" * 40, "BOT_API_TOKEN": "a" * 40, "METRICS_TOKEN": "m" * 40,
        "AI_ENABLED": "true", "LLM_URL": "https://gw.x5.invalid/v1", "LLM_MODEL": "qwen3-light",
        "LLM_CHAT_MODEL": "qwen3-chat", "LLM_API_KEY": "k", "LLM_VERIFY_TLS": "0", "LLM_TIMEOUT": "45",
        "LLM_EXTRA_JSON": '{"top_p":0.9}', "EMBED_MODEL": "bge-m3",
    }.items():
        monkeypatch.setenv(key, value)
    s = Settings.from_env()
    assert (s.ai_url, s.ai_model, s.ai_api_key) == ("https://gw.x5.invalid/v1", "qwen3-chat", "k")
    assert s.ai_verify_tls is False and s.ai_timeout == 45 and s.ai_embed_model == "bge-m3"
    assert s.ai_extra_json == '{"top_p":0.9}'
