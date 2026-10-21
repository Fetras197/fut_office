"""Отдел 3. Аналитик наборов (SBC) и прогноза: какие наборы открыты, какие рейтинги корма нужны, цена корма в своём недельном диапазоне,
журнал прогнозов с проверкой через 1/3/7 дней и калибровкой ожидаемого роста по реально случившемуся."""
import json
import re
import statistics
import time

from . import cards, config, http, intel, ru
from .model import Opinion

FODDER_RATINGS = range(83, 90)
PRIOR_UP = {"fodder": 0.20, "totw": 0.20, "cycle": 0.15}      # стартовые ожидания роста (до накопления проверок); дальше заменяются медианой по проверенным прогнозам
CHECK_DAYS = (1, 3, 7)
SBC_URL = re.compile(r'href="/sbc/(players|upgrades|challenges|foundations|icons|heroes|[a-z\-]+)/27-(\d+)-([a-z0-9\-]+)/"')


def parse_sbc_list(page: str) -> list[dict]:
    out, seen = [], set()
    for m in SBC_URL.finditer(page):
        cat, num, slug = m.groups()
        if cat == "category" or num in seen:
            continue
        seen.add(num)
        out.append({"sid": num, "category": cat, "slug": slug, "url": f"https://www.fut.gg/sbc/{cat}/27-{num}-{slug}/"})
    return out


def sbc_ratings(slug: str, category: str) -> list[int]:
    """Рейтинги корма, который набор «съедает»: для улучшений «N+» — карты вокруг N."""
    if category != "upgrades":
        return []
    nums = [int(n) for n in re.findall(r"(?<![0-9])(7[5-9]|8[0-9]|9[0-3])(?![0-9])", slug)]
    if not nums:
        return []
    n = nums[0]
    return [r for r in range(n - 1, n + 3) if r in FODDER_RATINGS]


def describe(slug: str, category: str) -> str:
    """Название набора по-русски (по виду набора и числам в адресе)."""
    nums = re.findall(r"(?<![0-9])(7[5-9]|8[0-9]|9[0-3])(?![0-9])", slug)
    if category == "upgrades":
        return f"набор-улучшение с картами {nums[0]}+" if nums else "набор-улучшение"
    if "player-pick" in slug or category == "players":
        return "набор с выбором игрока"
    return "набор заданий" if category == "challenges" else "базовый набор"


def refresh_sbc(db, now: float) -> list[dict]:
    """Список наборов с fut.gg. Возвращает НОВЫЕ наборы."""
    page = http.get("https://www.fut.gg/sbc/", timeout=30, pause_sec=1800)
    if page is None:
        return []
    new = []
    for s in parse_sbc_list(page):
        if db.one("SELECT 1 FROM sbc WHERE sid=?", (s["sid"],)):
            db.x("UPDATE sbc SET last_seen=? WHERE sid=?", (now, s["sid"]))
            continue
        rt = sbc_ratings(s["slug"], s["category"])
        db.x("INSERT INTO sbc VALUES (?,?,?,?,?,?,?,?)", (s["sid"], s["slug"], 0, s["category"], now, now, json.dumps(rt), s["url"]))
        new.append({**s, "ratings": rt})
    return new


# ── индекс корма ─────────────────────────────────────────────────────────────
def crawl_cards(db, pages: int = 4) -> int:
    """Справочник карт по рейтингам 83–89 (fut.gg, 30 карт на страницу): понемногу за проход. Возвращает число страниц."""
    ptr = db.kv_get("crawl", {"r": 83, "p": 1, "done_ts": 0})
    done = 0
    if ptr["r"] > 89:
        if time.time() - ptr.get("done_ts", 0) < 2 * 86400:
            return 0
        ptr = {"r": 83, "p": 1, "done_ts": 0}
    for _ in range(pages):
        r, p = ptr["r"], ptr["p"]
        page = http.get(f"https://www.fut.gg/players/?overall__gte={r}&overall__lte={r}&page={p}", timeout=30, min_interval=1.5, pause_sec=1800)
        if page is None:
            break
        found = cards.parse_search(page)
        for c in found:
            db.x("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?)", (c["ea_id"], c["name"], c["rating"], c["card_name"], c["rarity"], time.time()))
        done += 1
        if len(found) < 30:
            ptr = {"r": r + 1, "p": 1, "done_ts": time.time()}
            if r + 1 > 89:
                break
        else:
            ptr["p"] = p + 1
    db.kv_set("crawl", ptr)
    return done


