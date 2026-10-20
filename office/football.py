"""Отдел 4. Футбольный аналитик: реальные матчи из бесплатных источников без ключей — ESPN (голы, удаления, все главные лиги) и FPL (голы, передачи, бонусные очки, травмы английской лиги).
Кандидаты в команду недели и ин-формы определяются заранее; травмы накладывают запрет."""
import datetime as dt
import json


from . import cards, config, http, sbc
from .model import Opinion

LEAGUES = ["eng.1", "esp.1", "ger.1", "ita.1", "fra.1", "uefa.champions", "uefa.europa", "ned.1", "por.1"]
ESPN = "https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/scoreboard?dates={d}"
MIN_SCORE = 5
TOTW_DEADLINE = (2, 18)       # среда 18:00 по Москве — состав команды недели уже известен


def espn_players(events: list[dict]) -> dict[str, dict]:
    """Игроки из событий ESPN: голы (без автоголов) и красные карточки."""
    out: dict[str, dict] = {}
    for e in events:
        for c in e.get("competitions", [{}])[0].get("details", []) or []:
            ath = (c.get("athletesInvolved") or [{}])[0].get("displayName")
            if not ath:
                continue
            p = out.setdefault(ath, {"goals": 0, "reds": 0})
            if c.get("scoringPlay") and not c.get("ownGoal") and not c.get("shootout"):
                p["goals"] += 1
            if c.get("redCard"):
                p["reds"] += 1
    return out


def fetch_espn(db, now: float, days: int = 4, session=None) -> int:
    got = 0
    today = dt.datetime.fromtimestamp(now, dt.timezone.utc).date()
    for lg in LEAGUES:
        for i in range(days):
            day = today - dt.timedelta(days=i)
            ck = f"espn:{lg}:{day}"
            if db.kv_get(ck) == "done":
                continue
            txt = http.get(ESPN.format(lg=lg, d=day.strftime("%Y%m%d")), session=session, min_interval=0.3, pause_sec=1800)
            if txt is None:
                continue
            try:
                events = json.loads(txt).get("events") or []
            except ValueError:
                continue
            fin = [e for e in events if e.get("status", {}).get("type", {}).get("completed")]
            for name, st in espn_players(fin).items():
                score = 3 * st["goals"] - 3 * st["reds"]
                rid = f"espn:{lg}:{day}:{name}"
                db.x("INSERT OR REPLACE INTO football VALUES (?,?,?,?,?,?,?,?,?,?)", (rid, now, name, lg, st["goals"], 0, 0, 0, score,
                                                                                       f"голов {st['goals']}" + (", удалён" if st["reds"] else "")))
                got += 1
            if events and len(fin) == len(events) and i >= 1:
                db.kv_set(ck, "done")
    return got


def fetch_fpl(db, now: float, session=None) -> int:
    """Английская лига: голы, передачи, бонусы за последний тур; статус травм."""
    boot = http.get("https://fantasy.premierleague.com/api/bootstrap-static/", session=session, timeout=45, pause_sec=1800)
    if boot is None:
        return 0
    try:
        b = json.loads(boot)
        ev = next((e for e in b["events"] if e.get("is_current")), None) or b["events"][0]
        elements = {e["id"]: e for e in b["elements"]}
        teams = {t["id"]: t["name"] for t in b["teams"]}
    except (ValueError, KeyError, StopIteration, IndexError):
        return 0
    inj = {}
    for e in elements.values():
        if e["status"] in ("i", "d", "s") and (e.get("chance_of_playing_next_round") in (0, 25, 50, None)) and e["status"] != "u":
            inj[cards.norm(f"{e['first_name']} {e['second_name']}")] = e["news"][:80]
    db.kv_set("injured", {"ts": now, "names": inj})
    live = http.get(f"https://fantasy.premierleague.com/api/event/{ev['id']}/live/", session=session, timeout=45, pause_sec=1800)
    n = 0
    if live:
        try:
            rows = json.loads(live)["elements"]
        except (ValueError, KeyError):
            rows = []
        for r in rows:
            st, e = r.get("stats", {}), elements.get(r["id"])
            if not e or st.get("minutes", 0) < 30:
                continue
            g, a, bonus = st.get("goals_scored", 0), st.get("assists", 0), st.get("bonus", 0)
            score = 3 * g + 2 * a + bonus - 3 * st.get("red_cards", 0) + (1 if st.get("clean_sheets") and e["element_type"] <= 2 else 0)
            if score < 3:
                continue
            full = f"{e['first_name']} {e['second_name']}"
            db.x("INSERT OR REPLACE INTO football VALUES (?,?,?,?,?,?,?,?,?,?)", (f"fpl:{ev['id']}:{r['id']}", now, full, teams.get(e["team"], ""), g, a, bonus,
                                                                                   1 if cards.norm(full) in inj else 0, score,
                                                                                   f"голов {g}, передач {a}, бонус {bonus}"))
            n += 1
    return n


def injured(db, now: float) -> dict:
    d = db.kv_get("injured") or {}
    return d.get("names", {}) if now - d.get("ts", 0) < 12 * 3600 else {}


def deadline_hours(now: float) -> float:
    """Часов до среды 18:00 по Москве (0 — уже прошла: состав команды недели известен)."""
    w = dt.datetime.fromtimestamp(now, config.TZ)
    if w.weekday() > 2 or (w.weekday() == 2 and w.hour >= TOTW_DEADLINE[1]):
        return 0.0
    tgt = (w + dt.timedelta(days=(TOTW_DEADLINE[0] - w.weekday()))).replace(hour=TOTW_DEADLINE[1], minute=0, second=0, microsecond=0)
    return max(0.0, (tgt - w).total_seconds() / 3600)


def run(db, now: float, session=None) -> dict:
    return {"espn": fetch_espn(db, now, session=session), "fpl": fetch_fpl(db, now, session=session)}


def opinions(db, feed: dict | None, now: float, max_resolve: int = 8) -> list[Opinion]:
    """Кандидаты в команду недели: по ним — карты золотой версии с текущей ценой."""
    ops: list[Opinion] = []
    hrs = deadline_hours(now)
    if not feed or hrs <= 0:
        return ops
    inj = injured(db, now)
    rows = db.q("SELECT * FROM football WHERE ts>? AND score>=? AND injured=0 ORDER BY score DESC LIMIT 25", (now - 6 * 86400, MIN_SCORE))
    seen = set()
    resolved = 0
    for r in rows:
        if r["player"] in seen or cards.norm(r["player"]) in inj:
            continue
        seen.add(r["player"])
        if resolved >= max_resolve:
            break
        resolved += 1
        c = cards.resolve(db, r["player"], "gold")
        if not c or c["rating"] < 74:
            continue
        cur = feed["prices"].get(c["ea_id"])
        if not cur or cur < 400:
            continue
        up = sbc.calibrated_up(db, "totw")
        ops.append(Opinion("football", c["ea_id"], c["name"], "Золотая редкая" if c["rarity"].lower() == "rare" else "Золотая", c["rating"],
                           round(min(0.85, 0.25 + 0.08 * r["score"]), 2), "invest",
                           f"в недавних матчах {r['note']} — вероятный кандидат в команду недели (состав объявят в среду в 18:00 по Москве)",
                           buy_max=cur, sell_ref=int(cur * (1 + up)), ttl_h=min(hrs, 24), horizon_d=min(hrs / 24, 3)))
        sbc.add_forecast(db, now, c["ea_id"], c["name"], "totw", "up", cur, "кандидат в команду недели")
    return ops
