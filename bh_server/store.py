from __future__ import annotations

import bisect
import hashlib
import re
import secrets
import sqlite3
import threading
import time

MAX_LEVEL = 100
XP_STEPS = (210, 368, 455, 542, 628, 737, 867, 997, 1127, 1278, 1430, 1582, 1733, 1907, 2080, 2253, 2427, 2622, 2817,
            3012, 3207, 3402, 3618, 3813, 4030, 4247, 4463, 4680, 4918, 5135, 5373, 5612, 5850, 6088, 6327, 6565, 6803,
            7063, 7302, 7562, 7800, 8060, 8320, 8580, 8840, 9122, 9382, 9642, 9923, 10183, 10465, 10747, 11007, 11288,
            11570, 11852, 12113, 12415, 12718, 13000, 13282, 13585, 13867, 14170, 14473, 14755, 15058, 15362, 15665,
            15968, 16272, 16575, 16878, 17182, 17507, 17810, 18113, 18438, 18742, 19067, 19370, 19695, 20020, 20323,
            20648, 20973, 21298, 21623, 21948, 22273, 22598, 22923, 23270, 23595, 23920, 24267, 24592, 24917, 25263)
LEVEL_STARTS = [0]
for _step in XP_STEPS:
    LEVEL_STARTS.append(LEVEL_STARTS[-1] + _step)

GUILDS_SCHEMA = """
CREATE TABLE IF NOT EXISTS account_guilds (
  account_id INTEGER NOT NULL REFERENCES accounts(id), guild_id TEXT NOT NULL, PRIMARY KEY (account_id, guild_id));
CREATE INDEX IF NOT EXISTS account_guilds_guild ON account_guilds (guild_id);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY, discord_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  created REAL NOT NULL, last_login REAL NOT NULL, xp INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS stats (
  account_id INTEGER NOT NULL REFERENCES accounts(id), key TEXT NOT NULL, value INTEGER NOT NULL,
  PRIMARY KEY (account_id, key));
CREATE TABLE IF NOT EXISTS players (
  id INTEGER PRIMARY KEY, key_hash TEXT UNIQUE NOT NULL, account_id INTEGER REFERENCES accounts(id),
  name TEXT NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS players_account ON players (account_id);
CREATE TABLE IF NOT EXISTS name_history (
  player_id INTEGER NOT NULL REFERENCES players(id), old TEXT NOT NULL, new TEXT NOT NULL, time REAL NOT NULL,
  why TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS name_history_player ON name_history (player_id);
CREATE TABLE IF NOT EXISTS matches (
  id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id), time REAL NOT NULL,
  mode TEXT, map TEXT, legend TEXT, result TEXT, xp INTEGER NOT NULL);
"""


ADJECTIVES = ["Swift", "Brave", "Quiet", "Lucky", "Mighty", "Clever", "Bold", "Sunny", "Stormy", "Frosty", "Nimble", "Steady",
              "Wild", "Calm", "Fierce", "Gentle", "Rapid", "Silent", "Golden", "Iron", "Crimson", "Azure", "Jolly", "Sly"]
NOUNS = ["Otter", "Comet", "Falcon", "Badger", "Maple", "Pebble", "Lynx", "Raven", "Panda", "Walrus", "Cobra", "Heron",
         "Beetle", "Marmot", "Puffin", "Gecko", "Bison", "Ferret", "Wombat", "Koala", "Tiger", "Moose", "Squid", "Yak"]
NAME_OK = re.compile(r"^[A-Za-z0-9_\- ]{3,16}$")
RANDOM_NAME = re.compile(r"^[A-Z][a-z]+ [A-Z][a-z]+ \d\d$")


def random_name() -> str:
    return f"{secrets.choice(ADJECTIVES)} {secrets.choice(NOUNS)} {secrets.randbelow(90) + 10}"


TOKEN_IDLE_DAYS = 180


def discord_name(raw: str) -> str | None:
    kept = "".join(c for c in (raw or "") if c.isascii() and (c.isalnum() or c in "_- "))
    name = " ".join(kept.split())[:16].strip()
    return name if check_name(name) is None else None


def check_name(name: str) -> str | None:
    if not NAME_OK.match(name or ""):
        return "3-16 letters, digits, spaces, _ or -"
    if name != name.strip() or "  " in name:
        return "no spaces at the start or end, and no double spaces"
    if name.replace(" ", "").isdigit():
        return "not only digits"
    return None


def level_for(xp: int) -> int:
    return min(MAX_LEVEL, bisect.bisect_right(LEVEL_STARTS, max(0, xp)))


