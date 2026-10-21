"""Отдел 2. Аналитик рынка (в облаке): не голосует, а проверяет текущий минимум карты и тренд по ленте fut.gg (журнал цен ведёт sbc.record_prices).
Futbin как запасной источник из облака не используется: он режет адреса GitHub Actions (решение в DECISIONS.md)."""
from . import ru

FALL_VETO = -0.08       # за сутки карта подешевела больше чем на 8%: покупать «на отскок» не советуем


def check(db, feed: dict | None, ea_id: int, now: float, cur: int | None = None) -> dict | None:
    """{"cur": текущий минимум, "day": изменение за ~сутки или None, "week_min": минимум за неделю, "n": число записей} либо None, если цены нет."""
    if not feed:
        return None
    cur = cur or (feed["prices"].get(int(ea_id)) if ea_id and ea_id > 0 else None)
    if not cur:
        return None
    rows = db.q("SELECT ts, price FROM price_log WHERE ea_id=? AND ts>? ORDER BY ts", (ea_id, now - 7 * 86400))
    day = None
    old = [r for r in rows if now - r["ts"] >= 20 * 3600 and now - r["ts"] <= 30 * 3600]
    if old:
        base = old[-1]["price"]
        day = cur / base - 1
    return {"cur": int(cur), "day": day, "week_min": min([r["price"] for r in rows] + [cur]), "n": len(rows)}


def veto(m: dict | None) -> str | None:
    if m and m["day"] is not None and m["day"] <= FALL_VETO:
        return f"карта дешевеет: за сутки {m['day'] * 100:.0f}% — ждём, пока цена остановится".replace(".", ",")
    return None


def line(m: dict | None) -> str | None:
    if not m:
        return None
    d = "" if m["day"] is None else f", за сутки {m['day'] * 100:+.0f}%".replace(".", ",")
    return f"Аналитик рынка: сейчас минимум {ru.num(m['cur'])}{d}"
