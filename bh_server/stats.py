"""server stats and leaderboards: match results reported by the players' own clients (11040), kept per account and
per legend, the rated ladders, the match history, and the web pages, JSON and game packets that show them.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
import html
import json
import threading
import time
import urllib.parse

from . import replays
from .bits import BitReader, BitWriter
from .frame import Packet
from .playlists import BY_ID
from .store import level_span

REPORT, RECORDED, ASK, ANSWER, PROFILE, LEGEND_NAMES, REGION = 11040, 11041, 11042, 11043, 11044, 11045, 11046
CAREER_ASK, LEGEND_CAREER, ACCOUNT_CAREER = 157, 2519, 2520
ARCADE_RESULT, ARCADE_ASK, ARCADE_BESTS = 49, 156, 2531
ARCADE_MAX_SCORE = 10_000_000
BIO, BIO_MAX = 11026, 140
HANDLED = {REPORT, ASK, PROFILE, LEGEND_NAMES, REGION, CAREER_ASK, ARCADE_RESULT, ARCADE_ASK, BIO}
RATINGS = 2483
RANK = 2479
TEAMS = 2478
ELO_1V1 = 1
LADDER = "1v1"
LADDERS = {"1v1": 1, "2v2": 2, "3v3": 3}
LADDER_OF_PLAYLIST = {"1v1Ranked": "1v1", "2v2Ranked": "2v2", "Ranked3v3": "3v3"}
TEAM_LADDER = "2v2team"
LEGEND_PREFIX = "L"
TIER_STEP = 100
TIER_WIDTH = 5 * TIER_STEP
VALHALLAN_AT = 6 * TIER_WIDTH
TIER_STARTS = tuple(zip(("Tin", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Valhallan"),
                        range(0, VALHALLAN_AT + 1, TIER_WIDTH)))
VALHALLAN_STARS = 50
STREAK_FROM, STREAK_POINTS, STREAK_CAP = 3, 1.2, 10
STREAK_WINDOW, STREAK_KEEP = 12, 0.40
TIERS = ("Unranked", "Tin", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Valhallan")
START_RATING = 180
SOFT_RESET_FROM = 2300
RATING_FLOOR = 0
PLACEMENT_GAMES = 10
K_BASE, K_PLACEMENT_MULT = 32, 3
DAILY_CAP = 200
REPORT_WINDOW = 120.0
PROGRESS, ACCOUNT_XP = 2700, 2527
LEGEND_RATINGS, LEGEND_LEVELS = 2702, 11019
XP_PER_MINUTE = 40
XP_FULL_ACTIVITY_DAMAGE = 166
XP_LOSS_FACTOR = 0.75
XP_BONUS_FROM, XP_BONUS_TO = 2000, 3000
VERSION = 1
DETAIL_VERSION = 2
DETAIL_FIELDS = ("damage_unarmed", "damage_thrown", "damage_weapon1", "damage_weapon2", "damage_gadgets",
                 "ko_unarmed", "ko_thrown", "ko_weapon1", "ko_weapon2", "ko_gadgets", "held_weapon1", "held_weapon2",
                 "team_kos",
                 "damage_bomb", "ko_bomb", "damage_mine", "ko_mine", "damage_spikeball", "ko_spikeball",
                 "damage_flyby", "ko_flyby", "hits_snowball", "ko_snowball")
MAX_PLAYERS = 8
MAX_TEXT = 40
MAX_COUNT = 9999
MAX_DAMAGE = 10_000_000
MAX_DURATION = 24 * 3600 * 1000
MAX_JSON = 32000

INDEXES = """
CREATE INDEX IF NOT EXISTS results_room_seed ON results (room, seed);
CREATE INDEX IF NOT EXISTS results_season ON results (season, verdict, format, rated);
CREATE INDEX IF NOT EXISTS results_time ON results (time);
CREATE INDEX IF NOT EXISTS ratings_ladder ON ratings (ladder, rating, games);
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS results (
  id INTEGER PRIMARY KEY, time REAL NOT NULL, room INTEGER NOT NULL, seed INTEGER NOT NULL, kind TEXT NOT NULL,
  mode TEXT, level INTEGER, map TEXT, duration INTEGER, verdict TEXT NOT NULL, rated INTEGER NOT NULL DEFAULT 0,
  note TEXT NOT NULL DEFAULT '', season INTEGER, format TEXT);
CREATE TABLE IF NOT EXISTS result_players (
  result_id INTEGER NOT NULL REFERENCES results(id), player_id INTEGER NOT NULL, account_id INTEGER REFERENCES accounts(id),
  name TEXT NOT NULL, legend INTEGER, legend_name TEXT, team INTEGER, place INTEGER, outcome TEXT, kos INTEGER,
  falls INTEGER, self_kos INTEGER, damage_dealt INTEGER, damage_taken INTEGER, rating_before INTEGER, rating_after INTEGER,
  PRIMARY KEY (result_id, player_id));
CREATE INDEX IF NOT EXISTS result_players_account ON result_players (account_id);
CREATE TABLE IF NOT EXISTS ratings (
  account_id INTEGER NOT NULL REFERENCES accounts(id), ladder TEXT NOT NULL, rating INTEGER NOT NULL,
  peak INTEGER NOT NULL, games INTEGER NOT NULL, wins INTEGER NOT NULL, shown INTEGER,
  protected INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0, recent TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (account_id, ladder));
CREATE TABLE IF NOT EXISTS seasons (id INTEGER PRIMARY KEY, name TEXT NOT NULL, started REAL NOT NULL, ended REAL);
CREATE TABLE IF NOT EXISTS season_ratings (
  season INTEGER NOT NULL, account_id INTEGER NOT NULL, ladder TEXT NOT NULL, rating INTEGER NOT NULL, peak INTEGER NOT NULL,
  games INTEGER NOT NULL, wins INTEGER NOT NULL, rank INTEGER NOT NULL, PRIMARY KEY (season, account_id, ladder));