def level_span(xp: int) -> tuple[int, int, int]:
    level = level_for(xp)
    start = LEVEL_STARTS[level - 1]
    return level, start, start + (XP_STEPS[level - 1] if level < MAX_LEVEL else XP_STEPS[-1])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _Rows:
    __slots__ = ("_rows", "_i", "lastrowid", "rowcount")

    def __init__(self, cur: sqlite3.Cursor) -> None:
        self._rows = cur.fetchall() if cur.description is not None else []
        self._i = 0
        self.lastrowid, self.rowcount = cur.lastrowid, cur.rowcount

    def fetchone(self):
        if self._i >= len(self._rows):
            return None
        self._i += 1
        return self._rows[self._i - 1]

    def fetchall(self) -> list:
        rest, self._i = self._rows[self._i:], len(self._rows)
        return rest

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class SharedConnection:

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.RLock()
        self._made: set[str] = set()

    def execute(self, sql: str, *args) -> _Rows:
        with self._lock:
            return _Rows(self._conn.execute(sql, *args))

    def executemany(self, sql: str, rows) -> _Rows:
        rows = list(rows)
        with self._lock:
            return _Rows(self._conn.executemany(sql, rows))

    def executescript(self, script: str) -> None:
        with self._lock:
            self._conn.executescript(script)

    def ensure(self, script: str) -> None:
        with self._lock:
            if script not in self._made:
                self._conn.executescript(script)
                self._made.add(script)

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "SharedConnection":
        with self._lock:
            self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        with self._lock:
            return self._conn.__exit__(*exc)

    @property
    def row_factory(self):
        return self._conn.row_factory

    @row_factory.setter
    def row_factory(self, factory) -> None:
        self._conn.row_factory = factory


