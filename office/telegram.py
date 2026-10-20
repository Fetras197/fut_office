"""Telegram (Bot API): только отправка и правка сообщений. Приём кнопок — на телефоне (getUpdates в облаке не вызываем: конфликт 409)."""
import os

import requests


def _creds():
    return os.environ.get("TG_TOKEN"), os.environ.get("TG_CHAT")


def configured() -> bool:
    return all(_creds())


def _call(method: str, **data) -> dict:
    tok, _ = _creds()
    r = requests.post(f"https://api.telegram.org/bot{tok}/{method}", json=data, timeout=40)
    try:
        return r.json()
    except ValueError:
        return {"ok": False}


def send(text: str, buttons: list[list[tuple[str, str]]] | None = None, html: bool = True) -> int | None:
    """Номер сообщения или None. buttons: ряды [(подпись, callback_data)]."""
    tok, chat = _creds()
    if not tok or not chat:
        return None
    data = {"chat_id": chat, "text": text[:4000], "disable_web_page_preview": True}
    if html:
        data["parse_mode"] = "HTML"
    if buttons:
        data["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row] for row in buttons]}
    try:
        d = _call("sendMessage", **data)
    except requests.RequestException:
        return None
    return d.get("result", {}).get("message_id") if d.get("ok") else None


def edit_markup(msg_id: int, buttons=None) -> bool:
    tok, chat = _creds()
    if not tok:
        return False
    kb = {"inline_keyboard": [[{"text": t, "callback_data": c} for t, c in row] for row in (buttons or [])]}
    try:
        return bool(_call("editMessageReplyMarkup", chat_id=chat, message_id=msg_id, reply_markup=kb).get("ok"))
    except requests.RequestException:
        return False
