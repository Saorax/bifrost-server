from __future__ import annotations

import json
import re
import sqlite3
import struct
import threading
import time

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

CREATE, DISBAND, INVITE, INVITE_ANSWER, KICK, PROMOTE, DEMOTE, MAKE_LEADER, LEAVE = 100, 101, 102, 103, 104, 105, 106, 107, 108
NOTICE_SET, CHAT, SPECTATE, JOIN, PARTY_INVITE, RECRUITING, TAGS, DESCRIPTION, APPLY = 109, 110, 111, 112, 113, 114, 115, 116, 117
APPLICANT_ANSWER, RECRUIT_LIST, BATTLE_LOG, LEADERBOARD, PLAYER_INFO, MISSIONS_REFRESH, MISSIONS = 118, 119, 120, 121, 122, 123, 124
HANDLED = set(range(CREATE, MISSIONS + 1))
AVATAR = 64

SYSTEM, CREATE_FAILED, STATE, RANK_CHANGED, LEFT, INVITED, JOINED, NOTICE = 2485, 2486, 2487, 2489, 2490, 2491, 2492, 2493
CLAN_CHAT, EMBLEM, TAGS_SET, DESCRIPTION_SET, RECRUITING_SET, APPLICATION, RECRUIT_PAGE = 2494, 2498, 2499, 2500, 2501, 2502, 2503
MISSION_LIST, BATTLE_LOG_LIST = 2504, 2505
MEMBER_STATUS, MEMBER_ELO = 2488, 2508
MEMBER_AVATAR = 2507
WEEK_SCORE, CONTRIBUTIONS = 2509, 2510
DUEL = 2518
BATTLE_LOG_KEEP, BATTLE_LOG_DAYS = 50, 21
DUEL_RUNNING = 1
EVENT_RESET = 2552
BOARDS = (2511, 2512, 2513, 2514, 2515)
BOARD_EMPTY = 2516
BOARD_ROWS = 100
POPUP = 11013