CREATE TABLE IF NOT EXISTS legend_names (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS profiles (
  account_id INTEGER PRIMARY KEY, avatar INTEGER, title INTEGER, podium INTEGER, legend INTEGER, costume INTEGER,
  colour INTEGER, updated REAL NOT NULL, region INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS profile_bios (account_id INTEGER PRIMARY KEY, bio TEXT NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS team_ratings (
  a INTEGER NOT NULL, b INTEGER NOT NULL, ladder TEXT NOT NULL, rating INTEGER NOT NULL, peak INTEGER NOT NULL,
  games INTEGER NOT NULL, wins INTEGER NOT NULL, PRIMARY KEY (a, b, ladder));
CREATE TABLE IF NOT EXISTS season_team_ratings (
  season INTEGER NOT NULL, a INTEGER NOT NULL, b INTEGER NOT NULL, ladder TEXT NOT NULL, rating INTEGER NOT NULL,
  peak INTEGER NOT NULL, games INTEGER NOT NULL, wins INTEGER NOT NULL, rank INTEGER NOT NULL,
  PRIMARY KEY (season, a, b, ladder));
CREATE TABLE IF NOT EXISTS arcade_best (
  account_id INTEGER NOT NULL REFERENCES accounts(id), legend INTEGER NOT NULL, score INTEGER NOT NULL,
  placement INTEGER NOT NULL, mode INTEGER NOT NULL, updated REAL NOT NULL, PRIMARY KEY (account_id, legend));
CREATE TABLE IF NOT EXISTS legend_xp (
  account_id INTEGER NOT NULL REFERENCES accounts(id), legend INTEGER NOT NULL, xp INTEGER NOT NULL,
  PRIMARY KEY (account_id, legend));
CREATE TABLE IF NOT EXISTS result_details (
  result_id INTEGER NOT NULL REFERENCES results(id), player_id INTEGER NOT NULL, """ + ", ".join(
    f"{f} INTEGER NOT NULL DEFAULT 0" for f in DETAIL_FIELDS) + """,
  PRIMARY KEY (result_id, player_id));
CREATE TABLE IF NOT EXISTS elo_ledger (
  day TEXT NOT NULL, account_id INTEGER NOT NULL, opponent_id INTEGER NOT NULL, ladder TEXT NOT NULL,
  gained INTEGER NOT NULL, PRIMARY KEY (day, account_id, opponent_id, ladder));
CREATE TABLE IF NOT EXISTS result_teams (
  result_id INTEGER NOT NULL REFERENCES results(id), a INTEGER NOT NULL, b INTEGER NOT NULL, rating_before INTEGER NOT NULL,
  rating_after INTEGER NOT NULL, PRIMARY KEY (result_id, a, b));
"""
PROFILE_FIELDS = ("avatar", "title", "podium", "legend", "costume", "colour")

PLAYER_FIELDS = ("player", "legend", "legend_name", "team", "place", "kos", "falls", "self_kos", "damage_dealt", "damage_taken")
COUNTED = "confirmed"


def read_report(payload: bytes) -> dict:
    r = BitReader(payload)
    try:
        version = r.uint()
        if version not in (VERSION, DETAIL_VERSION):
            raise ValueError(f"report version {version}, expected {VERSION} or {DETAIL_VERSION}")
        rep = {"room": r.uint(), "seed": r.uint(), "level": r.uint(), "map": r.string()[:MAX_TEXT],
               "mode": r.string()[:MAX_TEXT], "duration": r.uint()}
        n = r.uint()
        if r.pos > len(payload) * 8:
            raise IndexError
        if not 1 <= n <= MAX_PLAYERS:
            raise ValueError(f"{n} players")
        players = []
        for _ in range(n):
            players.append({"player": r.uint(), "legend": r.uint(), "legend_name": r.string()[:MAX_TEXT], "team": r.uint(),
                            "place": r.uint(), "kos": r.uint(), "falls": r.uint(), "self_kos": r.uint(),
                            "damage_dealt": r.uint(), "damage_taken": r.uint()})
        detail = {}
        if version == DETAIL_VERSION:
            for p in players:
                if r.bool():
                    values = {f: r.uint() for f in DETAIL_FIELDS}
                    if p["player"]:
                        detail[p["player"]] = values
        if r.pos > len(payload) * 8:
            raise IndexError
    except IndexError:
        raise ValueError("report too short") from None
    if r.left() >= 8:
        raise ValueError("bytes left over")
    bots = sorted((p for p in players if p["player"] == 0), key=lambda p: [p[f] for f in PLAYER_FIELDS])
    players = [p for p in players if p["player"] != 0]
    if len({p["player"] for p in players}) != len(players):
        raise ValueError("a player twice")
    for p in players + bots:
        if not 1 <= p["place"] <= n:
            raise ValueError(f"place {p['place']}")
        if max(p["kos"], p["falls"], p["self_kos"], p["team"]) > MAX_COUNT or max(p["damage_dealt"], p["damage_taken"]) > MAX_DAMAGE:
            raise ValueError("a number out of range")
    if rep["duration"] > MAX_DURATION:
        raise ValueError("duration out of range")
    for d in detail.values():
        if any(v > (MAX_DURATION if f.startswith("held") else MAX_DAMAGE) for f, v in d.items()):
            raise ValueError("a breakdown number out of range")
    rep["players"] = sorted(players, key=lambda p: p["player"])
    if bots:
        rep["bots"] = bots
    if version == DETAIL_VERSION:
        rep["detail"] = detail
    return rep


def write_report(rep: dict) -> bytes:
    detail = rep.get("detail")
    w = BitWriter().uint(DETAIL_VERSION if detail is not None else VERSION).uint(rep["room"]).uint(rep["seed"]).uint(rep["level"]).string(rep.get("map", ""))
    rows = rep["players"] + rep.get("bots", [])
    w.string(rep.get("mode", "")).uint(rep.get("duration", 0)).uint(len(rows))
    for p in rows:
        w.uint(p["player"]).uint(p.get("legend", 0)).string(p.get("legend_name", "")).uint(p.get("team", 0)).uint(p["place"])
        w.uint(p.get("kos", 0)).uint(p.get("falls", 0)).uint(p.get("self_kos", 0))
        w.uint(p.get("damage_dealt", 0)).uint(p.get("damage_taken", 0))
    if detail is not None:
        for p in rows:
            d = detail.get(p["player"]) if p["player"] else None
            w.bool(d is not None)
            for f in DETAIL_FIELDS if d is not None else ():
                w.uint(d.get(f, 0))
    return w.bytes()


def outcomes(players: list[dict], bots: list[dict] = ()) -> dict[int, str]:
    best = min(p["place"] for p in list(players) + list(bots))
    if all(p["place"] == best for p in list(players) + list(bots)):
        return {p["player"]: "draw" for p in players}
    return {p["player"]: "win" if p["place"] == best else "loss" for p in players}


def elo(ra: int, rb: int, score_a: float, ka: int, kb: int) -> tuple[int, int]:
    ea = 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))
    na = ra + round(ka * (score_a - ea))
    nb = rb + round(kb * ((1.0 - score_a) - (1.0 - ea)))
    return max(RATING_FLOOR, min(0xFFFF, na)), max(RATING_FLOOR, min(0xFFFF, nb))


def k_factor(games: int, rating: int = 0) -> int:
    return K_BASE * K_PLACEMENT_MULT if games < PLACEMENT_GAMES else K_BASE


def match_format(players: list[dict]) -> str:
    sides: dict[int, int] = {}
    for i, p in enumerate(players):
        side = p.get("team") or -(i + 1)
        sides[side] = sides.get(side, 0) + 1
    sizes = sorted(sides.values())
    if len(sizes) == 2 and sizes[0] == sizes[1] and sizes[0] <= 4:
        return f"{sizes[0]}v{sizes[0]}"
    return "ffa" if all(n == 1 for n in sizes) else "teams"


def tier_of(rating: int, games: int, rank: int = 0, wins: int = 0, stars: int | None = None) -> str:
    if games < PLACEMENT_GAMES:
        return TIERS[0]
    stars = stars_for(rating) if stars is None else stars
    if stars == VALHALLAN_STARS:
        return TIERS[7]
    return TIERS[min(6, 1 + (stars - 1) // 6)] if stars else TIERS[0]


def tier_step(stars: int) -> int:
    if not stars or stars == VALHALLAN_STARS:
        return 0
    return 6 - (stars - 1) % 6


def recent_after(recent: str, outcome: str) -> str:
    return (recent + {"win": "W", "loss": "L"}.get(outcome, "D"))[-STREAK_WINDOW:]


def streak_after(streak: int, outcome: str, recent: str) -> int:
    if outcome not in ("win", "loss"):
        return streak
    if streak != 0:
        streak += 1 if outcome == "win" else -1
        ours, theirs = ("W", "L") if streak > 0 else ("L", "W")
        if streak == 0 or recent.endswith(theirs * STREAK_FROM) or recent.count(ours) / len(recent) < STREAK_KEEP:
            streak = 0
    if streak == 0:
        if recent.endswith("W" * STREAK_FROM):
            return STREAK_FROM
        if recent.endswith("L" * STREAK_FROM):
            return -STREAK_FROM
    return streak


def streak_points(streak: int) -> int:
    return round(STREAK_POINTS * min(abs(streak), STREAK_CAP)) if abs(streak) >= STREAK_FROM else 0


def streaked(old: int, new: int, streak: int) -> int:
    change = new - old
    if change != 0 and (change > 0) == (streak > 0):
        change += streak_points(streak) if change > 0 else -streak_points(streak)
    return max(RATING_FLOOR, min(0xFFFF, old + change))


def protected_stars(shown: int | None, protected: bool, rating: int, outcome: str) -> tuple[int, bool]:
    natural = stars_for(rating)
    if shown is None or natural >= shown or shown == 0:
        return natural, False
    if outcome != "loss":
        return shown, protected
    if not protected:
        return shown, True
    return natural, False


def is_legend_ladder(ladder: str) -> bool:
    return ladder.startswith(LEGEND_PREFIX) and ladder[1:].isdigit()


def placement_games(ladder: str | None) -> int:
    return 0 if ladder and is_legend_ladder(ladder) else PLACEMENT_GAMES


def tier_progress(rating: int) -> dict:
    tier, start, nxt = TIER_STARTS[0][0], 0, None
    for i, (name, at) in enumerate(TIER_STARTS):
        if rating >= at:
            tier, start = name, at
            nxt = TIER_STARTS[i + 1][1] if i + 1 < len(TIER_STARTS) else None
    return {"tier": tier, "start": start, "next": nxt}


def soft_reset(rating: int) -> int:
    return SOFT_RESET_FROM + (rating - SOFT_RESET_FROM) // 2 if rating > SOFT_RESET_FROM else rating


def _locked(method):
    def run(self, *args, **kwargs):
        with self.lock:
            return method(self, *args, **kwargs)
    run.__name__, run.__doc__ = method.__name__, method.__doc__
    return run


class Ledger:

    def __init__(self, store) -> None:
        self.store, self.db = store, store.db
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("BEGIN;" + SCHEMA + replays.SCHEMA + "COMMIT;")
            self._migrate()
            self.db.executescript("BEGIN;" + INDEXES + "COMMIT;")

    def _migrate(self) -> None:
        if "region" not in {r[1] for r in self.db.execute("PRAGMA table_info(profiles)")}:
            with self.db:
                self.db.execute("ALTER TABLE profiles ADD COLUMN region INTEGER NOT NULL DEFAULT 0")
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(ratings)")}
        if "streak" not in cols:
            with self.db:
                self.db.execute("ALTER TABLE ratings ADD COLUMN shown INTEGER")
                self.db.execute("ALTER TABLE ratings ADD COLUMN protected INTEGER NOT NULL DEFAULT 0")
                self.db.execute("ALTER TABLE ratings ADD COLUMN streak INTEGER NOT NULL DEFAULT 0")
        if "recent" not in {r[1] for r in self.db.execute("PRAGMA table_info(ratings)")}:
            with self.db:
                self.db.execute("ALTER TABLE ratings ADD COLUMN recent TEXT NOT NULL DEFAULT ''")
                self.db.execute("UPDATE ratings SET streak = 0 WHERE streak > -3 AND streak < 3")
        have = {r[1] for r in self.db.execute("PRAGMA table_info(results)")}
        if "season" in have and "format" in have:
            return
        with self.db:
            if "season" not in have:
                self.db.execute("ALTER TABLE results ADD COLUMN season INTEGER")
            if "format" not in have:
                self.db.execute("ALTER TABLE results ADD COLUMN format TEXT")
            if self.db.execute("SELECT 1 FROM seasons").fetchone() is None:
                first = self.db.execute("SELECT MIN(time) FROM results").fetchone()[0]
                self.db.execute("INSERT INTO seasons (id, name, started) VALUES (1, 'Season 1', ?)", (first or time.time(),))
            self.db.execute("UPDATE results SET season = ? WHERE season IS NULL", (self._season_id(),))
            todo = [r[0] for r in self.db.execute("SELECT id FROM results WHERE format IS NULL AND verdict = ?", (COUNTED,))]
            for rid in todo:
                ps = [{"team": r[0]} for r in self.db.execute("SELECT team FROM result_players WHERE result_id = ?", (rid,))]
                self.db.execute("UPDATE results SET format = ? WHERE id = ?", (match_format(ps) if ps else None, rid))

    def _season_id(self) -> int:
        row = self.db.execute("SELECT id FROM seasons WHERE ended IS NULL ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            nxt = self.db.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM seasons").fetchone()[0]
            with self.db:
                self.db.execute("INSERT INTO seasons (id, name, started) VALUES (?, ?, ?)", (nxt, f"Season {nxt}", time.time()))
            return nxt
        return row[0]

    @_locked
    def season_id(self) -> int:
        return self._season_id()

    @_locked
    def seasons(self) -> list[dict]:
        cur = self._season_id()
        return [{"id": r["id"], "name": r["name"], "started": r["started"], "ended": r["ended"], "current": r["id"] == cur}
                for r in self.db.execute("SELECT * FROM seasons ORDER BY id DESC")]

    def end_season(self, name: str | None = None) -> dict:
        with self.lock, self.db:
            cur = self._season_id()
            for (ladder,) in self.db.execute("SELECT DISTINCT ladder FROM ratings").fetchall():
                need = placement_games(ladder)
                rows = self.db.execute("SELECT * FROM ratings WHERE ladder = ? ORDER BY (games >= ?) DESC, rating DESC, "
                                       "games DESC, account_id", (ladder, need)).fetchall()
                for i, r in enumerate(rows, 1):
                    self.db.execute("INSERT OR REPLACE INTO season_ratings VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (cur, r["account_id"], ladder, r["rating"], r["peak"], r["games"], r["wins"],
                                     i if r["games"] >= need else 0))
            rows = self.db.execute("SELECT * FROM team_ratings ORDER BY (games >= ?) DESC, rating DESC, games DESC, a, b",
                                   (PLACEMENT_GAMES,)).fetchall()
            for i, r in enumerate(rows, 1):
                self.db.execute("INSERT OR REPLACE INTO season_team_ratings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                (cur, r["a"], r["b"], r["ladder"], r["rating"], r["peak"], r["games"], r["wins"],
                                 i if r["games"] >= PLACEMENT_GAMES else 0))
            for r in self.db.execute("SELECT account_id, ladder, rating FROM ratings").fetchall():
                start = soft_reset(r["rating"])
                self.db.execute("UPDATE ratings SET rating = ?, peak = ?, games = 0, wins = 0 WHERE account_id = ? AND ladder = ?",
                                (start, start, r["account_id"], r["ladder"]))
            for r in self.db.execute("SELECT a, b, ladder, rating FROM team_ratings").fetchall():
                start = soft_reset(r["rating"])
                self.db.execute("UPDATE team_ratings SET rating = ?, peak = ?, games = 0, wins = 0 WHERE a = ? AND b = ? AND "
                                "ladder = ?", (start, start, r["a"], r["b"], r["ladder"]))
            now = time.time()
            self.db.execute("UPDATE seasons SET ended = ? WHERE id = ?", (now, cur))
            self.db.execute("INSERT INTO seasons (id, name, started) VALUES (?, ?, ?)", (cur + 1, name or f"Season {cur + 1}", now))
        return self.seasons()[0]

    @_locked
    def _ratings_of(self, ladder: str, season: int) -> dict[int, dict]:
        if season == self._season_id():
            rows = self.db.execute("SELECT account_id, rating, peak, games, wins FROM ratings WHERE ladder = ?", (ladder,))
        else:
            rows = self.db.execute("SELECT account_id, rating, peak, games, wins, rank FROM season_ratings "
                                   "WHERE ladder = ? AND season = ?", (ladder, season))
        return {r["account_id"]: {k: r[k] for k in r.keys() if k != "account_id"} for r in rows}

    def set_profile(self, account_id: int, values: dict) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO profiles (account_id, avatar, title, podium, legend, costume, colour, updated) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET avatar = excluded.avatar, "
                            "title = excluded.title, podium = excluded.podium, "
                            "legend = COALESCE(NULLIF(excluded.legend, 0), profiles.legend), "
                            "costume = CASE WHEN excluded.legend = 0 THEN profiles.costume ELSE excluded.costume END, "
                            "colour = CASE WHEN excluded.legend = 0 THEN profiles.colour ELSE excluded.colour END, "
                            "updated = excluded.updated",
                            (account_id, *(int(values.get(k) or 0) for k in PROFILE_FIELDS), time.time()))

    @_locked
    def profile(self, account_id: int) -> dict:
        row = self.db.execute("SELECT * FROM profiles WHERE account_id = ?", (account_id,)).fetchone()
        out = {k: (row[k] or 0) for k in PROFILE_FIELDS} if row else {k: 0 for k in PROFILE_FIELDS}
        out["region"] = row["region"] if row else 0
        last = self.db.execute("SELECT rp.legend FROM result_players rp JOIN results r ON r.id = rp.result_id "
                               "WHERE rp.account_id = ? AND rp.legend IS NOT NULL ORDER BY r.time DESC, r.id DESC LIMIT 1",
                               (account_id,)).fetchone()
        if last and last[0] != out["legend"] and (not row or row["updated"] < self._last_match_time(account_id)):
            out.update(legend=last[0], costume=0, colour=0)
        return out

    def _last_match_time(self, account_id: int) -> float:
        row = self.db.execute("SELECT MAX(r.time) FROM result_players rp JOIN results r ON r.id = rp.result_id "
                              "WHERE rp.account_id = ?", (account_id,)).fetchone()
        return row[0] or 0.0

    def arcade_record(self, account_id: int, mode: int, legend: int, score: int, placement: int) -> bool:
        with self.lock, self.db:
            row = self.db.execute("SELECT score FROM arcade_best WHERE account_id = ? AND legend = ?",
                                  (account_id, legend)).fetchone()
            if row is not None and row[0] >= score:
                return False
            self.db.execute("INSERT OR REPLACE INTO arcade_best VALUES (?, ?, ?, ?, ?, ?)",
                            (account_id, legend, score, placement, mode, time.time()))
            return True

    def arcade_bests(self, account_id: int) -> list[tuple[int, int, int]]:
        with self.lock:
            return [tuple(r) for r in self.db.execute(
                "SELECT legend, score, placement FROM arcade_best WHERE account_id = ? ORDER BY legend", (account_id,))]

    def set_region(self, account_id: int, region: int) -> None:
        with self.lock, self.db:
            self.db.execute("INSERT INTO profiles (account_id, updated, region) VALUES (?, 0, ?) "
                            "ON CONFLICT(account_id) DO UPDATE SET region = excluded.region", (account_id, region))

    def set_bio(self, account_id: int, bio: str) -> str:
        bio = " ".join(bio.split())[:BIO_MAX]
        with self.lock, self.db:
            if bio:
                self.db.execute("INSERT OR REPLACE INTO profile_bios VALUES (?, ?, ?)", (account_id, bio, time.time()))
            else:
                self.db.execute("DELETE FROM profile_bios WHERE account_id = ?", (account_id,))
        return bio

    @_locked
    def bio(self, account_id: int) -> str:
        row = self.db.execute("SELECT bio FROM profile_bios WHERE account_id = ?", (account_id,)).fetchone()
        return row[0] if row else ""

    def _has_table(self, name: str) -> bool:
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None

    @_locked
    def look(self, account_id: int) -> dict:
        out = {"theme": 0, "frame": 0}
        if not self._has_table("profile_picks"):
            return out
        row = self.db.execute("SELECT data FROM profile_picks WHERE owner = ?", (f"a:{account_id}",)).fetchone()
        try:
            sets = json.loads(row[0]).get("sets", {}) if row else {}
        except ValueError:
            sets = {}
        for name in out:
            words = sets.get(name) or []
            out[name] = next((i * 32 + b for i, w in enumerate(words) if w for b in range(32) if (w >> b) & 1), 0)
        return out

    @_locked
    def badges(self, account_id: int) -> dict:
        out: dict = {"achievements": [], "tournaments": []}
        if self._has_table("achievements"):
            names = getattr(self, "_achievement_names", None)
            if names is None:
                from . import achievements
                try:
                    names = self._achievement_names = {a.id: a.name for a in achievements.load().values()}
                except OSError:
                    names = {}
            out["achievements"] = [{"id": r[0], "name": names.get(r[0], f"Achievement {r[0]}"), "when": int(r[1])} for r in self.db.execute(
                "SELECT achievement, MAX(done) FROM achievements WHERE done IS NOT NULL AND player_id IN "
                "(SELECT id FROM players WHERE account_id = ?) GROUP BY achievement ORDER BY MAX(done) DESC",
                (account_id,))]
        if self._has_table("tournaments"):
            out["tournaments"] = [{"id": r[0], "name": r[1], "mode": r[2], "when": int(r[3] or 0)} for r in self.db.execute(
                "SELECT t.id, t.name, t.mode, t.finished FROM tournaments t WHERE t.state = 'done' AND (t.winner = ? OR "
                "EXISTS (SELECT 1 FROM tournament_entrants e WHERE e.tournament = t.id AND e.account_id = t.winner "
                "AND e.partner = ?)) ORDER BY t.finished DESC LIMIT 20", (account_id, account_id))]
        return out

    def set_legend_names(self, names: dict[int, str]) -> None:
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO legend_names VALUES (?, ?)", list(names.items()))

    @_locked
    def legend_name(self, legend: int | None, fallback: str | None = None) -> str:
        row = self.db.execute("SELECT name FROM legend_names WHERE id = ?", (legend,)).fetchone() if legend else None
        if row:
            return row[0]
        if not fallback and legend:
            row = self.db.execute("SELECT legend_name FROM result_players WHERE legend = ? AND legend_name IS NOT NULL "
                                  "LIMIT 1", (legend,)).fetchone()
            fallback = row[0] if row else None
        return fallback or (f"Legend {legend}" if legend else "-")

    @_locked
    def top_legend(self, account_id: int, season: int | None = None) -> int | None:
        row = self.db.execute(
            "SELECT rp.legend FROM result_players rp JOIN results r ON r.id = rp.result_id WHERE rp.account_id = ? "
            "AND r.verdict = ? AND rp.legend IS NOT NULL" + (" AND r.season = ?" if season else "") +
            " GROUP BY rp.legend ORDER BY COUNT(*) DESC, SUM(rp.outcome = 'win') DESC, rp.legend LIMIT 1",
            (account_id, COUNTED, season) if season else (account_id, COUNTED)).fetchone()
        return row[0] if row else None

    @_locked
    def account_of_player(self, player_id: int) -> int | None:
        row = self.db.execute("SELECT account_id FROM players WHERE id = ?", (player_id,)).fetchone()
        return row["account_id"] if row else None

    def _standing(self, account_id: int, ladder: str) -> dict:
        row = self.rating(account_id, ladder) or {"peak": START_RATING, "wins": 0, "games": 0}
        return {"peak": row["peak"], "wins": row["wins"], "rank": self.rank(account_id, ladder)}

    @_locked
    def ranked_summary(self, account_id: int, ladder: str = LADDER) -> dict:
        row = self.rating(account_id, ladder) or {"rating": START_RATING, "peak": START_RATING, "games": 0, "wins": 0,
                                                  "streak": 0, "protected": 0}
        last = [r["outcome"] for r in self.db.execute(
            "SELECT rp.outcome AS outcome FROM result_players rp JOIN results r ON r.id = rp.result_id "
            "WHERE rp.account_id = ? AND r.verdict = ? AND r.rated = 1 AND r.format = ? AND r.season = ? "
            "ORDER BY r.time DESC, r.id DESC LIMIT 12", (account_id, COUNTED, ladder, self._season_id()))]
        rating = row["rating"]
        nxt = None if rating >= VALHALLAN_AT else (rating // TIER_STEP + 1) * TIER_STEP
        return {"ladder": ladder, "rating": rating, "peak": row["peak"], "games": row["games"], "wins": row["wins"],
                "streak": row.get("streak") or 0, "protected": bool(row.get("protected")), "placement": PLACEMENT_GAMES,
                "step": TIER_STEP, "next_step": nxt, **tier_progress(rating), "last": last,
                "rank": self.rank(account_id, ladder), "season": self._season_id(),
                **({"teammate": self._best_teammate(account_id)} if ladder == "2v2" else {})}

    @_locked
    def team_summary(self, account_id: int, partner: int) -> dict:
        a, b = sorted((account_id, partner))
        row = self.team_rating(a, b) or {"rating": START_RATING, "peak": START_RATING, "games": 0, "wins": 0}
        last = [r["outcome"] for r in self.db.execute(
            "SELECT x.outcome AS outcome FROM result_players x JOIN result_players y ON x.result_id = y.result_id AND "
            "x.team = y.team JOIN results r ON r.id = x.result_id WHERE x.account_id = ? AND y.account_id = ? AND "
            "r.verdict = ? AND r.rated = 1 AND r.format = '2v2' AND r.season = ? ORDER BY r.time DESC, r.id DESC LIMIT 12",
            (a, b, COUNTED, self._season_id()))]
        ladder_row = next(iter(self.team_leaderboard(1, which=lambda pair: pair == [a, b])), None)
        rank = ((ladder_row or {}).get("rating") or {}).get("rank", 0)
        rating = row["rating"]
        nxt = None if rating >= VALHALLAN_AT else (rating // TIER_STEP + 1) * TIER_STEP
        members = []
        for acc in (account_id, partner):
            own = self.rating(acc, "2v2") or {"rating": START_RATING, "games": 0}
            members.append({"name": self.name_of(acc), "rating": own["rating"], "games": own["games"]})
        return {"ladder": "2v2", "team": True, "rating": rating, "peak": row["peak"], "games": row["games"],
                "wins": row["wins"], "streak": 0, "protected": False, "placement": PLACEMENT_GAMES, "step": TIER_STEP,
                "next_step": nxt, **tier_progress(rating), "last": last, "rank": rank, "season": self._season_id(),
                "members": members}

    def _best_teammate(self, account_id: int) -> dict | None:
        teams = self.teams_of(account_id)
        return teams[0] if teams else None

    @_locked
    def rating(self, account_id: int, ladder: str = LADDER) -> dict | None:
        row = self.db.execute("SELECT rating, peak, games, wins, shown, protected, streak, recent FROM ratings WHERE "
                              "account_id = ? AND ladder = ?", (account_id, ladder)).fetchone()
        return dict(row) if row else None

    def record(self, match: "Pending", verdict: str, rep: dict | None, note: str = "", rate_custom: bool = True,
               xp_rules: dict | None = None) -> dict:
        players = {p["player"]: p for p in rep["players"]} if rep and verdict == COUNTED else {}
        accounts = {pid: self.account_of_player(pid) for pid in match.ids}
        result = outcomes(rep["players"], rep.get("bots", ())) if players else {}
        playlist = BY_ID.get(match.playlist)
        kind = "custom" if match.playlist == 0 else (playlist.name if playlist else f"playlist {match.playlist}")
        fmt = match_format(list(players.values())) if players else None
        signed = [accounts[pid] for pid in match.ids]
        allowed = ((match.playlist == 0 and rate_custom) or
                   bool(playlist and playlist.ranked and LADDER_OF_PLAYLIST.get(playlist.name) == fmt))
        rated = (fmt in LADDERS and not rep.get("bots") and len(players) == len(match.ids) and all(signed) and len(set(signed)) == len(signed)
                 and allowed)
        changes: dict[int, tuple[int, int]] = {}
        team_changes: dict[tuple, tuple[int, int]] = {}
        legend_changes: dict[int, tuple[int, int]] = {}
        stars_shown: dict[int, tuple[int, int]] = {}
        legend_stars: dict[int, tuple[int, int]] = {}
        details: dict[int, dict] = {}
        progress: dict[int, dict] = {}
        with self.lock, self.db:
            season = self._season_id()
            if rated:
                fresh = {"rating": START_RATING, "peak": START_RATING, "games": 0, "wins": 0}
                old = {pid: self.rating(accounts[pid], fmt) or dict(fresh) for pid in players}
                scores = {"win": 1.0, "draw": 0.5, "loss": 0.0}
                if fmt == "1v1":
                    pa, pb = sorted(players)
                    new = dict(zip((pa, pb), elo(old[pa]["rating"], old[pb]["rating"], scores[result[pa]],
                                                 k_factor(old[pa]["games"], old[pa]["rating"]),
                                                 k_factor(old[pb]["games"], old[pb]["rating"]))))
                else:
                    side_of = {pid: players[pid]["team"] for pid in players}
                    avg = {}
                    for side in set(side_of.values()):
                        members = [old[pid]["rating"] for pid in players if side_of[pid] == side]
                        avg[side] = sum(members) / len(members)
                    new = {}
                    for pid in players:
                        mine = old[pid]["rating"]
                        theirs = next(v for k, v in avg.items() if k != side_of[pid])
                        expected = 1.0 / (1.0 + 10 ** ((theirs - mine) / 400.0))
                        change = round(k_factor(old[pid]["games"], mine) * (scores[result[pid]] - expected))
                        new[pid] = max(RATING_FLOOR, min(0xFFFF, old[pid]["rating"] + change))
                capped = self._daily_cap(players, accounts, fmt, old, new,
                                         int((xp_rules or {}).get("daily_elo_cap", DAILY_CAP)))
                for pid in sorted(players):
                    acc, o, n = accounts[pid], old[pid], new[pid]
                    n, stars_shown[pid], details[pid] = self._put_rating(acc, fmt, o, n, result[pid])
                    if capped.get(pid):
                        details[pid]["daily_cap"] = capped[pid]
                    changes[pid] = (o["rating"], n)
                if fmt == "1v1":
                    pa, pb = sorted(players)
                    la, lb = (f"{LEGEND_PREFIX}{players[x]['legend']}" for x in (pa, pb))
                    oa, ob = (self.rating(accounts[pa], la) or dict(fresh), self.rating(accounts[pb], lb) or dict(fresh))
                    na, nb = elo(oa["rating"], ob["rating"], scores[result[pa]],
                                 k_factor(max(oa["games"], PLACEMENT_GAMES), oa["rating"]),
                                 k_factor(max(ob["games"], PLACEMENT_GAMES), ob["rating"]))
                    na, legend_stars[pa], _ = self._put_rating(accounts[pa], la, oa, na, result[pa])
                    nb, legend_stars[pb], _ = self._put_rating(accounts[pb], lb, ob, nb, result[pb])
                    legend_changes[pa], legend_changes[pb] = (oa["rating"], na), (ob["rating"], nb)
                if fmt == "2v2":
                    pairs = {}
                    for pid in players:
                        pairs.setdefault(players[pid]["team"], []).append(accounts[pid])
                    (sa, ta), (sb, tb) = sorted((side, tuple(sorted(accs))) for side, accs in pairs.items())
                    oa, ob = (self.team_rating(*ta) or dict(fresh), self.team_rating(*tb) or dict(fresh))
                    first = next(pid for pid in players if players[pid]["team"] == sa)
                    na, nb = elo(oa["rating"], ob["rating"], scores[result[first]], k_factor(oa["games"], oa["rating"]),
                                 k_factor(ob["games"], ob["rating"]))
                    won_a = result[first] == "win"
                    won_b = next(result[pid] for pid in players if players[pid]["team"] == sb) == "win"
                    for (a, b), o, n, won in ((ta, oa, na, won_a), (tb, ob, nb, won_b)):
                        self.db.execute(
                            "INSERT INTO team_ratings (a, b, ladder, rating, peak, games, wins) VALUES (?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(a, b, ladder) DO UPDATE SET rating = excluded.rating, peak = excluded.peak, "
                            "games = excluded.games, wins = excluded.wins",
                            (a, b, TEAM_LADDER, n, max(o["peak"], n), o["games"] + 1, o["wins"] + won))
                        team_changes[(a, b)] = (o["rating"], n)
            cur = self.db.execute(
                "INSERT INTO results (time, room, seed, kind, mode, level, map, duration, verdict, rated, note, season, format) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), match.key[0], match.key[1], kind, rep["mode"] if players else None,
                 rep["level"] if players else match.level, rep["map"] if players else None,
                 rep["duration"] if players else None, verdict, int(rated), note, season, fmt))
            rid = cur.lastrowid
            for pid in match.ids:
                p = players.get(pid, {})
                before, after = changes.get(pid, (None, None))
                self.db.execute(
                    "INSERT INTO result_players VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (rid, pid, accounts[pid], match.names[pid], p.get("legend"), p.get("legend_name"), p.get("team"),
                     p.get("place"), result.get(pid), p.get("kos"), p.get("falls"), p.get("self_kos"),
                     p.get("damage_dealt"), p.get("damage_taken"), before, after))
            for pid, d in (rep.get("detail") or {}).items() if players else ():
                if pid in players:
                    self.db.execute(f"INSERT INTO result_details (result_id, player_id, {', '.join(DETAIL_FIELDS)}) "
                                    f"VALUES ({', '.join('?' * (len(DETAIL_FIELDS) + 2))})",
                                    (rid, pid, *(int(d.get(f, 0)) for f in DETAIL_FIELDS)))
            for (a, b), (before, after) in team_changes.items():
                self.db.execute("INSERT INTO result_teams VALUES (?, ?, ?, ?, ?)", (rid, a, b, before, after))
            ranked_ladder = LADDER_OF_PLAYLIST.get(playlist.name) if playlist and playlist.ranked else None
            for pid in sorted(players):
                if accounts[pid]:
                    bonus = self._ranked_bonus(pid, players, accounts, fmt, changes) if ranked_ladder and rated else 0.0
                    gain, mult = match_xp(players[pid], rep["duration"], result.get(pid), bonus, xp_rules)
                    progress[pid] = self._progress(accounts[pid], players[pid], gain, rated, fmt, ranked_ladder,
                                                   changes.get(pid), legend_changes.get(pid), team_changes)
                    progress[pid]["mult"] = mult
                    if pid in stars_shown:
                        progress[pid]["stars"] = stars_shown[pid]
                    if pid in legend_stars:
                        progress[pid]["team_stars"] = legend_stars[pid]
        return {"id": rid, "rated": rated, "ladder": fmt if rated else None, "ratings": changes, "accounts": accounts,
                "teams": team_changes, "progress": progress, "details": details,
                "standing": {pid: self._standing(accounts[pid], fmt) for pid in changes}}

    def _daily_cap(self, players: dict, accounts: dict, fmt: str, old: dict, new: dict, cap: int = DAILY_CAP) -> dict[int, int]:
        if cap <= 0:
            return {}
        day = time.strftime("%Y-%m-%d", time.gmtime())
        clipped = {}
        for pid in sorted(players):
            gain = new[pid] - old[pid]["rating"]
            if gain <= 0:
                continue
            mult = K_PLACEMENT_MULT if old[pid]["games"] < PLACEMENT_GAMES else 1
            base = gain / mult
            side = players[pid].get("team")
            opps = [accounts[o] for o in players if o != pid and (fmt == "1v1" or players[o].get("team") != side)]
            used = {opp: (self.db.execute("SELECT gained FROM elo_ledger WHERE day = ? AND account_id = ? AND "
                                          "opponent_id = ? AND ladder = ?", (day, accounts[pid], opp, fmt)).fetchone()
                          or (0,))[0] for opp in opps}
            left = max(0, min(cap - u for u in used.values())) if used else cap
            allowed = min(base, left)
            if allowed < base:
                new[pid] = old[pid]["rating"] + round(allowed * mult)
                clipped[pid] = gain - (new[pid] - old[pid]["rating"])
            booked = round(allowed)
            if booked:
                for opp in opps:
                    self.db.execute("INSERT INTO elo_ledger (day, account_id, opponent_id, ladder, gained) VALUES "
                                    "(?, ?, ?, ?, ?) ON CONFLICT(day, account_id, opponent_id, ladder) DO UPDATE SET "
                                    "gained = gained + excluded.gained", (day, accounts[pid], opp, fmt, booked))
        return clipped

    def _ranked_bonus(self, pid: int, players: dict, accounts: dict, fmt: str, changes: dict) -> float:
        side = players[pid].get("team")
        others = [o for o in players if o != pid and (fmt == "1v1" or players[o].get("team") != side)]
        if not others:
            return 0.0
        ratings = [changes[o][0] if o in changes else START_RATING for o in others]
        rating = sum(ratings) / len(ratings)
        if any(self._valhallan(accounts[o], fmt) for o in others if accounts.get(o)):
            return 1.0
        if rating < XP_BONUS_FROM:
            return 0.0
        return 0.05 + 0.45 * min(1.0, (rating - XP_BONUS_FROM) / (XP_BONUS_TO - XP_BONUS_FROM))

    def _valhallan(self, acc: int, ladder: str) -> bool:
        row = self.rating(acc, ladder)
        return bool(row) and tier_of(row["rating"], row["games"], self.rank(acc, ladder), row["wins"]) == "Valhallan"

    def _progress(self, acc: int, player: dict, gain: int, rated: bool, fmt: str | None, ranked_ladder: str | None,
                  change, legend_change, team_changes: dict) -> dict:
        legend = int(player.get("legend") or 0)
        before = self.db.execute("SELECT xp FROM accounts WHERE id = ?", (acc,)).fetchone()
        before = before[0] if before else 0
        self.db.execute("UPDATE accounts SET xp = xp + ? WHERE id = ?", (gain, acc))
        out = {"legend": legend, "xp": (before, before + gain), "legend_xp": (0, 0),
               "rating": (0, 0), "games": 0, "team": (0, 0), "team_games": 0}
        if legend:
            row = self.db.execute("SELECT xp FROM legend_xp WHERE account_id = ? AND legend = ?", (acc, legend)).fetchone()
            lb = row[0] if row else 0
            self.db.execute("INSERT INTO legend_xp (account_id, legend, xp) VALUES (?, ?, ?) ON CONFLICT(account_id, legend) "
                            "DO UPDATE SET xp = excluded.xp", (acc, legend, lb + gain))
            out["legend_xp"] = (lb, lb + gain)
        ladder = fmt if rated else ranked_ladder
        if ladder:
            row = self.rating(acc, ladder) or {"rating": START_RATING, "games": 0}
            out["rating"] = change or (row["rating"], row["rating"])
            out["games"] = row["games"]
            if ladder == "1v1" and legend:
                lrow = self.rating(acc, f"{LEGEND_PREFIX}{legend}") or {"rating": START_RATING}
                out["team"] = legend_change or (lrow["rating"], lrow["rating"])
            for (a, b), pair in team_changes.items():
                if acc in (a, b):
                    out["team"] = pair
                    trow = self.team_rating(a, b)
                    out["team_games"] = trow["games"] if trow else 0
        return out

    def _put_rating(self, acc: int, ladder: str, old: dict, new: int, outcome: str) -> tuple[int, tuple[int, int], dict]:
        recent = recent_after(old.get("recent") or "", outcome)
        streak = streak_after(old.get("streak") or 0, outcome, recent)
        base = new
        new = streaked(old["rating"], new, streak)
        before = old.get("shown")
        if before is None:
            before = stars_for(old["rating"]) if old["games"] else 0
        shown, protected = protected_stars(before if old["games"] else None, bool(old.get("protected")), new, outcome)
        self.db.execute(
            "INSERT INTO ratings (account_id, ladder, rating, peak, games, wins, shown, protected, streak, recent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(account_id, ladder) DO UPDATE SET rating = excluded.rating, "
            "peak = excluded.peak, games = excluded.games, wins = excluded.wins, shown = excluded.shown, "
            "protected = excluded.protected, streak = excluded.streak, recent = excluded.recent",
            (acc, ladder, new, max(old["peak"], new), old["games"] + 1, old["wins"] + (outcome == "win"), shown,
             int(protected), streak, recent))
        detail = {"base": base - old["rating"], "streak_bonus": new - base, "streak": streak, "protected": bool(protected),
                  "games": old["games"] + 1}
        return new, (before, shown), detail

    @_locked
    def career(self, account_id: int) -> dict:
        sums = ", ".join(f"SUM(COALESCE(d.{f}, 0)) AS {f}" for f in DETAIL_FIELDS)
        rows = self.db.execute(
            "SELECT p.legend AS legend, COUNT(*) AS games, SUM(p.outcome = 'win') AS wins, "
            "SUM(COALESCE(p.kos, 0)) AS kos, SUM(COALESCE(p.falls, 0)) AS falls, "
            "SUM(COALESCE(p.self_kos, 0)) AS self_kos, SUM(COALESCE(p.damage_dealt, 0)) AS dealt, "
            "SUM(COALESCE(p.damage_taken, 0)) AS taken, SUM(COALESCE(r.duration, 0)) AS duration, " + sums + " "
            "FROM result_players p JOIN results r ON r.id = p.result_id "
            "LEFT JOIN result_details d ON d.result_id = p.result_id AND d.player_id = p.player_id "
            "WHERE p.account_id = ? AND r.verdict = ? AND p.legend IS NOT NULL AND p.legend > 0 GROUP BY p.legend",
            (account_id, COUNTED)).fetchall()
        legends = {int(r["legend"]): {k: int(r[k] or 0) for k in r.keys() if k != "legend"} for r in rows}
        total = {f: sum(row[f] for row in legends.values()) for f in ("games", "wins", *DETAIL_FIELDS)}
        return {"legends": legends, "account": total}

    def team_rating(self, a: int, b: int) -> dict | None:
        a, b = sorted((a, b))
        row = self.db.execute("SELECT rating, peak, games, wins FROM team_ratings WHERE a = ? AND b = ? AND ladder = ?",
                              (a, b, TEAM_LADDER)).fetchone()
        return dict(row) if row else None

    @_locked
    def name_of(self, account_id: int) -> str:
        row = self.db.execute(
            "SELECT COALESCE(pl.name, rp.name) AS name FROM result_players rp JOIN results r ON r.id = rp.result_id "
            "LEFT JOIN players pl ON pl.id = rp.player_id WHERE rp.account_id = ? ORDER BY r.time DESC, r.id DESC LIMIT 1",
            (account_id,)).fetchone()
        if row:
            return row["name"]
        row = self.db.execute("SELECT name FROM players WHERE account_id = ? ORDER BY id LIMIT 1", (account_id,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row["name"] if row and row["name"] else f"Player {account_id}"

    _TOTALS = ("COUNT(*) AS matches, SUM(rp.outcome = 'win') AS wins, SUM(rp.outcome = 'loss') AS losses, "
               "SUM(rp.outcome = 'draw') AS draws, SUM(rp.kos) AS kos, SUM(rp.falls) AS falls, SUM(rp.self_kos) AS self_kos, "
               "SUM(rp.damage_dealt) AS damage_dealt, SUM(rp.damage_taken) AS damage_taken")

    @staticmethod
    def _totals(row) -> dict:
        d = {k: (row[k] or 0) for k in ("matches", "wins", "losses", "draws", "kos", "falls", "self_kos", "damage_dealt",
                                        "damage_taken")}
        d["win_rate"] = round(d["wins"] / d["matches"], 3) if d["matches"] else 0.0
        return d

    @staticmethod
    def _ladder_where(ladder: str | None) -> tuple[str, tuple]:
        if not ladder:
            return "", ()
        if is_legend_ladder(ladder):
            return " AND r.rated = 1 AND r.format = '1v1' AND rp.legend = ?", (int(ladder[1:]),)
        return " AND r.rated = 1 AND r.format = ?", (ladder,)

    @_locked
    def player(self, account_id: int, recent: int = 20, season: int | None = None, ladder: str | None = None) -> dict | None:
        known = self.db.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not known:
            return None
        season = self._season_id() if season is None else season
        lw, la = self._ladder_where(ladder)
        where, args = ("rp.account_id = ? AND r.verdict = ?" + (" AND r.season = ?" if season else "") + lw,
                       ((account_id, COUNTED, season) if season else (account_id, COUNTED)) + la)
        totals = self.db.execute(f"SELECT {self._TOTALS} FROM result_players rp JOIN results r ON r.id = rp.result_id "
                                 f"WHERE {where}", args).fetchone()
        legends = []
        for row in self.db.execute(
                f"SELECT rp.legend AS legend, MAX(rp.legend_name) AS legend_name, {self._TOTALS} FROM result_players rp "
                f"JOIN results r ON r.id = rp.result_id WHERE {where} "
                "GROUP BY rp.legend ORDER BY matches DESC, wins DESC, rp.legend", args):
            legends.append({"legend": row["legend"], "legend_name": row["legend_name"] or f"Legend {row['legend']}",
                            **self._totals(row)})
        ladders = {}
        for name in LADDERS:
            r = self._ratings_of(name, season or self._season_id()).get(account_id)
            if r:
                rank = r.get("rank", None)
                if rank is None:
                    rank = self.rank(account_id, name)
                r.update(rank=rank, tier=tier_of(r["rating"], r["games"], rank, r["wins"]), losses_or_draws=r["games"] - r["wins"])
                ladders[name] = r
        history, outcomes = self._history(account_id, season or self._season_id())
        if ladder:
            history = {ladder: history[ladder]} if ladder in history else {}
            outcomes = {ladder: outcomes[ladder]} if ladder in outcomes else {}
            heads = self.db.execute(
                f"SELECT r.* FROM results r JOIN result_players rp ON rp.result_id = r.id WHERE {where} "
                "ORDER BY r.time DESC, r.id DESC LIMIT ?", (*args, recent)).fetchall()
            latest = [self._match(h) for h in heads]
        else:
            latest = self.recent(recent, account_id)
        return {"id": account_id, "name": self.name_of(account_id), "rating": ladders.get(LADDER), "ratings": ladders,
                "season": season, "ladder": ladder, "totals": self._totals(totals), "legends": legends,
                "top_legend": legends[0]["legend"] if legends else self.top_legend(account_id),
                "profile": self.profile(account_id), "recent": latest,
                "streak": self._streak(account_id, ladder), "history": history, "results": outcomes}

    @_locked
    def past_seasons(self, account_id: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT sr.season, s.name, sr.ladder, sr.rating, sr.peak, sr.games, sr.wins, sr.rank FROM season_ratings sr "
            "LEFT JOIN seasons s ON s.id = sr.season WHERE sr.account_id = ? ORDER BY sr.season DESC, sr.ladder",
            (account_id,)).fetchall()
        return [{"season": r["season"], "name": r["name"] or f"Season {r['season']}", "ladder": r["ladder"],
                 "rating": r["rating"], "peak": r["peak"], "games": r["games"], "wins": r["wins"], "rank": r["rank"],
                 "tier": tier_of(r["rating"], r["games"], r["rank"], r["wins"])} for r in rows]

    @_locked
    def weapons(self, account_id: int, heroes: dict | None = None) -> list[dict]:
        if heroes is None:
            from .missions import heroes as load
            heroes = load()
        rows = self.db.execute(
            "SELECT rp.legend AS legend, SUM(d.ko_weapon1) AS k1, SUM(d.ko_weapon2) AS k2, SUM(d.damage_weapon1) AS d1, "
            "SUM(d.damage_weapon2) AS d2, SUM(d.held_weapon1) AS h1, SUM(d.held_weapon2) AS h2 FROM result_details d "
            "JOIN result_players rp ON rp.result_id = d.result_id AND rp.player_id = d.player_id "
            "JOIN results r ON r.id = d.result_id WHERE rp.account_id = ? AND r.verdict = ? GROUP BY rp.legend",
            (account_id, COUNTED)).fetchall()
        out: dict[str, dict] = {}
        for r in rows:
            names = heroes.get(r["legend"], (("", ""), None))[0]
            for slot, name in enumerate(names[:2], 1):
                if not name:
                    continue
                w = out.setdefault(name, {"weapon": name, "kos": 0, "damage": 0, "held": 0})
                w["kos"] += r[f"k{slot}"] or 0
                w["damage"] += r[f"d{slot}"] or 0
                w["held"] += r[f"h{slot}"] or 0
        return sorted(out.values(), key=lambda w: (-w["kos"], -w["damage"], w["weapon"]))

    @_locked
    def rank(self, account_id: int, ladder: str = LADDER) -> int:
        me = self.rating(account_id, ladder)
        if not me or me["games"] < PLACEMENT_GAMES:
            return 0
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM ratings WHERE ladder = ? AND games >= ? AND account_id != ? AND "
            "(rating > ? OR (rating = ? AND (games > ? OR (games = ? AND account_id < ?))))",
            (ladder, PLACEMENT_GAMES, account_id, me["rating"], me["rating"], me["games"], me["games"], account_id)).fetchone()
        return row["n"] + 1

    @_locked
    def leaderboard(self, limit: int = 50, ladder: str = "all", season: int | None = None,
                    only: set | None = None, region: int | None = None, search: str = "") -> list[dict]:
        season = season or self._season_id()
        if is_legend_ladder(ladder):
            fmt, args = " AND r.format = '1v1' AND r.rated = 1 AND rp.legend = ?", (COUNTED, season, int(ladder[1:]))
        else:
            fmt = "" if ladder == "all" else " AND r.format = ? AND r.rated = 1"
            args = (COUNTED, season) + (() if ladder == "all" else (ladder,))
        known = ladder in LADDERS or ladder == "all" or is_legend_ladder(ladder)
        ratings = self._ratings_of(LADDER if ladder == "all" else ladder, season) if known else {}
        keep = self._filter(only, region)
        rows = []
        for row in self.db.execute(
                f"SELECT rp.account_id AS account_id, {self._TOTALS} FROM result_players rp JOIN results r ON r.id = rp.result_id "
                f"WHERE rp.account_id IS NOT NULL AND r.verdict = ? AND r.season = ?{fmt} GROUP BY rp.account_id", args):
            acc = row["account_id"]
            if keep is not None and acc not in keep:
                continue
            r = ratings.get(acc)
            rows.append({"id": acc, "name": self.name_of(acc), "rating": dict(r) if r else None, **self._totals(row)})
        need = placement_games(ladder)
        placed = lambda d: d["rating"] is not None and d["rating"]["games"] >= need
        rows.sort(key=lambda d: (not placed(d), d["rating"] is None, -(d["rating"] or {}).get("rating", 0), -d["wins"],
                                 -d["matches"], d["id"]))
        for i, d in enumerate(rows, 1):
            d["rank"] = i
        rows = _searched(rows, search)[:limit]
        for d in rows:
            i = d["rank"]
            if d["rating"] is not None:
                place = i if placed(d) else 0
                d["rating"].update(rank=place, tier=tier_of(d["rating"]["rating"],
                                                        max(d["rating"]["games"], PLACEMENT_GAMES - need), place,
                                                        d["rating"]["wins"]), placement=need)
            d["top_legend"] = self.top_legend(d["id"], season)
            d["profile"] = self.profile(d["id"])
        return rows

    def _filter(self, only: set | None, region: int | None) -> set | None:
        keep = set(only) if only is not None else None
        if region is not None:
            here = {r[0] for r in self.db.execute("SELECT account_id FROM profiles WHERE region = ?", (region,))}
            if region == 0:
                here |= {r[0] for r in self.db.execute(
                    "SELECT id FROM accounts WHERE id NOT IN (SELECT account_id FROM profiles)")}
            keep = here if keep is None else keep & here
        return keep

    @_locked
    def legends_rated(self, season: int | None = None) -> list[dict]:
        season = season or self._season_id()
        rows = self.db.execute(
            "SELECT rp.legend AS legend, COUNT(DISTINCT rp.account_id) AS n, COUNT(*) AS matches, "
            "SUM(rp.outcome = 'win') AS wins, SUM(rp.outcome = 'loss') AS losses, SUM(rp.outcome = 'draw') AS draws "
            "FROM result_players rp JOIN results r ON "
            "r.id = rp.result_id WHERE r.verdict = ? AND r.season = ? AND r.format = '1v1' AND r.rated = 1 "
            "AND rp.account_id IS NOT NULL "
            "AND rp.legend IS NOT NULL GROUP BY rp.legend ORDER BY n DESC, rp.legend", (COUNTED, season)).fetchall()
        return [{"legend": r["legend"], "name": self.legend_name(r["legend"]), "players": r["n"],
                 "matches": r["matches"], "wins": r["wins"] or 0, "losses": r["losses"] or 0, "draws": r["draws"] or 0}
                for r in rows]

    @_locked
    def team_leaderboard(self, limit: int = 50, season: int | None = None, only: set | None = None,
                         region: int | None = None, search: str = "", which=None) -> list[dict]:
        season = season or self._season_id()
        current = season == self._season_id()
        table = ("SELECT a, b, rating, peak, games, wins FROM team_ratings WHERE ladder = ?" if current else
                 "SELECT a, b, rating, peak, games, wins, rank FROM season_team_ratings WHERE ladder = ? AND season = ?")
        ratings = {(r["a"], r["b"]): {k: r[k] for k in r.keys() if k not in ("a", "b")}
                   for r in self.db.execute(table, (TEAM_LADDER,) if current else (TEAM_LADDER, season))}
        keep = self._filter(only, region)
        rows = []
        for r in self.db.execute(
                "SELECT MIN(x.account_id, y.account_id) AS a, MAX(x.account_id, y.account_id) AS b, COUNT(*) AS matches, "
                "SUM(x.outcome = 'win') AS wins, SUM(x.outcome = 'loss') AS losses, SUM(x.outcome = 'draw') AS draws, "
                "SUM(x.falls + y.falls) AS falls FROM result_players x JOIN result_players y "
                "ON x.result_id = y.result_id AND x.team = y.team AND x.account_id < y.account_id JOIN results r ON "
                "r.id = x.result_id WHERE r.verdict = ? AND r.season = ? AND r.format = '2v2' AND r.rated = 1 GROUP BY a, b",
                (COUNTED, season)):
            a, b = r["a"], r["b"]
            if keep is not None and not (a in keep or b in keep):
                continue
            rt = ratings.get((a, b))
            d = {k: (r[k] or 0) for k in ("matches", "wins", "losses", "draws", "falls")}
            d["win_rate"] = round(d["wins"] / d["matches"], 3) if d["matches"] else 0.0
            rows.append({"id": f"{a}-{b}", "accounts": [a, b], "rating": dict(rt) if rt else None, **d})
        placed = lambda d: d["rating"] is not None and d["rating"]["games"] >= PLACEMENT_GAMES
        rows.sort(key=lambda d: (not placed(d), d["rating"] is None, -(d["rating"] or {}).get("rating", 0), -d["wins"],
                                 -d["matches"], d["id"]))
        names: dict[int, str] = {}
        name = lambda x: names[x] if x in names else names.setdefault(x, self.name_of(x))
        for i, d in enumerate(rows, 1):
            d["rank"] = i
        if (search or "").strip():
            for d in rows:
                d["name"] = f"{name(d['accounts'][0])} & {name(d['accounts'][1])}"
            rows = _searched(rows, search)
        if which is not None:
            rows = [d for d in rows if which(d["accounts"])]
        rows = rows[:limit]
        for d in rows:
            d["name"] = f"{name(d['accounts'][0])} & {name(d['accounts'][1])}"
            i = d["rank"]
            if d["rating"] is not None:
                place = i if placed(d) else 0
                d["rating"].update(rank=place, tier=tier_of(d["rating"]["rating"], d["rating"]["games"], place,
                                                        d["rating"]["wins"]))
            d["members"] = [{"id": x, "name": name(x), "top_legend": self.top_legend(x, season),
                             "profile": self.profile(x)} for x in d["accounts"]]
        return rows

    @_locked
    def player_of_account(self, account_id: int) -> int | None:
        row = self.db.execute("SELECT id FROM players WHERE account_id = ? ORDER BY id LIMIT 1", (account_id,)).fetchone()
        return row["id"] if row else None

    @_locked
    def teams_of(self, account_id: int) -> list[dict]:
        out = []
        for d in self.team_leaderboard(100000, which=lambda pair: account_id in pair):
            if d["rating"] is None:
                continue
            partner = d["accounts"][1] if d["accounts"][0] == account_id else d["accounts"][0]
            r = d["rating"]
            out.append({"partner": partner, "name": self.name_of(partner), "rating": r["rating"], "games": r["games"],
                        "wins": r["wins"], "rank": r.get("rank", 0)})
        out.sort(key=lambda t: (-t["rating"], -t["games"], t["partner"]))
        return out

    @_locked
    def team(self, a: int, b: int, season: int | None = None) -> dict | None:
        a, b = sorted((a, b))
        season = season or self._season_id()
        row = next(iter(self.team_leaderboard(100000, season, which=lambda pair: pair == [a, b])), None)
        if row is None:
            return None
        heads = self.db.execute(
            "SELECT r.* FROM results r JOIN result_players x ON x.result_id = r.id AND x.account_id = ? JOIN result_players y "
            "ON y.result_id = r.id AND y.account_id = ? AND y.team = x.team WHERE r.format = '2v2' AND r.rated = 1 "
            "AND r.season = ? ORDER BY r.time DESC, r.id DESC LIMIT 10", (a, b, season)).fetchall()
        row["recent"] = [self._match(h) for h in heads]
        for m in row["recent"]:
            t = self.db.execute("SELECT rating_before, rating_after FROM result_teams WHERE result_id = ? AND a = ? AND b = ?",
                                (m["id"], a, b)).fetchone()
            m["team_rating"] = dict(t) if t else None
        row["season"] = season
        return row

    @_locked
    def partners(self, account_id: int, limit: int = 8) -> list[dict]:
        rows = self.db.execute(
            "SELECT y.account_id AS partner, COUNT(*) AS games, SUM(x.outcome = 'win') AS wins, MAX(r.time) AS last "
            "FROM result_players x JOIN result_players y ON y.result_id = x.result_id AND y.team = x.team AND "
            "y.account_id IS NOT NULL AND y.account_id != x.account_id JOIN results r ON r.id = x.result_id "
            "WHERE x.account_id = ? AND x.team IS NOT NULL AND x.team > 0 AND r.verdict = ? AND r.format = '2v2' "
            "GROUP BY y.account_id ORDER BY games DESC, wins DESC, last DESC LIMIT ?",
            (account_id, COUNTED, limit)).fetchall()
        mine = self.profile(account_id).get("avatar", 0)
        return [{"account": r["partner"], "name": self.name_of(r["partner"]),
                 "legend": self._partner_legend(account_id, r["partner"]), "games": r["games"],
                 "wins": r["wins"] or 0, "last": r["last"], "team": self.team_rating(account_id, r["partner"]),
                 "avatar": self.profile(r["partner"]).get("avatar", 0), "my_avatar": mine,
                 "my_legend": self._partner_legend(r["partner"], account_id)}
                for r in rows]

    def _partner_legend(self, account_id: int, partner: int) -> int | None:
        row = self.db.execute(
            "SELECT y.legend AS legend FROM result_players x JOIN result_players y ON y.result_id = x.result_id AND "
            "y.team = x.team AND y.account_id = ? JOIN results r ON r.id = x.result_id WHERE x.account_id = ? AND "
            "x.team > 0 AND r.verdict = ? AND r.format = '2v2' AND y.legend IS NOT NULL GROUP BY y.legend "
            "ORDER BY COUNT(*) DESC, MAX(r.time) DESC LIMIT 1", (partner, account_id, COUNTED)).fetchone()
        return row["legend"] if row else None

    @_locked
    def friend_accounts(self, account_id: int) -> set:
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'friends'").fetchone() is None:
            return {account_id}
        me = self.db.execute("SELECT discord_id FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if me is None:
            return {account_id}
        ids = {me[0], "d:" + me[0]}
        found = set()
        for a, b in self.db.execute("SELECT a, b FROM friends WHERE a IN (?, ?) OR b IN (?, ?)", (*ids, *ids)):
            other = b if a in ids else a
            found.add(other[2:] if other.startswith("d:") else other)
        q = ",".join("?" * len(found)) or "NULL"
        return {account_id} | {r[0] for r in self.db.execute(f"SELECT id FROM accounts WHERE discord_id IN ({q})", list(found))}

    @_locked
    def head_to_head(self, me: int, them: int, ladder: str | None = None) -> dict:
        lw, la = self._ladder_where(ladder)
        lw = lw.replace("rp.legend", "x.legend")
        row = self.db.execute(
            "SELECT COUNT(*) AS n, SUM(x.outcome = 'win') AS w, SUM(x.outcome = 'loss') AS l FROM result_players x JOIN "
            "result_players y ON y.result_id = x.result_id AND y.account_id = ? AND (x.team IS NULL OR x.team = 0 OR "
            f"x.team != y.team) JOIN results r ON r.id = x.result_id WHERE x.account_id = ? AND r.verdict = ?{lw}",
            (them, me, COUNTED, *la)).fetchone()
        return {"matches": row["n"] or 0, "wins": row["w"] or 0, "losses": row["l"] or 0}

    def _streak(self, account_id: int, ladder: str | None = None) -> dict:
        kind, n = None, 0
        lw, la = self._ladder_where(ladder)
        for (o,) in self.db.execute("SELECT rp.outcome FROM result_players rp JOIN results r ON r.id = rp.result_id "
                                    f"WHERE rp.account_id = ? AND r.verdict = ?{lw} ORDER BY r.time DESC, r.id DESC LIMIT 50",
                                    (account_id, COUNTED, *la)):
            if o not in ("win", "loss"):
                break
            if kind is None:
                kind = o
            if o != kind:
                break
            n += 1
        return {"kind": kind, "count": n}

    def _history(self, account_id: int, season: int) -> tuple[dict, dict]:
        out, outcomes = {}, {}
        for ladder in LADDERS:
            rows = self.db.execute(
                "SELECT rp.rating_before, rp.rating_after, rp.outcome FROM result_players rp JOIN results r ON "
                "r.id = rp.result_id WHERE rp.account_id = ? AND r.season = ? AND r.format = ? AND r.rated = 1 AND "
                "rp.rating_after IS NOT NULL ORDER BY r.time, r.id", (account_id, season, ladder)).fetchall()
            if rows:
                points = [rows[0][0]] + [r[1] for r in rows]
                out[ladder] = points[-60:]
                outcomes[ladder] = [r[2] for r in reversed(rows[-60:])]
        return out, outcomes

    @_locked
    def clan_leaderboard(self, season: int | None = None, limit: int = 50) -> list[dict]:
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'clans'").fetchone() is None:
            return []
        season = season or self._season_id()
        ratings = self._ratings_of(LADDER, season)
        rows = []
        for c in self.db.execute("SELECT id, name, emblem FROM clans").fetchall():
            members = [r[0] for r in self.db.execute(
                "SELECT a.id FROM clan_members m JOIN accounts a ON 'd:' || a.discord_id = m.key OR a.discord_id = m.key "
                "WHERE m.clan_id = ?", (c["id"],))]
            if not members:
                continue
            q = ",".join("?" * len(members))
            t = self.db.execute(
                f"SELECT COUNT(*) AS n, SUM(rp.outcome = 'win') AS w FROM result_players rp JOIN results r ON "
                f"r.id = rp.result_id WHERE rp.account_id IN ({q}) AND r.verdict = ? AND r.rated = 1 AND r.season = ?",
                (*members, COUNTED, season)).fetchone()
            placed = [ratings[m]["rating"] for m in members if m in ratings and ratings[m]["games"] >= PLACEMENT_GAMES]
            rows.append({"id": c["id"], "name": c["name"], "emblem": c["emblem"], "members": len(members),
                         "rated_matches": t["n"] or 0, "rated_wins": t["w"] or 0,
                         "average": round(sum(placed) / len(placed)) if placed else None,
                         "best": sorted(({"id": m, "name": self.name_of(m), "rating": ratings[m]["rating"]} for m in members
                                         if m in ratings), key=lambda x: -x["rating"])[:5]})
        rows.sort(key=lambda d: (-d["rated_wins"], -(d["average"] or 0), d["name"].lower()))
        for i, d in enumerate(rows[:limit], 1):
            d["rank"] = i
        return rows[:limit]

    @_locked
    def recent(self, limit: int = 20, account_id: int | None = None, rated: bool = False, search: str = "") -> list[dict]:
        if account_id is None and search:
            like = "%" + search.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"
            heads = self.db.execute(
                "SELECT * FROM results r WHERE " + ("r.rated = 1 AND " if rated else "") +
                "EXISTS (SELECT 1 FROM result_players rp LEFT JOIN players pl ON pl.id = rp.player_id "
                "WHERE rp.result_id = r.id AND COALESCE(pl.name, rp.name) LIKE ? ESCAPE '!') "
                "ORDER BY r.time DESC, r.id DESC LIMIT ?", (like, limit)).fetchall()
        elif account_id is None:
            heads = self.db.execute("SELECT * FROM results" + (" WHERE rated = 1" if rated else "") +
                                    " ORDER BY time DESC, id DESC LIMIT ?", (limit,)).fetchall()
        else:
            heads = self.db.execute("SELECT r.* FROM results r JOIN result_players rp ON rp.result_id = r.id "
                                    "WHERE rp.account_id = ? ORDER BY r.time DESC, r.id DESC LIMIT ?", (account_id, limit)).fetchall()
        return [self._match(h) for h in heads]

    @_locked
    def match(self, result_id: int) -> dict | None:
        head = self.db.execute("SELECT * FROM results WHERE id = ?", (result_id,)).fetchone()
        return self._match(head) if head else None

    def _tiers_then(self, head, account_id: int, before: int | None, after: int) -> dict:
        games = self.db.execute(
            "SELECT COUNT(*) AS n FROM results r JOIN result_players rp ON rp.result_id = r.id WHERE rp.account_id = ? "
            "AND r.rated = 1 AND r.format = ? AND r.season = ? AND (r.time < ? OR (r.time = ? AND r.id <= ?))",
            (account_id, head["format"], head["season"], head["time"], head["time"], head["id"])).fetchone()["n"]
        tier_after = tier_progress(after)["tier"] if games >= PLACEMENT_GAMES else TIERS[0]
        tier_before = tier_progress(before)["tier"] if before is not None and games - 1 >= PLACEMENT_GAMES else TIERS[0]
        return {"tier_before": tier_before, "tier_after": tier_after, "games_then": games}

    def _match(self, head) -> dict:
        players = []
        for p in self.db.execute("SELECT rp.*, COALESCE(pl.name, rp.name) AS shown FROM result_players rp "
                                 "LEFT JOIN players pl ON pl.id = rp.player_id WHERE rp.result_id = ? "
                                 "ORDER BY COALESCE(rp.place, 99), rp.player_id", (head["id"],)):
            d = {"player": p["player_id"], "account": p["account_id"], "name": p["shown"]}
            for k in ("legend", "legend_name", "team", "place", "outcome", "kos", "falls", "self_kos", "damage_dealt",
                      "damage_taken", "rating_before", "rating_after"):
                d[k] = p[k]
            if head["rated"] and p["account_id"] is not None and p["rating_after"] is not None:
                d.update(self._tiers_then(head, p["account_id"], p["rating_before"], p["rating_after"]))
            if p["account_id"] is not None:
                av = self.db.execute("SELECT avatar FROM profiles WHERE account_id = ?", (p["account_id"],)).fetchone()
                d["avatar"] = (av[0] or 0) if av else 0
            players.append(d)
        kept = self.db.execute("SELECT 1 FROM replays WHERE room = ? AND seed = ?", (head["room"], head["seed"])).fetchone()
        return {"id": head["id"], "time": head["time"], "kind": head["kind"], "mode": head["mode"], "level": head["level"],
                "map": head["map"], "duration": head["duration"], "verdict": head["verdict"], "counted": head["verdict"] == COUNTED,
                "rated": bool(head["rated"]), "note": head["note"], "players": players,
                "room": head["room"], "seed": head["seed"], "replay": kept is not None}


_ledgers_lock = threading.Lock()


def ledger_for(store) -> Ledger:
    with _ledgers_lock:
        led = getattr(store, "_stats_ledger", None)
        if led is None:
            led = Ledger(store)
            store._stats_ledger = led
        return led


@dataclass
class Pending:
    key: tuple[int, int]
    ids: list[int]
    names: dict[int, str]
    sends: dict[int, object]
    playlist: int = 0
    level: int = 0
    first: float = 0.0
    reports: dict[int, dict] = field(default_factory=dict)


def merged_detail(reports: dict[int, dict], agreeing: list[int]) -> dict[int, dict]:
    out: dict[int, dict] = {}
    order = list(agreeing) + [pid for pid in reports if pid not in agreeing]
    for pid in {p["player"] for rep in reports.values() for p in rep["players"]}:
        own = reports.get(pid, {}).get("detail", {}).get(pid)
        if own is None:
            own = next((reports[o]["detail"][pid] for o in order if pid in reports[o].get("detail", {})), None)
        if own is not None:
            out[pid] = own
    return out


def _canon(rep: dict) -> str:
    return json.dumps({k: rep.get(k, []) for k in ("room", "seed", "level", "map", "mode", "duration", "players", "bots")},
                      sort_keys=True)


def _differences(a: dict, b: dict) -> str:
    out = [k for k in ("level", "map", "mode", "duration") if a[k] != b[k]]
    pa, pb = {p["player"]: p for p in a["players"]}, {p["player"]: p for p in b["players"]}
    for pid in sorted(pa):
        diff = [k for k in PLAYER_FIELDS if pa[pid].get(k) != pb.get(pid, {}).get(k)]
        if diff:
            out.append(f"player {pid} " + "/".join(diff))
    return ", ".join(out) or "the same"


class Results:

    def __init__(self, ledger: Ledger, log, cfg: dict | None = None, clock=time.monotonic, window: float = REPORT_WINDOW,
                 timers: bool = True) -> None:
        self.ledger, self.log, self.cfg = ledger, log, cfg or {}
        self.clock, self.window, self.timers = clock, window, timers
        self.pending: dict[tuple[int, int], Pending] = {}
        self.done: OrderedDict = OrderedDict()
        self.lock = threading.Lock()
        self.listeners: list = []

    def report(self, player_id: int, rep: dict, room) -> tuple[str, str | None]:
        settled = []
        with self.lock:
            settled += self._due()
            key = (rep["room"], rep["seed"])
            if key in self.done:
                return "already recorded", None
            entry = self.pending.get(key)
            if entry is None:
                if room is None or not room.started or room.number != rep["room"] or getattr(room, "seed", None) != rep["seed"]:
                    return "refused: no such started match", "The server doesn't know that match, so it wasn't recorded."
                lineup = list(getattr(room, "lineup", None) or room.members)
                entry = Pending(key, [m.player_id for m in lineup], {m.player_id: m.name for m in lineup},
                                {m.player_id: m.send for m in lineup}, room.settings.get("playlist", 0), room.start_level,
                                self.clock())
            if player_id not in entry.ids:
                return "refused: not a player of that match", "You weren't a player in that match."
            if sorted(p["player"] for p in rep["players"]) != sorted(entry.ids):
                return "refused: other players than the match's", "Those aren't the players the match started with."
            if player_id in entry.reports:
                return "duplicate", None
            if key not in self.pending:
                self.pending[key] = entry
                if self.timers:
                    t = threading.Timer(self.window + 1.0, self.sweep)
                    t.daemon = True
                    t.start()
            entry.reports[player_id] = rep
            what = f"report {len(entry.reports)} of {len(entry.ids)}"
            if len(entry.reports) == len(entry.ids):
                settled.append(self._settle(entry))
        self._notify(settled)
        return what, None

    def sweep(self) -> int:
        with self.lock:
            settled = self._due()
        self._notify(settled)
        return len(settled)

    def _due(self) -> list:
        now = self.clock()
        return [self._settle(e) for e in list(self.pending.values()) if now - e.first >= self.window]

    def _settle(self, entry: Pending) -> tuple:
        self.pending.pop(entry.key, None)
        groups: dict[str, list[int]] = {}
        for pid, rep in entry.reports.items():
            groups.setdefault(_canon(rep), []).append(pid)
        best = max(groups.values(), key=len)
        n, agreeing = len(entry.ids), len(best)
        rep = entry.reports[best[0]]
        if verdict_detail := merged_detail(entry.reports, best):
            rep = dict(rep, detail=verdict_detail)
        others = [pid for pid in entry.reports if pid not in best]
        if agreeing >= 2 and (agreeing == n or agreeing * 2 > n):
            verdict = COUNTED
            missing = [pid for pid in entry.ids if pid not in entry.reports]
            note = "; ".join(x for x in (
                ("differed: " + ", ".join(f"player {pid} ({_differences(rep, entry.reports[pid])})" for pid in others)) if others else "",
                ("no report from player " + ", ".join(map(str, missing))) if missing else "") if x)
        elif len(groups) > 1:
            verdict = "disputed"
            first = entry.reports[entry.ids[0]] if entry.ids[0] in entry.reports else rep
            note = "reports differ: " + "; ".join(f"player {pid}: {_differences(first, r)}" for pid, r in entry.reports.items()
                                                    if r is not first)
        else:
            verdict = "unconfirmed"
            note = f"{len(entry.reports)} of {n} players reported"
        out = self.ledger.record(entry, verdict, rep, note, rate_custom=self.cfg.get("rate_custom_rooms", True),
                                 xp_rules=self.cfg)
        out["playlist"] = entry.playlist
        self.done[entry.key] = out["id"]
        while len(self.done) > 1000:
            self.done.popitem(last=False)
        self.log(f"match {entry.key[0]}/{entry.key[1]}: {verdict}" + (f" ({note})" if note else "") +
                 f", result {out['id']}" + (", rated" if out["rated"] else ""))
        return entry, verdict, out, (rep if verdict == COUNTED else None)

    def _notify(self, settled: list) -> None:
        for entry, verdict, out, rep in settled:
            for listener in list(self.listeners):
                try:
                    listener(entry, verdict, out, rep)
                except Exception as e:
                    self.log(f"result listener failed: {type(e).__name__}: {e}")
            for pid in entry.ids:
                send = entry.sends.get(pid)
                if send is None:
                    continue
                w = BitWriter().bool(verdict == COUNTED).string(message(verdict, out, pid))
                w.string(json.dumps(rating_change(out, pid), separators=(",", ":")))
                packets = [Packet(RECORDED, w.bytes())]
                if pid in out["ratings"] and out.get("ladder", LADDER) in ("1v1", "2v2"):
                    packets += rating_packets(self.ledger, pid, out["accounts"][pid], self.cfg)
                    if out.get("ladder") == "2v2":
                        pk = teams_packet(self.ledger, pid, out["accounts"][pid])
                        if pk is not None:
                            packets.append(pk)
                packets += progress_packets(out, pid)
                if out["accounts"].get(pid):
                    pk = legend_ratings_packet(self.ledger, out["accounts"][pid])
                    if pk is not None:
                        packets.append(pk)
                    if verdict == COUNTED:
                        packets += career_packets(self.ledger, out["accounts"][pid])
                for pk in packets:
                    try:
                        send(pk)
                    except (OSError, TypeError):
                        break


def ranked_match(out: dict) -> bool:
    playlist = BY_ID.get(out.get("playlist", 0))
    return playlist is not None and playlist.name in LADDER_OF_PLAYLIST


def rating_change(out: dict, player_id: int) -> dict:
    if player_id not in out.get("ratings", {}) or not ranked_match(out):
        return {}
    before, after = out["ratings"][player_id]
    d = {"ladder": out.get("ladder") or LADDER, "before": before, "after": after, **tier_progress(after),
         "tier_before": tier_progress(before)["tier"], **out.get("details", {}).get(player_id, {}),
         "placement": PLACEMENT_GAMES, "step": TIER_STEP}
    acc = out.get("accounts", {}).get(player_id)
    for (a, b), (tb, ta) in out.get("teams", {}).items():
        if acc in (a, b):
            d["team"] = {"before": tb, "after": ta}
    pr = out.get("progress", {}).get(player_id) or {}
    if d["ladder"] == "1v1" and pr.get("legend"):
        d["legend"] = {"id": pr["legend"], "before": pr["team"][0], "after": pr["team"][1]}
    d.update(out.get("standing", {}).get(player_id, {}))
    d["players"] = {str(pid): [b4, af, (out.get("progress", {}).get(pid, {}).get("stars") or (0, stars_for(af)))[1]]
                    for pid, (b4, af) in out["ratings"].items()}
    return d


def match_xp(player: dict, duration_ms: int, outcome: str | None, bonus: float = 0.0,
             rules: dict | None = None) -> tuple[int, float]:
    rules = rules or {}
    minutes = max(0, duration_ms or 0) / 60000.0
    full = float(rules.get("xp_full_activity_damage", XP_FULL_ACTIVITY_DAMAGE))
    activity = min(1.0, (player.get("damage_dealt") or 0) / (minutes * full)) if minutes > 0 and full > 0 else 0.0
    loss = float(rules.get("xp_loss_factor", XP_LOSS_FACTOR))
    factor = {"win": 1.0, "draw": (1.0 + loss) / 2, "loss": loss}.get(outcome or "", loss)
    mult = 1.0 + max(0.0, bonus)
    xp = int(float(rules.get("xp_per_minute", XP_PER_MINUTE)) * minutes * activity * factor * mult)
    return max(1, xp), mult


def progress_packets(out: dict, player_id: int) -> list[Packet]:
    pr = out.get("progress", {}).get(player_id)
    if not pr:
        return []
    legend = pr["legend"]
    w = BitWriter().uint(player_id)
    w.bool(True).uint(0).uint(legend).uint(0).small(min(out.get("playlist", 0), 0xFFFF))
    if legend:
        lb, la = pr["legend_xp"]
        lv0, s0, _ = level_span(lb)
        lv1, s1, n1 = level_span(la)
        w.small(0).uint(la - lb).uint(la).uint(lv0).uint(lv1).uint(s0).uint(s1).uint(n1)
        w.bool(False)
        w.uint(0).uint(0).uint(0).uint(0).uint(0)
        if la == lb:
            w.string("")
        (rb, ra), (tb, ta) = pr["rating"], pr["team"]
        w.uint(rb).uint(ra).uint(tb).uint(ta)
        sb, sa = pr.get("stars") or (stars_for(rb), stars_for(ra))
        tsb, tsa = pr.get("team_stars") or (stars_for(tb), stars_for(ta))
        w.uint(sb).uint(sa).uint(tsb).uint(tsa)
        w.uint(pr["games"]).uint(pr["team_games"])
    xb, xa = pr["xp"]
    lv0, s0, _ = level_span(xb)
    lv1, s1, n1 = level_span(xa)
    w.uint(0).uint(xa - xb).f32(pr.get("mult", 1.0)).f32(1.0)
    w.uint(xa).uint(lv0).uint(lv1).uint(s0).uint(s1).uint(n1).uint(0).uint(0)
    for mission in out.get("missions", {}).get(player_id, ()):
        w.bool(True).uint(mission).uint(0).uint(0).small(min(out.get("playlist", 0), 0xFFFF))
        w.uint(0).uint(0).f32(1.0).f32(1.0).uint(xa).uint(lv1).uint(lv1).uint(s1).uint(s1).uint(n1).uint(0).uint(0)
    w.bool(False)
    return [Packet(PROGRESS, w.bytes()),
            Packet(ACCOUNT_XP, BitWriter().uint(xa).small(min(lv1, 0xFFFF)).uint(s1).uint(n1).bytes())]


def message(verdict: str, out: dict, player_id: int) -> str:
    if verdict == COUNTED:
        if player_id in out["ratings"] and ranked_match(out):
            before, after = out["ratings"][player_id]
            return f"Match recorded. {out.get('ladder') or LADDER} rating {after} ({after - before:+d})."
        if not out["accounts"].get(player_id):
            return "Match recorded. Sign in with Discord to keep your stats."
        return "Match recorded."
    if verdict == "disputed":
        return "The players' results differ, so this match wasn't counted."
    return "Not every player reported this match, so it wasn't counted."


STAR_STEPS = (0, 1) + tuple(TIER_STEP * k for k in range(5)) + tuple(
    v for t in range(1, 6) for v in ((TIER_WIDTH * t - TIER_STEP,) + tuple(TIER_WIDTH * t + TIER_STEP * k for k in range(5))))


def stars_for(rating: int) -> int:
    if rating >= VALHALLAN_AT:
        return VALHALLAN_STARS
    for i in range(2, len(STAR_STEPS)):
        if rating < STAR_STEPS[i]:
            return i - 2 if STAR_STEPS[i - 2] == STAR_STEPS[i - 1] else i - 1
    return len(STAR_STEPS) - 1


def ratings_packet(player_id: int, row: dict | None, elo_id: int = ELO_1V1) -> Packet:
    row = row or {"rating": START_RATING, "peak": START_RATING, "games": 0, "wins": 0}
    w = BitWriter().bool(True).uint(player_id).small(elo_id).uint(row["games"]).uint(row["wins"])
    stars = row.get("shown") if row.get("shown") is not None else stars_for(row["rating"])
    w.small(row["rating"]).small(stars).small(row["peak"])
    return Packet(RATINGS, w.bool(False).bytes())


def legend_ratings_packet(ledger: "Ledger", account_id: int) -> Packet | None:
    with ledger.lock:
        rows = ledger.db.execute("SELECT ladder, rating, peak, games, wins, shown FROM ratings WHERE account_id = ? AND "
                                 "ladder LIKE ?", (account_id, LEGEND_PREFIX + "%")).fetchall()
    rows = [r for r in rows if is_legend_ladder(r["ladder"])]
    if not rows:
        return None
    w = BitWriter().uint(0)
    for r in rows:
        stars = r["shown"] if r["shown"] is not None else stars_for(r["rating"])
        w.bool(True).uint(int(r["ladder"][1:])).uint(r["rating"]).uint(r["games"]).uint(r["wins"]).uint(stars)
        w.uint(r["peak"])
    return Packet(LEGEND_RATINGS, w.bool(False).bytes())


def career_packets(ledger: "Ledger", account_id: int) -> list[Packet]:
    c = ledger.career(account_id)
    w = BitWriter()
    for hero, r in sorted(c["legends"].items()):
        w.bool(True).uint(hero).string(str(r["dealt"])).string(str(r["taken"]))
        for v in (r["kos"], r["falls"], r["self_kos"], r["team_kos"], r["duration"] // 1000, r["games"], r["wins"]):
            w.uint(v)
        for f in ("damage_unarmed", "damage_thrown", "damage_weapon1", "damage_weapon2", "damage_gadgets"):
            w.string(str(r[f]))
        for f in ("ko_unarmed", "ko_thrown", "ko_weapon1", "ko_weapon2", "ko_gadgets"):
            w.uint(r[f])
        w.uint(r["held_weapon1"] // 1000).uint(r["held_weapon2"] // 1000)
    a = c["account"]
    acc = BitWriter().uint(a["games"]).uint(a["wins"])
    for f in ("damage_bomb", "damage_mine", "damage_spikeball", "damage_flyby"):
        acc.string(str(a[f]))
    acc.uint(a["hits_snowball"])
    for f in ("ko_bomb", "ko_mine", "ko_spikeball", "ko_flyby", "ko_snowball"):
        acc.uint(a[f])
    return [Packet(LEGEND_CAREER, w.bool(False).bytes()), Packet(ACCOUNT_CAREER, acc.bytes())]


def legend_levels_packet(ledger: "Ledger", account_id: int) -> Packet | None:
    with ledger.lock:
        rows = ledger.db.execute("SELECT legend, xp FROM legend_xp WHERE account_id = ?", (account_id,)).fetchall()
    if not rows:
        return None
    w = BitWriter().uint(len(rows))
    for r in rows:
        level, start, nxt = level_span(r["xp"])
        w.uint(r["legend"]).uint(level).uint(r["xp"]).uint(start).uint(nxt)
    return Packet(LEGEND_LEVELS, w.bytes())


def rank_packet(player_id: int, rank: int, region: int) -> Packet:
    return Packet(RANK, BitWriter().uint(player_id).uint(ELO_1V1).uint(rank).bool(False).uint(region).bytes())


def teams_packet(ledger: "Ledger", player_id: int, account_id: int, limit: int = 10) -> Packet | None:
    teams = ledger.teams_of(account_id)[:limit]
    rows = []
    for t in teams:
        mate = ledger.player_of_account(t["partner"])
        if mate is None:
            continue
        rows.append((mate, t))
    if not rows:
        return None
    me = ledger.name_of(account_id).replace("+", " ")
    w = BitWriter()
    for mate, t in rows:
        w.bool(True).uint(player_id).uint(mate).string(me + "+" + t["name"].replace("+", " "))
        w.uint(t["games"]).uint(t["wins"]).uint(t["rank"]).uint(stars_for(t["rating"])).uint(t["rating"])
    return Packet(TEAMS, w.bool(False).bytes())


def rating_packets(ledger: "Ledger", player_id: int, account_id: int, cfg: dict | None) -> list[Packet]:
    out = []
    row = ledger.rating(account_id)
    if row:
        out.append(ratings_packet(player_id, row))
    duo = ledger.rating(account_id, "2v2")
    if duo:
        out.append(ratings_packet(player_id, duo, LADDERS["2v2"]))
    return out


_results_lock = threading.Lock()


def results_for(rooms, store, log, cfg: dict | None = None) -> Results:
    with _results_lock:
        res = getattr(rooms, "results", None)
        if res is None:
            res = Results(ledger_for(store), log, cfg)
            rooms.results = res
            if hasattr(rooms, "match_over"):
                res.listeners.append(lambda entry, verdict, out, rep: rooms.match_over(rooms.by_number.get(entry.key[0]), entry.key[1]))
        return res


def arcade_packet(rows) -> Packet:
    w = BitWriter()
    for legend, score, placement in rows:
        w.bool(True).uint(legend).uint(score).uint(placement)
    return Packet(ARCADE_BESTS, w.bool(False).bytes())


class Desk:

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms

    def _results(self) -> Results | None:
        s = self.session
        return results_for(self.rooms, s.store, s.log, s.cfg) if s.store is not None else None

    def _me(self) -> int | None:
        p = self.session.player
        return p["id"] if p else self.session.player_id

    def account(self) -> int | None:
        me = self._me()
        if self.session.store is None or me is None:
            return None
        return ledger_for(self.session.store).account_of_player(me)

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        log, me = self.session.log, self._me()
        if p.type == ASK and me is not None:
            query = BitReader(p.payload).string()[:64]
            if query == "activity":
                social = getattr(self.session, "social", None)
                body = json.dumps(social.activity() if social is not None else {"error": "no presence"}, separators=(",", ":"))
                self.session.send(Packet(ANSWER, BitWriter().string(query).string(body).bytes()))
                return True
        if p.type == CAREER_ASK:
            acc = self.account()
            if acc is not None:
                for pk in career_packets(ledger_for(self.session.store), acc):
                    self.session.send(pk)
                log(f"career stats sent to player {me}")
            return True
        if p.type == ARCADE_ASK:
            acc = self.account()
            rows = ledger_for(self.session.store).arcade_bests(acc) if acc is not None else []
            self.session.send(arcade_packet(rows))
            log(f"arcade records sent to player {me} ({len(rows)} legends)")
            return True
        if p.type == ARCADE_RESULT:
            acc = self.account()
            r = BitReader(p.payload)
            try:
                mode, legend, score, placement = r.uint(), r.uint(), r.uint(), r.uint()
                rounds = [[r.uint() for _ in range(6)] for _ in range(3)]
            except IndexError:
                log(f"arcade result from player {me} unreadable")
                return True
            if acc is None or not 0 < legend < 0x10000 or score > ARCADE_MAX_SCORE:
                log(f"arcade result from player {me} not kept (legend {legend}, score {score}" +
                    (", a guest)" if acc is None else ")"))
                return True
            best = ledger_for(self.session.store).arcade_record(acc, mode, legend, score, placement)
            log(f"arcade run by player {me}: mode {mode}, legend {legend}, score {score}, place {placement}, rounds "
                f"{rounds}" + (" (a new best)" if best else ""))
            return True
        results = self._results()
        if me is None or results is None:
            log(f"stats packet {p.type} " + ("before login" if me is None else "without a store") + ": ignored")
            return True
        if p.type == REGION:
            acc = self.account()
            try:
                region = BitReader(p.payload).uint()
            except IndexError:
                return True
            if acc is not None and region < len(regions(self.session.cfg)):
                ledger_for(self.session.store).set_region(acc, region)
                log(f"player {me} is in region {region}")
            return True
        if p.type == LEGEND_NAMES:
            r, names = BitReader(p.payload), {}
            try:
                for _ in range(min(r.uint(), 500)):
                    hid, name = r.uint(), r.string()[:MAX_TEXT]
                    if 0 < hid < 0x10000 and name:
                        names[hid] = name
            except IndexError:
                names = {}
            if names and self.account() is not None:
                ledger_for(self.session.store).set_legend_names(names)
            return True
        if p.type == BIO:
            acc = self.account()
            try:
                text = BitReader(p.payload).string()
            except IndexError:
                return True
            if acc is not None:
                kept = ledger_for(self.session.store).set_bio(acc, text)
                log(f"player {me} set their bio ({len(kept)} characters)")
            return True
        if p.type == PROFILE:
            acc = self.account()
            try:
                values = read_profile(p.payload)
            except ValueError as e:
                log(f"profile from player {me} unreadable: {e}")
                return True
            if acc is not None:
                ledger_for(self.session.store).set_profile(acc, values)
            return True
        if p.type == REPORT:
            try:
                rep = read_report(p.payload)
            except ValueError as e:
                log(f"match report from player {me} unreadable: {e}")
                self.session.send(Packet(RECORDED, BitWriter().bool(False).string("The server couldn't read the match result.").bytes()))
                return True
            room = self.rooms.room_of(me)
            what, refusal = results.report(me, rep, room)
            log(f"match report from player {me} for room {rep['room']}: {what}")
            lobby = getattr(self.session, "lobby", None)
            if room is not None and room.number == rep["room"] and room.lineup:
                from .lobby import left_participants, on_scoreboard, participant_left_packet, participants_packet
                self.session.send(participants_packet(room, {pl["player"]: pl["legend"] for pl in rep["players"]}))
                for gone in left_participants(room):
                    self.session.send(participant_left_packet(gone))
                on_scoreboard(self.rooms, room, me)
            if lobby is not None and hasattr(lobby, "match_reported") and (rep["room"], rep["seed"]) in results.done:
                lobby.match_reported(room)
            if refusal:
                self.session.send(Packet(RECORDED, BitWriter().bool(False).string(refusal).bytes()))
        else:
            query = BitReader(p.payload).string()[:64]
            results.sweep()
            body = answer(ledger_for(self.session.store), query, self.account(), self.session.cfg)
            self.session.send(Packet(ANSWER, BitWriter().string(query).string(body).bytes()))
            log(f"stats asked by player {me}: {query!r} ({len(body)} bytes)")
        return True

    def after_login(self) -> None:
        me, acc = self._me(), self.account()
        if me is None or acc is None:
            return
        ledger = ledger_for(self.session.store)
        packets = rating_packets(ledger, me, acc, self.session.cfg)
        for pk in packets:
            self.session.send(pk)
        for pk in (legend_ratings_packet(ledger, acc), legend_levels_packet(ledger, acc), teams_packet(ledger, me, acc)):
            if pk is not None:
                self.session.send(pk)
        if packets:
            one = ledger.rating(acc)
            self.session.log(f"ratings for player {me}: 1v1 {one['rating'] if one else '-'} ({len(packets)} rows/ranks sent)")


def read_profile(payload: bytes) -> dict:
    r = BitReader(payload)
    try:
        version = r.uint()
        if version != 1:
            raise ValueError(f"profile version {version}")
        values = {k: r.uint() for k in PROFILE_FIELDS}
    except IndexError:
        raise ValueError("profile too short") from None
    if r.pos > len(payload) * 8:
        raise ValueError("profile too short")
    if any(v > 0xFFFFFF for v in values.values()):
        raise ValueError("a number out of range")
    return values


def write_profile(values: dict) -> bytes:
    w = BitWriter().uint(1)
    for k in PROFILE_FIELDS:
        w.uint(int(values.get(k) or 0))
    return w.bytes()


def regions(cfg: dict | None) -> list[str]:
    names = (cfg or {}).get("regions") or ["Global"]
    return [str(n)[:24] for n in names][:16]


def _searched(rows: list[dict], search: str) -> list[dict]:
    s = (search or "").strip().casefold()
    return [d for d in rows if s in str(d.get("name", "")).casefold()] if s else rows


def _ladder_query(rest: str) -> tuple[str, int | None, str, str] | None:
    parts = rest.split(":", 3)
    ladder = parts[0] or "all"
    if ladder not in LADDERS and ladder not in ("all", TEAM_LADDER) and not is_legend_ladder(ladder):
        return None
    season = None
    if len(parts) > 1:
        if not parts[1].isdigit():
            return None
        season = int(parts[1]) or None
    filt = parts[2] if len(parts) > 2 else "all"
    if filt not in ("all", "friends") and not (filt[:1] == "r" and filt[1:].isdigit()):
        return None
    search = urllib.parse.unquote(parts[3])[:MAX_TEXT] if len(parts) > 3 else ""
    return ladder, season, filt, search


def answer(ledger: Ledger, query: str, account_id: int | None, cfg: dict | None = None) -> str:
    lq = _ladder_query(query[12:]) if query.startswith("leaderboard:") else None
    parts = query.split(":")
    if query == "me":
        obj = ledger.player(account_id, recent=10) if account_id else None
        obj = obj or {"error": "Sign in with Discord (Options) to keep stats on this server."}
    elif query == "leaderboard":
        obj = {"ladder": "all", "season": ledger.season_id(), "rows": ledger.leaderboard(50)}
    elif lq is not None:
        ladder, season, filt, search = lq
        season = season or ledger.season_id()
        if filt == "friends" and not account_id:
            obj = {"ladder": ladder, "season": season, "filter": filt, "rows": [],
                   "error": "Sign in with Discord (Options) to see your friends here."}
        else:
            only = ledger.friend_accounts(account_id) if filt == "friends" else None
            region = int(filt[1:]) if filt.startswith("r") else None
            rows = (ledger.team_leaderboard(100, season, only, region, search) if ladder == TEAM_LADDER else
                    ledger.leaderboard(100, ladder, season, only, region, search))
            obj = {"ladder": ladder, "season": season, "filter": filt, "rows": rows, "you": account_id}
            if search:
                obj["search"] = search
    elif parts[0] == "legends" and len(parts) <= 2 and all(x.isdigit() for x in parts[1:]):
        obj = {"legends": ledger.legends_rated(int(parts[1]) if len(parts) > 1 else None)}
    elif parts[0] == "clans" and len(parts) <= 2 and all(x.isdigit() for x in parts[1:]):
        obj = {"clans": ledger.clan_leaderboard(int(parts[1]) if len(parts) > 1 else None)}
    elif query == "seasons":
        obj = {"seasons": ledger.seasons(), "ladders": [*LADDERS, TEAM_LADDER], "regions": regions(cfg),
               "you": account_id, "your_region": ledger.profile(account_id)["region"] if account_id else 0}
    elif parts[0] == "ranked" and len(parts) == 2 and parts[1] in LADDERS:
        obj = ledger.ranked_summary(account_id, parts[1]) if account_id else {"error": "not signed in"}
    elif parts[0] == "ranked" and len(parts) == 3 and parts[1] in LADDERS and parts[2].isdigit():
        acc = ledger.account_of_player(int(parts[2]))
        obj = dict(ledger.ranked_summary(acc, parts[1]), name=ledger.name_of(acc)) if acc else {"error": "no such player"}
    elif parts[0] == "ranked" and len(parts) == 3 and parts[1] == "team" and parts[2].isdigit():
        acc = ledger.account_of_player(int(parts[2]))
        obj = (ledger.team_summary(account_id, acc) if account_id and acc and acc != account_id else
               {"error": "not signed in" if not account_id else "no such player"})
    elif query == "recent":
        obj = {"matches": ledger.recent(20, rated=True)}
    elif query.startswith("recent:"):
        search = urllib.parse.unquote(query[7:])[:MAX_TEXT]
        obj = {"matches": ledger.recent(20, rated=True, search=search), "search": search}
    elif (parts[0] == "player" and len(parts) in (2, 3, 4) and all(x.isdigit() for x in parts[1:3]) and
          (len(parts) < 4 or parts[3] in LADDERS or is_legend_ladder(parts[3]))):
        them = int(parts[1])
        ladder = parts[3] if len(parts) > 3 else None
        obj = ledger.player(them, recent=10, season=int(parts[2]) or None if len(parts) > 2 else None, ladder=ladder)
        if obj is None:
            obj = {"error": "no such player"}
        elif account_id and account_id != them:
            obj["vs_you"] = ledger.head_to_head(account_id, them, ladder)
    elif parts[0] == "profile" and len(parts) == 2 and (parts[1].isdigit() or
                                                        (parts[1][:1] == "a" and parts[1][1:].isdigit())):
        by_account = parts[1][0] == "a"
        acc = int(parts[1][1:]) if by_account else ledger.account_of_player(int(parts[1]))
        obj = ledger.player(acc, recent=20) if acc else None
        if obj is None:
            obj = {"error": "no such player"}
        else:
            if not by_account:
                obj["user_id"] = int(parts[1])
            obj["seasons"] = ledger.past_seasons(acc)
            obj["weapons"] = ledger.weapons(acc)[:6]
            obj["partners"] = ledger.partners(acc)
            obj["bio"] = ledger.bio(acc)
            obj["look"] = ledger.look(acc)
            obj["badges"] = ledger.badges(acc)
            obj["mine"] = acc == account_id
            if account_id and account_id != acc:
                obj["vs_you"] = ledger.head_to_head(account_id, acc, None)
    elif parts[0] == "team" and len(parts) in (2, 3) and "-" in parts[1]:
        ab = parts[1].split("-")
        if len(ab) == 2 and all(x.isdigit() for x in ab) and all(x.isdigit() for x in parts[2:]):
            obj = ledger.team(int(ab[0]), int(ab[1]), int(parts[2]) or None if len(parts) > 2 else None) or {"error": "no such team"}
        else:
            obj = {"error": "unknown query"}
    else:
        obj = {"error": "unknown query"}
    return _fit(obj)


def _fit(obj: dict) -> str:
    body = json.dumps(obj, separators=(",", ":"))
    if len(body.encode()) <= MAX_JSON:
        return body
    keys = [k for k, v in obj.items() if isinstance(v, list) and v]

    def lengths(n: int) -> list[int]:
        lens = [len(obj[k]) for k in keys]
        for _ in range(n):
            i = max(range(len(lens)), key=lens.__getitem__)
            lens[i] -= 1
        return lens

    def dump(n: int) -> str:
        cut = dict(zip(keys, lengths(n)))
        return json.dumps({k: (v[:cut[k]] if k in cut else v) for k, v in obj.items()}, separators=(",", ":"))

    total = sum(len(obj[k]) for k in keys)
    if not keys or len(dump(total).encode()) > MAX_JSON:
        return json.dumps({"error": "too large"})
    lo, hi = 0, total
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if len(dump(mid).encode()) <= MAX_JSON:
            hi = mid
        else:
            lo = mid
    return dump(hi)


STYLE = ("<style>table{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 20px}th,td{padding:4px 6px;"
         "text-align:right;border-bottom:1px solid #262b36}th:first-child,td:first-child,td.l,th.l{text-align:left}"
         "th{color:#9aa3b2;font-weight:600}small,.m{color:#9aa3b2}.w{color:#4cc2b6}.x{color:#e07a7a}</style>")
NAV = "<p><a href='/leaderboard'>Leaderboard</a> · <a href='/matches'>Recent matches</a> · <a href='/mods'>Shared mods</a> · <a href='/'>Your account</a></p>"


def _e(v) -> str:
    return html.escape("" if v is None else str(v))


def _rate(d: dict) -> str:
    return f"{round(100 * d['win_rate'])}%" if d["matches"] else "-"


TIER_COLOURS = {"Tin": "#8c8f95", "Bronze": "#c0814d", "Silver": "#b9c3cf", "Gold": "#e8c14a", "Platinum": "#5fd0c6",
                "Diamond": "#7aa8ff", "Valhallan": "#d38bff", "Unranked": "#9aa3b2"}


def leaderboard_html(ledger: Ledger, ladder: str = "1v1", season: int | None = None) -> tuple[str, str]:
    season = season or ledger.season_id()
    seasons = ledger.seasons()
    shown = {"1v1": "1V1", "2v2": "2V2 SOLO", TEAM_LADDER: "2V2 TEAM", "3v3": "3V3"}
    tabs = " · ".join(f"<b>{t}</b>" if x == ladder else f"<a href='/leaderboard/{x}/{season}'>{t}</a>"
                      for x, t in shown.items())
    picks = " · ".join(f"<b>{_e(z['name'])}</b>" if z["id"] == season else
                       f"<a href='/leaderboard/{ladder}/{z['id']}'>{_e(z['name'])}</a>" for z in seasons)
    head = STYLE + NAV + f"<p>{tabs}</p><p class=m>Season: {picks}</p>"
    rows = ledger.team_leaderboard(100, season) if ladder == TEAM_LADDER else ledger.leaderboard(100, ladder, season)
    if not rows:
        return "Leaderboard", head + "<p class=m>No counted matches in this ladder and season.</p>"
    body = []
    for d in rows:
        r = d["rating"]
        tier = r["tier"] if r else "Unranked"
        rating = f"{r['rating']}" if r else "-"
        if r and r["games"] < PLACEMENT_GAMES:
            tier = f"Placement {r['games']}/{PLACEMENT_GAMES}"
        who = (" &amp; ".join(f"<a href='/player/{m['id']}'>{_e(m['name'])}</a>" for m in d["members"]) if ladder == TEAM_LADDER
               else f"<a href='/player/{d['id']}'>{_e(d['name'])}</a>")
        main = (", ".join(_e(ledger.legend_name(m["top_legend"])) for m in d["members"]) if ladder == TEAM_LADDER
                else _e(ledger.legend_name(d["top_legend"])))
        body.append(f"<tr><td class=l>{d['rank']}. {who}</td>"
                    f"<td class=l style='color:{TIER_COLOURS.get(tier, '#9aa3b2')}'>{tier}</td><td>{rating}</td>"
                    f"<td class=l>{main}</td><td>{d['matches']}</td><td>{d['wins']}</td>"
                    f"<td>{d['losses']}</td><td>{_rate(d)}</td><td>{d['kos']}</td><td>{d['falls']}</td></tr>")
    what = ("every counted match, ranked by the 1v1 ladder" if ladder == "all" else
            f"{ladder} matches; rating (Elo, from {START_RATING}) after {PLACEMENT_GAMES} placement games")
    return "Leaderboard", (head + f"<p class=m>{what}. Only matches every player's game reported the same way count.</p>"
                           "<table><tr><th class=l>Player</th><th class=l>Tier</th><th>Rating</th><th class=l>Main</th>"
                           "<th>Games</th><th>Wins</th><th>Losses</th><th>Win %</th><th>KOs</th><th>Falls</th></tr>"
                           + "".join(body) + "</table>")
