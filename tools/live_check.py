"""Живая проверка (этап 6): цикл проходов офиса с реальной сетью на заданное время.
Данные «аналитика рынка» берутся из базы работающего телефонного бота (так же, как их выложит office_link). Запретные часы по умолчанию отключены (FO_IGNORE_HOURS=1),
сообщения в Telegram не отправляются — сигналы пишутся в базу, а проверка идёт по двум независимым источникам:
 1) лента цен fut.gg через ≥ 10 минут после сигнала: цена карты не выше «Купить до»;
 2) если карта есть в базе сканера Futbin — её самый дешёвый лот на тот же момент.
Использование: python3 tools/live_check.py --minutes 120 --every 5 --out reports/live_check.md"""
import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("FO_IGNORE_HOURS", "1")
os.environ.setdefault("FO_STATE", str(ROOT / "state_live"))

H = Path("/data/data/com.termux/files/home")
BOT_DB = H / "fetras/fut_bot/bot.db"
FB_CACHE = H / "fetras/futbin_mcp/cache.db"


def phone_feed(now):
    """Копия office_link.build_feed без зависимости от aiogram: те же условия отбора."""
    sys.path.insert(0, str(H / "fetras/fut_bot"))
    from bot import scan_db, office_link  # noqa: E402

    def map_fn(fid):
        try:
            con = sqlite3.connect(f"file:{FB_CACHE}?mode=ro", uri=True)
            r = con.execute("SELECT other_id,status FROM site_map WHERE site='futgg' AND fb_id=?", (fid,)).fetchone()
            con.close()
            return {"other_id": r[0], "status": r[1]} if r else None
        except sqlite3.Error:
            return None
    return office_link.build_feed(now, path=BOT_DB, map_fn=map_fn)


def futbin_lowest(fb_id):
    con = sqlite3.connect(f"file:{BOT_DB}?mode=ro", uri=True)
    try:
        r = con.execute("SELECT price_now, price_ts FROM scan_cards WHERE card_id=?", (fb_id,)).fetchone()
    finally:
        con.close()
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=120)
    ap.add_argument("--every", type=int, default=5)
    ap.add_argument("--out", default=str(ROOT / "reports" / "live_check.md"))
    a = ap.parse_args()
    from office import accountant, cards, cloud, db as dbm, link, llm

    db = dbm.DB()
    router = llm.RoleRouter()
    end = time.time() + a.minutes * 60
    log = []
    sent = []
    fbid_by_sig = {}
    n = 0
    while time.time() < end:
        n += 1
        now = time.time()
        feed = phone_feed(now)
        link.read_json = lambda path, branch="phone", session=None, _f=feed: _f if path == "phone_feed.json" else None
        try:
            out = cloud.run_pass(db=db, router=router, send=lambda t, b=None: sent.append(t) or len(sent), now=now)
        except Exception as e:  # проход не должен убивать проверку
            out = {"error": repr(e)[:200]}
        for s in db.q("SELECT id,ea_id FROM signals"):
            if s["id"] not in fbid_by_sig:
                m = [r for r in feed["hang"] if r.get("ea_id") == s["ea_id"]]
                fbid_by_sig[s["id"]] = m[0]["fb_id"] if m else None
        log.append({"pass": n, "t": time.strftime("%H:%M:%S"), "hang_rows": len(feed["hang"]), "trader": out.get("trader"), "opinions": out.get("opinions")})
        print(json.dumps(log[-1], ensure_ascii=False), flush=True)
        # проверка сигналов (через ≥ 10 минут после отправки)
        time.sleep(max(1, a.every * 60 - (time.time() - now)))
        accountant.check_prices(db, cards.feed(True), time.time())   # цены берём свежие
    # итог
    sigs = db.q("SELECT * FROM signals ORDER BY id")
    lines = ["# Живая проверка (этап 6)", "", f"Проходов: {n}, длительность: {a.minutes} мин, сигналов: {len(sigs)}.", ""]
    ok = tot = fb_ok = fb_tot = 0
    for s in sigs:
        chk = s["chk10"]
        good = chk is not None and chk <= s["buy_max"]
        if chk is not None:
            tot += 1
            ok += 1 if good else 0
        fbl = futbin_lowest(fbid_by_sig.get(s["id"])) if fbid_by_sig.get(s["id"]) else None
        if fbl:
            fb_tot += 1
            fb_ok += 1 if fbl[0] <= s["buy_max"] else 0
        lines.append(f"- №{s['id']} {s['name']} ({s['type']}): купить до {s['buy_max']}, при отправке {s['price_at']}, через ≥10 мин {chk}, Futbin сейчас {fbl[0] if fbl else '—'} → {'нашёлся' if good else 'НЕ нашёлся'}")
    lines += ["", f"Нашёлся по цене (fut.gg): {ok} из {tot}" + (f" = {100 * ok / tot:.0f}%" if tot else ""), f"Нашёлся по цене (Futbin, где карта есть в базе сканера): {fb_ok} из {fb_tot}" + (f" = {100 * fb_ok / fb_tot:.0f}%" if fb_tot else ""), "",
              "Воронка по проходам:", "```", *[json.dumps(x, ensure_ascii=False) for x in log], "```"]
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text("\n".join(lines))
    print("\n".join(lines[:6 + len(sigs)]))


if __name__ == "__main__":
    main()
