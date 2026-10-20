import json
import time

import pytest

from office import accountant, config, football, intel, llm, link, market, risk, ru, sbc, trader
from office.model import Opinion
from tests.conftest import FakeResp, FakeSession

NOW_Y, NOW_M, NOW_D = 2026, 10, 21     # среда


# ── SBC ──────────────────────────────────────────────────────────────────────
def test_sbc_parse_and_ratings():
    page = '<a href="/sbc/upgrades/27-71-2x-83-upgrade/">x</a><a href="/sbc/players/27-64-mls-future-stars-player-pick/">y</a><a href="/sbc/category/expired/">z</a>'
    l = sbc.parse_sbc_list(page)
    assert [x["sid"] for x in l] == ["71", "64"]
    assert sbc.sbc_ratings("2x-83-upgrade", "upgrades") == [83, 84, 85]
    assert sbc.sbc_ratings("mls-player-pick", "players") == []
    assert ru.no_latin(sbc.describe("2x-83-upgrade", "upgrades"))


def seed_fodder(db, feed_prices, rating=85, n=6):
    for i in range(n):
        db.x("INSERT INTO cards VALUES (?,?,?,?,?,?)", (rating * 100 + i, f"Игрок{i}", rating, "", "Rare", time.time()))
        feed_prices[rating * 100 + i] = 2000 + i * 100


def test_fodder_index_needs_five(db):
    prices = {}
    seed_fodder(db, prices, n=4)
    assert sbc.fodder_index(db, {"prices": prices}, 85) is None
    seed_fodder(db, prices, rating=86, n=6)
    idx, cheapest = sbc.fodder_index(db, {"prices": prices}, 86)
    assert idx == 2200 and cheapest == 8600


def test_percentile_and_calibration(db):
    now = time.time()
    assert sbc.percentile(db, -85, 2000, now) is None
    for i in range(10):
        db.x("INSERT INTO price_log VALUES (-85, ?, ?)", (now - i * 3600, 2000 + i * 100))
    assert sbc.percentile(db, -85, 2000, now) == 0.1
    assert sbc.calibrated_up(db, "fodder") == sbc.PRIOR_UP["fodder"]
    for i in range(5):
        db.x("INSERT INTO forecasts (ts,ea_id,name,dept,direction,base,p3) VALUES (?,?,?,?,?,?,?)", (now, i, "x", "fodder", "up", 1000, 1100))
    assert sbc.calibrated_up(db, "fodder") == pytest.approx(0.10)


def test_forecast_verification_1_3_7(db):
    t0 = time.time() - 8 * 86400
    db.x("INSERT INTO forecasts (ts,ea_id,name,dept,direction,base) VALUES (?,?,?,?,?,?)", (t0, 5, "x", "totw", "up", 1000))
    for d, p in ((1, 1100), (3, 990), (7, 1200)):
        db.x("INSERT INTO price_log VALUES (5, ?, ?)", (t0 + d * 86400, p))
    assert sbc.verify_forecasts(db, None, time.time()) == 3
    f = db.one("SELECT * FROM forecasts")
    assert (f["ok1"], f["ok3"], f["ok7"]) == (1, 0, 1)
    assert sbc.forecast_accuracy(db)[1] == (1, 1)


def test_sbc_opinion_fodder_needs_evidence(db, moscow):
    now = moscow(2026, 10, 21, 12)
    prices = {}
    seed_fodder(db, prices, rating=85)
    gg = {"prices": prices}
    assert sbc.opinions(db, gg, now) == []                         # ни спроса, ни ориентира
    srcs = [{"id": "robtrade", "kind": "telegram", "rating": 0.55, "title": "Роб Трейд"}]
    intel.ensure_stats(db, srcs)
    db.x("INSERT INTO facts (key,kind,player,rating,action,price,src,ts,text) VALUES ('fodder:r85','fodder','',85,'sell_above',2800,'robtrade',?,'корм')", (now,))
    ops = sbc.opinions(db, gg, now)
    assert len(ops) == 1 and ops[0].sell_ref == 2800 and ops[0].srcs == ["robtrade"] and ops[0].kind == "fodder"
    assert db.one("SELECT COUNT(*) n FROM forecasts")["n"] == 1


