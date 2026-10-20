import json
import time

from office import cards, cloud, config, db as dbm, football, http, intel, link, llm, sbc


class NoLLM:
    def ask(self, role, payload, system=None):
        raise llm.LLMUnavailable("нет ключей")


def setup_world(monkeypatch, db, now, price=80000):
    db.x("INSERT INTO cards VALUES (7,'Haaland',91,'Haaland','Rare',?)", (now,))
    phone = {"ts": now - 60, "hang": [{"ea_id": 7, "name": "Haaland", "rating": 91, "price": 80000, "avgp": 100000, "sales24h": 40, "lots": [80000], "ts": now - 60}]}
    monkeypatch.setattr(link, "read_json", lambda path, branch="phone", session=None: phone if path == "phone_feed.json" else None)
    monkeypatch.setattr(cards, "feed", lambda force=False: {"prices": {7: price}, "published": now, "fetched": now})
    monkeypatch.setattr(http, "get", lambda url, **kw: None)           # весь внешний мир недоступен
    monkeypatch.setattr(cards, "search", lambda *a, **k: [])
    monkeypatch.setattr(intel, "load_sources", lambda *a, **k: [{"id": "robtrade", "kind": "telegram", "handle": "robtrade", "rating": 0.55, "title": "Роб Трейд"}])
    intel.ensure_stats(db, intel.load_sources())
    db.x("INSERT INTO facts (key,kind,player,action,price,src,ts,text,ea_id) VALUES ('sell:c7','sell','Haaland','sell_above',100000,'robtrade',?,'ориентир|flip',7)", (now - 600,))


def test_full_pass_sends_signal_and_publishes(monkeypatch, db, moscow, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    now = moscow(2026, 10, 21, 14)
    setup_world(monkeypatch, db, now)
    sent = []
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda t, b=None: sent.append((t, b)) or 11, now=now)
    assert out["opinions"]["market"] == 1 and out["opinions"]["intel"] == 1
    assert out["trader"]["sent"] == 1 and len(sent) == 1
    text, buttons = sent[0]
    assert "Haaland" in text and "Купить до" in text and buttons[0][0][1].startswith("of:b:")
    row = db.one("SELECT * FROM signals")
    assert row["tg_msg"] == 11 and row["buy_max"] >= 80000 and set(json.loads(row["depts"])) == {"intel", "market"}
    pub = json.loads((tmp_path / "signals.json").read_text())
    assert pub["signals"][0]["name"] == "Haaland"


def test_pass_survives_department_failure(monkeypatch, db, moscow, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    now = moscow(2026, 10, 21, 14)
    setup_world(monkeypatch, db, now)
    def boom(*a, **k):
        raise RuntimeError("сломался")
    monkeypatch.setattr(football, "run", boom)
    monkeypatch.setattr(sbc, "refresh_sbc", boom)
    sent = []
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda t, b=None: sent.append(t) or 1, now=now)
    assert out["trader"]["sent"] == 1


def test_halt_stops_signals(monkeypatch, db, moscow, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setenv("OFFICE_HALT", "1")
    now = moscow(2026, 10, 21, 14)
    setup_world(monkeypatch, db, now)
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now)
    assert out["trader"] == {"halt": True} and db.one("SELECT COUNT(*) n FROM signals")["n"] == 0


def test_forbidden_hour_no_signal(monkeypatch, db, moscow, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    now = moscow(2026, 10, 21, 20)
    setup_world(monkeypatch, db, now)
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now)
    assert out["trader"]["sent"] == 0 and any("риск-менеджер" in k for k in out["trader"]["rejects"])


def test_second_pass_does_not_duplicate(monkeypatch, db, moscow, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    now = moscow(2026, 10, 21, 14)
    setup_world(monkeypatch, db, now)
    cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now)
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now + 1800)
    assert out["trader"]["sent"] == 0 and db.one("SELECT COUNT(*) n FROM signals")["n"] == 1
