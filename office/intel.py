"""Отдел 1. Разведка: открытые каналы без аккаунта → факты (купи/продай, утечки, SBC, промо, TOTW) → склейка дублей → мнения.
Каждый источник опрашивается не чаще своего интервала; сбой источника не роняет проход."""
import hashlib
from concurrent.futures import ThreadPoolExecutor
import html
import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from . import cards, config, http, llm, ru
from .model import Opinion

NOISE = re.compile(r"(Please open Telegram to view this post|VIEW IN TELEGRAM)", re.I)
LEAK_W = re.compile(r"слив|утечк|выйд|выйдет|выйдут|появ|будет в|leak|предикт|статы", re.I)
SBC_W = re.compile(r"сбч|sbc|задани|objective|набор", re.I)
TOTW_W = re.compile(r"команд[аыу] недели|totw|тотв|toty|ин-?форм|inform", re.I)
PROMO_W = re.compile(r"промо|promo|future stars|звёзды будущего|starts?|стартует|начал|хэллоуин|halloween|ultimate scream|heritage", re.I)
BUY_W = re.compile(r"закуп|покуп|скупа|берём|брать|ловим|купить|buy|снайп", re.I)
FLIP_W = re.compile(r"активный трейд|утром|вечером|снайп|быстр|перекуп|сегодня", re.I)
FODDER_W = re.compile(r"фо[дл]+ер|корм", re.I)
PRICE_LINE = re.compile(r"^[\s•\-\*▪️🔸🔹▫️]*([A-ZÀ-Ý][\wÀ-ÿ'’\.\-]+(?:\s+[A-ZÀ-Ýa-zà-ÿ][\wÀ-ÿ'’\.\-]+){0,3})\s*(>=|<=|≥|≤|>|<|→|–|—|-|:|до)\s*(до\s+)?(\d[\d\s\.,]*\d|\d)\s*(к|k|тыс|K)?", re.M)
NAME_LINE = re.compile(r"^[\s•\-\*▪️🔸🔹▫️]*(?:[\U0001F1E6-\U0001F1FF]{2}\s*)?([A-ZÀ-Ý][\wÀ-ÿ'’\.\-]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’\.\-]+){0,2})\s*(?:[\U0001F1E6-\U0001F1FF]{2})?\s*$", re.M)
RATING_LINE = re.compile(r"^[\s•\-\*▪️🔸🔹▫️]*(8[3-9]|9[0-1])\s*[—–\-:]+\s*>?\s*=?\s*(\d[\d\s\.,]*\d|\d)\s*(к|k)?\s*$", re.M)
DEMAND_RE = re.compile(r"(?:апгрейд|upgrade|улучшени\w*|сбч|sbc)[^\n.]{0,25}?\b(8[3-9])\s?\+|\b(8[3-9])\s?\+[^\n.]{0,25}?(?:апгрейд|upgrade|сбч|sbc)", re.I)
STOP_NAMES = {"Купи", "Футбол", "Boosty", "Тренировки", "Трейд", "Буст", "Rush", "FC", "EA", "SBC", "TOTW", "MLS", "WL"}


def load_sources(path: Path = config.ROOT / "sources.yaml") -> list[dict]:
    d = yaml.safe_load(path.read_text(encoding="utf-8"))
    base = d.get("defaults") or {}
    return [{**base, **s} for s in d.get("sources") or []]


# ── разбор источников (чистые функции) ───────────────────────────────────────
def _plain(frag: str) -> str:
    t = html.unescape(re.sub(r"<br\s*/?>", "\n", frag))
    t = re.sub(r"<[^>]+>", "", t)
    return NOISE.sub("", t).strip()


