"""Отдел 7. Бухгалтер и совет: записи кнопок («Купил / Не нашёл по цене / Продал за…»), прибыль, точность по отделам и источникам, воскресный разбор с понижением плохих источников."""
import datetime as dt
import json
import time

from . import config, intel, llm, risk, ru, sbc, telegram, trader

PRIOR_WEIGHT = 4
DEMOTE_BELOW, DEMOTE_MIN_CHECKS = 0.35, 8
OFF_BELOW, OFF_MIN_CHECKS = 0.25, 12


def ingest_ledger(db, ledger: dict | None) -> int:
    """Записи телефона: {"entries":[{"id","sid","action": bought|notfound|sold,"price","ts"}]}."""
    n = 0
    for e in (ledger or {}).get("entries", []):
        if e.get("action") not in ("bought", "notfound", "sold") or not e.get("id"):
            continue
        if db.one("SELECT 1 FROM ledger WHERE ext_id=?", (str(e["id"]),)):
            continue
        db.x("INSERT INTO ledger (sid,action,price,ts,ext_id) VALUES (?,?,?,?,?)", (int(e["sid"]), e["action"], int(e.get("price") or 0), float(e.get("ts") or time.time()), str(e["id"])))
        n += 1
    return n


def check_prices(db, gg: dict | None, now: float) -> int:
    """Проверка «карта действительно лежит по указанной цене» через ≥ 8 минут после отправки (по ленте fut.gg)."""
    n = 0
    if not gg:
        return 0
    for s in db.q("SELECT * FROM signals WHERE chk10 IS NULL AND ts<? AND ts>?", (now - 8 * 60, now - 6 * 3600)):
        cur = gg["prices"].get(s["ea_id"]) if s["ea_id"] > 0 else None
        if s["type"] == "fodder":
            cur = s["price_at"] and _fodder_now(db, gg, s)
        if cur:
            db.x("UPDATE signals SET chk10=?, chk10_ts=? WHERE id=?", (cur, now, s["id"]))
            n += 1
    return n


def _fodder_now(db, gg, s):
    rows = db.q("SELECT ea_id FROM cards WHERE rating=? AND lower(rarity) IN ('rare','common')", (s["rating"],))
    ps = [gg["prices"][r["ea_id"]] for r in rows if gg["prices"].get(r["ea_id"], 0) >= 150]
    return min(ps) if ps else None


def outcome(db, s: dict) -> dict:
    """{found: bool|None, sold_profit: int|None, bought: int|None}."""
    led = db.q("SELECT * FROM ledger WHERE sid=? ORDER BY ts", (s["id"],))
    bought = next((l["price"] or s["buy_max"] for l in led if l["action"] == "bought"), None)
    sold = next((l["price"] for l in led if l["action"] == "sold"), None)
    if any(l["action"] == "notfound" for l in led):
        found = False
    elif bought is not None or sold is not None:
        found = True
    elif s["chk10"]:
        found = s["chk10"] <= s["buy_max"]
    else:
        found = None
    return {"found": found, "sold_profit": (risk.after_tax(sold) - (bought or s["buy_max"])) if sold else None, "bought": bought}


def update_facts(db, now: float) -> None:
    """Проверка фактов источников: ориентир продажи считается верным, если цена карты за 48 часов доходила до 97% названного; утечка — если её подтвердил другой источник."""
    for f in db.q("SELECT * FROM facts WHERE outcome IS NULL AND ts<? AND ts>?", (now - 24 * 3600, now - 5 * 86400)):
        hit = None
        if f["kind"] == "sell" and f["ea_id"] and f["price"]:
            r = db.one("SELECT MAX(price) m, COUNT(*) n FROM price_log WHERE ea_id=? AND ts>?", (f["ea_id"], f["ts"]))
            if r and r["n"] >= 2:
                hit = 1 if r["m"] >= 0.97 * f["price"] else 0
        elif f["kind"] in ("leak", "sbc", "totw") and f["ts"] < now - 48 * 3600:
            hit = 1 if db.one("SELECT 1 FROM facts WHERE key=? AND src!=?", (f["key"], f["src"])) else 0
        elif f["kind"] == "fodder" and f["price"]:
            r = db.one("SELECT MAX(price) m, COUNT(*) n FROM price_log WHERE ea_id=? AND ts>?", (-f["rating"], f["ts"]))
            if r and r["n"] >= 2:
                hit = 1 if r["m"] >= 0.9 * f["price"] else 0
        if hit is not None:
            db.x("UPDATE facts SET outcome=? WHERE id=?", (hit, f["id"]))
            db.x("UPDATE src_stats SET checks=checks+1, hits=hits+? WHERE src=?", (hit, f["src"]))


