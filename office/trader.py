"""Отдел 6. Трейдер. Боевой сигнал — минимум два отдела «за» (разведка, наборы и прогноз, футбольный аналитик), оценка ≥ 60, не больше 10 в сутки, риск-менеджер не против,
аналитик рынка не видит падения. Кандидат с одним отделом — пробный режим: виртуальная запись и проверка через 24 и 72 часа (без сообщения владельцу).
Цена «Купить до» не ниже текущей цены карты и не выше предела, при котором прибыль после налога проходит пороги риск-менеджера."""
import datetime as dt
import json

from . import cards, config, market, risk, ru, sbc, telegram
from .model import Opinion

TYPE_HOURS = {"fodder": 24.0, "cycle": 24.0, "totw": 24.0, "channel": 24.0}
TERM = {"fodder": "1–2 дня", "cycle": "2–3 дня: покупка в четверг–пятницу, продажа в субботу–воскресенье", "totw": "до объявления команды недели (среда, 18:00 по Москве)", "channel": "1–2 дня"}
TRIALS_PER_DAY = 40
KIND_ORDER = ("fodder", "cycle", "totw", "channel")


def group(ops: list[Opinion]) -> dict[str, list[Opinion]]:
    out: dict[str, list[Opinion]] = {}
    for o in ops:
        out.setdefault(f"fodder:{o.rating}" if o.kind in ("fodder", "cycle") else f"c{o.ea_id}", []).append(o)
    return out


def depts_of(ops: list[Opinion]) -> set[str]:
    d = {o.dept for o in ops if not o.veto}
    d |= {"intel" for o in ops if o.srcs and o.dept != "intel"}      # внутри мнения другого отдела есть подтверждение источников разведки
    return d


def conf_label(score: float) -> str:
    return "высокая" if score >= 80 else "средняя" if score >= 70 else "умеренная"


def track_of(kind: str, rating: int, ea_id: int) -> int:
    return -rating if kind in ("fodder", "cycle") else ea_id


def build(db, key: str, ops: list[Opinion], gg: dict | None, now: float, allow_single: bool = False) -> tuple[dict | None, str]:
    """(сигнал или кандидат, причина отказа). Если отделов меньше двух и allow_single=False — отказ."""
    depts = depts_of(ops)
    if len(depts) < config.MIN_DEPTS and not allow_single:
        return None, "согласны меньше двух отделов"
    kind = next((k for k in KIND_ORDER if any(o.kind == k for o in ops)), "channel")
    top = max(ops, key=lambda o: o.strength)
    track = track_of(kind, top.rating, top.ea_id)
    cur_card = gg["prices"].get(top.ea_id) if gg else None
    if not cur_card:
        return None, "нет текущей цены карты"
    ref_cur = cur_card
    if track < 0:
        fi = sbc.fodder_index(db, gg, top.rating)
        ref_cur = fi[0] if fi else cur_card
    mk = market.check(db, gg, track, now, cur=ref_cur)
    mv = market.veto(mk)
    if mv:
        return None, "аналитик рынка: " + mv
    sells = [o.sell_ref for o in ops if o.sell_ref]
    if not sells:
        return None, "ни один отдел не назвал цену продажи"
    sell = cards.round_down(min(sells))
    buys = [o.buy_max for o in ops if o.buy_max]
    cap = risk.max_buy(sell)
    hi = min([cap, cards.round_down(cur_card * 1.04)] + ([cards.round_down(min(buys) * 1.03)] if buys else []))
    lo = cards.round_up(cur_card)
    if hi < lo:
        return None, f"цена карты {ru.num(cur_card)} уже выше допустимой {ru.num(hi)}"
    buy = hi
    liqs = [o.liq for o in ops if o.liq is not None]
    real = len(depts) >= config.MIN_DEPTS
    veto = risk.check(db, {"name": top.name, "buy": buy, "sell": sell, "liq": min(liqs) if liqs else None, "kind": kind, "ea_id": top.ea_id}, now, hours=real)
    if veto:
        return None, "риск-менеджер: " + veto
    best: dict[str, float] = {}
    for o in ops:
        best[o.dept] = max(best.get(o.dept, 0), o.strength)
    for d in depts - set(best):
        best[d] = max((o.strength for o in ops if o.srcs), default=0.4)      # подтверждающие источники разведки
    mean_s = sum(best.values()) / len(best)
    prof = risk.profit(buy, sell)
    score = 40 + 40 * mean_s + 6 * (len(depts) - 2) + min(10.0, prof / buy * 100 * 0.5)
    score = round(max(0.0, min(100.0, score)), 1)
    if real and score < config.MIN_SCORE:
        return None, f"оценка {score} ниже порога {config.MIN_SCORE}"
    ttl = min(min(o.ttl_h for o in ops), TYPE_HOURS[kind])
    why = [f"{ru.DEPTS[o.dept].capitalize()}: {o.reason}" for o in sorted(ops, key=lambda o: -o.strength)][:3]
    ml = market.line(mk)
    if ml:
        why.append(ml)
    return {"key": key, "type": kind, "ea_id": top.ea_id, "track": track, "name": top.name, "version": top.version, "rating": top.rating, "buy_max": buy, "sell_min": sell,
            "profit": prof, "expires": now + ttl * 3600, "score": score, "conf": conf_label(score), "depts": sorted(depts), "srcs": sorted({s for o in ops for s in o.srcs}),
            "price_at": ref_cur, "why": why, "real": real}, ""


