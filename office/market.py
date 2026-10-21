"""Отдел 2. Аналитик рынка. Работает на телефоне (Futbin: последние продажи, средняя цена, лоты, ликвидность; лимитер и пауза при 403 остаются в боте).
Здесь — облачный читатель: берёт phone_feed.json из ветки phone, сопоставляет карты с fut.gg и выдаёт мнения «зависшая цена»."""

from . import cards, config, intel, ru
from .model import Opinion

FLIP_TTL_H = 1.5


def fresh(feed: dict | None, now: float) -> bool:
    return bool(feed) and now - float(feed.get("ts", 0)) <= config.PHONE_STALE_MIN * 60


def opinions(db, phone: dict | None, gg: dict | None, now: float) -> list[Opinion]:
    ops = []
    if not fresh(phone, now):
        return ops
    for r in phone.get("hang", []):
        try:
            price, avgp = int(r["price"]), int(r["avgp"])
        except (KeyError, TypeError, ValueError):
            continue
        if now - float(r.get("ts", phone["ts"])) > 45 * 60 or price < 300 or avgp <= price:
            continue
        ea = r.get("ea_id")
        c = db.one("SELECT * FROM cards WHERE ea_id=?", (ea,)) if ea else None
        if not c:
            c = cards.resolve(db, r.get("name", ""), r.get("vkey", ""), int(r.get("rating") or 0))
        if not c:
            continue
        cur = gg["prices"].get(c["ea_id"]) if gg else None
        base = min(price, cur) if cur else price
        gap = (avgp * (1 - config.TAX) - base) / base
        if gap < 0.07:
            continue
        sales = r.get("sales24h")
        strength = min(0.9, 0.35 + gap * 1.2) * (0.9 if sales is None else 1.0 if sales >= 10 else 0.6)
        lots = r.get("lots") or []
        why = (f"на сайте Футбин самый дешёвый лот {ru.num(price)}, а средняя цена последних продаж {ru.num(avgp)}"
               + (f", продаж за сутки {sales}" if sales is not None else "") + (f", лотов в продаже {len(lots)}" if lots else ""))
        ver = intel._ver_ru(c["rarity"])
        ops.append(Opinion("market", c["ea_id"], c["name"], ver, c["rating"], round(strength, 2), "flip", why, srcs=[], buy_max=price, sell_ref=avgp,
                           ttl_h=FLIP_TTL_H, horizon_d=0.1, liq=sales))
    return ops
