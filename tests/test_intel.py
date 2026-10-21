import json
import time

from office import intel
from tests.conftest import FakeResp, FakeSession

TME = '''<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message" data-post="robtrade/8103">
<div class="tgme_widget_message_text js-message_text" dir="auto">🔥 Активный трейд!<br/>• Stanway &gt;49000<br/>• Vitinha &gt;45000</div>
<div class="tgme_widget_message_footer"><a class="tgme_widget_message_date" href="x"><time datetime="2026-10-20T09:12:18+00:00" class="time">09:12</time></a></div></div></div>'''


def test_parse_tme():
    p = intel.parse_tme(TME, "robtrade")
    assert len(p) == 1 and p[0]["uid"] == "robtrade/8103" and "Stanway >49000" in p[0]["text"]


def test_parse_youtube_and_reddit():
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 3600))
    xml = f'<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry><yt:videoId>AbC</yt:videoId><title>Слив TOTW</title><published>{now}</published></entry></feed>'
    v = intel.parse_youtube(xml)
    assert v[0]["uid"] == "yt:AbC" and v[0]["text"] == "Слив TOTW"
    assert intel.parse_youtube(xml, max_age_h=0.1) == []
    r = intel.parse_reddit(json.dumps({"data": {"children": [{"data": {"id": "x1", "title": "t", "selftext": "b", "permalink": "/r/a/", "created_utc": 1.0}}]}}))
    assert r[0]["uid"] == "r:x1"
    assert intel.parse_reddit("<html>blocked") == []


def test_headlines():
    page = '<a href="/news/ea-fc-27-totw-6-predictions/">Team of the Week 6 predictions: who makes it into the squad</a><a href="/about">короткая</a>'
    h = intel.parse_headlines(page, "https://x.test")
    assert len(h) == 1 and h[0]["url"].startswith("https://x.test/news/")


def test_rules_price_lines():
    f = intel.extract_rules("📊 К чему присмотреться:\n• Stanway >49000\n• Joan Garcia > 36к\n• Eric Garcia ≤ 12 000")
    by = {x["player"]: x for x in f}
    assert by["Stanway"]["action"] == "sell_above" and by["Stanway"]["price"] == 49000
    assert by["Joan Garcia"]["price"] == 36000
    assert by["Eric Garcia"]["action"] == "buy_below" and by["Eric Garcia"]["price"] == 12000


def test_rules_fodder_and_leak():
    f = intel.extract_rules("Ориентиры по рейтингам:\n• 84 — >800\n• 85 — > 2100\nОкно покупки: сейчас")
    assert [(x["rating"], x["price"]) for x in f if x["kind"] == "fodder"] == [(84, 800), (85, 2100)]
    g = intel.extract_rules("✔️ Выйдут в СБЧ/ЗАДАНИЯХ\n• Woltemade 🇩🇪\n• Mor 🇹🇷")
    assert {x["player"] for x in g} == {"Woltemade", "Mor"} and g[0]["kind"] == "sbc"


def test_rules_ignore_noise():
    assert intel.extract_rules("Розыгрыш 150к монет, подписывайтесь") == []
    assert intel.extract_rules("Цена 5 — 3")[:0] == []


def test_validate_llm_strict():
    ok = {"facts": [{"id": "a", "kind": "sell", "player": "Mbappé", "price": 50000, "action": "sell", "note": "x"}]}
    assert intel.validate_llm(ok, {"a"})[0]["action"] == "sell_above"
    assert intel.validate_llm(ok, {"zzz"}) == []                      # чужой id
    bad_price = {"facts": [{"id": "a", "kind": "sell", "player": "X Y", "price": 5, "action": "sell"}]}
    assert intel.validate_llm(bad_price, {"a"}) == []                 # цена нереальна
    bad_kind = {"facts": [{"id": "a", "kind": "pizza", "player": "X Y"}]}
    assert intel.validate_llm(bad_kind, {"a"}) == []
    inj = {"facts": [{"id": "a", "kind": "leak", "player": "Robert'); DROP TABLE", "price": 0}]}
    assert intel.validate_llm(inj, {"a"})[0]["player"] == ""            # странное имя обнуляется
    assert intel.validate_llm("мусор", {"a"}) == [] and intel.validate_llm({"facts": "x"}, {"a"}) == []


def test_validate_llm_buy_with_sell_price():
    obj = {"facts": [{"id": "a", "kind": "buy", "player": "Mac Allister", "price": 12000, "sell_price": 15500, "action": "buy"}]}
    out = intel.validate_llm(obj, {"a"})
    assert [(f["kind"], f["action"], f["price"]) for f in out] == [("buy", "buy_below", 12000), ("sell", "sell_above", 15500)]


def test_confidence_grows_with_sources():
    assert intel.confidence([0.5]) == 0.4
    assert intel.confidence([0.5, 0.5]) > intel.confidence([0.5])
    assert intel.confidence([0.5, 0.5, 0.5]) > 0.7
    assert intel.confidence([1, 1, 1, 1]) <= 0.95


def test_poll_interval_and_dedup(db):
    s = {"id": "robtrade", "kind": "telegram", "handle": "robtrade", "interval_min": 30}
    intel.ensure_stats(db, [s])
    page = TME.replace("2026-10-20T09:12:18", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 600)))
    sess = FakeSession({"t.me/s/robtrade": page})
    now = time.time()
    assert intel.poll(db, s, now, sess) == 1
    assert intel.poll(db, s, now + 60, sess) == -1                 # рано
    assert intel.poll(db, s, now + 31 * 60, sess) == 0              # дубль не добавлен
    assert db.one("SELECT COUNT(*) n FROM raw")["n"] == 1