LEADER, OFFICER, MEMBER, RECRUIT = 0, 1, 2, 3
ONLY_LEADER_DISBANDS, CANNOT_INVITE, ALREADY_IN_CLAN = 3, 7, 8
MAX_CAPACITY, INVITE_MISSING, LEADER_CANNOT_QUIT = 10, 13, 16
NAME_LENGTH, NAME_CHARS, NAME_TAKEN, IN_CLAN_ON_CREATE, MUST_BE_MEMBER = 28, 29, 30, 34, 38
APP_PENDING, APP_GONE = 1, 6
XP_UPDATE, XP_GAINED = 2496, 2497
XP_STEP, LEVEL_CAP = 1000, 100
TIER_FROM = (1, 10, 25, 50, 75)
TIER_CAPACITY = (15, 15, 50, 100, 150, 200)
NAME_OK = re.compile(r"^[A-Za-z0-9 ]{2,20}$")
MAX_TEXT = 300
MAX_TAGS = 16
GUILD_HISTORY = 11092
CHAT_KEEP, CHAT_DAYS = 50, 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS clans (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, lname TEXT UNIQUE NOT NULL, notice TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '', tags TEXT NOT NULL DEFAULT '', emblem INTEGER NOT NULL DEFAULT 0,
  recruiting INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS clan_members (
  key TEXT PRIMARY KEY, clan_id INTEGER NOT NULL, rank INTEGER NOT NULL, joined REAL NOT NULL);
CREATE INDEX IF NOT EXISTS clan_members_clan ON clan_members (clan_id);
CREATE TABLE IF NOT EXISTS clan_applications (
  key TEXT NOT NULL, clan_id INTEGER NOT NULL, created REAL NOT NULL, PRIMARY KEY (key, clan_id));
CREATE TABLE IF NOT EXISTS clan_duels (
  week INTEGER NOT NULL, clan_id INTEGER NOT NULL, opponent INTEGER NOT NULL DEFAULT 0, points INTEGER NOT NULL DEFAULT 0,
  won INTEGER NOT NULL DEFAULT 0, settled INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (week, clan_id));
CREATE TABLE IF NOT EXISTS clan_battles (
  id INTEGER PRIMARY KEY, clan_id INTEGER NOT NULL, at REAL NOT NULL, playlist INTEGER NOT NULL, mates TEXT NOT NULL,
  opponent INTEGER NOT NULL, opponent_name TEXT NOT NULL, opponent_emblem INTEGER NOT NULL, opponent_tier INTEGER NOT NULL,
  opponents TEXT NOT NULL, points INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS clan_chat (
  id INTEGER PRIMARY KEY, clan_id INTEGER NOT NULL, at REAL NOT NULL, pid INTEGER NOT NULL, name TEXT NOT NULL,
  text TEXT NOT NULL, officers INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS clan_chat_clan ON clan_chat (clan_id, id);
"""
COLUMNS = (("clans", "xp", "INTEGER NOT NULL DEFAULT 0"), ("clan_members", "xp", "INTEGER NOT NULL DEFAULT 0"),
           ("clan_members", "week_xp", "INTEGER NOT NULL DEFAULT 0"), ("clan_members", "week", "INTEGER NOT NULL DEFAULT 0"),
           ("clan_members", "last_week_xp", "INTEGER NOT NULL DEFAULT 0"))


def clan_level(xp: int) -> tuple[int, int, int]:
    level, start = 1, 0
    while level < LEVEL_CAP and xp >= start + XP_STEP * level:
        start += XP_STEP * level
        level += 1
    return level, start, start + XP_STEP * min(level, LEVEL_CAP - 1)


def clan_level_start(level: int) -> int:
    return XP_STEP * level * (level - 1) // 2


def clan_tier(level: int) -> int:
    return sum(1 for f in TIER_FROM if level >= f)


def tier_capacity(tier: int) -> int:
    return TIER_CAPACITY[min(tier, len(TIER_CAPACITY) - 1)]


def week_now(now: float | None = None) -> int:
    return int(((now if now is not None else time.time()) / 86400 + 3) // 7)


def week_ends(week: int) -> float:
    return (7 * (week + 1) - 3) * 86400.0


def f32(w: BitWriter, v: float) -> BitWriter:
    return w.bits_(32, struct.unpack(">I", struct.pack(">f", v))[0])


class ClanDB:
    def __init__(self, db) -> None:
        self.db = db
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)
            for table, column, kind in COLUMNS:
                if column not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def q(self, sql: str, args: tuple = ()) -> list:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock, self.db:
            return self.db.execute(sql, args).rowcount

    def clan(self, cid: int) -> dict | None:
        rows = self.q("SELECT id, name, notice, description, tags, emblem, recruiting, created, xp FROM clans WHERE id = ?", (cid,))
        if not rows:
            return None
        r = rows[0]
        return {"id": r[0], "name": r[1], "notice": r[2], "description": r[3], "tags": r[4], "emblem": r[5],
                "recruiting": bool(r[6]), "created": r[7], "xp": r[8]}

    def clans(self) -> list[dict]:
        return [c for c in (self.clan(r[0]) for r in self.q("SELECT id FROM clans ORDER BY id")) if c]

    def membership(self, key: str) -> tuple[int, int] | None:
        rows = self.q("SELECT clan_id, rank FROM clan_members WHERE key = ?", (key,))
        return (rows[0][0], rows[0][1]) if rows else None

    def members(self, cid: int) -> list[tuple[str, int, float]]:
        return [(r[0], r[1], r[2]) for r in self.q("SELECT key, rank, joined FROM clan_members WHERE clan_id = ? ORDER BY rank, joined", (cid,))]

    def member_xp(self, key: str) -> tuple[int, int]:
        rows = self.q("SELECT xp, week_xp, week FROM clan_members WHERE key = ?", (key,))
        if not rows:
            return 0, 0
        return rows[0][0], rows[0][1] if rows[0][2] == week_now() else 0

    def member_elo(self, key: str) -> tuple[int, int]:
        if not key.startswith("d:"):
            return 0, 0
        try:
            rows = self.q("SELECT r.ladder, r.rating FROM accounts a JOIN ratings r ON r.account_id = a.id "
                          "WHERE a.discord_id = ? AND r.ladder IN ('1v1', '2v2')", (key[2:],))
        except sqlite3.Error:
            return 0, 0
        got = dict(rows)
        return int(got.get("1v1") or 0), int(got.get("2v2") or 0)

    def add_xp(self, key: str, amount: int) -> tuple[int, int, int] | None:
        week = week_now()
        with self.lock, self.db:
            row = self.db.execute("SELECT m.clan_id, c.xp FROM clan_members m JOIN clans c ON c.id = m.clan_id "
                                  "WHERE m.key = ?", (key,)).fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE clans SET xp = xp + ? WHERE id = ?", (amount, row[0]))
            self.db.execute("UPDATE clan_members SET xp = xp + ?, last_week_xp = CASE WHEN week = ? THEN last_week_xp "
                            "WHEN week = ? THEN week_xp ELSE 0 END, week_xp = CASE WHEN week = ? THEN week_xp + ? ELSE ? END, "
                            "week = ? WHERE key = ?", (amount, week, week - 1, week, amount, amount, week, key))
        return row[0], row[1], row[1] + amount

    def board(self) -> list[dict]:
        week = week_now()
        rows = self.q("SELECT c.id, c.name, c.emblem, c.xp, "
                      "COALESCE(SUM(CASE WHEN m.week = ? THEN m.week_xp ELSE 0 END), 0), "
                      "COALESCE(SUM(CASE WHEN m.week = ? THEN m.last_week_xp WHEN m.week = ? THEN m.week_xp ELSE 0 END), 0) "
                      "FROM clans c LEFT JOIN clan_members m ON m.clan_id = c.id GROUP BY c.id", (week, week, week - 1))
        try:
            elo = dict(self.q("SELECT m.clan_id, AVG(r.rating) FROM clan_members m JOIN accounts a ON m.key = 'd:' || a.discord_id "
                              "JOIN ratings r ON r.account_id = a.id AND r.ladder = '1v1' GROUP BY m.clan_id"))
        except sqlite3.OperationalError:
            elo = {}
        return [{"id": r[0], "name": r[1], "emblem": r[2], "xp": r[3], "week": r[4], "last_week": r[5],
                 "elo": int(round(elo.get(r[0]) or 0))} for r in rows]

    def duels(self, week: int | None = None) -> dict[int, int]:
        week = week_now() if week is None else week
        rows = dict(self.q("SELECT clan_id, opponent FROM clan_duels WHERE week = ?", (week,)))
        guilds = {g["id"]: g for g in self.board()}
        if not rows:
            self._settle(week - 1, guilds)
            order = sorted(guilds.values(), key=lambda g: (-clan_tier(clan_level(g["xp"])[0]), -g["xp"], g["id"]))
            for i in range(0, len(order), 2):
                a = order[i]["id"]
                b = order[i + 1]["id"] if i + 1 < len(order) else 0
                rows[a] = b
                if b:
                    rows[b] = a
        else:
            for cid in sorted(set(guilds) - set(rows)):
                free = next((c for c, o in sorted(rows.items()) if o == 0 and c in guilds), None)
                rows[cid] = free or 0
                if free:
                    rows[free] = cid
        with self.lock, self.db:
            for cid, opp in rows.items():
                self.db.execute("INSERT INTO clan_duels (week, clan_id, opponent) VALUES (?, ?, ?) "
                                "ON CONFLICT (week, clan_id) DO UPDATE SET opponent = excluded.opponent", (week, cid, opp))
        return {c: (o if o in guilds else 0) for c, o in rows.items() if c in guilds}

    def _settle(self, week: int, guilds: dict) -> None:
        rows = self.q("SELECT clan_id, opponent FROM clan_duels WHERE week = ? AND settled = 0", (week,))
        if not rows or week != week_now() - 1:
            return
        points = {cid: (guilds[cid]["last_week"] if cid in guilds else 0) for cid, _ in rows}
        with self.lock, self.db:
            for cid, opp in rows:
                won = 1 if opp and opp in points and points[cid] > points[opp] else 0
                self.db.execute("UPDATE clan_duels SET points = ?, won = ?, settled = 1 WHERE week = ? AND clan_id = ?",
                                (points[cid], won, week, cid))

    def add_battle(self, cid: int, playlist: int, mates: list, opponent: dict | None, opponents: list, points: int,
                   at: float | None = None) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO clan_battles (clan_id, at, playlist, mates, opponent, opponent_name, opponent_emblem, "
                            "opponent_tier, opponents, points) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (cid, time.time() if at is None else at, playlist, json.dumps(mates[:3]),
                             opponent["id"] if opponent else 0, opponent["name"] if opponent else "",
                             opponent["emblem"] if opponent else 0,
                             clan_tier(clan_level(opponent["xp"])[0]) if opponent else 0, json.dumps(opponents[:3]), points))
            self.db.execute("DELETE FROM clan_battles WHERE clan_id = ? AND id NOT IN (SELECT id FROM clan_battles "
                            "WHERE clan_id = ? ORDER BY id DESC LIMIT ?)", (cid, cid, BATTLE_LOG_KEEP))

    def battles(self, cid: int) -> list[dict]:
        cols = ("at", "playlist", "mates", "opponent", "opponent_name", "opponent_emblem", "opponent_tier", "opponents", "points")
        rows = self.q(f"SELECT {', '.join(cols)} FROM clan_battles WHERE clan_id = ? ORDER BY id DESC", (cid,))
        out = [dict(zip(cols, r)) for r in rows]
        for b in out:
            b["mates"], b["opponents"] = json.loads(b["mates"]), json.loads(b["opponents"])
        return out

    def duel_wins(self, cid: int) -> int:
        return self.q("SELECT COUNT(*) FROM clan_duels WHERE clan_id = ? AND won = 1", (cid,))[0][0]

    def capacity(self, cid: int) -> int:
        clan = self.clan(cid)
        return tier_capacity(clan_tier(clan_level(clan["xp"] if clan else 0)[0]))

    def create(self, name: str, key: str) -> int:
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO clans (name, lname, notice, created) VALUES (?, ?, ?, ?)",
                                  (name, name.lower(), "Welcome to your new guild!", time.time()))
            cid = int(cur.lastrowid)
            self.db.execute("INSERT INTO clan_members (key, clan_id, rank, joined) VALUES (?, ?, ?, ?)", (key, cid, LEADER, time.time()))
            self.db.execute("DELETE FROM clan_applications WHERE key = ?", (key,))
        return cid

    def add(self, key: str, cid: int, rank: int = RECRUIT) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT OR REPLACE INTO clan_members (key, clan_id, rank, joined) VALUES (?, ?, ?, ?)", (key, cid, rank, time.time()))
            self.db.execute("DELETE FROM clan_applications WHERE key = ?", (key,))

    def remove(self, key: str) -> None:
        self.x("DELETE FROM clan_members WHERE key = ?", (key,))

    def set_rank(self, key: str, rank: int) -> None:
        self.x("UPDATE clan_members SET rank = ? WHERE key = ?", (rank, key))

    def add_chat(self, cid: int, pid: int, name: str, text: str, officers: bool) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO clan_chat (clan_id, at, pid, name, text, officers) VALUES (?, ?, ?, ?, ?, ?)",
                            (cid, time.time(), pid, name, text, 1 if officers else 0))
            self.db.execute("DELETE FROM clan_chat WHERE clan_id = ? AND id NOT IN (SELECT id FROM clan_chat WHERE clan_id = ? "
                            "ORDER BY id DESC LIMIT ?)", (cid, cid, CHAT_KEEP))

    def chat(self, cid: int, officers: bool, now: float | None = None) -> list[tuple[float, int, str, str, bool]]:
        since = (now or time.time()) - CHAT_DAYS * 86400
        rows = self.q("SELECT at, pid, name, text, officers FROM clan_chat WHERE clan_id = ? AND at >= ? ORDER BY id",
                      (cid, since))
        return [(r[0], r[1], r[2], r[3], bool(r[4])) for r in rows if officers or not r[4]]

    def disband(self, cid: int) -> None:
        with self.lock, self.db:
            for t, col in (("clan_members", "clan_id"), ("clan_applications", "clan_id"), ("clan_chat", "clan_id"), ("clans", "id")):
                self.db.execute(f"DELETE FROM {t} WHERE {col} = ?", (cid,))

    def set(self, cid: int, column: str, value) -> None:
        assert column in ("notice", "description", "tags", "emblem", "recruiting")
        self.x(f"UPDATE clans SET {column} = ? WHERE id = ?", (value, cid))

    def applications_of(self, key: str) -> list[int]:
        return [r[0] for r in self.q("SELECT clan_id FROM clan_applications WHERE key = ?", (key,))]

    def applicants(self, cid: int) -> list[tuple[str, float]]:
        return [(r[0], r[1]) for r in self.q("SELECT key, created FROM clan_applications WHERE clan_id = ? ORDER BY created", (cid,))]

    def apply(self, key: str, cid: int) -> bool:
        with self.lock, self.db:
            return self.db.execute("INSERT OR IGNORE INTO clan_applications VALUES (?, ?, ?)", (key, cid, time.time())).rowcount > 0

    def withdraw(self, key: str, cid: int) -> bool:
        return self.x("DELETE FROM clan_applications WHERE key = ? AND clan_id = ?", (key, cid)) > 0


class Clans:

    def __init__(self, social, store=None, log=None) -> None:
        self.social, self.store = social, store
        self.log = log or (lambda line: None)
        self.db = ClanDB(store.db) if store is not None else None
        self.invites: dict[str, tuple[int, int]] = {}
        self.week = week_now()

    @classmethod
    def of(cls, social, store=None, log=None) -> "Clans":
        hub = getattr(social, "clans", None)
        if hub is None or (hub.db is None and store is not None):
            hub = cls(social, store, log)
            social.clans = hub
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
            listeners = getattr(social, "status_listeners", None)
            if listeners is not None:
                listeners.append(hub.status_changed)
        return hub

    @staticmethod
    def _progress(xp: int) -> tuple[int, int, float, str, float]:
        level, start, nxt = clan_level(xp)
        tier = clan_tier(level)
        bar = 1.0 if level >= LEVEL_CAP else (xp - start) / (nxt - start)
        text = f"{xp:,}" if level >= LEVEL_CAP else f"{xp - start:,}/{nxt - start:,}"
        if tier >= len(TIER_FROM):
            tier_bar = 1.0
        else:
            lo, hi = clan_level_start(TIER_FROM[tier - 1]), clan_level_start(TIER_FROM[tier])
            tier_bar = (xp - lo) / (hi - lo)
        return level, tier, max(0.0, min(1.0, bar)), text, max(0.0, min(1.0, tier_bar))

    @staticmethod
    def _tier_text(xp: int) -> str:
        tier = clan_tier(clan_level(xp)[0])
        if tier >= len(TIER_FROM):
            return ""
        lo, hi = clan_level_start(TIER_FROM[tier - 1]), clan_level_start(TIER_FROM[tier])
        return f"{xp - lo:,}/{hi - lo:,}"

    def add_xp(self, key: str, amount: int) -> None:
        if self.db is None or amount <= 0:
            return
        got = self.db.add_xp(key, amount)
        if got is None:
            return
        cid, before, after = got
        level, tier, bar, text, tier_bar = self._progress(after)
        pid = self.social.stable_id(key) or 0
        total, _ = self.db.member_xp(key)
        w = BitWriter().uint(level)
        f32(w, bar).string(text).bool(False).uint(tier).string(self._tier_text(after))
        f32(w, tier_bar).bool(False).uint(pid).uint(total)
        self._broadcast(cid, XP_UPDATE, w)
        self._broadcast(cid, WEEK_SCORE, BitWriter().uint(cid).uint(self._weekly(cid)[3]))
        _, week_xp = self.db.member_xp(key)
        self._broadcast(cid, CONTRIBUTIONS, BitWriter().bool(True).uint(cid).uint(pid).uint(total).uint(week_xp).uint(0).bool(False))
        clan = self.db.clan(cid)
        for s in self.social._sessions_for(key):
            self._send(s, XP_GAINED, BitWriter().uint(amount).string(clan["name"] if clan else ""))
        old_level = clan_level(before)[0]
        self.log(f"clan {cid}: +{amount} XP from {key} ({before} -> {after}, level {level}, tier {tier})")
        if level > old_level:
            for s in self._online(cid):
                self._popup(s, f"Your guild reached level {level}!" +
                            (f" Tier {tier}: room for {tier_capacity(tier)} members." if tier > clan_tier(old_level) else ""))

    def on_result(self, entry, verdict, out, rep) -> None:
        gained: dict[int, int] = {}
        for pid, prog in (out.get("progress") or {}).items():
            key = self._key_of(out, pid)
            if key is None:
                continue
            before, after = prog.get("xp", (0, 0))
            gained[pid] = int(after) - int(before)
            self.add_xp(key, gained[pid])
        if rep is not None and self.db is not None:
            self._log_battle(entry, out, rep, gained)
        if self.db is not None:
            for pid in (out.get("accounts") or {}):
                key = self._key_of(out, pid)
                mem = self.db.membership(key) if key else None
                if mem:
                    elo1, elo2 = self.db.member_elo(key)
                    w = BitWriter().uint(self.social.stable_id(key) or 0).small(min(elo1, 0xFFFF)).small(min(elo2, 0xFFFF))
                    self._broadcast(mem[0], MEMBER_ELO, w)

    def avatar_changed(self, key: str) -> None:
        mem = self.db.membership(key) if (self.db is not None and key.startswith("d:")) else None
        pid = self.social.stable_id(key) if mem else None
        if pid is not None:
            portrait = self.social.portrait(pid)
            self._broadcast(mem[0], MEMBER_AVATAR, BitWriter().uint(pid).small(min(portrait, 0xFFFF)))

    def status_changed(self, key: str, status: tuple[int, int]) -> None:
        mem = self.db.membership(key) if (self.db is not None and key.startswith("d:")) else None
        if mem is None:
            return
        pid = self.social.stable_id(key)
        if pid is not None:
            self._broadcast(mem[0], MEMBER_STATUS, BitWriter().uint(pid).uint(status[0]).uint(status[1]))

    def _key_of(self, out: dict, pid: int) -> str | None:
        acc = (out.get("accounts") or {}).get(pid)
        row = self.db.q("SELECT discord_id FROM accounts WHERE id = ?", (acc,)) if (acc and self.db) else []
        return f"d:{row[0][0]}" if row else None

    def _log_battle(self, entry, out: dict, rep: dict, gained: dict[int, int]) -> None:
        players = rep.get("players") or []
        names = getattr(entry, "names", None) or {}
        result = stats.outcomes(players, rep.get("bots", ()))
        side = {p["player"]: (p.get("team") or -(i + 1)) for i, p in enumerate(players)}
        guild = {}
        for p in players:
            key = self._key_of(out, p["player"])
            mem = self.db.membership(key) if key else None
            if mem:
                guild[p["player"]] = mem[0]
        playlist = int(getattr(entry, "playlist", 0) or out.get("playlist", 0) or 0)
        done = set()
        for pid, cid in guild.items():
            if (cid, side[pid]) in done:
                continue
            done.add((cid, side[pid]))
            mates = [q for q in players if side[q["player"]] == side[pid] and guild.get(q["player"]) == cid]
            others = [q for q in players if side[q["player"]] != side[pid]]
            opp_guilds = {guild.get(q["player"]) for q in others}
            opponent = self.db.clan(next(iter(opp_guilds))) if (len(opp_guilds) == 1 and None not in opp_guilds) else None
            label = lambda q: [q["player"], names.get(q["player"]) or self._name_of(out, q["player"])]
            opponents = [label(q) for q in others] + [[0, "Bot"] for _ in rep.get("bots", ())]
            won = result.get(pid) == "win"
            points = sum(max(0, gained.get(q["player"], 0)) for q in mates) if won else 0
            self.db.add_battle(cid, playlist, [label(q) for q in mates], opponent, opponents, min(points, 0x7FFF))
            self.log(f"clan {cid}: battle log row ({'won' if won else 'not won'}, {points} points)")

    def _name_of(self, out: dict, pid: int) -> str:
        key = self._key_of(out, pid)
        return self.social.display(key) if key else f"Player {pid}"

    def _weekly(self, cid: int) -> tuple[int, int, int, int]:
        guilds = self.db.board()
        me = next((g for g in guilds if g["id"] == cid), None)
        if me is None:
            return 0, 0, 0, 0

        def place(metric: str) -> int:
            if not me[metric]:
                return 0
            return 1 + sum(1 for g in guilds if (-g[metric], -g["xp"], g["id"]) < (-me[metric], -me["xp"], me["id"]))
        return place("last_week"), me["last_week"], place("week"), me["week"]

    def _send_board(self, s, board: int) -> None:
        if board >= len(BOARDS):
            return
        metric = ("last_week", "week", "xp", "elo", "xp")[board]
        guilds = sorted(self.db.board(), key=lambda g: (-g[metric], -g["xp"], g["id"]))[:BOARD_ROWS]
        if not guilds:
            self._send(s, BOARD_EMPTY, BitWriter())
            return
        w = BitWriter().small(1).small(1).small(len(guilds))
        for place, g in enumerate(guilds, 1):
            leader = next((k for k, rank, _ in self.db.members(g["id"]) if rank == LEADER), None)
            tier = clan_tier(clan_level(g["xp"])[0])
            w.uint(place).uint(g["id"]).string(g["name"]).uint(g["emblem"]).uint(g["last_week"])
            w.uint(min(g["xp"], 0x7FFFFFFF)).small(min(g["elo"], 0xFFFF)).uint(g["week"])
            w.uint(min(g["xp"], 0x7FFFFFFF)).small(tier)
            w.uint((self.social.stable_id(leader) or 0) if leader else 0).string(self.social.display(leader) if leader else "")
        self._send(s, BOARDS[board], w)
        s.log(f"guild leaderboard {board} sent ({len(guilds)} guilds)")

    def _duel_record(self, w: BitWriter, mine: bool, duel_id: int, cid: int, guilds: list[dict]) -> BitWriter:
        clan = self.db.clan(cid)
        g = next((x for x in guilds if x["id"] == cid), {"week": 0, "xp": 0, "id": cid})
        place = 0
        if g["week"]:
            place = 1 + sum(1 for x in guilds if (-x["week"], -x["xp"], x["id"]) < (-g["week"], -g["xp"], g["id"]))
        members = self.db.members(cid)
        w.bool(True).bool(mine).uint(duel_id)
        w.uint(clan["emblem"]).string(clan["name"]).uint(0).string("")
        w.uint(clan_tier(clan_level(clan["xp"])[0])).uint(len(members)).uint(self.db.duel_wins(cid)).uint(place)
        w.uint(g["week"]).uint(0).uint(0).uint(0).bool(False).uint(len(members))
        for key, rank, _ in members:
            pid = self.social.stable_id(key) or 0
            w.uint(pid).string(self.social.display(key)).uint(self.social.portrait(pid)).uint(rank)
            w.uint(self.db.member_xp(key)[1]).uint(0)
        w.uint(sum(1 for k, _, _ in members if self.social._sessions_for(k)))
        return w

    def send_duel(self, s) -> None:
        mem = self.db.membership(self.social.key(s))
        if not mem:
            return
        cid, week = mem[0], week_now()
        opp = self.db.duels(week).get(cid, 0)
        guilds = self.db.board()
        self._send(s, DUEL, self._duel_record(BitWriter(), True, week, cid, guilds))
        if opp:
            self._send(s, DUEL, self._duel_record(BitWriter(), False, week, opp, guilds))
        else:
            self._send(s, DUEL, BitWriter().bool(False).bool(False).uint(0))
        s.log(f"guilds duel sent: clan {cid} vs " + (f"clan {opp}" if opp else "nobody (sits this week out)"))

    def _battle_log(self, s, cid: int) -> BitWriter:
        t0 = getattr(s, "login_t0", None) or time.time()
        rows = [b for b in self.db.battles(cid) if time.time() - b["at"] < BATTLE_LOG_DAYS * 86400]
        w = BitWriter().uint(len(rows))
        for b in rows:
            w.uint(int(b["at"] - t0) & 0xFFFFFFFF).small(min(b["playlist"], 0xFFFF))
            for pid, name in (b["mates"] + [[0, ""]] * 3)[:3]:
                w.uint(pid).string(name).small(0)
            w.uint(b["opponent"]).string(b["opponent_name"] or "No guild").small(b["opponent_emblem"]).byte(b["opponent_tier"])
            for pid, name in (b["opponents"] + [[0, ""]] * 3)[:3]:
                w.uint(pid).string(name).small(0)
            w.bool(b["points"] < 0).small(min(abs(b["points"]), 0xFFFF))
        return w

    @staticmethod
    def _clock(s, when: float) -> int:
        t0 = getattr(s, "login_t0", None) or time.time()
        return max(1, int(when - t0))

    def _send(self, s, t: int, w: BitWriter) -> None:
        self.social._send(s, Packet(t, w.bytes()))

    def _popup(self, s, text: str) -> None:
        self.social._notice(s, text)

    def _fail(self, s, code: int, create: bool = False) -> None:
        if create:
            self._send(s, CREATE_FAILED, BitWriter().uint(code))
        else:
            self._send(s, SYSTEM, BitWriter().uint(code).string(""))

    def _online(self, cid: int) -> list:
        keys = {k for k, _, _ in self.db.members(cid)}
        return [s for s in self.social.sessions if self.social.key(s) in keys]

    def _broadcast(self, cid: int, t: int, w: BitWriter, officers_only: bool = False) -> None:
        ranks = {k: r for k, r, _ in self.db.members(cid)}
        for s in self._online(cid):
            if not officers_only or ranks.get(self.social.key(s), RECRUIT) <= OFFICER:
                self._send(s, t, w)

    def _member_row(self, w: BitWriter, key: str, rank: int, joined: float) -> BitWriter:
        pid = self.social.stable_id(key) or 0
        status, detail = self.social.key_status(key)
        w.uint(pid).string(self.social.display(key)).uint(rank).uint(int(joined)).uint(int(time.time()))
        w.uint(status).uint(detail).string("")
        xp, week_xp = self.db.member_xp(key)
        w.uint(xp).uint(week_xp).uint(xp).uint(week_xp).uint(0)
        elo1, elo2 = self.db.member_elo(key)
        portrait = self.social.portrait(pid) if pid else 0
        w.small(min(portrait, 0xFFFF)).small(min(elo1, 0xFFFF)).small(min(elo2, 0xFFFF)).byte(0).uint(0)
        return w

    def state(self, key: str, session=None) -> BitWriter:
        w = BitWriter().uint(0)
        mem = self.db.membership(key) if self.db else None
        clan = self.db.clan(mem[0]) if mem else None
        w.bool(clan is not None)
        if clan is not None:
            members = self.db.members(clan["id"])
            level, tier, bar, text, tier_bar = self._progress(clan["xp"])
            last_place, last_score, week_place, week_score = self._weekly(clan["id"])
            w.uint(clan["id"]).string(clan["name"]).small(clan["emblem"]).uint(tier).uint(last_place).string("")
            w.uint(0).uint(level).string(text)
            f32(w, bar).string(self._tier_text(clan["xp"]))
            f32(w, tier_bar).uint(0).string(f"{clan['xp']:,}").string(clan["notice"]).string(clan["tags"]).byte(1 if clan["recruiting"] else 0)
            w.string(clan["description"])
            w.uint(week_place).uint(last_score).uint(week_score)
            ends = self._clock(session, week_ends(week_now())) if session is not None else 0
            w.uint(DUEL_RUNNING if session is not None else 0).uint(ends).uint(ends).uint(0)
            for k, r, joined in members:
                w.bool(True)
                self._member_row(w, k, r, joined)
            w.bool(False)
            apps = self.db.applicants(clan["id"]) if mem[1] <= OFFICER else []
            w.bool(bool(apps))
            if apps:
                for k, created in apps:
                    w.bool(True)
                    self._application_row(w, clan["id"], k, APP_PENDING)
                w.bool(False)
        mine = self.db.applications_of(key) if self.db and clan is None else []
        w.bool(True)
        for cid in mine:
            w.bool(True).uint(cid).uint(APP_PENDING)
        w.bool(False)
        return w

    def _application_row(self, w: BitWriter, cid: int, key: str, status: int) -> BitWriter:
        w.uint(cid).uint(self.social.stable_id(key) or 0).string(self.social.display(key)).uint(0).uint(0).uint(0)
        w.byte(status)
        w.bool(False).small(0).uint(0).bool(False).small(0).bool(False).small(0).uint(0).string("")
        return w

    def send_state(self, s) -> None:
        if self.db is None:
            return
        self._send(s, STATE, self.state(self.social.key(s), s))

    def week_turn(self, now: float | None = None) -> int:
        week = week_now(now)
        if week == self.week or self.db is None:
            return 0
        self.week = week
        with self.social.lock:
            sessions = list(self.social.sessions)
        sent = 0
        for s in sessions:
            if self.social.pid(s) is None or self.db.membership(self.social.key(s)) is None:
                continue
            self._send(s, EVENT_RESET, BitWriter())
            self.send_state(s)
            sent += 1
        if sent:
            self.log(f"guilds: week {week} began, the duel reset (2552) and a fresh 2487 sent to {sent} game(s)")
        return sent

    def online(self, session) -> None:
        if self.db is None or self.social.pid(session) is None:
            return
        self.send_state(session)
        if not getattr(session, "guild_history_sent", False):
            session.guild_history_sent = True
            self.send_history(session)

    def history(self, key: str, now: float | None = None) -> BitWriter | None:
        mem = self.db.membership(key) if self.db else None
        if mem is None:
            return None
        now = now or time.time()
        lines = self.db.chat(mem[0], mem[1] <= OFFICER, now)
        if not lines:
            return None
        w = BitWriter().uint(len(lines))
        for at, pid, name, text, officers in lines:
            w.uint(max(0, int(now - at))).uint(pid).string(name).string(text).bool(officers)
        return w

    def send_history(self, s) -> int:
        w = self.history(self.social.key(s))
        if w is None:
            return 0
        self._send(s, GUILD_HISTORY, w)
        return 1

    def handle(self, session, p: Packet) -> bool:
        if p.type == AVATAR:
            return self._emblem(session, p)
        if p.type not in HANDLED:
            return False
        if self.db is None or self.social.pid(session) is None:
            return p.type in (CREATE, RECRUIT_LIST)
        try:
            with self.social.lock:
                self._dispatch(session, p.type, BitReader(p.payload))
        except IndexError:
            session.log(f"clan packet {p.type} too short ({len(p.payload)} bytes)")
        return True

    def _emblem(self, s, p: Packet) -> bool:
        try:
            r = BitReader(p.payload)
            avatar, for_guild = r.uint(), r.bool()
        except IndexError:
            return False
        if not for_guild or self.db is None or self.social.pid(s) is None:
            return False
        with self.social.lock:
            mem = self.db.membership(self.social.key(s))
            if not mem or mem[1] > OFFICER:
                return True
            self.db.set(mem[0], "emblem", avatar)
            s.log(f"clan {mem[0]} emblem set to avatar {avatar} by player {self.social.pid(s)}")
            self._broadcast(mem[0], EMBLEM, BitWriter().small(avatar))
        return True

    def _dispatch(self, s, t: int, r: BitReader) -> None:
        key, me, log = self.social.key(s), self.social.pid(s), s.log
        mem = self.db.membership(key)
        cid, rank = mem if mem else (0, RECRUIT)
        if t in (SPECTATE, JOIN, PARTY_INVITE):
            target = r.uint()
            w = BitWriter().uint(target)
            if t == JOIN:
                w.bool(False)
            self.social.handle(s, Packet({SPECTATE: 83, JOIN: 84, PARTY_INVITE: 34}[t], w.bytes()))
        elif t == CREATE:
            r.byte()
            name = " ".join(r.string().split())
            if not key.startswith("d:"):
                self._popup(s, "Sign in with Discord (Options > ACCOUNT) to create a clan.")
                return self._fail(s, MUST_BE_MEMBER, create=True)
            if mem:
                return self._fail(s, IN_CLAN_ON_CREATE, create=True)
            if not 2 <= len(name) <= 20:
                return self._fail(s, NAME_LENGTH, create=True)
            if not NAME_OK.match(name):
                return self._fail(s, NAME_CHARS, create=True)
            if self.db.q("SELECT 1 FROM clans WHERE lname = ?", (name.lower(),)):
                return self._fail(s, NAME_TAKEN, create=True)
            cid = self.db.create(name, key)
            log(f"clan {cid} {name!r} created by player {me}")
            for ss in self.social._sessions_for(key):
                self.send_state(ss)
        elif t == RECRUIT_LIST:
            rows = [c for c in self.db.clans() if c["recruiting"]]
            w = BitWriter().uint(0).uint(1)
            for c in rows:
                members = self.db.members(c["id"])
                leader = next((k for k, rk, _ in members if rk == LEADER), None)
                w.bool(True).uint(c["id"]).string(c["name"]).uint(self.social.stable_id(leader) or 0 if leader else 0)
                w.string(self.social.display(leader) if leader else "")
                tier = clan_tier(clan_level(c["xp"])[0])
                w.uint(len(members)).uint(tier_capacity(tier)).uint(c["emblem"]).uint(tier).uint(0).uint(c["xp"])
                w.uint(int(c["created"])).string(c["tags"])
            w.bool(False)
            self._send(s, RECRUIT_PAGE, w)
        elif t == APPLY:
            target, apply = r.uint(), r.bool()
            for _ in range(r.uint()):
                r.uint()
            if mem:
                return self._fail(s, ALREADY_IN_CLAN)
            if not key.startswith("d:"):
                return self._popup(s, "Sign in with Discord (Options > ACCOUNT) to join a clan.")
            clan = self.db.clan(target)
            if clan is None:
                return
            if apply:
                self.db.apply(key, target)
            else:
                self.db.withdraw(key, target)
            status = APP_PENDING if apply else APP_GONE
            row = self._application_row(BitWriter(), target, key, status)
            self._send(s, APPLICATION, row)
            self._broadcast(target, APPLICATION, row, officers_only=True)
            log(f"player {me} {'applied to' if apply else 'withdrew from'} clan {target}")
        elif t == BATTLE_LOG:
            self._send(s, BATTLE_LOG_LIST, self._battle_log(s, cid) if mem else BitWriter().uint(0))
        elif t == MISSIONS:
            self._send(s, MISSION_LIST, BitWriter().bool(False))
        elif t == LEADERBOARD:
            self._send_board(s, r.uint())
        elif t == MISSIONS_REFRESH:
            self.send_duel(s)
        elif t == PLAYER_INFO:
            r.uint()
        elif t == INVITE_ANSWER:
            target, _, accept = r.uint(), r.uint(), r.bool()
            inv = self.invites.pop(key, None)
            if inv is None or inv[0] != target or not accept:
                if accept:
                    self._fail(s, INVITE_MISSING)
                return
            if mem:
                return self._fail(s, ALREADY_IN_CLAN)
            self._join(s, key, target, "an invite")
        elif not mem:
            log(f"clan packet {t} from player {me}, not in a clan")
            self._fail(s, MUST_BE_MEMBER)
        elif t == INVITE:
            target = r.uint()
            tkey = self.social.key_for_pid(target)
            if tkey is None or not tkey.startswith("d:") or not self.social._sessions_for(tkey):
                return self._fail(s, CANNOT_INVITE)
            if self.db.membership(tkey):
                return self._fail(s, ALREADY_IN_CLAN)
            if len(self.db.members(cid)) >= self.db.capacity(cid):
                return self._fail(s, MAX_CAPACITY)
            self.invites[tkey] = (cid, me)
            clan = self.db.clan(cid)
            for ts in self.social._sessions_for(tkey):
                self._send(ts, INVITED, BitWriter().uint(cid).string(clan["name"]).uint(me).string(self.social.name(s)))
            log(f"player {me} invited player {target} to clan {cid}")
        elif t == APPLICANT_ANSWER:
            r.uint()
            target, accept = r.uint(), r.bool()
            tkey = self.social.key_for_pid(target)
            if rank > OFFICER or tkey is None or not self.db.withdraw(tkey, cid):
                return
            row = self._application_row(BitWriter(), cid, tkey, APP_GONE)
            self._broadcast(cid, APPLICATION, row, officers_only=True)
            for ts in self.social._sessions_for(tkey):
                self._send(ts, APPLICATION, row)
            if accept and not self.db.membership(tkey):
                self._join(None, tkey, cid, "an application")
        elif t in (KICK, PROMOTE, DEMOTE, MAKE_LEADER):
            target = r.uint()
            tkey = self.social.key_for_pid(target)
            tm = self.db.membership(tkey) if tkey else None
            if tm is None or tm[0] != cid or tkey == key:
                return self._fail(s, MUST_BE_MEMBER)
            old = tm[1]
            if t == KICK:
                if rank > OFFICER or old <= rank:
                    return
                self.db.remove(tkey)
                clan = self.db.clan(cid)
                w = BitWriter().uint(cid).uint(target).bool(True).string(clan["name"]).string(self.social.name(s))
                for ss in self.social._sessions_for(tkey):
                    self._send(ss, LEFT, w)
                self._broadcast(cid, LEFT, w)
                log(f"player {me} kicked player {target} from clan {cid}")
                return
            if t == MAKE_LEADER:
                if rank != LEADER:
                    return
                self.db.set_rank(tkey, LEADER)
                self.db.set_rank(key, OFFICER)
                self._broadcast(cid, RANK_CHANGED, BitWriter().uint(me).uint(target).string(self.social.display(tkey)).uint(old).uint(LEADER))
                self._broadcast(cid, RANK_CHANGED, BitWriter().uint(me).uint(me).string(self.social.name(s)).uint(LEADER).uint(OFFICER))
                return
            new = old - 1 if t == PROMOTE else old + 1
            if rank > OFFICER or old <= rank or new <= rank or not LEADER < new <= RECRUIT:
                return
            self.db.set_rank(tkey, new)
            self._broadcast(cid, RANK_CHANGED, BitWriter().uint(me).uint(target).string(self.social.display(tkey)).uint(old).uint(new))
        elif t in (LEAVE, DISBAND):
            r.uint()
            clan = self.db.clan(cid)
            members = self.db.members(cid)
            if t == DISBAND:
                if rank != LEADER:
                    return self._fail(s, ONLY_LEADER_DISBANDS)
                online = self._online(cid)
                self.db.disband(cid)
                for ss in online:
                    w = BitWriter().uint(cid).uint(self.social.pid(ss)).bool(False).string(clan["name"]).string("")
                    self._send(ss, LEFT, w)
                log(f"clan {cid} disbanded by player {me}")
                return
            if rank == LEADER and len(members) > 1:
                return self._fail(s, LEADER_CANNOT_QUIT)
            w = BitWriter().uint(cid).uint(me).bool(False).string(clan["name"]).string("")
            self._broadcast(cid, LEFT, w)
            self.db.remove(key)
            if len(members) <= 1:
                self.db.disband(cid)
            log(f"player {me} left clan {cid}")
        elif t == NOTICE_SET:
            r.uint()
            text = r.string()[:MAX_TEXT]
            if rank > OFFICER:
                return
            self.db.set(cid, "notice", text)
            self._broadcast(cid, NOTICE, BitWriter().uint(cid).string(text))
        elif t == DESCRIPTION:
            text = r.string()[:MAX_TEXT]
            if rank <= OFFICER:
                self.db.set(cid, "description", text)
                self._broadcast(cid, DESCRIPTION_SET, BitWriter().string(text))
        elif t == TAGS:
            tags = ",".join(str(r.uint()) for _ in range(min(r.uint(), MAX_TAGS)))
            if rank <= OFFICER:
                self.db.set(cid, "tags", tags)
                self._broadcast(cid, TAGS_SET, BitWriter().string(tags))
        elif t == RECRUITING:
            on = r.bool()
            if rank <= OFFICER:
                self.db.set(cid, "recruiting", 1 if on else 0)
                self._broadcast(cid, RECRUITING_SET, BitWriter().byte(1 if on else 0))
        elif t == CHAT:
            r.uint()
            text, officers = r.string()[:MAX_TEXT], r.bool()
            if officers and rank > OFFICER:
                return
            if hasattr(self.social, "muted") and self.social.muted(key):
                return self.social._tell_muted(s)
            self._broadcast(cid, CLAN_CHAT, BitWriter().uint(me).string(text).bool(officers), officers_only=officers)
            if cid:
                self.db.add_chat(cid, me, self.social.display(key), text, officers)

    def _join(self, s, key: str, cid: int, via: str) -> None:
        if len(self.db.members(cid)) >= self.db.capacity(cid):
            if s is not None:
                self._fail(s, MAX_CAPACITY)
            return
        self.db.add(key, cid)
        _, rank, joined = next(m for m in self.db.members(cid) if m[0] == key)
        row = BitWriter()
        pid = self.social.stable_id(key) or 0
        row.uint(pid).string(self.social.display(key)).uint(rank).uint(int(joined)).uint(int(time.time()))
        row.uint(2).uint(0).string("").uint(0).uint(0).string(via).small(0).small(0).small(0).byte(0)
        for ss in self._online(cid):
            if self.social.key(ss) == key:
                self.send_state(ss)
            else:
                self._send(ss, JOINED, row)
        self.log(f"player {pid} joined clan {cid} through {via}")
