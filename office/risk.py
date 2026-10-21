"""Отдел 5. Риск-менеджер. Не голосует — накладывает запрет: налог 5%, минимальная прибыль (500 монет и 7%), ликвидность,
запретные часы 19–21 МСК, пятница после 16:00 МСК, первые 4–7 часов нового промо, ночная тишина, травмы."""
import datetime as dt
import re

from . import cards, config, football, ru

PROMO_START = re.compile(r"начал|стартов|стартует|вышл|уже в игре|доступн|live|starts?", re.I)
PROMO_QUIET_H = 6.0         # «первые 4–7 часов»: берём середину диапазона


def after_tax(sell: float) -> int:
    return int(sell * (1 - config.TAX))


def profit(buy: float, sell: float) -> int:
    return after_tax(sell) - int(buy)


def profit_ok(buy: float, sell: float) -> bool:
    p = profit(buy, sell)
    return p >= config.MIN_PROFIT and p >= buy * config.MIN_ROI


def max_buy(sell: float) -> int:
    """Наибольшая цена покупки, при которой прибыль после налога ещё проходит оба порога."""
    s = after_tax(sell)
    cap = min(s - config.MIN_PROFIT, s / (1 + config.MIN_ROI))
    return cards.round_down(cap) if cap >= 150 else 0


def hours_veto(now: float) -> str | None:
    if config.IGNORE_HOURS:
        return None
    w = dt.datetime.fromtimestamp(now, config.TZ)
    if 19 <= w.hour < 21:
        return "запретные часы 19–21 по Москве: на рынке наплыв предложений и цены скачут"
    if w.weekday() == 4 and w.hour >= 16:
        return "пятница после 16:00 по Москве: готовится новое промо, цены нестабильны"
    if 0 <= w.hour < 8:
        return "ночная тишина (00–08 по Москве): сигналы не отправляются"
    return None


def promo_veto(db, now: float) -> str | None:
    for f in db.q("SELECT * FROM facts WHERE kind='promo' AND ts>?", (now - PROMO_QUIET_H * 3600,)):
        if PROMO_START.search(f["text"] or ""):
            return "идут первые часы нового промо: цены ещё не устоялись"
    return None


def check(db, bundle: dict, now: float, hours: bool = True) -> str | None:
    """bundle: {name, buy, sell, liq, kind, ea_id}. Строка-причина запрета или None. hours=False — пробный кандидат: запретные часы и промо его не касаются (это не сигнал)."""
    for v in ((hours_veto(now), promo_veto(db, now)) if hours else ()):
        if v:
            return v
    inj = football.injured(db, now)
    nm = cards.norm(bundle["name"])
    if nm and any(nm == k or (len(nm) > 4 and nm.split()[-1] == k.split()[-1] and nm.split()[0][:1] == k.split()[0][:1]) for k in inj):
        return "по игроку есть сообщение о травме"
    if not profit_ok(bundle["buy"], bundle["sell"]):
        return f"прибыль после налога {ru.num(profit(bundle['buy'], bundle['sell']))} ниже порога (нужно не меньше {config.MIN_PROFIT} монет и 7%)"
    liq, kind = bundle.get("liq"), bundle["kind"]
    if liq is not None and liq < (8 if kind == "flip" else 3):
        return f"низкая ликвидность: продаж за сутки всего {liq}"
    return None