def fodder_index(db, feed: dict | None, rating: int) -> tuple[int, int] | None:
    """(цена индекса, ea_id самой дешёвой карты): середина из пяти самых дешёвых золотых карт рейтинга. Нужно ≥ 5 карт с ценой."""
    if not feed:
        return None
    rows = db.q("SELECT ea_id FROM cards WHERE rating=? AND lower(rarity) IN ('rare','common')", (rating,))
    ps = sorted((feed["prices"][r["ea_id"]], r["ea_id"]) for r in rows if feed["prices"].get(r["ea_id"], 0) >= 150)
    if len(ps) < 5:
        return None
    return int(statistics.median(p for p, _ in ps[:5])), ps[0][1]


def log_price(db, ea_id: int, price: int, now: float, min_gap: float | None = None) -> None:
    min_gap = config.PRICE_LOG_GAP if min_gap is None else min_gap
    last = db.one("SELECT MAX(ts) AS t FROM price_log WHERE ea_id=?", (ea_id,))
    if not last or not last["t"] or now - last["t"] >= min_gap:
        db.x("INSERT INTO price_log VALUES (?,?,?)", (ea_id, now, int(price)))


def tracked_ids(db, now: float) -> set[int]:
    ids = {r["ea_id"] for r in db.q("SELECT DISTINCT ea_id FROM facts WHERE ea_id IS NOT NULL AND ts>?", (now - 5 * 86400,))}
    ids |= {r["ea_id"] for r in db.q("SELECT DISTINCT ea_id FROM signals WHERE ts>?", (now - 8 * 86400,)) if r["ea_id"] and r["ea_id"] > 0}
    ids |= {r["ea_id"] for r in db.q("SELECT DISTINCT ea_id FROM forecasts WHERE ea_id>0 AND ts>?", (now - 8 * 86400,))}
    ids |= {r["track_id"] for r in db.q("SELECT DISTINCT track_id FROM trials WHERE track_id>0 AND ts>?", (now - 5 * 86400,))}
    return ids


def record_prices(db, feed: dict | None, now: float, extra_ids=()) -> int:
    if not feed:
        return 0
    n = 0
    for r in FODDER_RATINGS:
        fi = fodder_index(db, feed, r)
        if fi:
            log_price(db, -r, fi[0], now)
            n += 1
    for i in tracked_ids(db, now) | {int(e) for e in extra_ids if e}:
        p = feed["prices"].get(i)
        if p:
            log_price(db, i, p, now)
            n += 1
    return n


def percentile(db, ea_id: int, price: int, now: float, days: int = 7, min_n: int = 6) -> float | None:
    rows = db.q("SELECT price FROM price_log WHERE ea_id=? AND ts>?", (ea_id, now - days * 86400))
    if len(rows) < min_n:
        return None
    return sum(1 for r in rows if r["price"] <= price) / len(rows)


# ── прогнозы и их проверка ───────────────────────────────────────────────────
def calibrated_up(db, pattern: str) -> float:
    """Ожидаемый рост: медиана реально случившегося по проверенным прогнозам (≥ 5), иначе стартовое значение. Не ниже 0."""
    rows = db.q("SELECT base, p3 FROM forecasts WHERE dept=? AND direction='up' AND p3 IS NOT NULL AND base>0", (pattern,))
    if len(rows) >= 5:
        return max(0.0, statistics.median(r["p3"] / r["base"] - 1 for r in rows))
    return PRIOR_UP.get(pattern, 0.15)


def add_forecast(db, now: float, ea_id: int, name: str, pattern: str, direction: str, base: int, reason: str) -> None:
    if db.one("SELECT 1 FROM forecasts WHERE ea_id=? AND dept=? AND ts>?", (ea_id, pattern, now - 24 * 3600)):
        return
    db.x("INSERT INTO forecasts (ts,ea_id,name,dept,direction,base,reason) VALUES (?,?,?,?,?,?,?)", (now, ea_id, name, pattern, direction, base, reason))


