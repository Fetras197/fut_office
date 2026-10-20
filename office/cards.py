"""Карточки и цены: открытые файлы fut.gg (r2) — цены ВСЕХ карт консольного рынка за 2 запроса; поиск карты по имени игрока.
Без входа в аккаунт EA. Парсеры — те же, что в телефонном ядре (futbin_mcp/sources.py)."""
import json
import re
import time
import unicodedata

from . import config, http

R2 = "https://r2.fut.gg/27"
FEED_KEY = "ps5-dyn"    # у fut.gg один консольный рынок; отдельной цены Xbox нет
_memo: dict = {}


def parse_manifest(text: str) -> dict:
    m = json.loads(text)
    pub = (m.get("_published_at") or {}).get(f"player-prices-{FEED_KEY}")
    need = ("player-prices-index", f"player-prices-{FEED_KEY}")
    if not isinstance(m, dict) or any(not isinstance(m.get(k), str) for k in need) or not isinstance(pub, (int, float)) or pub < 1e9:
        raise ValueError("манифест неожиданной формы")
    return {"version": m.get("_version", 1), "index": m["player-prices-index"], "dyn": m[f"player-prices-{FEED_KEY}"], "published": float(pub)}


def parse_index(text: str) -> list[int]:
    j = json.loads(text)
    ids = [int(j["id0"])]
    for d in j["d"]:
        ids.append(ids[-1] + int(d))
    return ids


def parse_dyn(text: str, ids: list[int]) -> dict[int, int]:
    p = json.loads(text)["p"]
    if len(p) != len(ids):
        raise ValueError("лента цен не совпадает с индексом")
    return {i: int(v) for i, v in zip(ids, p) if isinstance(v, (int, float)) and v > 0}


_CARD_RE = re.compile(r'eaId:(\d+),overall:(\d+),commonName:("(?:[^"\\]|\\.)*"),cardName:("(?:[^"\\]|\\.)*"),rarityName:("(?:[^"\\]|\\.)*")')


def _js(q: str) -> str:
    try:
        return json.loads(q)
    except ValueError:
        return q.strip('"')


def parse_search(page: str) -> list[dict]:
    out, seen = [], set()
    for m in _CARD_RE.finditer(page):
        ea = int(m.group(1))
        if ea not in seen:
            seen.add(ea)
            out.append({"ea_id": ea, "rating": int(m.group(2)), "name": _js(m.group(3)), "card_name": _js(m.group(4)), "rarity": _js(m.group(5))})
    return out


def norm(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", (s or "").lower().replace("æ", "ae").replace("ø", "o").replace("đ", "d").replace("ł", "l"))
    return re.sub(r"[^a-z0-9 ]+", " ", "".join(c for c in s if not unicodedata.combining(c))).strip()


def feed(force: bool = False) -> dict | None:
    """{"prices": {ea_id: цена}, "published": ts, "fetched": ts} или None (сеть). Кэш в памяти прохода."""
    if _memo.get("feed") and not force:
        return _memo["feed"]
    try:
        man = parse_manifest(http.get(f"{R2}/manifest.json", pause_sec=1800) or "")
        ikey = man["index"]
        if _memo.get("ids_key") != ikey:
            _memo["ids"] = parse_index(http.get(f"{R2}/player-prices-index.v{man['version']}.{ikey}.json", timeout=40) or "")
            _memo["ids_key"] = ikey
        prices = parse_dyn(http.get(f"{R2}/player-prices-{FEED_KEY}.v{man['version']}.{man['dyn']}.json", timeout=40) or "", _memo["ids"])
    except (ValueError, KeyError, TypeError):
        return None
    _memo["feed"] = {"prices": prices, "published": man["published"], "fetched": time.time()}
    return _memo["feed"]


def price(ea_id: int, fresh: bool = False) -> int | None:
    f = feed(force=fresh)
    return f["prices"].get(int(ea_id)) if f else None


# ── шаг цены EA ──────────────────────────────────────────────────────────────
def step(p: float) -> int:
    return 50 if p < 1000 else 100 if p < 10000 else 250 if p < 50000 else 500 if p < 100000 else 1000


def round_down(p: float) -> int:
    s = step(p)
    return int(p // s * s)


def round_up(p: float) -> int:
    s = step(p)
    return int(-(-p // s) * s)


def prev_step(p: int) -> int:
    """Цена на один шаг ниже (для «продать от»: чуть ниже ориентира, чтобы лот ушёл)."""
    s = step(p - 1)
    return int(p - s)


# ── поиск карты ──────────────────────────────────────────────────────────────
VERSION_RARITY = {   # ключ версии из разведки → подстроки названия редкости fut.gg (в нижнем регистре)
    "totw": ("team of the week",), "if": ("in-form", "inform", "team of the week"), "icon": ("icon",), "hero": ("hero",),
    "evo": ("evolution",), "gold": ("rare", "common"), "": (),
}


def search(db, name: str, ttl: float = 7 * 86400) -> list[dict]:
    """Карты игрока по имени (кэш в базе). Пусто — не нашли или сайт не ответил."""
    q = norm(name)
    if len(q) < 3:
        return []
    row = db.one("SELECT ea_ids, ts FROM name_map WHERE q=?", (q,))
    if row and time.time() - row["ts"] < ttl:
        ids = json.loads(row["ea_ids"])
        return [c for c in (db.one("SELECT * FROM cards WHERE ea_id=?", (i,)) for i in ids) if c]
    page = http.get("https://www.fut.gg/players/?name=" + q.replace(" ", "+"), timeout=30, pause_sec=1800)
    if page is None:
        return []
    found = parse_search(page)
    for c in found:
        db.x("INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?)", (c["ea_id"], c["name"], c["rating"], c["card_name"], c["rarity"], time.time()))
    db.x("INSERT OR REPLACE INTO name_map VALUES (?,?,?)", (q, json.dumps([c["ea_id"] for c in found]), time.time()))
    return found


def resolve(db, name: str, version: str = "", rating: int = 0) -> dict | None:
    """Единственная подходящая карта или None (никаких догадок: при двух кандидатах — пропуск)."""
    nq = norm(name)
    cands = [c for c in search(db, name) if nq and (nq in norm(c["name"]) or nq in norm(c["card_name"]) or norm(c["name"]).split(" ")[-1:] == nq.split(" ")[-1:])]
    if rating:
        cands = [c for c in cands if c["rating"] == rating]
    keys = VERSION_RARITY.get(version or "", ())
    if keys:
        cands = [c for c in cands if any(k in c["rarity"].lower() for k in keys)]
    elif version in ("", "gold"):
        cands = [c for c in cands if c["rarity"].lower() in ("rare", "common")] or cands
    return cands[0] if len(cands) == 1 else None