def update_ratings(db, srcs: list[dict]) -> None:
    prior = {s["id"]: s.get("rating", 0.5) for s in srcs}
    for r in db.q("SELECT * FROM src_stats"):
        p = prior.get(r["src"], 0.5)
        db.x("UPDATE src_stats SET rating=? WHERE src=?", (round((r["hits"] + PRIOR_WEIGHT * p) / (r["checks"] + PRIOR_WEIGHT), 3), r["src"]))


def update_dept_stats(db, now: float) -> None:
    day = dt.datetime.fromtimestamp(now, config.TZ).strftime("%F")
    for s in db.q("SELECT * FROM signals WHERE ts>? AND status='sent' AND (chk10 IS NOT NULL OR id IN (SELECT sid FROM ledger))", (now - 3 * 86400,)):
        if db.one("SELECT 1 FROM kv WHERE k=?", (f"acct:{s['id']}",)):
            continue
        o = outcome(db, s)
        if o["found"] is None:
            continue
        good = 1 if (o["sold_profit"] > 0 if o["sold_profit"] is not None else o["found"]) else 0
        for d in json.loads(s["depts"]):
            db.x("INSERT OR IGNORE INTO dept_stats (dept,day,votes,good) VALUES (?,?,0,0)", (d, day))
            db.x("UPDATE dept_stats SET votes=votes+1, good=good+? WHERE dept=? AND day=?", (good, d, day))
        db.kv_set(f"acct:{s['id']}", 1)


def stats(db, now: float, days: int = 7) -> dict:
    sigs = db.q("SELECT * FROM signals WHERE ts>? AND status='sent'", (now - days * 86400,))
    found = total = 0
    profit = sold = bought = 0
    for s in sigs:
        o = outcome(db, s)
        if o["found"] is not None:
            total += 1
            found += 1 if o["found"] else 0
        if o["bought"] is not None:
            bought += 1
        if o["sold_profit"] is not None:
            sold += 1
            profit += o["sold_profit"]
    depts = {r["dept"]: (r["g"], r["v"]) for r in db.q("SELECT dept, SUM(good) g, SUM(votes) v FROM dept_stats WHERE day>=? GROUP BY dept", ((dt.datetime.fromtimestamp(now, config.TZ) - dt.timedelta(days=days)).strftime("%F"),))}
    return {"signals": len(sigs), "checked": total, "found": found, "bought": bought, "sold": sold, "profit": profit, "depts": depts,
            "sources": db.q("SELECT src, rating, checks, hits, enabled FROM src_stats ORDER BY rating DESC")}


def pct(a, b):
    return f"{100 * a / b:.0f}%" if b else "нет данных"


def report_text(db, now: float, days: int = 7) -> str:
    st = stats(db, now, days)
    fa = sbc.forecast_accuracy(db)
    L = [f"📊 <b>Итоги за {days} {ru.plural(days, 'день', 'дня', 'дней')}</b>",
         f"Подсказок отправлено: {st['signals']}. Карта нашлась по указанной цене: {st['found']} из {st['checked']} ({pct(st['found'], st['checked'])}).",
         f"Куплено по кнопке: {st['bought']}, продано: {st['sold']}. Чистая прибыль после налога по проданному: {ru.coins(st['profit'])}."]
    if st["depts"]:
        L.append("<b>Точность отделов:</b> " + "; ".join(f"{ru.DEPTS.get(d, d)} — {pct(g, v)} ({v})" for d, (g, v) in st["depts"].items()))
    L.append("<b>Проверка прогнозов:</b> " + ", ".join(f"через {d} {ru.plural(d, 'день', 'дня', 'дней')}: {pct(s, n)} ({n})" for d, (s, n) in fa.items()))
    srcs = [r for r in st["sources"] if r["checks"] > 0]
    if srcs:
        L.append("<b>Источники:</b> " + "; ".join(f"{intel._title(db, r['src'])} — {int(r['rating'] * 100)}% ({r['checks']})" for r in srcs[:8]))
    return "\n".join(L)


