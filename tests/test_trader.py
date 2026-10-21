import json
import re
import time

from office import accountant, market, risk, ru, sbc, trader
from office.model import Opinion


def op(dept, ea=7, strength=0.7, buy=None, sell=None, kind="channel", srcs=None, rating=91, name="Haaland", ttl=24.0):
    return Opinion(dept, ea, name, "Золотая редкая", rating, strength, kind, "довод отдела", srcs=srcs or [], buy_max=buy, sell_ref=sell, ttl_h=ttl)


GG = {"prices": {7: 80000}}


def card(db, now, ea=7):
    db.x("INSERT OR IGNORE INTO cards VALUES (?,?,?,?,?,?)", (ea, "Haaland", 91, "Haaland", "Rare", now))


def test_two_depts_required_for_real_signal(db, moscow):
    now = moscow(2026, 10, 21, 14)
    one = [op("intel", sell=100000, buy=80000, srcs=["robtrade"], kind="channel")]
    sig, why = trader.build(db, "c7", one, GG, now)
    assert sig is None and "двух отделов" in why
    sig, _ = trader.build(db, "c7", one, GG, now, allow_single=True)
    assert sig and sig["real"] is False and sig["depts"] == ["intel"]        # пробный кандидат
    two = one + [op("football", kind="totw", sell=100000)]
    sig, _ = trader.build(db, "c7", two, GG, now)
    assert sig and sig["real"] and sig["depts"] == ["football", "intel"] and sig["type"] == "totw" and sig["score"] >= 60
    assert sig["buy_max"] >= 80000 and sig["buy_max"] <= risk.max_buy(100000) and sig["profit"] == risk.profit(sig["buy_max"], sig["sell_min"]) >= 500