# ── футбол ───────────────────────────────────────────────────────────────────
def test_espn_players():
    ev = [{"competitions": [{"details": [{"scoringPlay": True, "athletesInvolved": [{"displayName": "A B"}]}, {"scoringPlay": True, "ownGoal": True, "athletesInvolved": [{"displayName": "C D"}]},
                                         {"scoringPlay": True, "athletesInvolved": [{"displayName": "A B"}]}, {"redCard": True, "athletesInvolved": [{"displayName": "E F"}]}]}]}]
    r = football.espn_players(ev)
    assert r["A B"]["goals"] == 2 and r["C D"]["goals"] == 0 and r["E F"]["reds"] == 1


def test_totw_deadline(moscow):
    assert football.deadline_hours(moscow(2026, 10, 19, 12)) > 40            # понедельник
    assert football.deadline_hours(moscow(2026, 10, 21, 17)) == pytest.approx(1.0)
    assert football.deadline_hours(moscow(2026, 10, 21, 19)) == 0           # состав уже известен
    assert football.deadline_hours(moscow(2026, 10, 23, 12)) == 0


def test_fpl_injuries_and_scores(db):
    boot = {"events": [{"id": 8, "is_current": True}], "teams": [{"id": 1, "name": "Арсенал"}],
            "elements": [{"id": 1, "first_name": "Erling", "second_name": "Haaland", "team": 1, "element_type": 4, "status": "a", "news": "", "chance_of_playing_next_round": None},
                         {"id": 2, "first_name": "Bukayo", "second_name": "Saka", "team": 1, "element_type": 3, "status": "i", "news": "Ankle", "chance_of_playing_next_round": 0}]}
    live = {"elements": [{"id": 1, "stats": {"minutes": 90, "goals_scored": 2, "assists": 0, "bonus": 3, "red_cards": 0, "clean_sheets": 0}},
                         {"id": 2, "stats": {"minutes": 10, "goals_scored": 1}}]}
    sess = FakeSession({"bootstrap-static": json.dumps(boot), "event/8/live": json.dumps(live)})
    assert football.fetch_fpl(db, time.time(), session=sess) == 1
    assert db.one("SELECT score FROM football")["score"] == 9
    assert "bukayo saka" in football.injured(db, time.time())


# ── риск ─────────────────────────────────────────────────────────────────────
def test_tax_and_thresholds():
    assert risk.after_tax(10000) == 9500
    assert risk.profit(8000, 10000) == 1500
    assert risk.profit_ok(8000, 10000)
    assert not risk.profit_ok(9400, 10000)                          # прибыль 100 < 500
    assert not risk.profit_ok(100000, 105000)                       # 4750 прибыль, но 4,75% < 7%
    assert risk.profit_ok(1000, 1700) is True or risk.profit(1000, 1700) < 500   # на дешёвых картах 500 монет не набрать
    b = risk.max_buy(10000)
    assert risk.profit_ok(b, 10000) and not risk.profit_ok(b + 600, 10000)


def test_forbidden_hours(moscow):
    assert risk.hours_veto(moscow(2026, 10, 21, 19, 30))              # 19–21 МСК
    assert risk.hours_veto(moscow(2026, 10, 21, 20, 59))
    assert risk.hours_veto(moscow(2026, 10, 21, 21, 0)) is None
    assert risk.hours_veto(moscow(2026, 10, 21, 14)) is None
    assert risk.hours_veto(moscow(2026, 10, 23, 16, 0))               # пятница 16:00
    assert risk.hours_veto(moscow(2026, 10, 23, 15, 59)) is None
    assert risk.hours_veto(moscow(2026, 10, 22, 16, 0)) is None       # четверг 16:00 можно
    assert risk.hours_veto(moscow(2026, 10, 21, 3))                   # ночь


def test_new_promo_window(db, moscow):
    now = moscow(2026, 10, 21, 14)
    db.x("INSERT INTO facts (key,kind,src,ts,text) VALUES ('promo:x','promo','robtrade',?,'Промо Хэллоуин уже в игре')", (now - 3 * 3600,))
    assert risk.promo_veto(db, now)
    assert risk.promo_veto(db, now + 4 * 3600) is None               # прошло 7 часов
    db.x("DELETE FROM facts")
    db.x("INSERT INTO facts (key,kind,src,ts,text) VALUES ('promo:y','promo','robtrade',?,'Скоро новое промо?')", (now - 1 * 3600,))
    assert risk.promo_veto(db, now) is None                          # слух, а не старт


def test_liquidity_veto(db, moscow):
    now = moscow(2026, 10, 21, 14)
    base = {"name": "Игрок", "buy": 8000, "sell": 10000, "kind": "flip", "ea_id": 1}
    assert risk.check(db, {**base, "liq": None}, now) is None
    assert "ликвидность" in risk.check(db, {**base, "liq": 3}, now)


