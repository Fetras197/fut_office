import json

from office import cards, cloud, config, football, http, intel, llm, sbc
from tests.test_trader import seed


class NoLLM:
    def ask(self, role, payload, system=None):
        raise llm.LLMUnavailable("нет ключей")


def world(monkeypatch, db, now, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    prices = {}
    seed(db, 87, prices)
    db.x("INSERT INTO cards VALUES (7,'Haaland',91,'Haaland','Rare',?)", (now,))
    prices[7] = 80000
    monkeypatch.setattr(cards, "feed", lambda force=False: {"prices": prices, "published": now, "fetched": now})
    monkeypatch.setattr(http, "get", lambda url, **kw: None)
    monkeypatch.setattr(cards, "search", lambda *a, **k: [])
    srcs = [{"id": "robtrade", "kind": "telegram", "handle": "robtrade", "rating": 0.55, "title": "Роб Трейд"}]
    monkeypatch.setattr(intel, "load_sources", lambda *a, **k: srcs)
    intel.ensure_stats(db, srcs)
    return prices


def test_thursday_cycle_signal_end_to_end(monkeypatch, db, moscow, tmp_path):
    now = moscow(2026, 10, 22, 19, 30)                      # четверг вечер, но 19–21 запрещено → сначала проверим, что час учтён
    world(monkeypatch, db, now, tmp_path)
    sent = []
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda t, b=None: sent.append(t) or 1, now=now)
    assert sent == [] and out["trader"]["sent"] == 0 and out["opinions"]["sbc"] >= 1       # мнение есть, но 19–21 — запрет
    assert out["trader"]["trials"] >= 0


def test_pass_with_two_depts_sends_and_records_virtual(monkeypatch, db, moscow, tmp_path):
    now = moscow(2026, 10, 23, 12)                          # пятница до 16:00, цикл активен
    world(monkeypatch, db, now, tmp_path)
    db.x("INSERT INTO facts (key,kind,rating,action,price,src,ts,text) VALUES ('fodder:r87','fodder',87,'none',0,'robtrade',?,'утечка')", (now,))
    sent = []
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda t, b=None: sent.append((t, b)) or 5, now=now)
    assert out["trader"]["sent"] == 1 and "корм" in sent[0][0] and sent[0][1][0][0][1].startswith("of:b:")
    assert {r["mode"] for r in db.q("SELECT mode FROM trials")} == {"signal"}
    assert json.loads((tmp_path / "signals.json").read_text())["signals"][0]["type"] == "fodder"


def test_pass_survives_failures_and_halt(monkeypatch, db, moscow, tmp_path):
    now = moscow(2026, 10, 23, 12)
    world(monkeypatch, db, now, tmp_path)
    def boom(*a, **k):
        raise RuntimeError("сломался")
    monkeypatch.setattr(football, "run", boom)
    monkeypatch.setattr(sbc, "refresh_sbc", boom)
    out = cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now)
    assert "trader" in out
    monkeypatch.setenv("OFFICE_HALT", "1")
    assert cloud.run_pass(db=db, router=NoLLM(), send=lambda *a: 1, now=now + 1800)["trader"] == {"halt": True}