def day_start(now: float) -> float:
    return dt.datetime.fromtimestamp(now, config.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def today_count(db, now: float) -> int:
    return db.one("SELECT COUNT(*) n FROM signals WHERE ts>?", (day_start(now),))["n"]


def text_of(sig: dict, sid: int) -> str:
    kind = ru.SIGNAL_TYPES[sig["type"]]
    who = f"{sig['name']} — {sig['version']}, рейтинг {sig['rating']}"
    if sig["type"] in ("fodder", "cycle"):
        who = f"корм: золотые карты рейтинга {sig['rating']} (например, {sig['name']})"
    pct = sig["profit"] / sig["buy_max"] * 100
    lines = [f"🔔 <b>Подсказка №{sid}: {kind}</b>",
             f"<b>Игрок:</b> {who}",
             f"<b>Купить до:</b> <code>{sig['buy_max']}</code> — в поиске поставьте максимальную цену выкупа {ru.num(sig['buy_max'])}",
             f"<b>Продать от:</b> <code>{sig['sell_min']}</code>",
             f"<b>Прибыль после налога 5%:</b> около {ru.coins(sig['profit'])} ({pct:.1f}%)".replace(".", ","),
             f"<b>Срок сделки:</b> {TERM[sig['type']]}",
             f"<b>Подсказка действует до:</b> {ru.hhmm(sig['expires'])} по Москве",
             f"<b>Уверенность:</b> {sig['conf']} ({int(sig['score'])} из 100)",
             "<b>Почему:</b>"] + [f"• {w}" for w in sig["why"]]
    return "\n".join(lines)


def buttons(sid: int):
    return [[("✅ Купил", f"of:b:{sid}"), ("❌ Не нашёл по цене", f"of:n:{sid}")], [("💰 Продал за…", f"of:s:{sid}")]]


def record_trial(db, sig: dict, mode: str, now: float, sig_id: int | None = None) -> int:
    return db.x("INSERT INTO trials (ts,key,mode,type,track_id,ea_id,name,rating,buy_max,sell_min,price_at,depts,score,sig_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (now, sig["key"], mode, sig["type"], sig["track"], sig["ea_id"], sig["name"], sig["rating"], sig["buy_max"], sig["sell_min"], sig["price_at"],
                 json.dumps(sig["depts"], ensure_ascii=False), sig["score"], sig_id))


def run(db, ops: list[Opinion], gg_fetch, now: float, send=telegram.send, max_new: int = 3) -> dict:
    """Один проход трейдера. gg_fetch(force) → лента fut.gg (перед отправкой цена перепроверяется)."""
    res = {"candidates": 0, "sent": 0, "trials": 0, "rejects": {}, "keys": []}
    left = config.MAX_SIGNALS_PER_DAY - today_count(db, now)
    trials_today = db.one("SELECT COUNT(*) n FROM trials WHERE mode='trial' AND ts>?", (day_start(now),))["n"]
    cand = []
    gg = gg_fetch(False)
    for key, grp in group(ops).items():
        res["candidates"] += 1
        res["keys"].append(key)
        if db.one("SELECT 1 FROM signals WHERE key=? AND ts>?", (key, now - config.DEDUP_HOURS * 3600)):
            continue
        sig, why = build(db, key, grp, gg, now, allow_single=True)
        if not sig:
            res["rejects"][why[:48]] = res["rejects"].get(why[:48], 0) + 1
            continue
        if sig["real"]:
            cand.append(sig)
        elif trials_today < TRIALS_PER_DAY and not db.one("SELECT 1 FROM trials WHERE mode='trial' AND key=? AND ts>?", (key, now - 24 * 3600)):
            record_trial(db, sig, "trial", now)
            trials_today += 1
            res["trials"] += 1
    cand.sort(key=lambda s: -s["score"])
    gg2 = None
    for sig in cand:
        if left <= 0 or res["sent"] >= max_new:
            break
        if gg2 is None:
            gg2 = gg_fetch(True) or gg                       # свежая лента перед отправкой
        cur = gg2["prices"].get(sig["ea_id"]) if gg2 else None
        if not cur or cur > sig["buy_max"]:
            res["rejects"]["цена ушла выше"] = res["rejects"].get("цена ушла выше", 0) + 1
            continue
        sid = db.x("INSERT INTO signals (ts,key,type,ea_id,name,version,rating,buy_max,sell_min,profit,expires,score,conf,depts,srcs,why,price_at,chk0) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (now, sig["key"], sig["type"], sig["ea_id"], sig["name"], sig["version"], sig["rating"], sig["buy_max"], sig["sell_min"], sig["profit"], sig["expires"],
                    sig["score"], {"высокая": 1.0, "средняя": 0.7}.get(sig["conf"], 0.5), json.dumps(sig["depts"], ensure_ascii=False),
                    json.dumps(sig["srcs"], ensure_ascii=False), json.dumps(sig["why"], ensure_ascii=False), cur, cur))
        mid = send(text_of(sig, sid), buttons(sid))
        db.x("UPDATE signals SET tg_msg=?, status=? WHERE id=?", (mid, "sent" if mid else "unsent", sid))
        record_trial(db, sig, "signal", now, sid)
        left -= 1
        res["sent"] += 1
    return res