def price_near(db, ea_id: int, ts: float, feed: dict | None, now: float) -> int | None:
    if ea_id < 0:
        r = db.one("SELECT price FROM price_log WHERE ea_id=? AND ts>=? ORDER BY ts LIMIT 1", (ea_id, ts - 3600))
        return r["price"] if r else None
    r = db.one("SELECT price FROM price_log WHERE ea_id=? AND ts>=? ORDER BY ts LIMIT 1", (ea_id, ts - 3600))
    if r:
        return r["price"]
    return feed["prices"].get(ea_id) if feed and now - ts < 12 * 3600 else None


def verify_forecasts(db, feed: dict | None, now: float) -> int:
    """Проверка прогнозов через 1/3/7 дней. Прогноз «верен», если цена сдвинулась в названную сторону не меньше чем на 2%."""
    n = 0
    for f in db.q("SELECT * FROM forecasts WHERE ok7 IS NULL AND ts>?", (now - 12 * 86400,)):
        for d in CHECK_DAYS:
            if f[f"ok{d}"] is not None or now < f["ts"] + d * 86400:
                continue
            p = None
            r = db.one("SELECT price FROM price_log WHERE ea_id=? AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1",
                       (f["ea_id"], f["ts"] + d * 86400 - 6 * 3600, f["ts"] + d * 86400 + 18 * 3600, f["ts"] + d * 86400))
            if r:
                p = r["price"]
            elif f["ea_id"] > 0 and feed and now < f["ts"] + d * 86400 + 18 * 3600:
                p = feed["prices"].get(f["ea_id"])
            if not p:
                continue
            ch = p / f["base"] - 1
            ok = 1 if (ch >= 0.02 if f["direction"] == "up" else ch <= -0.02) else 0
            db.x(f"UPDATE forecasts SET p{d}=?, ok{d}=? WHERE id=?", (p, ok, f["id"]))
            n += 1
    return n


def forecast_accuracy(db) -> dict:
    out = {}
    for d in CHECK_DAYS:
        r = db.one(f"SELECT COUNT(*) n, SUM(ok{d}) s FROM forecasts WHERE ok{d} IS NOT NULL")
        out[d] = (r["s"] or 0, r["n"]) if r and r["n"] else (0, 0)
    return out


# ── мнения ───────────────────────────────────────────────────────────────────
def week_phase(now: float) -> str:
    """Недельный цикл (по Москве): подсказка, что обычно происходит с ценами."""
    import datetime as dt
    w = dt.datetime.fromtimestamp(now, config.TZ)
    d, h = w.weekday(), w.hour
    if d == 4 and h >= 16:
        return "пятничный вечер: выходит новое промо, цены нестабильны"
    return {0: "понедельник: после наград Лиги выходных рынок насыщен картами", 1: "вторник: корм обычно дешёвый", 2: "среда: ожидание команды недели, цены игроков растут до её выхода",
            3: "четверг: после выхода команды недели цены её игроков падают", 4: "пятница: игроки готовятся к промо", 5: "суббота: спрос на корм под наборы", 6: "воскресенье: перед новой неделей цены корма растут"}[d]


