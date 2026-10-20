"""Сеть: общий запрос с повтором, паузой источника после блокировки (403/429) и тихим пропуском."""
import time

import requests

from . import config

_pause: dict[str, float] = {}
_last: dict[str, float] = {}


def host(url: str) -> str:
    return url.split("/")[2] if "//" in url else url


def paused(h: str, now: float | None = None) -> bool:
    return (now or time.time()) < _pause.get(h, 0.0)


def get(url: str, *, timeout: int = 25, min_interval: float = 1.0, headers: dict | None = None, session=None, pause_sec: int = 6 * 3600) -> str | None:
    """Текст ответа или None (блокировка/сеть/пауза). Исключения не пробрасывает: сбой источника не роняет проход."""
    h = host(url)
    if paused(h):
        return None
    wait = _last.get(h, 0.0) + min_interval - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last[h] = time.monotonic()
    try:
        r = (session or requests).get(url, timeout=timeout, headers={"User-Agent": config.UA, "Accept-Language": "ru,en;q=0.8", **(headers or {})})
    except requests.RequestException:
        return None
    if r.status_code in (403, 429):
        _pause[h] = time.time() + pause_sec
        return None
    if r.status_code != 200:
        return None
    return r.text