# ── рынок (телефон) ──────────────────────────────────────────────────────────
def test_market_reads_phone_feed(db):
    now = time.time()
    db.x("INSERT INTO cards VALUES (7,'Haaland',91,'Haaland','Rare',?)", (now,))
    phone = {"ts": now - 60, "hang": [{"ea_id": 7, "name": "Haaland", "rating": 91, "price": 80000, "avgp": 100000, "sales24h": 40, "lots": [80000, 90000], "ts": now - 120}]}
    ops = market.opinions(db, phone, {"prices": {7: 82000}}, now)
    assert len(ops) == 1 and ops[0].kind == "flip" and ops[0].sell_ref == 100000 and ops[0].liq == 40
    assert market.opinions(db, {**phone, "ts": now - 3 * 3600}, None, now) == []        # данные телефона устарели
    assert market.opinions(db, None, None, now) == []
    cheap = {"ts": now, "hang": [{"ea_id": 7, "price": 99000, "avgp": 100000, "ts": now}]}
    assert market.opinions(db, cheap, None, now) == []                                 # запас меньше налога


# ── трейдер ──────────────────────────────────────────────────────────────────
def op(dept, ea=7, strength=0.7, buy=None, sell=None, kind="flip", srcs=None, liq=None, rating=91, name="Haaland"):
    return Opinion(dept, ea, name, "Золотая редкая", rating, strength, kind, "довод отдела", srcs=srcs or [], buy_max=buy, sell_ref=sell, liq=liq)


GG = {"prices": {7: 80000}}


