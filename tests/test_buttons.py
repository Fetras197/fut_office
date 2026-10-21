import time

from office import accountant, buttons, trader
from tests.test_trader import GG, op


def mk(db, moscow):
    now = moscow(2026, 10, 21, 14)
    trader.run(db, [op("intel", sell=100000, buy=80000), op("football", kind="totw", sell=100000)], lambda f: GG, now, send=lambda *a: 11)
    return now, db.one("SELECT id FROM signals")["id"]


def cb(uid, data, user="42"):
    return {"update_id": uid, "callback_query": {"id": f"q{uid}", "from": {"id": int(user)}, "data": data}}


def msg(uid, text, chat="42"):
    return {"update_id": uid, "message": {"chat": {"id": int(chat)}, "text": text}}


def setup(monkeypatch):
    monkeypatch.setenv("TG_TOKEN", "t")
    monkeypatch.setenv("TG_CHAT", "42")
    said, calls = [], []
    return said, calls, (lambda t: said.append(t) or 1), (lambda m, **k: calls.append((m, k)) or {"ok": True})


def test_buy_notfound_and_sale_flow(db, moscow, monkeypatch):
    now, sid = mk(db, moscow)
    said, calls, say, call = setup(monkeypatch)
    ups = [cb(10, f"of:b:{sid}"), cb(11, f"of:s:{sid}"), msg(12, "52 000")]
    r = buttons.poll(db, now + 600, fetch=lambda off: ups if off == 0 else [], say=say, call=call)
    assert r == {"updates": 3, "recorded": 2}
    assert [(l["action"], l["price"]) for l in db.q("SELECT * FROM ledger ORDER BY id")] == [("bought", 0), ("sold", 52000)]
    assert db.kv_get("tg_offset") == 13 and any("answerCallbackQuery" == c[0] for c in calls)
    assert any("За сколько продали" in t for t in said) and any("продана за 52 000" in t for t in said)
    o = accountant.outcome(db, db.one("SELECT * FROM signals"))
    assert o["found"] and o["sold_profit"] == 49400 - db.one("SELECT buy_max FROM signals")["buy_max"]
    # повтор тех же обновлений ничего не удваивает
    r2 = buttons.poll(db, now + 700, fetch=lambda off: [cb(10, f"of:b:{sid}")], say=say, call=call)
    assert r2["recorded"] == 0


def test_notfound_and_foreign_users_ignored(db, moscow, monkeypatch):
    now, sid = mk(db, moscow)
    said, calls, say, call = setup(monkeypatch)
    ups = [cb(20, f"of:n:{sid}"), cb(21, f"of:b:{sid}", user="999"), msg(22, "100000", chat="999"), cb(23, "of:b:9999"), cb(24, "other")]
    r = buttons.poll(db, now, fetch=lambda off: ups, say=say, call=call)
    assert r["recorded"] == 1 and [l["action"] for l in db.q("SELECT * FROM ledger")] == ["notfound"]


def test_sale_price_needs_pending_and_sane_value(db, moscow, monkeypatch):
    now, sid = mk(db, moscow)
    said, calls, say, call = setup(monkeypatch)
    assert buttons.poll(db, now, fetch=lambda off: [msg(30, "52000")], say=say, call=call)["recorded"] == 0      # без «Продал за…» число не принимаем
    buttons.poll(db, now, fetch=lambda off: [cb(31, f"of:s:{sid}"), msg(32, "12")], say=say, call=call)
    assert db.q("SELECT * FROM ledger") == [] and any("не похожа" in t for t in said)
    assert buttons.poll(db, now + 7 * 3600, fetch=lambda off: [msg(33, "52000")], say=say, call=call)["recorded"] == 0   # ожидание просрочено


def test_no_credentials_or_network_error(db, monkeypatch):
    import requests
    monkeypatch.delenv("TG_TOKEN", raising=False)
    assert buttons.poll(db, time.time(), fetch=lambda o: [cb(1, "of:b:1")])["updates"] == 0
    monkeypatch.setenv("TG_TOKEN", "t"); monkeypatch.setenv("TG_CHAT", "42")
    def boom(off):
        raise requests.ConnectionError()
    assert buttons.poll(db, time.time(), fetch=boom)["updates"] == 0
    assert buttons.poll(db, time.time(), fetch=lambda o: None)["updates"] == 0
