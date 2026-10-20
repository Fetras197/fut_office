from office import cards, config, ru, http
from tests.conftest import FakeResp, FakeSession


def test_plural_and_coins():
    assert ru.plural(1, "монета", "монеты", "монет") == "монета"
    assert ru.plural(3, "монета", "монеты", "монет") == "монеты"
    assert ru.plural(11, "монета", "монеты", "монет") == "монет"
    assert ru.plural(22, "монета", "монеты", "монет") == "монеты"
    assert ru.coins(1500) == "1 500 монет"
    assert ru.coins(1001) == "1 001 монета"


def test_no_latin():
    assert ru.no_latin("Прибыль 500 монет")
    assert not ru.no_latin("Прибыль ROI")
    assert ru.no_latin("Игрок Mbappé: рост", allow=("Mbappé",))


def test_price_steps():
    assert cards.round_down(12345) == 12250
    assert cards.round_down(999) == 950
    assert cards.round_up(955) == 1000
    assert cards.round_down(55300) == 55000
    assert cards.prev_step(10000) == 9900


def test_feed_parsers():
    man = '{"_version":1,"_published_at":{"player-prices-ps5-dyn":1790000000},"player-prices-index":"abc","player-prices-ps5-dyn":"xyz"}'
    m = cards.parse_manifest(man)
    assert m["dyn"] == "xyz" and m["published"] == 1790000000.0
    ids = cards.parse_index('{"id0":100,"d":[5,5,10]}')
    assert ids == [100, 105, 110, 120]
    assert cards.parse_dyn('{"p":[0,1500,2000,3000]}', ids) == {105: 1500, 110: 2000, 120: 3000}
    import pytest
    with pytest.raises(ValueError):
        cards.parse_dyn('{"p":[1]}', ids)


def test_search_and_resolve(db):
    page = 'eaId:111,overall:87,commonName:"Bellerín",cardName:"Bellerín",rarityName:"Rare" eaId:112,overall:90,commonName:"Bellerín",cardName:"Bellerín",rarityName:"Team of the week"'
    found = cards.parse_search(page)
    assert [c["ea_id"] for c in found] == [111, 112]
    FakeSession({"fut.gg/players": page})
    orig = http.get
    http.get = lambda url, **kw: page
    try:
        assert cards.resolve(db, "Bellerin", "gold")["ea_id"] == 111
        assert cards.resolve(db, "Bellerin", "totw")["ea_id"] == 112
        assert cards.resolve(db, "Bellerin", "")["ea_id"] == 111
        assert cards.resolve(db, "Bellerin", "icon") is None
    finally:
        http.get = orig


def test_http_pause_on_403(monkeypatch):
    s = FakeSession({"example.test": FakeResp("", 403)})
    assert http.get("https://example.test/x", session=s, min_interval=0) is None
    assert http.paused("example.test")
    n = len(s.calls)
    assert http.get("https://example.test/y", session=s, min_interval=0) is None
    assert len(s.calls) == n          # во время паузы сеть не трогаем


def test_http_ok():
    s = FakeSession({"ok.test": "привет"})
    assert http.get("https://ok.test/", session=s, min_interval=0) == "привет"


def test_config_rules():
    assert config.TAX == 0.05 and config.MIN_PROFIT == 500 and config.MIN_SCORE == 60 and config.MAX_SIGNALS_PER_DAY == 10
