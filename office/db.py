"""Общая база офиса (SQLite, лежит в ветке state). Короткие соединения, WAL."""
import json
import sqlite3
import time
from contextlib import contextmanager

from . import config

SCHEMA = [
    "CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)",
    # прочитанные записи источников: повторно не разбираем
    "CREATE TABLE IF NOT EXISTS raw (uid TEXT PRIMARY KEY, src TEXT, ts REAL, url TEXT, text TEXT, parsed INTEGER DEFAULT 0)",
    # факты разведки (одна запись = одно утверждение одного источника)
    "CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, kind TEXT, player TEXT, version TEXT, rating INTEGER, "
    "action TEXT, price INTEGER, src TEXT, ts REAL, raw_uid TEXT, text TEXT, outcome INTEGER, ea_id INTEGER)",
    "CREATE INDEX IF NOT EXISTS ix_facts_key ON facts(key, ts)",
    "CREATE TABLE IF NOT EXISTS src_stats (src TEXT PRIMARY KEY, kind TEXT, checks INTEGER DEFAULT 0, hits INTEGER DEFAULT 0, rating REAL DEFAULT 0.5, "
    "last_poll REAL DEFAULT 0, last_ok REAL DEFAULT 0, fails INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1)",
    "CREATE TABLE IF NOT EXISTS cards (ea_id INTEGER PRIMARY KEY, name TEXT, rating INTEGER, card_name TEXT, rarity TEXT, ts REAL)",
    "CREATE TABLE IF NOT EXISTS name_map (q TEXT PRIMARY KEY, ea_ids TEXT, ts REAL)",
    "CREATE TABLE IF NOT EXISTS price_log (ea_id INTEGER, ts REAL, price INTEGER)",
    "CREATE INDEX IF NOT EXISTS ix_pl ON price_log(ea_id, ts)",
    "CREATE TABLE IF NOT EXISTS sbc (sid TEXT PRIMARY KEY, name TEXT, cost INTEGER, category TEXT, first_seen REAL, last_seen REAL, ratings TEXT, url TEXT)",
    "CREATE TABLE IF NOT EXISTS forecasts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, ea_id INTEGER, name TEXT, dept TEXT, direction TEXT, base INTEGER, "
    "reason TEXT, p1 INTEGER, p3 INTEGER, p7 INTEGER, ok1 INTEGER, ok3 INTEGER, ok7 INTEGER)",
    "CREATE TABLE IF NOT EXISTS football (id TEXT PRIMARY KEY, ts REAL, player TEXT, team TEXT, goals INTEGER, assists INTEGER, rating REAL, injured INTEGER, score REAL, note TEXT)",
    "CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, key TEXT, type TEXT, ea_id INTEGER, name TEXT, version TEXT, rating INTEGER, "
    "buy_max INTEGER, sell_min INTEGER, profit INTEGER, expires REAL, score REAL, conf REAL, depts TEXT, srcs TEXT, why TEXT, price_at INTEGER, "
    "chk0 INTEGER, chk10 INTEGER, chk10_ts REAL, tg_msg INTEGER, status TEXT DEFAULT 'sent')",
    "CREATE INDEX IF NOT EXISTS ix_sig_key ON signals(key, ts)",
    "CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, sid INTEGER, action TEXT, price INTEGER, ts REAL, ext_id TEXT UNIQUE)",
    "CREATE TABLE IF NOT EXISTS dept_stats (dept TEXT, day TEXT, votes INTEGER DEFAULT 0, good INTEGER DEFAULT 0, PRIMARY KEY(dept, day))",
]


class DB:
    def __init__(self, path=None):
        self.path = str(path or config.DB_PATH)
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        with self.con() as c:
            try:
                c.execute("PRAGMA journal_mode=WAL")
            except sqlite3.Error:
                pass
            for s in SCHEMA:
                c.execute(s)

    @contextmanager
    def con(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def q(self, sql, args=()):
        with self.con() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        with self.con() as c:
            cur = c.execute(sql, args)
            return cur.lastrowid

    def kv_get(self, k, default=None):
        r = self.one("SELECT v FROM kv WHERE k=?", (k,))
        if not r:
            return default
        try:
            return json.loads(r["v"])
        except ValueError:
            return default

    def kv_set(self, k, v):
        self.x("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, json.dumps(v, ensure_ascii=False)))

    def prune(self, now: float | None = None):
        now = now or time.time()
        self.x("DELETE FROM raw WHERE ts<?", (now - 14 * 86400,))
        self.x("DELETE FROM price_log WHERE ts<?", (now - 21 * 86400,))
        self.x("DELETE FROM facts WHERE ts<?", (now - 60 * 86400,))
