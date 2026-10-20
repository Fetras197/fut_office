"""Отдел 6. Трейдер: собирает мнения отделов по каждой карте; финальный сигнал только при согласии минимум двух отделов, оценке ≥ 60 и не больше 10 сигналов в сутки.
Цена «Купить до» всегда не ниже текущей цены карты (иначе сигнал бесполезен) и не выше предела, при котором прибыль после налога проходит пороги риск-менеджера."""
import datetime as dt
import json

from . import cards, config, ru, risk, telegram
from .model import Opinion

TYPE_HOURS = {"flip": 1.5, "invest": 24.0, "fodder": 12.0}


def group(ops: list[Opinion]) -> dict[str, list[Opinion]]:
    out: dict[str, list[Opinion]] = {}
    for o in ops:
        out.setdefault(f"fodder:{o.rating}" if o.kind == "fodder" else f"c{o.ea_id}", []).append(o)
    return out


def depts_of(ops: list[Opinion]) -> set[str]:
    d = {o.dept for o in ops if not o.veto}
    d |= {"intel" for o in ops if o.srcs and o.dept != "intel"}      # внутри мнения другого отдела есть подтверждение источников разведки
    return d


def conf_label(score: float) -> str:
    return "высокая" if score >= 80 else "средняя" if score >= 70 else "умеренная"


def build(db, key: str, ops: list[Opinion], gg: dict | None, now: float) -> tuple[dict | None, str]:
    """(сигнал, причина отказа)."""
    depts = depts_of(ops)
    if len(depts) < config.MIN_DEPTS:
        return None, "согласны меньше двух отделов"
    top = max(ops, key=lambda o: o.strength)
    cur = gg["prices"].get(top.ea_id) if gg else None
    if not cur:
        return None, "нет текущей цены карты"
    sells = [o.sell_ref for o in ops if o.sell_ref]
    if not sells:
        return None, "ни один отдел не назвал цену продажи"
    sell = cards.round_down(min(sells))
    buys = [o.buy_max for o in ops if o.buy_max]
    cap = risk.max_buy(sell)
    hi = min([cap, cards.round_down(cur * 1.04)] + ([cards.round_down(min(buys) * 1.03)] if buys else []))
    lo = cards.round_up(cur)
    if hi < lo:
        return None, f"цена карты {ru.num(cur)} уже выше допустимой {ru.num(hi)}"
    buy = hi
    kind = "fodder" if any(o.kind == "fodder" for o in ops) else "flip" if any(o.kind == "flip" for o in ops) else "invest"
    liqs = [o.liq for o in ops if o.liq is not None]
    veto = risk.check(db, {"name": top.name, "buy": buy, "sell": sell, "liq": min(liqs) if liqs else None, "kind": kind, "ea_id": top.ea_id}, now)
    if veto:
        return None, "риск-менеджер: " + veto
    best: dict[str, float] = {}
    for o in ops:
        best[o.dept] = max(best.get(o.dept, 0), o.strength)
    for d in depts - set(best):
        best[d] = max((o.strength for o in ops if o.srcs), default=0.4)      # подтверждающие источники разведки
    mean_s = sum(best.values()) / len(best)
    prof = risk.profit(buy, sell)
    score = 40 + 40 * mean_s + 6 * (len(depts) - 2) + min(10.0, prof / buy * 100 * 0.5) - (5 if not liqs and kind == "flip" else 0)
    score = round(max(0.0, min(100.0, score)), 1)
    if score < config.MIN_SCORE:
        return None, f"оценка {score} ниже порога {config.MIN_SCORE}"
    ttl = min(min(o.ttl_h for o in ops), TYPE_HOURS[kind])
    srcs = sorted({s for o in ops for s in o.srcs})
    return {"key": key, "type": kind, "ea_id": top.ea_id, "name": top.name, "version": top.version, "rating": top.rating, "buy_max": buy, "sell_min": sell,
            "profit": prof, "expires": now + ttl * 3600, "score": score, "conf": conf_label(score), "depts": sorted(depts), "srcs": srcs, "price_at": cur,
            "why": [f"{ru.DEPTS[o.dept].capitalize()}: {o.reason}" for o in sorted(ops, key=lambda o: -o.strength)][:4]}, ""


def today_count(db, now: float) -> int:
    start = dt.datetime.fromtimestamp(now, config.TZ).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return db.one("SELECT COUNT(*) n FROM signals WHERE ts>=? AND status!='dry_skip'", (start,))["n"]


def text_of(sig: dict, sid: int) -> str:
    kind = ru.SIGNAL_TYPES[sig["type"]]
    who = f"{sig['name']} — {sig['version']}, рейтинг {sig['rating']}"
    if sig["type"] == "fodder":
        who = f"корм: золотые карты рейтинга {sig['rating']} (например, {sig['name']})"
    pct = sig["profit"] / sig["buy_max"] * 100
    lines = [f"🔔 <b>Подсказка №{sid}: {kind}</b>",
             f"<b>Игрок:</b> {who}",
             f"<b>Купить до:</b> <code>{sig['buy_max']}</code> — в поиске поставьте максимальную цену выкупа {ru.num(sig['buy_max'])}",
             f"<b>Продать от:</b> <code>{sig['sell_min']}</code>",
             f"<b>Прибыль после налога 5%:</b> около {ru.coins(sig['profit'])} ({pct:.1f}%)".replace(".", ","),
             f"<b>Действует до:</b> {ru.hhmm(sig['expires'])} по Москве",
             f"<b>Уверенность:</b> {sig['conf']} ({int(sig['score'])} из 100)",
             "<b>Почему:</b>"] + [f"• {w}" for w in sig["why"]]
    return "\n".join(lines)


def buttons(sid: int):
    return [[("✅ Купил", f"of:b:{sid}"), ("❌ Не нашёл по цене", f"of:n:{sid}")], [("💰 Продал за…", f"of:s:{sid}")]]


def run(db, ops: list[Opinion], gg_fetch, now: float, send=telegram.send, max_new: int = 3) -> dict:
    """Один проход трейдера. gg_fetch(force) → свежая лента fut.gg (перед отправкой цена перепроверяется)."""
    res = {"candidates": 0, "sent": 0, "rejects": {}}
    left = config.MAX_SIGNALS_PER_DAY - today_count(db, now)
    cand = []
    gg = gg_fetch(False)
    for key, grp in group(ops).items():
        res["candidates"] += 1
        if db.one("SELECT 1 FROM signals WHERE key=? AND ts>?", (key, now - config.DEDUP_HOURS * 3600)):
            continue
        sig, why = build(db, key, grp, gg, now)
        if sig:
            cand.append(sig)
        else:
            res["rejects"][why[:48]] = res["rejects"].get(why[:48], 0) + 1
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
                    sig["score"], sig["conf"] == "высокая" and 1.0 or (0.7 if sig["conf"] == "средняя" else 0.5), json.dumps(sig["depts"], ensure_ascii=False),
                    json.dumps(sig["srcs"], ensure_ascii=False), json.dumps(sig["why"], ensure_ascii=False), cur, cur))
        mid = send(text_of(sig, sid), buttons(sid))
        db.x("UPDATE signals SET tg_msg=?, status=? WHERE id=?", (mid, "sent" if mid else "unsent", sid))
        left -= 1
        res["sent"] += 1
    return res