def sunday_council(db, srcs: list[dict], router, now: float, send=telegram.send) -> str | None:
    """Раз в неделю (воскресенье после 20:00 по Москве): итоги, понижение плохих источников, совет."""
    w = dt.datetime.fromtimestamp(now, config.TZ)
    key = f"council:{w.isocalendar()[0]}-{w.isocalendar()[1]}"
    if w.weekday() != 6 or w.hour < 20 or db.kv_get(key):
        return None
    update_ratings(db, srcs)
    notes = []
    for r in db.q("SELECT * FROM src_stats WHERE enabled=1"):
        title = intel._title(db, r["src"])
        if r["checks"] >= OFF_MIN_CHECKS and r["rating"] < OFF_BELOW:
            db.x("UPDATE src_stats SET enabled=0 WHERE src=?", (r["src"],))
            notes.append(f"источник «{title}» отключён: точность {int(r['rating'] * 100)}% по {r['checks']} проверкам")
        elif r["checks"] >= DEMOTE_MIN_CHECKS and r["rating"] < DEMOTE_BELOW:
            notes.append(f"источник «{title}» понижен: точность {int(r['rating'] * 100)}% по {r['checks']} проверкам, его слову верят меньше")
    text = report_text(db, now)
    vs = virtual_stats(db, now, 7, 24)
    if vs["n"]:
        text += "\n<b>Виртуальная проверка (24 часа, неделя):</b> отделы — " + "; ".join(f"{ru.DEPTS.get(d, d)} {g}/{n}" for d, (g, n) in vs["dept"].items()) + \
                "; типы — " + "; ".join(f"{ru.SIGNAL_TYPES.get(t, t)} {g}/{n}" for t, (g, n) in vs["type"].items())
        vs3 = virtual_stats(db, now, 7, 72)
        if vs3["n"]:
            text += "; через 72 часа в плюс: " + ", ".join(f"{ru.DEPTS.get(d, d)} {g}/{n}" for d, (g, n) in vs3["dept"].items())
    if notes:
        text += "\n<b>Решения совета:</b>\n" + "\n".join("• " + n for n in notes)
    verdict = None
    if router is not None:
        try:
            v = router.ask("council", {"итоги": stats(db, now, 7) | {"sources": None}, "решения": notes})
            vt = str(v.get("verdict", ""))[:600]
            if vt and ru.no_latin(vt):
                verdict = vt
        except (llm.LLMError, TypeError, ValueError):
            pass
    if verdict:
        text += "\n<b>Вывод совета:</b> " + verdict
    send(text, None)
    db.kv_set(key, now)
    return text

# ── виртуальная проверка (пробные кандидаты и сами сигналы) через 24 и 72 часа ─────
def _price_at(db, track: int, ts: float, feed: dict | None, now: float):
    r = db.one("SELECT price FROM price_log WHERE ea_id=? AND ts BETWEEN ? AND ? ORDER BY ABS(ts-?) LIMIT 1", (track, ts - 6 * 3600, ts + 18 * 3600, ts))
    if r:
        return r["price"]
    if track > 0 and feed and now < ts + 18 * 3600:
        return feed["prices"].get(track)
    return None


def verify_trials(db, feed: dict | None, now: float) -> int:
    """Виртуальный результат: покупка по цене на момент записи; через 24/72 ч смотрим цену и максимум за период."""
    n = 0
    for t in db.q("SELECT * FROM trials WHERE done=0 AND ts>?", (now - 6 * 86400,)):
        for h in (24, 72):
            if t[f"p{h}"] is not None or now < t["ts"] + h * 3600:
                continue
            p = _price_at(db, t["track_id"], t["ts"] + h * 3600, feed, now)
            if not p:
                continue
            hi = db.one("SELECT MAX(price) m FROM price_log WHERE ea_id=? AND ts BETWEEN ? AND ?", (t["track_id"], t["ts"], t["ts"] + h * 3600 + 3600))["m"] or p
            db.x(f"UPDATE trials SET p{h}=?, hi{h}=? WHERE id=?", (p, max(hi, p), t["id"]))
            n += 1
        t2 = db.one("SELECT p72 FROM trials WHERE id=?", (t["id"],))
        if t2["p72"] is not None or now > t["ts"] + 96 * 3600:
            db.x("UPDATE trials SET done=1 WHERE id=?", (t["id"],))
    return n