def parse_tme(page: str, handle: str) -> list[dict]:
    out = []
    for b in page.split("tgme_widget_message_wrap")[1:]:
        m = re.search(r'data-post="([^"]+)"', b)
        d = re.search(r'<time[^>]*datetime="([^"]+)"', b)
        t = re.search(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>\s*(?:<div class="tgme_widget_message_(?:footer|reply|link)|<a class="tgme_widget_message_(?:date|photo)|<div class="tgme_widget_message_(?:info|meta))', b, re.S)
        if not (m and d):
            continue
        text = _plain(t.group(1)) if t else ""
        text = re.sub(r"\n[\d❤👍🌚😭🤨👀💔🔥\s]+$", "", text).strip()   # хвост с числом реакций
        try:
            ts = time.mktime(time.strptime(d.group(1)[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            continue
        out.append({"uid": m.group(1), "ts": ts, "url": "https://t.me/" + m.group(1), "text": text})
    return out


def parse_youtube(xml: str, max_age_h: float = 48) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom", "y": "http://www.youtube.com/xml/schemas/2015"}
    out = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    for e in root.findall("a:entry", ns):
        vid = e.findtext("y:videoId", "", ns)
        pub = e.findtext("a:published", "", ns)
        title = e.findtext("a:title", "", ns)
        try:
            ts = time.mktime(time.strptime(pub[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
        except ValueError:
            continue
        if vid and time.time() - ts <= max_age_h * 3600:
            out.append({"uid": "yt:" + vid, "ts": ts, "url": f"https://www.youtube.com/watch?v={vid}", "text": title})
    return out


def parse_headlines(page: str, base: str, limit: int = 15) -> list[dict]:
    out, seen = [], set()
    for m in re.finditer(r'<a\b[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', page, re.S):
        href, t = m.group(1), re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", m.group(2)))).strip()
        if len(t) < 30 or not re.search(r"/(news|article|articles|guides?|blog)/|fc-?27|ea-fc|fc27", href):
            continue
        url = href if href.startswith("http") else base.rstrip("/") + "/" + href.lstrip("/")
        if url in seen:
            continue
        seen.add(url)
        out.append({"uid": "h:" + hashlib.sha1(url.encode()).hexdigest()[:16], "ts": time.time(), "url": url, "text": t[:300]})
        if len(out) >= limit:
            break
    return out


def parse_reddit(text: str) -> list[dict]:
    try:
        ch = json.loads(text)["data"]["children"]
    except (ValueError, KeyError, TypeError):
        return []
    return [{"uid": "r:" + c["data"]["id"], "ts": float(c["data"].get("created_utc", time.time())), "url": "https://reddit.com" + c["data"].get("permalink", ""),
             "text": (c["data"].get("title", "") + "\n" + c["data"].get("selftext", ""))[:600]} for c in ch if isinstance(c, dict) and "data" in c]


# ── опрос ────────────────────────────────────────────────────────────────────
def ensure_stats(db, srcs: list[dict]) -> None:
    for s in srcs:
        if not db.one("SELECT 1 FROM src_stats WHERE src=?", (s["id"],)):
            db.x("INSERT INTO src_stats (src,kind,rating,enabled) VALUES (?,?,?,?)", (s["id"], s["kind"], s.get("rating", 0.5), 1 if s.get("enabled", True) else 0))


def fetch_items(s: dict, session=None) -> list[dict] | None:
    """Свежие записи источника или None (источник недоступен — пропуск без ошибки)."""
    k = s["kind"]
    if k == "telegram":
        page = http.get(f"https://t.me/s/{s['handle']}", session=session, pause_sec=1800)
        return None if page is None else parse_tme(page, s["handle"])
    if k == "youtube":
        xml = http.get(f"https://www.youtube.com/feeds/videos.xml?channel_id={s['channel_id']}", session=session, pause_sec=1800)
        return None if xml is None else parse_youtube(xml, s.get("max_age_h", 48))
    if k == "site":
        page = http.get(s["url"], session=session, timeout=30, pause_sec=6 * 3600)
        return None if page is None else parse_headlines(page, "/".join(s["url"].split("/")[:3]))
    if k == "reddit":
        txt = http.get(s["url"], session=session, pause_sec=6 * 3600)
        return None if txt is None else parse_reddit(txt)
    return None


def poll(db, s: dict, now: float, session=None) -> int:
    """Опросить один источник, если подошёл срок. Возвращает число новых записей (-1 — не опрашивали)."""
    st = db.one("SELECT * FROM src_stats WHERE src=?", (s["id"],))
    if not st or not st["enabled"] or not s.get("enabled", True):
        return -1
    if now - st["last_poll"] < s.get("interval_min", 30) * 60 - 60:
        return -1
    items = fetch_items(s, session)
    db.x("UPDATE src_stats SET last_poll=? WHERE src=?", (now, s["id"]))
    if items is None:
        db.x("UPDATE src_stats SET fails=fails+1 WHERE src=?", (s["id"],))
        return 0
    db.x("UPDATE src_stats SET last_ok=?, fails=0 WHERE src=?", (now, s["id"]))
    new = 0
    for it in items:
        uid = f"{s['id']}|{it['uid']}"
        if db.one("SELECT 1 FROM raw WHERE uid=?", (uid,)) or now - it["ts"] > 3 * 86400:
            continue
        db.x("INSERT INTO raw (uid,src,ts,url,text) VALUES (?,?,?,?,?)", (uid, s["id"], it["ts"], it["url"], it["text"]))
        new += 1
    return new


# ── извлечение фактов ────────────────────────────────────────────────────────
def _num(s: str, mult: str | None) -> int:
    digits = re.sub(r"[\s,]", "", s).replace(".", "")
    v = int(digits) if digits.isdigit() else 0
    return v * 1000 if mult and v < 1000 else v


def extract_rules(text: str) -> list[dict]:
    """Правила: списки «Имя > цена», перечни игроков в утечках, общий тип поста."""
    low = text.lower()
    facts = []
    buyish = bool(BUY_W.search(low))
    for m in PRICE_LINE.finditer(text):
        name, op, upto, raw, mult = m.group(1).strip(), m.group(2), m.group(3), m.group(4), m.group(5)
        if name.split()[0] in STOP_NAMES:
            continue
        price = _num(raw, mult)
        if not (150 <= price <= 20_000_000):
            continue
        sell = op in (">", ">=", "≥", "→")
        buy = op in ("<", "<=", "≤", "до") or bool(upto)
        if not (sell or buy):
            continue
        facts.append({"kind": "sell" if sell else "buy", "player": name, "version": "", "rating": 0, "action": "sell_above" if sell else "buy_below",
                      "price": price, "note": ("ориентир продажи" if sell else "потолок покупки") + f" {ru.num(price)}", "flip": bool(FLIP_W.search(low)), "buyish": buyish})
    for m in RATING_LINE.finditer(text):   # «84 — >800»: ориентир цены корма по рейтингу
        price = _num(m.group(2), m.group(3))
        if 150 <= price <= 2_000_000:
            facts.append({"kind": "fodder", "player": "", "version": "", "rating": int(m.group(1)), "action": "sell_above", "price": price,
                          "note": f"корм {m.group(1)}: ориентир {ru.num(price)}", "flip": False})
    for m in DEMAND_RE.finditer(text):      # утечка набора-улучшения «83+», «апгрейд 85+»: спрос на корм этого рейтинга (без цены)
        r = int(m.group(1) or m.group(2))
        if 83 <= r <= 89 and not any(f["kind"] == "fodder" and f["rating"] == r for f in facts):
            facts.append({"kind": "fodder", "player": "", "version": "", "rating": r, "action": "none", "price": 0, "note": f"утечка: набор под карты {r}+", "flip": False})
    kind = None
    if TOTW_W.search(low):
        kind = "totw"
    elif SBC_W.search(low) and LEAK_W.search(low):
        kind = "sbc"
    elif LEAK_W.search(low):
        kind = "leak"
    elif PROMO_W.search(low):
        kind = "promo"
    elif FODDER_W.search(low) and BUY_W.search(low):
        kind = "fodder"
    if kind and not [f for f in facts if f["kind"] != "fodder"]:
        names = [n.strip() for n in NAME_LINE.findall(text) if n.split()[0] not in STOP_NAMES and len(n) > 2]
        for n in names[:12]:
            facts.append({"kind": kind, "player": n, "version": "", "rating": 0, "action": "none", "price": 0, "note": text.split("\n")[0][:110]})
    if kind and not facts:
        facts.append({"kind": kind, "player": "", "version": "", "rating": 0, "action": "none", "price": 0, "note": text.split("\n")[0][:110]})
    return facts


VIDEO_EVERY = 3 * 3600       # пересказ видео — не чаще раза в 3 часа (он долгий)
LLM_ERR = [""]      # последняя ошибка цепочки нейросетей (для журнала прохода)
KINDS = {"buy", "sell", "leak", "sbc", "promo", "totw", "fodder"}
ACTIONS_OK = {"buy": "buy_below", "sell": "sell_above", "buy_below": "buy_below", "sell_above": "sell_above", "hold": "none", "none": "none"}


def validate_llm(obj: dict, valid_ids: set[str]) -> list[dict]:
    """Строгая проверка ответа нейросети: допустимые значения, цена в разумных пределах, id поста существует."""
    out = []
    for f in (obj.get("facts") if isinstance(obj, dict) else None) or []:
        if not isinstance(f, dict) or str(f.get("id")) not in valid_ids or f.get("kind") not in KINDS:
            continue
        try:
            price = int(f.get("price") or 0)
            rating = int(f.get("rating") or 0)
        except (TypeError, ValueError):
            continue
        if price and not 150 <= price <= 20_000_000 or rating and not 40 <= rating <= 99:
            continue
        player = str(f.get("player") or "").strip()[:60]
        if player and not re.fullmatch(r"[A-Za-zÀ-ÿ'’\.\- ]{2,60}", player):
            player = ""
        act = ACTIONS_OK.get(f.get("action") or "none", "none")
        if price and act == "none":
            continue
        base = {"uid": str(f["id"]), "player": player, "version": str(f.get("version") or "")[:12], "rating": rating, "note": str(f.get("note") or "")[:140]}
        out.append({**base, "kind": f["kind"], "action": act, "price": price})
        try:
            sp = int(f.get("sell_price") or 0)
        except (TypeError, ValueError):
            sp = 0
        if f["kind"] == "buy" and player and 150 <= sp <= 20_000_000:      # «купить до X, продать по Y» — две стороны одной сделки
            out.append({**base, "kind": "sell", "action": "sell_above", "price": sp})
    return out


def extract_llm(router, posts: list[dict]) -> list[dict]:
    if not posts or router is None:
        return []
    try:
        obj = router.ask("extract", {"posts": [{"id": p["uid"], "text": p["text"][:900]} for p in posts]})
    except llm.LLMError as e:
        LLM_ERR[0] = str(e)[:200]
        return []
    return validate_llm(obj, {p["uid"] for p in posts})


def fact_key(kind: str, player: str, ea_id: int | None, note: str, rating: int = 0) -> str:
    if kind == "fodder" and rating and not player:
        return f"fodder:r{rating}"
    who = f"c{ea_id}" if ea_id else cards.norm(player) or hashlib.sha1(cards.norm(note).encode()).hexdigest()[:8]
    group = kind
    return f"{group}:{who}"


def process_raw(db, router, now: float, max_llm_posts: int = 12, max_resolve: int = 14) -> dict:
    """Необработанные записи → факты. Сначала правила; посты с ключевыми словами, где правила ничего не дали, — нейросети."""
    rows = db.q("SELECT * FROM raw WHERE parsed=0 ORDER BY ts DESC LIMIT 150")
    stats = {"posts": len(rows), "rules": 0, "llm": 0}
    llm_posts, resolved = [], 0
    pending: list[tuple[dict, dict]] = []
    for r in rows:
        src = r["src"]
        if "|yt:" in r["uid"] and "\n" not in (r["text"] or "") and now - r["ts"] < 12 * 3600:
            continue          # ждёт пересказа видео
        fs = extract_rules(r["text"]) if r["text"] else []
        cyr = re.search(r"[А-Яа-я]{4,}", r["text"] or "") and not re.search(r"[A-Z][a-zà-ÿ]{2,}", r["text"] or "")
        needs_llm = (not fs or all(not f["player"] for f in fs)) and len(r["text"] or "") >= 40 and (LEAK_W.search(r["text"].lower()) or TOTW_W.search(r["text"].lower()) or SBC_W.search(r["text"].lower()) or BUY_W.search(r["text"].lower()) or src.startswith(("teddy", "prorec")) or cyr)
        if needs_llm and len(llm_posts) < max_llm_posts:
            llm_posts.append({"uid": r["uid"], "text": r["text"]})
        for f in fs:
            pending.append((r, f))
        stats["rules"] += len(fs)
        if not needs_llm:
            db.x("UPDATE raw SET parsed=1 WHERE uid=?", (r["uid"],))
    by_uid = {r["uid"]: r for r in rows}
    if llm_posts and now < (db.kv_get("llm_backoff") or 0):
        llm_posts = []        # недавно нейросети были недоступны: не тратим минуты прохода на повторы
    if llm_posts:
        LLM_ERR[0] = ""
        got = extract_llm(router, llm_posts)
        stats["llm"] = len(got)
        stats["llm_posts"] = len(llm_posts)
        if LLM_ERR[0] and not got:
            db.kv_set("llm_backoff", now + 20 * 60)
        if LLM_ERR[0]:
            stats["llm_err"] = LLM_ERR[0]
        for f in got:
            pending.append((by_uid[f["uid"]], f))
        for p in llm_posts:
            if LLM_ERR[0] and not got and now - by_uid[p["uid"]]["ts"] < 18 * 3600:
                continue          # нейросети недоступны (лимит): пост разберём позже, пока он свежий
            db.x("UPDATE raw SET parsed=1 WHERE uid=?", (p["uid"],))   # пост разобран нейросетью (даже если фактов нет)
    for r, f in pending:
        ea = None
        if f["player"] and f["kind"] in ("buy", "sell", "totw", "leak", "sbc", "fodder", "promo") and resolved < max_resolve:
            c = cards.resolve(db, f["player"], f.get("version", ""), f.get("rating", 0))
            resolved += 1
            ea = c["ea_id"] if c else None
        elif f["player"]:
            row = db.one("SELECT ea_ids FROM name_map WHERE q=?", (cards.norm(f["player"]),))
            if row:
                c = cards.resolve(db, f["player"], f.get("version", ""), f.get("rating", 0))
                ea = c["ea_id"] if c else None
        key = fact_key(f["kind"], f["player"], ea, f["note"], f.get("rating", 0))
        if db.one("SELECT 1 FROM facts WHERE key=? AND src=? AND ts>?", (key, r["src"], r["ts"] - 6 * 3600)):
            continue
        db.x("INSERT INTO facts (key,kind,player,version,rating,action,price,src,ts,raw_uid,text,ea_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
             (key, f["kind"], f["player"], f.get("version", ""), f.get("rating", 0), f["action"], f["price"], r["src"], r["ts"], r["uid"], (f["note"] + ("|flip" if f.get("flip") else "")), ea))
    return stats


# ── склейка дублей ───────────────────────────────────────────────────────────
def src_rating(db, src: str) -> float:
    r = db.one("SELECT rating FROM src_stats WHERE src=?", (src,))
    return r["rating"] if r else 0.5


def confidence(ratings: list[float]) -> float:
    """Независимые источники: 1 − ∏(1 − 0,8·рейтинг). Один источник с точностью 0,5 даёт 0,4; три — около 0,78."""
    p = 1.0
    for r in ratings:
        p *= 1 - 0.8 * r
    return round(min(0.95, 1 - p), 3)


def merge(db, now: float, window_h: float = 72) -> list[dict]:
    """События: факты с одним ключом из разных источников склеены, уверенность растёт."""
    rows = db.q("SELECT * FROM facts WHERE ts>? ORDER BY ts", (now - window_h * 3600,))
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["key"], []).append(r)
    out = []
    for key, rs in groups.items():
        by_src: dict[str, dict] = {}
        for r in rs:
            by_src[r["src"]] = r
        ratings = [src_rating(db, s) for s in by_src]
        prices = sorted(r["price"] for r in rs if r["price"])
        last = rs[-1]
        out.append({"key": key, "kind": last["kind"], "ea_id": last["ea_id"], "player": last["player"], "version": last["version"], "rating": last["rating"],
                    "action": last["action"], "price": prices[len(prices) // 2] if prices else 0, "srcs": list(by_src), "n": len(by_src),
                    "conf": confidence(ratings), "ts": last["ts"], "note": last["text"].split("|")[0], "flip": any(r["text"].endswith("|flip") for r in rs), "ids": [r["id"] for r in rs]})
    return sorted(out, key=lambda e: -e["conf"])


def _title(db, src: str) -> str:
    return next((s.get("title", src) for s in load_sources() if s["id"] == src), src)


def opinions(db, now: float) -> list[Opinion]:
    """Мнения разведки: ориентиры цен из каналов по картам, которые нашлись в базе fut.gg."""
    ops = []
    for e in merge(db, now):
        if not e["ea_id"] or e["conf"] < 0.3:
            continue
        c = db.one("SELECT * FROM cards WHERE ea_id=?", (e["ea_id"],))
        if not c:
            continue
        ver = ru.VERSIONS.get(c["rarity"].lower().replace(" ", "_"), None) or _ver_ru(c["rarity"])
        names = ", ".join(_title(db, s) for s in e["srcs"])
        if e["kind"] == "sell" and e["price"]:
            ops.append(Opinion("intel", e["ea_id"], c["name"], ver, c["rating"], e["conf"], "channel",
                               f"источники ({names}) дают ориентир продажи {ru.num(e['price'])}" + (f", подтверждений: {e['n']}" if e["n"] > 1 else ""),
                               srcs=e["srcs"], sell_ref=e["price"], ttl_h=24, horizon_d=2))
        elif e["kind"] == "buy" and e["price"]:
            ops.append(Opinion("intel", e["ea_id"], c["name"], ver, c["rating"], e["conf"], "channel",
                               f"источники ({names}) советуют брать не дороже {ru.num(e['price'])}", srcs=e["srcs"], buy_max=e["price"], ttl_h=24, horizon_d=2))
    return ops


RARITY_RU = [("team of the week", "Команда недели"), ("in-form", "Ин-форм"), ("icon", "Икона"), ("hero", "Герой"), ("future stars", "Звёзды будущего"),
             ("destined", "Путь к славе"), ("star performer", "Звёздный игрок"), ("evolution", "Эволюция"), ("potm", "Игрок месяца"), ("rare", "Золотая редкая"), ("common", "Золотая")]


def _ver_ru(rarity: str) -> str:
    low = (rarity or "").lower()
    return next((v for k, v in RARITY_RU if k in low), "Особая версия")


def run(db, router, now: float | None = None, session=None, srcs: list[dict] | None = None) -> dict:
    now = now or time.time()
    srcs = srcs if srcs is not None else load_sources()
    ensure_stats(db, srcs)
    got = {}

    def one(s):
        try:
            return s["id"], poll(db, s, now, session)
        except Exception as e:   # сбой одного источника не должен ронять проход
            return s["id"], f"сбой: {type(e).__name__}"
    with ThreadPoolExecutor(max_workers=6) as ex:       # источники опрашиваются параллельно: проход короче, минуты Actions целее
        for sid, n in ex.map(one, srcs):
            if n != -1:
                got[sid] = n
    stats = process_raw(db, router, now)
    stats["sources"] = got
    video_ok = now - (db.kv_get("video_last") or 0) >= VIDEO_EVERY
    for s in srcs:     # ролики пересказываем в последнюю очередь и не чаще раза в 3 часа: пересказ разберётся на следующем проходе
        if video_ok and s["kind"] == "youtube" and s.get("enabled", True):
            try:
                if _summarize_videos(db, s, now, limit=1):
                    db.kv_set("video_last", now)
                    video_ok = False
            except Exception as e:
                stats.setdefault("video_err", str(type(e).__name__))
    return stats


def _summarize_videos(db, s: dict, now: float, limit: int = 2) -> bool:
    """Новые ролики: Gemini смотрит видео и пересказывает; пересказ складывается как запись для разбора."""
    done = False
    rows = db.q("SELECT * FROM raw WHERE src=? AND parsed=0 AND uid LIKE ? ORDER BY ts DESC LIMIT ?", (s["id"], f"{s['id']}|yt:%", limit))
    for r in rows:
        if "\n" in (r["text"] or ""):
            continue                                   # пересказ уже добавлен
        txt = llm.video_summary(r["url"])
        done = True                                   # попытка была (даже неудачная) — следующая не раньше чем через 3 часа
        if txt:
            db.x("UPDATE raw SET text=?, parsed=0 WHERE uid=?", (f"{r['text']}\n{txt}"[:3000], r["uid"]))
        elif now - r["ts"] > 12 * 3600:
            db.x("UPDATE raw SET parsed=1 WHERE uid=?", (r["uid"],))   # пересказать не удалось за 12 часов: по заголовку не разбираем
    return done
