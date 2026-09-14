"""Приёмка: экраны и переходы (п. 3, 4, 5, 7, 8, 9, 10), сценарии диалога 1–13."""
from conftest import datas, labels, last_text

from app.models import Incoming


def test_s0_once_per_session(engine):
    r = engine.handle(Incoming(user_id="new", text=None))
    assert r.screen == "S0" and len(r.messages) == 1
    assert "справочник Аватара" in r.messages[0].text
    r2 = engine.handle(Incoming(user_id="new", text="меню"))
    assert r2.screen == "S1" and len(r2.messages) == 1  # приветствие второй раз не шлём


def test_s0_then_query_in_one_go(engine):
    r = engine.handle(Incoming(user_id="new2", text="модем"))
    assert [m.text[:4] for m in r.messages][0].startswith("Я сп")
    assert r.screen == "S5a"


def test_main_menu_buttons(bot):
    r = bot.click("menu")
    assert r.screen == "S1"
    assert labels(r) == ["Подготовка до ПИ", "Связь и сбои", "Как снимать", "Порядок и после ПИ",
                         "Этапы ПИ — справка", "Поиск", "Помощь человека"]


def test_depth_three_clicks_to_any_card(bot, content):
    """От меню до любой карточки — не более трёх нажатий, без ввода текста."""
    for card in content.cards.values():
        bot.click("menu")
        r = bot.click(f"sec:{card.section}:0")
        clicks = 2
        if f"card:{card.id}" not in datas(r):
            r = bot.click(f"sec:{card.section}:1")
            clicks += 1
        assert f"card:{card.id}" in datas(r), card.id
        r = bot.click(f"card:{card.id}")
        clicks += 1
        assert r.screen == "S3" and clicks <= 4  # 1 нажатие «Меню» + до 3 нажатий до карточки
        assert "Меню" in labels(r)  # возврат в меню за одно нажатие


def test_section_pagination(bot):
    r = bot.click("sec:A:0")
    assert r.screen == "S2"
    topics = [d for d in datas(r) if d.startswith("card:")]
    assert len(topics) == 8 and "Ещё темы" in labels(r) and "Назад" in labels(r)
    r = bot.click("sec:A:1")
    topics2 = [d for d in datas(r) if d.startswith("card:")]
    assert len(topics2) == 2
    r = bot.click("sec:C:0")
    assert "Ещё темы" not in labels(r)


def test_card_scenario_2(bot):
    r = bot.click("card:A-07")
    t = last_text(r)
    assert t.startswith("Как включить модем и вставить сим-карту?")
    assert "1. " in t and "Подробнее:" in t and "стр. 7" in t
    assert labels(r) == ["Помогло", "Не помогло", "Другие темы", "Меню"]


def test_helped_flow(bot, journal):
    bot.click("card:A-07")
    r = bot.click("ok:A-07")
    assert r.screen == "S3a"
    events = [(x["event"], x["card_id"], x["rating"]) for x in journal.rows()]
    assert ("card_view", "A-07", None) in events
    assert ("rating", "A-07", "helped") in events


def test_not_helped_flow_scenario_6(bot, journal):
    bot.click("card:B-07")
    r = bot.click("no:B-07")
    assert r.screen == "S4" and labels(r) == ["Не то, что искал", "Не хватает деталей", "Сделал, не сработало"]
    r = bot.click("why:details:B-07")
    assert r.screen == "S4a" and "http" in last_text(r)
    assert labels(r) == ["Помощь человека", "Другие темы", "Меню"]
    rows = [x for x in journal.rows() if x["event"] == "clarify"]
    assert rows and rows[-1]["card_id"] == "B-07" and rows[-1]["reason"] == "details"
    r = bot.click("help")
    assert r.screen == "S6"


def test_search_scenarios_3_4(bot, journal):
    r = bot.click("search")
    assert r.screen == "S5"
    r = bot.text("модем")
    assert r.screen == "S5a" and 1 <= len([d for d in datas(r) if d.startswith("card:")]) <= 5
    assert "Другой запрос" in labels(r)
    r = bot.text("не грузит")
    assert r.screen == "S5b" and labels(r) == ["Меню", "Помощь человека"]
    miss = [x for x in journal.rows() if x["event"] == "search_miss"]
    assert miss and miss[-1]["query"] == "не грузит"


def test_free_text_scenario_5(bot, journal):
    r = bot.text("а что делать если модем не ловит сеть")
    assert r.screen == "S7" and labels(r) == ["Меню", "Поиск"]
    assert "Целиком вопрос понять не смогу" in last_text(r)
    assert journal.rows()[-1]["event"] == "unrecognized"


def test_action_request_scenario_11(bot):
    r = bot.text("занеси паллеты в S9999")
    assert r.screen == "S7" and "GK и Inventa" in last_text(r)


def test_attachment_scenario_10(bot, journal):
    r = bot.attach("voice")
    assert r.screen == "S7" and "не обрабатываю" in last_text(r)
    assert journal.rows()[-1]["event"] == "attachment"


def test_thanks_scenario_13(bot, journal):
    r = bot.text("спасибо")
    assert r.screen == "S7" and "Меню" in labels(r)
    assert journal.rows()[-1]["event"] == "thanks"
    assert not any(x["event"] == "card_view" for x in journal.rows())


def test_help_scenario_9(bot, journal):
    r = bot.click("help")
    assert labels(r) == ["Вопрос по ПИ", "Не работает техника", "Организационный вопрос", "Меню"]
    r = bot.click("help:tech")
    assert r.screen == "S6a" and "Меню" in labels(r)
    assert journal.rows()[-1]["help_type"] == "tech"


def test_e03_scenario_8(bot):
    r = bot.click("card:E-03")
    assert "ревизор" in last_text(r).lower()


def test_text_fallbacks_number_and_label(bot):
    bot.click("menu")
    r = bot.text("2")  # вторая кнопка меню — «Связь и сбои»
    assert r.screen == "S2" and "Связь и сбои" in last_text(r)
    r = bot.text("Назад")
    assert r.screen == "S1"
    r = bot.text("Помощь человека")
    assert r.screen == "S6"


def test_material_error_s8(engine, journal):
    r = engine.handle(Incoming(user_id="x", callback="card:Z-99"))
    assert r.screen == "S8" and "Не могу открыть материал" in r.messages[-1].text
    assert journal.rows()[-1]["event"] == "material_error"


def test_unknown_callback_goes_menu(bot):
    assert bot.click("whatever").screen == "S1"


def test_journal_has_no_user_ids(bot, journal):
    bot.click("card:A-01")
    for row in journal.rows():
        assert "u1" not in (row["user_hash"], row["session_id"])
        assert row["user_hash"] != "u1"