def opinions(db, feed: dict | None, now: float) -> list[Opinion]:
    ops = []
    if not feed:
        return ops
    active = db.q("SELECT * FROM sbc WHERE category='upgrades' AND last_seen>? AND first_seen>?", (now - 3 * 3600, now - 3 * 86400))
    events = {e["key"]: e for e in intel.merge(db, now)}
    for r in FODDER_RATINGS:
        fi = fodder_index(db, feed, r)
        if not fi:
            continue
        idx, cheapest = fi
        demand = [s for s in active if r in json.loads(s["ratings"] or "[]")]
        ev = events.get(f"fodder:r{r}")
        ref = ev["price"] if ev and ev["price"] and ev["conf"] >= 0.3 else 0
        pct = percentile(db, -r, idx, now)
        reasons, sell, strength = [], 0, 0.0
        names = ", ".join(intel._title(db, s) for s in ev["srcs"]) if ev else ""
        if ref and ref >= idx * 1.15:
            sell = ref
            reasons.append(f"источники ({names}) называют цену корма {r} около {ru.num(ref)}, а у самых дешёвых карт — {ru.num(idx)}")
            strength = ev["conf"]
        elif ev and not ev["price"] and ev["conf"] >= 0.3:          # утечка о наборе под карты этого рейтинга, цены в посте нет
            sell = int(idx * (1 + calibrated_up(db, "fodder")))
            reasons.append(f"по утечкам ({names}) скоро наборы под карты {r}; корм пока стоит {ru.num(idx)}, обычно он дорожает к выходу набора")
            strength = ev["conf"]
        if demand and (pct is not None and pct <= 0.35):
            sell = max(sell, int(idx * (1 + calibrated_up(db, "fodder"))))
            reasons.append(f"открыт {describe(demand[0]['name'], 'upgrades')}, спрос на карты {r}; цена в нижней трети своего недельного диапазона")
            strength = max(strength, 0.55)
        if not sell:
            continue
        c = db.one("SELECT * FROM cards WHERE ea_id=?", (cheapest,))
        ops.append(Opinion("sbc", cheapest, c["name"] if c else "корм", "Золотая редкая", r, round(min(strength, 0.9), 2), "fodder", "; ".join(reasons),
                           srcs=(ev["srcs"] if ev and (ref or not ev["price"]) else []), buy_max=idx, sell_ref=sell, ttl_h=12, horizon_d=2))
        add_forecast(db, now, -r, f"корм {r}", "fodder", "up", idx, reasons[0])
    return ops


def promo_watch(db, feed: dict | None, now: float) -> None:
    """Поведение цен после промо: в первые часы нового промо записываем прогноз «корм дешевеет» и проверяем его через 1/3/7 дней."""
    from . import risk
    if not feed or not risk.promo_veto(db, now):
        return
    for r in (85, 87):
        fi = fodder_index(db, feed, r)
        if fi:
            add_forecast(db, now, -r, f"корм {r}", "promo", "down", fi[0], "начало нового промо: предложение карт растёт")


def cycle_opinions(db, feed: dict | None, now: float) -> list[Opinion]:
    """Недельный цикл: корм дешевле в четверг вечером и в пятницу (после наград Лиги выходных рынок насыщен), к выходным дорожает перед воскресной сменой промо.
    Окно покупки — четверг 18:00 … пятница 16:00 по Москве, продажа — суббота–воскресенье. Ожидаемый рост — стартовое значение, дальше его заменяет проверка прогнозов."""
    import datetime as dt
    w = dt.datetime.fromtimestamp(now, config.TZ)
    w.replace(hour=18, minute=0, second=0, microsecond=0)
    if not feed or not ((w.weekday() == 3 and w.hour >= 18) or (w.weekday() == 4 and w.hour < 16)):
        return []
    end = (w + dt.timedelta(days=(4 - w.weekday()))).replace(hour=16, minute=0, second=0, microsecond=0)
    ops = []
    for r in (86, 87, 88):         # на дешёвом корме 500 монет прибыли не набрать
        fi = fodder_index(db, feed, r)
        if not fi:
            continue
        idx, cheapest = fi
        c = db.one("SELECT * FROM cards WHERE ea_id=?", (cheapest,))
        pct = percentile(db, -r, idx, now)
        up = calibrated_up(db, "cycle")
        why = f"недельный цикл: корм {r} обычно дешевле в четверг вечером и в пятницу, а в субботу и воскресенье дорожает; продавать лучше с субботы по воскресенье"
        if pct is not None:
            why += f" (сейчас цена в {int(pct * 100)}% своего недельного диапазона снизу)"
        ops.append(Opinion("sbc", cheapest, c["name"] if c else "корм", "Золотая редкая", r, 0.5 if pct is None or pct > 0.5 else 0.65, "cycle", why,
                           buy_max=idx, sell_ref=int(idx * (1 + up)), ttl_h=max(1.0, (end - w).total_seconds() / 3600), horizon_d=2))
        add_forecast(db, now, -r, f"корм {r}", "cycle", "up", idx, "недельный цикл")
    return ops
