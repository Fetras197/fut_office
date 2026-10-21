"""Один проход облачного офиса (`python -m office cloud`, GitHub Actions каждые 30 минут): разведка → наборы и прогноз → футбол → мнения отделов → бухгалтер → трейдер → сигналы в Telegram.
Состояние (office.db, журналы) лежит в ветке state. Сбой одного отдела не роняет проход."""
import json
import os
import time
import traceback

from . import accountant, cards, config, db as dbm, football, intel, link, llm, market, sbc, telegram, trader

SIGNALS_FILE = "signals.json"


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def guarded(name: str, fn, default=None):
    t = time.time()
    try:
        r = fn()
        log(f"{name}: ok за {time.time() - t:.1f} с")
        return r
    except Exception as e:     # отдел сломался — остальные работают
        log(f"{name}: СБОЙ {type(e).__name__}: {str(e)[:150]}")
        traceback.print_exc(limit=3)
        return default


def every(db, key: str, sec: float, now: float) -> bool:
    last = db.kv_get(key, 0)
    if now - last < sec:
        return False
    db.kv_set(key, now)
    return True


def publish(db, now: float) -> None:
    rows = db.q("SELECT id,ts,type,name,version,rating,buy_max,sell_min,expires,status FROM signals ORDER BY id DESC LIMIT 40")
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (config.STATE_DIR / SIGNALS_FILE).write_text(json.dumps({"ts": now, "signals": rows}, ensure_ascii=False))


def run_pass(db=None, router=None, send=None, now: float | None = None, dry: bool = False) -> dict:
    config.load_env()
    now = now or time.time()
    db = db or dbm.DB()
    srcs = intel.load_sources()
    if router is None:
        router = llm.RoleRouter()
    out = {}
    phone = guarded("телефон: данные рынка", lambda: link.read_json("phone_feed.json", "phone"))
    ledger = guarded("телефон: журнал кнопок", lambda: link.read_json("ledger.json", "phone"))
    gg = guarded("fut.gg: цены", lambda: cards.feed(True))
    out["intel"] = guarded("разведка", lambda: intel.run(db, router, now), {})
    if every(db, "sbc_refresh", 55 * 60, now):
        out["sbc_new"] = guarded("наборы SBC", lambda: sbc.refresh_sbc(db, now), [])
    guarded("справочник карт", lambda: sbc.crawl_cards(db, pages=14))
    guarded("журнал цен", lambda: sbc.record_prices(db, gg, now))
    if every(db, "football", 100 * 60, now):
        out["football"] = guarded("футбол", lambda: football.run(db, now), {})
    guarded("поведение после промо", lambda: sbc.promo_watch(db, gg, now))
    guarded("проверка прогнозов", lambda: sbc.verify_forecasts(db, gg, now))
    ops = []
    for name, fn in (("мнения: разведка", lambda: intel.opinions(db, now)), ("мнения: рынок", lambda: market.opinions(db, phone, gg, now)),
                     ("мнения: наборы", lambda: sbc.opinions(db, gg, now)), ("мнения: футбол", lambda: football.opinions(db, gg, now))):
        ops += guarded(name, fn, []) or []
    out["opinions"] = {d: sum(1 for o in ops if o.dept == d) for d in ("intel", "market", "sbc", "football")}
    guarded("бухгалтер: журнал", lambda: accountant.ingest_ledger(db, ledger))
    guarded("бухгалтер: проверка цен", lambda: accountant.check_prices(db, cards.feed(False), now))
    guarded("бухгалтер: источники", lambda: (accountant.update_facts(db, now), accountant.update_ratings(db, srcs), accountant.update_dept_stats(db, now)))
    if os.environ.get("OFFICE_HALT") == "1":
        log("OFFICE_HALT=1: новые сигналы не отправляются")
        out["trader"] = {"halt": True}
    else:
        snd = send or (telegram.send if telegram.configured() and not dry else lambda t, b=None: (print(t) or None))
        out["trader"] = guarded("трейдер", lambda: trader.run(db, ops, lambda force: cards.feed(force), now, send=snd), {})
        guarded("совет", lambda: accountant.sunday_council(db, srcs, router, now, send=snd))
    guarded("публикация", lambda: publish(db, now))
    guarded("уборка", lambda: db.prune(now))
    log("итог: " + json.dumps(out, ensure_ascii=False, default=str)[:900])
    return out


def main(cmd: str, args: list[str]) -> int:
    if cmd == "cloud":
        run_pass(dry=os.environ.get("FO_DRY") == "1")
        return 0
    if cmd == "report":
        print(accountant.report_text(dbm.DB(), time.time()))
        return 0
    if cmd == "llmcheck":
        r = llm.RoleRouter().ask("council", {"проверка": True})
        print(r)
        return 0
    if cmd == "prune":
        dbm.DB().prune()
        return 0
    print("команды: cloud | report | llmcheck | prune")
    return 2