def test_two_depts_required(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, why = trader.build(db, "c7", [op("market", sell=100000, buy=80000)], GG, now)
    assert sig is None and "двух отделов" in why
    sig, why = trader.build(db, "c7", [op("market", sell=100000, buy=80000), op("intel", sell=100000, srcs=["robtrade"])], GG, now)
    assert sig and sig["type"] == "flip" and sig["buy_max"] >= 80000 and sig["buy_max"] <= risk.max_buy(100000) and sig["score"] >= 60
    assert sig["profit"] == risk.profit(sig["buy_max"], sig["sell_min"]) >= 500
    assert sig["depts"] == ["intel", "market"]


def test_source_inside_opinion_counts_as_second_dept(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, _ = trader.build(db, "c7", [op("sbc", kind="fodder", sell=100000, buy=80000, srcs=["robtrade"], strength=0.8)], GG, now)
    assert sig and sig["type"] == "fodder" and set(sig["depts"]) == {"sbc", "intel"}


def test_buy_price_never_below_current(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, why = trader.build(db, "c7", [op("market", sell=100000, buy=70000), op("intel", sell=100000, srcs=["a"])], GG, now)
    assert sig is None and "выше допустимой" in why                   # карта уже стоит 80000 при потолке ~72000


def test_low_score_and_risk_veto(db, moscow):
    now = moscow(2026, 10, 21, 14)
    weak = [op("market", sell=100000, buy=80000, strength=0.05), op("intel", sell=100000, srcs=["a"], strength=0.05)]
    sig, why = trader.build(db, "c7", weak, GG, now)
    assert sig is None and "оценка" in why
    sig, why = trader.build(db, "c7", [op("market", sell=100000, buy=80000), op("intel", sell=100000, srcs=["a"])], GG, moscow(2026, 10, 21, 20))
    assert sig is None and "риск-менеджер" in why


def test_message_format(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, _ = trader.build(db, "c7", [op("market", sell=100000, buy=80000, liq=40), op("intel", sell=100000, srcs=["robtrade"])], GG, now)
    t = trader.text_of(sig, 5)
    import re
    assert ru.no_latin(re.sub(r"<[^>]+>", "", t).replace("Haaland", ""))                       # кроме имени игрока латиницы нет
    for part in ("Купить до", "Продать от", "Прибыль после налога", "Действует до", "Уверенность", "Почему", "быстрый перекуп", "Haaland"):
        assert part in t
    assert 2 <= t.count("\n• ") <= 4
    assert [b[1] for row in trader.buttons(5) for b in row] == ["of:b:5", "of:n:5", "of:s:5"]


def test_daily_limit_and_dedup(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sent = []
    def send(text, buttons=None):
        sent.append(text)
        return len(sent)
    ops = []
    for i in range(12):
        db.x("INSERT INTO cards VALUES (?,?,?,?,?,?)", (100 + i, f"P{i}", 88, "", "Rare", now))
        ops += [op("market", ea=100 + i, sell=100000, buy=80000, name=f"P{i}"), op("intel", ea=100 + i, sell=100000, srcs=["a"], name=f"P{i}")]
    gg = {"prices": {100 + i: 80000 for i in range(12)}}
    r = trader.run(db, ops, lambda force: gg, now, send=send, max_new=20)
    assert r["sent"] == 10 and len(sent) == 10                        # не больше 10 в сутки
    r2 = trader.run(db, ops, lambda force: gg, now + 600, send=send, max_new=20)
    assert r2["sent"] == 0
    # повтор той же карты в течение 6 часов не отправляется даже на следующий день утром?  — через 7 часов уже можно (лимит дня сброшен)
    r3 = trader.run(db, ops, lambda force: gg, now + 24 * 3600, send=send, max_new=20)
    assert r3["sent"] == 10


def test_price_recheck_before_send(db, moscow):
    now = moscow(2026, 10, 21, 14)
    ops = [op("market", sell=100000, buy=80000), op("intel", sell=100000, srcs=["a"])]
    calls = []
    def fetch(force):
        calls.append(force)
        return {"prices": {7: 95000}} if force else GG                   # после свежей загрузки цена выросла
    r = trader.run(db, ops, fetch, now, send=lambda *a: 1)
    assert r["sent"] == 0 and calls == [False, True]


def test_unsent_when_telegram_down(db, moscow):
    now = moscow(2026, 10, 21, 14)
    ops = [op("market", sell=100000, buy=80000), op("intel", sell=100000, srcs=["a"])]
    trader.run(db, ops, lambda f: GG, now, send=lambda *a: None)
    assert db.one("SELECT status FROM signals")["status"] == "unsent"


# ── бухгалтер ────────────────────────────────────────────────────────────────
def mk_signal(db, now, **kw):
    d = dict(ts=now, key="c7", type="flip", ea_id=7, name="Haaland", version="v", rating=91, buy_max=80000, sell_min=100000, profit=15000, expires=now + 3600,
             score=70, conf=0.7, depts='["intel","market"]', srcs='["robtrade"]', why="[]", price_at=79000, status="sent")
    d.update(kw)
    return db.x("INSERT INTO signals (" + ",".join(d) + ") VALUES (" + ",".join("?" * len(d)) + ")", tuple(d.values()))


def test_ledger_idempotent_and_profit(db):
    now = time.time()
    sid = mk_signal(db, now - 7200)
    led = {"entries": [{"id": "a", "sid": sid, "action": "bought", "price": 79000, "ts": now}, {"id": "b", "sid": sid, "action": "sold", "price": 100000, "ts": now}, {"id": "zzz", "sid": sid, "action": "weird"}]}
    assert accountant.ingest_ledger(db, led) == 2
    assert accountant.ingest_ledger(db, led) == 0
    o = accountant.outcome(db, db.one("SELECT * FROM signals"))
    assert o["found"] is True and o["sold_profit"] == 95000 - 79000
    st = accountant.stats(db, now)
    assert st["sold"] == 1 and st["profit"] == 16000


def test_price_check_and_dept_stats(db):
    now = time.time()
    sid = mk_signal(db, now - 900)
    assert accountant.check_prices(db, {"prices": {7: 79500}}, now) == 1
    accountant.update_dept_stats(db, now)
    accountant.update_dept_stats(db, now)                              # второй вызов не удваивает
    r = {x["dept"]: (x["good"], x["votes"]) for x in db.q("SELECT * FROM dept_stats")}
    assert r == {"intel": (1, 1), "market": (1, 1)}
    s2 = mk_signal(db, now - 900, key="c8")
    accountant.ingest_ledger(db, {"entries": [{"id": "n", "sid": s2, "action": "notfound", "ts": now}]})
    accountant.update_dept_stats(db, now)
    r = {x["dept"]: (x["good"], x["votes"]) for x in db.q("SELECT * FROM dept_stats")}
    assert r["market"] == (1, 2)                                       # «Не нашёл по цене» = промах отдела


def test_source_rating_bayes_and_demotion(db, moscow, monkeypatch):
    srcs = [{"id": "bad", "kind": "telegram", "rating": 0.5, "title": "Плохой канал"}, {"id": "good", "kind": "telegram", "rating": 0.5, "title": "Хороший канал"}]
    intel.ensure_stats(db, srcs)
    monkeypatch.setattr(intel, "load_sources", lambda *a, **k: srcs)
    db.x("UPDATE src_stats SET checks=12, hits=1 WHERE src='bad'")
    db.x("UPDATE src_stats SET checks=12, hits=11 WHERE src='good'")
    accountant.update_ratings(db, srcs)
    rb = db.one("SELECT rating FROM src_stats WHERE src='bad'")["rating"]
    rg = db.one("SELECT rating FROM src_stats WHERE src='good'")["rating"]
    assert rb < 0.25 and rg > 0.8
    assert accountant.update_ratings(db, srcs) is None
    msgs = []
    now = moscow(2026, 10, 25, 21)                                    # воскресенье вечер
    text = accountant.sunday_council(db, srcs, None, now, send=lambda t, b=None: msgs.append(t))
    assert text and "Плохой канал" in text and "отключён" in text and len(msgs) == 1
    assert db.one("SELECT enabled FROM src_stats WHERE src='bad'")["enabled"] == 0
    assert accountant.sunday_council(db, srcs, None, now + 600, send=lambda *a: msgs.append(1)) is None   # раз в неделю
    assert accountant.sunday_council(db, srcs, None, moscow(2026, 10, 26, 21), send=lambda *a: 0) is None  # только по воскресеньям


def test_report_is_russian(db):
    now = time.time()
    mk_signal(db, now - 900)
    accountant.check_prices(db, {"prices": {7: 79500}}, now)
    t = accountant.report_text(db, now)
    import re
    assert ru.no_latin(re.sub(r"<[^>]+>", "", t))
    assert "Подсказок отправлено: 1" in t


def test_fact_outcomes_update_source_stats(db):
    now = time.time()
    intel.ensure_stats(db, [{"id": "robtrade", "kind": "telegram", "rating": 0.5}])
    db.x("INSERT INTO facts (key,kind,src,ts,price,ea_id) VALUES ('price:c7','sell','robtrade',?,50000,7)", (now - 30 * 3600,))
    db.x("INSERT INTO price_log VALUES (7, ?, 40000)", (now - 20 * 3600,))
    db.x("INSERT INTO price_log VALUES (7, ?, 49500)", (now - 5 * 3600,))
    accountant.update_facts(db, now)
    st = db.one("SELECT checks,hits FROM src_stats WHERE src='robtrade'")
    assert (st["checks"], st["hits"]) == (1, 1)


# ── нейросети и связь ────────────────────────────────────────────────────────
def test_router_falls_back_and_degrades():
    class Dead:
        def ask(self, role, payload, system=None):
            raise llm.LLMUnavailable("лимит")
    class Alive:
        def ask(self, role, payload, system=None):
            return {"ok": 1}
    r = llm.RoleRouter(gemini=Dead(), fallbacks=[Alive()])
    assert r.ask("extract", {"a": 1}) == {"ok": 1}
    r2 = llm.RoleRouter(gemini=Dead(), fallbacks=[Dead()])
    with pytest.raises(llm.LLMUnavailable):
        r2.ask("extract", {"a": 2})


def test_no_keys_means_rules_only(monkeypatch):
    for k in ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(llm.LLMError):
        llm.RoleRouter().ask("extract", {"x": 1})
    assert llm.video_summary("https://youtu.be/x") is None
    assert intel.extract_llm(llm.RoleRouter(), [{"uid": "a", "text": "слив"}]) == []


def test_openrouter_cap_exists():
    assert llm.OR_DAILY_CAP_USD == 0.20 and hasattr(llm, "or_cap_left")
    assert "openrouter" in llm.OpenAICompatBackend.PROVIDERS


def test_phone_link_roundtrip(monkeypatch):
    import base64
    monkeypatch.setenv("GH_TOKEN", "t")
    store = {}
    class S:
        def get(self, url, params=None, headers=None, timeout=0):
            p = url.split("/contents/")[-1]
            if "/contents/" not in url:
                return FakeResp(json.dumps({"object": {"sha": "abc"}}))
            if p in store:
                return FakeResp(json.dumps({"content": base64.b64encode(store[p]).decode(), "sha": "s1"}))
            return FakeResp("", 404)
        def put(self, url, headers=None, json=None, timeout=0):
            store[url.split("/contents/")[-1]] = base64.b64decode(json["content"])
            return FakeResp("", 201)
        post = get
    s = S()
    assert link.read_json("phone_feed.json", "phone", session=s) is None
    assert link.write_json("phone_feed.json", {"ts": 1, "hang": []}, "phone", session=s)
    assert link.read_json("phone_feed.json", "phone", session=s) == {"ts": 1, "hang": []}
    monkeypatch.delenv("GH_TOKEN")
    assert link.read_json("phone_feed.json", "phone", session=s) is None            # без токена — молча None
