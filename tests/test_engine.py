"""Экраны и переходы MVP v11: лист «Приёмка MVP» (п. 1–8, 13) и «Логика ответов»."""
from conftest import datas, labels, last_text

from app.models import Incoming


def test_open_shows_menu_once_greeting(engine):
    r = engine.handle(Incoming(user_id="new", text=None))
    assert r.screen == "S1" and len(r.messages) == 2  # приветствие + меню
    r2 = engine.handle(Incoming(user_id="new", text="меню"))
    assert r2.screen == "S1" and len(r2.messages) == 1


def test_main_menu_v11(bot):
    r = bot.click("menu")
    assert labels(r) == ["Типовые вопросы", "Инструкции", "Чек-листы", "Нужна помощь"]


def test_depth_three_clicks_to_any_card(bot, content):
    """П. 1/13: от меню до любого материала — не более трёх нажатий, без ввода текста."""
    for card in content.cards.values():
        bot.click("menu")
        r = bot.click(f"type:{card.type}")
        clicks = 1
        if f"card:{card.id}" not in datas(r):
            r = bot.click(f"type:{card.type}:{card.group}:0")
            clicks += 1
        assert f"card:{card.id}" in datas(r), card.id
        r = bot.click(f"card:{card.id}")
        clicks += 1
        assert r.screen == "S3" and clicks <= 3, card.id
        assert "Меню" in labels(r)


def test_type_with_many_cards_shows_groups_first(bot):
    r = bot.click("type:answer")  # 18 тем -> сначала разделы
    assert r.screen == "S2" and all(d.startswith("type:answer:") for d in datas(r) if d != "menu")
    r = bot.click("type:answer:C:0")
    assert "Как снимать" in last_text(r) and len([d for d in datas(r) if d.startswith("card:")]) == 6
    assert "Назад" in labels(r)
    r = bot.click("type:instruction")  # 6 тем -> сразу список
    assert len([d for d in datas(r) if d.startswith("card:")]) == 6


def test_card_render_checklist(bot):
    r = bot.click("card:A-07")
    t = last_text(r)
    assert t.startswith("Как включить модем и вставить сим-карту?")
    assert "Когда:" in t and "Подготовьте:" in t and "1. " in t and "Результат:" in t
    assert "Если не получается:" in t and "Полный документ:" in t and "http" in t
    assert labels(r) == ["Помогло", "Не помогло", "Другие темы", "Меню"]


def test_card_render_answer(bot):
    t = last_text(bot.click("card:C-02"))
    assert "Когда:" not in t and "Результат:" not in t and "Если не получается:" in t


def test_rating_last_is_final(bot, journal):
    """П. 5: обе кнопки сохраняют оценку; повторное нажатие не увеличивает счётчик; итоговая — последняя."""
    from app.metrics import summary
    bot.click("card:A-07")
    assert bot.click("no:A-07").screen == "S4"
    bot.click("card:A-07")  # повтор темы в том же сеансе — то же обращение
    assert bot.click("ok:A-07").screen == "S3a"
    assert bot.click("ok:A-07").screen == "S3a"
    m = summary(journal)
    assert m["answers_issued"] == 1 and m["card_views_total"] == 2
    assert m["helped"] == 1 and m["not_helped"] == 0 and m["usefulness_pct"] == 100.0


def test_not_helped_offers_topics_or_help(bot, journal):
    bot.click("card:B-07")
    r = bot.click("no:B-07")
    assert labels(r) == ["Другие темы", "Нужна помощь", "Меню"]
    r = bot.click("help")
    assert r.screen == "S6"
    r = bot.click("help:tech")
    assert r.screen == "S6a"
    row = [x for x in journal.rows() if x["event"] == "help_request"][-1]
    assert row["card_id"] == "B-07" and row["help_type"] == "tech"


def test_help_without_topic_logged(bot, journal):
    r = bot.click("help")
    assert labels(r) == ["Вопрос по ходу ПИ", "Не работает техника", "Организационный вопрос", "Меню"]
    bot.click("help:pi")
    row = [x for x in journal.rows() if x["event"] == "help_request"][-1]
    assert row["card_id"] is None and row["help_type"] == "pi"


def test_free_text_offers_menu_and_is_logged(bot, journal):
    """П. 7: бот предлагает меню и не отвечает по смыслу; вопрос попадает в журнал для ответственного."""
    r = bot.text("а что делать если модем не ловит сеть")
    assert r.screen == "S7" and labels(r) == ["Меню", "Нужна помощь"]
    assert "по смыслу" in last_text(r)
    row = journal.rows()[-1]
    assert row["event"] == "question" and row["query"] == "а что делать если модем не ловит сеть"
    r = bot.text("модем")  # поиск выключен по умолчанию — одно слово тоже свободный текст
    assert r.screen == "S7"


def test_action_request(bot):
    assert "GK и Inventa" in last_text(bot.text("занеси паллеты в S9999"))


def test_attachment(bot, journal):
    r = bot.attach("voice")
    assert r.screen == "S7" and journal.rows()[-1]["event"] == "attachment"


def test_thanks_not_an_appeal(bot, journal):
    r = bot.text("спасибо")
    assert r.screen == "S7" and journal.rows()[-1]["event"] == "thanks"


def test_text_fallbacks_number_and_label(bot):
    bot.click("menu")
    r = bot.text("3")  # третья кнопка меню — «Чек-листы»
    assert r.screen == "S2" and "Чек-листы" in last_text(r)
    assert bot.text("Нужна помощь").screen == "S6"
    assert bot.text("Меню").screen == "S1"


def test_unpublished_material_gives_s8(engine, journal):
    """П. 8/10: недоступный или неопубликованный материал -> сообщение об ошибке и контакт, не случайный текст."""
    r = engine.handle(Incoming(user_id="x", callback="card:Z-99"))
    assert r.screen == "S8" and "Не могу открыть материал" in r.messages[-1].text
    assert labels(r) == ["Нужна помощь", "Меню"]
    assert journal.rows()[-1]["event"] == "material_error"


def test_unknown_callback_goes_menu(bot):
    assert bot.click("whatever").screen == "S1"


def test_journal_has_participant_and_session(bot, journal):
    """П. 11/12: в журнале есть участник и сеанс для ручной привязки к ПИ."""
    bot.click("card:A-01")
    row = journal.rows()[-1]
    assert row["user_ref"] == "u1" and row["session_id"] and row["card_version"] == "1.0"


def test_journal_hash_mode(content, journal, settings):
    from dataclasses import replace

    from app.engine import Engine
    from app.sessions import SessionStore
    e = Engine(content, journal, SessionStore(60), replace(settings, journal_user_mode="hash"))
    e.handle(Incoming(user_id="secret-user", callback="card:A-01"))
    assert all("secret-user" not in x["user_ref"] for x in journal.rows())


def test_search_when_enabled(content, journal, settings):
    from dataclasses import replace

    from app.engine import Engine
    from app.sessions import SessionStore
    e = Engine(content, journal, SessionStore(60), replace(settings, search_enabled=True))
    r = e.handle(Incoming(user_id="s", text="модем"))
    assert r.screen == "S5a" and "card:A-07" in datas(r)
    assert "Поиск" in labels(e.handle(Incoming(user_id="s", callback="menu")))