class Store:
    def __init__(self, path: str = ":memory:") -> None:
        self.db = SharedConnection(sqlite3.connect(path, check_same_thread=False))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        if "named" not in {r[1] for r in self.db.execute("PRAGMA table_info(players)")}:
            with self.db:
                self.db.execute("ALTER TABLE players ADD COLUMN named INTEGER NOT NULL DEFAULT 0")
                for r in self.db.execute("SELECT id, name FROM players").fetchall():
                    if not RANDOM_NAME.match(r["name"]):
                        self.db.execute("UPDATE players SET named = 1 WHERE id = ?", (r["id"],))
        if "used" not in {r[1] for r in self.db.execute("PRAGMA table_info(sessions)")}:
            with self.db:
                self.db.execute("ALTER TABLE sessions ADD COLUMN used REAL")
                self.db.execute("UPDATE sessions SET used = created")
        self._guilds_made = False

    def close(self) -> None:
        self.db.close()

    def login(self, discord_id: str, name: str) -> tuple[dict, str]:
        now = time.time()
        with self.db:
            self.db.execute("INSERT INTO accounts (discord_id, name, created, last_login) VALUES (?, ?, ?, ?) "
                            "ON CONFLICT(discord_id) DO UPDATE SET name = excluded.name, last_login = excluded.last_login",
                            (discord_id, name, now, now))
            acc = self.db.execute("SELECT * FROM accounts WHERE discord_id = ?", (discord_id,)).fetchone()
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO sessions (token_hash, account_id, created, used) VALUES (?, ?, ?, ?)",
                            (_hash(token), acc["id"], now, now))
        return self.account(acc["id"]), token

    def _guilds_table(self) -> None:
        if not self._guilds_made:
            self.db.executescript(GUILDS_SCHEMA)
            self._guilds_made = True

    def set_guilds(self, account_id: int, guild_ids: list[str] | None) -> None:
        if guild_ids is None:
            return
        self._guilds_table()
        with self.db:
            self.db.execute("DELETE FROM account_guilds WHERE account_id = ?", (account_id,))
            self.db.executemany("INSERT OR IGNORE INTO account_guilds VALUES (?, ?)", [(account_id, g) for g in guild_ids])

    def account_for_token(self, token: str, now: float | None = None) -> dict | None:
        now = time.time() if now is None else now
        key = _hash(token)
        row = self.db.execute("SELECT account_id, created, used FROM sessions WHERE token_hash = ?", (key,)).fetchone()
        if row is None:
            return None
        used = row["used"] if row["used"] is not None else row["created"]
        if now - used > TOKEN_IDLE_DAYS * 86400:
            with self.db:
                self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (key,))
            return None
        if now - used > 3600:
            with self.db:
                self.db.execute("UPDATE sessions SET used = ? WHERE token_hash = ?", (now, key))
        return self.account(row["account_id"])

    def end_session(self, token: str) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),)).rowcount > 0

    def account(self, account_id: int) -> dict | None:
        a = self.db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if a is None:
            return None
        stats = {r["key"]: r["value"] for r in self.db.execute("SELECT key, value FROM stats WHERE account_id = ?", (account_id,))}
        return {"id": a["id"], "discord_id": a["discord_id"], "name": a["name"], "xp": a["xp"],
                "level": level_for(a["xp"]), "stats": stats, "matches": self.matches(account_id),
                "guilds": self.guilds_of(account_id)}

    def guilds_of(self, account_id: int) -> list[str]:
        if not self._guilds_made and self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'account_guilds'").fetchone() is None:
            return []
        self._guilds_made = True
        return [str(r[0]) for r in self.db.execute("SELECT guild_id FROM account_guilds WHERE account_id = ?", (account_id,))]

    def matches(self, account_id: int, limit: int = 20) -> list[dict]:
        rows = self.db.execute("SELECT time, mode, map, legend, result, xp FROM matches WHERE account_id = ? "
                               "ORDER BY time DESC LIMIT ?", (account_id, limit))
        return [dict(r) for r in rows]

    def player(self, key_hash: str) -> dict:
        row = self.db.execute("SELECT * FROM players WHERE key_hash = ?", (key_hash,)).fetchone()
        if row is None:
            name = random_name()
            for _ in range(20):
                if not self.name_taken(name):
                    break
                name = random_name()
            with self.db:
                self.db.execute("INSERT INTO players (key_hash, name, created) VALUES (?, ?, ?)", (key_hash, name, time.time()))
            row = self.db.execute("SELECT * FROM players WHERE key_hash = ?", (key_hash,)).fetchone()
        return dict(row)

    def link_player(self, player_id: int, account_id: int) -> None:
        with self.db:
            self.db.execute("UPDATE players SET account_id = ? WHERE id = ?", (account_id, player_id))

    def name_taken(self, name: str, player_id: int | None = None) -> bool:
        row = self.db.execute("SELECT account_id FROM players WHERE id = ?", (player_id,)).fetchone() if player_id else None
        acc = row["account_id"] if row else None
        return self.db.execute("SELECT 1 FROM players WHERE lower(name) = lower(?) AND id IS NOT ? AND "
                               "(? IS NULL OR account_id IS NULL OR account_id != ?)",
                               (name, player_id, acc, acc)).fetchone() is not None

    def set_player_name(self, player_id: int, name: str, why: str = "picked") -> str | None:
        refused = check_name(name)
        if refused:
            return refused
        if self.name_taken(name, player_id):
            return "that name is taken"
        row = self.db.execute("SELECT name FROM players WHERE id = ?", (player_id,)).fetchone()
        with self.db:
            self.db.execute("UPDATE players SET name = ?, named = 1 WHERE id = ?", (name, player_id))
            if row is not None and row["name"] != name:
                self.db.execute("INSERT INTO name_history VALUES (?, ?, ?, ?, ?)", (player_id, row["name"], name, time.time(), why))
        return None

    def name_history(self, player_id: int) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT old, new, time, why FROM name_history WHERE player_id = ? "
                                                 "ORDER BY time, rowid", (player_id,))]

    def name_at_sign_in(self, player_id: int, account: dict) -> str | None:
        row = self.db.execute("SELECT named FROM players WHERE id = ?", (player_id,)).fetchone()
        if row is None or row["named"]:
            return None
        other = self.db.execute("SELECT name FROM players WHERE account_id = ? AND named = 1 AND id != ? ORDER BY id DESC",
                                (account["id"], player_id)).fetchone()
        name, why = (other["name"], "account") if other else (discord_name(account.get("name", "")), "discord")
        if name is None or self.set_player_name(player_id, name, why) is not None:
            return None
        return name

    def players_of(self, account_id: int) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT id, name FROM players WHERE account_id = ?", (account_id,))]

    def record_match(self, account_id: int, mode: str, map_: str, legend: str, result: str, xp: int) -> dict:
        xp = max(0, int(xp))
        with self.db:
            self.db.execute("INSERT INTO matches (account_id, time, mode, map, legend, result, xp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (account_id, time.time(), mode, map_, legend, result, xp))
            self.db.execute("UPDATE accounts SET xp = xp + ? WHERE id = ?", (xp, account_id))
            for key in ["games"] + (["wins"] if result == "win" else []) + ([f"games:{legend}"] if legend else []):
                self.db.execute("INSERT INTO stats VALUES (?, ?, 1) ON CONFLICT(account_id, key) DO UPDATE SET value = value + 1",
                                (account_id, key))
        return self.account(account_id)