def virtual_profit(t: dict, h: int) -> int | None:
    p = t[f"p{h}"]
    return None if p is None else risk.after_tax(p) - t["price_at"]


def virtual_stats(db, now: float, days: int = 7, h: int = 24, matured_days: float | None = None) -> dict:
    """{'dept': {отдел: (в плюс, всего)}, 'type': {тип: (в плюс, всего)}, 'mode': {'signal'|'trial': (в плюс, всего)}, 'profit': средняя виртуальная прибыль, 'n': число проверенных}"""
    rows = [t for t in db.q(f"SELECT * FROM trials WHERE p{h} IS NOT NULL AND ts>?", (now - days * 86400,)) if matured_days is None or t["ts"] + h * 3600 > now - matured_days * 86400]
    out = {"dept": {}, "type": {}, "mode": {}, "n": len(rows), "profit": None}
    prof = []
    for t in rows:
        vp = virtual_profit(t, h)
        good = 1 if vp > 0 else 0
        prof.append(vp)
        keys = [("dept", d) for d in json.loads(t["depts"])] + [("type", t["type"]), ("mode", t["mode"])]
        for kind, k in keys:
            g, n = out[kind].get(k, (0, 0))
            out[kind][k] = (g + good, n + 1)
    if prof:
        out["profit"] = int(sum(prof) / len(prof))
    return out


def best_dept(vs: dict, min_n: int = 3):
    c = [(g / n, n, d) for d, (g, n) in vs["dept"].items() if n >= min_n]
    return max(c) if c else None


def bump_day(db, now: float, **kw) -> None:
    key = f"day:{dt.datetime.fromtimestamp(now, config.TZ).strftime('%F')}"
    d = db.kv_get(key, {})
    for k, v in kw.items():
        d[k] = sorted(set(d.get(k, [])) | set(v)) if isinstance(v, list) else d.get(k, 0) + v
    db.kv_set(key, d)


def evening_report(db, now: float, send=telegram.send) -> str | None:
    """Отчёт в 21:00 по Москве (один раз в сутки, на первом проходе после 21:00)."""
    w = dt.datetime.fromtimestamp(now, config.TZ)
    key = f"evening:{w.strftime('%F')}"
    if w.hour < 21 or db.kv_get(key):
        return None
    day = db.kv_get(f"day:{w.strftime('%F')}", {})
    start = trader.day_start(now)
    sigs = db.q("SELECT type, depts FROM signals WHERE ts>=?", (start,))
    trials = db.one("SELECT COUNT(*) n FROM trials WHERE mode='trial' AND ts>=?", (start,))["n"]
    v24 = virtual_stats(db, now, days=4, h=24, matured_days=1)
    v7 = virtual_stats(db, now, days=7, h=24)
    L = [f"📋 <b>Отчёт за день, {w.strftime('%d.%m')}</b>",
         f"Кандидатов рассмотрено: {len(day.get('keys', []))}. Боевых сигналов: {len(sigs)} из {config.MAX_SIGNALS_PER_DAY} возможных. Пробных записей (один отдел «за»): {trials}."]
    if sigs:
        L.append("Сигналы по типам: " + ", ".join(f"{ru.SIGNAL_TYPES[t]} — {n}" for t, n in _count([s["type"] for s in sigs]).items()))
    if v24["n"]:
        sg, tg = v24["mode"].get("signal", (0, 0)), v24["mode"].get("trial", (0, 0))
        L.append(f"Виртуальный результат (проверки через 24 часа, завершённые за сутки): всего {v24['n']}, в плюс после налога — {sum(g for g, _ in (sg, tg))}; "
                 f"сигналы — {sg[0]} из {sg[1]}, пробные — {tg[0]} из {tg[1]}; средняя виртуальная прибыль {ru.coins(v24['profit'])}.")
    else:
        L.append("Виртуальных проверок через 24 часа за сутки пока нет — записи ещё созревают.")
    b = best_dept(v7)
    L.append(f"Лучший отдел за неделю: {ru.DEPTS[b[2]]} — {b[0] * 100:.0f}% в плюс ({b[1]} проверок)." if b else "Лучший отдел пока не определён: мало проверок (нужно не меньше трёх на отдел).")
    send("\n".join(L), None)
    db.kv_set(key, now)
    return "\n".join(L)


def _count(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out
