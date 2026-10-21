"""Кнопки под сигналами в облаке: на каждом проходе читаем обновления бота (getUpdates; телефонный бот остановлен, конфликта нет).
«Купил» и «Не нашёл по цене» пишутся сразу; «Продал за…» просит число, следующее число от владельца в течение 6 часов записывается как цена продажи."""
import re

import requests

from . import telegram

PENDING_H = 6


def _updates(offset: int):
    tok, _ = telegram._creds()
    r = requests.get(f"https://api.telegram.org/bot{tok}/getUpdates", params={"offset": offset, "timeout": 0, "allowed_updates": '["callback_query","message"]'}, timeout=30)
    d = r.json()
    return d.get("result", []) if d.get("ok") else None


def _say(text: str):
    return telegram.send(text, None, html=False)


def poll(db, now: float, fetch=_updates, say=_say, call=telegram._call) -> dict:
    """-> {'updates', 'recorded'}. Обновления подтверждаются сдвигом offset (хранится в состоянии)."""
    out = {"updates": 0, "recorded": 0}
    if not telegram.configured():
        return out
    chat = str(telegram._creds()[1])
    off = int(db.kv_get("tg_offset", 0) or 0)
    try:
        ups = fetch(off)
    except requests.RequestException:
        return out
    if ups is None:
        return out
    for u in ups:
        off = max(off, u["update_id"] + 1)
        out["updates"] += 1
        cq = u.get("callback_query")
        if cq and str((cq.get("from") or {}).get("id")) == chat and str(cq.get("data", "")).startswith("of:"):
            out["recorded"] += _callback(db, cq, now, u["update_id"], say, call)
        elif u.get("message") and str(u["message"].get("chat", {}).get("id")) == chat:
            out["recorded"] += _text(db, u["message"], now, u["update_id"], say)
    db.kv_set("tg_offset", off)
    return out


def _add(db, sid: int, action: str, price: int, now: float, ext: str) -> int:
    if db.one("SELECT 1 FROM ledger WHERE ext_id=?", (ext,)):
        return 0
    db.x("INSERT INTO ledger (sid,action,price,ts,ext_id) VALUES (?,?,?,?,?)", (sid, action, price, now, ext))
    return 1


def _callback(db, cq: dict, now: float, uid: int, say, call) -> int:
    try:
        _, act, sid = cq["data"].split(":")
        sid = int(sid)
    except ValueError:
        return 0
    sig = db.one("SELECT id, tg_msg FROM signals WHERE id=?", (sid,))
    try:
        call("answerCallbackQuery", callback_query_id=cq["id"])
    except requests.RequestException:
        pass
    if not sig:
        return 0
    n = 0
    if act == "b":
        n = _add(db, sid, "bought", 0, now, f"u{uid}")
        say(f"Записал по подсказке №{sid}: куплено по максимальной цене. Когда продадите — нажмите «Продал за…».")
    elif act == "n":
        n = _add(db, sid, "notfound", 0, now, f"u{uid}")
        say(f"Записал по подсказке №{sid}: по этой цене карту найти не удалось.")
    elif act == "s":
        db.kv_set("pending_sale", {"sid": sid, "ts": now})
        say(f"За сколько продали карту по подсказке №{sid}? Напишите цену числом, например 52000.")
    return n


def _text(db, msg: dict, now: float, uid: int, say) -> int:
    t = (msg.get("text") or "").strip()
    pend = db.kv_get("pending_sale")
    if not pend or now - pend["ts"] > PENDING_H * 3600 or not re.fullmatch(r"\d[\d\s.,]*", t):
        return 0
    price = int(re.sub(r"\D", "", t))
    if not 150 <= price <= 20_000_000:
        say("Такая цена не похожа на правду. Напишите цену продажи числом от 150.")
        return 0
    db.kv_set("pending_sale", None)
    n = _add(db, pend["sid"], "sold", price, now, f"u{uid}")
    say(f"Записал: подсказка №{pend['sid']} продана за {price:,}. Прибыль после налога посчитает бухгалтер.".replace(",", " "))
    return n