def test_source_inside_opinion_counts_as_second_dept(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, _ = trader.build(db, "c7", [op("sbc", kind="fodder", sell=100000, buy=80000, srcs=["robtrade"], strength=0.8)], GG, now)
    assert sig and sig["type"] == "fodder" and set(sig["depts"]) == {"sbc", "intel"}


def test_type_priority_and_grouping():
    ops = [op("sbc", kind="cycle", rating=86), op("sbc", kind="fodder", rating=86), op("intel", ea=9, kind="channel")]
    g = trader.group(ops)
    assert set(g) == {"fodder:86", "c9"}


def test_buy_price_never_below_current(db, moscow):
    now = moscow(2026, 10, 21, 14)
    ops = [op("intel", sell=100000, buy=70000, srcs=["a"]), op("football", kind="totw", sell=100000)]
    sig, why = trader.build(db, "c7", ops, GG, now)
    assert sig is None and "выше допустимой" in why


def test_low_score_and_risk_veto(db, moscow):
    now = moscow(2026, 10, 21, 14)
    weak = [op("intel", sell=100000, buy=80000, strength=0.05), op("football", kind="totw", sell=100000, strength=0.05)]
    sig, why = trader.build(db, "c7", weak, GG, now)
    assert sig is None and "оценка" in why
    good = [op("intel", sell=100000, buy=80000), op("football", kind="totw", sell=100000)]
    sig, why = trader.build(db, "c7", good, GG, moscow(2026, 10, 21, 20))
    assert sig is None and "риск-менеджер" in why
    sig, _ = trader.build(db, "c7", good, GG, moscow(2026, 10, 21, 20), allow_single=True)
    assert sig is None                                                       # два отдела — запретные часы действуют
    one = [op("intel", sell=100000, buy=80000)]
    sig, _ = trader.build(db, "c7", one, GG, moscow(2026, 10, 21, 20), allow_single=True)
    assert sig and not sig["real"]                                           # пробному кандидату часы не мешают


def test_market_trend_vetoes_falling_card(db, moscow):
    now = moscow(2026, 10, 21, 14)
    db.x("INSERT INTO price_log VALUES (7, ?, 100000)", (now - 24 * 3600,))
    m = market.check(db, GG, 7, now)
    assert m["cur"] == 80000 and round(m["day"], 2) == -0.2 and market.veto(m)
    ops = [op("intel", sell=120000, buy=80000), op("football", kind="totw", sell=120000)]
    sig, why = trader.build(db, "c7", ops, GG, now)
    assert sig is None and "аналитик рынка" in why
    assert market.check(db, {"prices": {}}, 7, now) is None and market.veto(None) is None


def test_market_line_in_why(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sig, _ = trader.build(db, "c7", [op("intel", sell=100000, buy=80000), op("football", kind="totw", sell=100000)], GG, now)
    assert sig["why"][-1].startswith("Аналитик рынка: сейчас минимум 80 000") and 2 <= len(sig["why"]) <= 4


def test_message_format_all_types(db, moscow):
    now = moscow(2026, 10, 21, 14)
    for kind, label in (("channel", "сигнал канала"), ("totw", "команду недели"), ("fodder", "корм под наборы")):
        sig, _ = trader.build(db, "c7", [op("intel", sell=100000, buy=80000, srcs=["a"], kind=kind), op("football", kind=kind, sell=100000)], GG, now)
        t = trader.text_of(sig, 5)
        assert ru.no_latin(re.sub(r"<[^>]+>", "", t).replace("Haaland", ""))
        for part in ("Купить до", "Продать от", "Прибыль после налога 5%", "Срок сделки", "действует до", "Уверенность", "Почему", label):
            assert part in t, (kind, part)
        assert 2 <= t.count("\n• ") <= 4
    assert [b[1] for row in trader.buttons(5) for b in row] == ["of:b:5", "of:n:5", "of:s:5"]


def run_ops(n=12, strength=0.7):
    ops = []
    for i in range(n):
        ops += [op("intel", ea=100 + i, sell=100000, buy=80000, srcs=["a"], name=f"P{i}", strength=strength), op("football", ea=100 + i, kind="totw", sell=100000, name=f"P{i}", strength=strength)]
    return ops, {"prices": {100 + i: 80000 for i in range(n)}}


def test_daily_limit_dedup_and_virtual_record(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sent = []
    ops, gg = run_ops()
    r = trader.run(db, ops, lambda f: gg, now, send=lambda t, b=None: sent.append(t) or len(sent), max_new=20)
    assert r["sent"] == 10 and len(sent) == 10                                # не больше 10 в сутки
    assert db.one("SELECT COUNT(*) n FROM trials WHERE mode='signal'")["n"] == 10
    assert trader.run(db, ops, lambda f: gg, now + 600, send=lambda *a: 1, max_new=20)["sent"] == 0
    assert trader.run(db, ops, lambda f: gg, now + 24 * 3600, send=lambda *a: 1, max_new=20)["sent"] == 10


def test_single_dept_goes_to_trials_not_telegram(db, moscow):
    now = moscow(2026, 10, 21, 14)
    sent = []
    ops = [op("intel", ea=7, sell=100000, buy=80000, srcs=[])]
    r = trader.run(db, ops, lambda f: GG, now, send=lambda *a: sent.append(1) or 1)
    assert r["sent"] == 0 and r["trials"] == 1 and not sent
    t = db.one("SELECT * FROM trials")
    assert t["mode"] == "trial" and json.loads(t["depts"]) == ["intel"] and t["price_at"] == 80000
    assert trader.run(db, ops, lambda f: GG, now + 3600, send=lambda *a: 1)["trials"] == 0     # не дублируем в течение суток


def test_price_recheck_before_send(db, moscow):
    now = moscow(2026, 10, 21, 14)
    ops = [op("intel", sell=100000, buy=80000), op("football", kind="totw", sell=100000)]
    calls = []
    def fetch(force):
        calls.append(force)
        return {"prices": {7: 95000}} if force else GG
    assert trader.run(db, ops, fetch, now, send=lambda *a: 1)["sent"] == 0 and calls == [False, True]


def test_unsent_when_telegram_down(db, moscow):
    now = moscow(2026, 10, 21, 14)
    trader.run(db, [op("intel", sell=100000, buy=80000), op("football", kind="totw", sell=100000)], lambda f: GG, now, send=lambda *a: None)
    assert db.one("SELECT status FROM signals")["status"] == "unsent"


# ── виртуальные проверки, отчёт 21:00 ───────────────────────────────────────
def mk_trial(db, ts, depts, typ="channel", mode="trial", price_at=80000, sell=100000, track=7):
    return db.x("INSERT INTO trials (ts,key,mode,type,track_id,ea_id,name,rating,buy_max,sell_min,price_at,depts,score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ts, f"k{ts}{depts}", mode, typ, track, track, "X", 90, price_at, sell, price_at, json.dumps(depts), 70))


def test_virtual_check_24_72(db):
    now = time.time()
    t0 = now - 80 * 3600
    mk_trial(db, t0, ["intel"])                                         # вырос
    mk_trial(db, t0 + 1, ["football"], typ="totw")                       # упал
    db.x("INSERT INTO price_log VALUES (7, ?, 90000)", (t0 + 24 * 3600,))
    db.x("INSERT INTO price_log VALUES (7, ?, 101000)", (t0 + 50 * 3600,))
    db.x("INSERT INTO price_log VALUES (7, ?, 99000)", (t0 + 72 * 3600,))
    assert accountant.verify_trials(db, None, now) >= 4
    rows = db.q("SELECT * FROM trials ORDER BY id")
    assert rows[0]["p24"] == 90000 and rows[0]["p72"] == 99000 and rows[0]["hi72"] == 101000 and rows[0]["done"] == 1
    assert accountant.virtual_profit(rows[0], 24) == 85500 - 80000
    assert accountant.virtual_profit(rows[0], 72) == 94050 - 80000


def test_virtual_stats_by_dept_and_type(db):
    now = time.time()
    t0 = now - 30 * 3600
    for i, (d, typ, p) in enumerate((("intel", "channel", 90000), ("intel", "channel", 70000), ("football", "totw", 95000))):
        tid = mk_trial(db, t0 + i, [d], typ=typ, track=10 + i)
        db.x("UPDATE trials SET p24=? WHERE id=?", (p, tid))
    vs = accountant.virtual_stats(db, now, 7, 24)
    assert vs["dept"] == {"intel": (1, 2), "football": (1, 1)} and vs["type"]["channel"] == (1, 2) and vs["mode"]["trial"] == (2, 3)
    assert accountant.best_dept(vs, min_n=1)[2] == "football" and accountant.best_dept(vs, min_n=3) is None


def test_evening_report_once_at_21(db, moscow):
    now = moscow(2026, 10, 21, 21, 5)
    t0 = now - 30 * 3600
    for i in range(3):
        tid = mk_trial(db, t0 + i, ["intel"], track=20 + i)
        db.x("UPDATE trials SET p24=? WHERE id=?", (95000, tid))
    accountant.bump_day(db, now, keys=["c1", "c2", "c2"])
    msgs = []
    assert accountant.evening_report(db, moscow(2026, 10, 21, 20, 30), send=lambda t, b=None: msgs.append(t)) is None
    t = accountant.evening_report(db, now, send=lambda t, b=None: msgs.append(t))
    assert t and len(msgs) == 1 and "Кандидатов рассмотрено: 2" in t and "Лучший отдел за неделю: разведка" in t and "Виртуальный результат" in t
    assert ru.no_latin(re.sub(r"<[^>]+>", "", t))
    assert accountant.evening_report(db, now + 600, send=lambda *a: msgs.append(1)) is None


# ── недельный цикл и утечки ──────────────────────────────────────────────────
def seed(db, rating, prices, n=6):
    for i in range(n):
        db.x("INSERT INTO cards VALUES (?,?,?,?,?,?)", (rating * 100 + i, f"Игрок{i}", rating, "", "Rare", time.time()))
        prices[rating * 100 + i] = 5000 + i * 100


def test_cycle_only_thursday_evening_friday(db, moscow):
    prices = {}
    seed(db, 87, prices)
    gg = {"prices": prices}
    assert sbc.cycle_opinions(db, gg, moscow(2026, 10, 22, 17)) == []                 # четверг до 18:00
    ops = sbc.cycle_opinions(db, gg, moscow(2026, 10, 22, 19))
    assert len(ops) == 1 and ops[0].kind == "cycle" and ops[0].rating == 87 and "с субботы по воскресенье" in ops[0].reason and ops[0].sell_ref > ops[0].buy_max
    assert 20 < ops[0].ttl_h < 22                                                      # до пятницы 16:00
    assert sbc.cycle_opinions(db, gg, moscow(2026, 10, 23, 15))
    assert sbc.cycle_opinions(db, gg, moscow(2026, 10, 23, 16)) == []
    assert sbc.cycle_opinions(db, gg, moscow(2026, 10, 25, 12)) == []
    assert ru.no_latin(ops[0].reason)


def test_leak_makes_fodder_opinion(db, moscow):
    from office import intel
    now = moscow(2026, 10, 21, 14)
    srcs = [{"id": "sherman_s", "kind": "telegram", "rating": 0.6, "title": "Шерман"}]
    intel.ensure_stats(db, srcs)
    f = intel.extract_rules("Слив: завтра в игре СБЧ апгрейд 86+")
    assert [(x["kind"], x["rating"], x["price"]) for x in f if x["kind"] == "fodder"] == [("fodder", 86, 0)]
    db.x("INSERT INTO facts (key,kind,rating,action,price,src,ts,text) VALUES ('fodder:r86','fodder',86,'none',0,'sherman_s',?,'утечка')", (now,))
    prices = {}
    seed(db, 86, prices)
    import unittest.mock as um
    with um.patch.object(intel, "load_sources", return_value=srcs):
        ops = sbc.opinions(db, {"prices": prices}, now)
    assert len(ops) == 1 and ops[0].kind == "fodder" and "по утечкам" in ops[0].reason and ops[0].srcs == ["sherman_s"] and ru.no_latin(ops[0].reason)