def test_source_failure_is_silent(db):
    s = {"id": "reddit_easportsfc", "kind": "reddit", "url": "https://www.reddit.com/r/x/new.json", "interval_min": 60}
    intel.ensure_stats(db, [s])
    sess = FakeSession({"reddit.com": FakeResp("blocked", 403)})
    assert intel.poll(db, s, time.time(), sess) == 0
    assert db.one("SELECT fails FROM src_stats WHERE src='reddit_easportsfc'")["fails"] == 1


def test_disabled_source_skipped(db):
    s = {"id": "futreveal", "kind": "telegram", "handle": "futreveal", "enabled": False}
    intel.ensure_stats(db, [s])
    assert intel.poll(db, s, time.time(), FakeSession({})) == -1


def test_merge_and_opinions(db, monkeypatch):
    now = time.time()
    db.x("INSERT INTO cards VALUES (1,'Stanway',83,'Stanway','Rare',?)", (now,))
    srcs = [{"id": "robtrade", "kind": "telegram", "rating": 0.55, "title": "Роб Трейд"}, {"id": "sherman_s", "kind": "telegram", "rating": 0.55, "title": "Шерман"}]
    intel.ensure_stats(db, srcs)
    monkeypatch.setattr(intel, "load_sources", lambda *a, **k: srcs)
    for src in ("robtrade", "sherman_s"):
        db.x("INSERT INTO facts (key,kind,player,version,rating,action,price,src,ts,text,ea_id) VALUES ('sell:c1','sell','Stanway','',0,'sell_above',49000,?,?,'x|flip',1)", (src, now))
    ev = intel.merge(db, now)
    assert len(ev) == 1 and ev[0]["n"] == 2 and ev[0]["conf"] > 0.55 and ev[0]["flip"]
    ops = intel.opinions(db, now)
    assert ops[0].sell_ref == 49000 and ops[0].kind == "flip" and "Роб Трейд" in ops[0].reason


def test_process_raw_dedups_same_fact(db, monkeypatch):
    now = time.time()
    intel.ensure_stats(db, [{"id": "robtrade", "kind": "telegram", "rating": 0.5}])
    monkeypatch.setattr(intel.cards, "resolve", lambda *a, **k: None)
    db.x("INSERT INTO raw (uid,src,ts,url,text) VALUES ('robtrade|1','robtrade',?, 'u', '• Stanway >49000\n• Vitinha >45000')", (now,))
    st = intel.process_raw(db, None, now)
    assert st["rules"] == 2
    assert db.one("SELECT COUNT(*) n FROM facts")["n"] == 2
    assert db.one("SELECT parsed FROM raw")["parsed"] == 1


def test_sources_yaml_has_required():
    ids = {s["id"]: s for s in intel.load_sources()}
    for h in ("robtrade", "sherman_s", "AJAXFA", "worldeafc", "jeepfifa"):
        assert ids[h]["kind"] == "telegram"
    assert ids["teddy127"]["channel_id"] == "UCZvo1h28WyGeWz5ccrJupbQ" and ids["prorecoil"]["channel_id"] == "UCZWklCuZj8F3EfrtTisx__g"
    assert all(30 <= s.get("interval_min", 30) <= 60 for s in ids.values())
    assert all("rating" in s for s in ids.values())
    assert not any(s["kind"] == "x" or "twitter" in json.dumps(s).lower() for s in ids.values())
    kinds = {s["kind"] for s in ids.values()}
    assert {"telegram", "youtube", "site", "reddit"} <= kinds


def test_video_waits_for_summary_then_parsed(db, monkeypatch):
    now = time.time()
    intel.ensure_stats(db, [{"id": "teddy127", "kind": "youtube", "rating": 0.5}])
    monkeypatch.setattr(intel.cards, "resolve", lambda *a, **k: None)
    db.x("INSERT INTO raw (uid,src,ts,url,text) VALUES ('teddy127|yt:AbC','teddy127',?,'https://youtu.be/AbC','Слив TOTW')", (now,))
    intel.process_raw(db, None, now)
    assert db.one("SELECT parsed FROM raw")["parsed"] == 0            # без пересказа не трогаем
    monkeypatch.setattr(intel.llm, "video_summary", lambda url: "• Mac Allister до 850")
    intel._summarize_videos(db, {"id": "teddy127"}, now)
    intel.process_raw(db, None, now)
    assert db.one("SELECT COUNT(*) n FROM facts WHERE kind='buy'")["n"] == 1


def test_robtrade_upto_list_is_buy_cap():
    f = intel.extract_rules("📊 Список целей и цены:\n• Mac Allister — до 850\n• Gabriel — до 18,250")
    assert [(x["player"], x["action"], x["price"]) for x in f] == [("Mac Allister", "buy_below", 850), ("Gabriel", "buy_below", 18250)]


def test_video_summary_at_most_every_3_hours(db, monkeypatch):
    now = time.time()
    s = {"id": "teddy127", "kind": "youtube", "channel_id": "x", "rating": 0.5}
    intel.ensure_stats(db, [s])
    monkeypatch.setattr(intel, "fetch_items", lambda *a, **k: [{"uid": "yt:%d" % i, "ts": now - 60, "url": "https://youtu.be/%d" % i, "text": "t%d" % i} for i in range(3)])
    calls = []
    monkeypatch.setattr(intel.llm, "video_summary", lambda url: calls.append(url) or "• Mac Allister до 850")
    intel.run(db, None, now, srcs=[s])
    assert len(calls) == 1
    intel.run(db, None, now + 3600, srcs=[{**s, "interval_min": 0}])
    assert len(calls) == 1                                           # через час — нельзя
    intel.run(db, None, now + 3 * 3600 + 60, srcs=[{**s, "interval_min": 0}])
    assert len(calls) == 2
