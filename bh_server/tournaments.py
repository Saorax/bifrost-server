from __future__ import annotations

import html
import json
import re
import random
import sqlite3
import threading
import time
from xml.sax.saxutils import escape

from .bits import BitReader, BitWriter
from .frame import Packet
from .stats import ANSWER, ASK, COUNTED, MAX_JSON, START_RATING, outcomes

SCHEMA = """
CREATE TABLE IF NOT EXISTS tournaments (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, owner INTEGER NOT NULL, state TEXT NOT NULL, best_of INTEGER NOT NULL,
  max_players INTEGER NOT NULL, about TEXT NOT NULL DEFAULT '', created REAL NOT NULL, starts REAL, started REAL,
  finished REAL, winner INTEGER, mode TEXT NOT NULL DEFAULT '1v1', checkin INTEGER NOT NULL DEFAULT 0,
  best_of_top8 INTEGER NOT NULL DEFAULT 0, best_of_final INTEGER NOT NULL DEFAULT 0,
  min_rating INTEGER NOT NULL DEFAULT 0, max_rating INTEGER NOT NULL DEFAULT 0, losers INTEGER NOT NULL DEFAULT 0,
  paused REAL, stocks INTEGER NOT NULL DEFAULT 3, minutes INTEGER NOT NULL DEFAULT 8,
  seeding TEXT NOT NULL DEFAULT 'rating', rules TEXT NOT NULL DEFAULT '', region TEXT NOT NULL DEFAULT '',
  stream TEXT NOT NULL DEFAULT '', prize TEXT NOT NULL DEFAULT '', legend_rule TEXT NOT NULL DEFAULT '',
  legends TEXT NOT NULL DEFAULT '', weapon_rule TEXT NOT NULL DEFAULT '', weapons TEXT NOT NULL DEFAULT '',
  map_set INTEGER NOT NULL DEFAULT 0, map_pick TEXT NOT NULL DEFAULT 'random', scoring INTEGER NOT NULL DEFAULT 2,
  damage INTEGER NOT NULL DEFAULT 100, variation INTEGER NOT NULL DEFAULT 0, weapon_rate INTEGER NOT NULL DEFAULT 2,
  gadget_rate INTEGER NOT NULL DEFAULT 0, team_damage INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS tournament_entrants (
  tournament INTEGER NOT NULL, account_id INTEGER NOT NULL, joined REAL NOT NULL, seed INTEGER, partner INTEGER,
  PRIMARY KEY (tournament, account_id));
CREATE TABLE IF NOT EXISTS tournament_matches (
  tournament INTEGER NOT NULL, round INTEGER NOT NULL, slot INTEGER NOT NULL, a INTEGER, b INTEGER,
  wins_a INTEGER NOT NULL DEFAULT 0, wins_b INTEGER NOT NULL DEFAULT 0, winner INTEGER, results TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '', ready_at REAL, deadline REAL, checked TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (tournament, round, slot));
CREATE TABLE IF NOT EXISTS tournament_admins (
  tournament INTEGER NOT NULL, account_id INTEGER NOT NULL, PRIMARY KEY (tournament, account_id));
CREATE TABLE IF NOT EXISTS tournament_team_invites (
  tournament INTEGER NOT NULL, inviter INTEGER NOT NULL, invitee INTEGER NOT NULL, created REAL NOT NULL,
  PRIMARY KEY (tournament, inviter, invitee));
"""

OPEN, RUNNING, DONE, CANCELLED = "open", "running", "done", "cancelled"
MODES = ("1v1", "2v2")
BEST_OF = (1, 3, 5)
LATER_BEST_OF = (0, 3, 5, 7)
SINGLE, DOUBLE, DOUBLE_NO_RESET = 0, 1, 2
ELIMINATION = (SINGLE, DOUBLE, DOUBLE_NO_RESET)
LOSERS, GRAND, GRAND_RESET = 100, 200, 201
POOL, POOL_SLOTS, MAX_POOLS = -100, 64, 64
MAX_POOL_SIZE = 16
POOL_KINDS = ("robin", "bracket")
POOL_ADVANCE = (1, 2, 4)
POOLS_UNTIL = (0, 2, 4, 8, 16, 32, 64, 128)
PHASE_CUTS = (2, 4, 8, 16, 32, 64, 128)
START_IN_DAYS = 365
NOT_PLAYED = "not played"
DISQUALIFIED = "disqualified"
MAX_ADMINS = 5
STOCKS, MINUTES = tuple(range(1, 11)), (2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30)
SCORINGS = {2: "Stock", 1: "Timed"}
GAME_MODES = {3: ("Brawlball", "2v2", [1, 4, 900, 45, 0, 3, 5, 100, 100, 7, 5, 7, 0, 0, 0]),
         4: ("Bombsketball", "2v2", [5, 4, 120, 0, 0, 4, 0, 100, 100, 9, 1, 0, 0, 0, 0]),
         33: ("Capture the Flag", "2v2", [5, 8, 480, 0, 0, 33, 3, 100, 100, 34, 9, 14, 0, 0, 0]),
         39: ("Kung Foot", "2v2", [5, 4, 900, 0, 0, 39, 7, 100, 100, 39, 6, 9, 0, 0, 0]),
         48: ("Crew Battle", "2v2", [513, 8, 1200, 0, 3, 48, 0, 100, 100, 36, 2, 2, 4, 0, 0]),
         49: ("Street Brawl", "1v1", [4, 2, 480, 0, 3, 49, 0, 100, 100, 6, 3, 2, 0, 0, 0])}
ALL_SCORINGS = {**SCORINGS, **{k: v[0] for k, v in GAME_MODES.items()}}
VARIATIONS = {0: "None", 1: "Strikeout", 2: "Switchcraft", 3: "Morph"}
WEAPON_RATES = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
GADGET_RATES = {0: "Off", 4: "Low", 5: "Medium", 6: "High"}
DAMAGE_RANGE = (50, 300)
SEEDINGS = ("rating", "random", "signup", "manual")
LIST_RULES = ("", "ban", "only")
WEAPONS = ("Axe", "Boots", "Bow", "Cannon", "Chakram", "Fists", "Greatsword", "Hammer", "Katar", "Orb", "Pistol",
           "RocketLance", "Scythe", "Spear", "Sword")
MAP_POOLS = {"1v1": (36, 98, 3, 33), "2v2": (37, 99, 109, 4)}
MAP_PICKS = {"random": 0, "straight": 1, "snake": 2, "tournament": 4}
DEFAULT_MAP_PICK = "tournament"
MAP_POOL_NAMES = {36: "Tournament 1v1", 98: "Ranked 1v1", 3: "Standard 1v1", 33: "Experimental 1v1", 37: "Tournament 2v2",
                  99: "Ranked 2v2", 109: "Unranked 2v2", 4: "Standard 2v2"}
NOTES = {"rules": 300, "region": 40, "stream": 120, "prize": 100}
MORE = ("stocks", "minutes", "seeding", "start_in", *NOTES, "legend_rule", "legends", "weapon_rule", "weapons",
        "map_set", "map_pick", "scoring", "damage", "variation", "weapon_rate", "gadget_rate", "team_damage",
        "pools_until", "pool_advance", "pool_kind", "phases")
RULES = ("scoring", "damage", "variation", "weapon_rate", "gadget_rate", "team_damage")
MAX_RATING_LIMIT = 4000
MIN_PLAYERS, MAX_PLAYERS = 2, 256
MAX_NAME, MAX_ABOUT = 40, 200
TICK = 3.0
LOCK_HOLD = 5.0
CHECKIN_MINUTES, CHECKIN_CHOICES = 10, (0, 5, 10, 15, 20, 30)
EXTEND_MINUTES = 5
NO_SHOW = "no-show"
AWARDED = "awarded"
REPORTED = "reported"
SCORED_NOTES = (AWARDED, NO_SHOW, DISQUALIFIED)
EVENT_IMAGE = "placeholder.jpg"
ROOM_GAME = {"1v1": [0, 2, 480, 0, 3, 2, 0, 100, 100, 36, 2, 2, 0, 0, 0],
             "2v2": [3, 4, 480, 0, 3, 2, 0, 100, 100, 37, 2, 2, 0, 0, 0]}
LEVELS = {"1v1": (135, 42, 212, 223, 229, 233, 29, 28),
          "2v2": (42, 212, 223, 229, 18, 269, 29, 28)}
PRIVATE_ROOM = 14


def list_row(key: str, title: str, text: str) -> Packet:
    return Packet(LIST_NOTICE, BitWriter().string(key).string(title).string(text).bytes())


def list_notice(text: str) -> Packet:
    name, _, news = text.partition(": ")
    if not news:
        name, news = "", text
    return list_row("tour:" + name, (name or "Tournament").upper(), news)


_level_sets: dict | None = None


def level_set_levels(set_id: int) -> tuple:
    global _level_sets
    if _level_sets is None:
        from .missions import GAME_DATA
        _level_sets = {}
        try:
            sets = (GAME_DATA / "LevelSetTypes.xml").read_text(encoding="utf-8", errors="replace")
            types = (GAME_DATA.parent / "Init" / "LevelTypes.xml").read_text(encoding="utf-8", errors="replace")
            ids = {m.group(1): int(m.group(2)) for m in
                   re.finditer(r'<LevelType LevelName="([^"]*)"[^>]*>.*?<LevelID>(\d+)</LevelID>', types, re.S)}
            for m in re.finditer(r"<LevelSetType [^>]*>(.*?)</LevelSetType>", sets, re.S):
                sid = re.search(r"<LevelSetID>(\d+)</LevelSetID>", m.group(1))
                names = re.search(r"<LevelTypes>([^<]*)</LevelTypes>", m.group(1))
                if sid and names:
                    _level_sets[int(sid.group(1))] = tuple(ids[n] for n in names.group(1).split(",") if n in ids)
        except OSError:
            pass
    return _level_sets.get(set_id, ())


_hero_names: dict | None = None


def hero_names() -> dict:
    global _hero_names
    if _hero_names is None:
        from .missions import GAME_DATA
        _hero_names = {}
        try:
            text = (GAME_DATA / "HeroTypes.xml").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'<HeroType HeroName="([^"]*)">(.*?)</HeroType>', text, re.S):
                hid = re.search(r"<HeroID>(\d+)</HeroID>", m.group(2))
                bio = re.search(r"<BioName>([^<]*)</BioName>", m.group(2))
                if hid and int(hid.group(1)) > 2:
                    name = bio.group(1).strip().strip('"') if bio and bio.group(1).strip().strip('"') else m.group(1)
                    _hero_names[int(hid.group(1))] = name
        except OSError:
            pass
    return _hero_names


def hero_ids() -> dict:
    import unicodedata
    from .missions import GAME_DATA
    out = {}
    for hid, name in hero_names().items():
        key = name.lower().replace(" ", "")
        out[key] = hid
        out.setdefault("".join(c for c in unicodedata.normalize("NFKD", key) if not unicodedata.combining(c)), hid)
    try:
        text = (GAME_DATA / "HeroTypes.xml").read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'<HeroType HeroName="([^"]*)"><HeroID>(\d+)</HeroID>', text):
            out.setdefault(m.group(1).lower().replace(" ", ""), int(m.group(2)))
    except OSError:
        pass
    return out


def room_game(m: dict) -> list:
    if m.get("scoring") in GAME_MODES:
        game = list(GAME_MODES[m["scoring"]][2])
        if game[4]:
            game[4] = m["stocks"]
        game[8] = m.get("damage") or 100
        if m["mode"] == "2v2" and m.get("team_damage") == 0:
            game[0] &= ~2
        return game
    game = list(ROOM_GAME[m["mode"]])
    game[2], game[4] = m["minutes"] * 60, m["stocks"]
    game[5] = m.get("scoring") or 2
    game[8] = m.get("damage") or 100
    game[11] = m["weapon_rate"] if m.get("weapon_rate") is not None else 2
    game[12] = m.get("gadget_rate") or 0
    game[14] = m.get("variation") or 0
    if m["mode"] == "2v2" and m.get("team_damage") == 0:
        game[0] &= ~2
    if m.get("map_set"):
        game[9] = m["map_set"]
    return game


def map_pool(t: dict) -> tuple:
    if t.get("scoring") in GAME_MODES:
        got = level_set_levels(GAME_MODES[t["scoring"]][2][9])
        if got:
            return got
    got = level_set_levels(t["map_set"]) if t.get("map_set") else ()
    return got or LEVELS[t["mode"]]


def heroes_off(t: dict) -> tuple[set, str]:
    from .missions import heroes
    legends = {int(x) for x in str(t.get("legends") or "").split(",") if x.strip().isdigit()}
    weapons = {x for x in str(t.get("weapons") or "").split(",") if x}
    lrule, wrule = t.get("legend_rule") or "", t.get("weapon_rule") or ""
    if not (lrule and legends) and not (wrule and weapons):
        return set(), ""
    off = set()
    for hid, (carried, _tags) in heroes().items():
        if hid <= 2:
            continue
        carried = {w for w in carried if w and w != "--"}
        if (lrule == "ban" and hid in legends) or (lrule == "only" and legends and hid not in legends) or \
                (wrule == "ban" and carried & weapons) or (wrule == "only" and weapons and not carried <= weapons):
            off.add(hid)
    parts = []
    if lrule and legends:
        parts.append("legend banned" if lrule == "ban" else "legend not allowed")
    if wrule and weapons:
        parts.append(("no " if wrule == "ban" else "only ") + ", ".join(sorted(weapons)).lower())
    return off, f"{t.get('name', 'Tournament')}: {' or '.join(parts)}. Pick another."


def bracket_order(size: int) -> list[int]:
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def bracket_size(n: int) -> int:
    size = 2
    while size < n:
        size *= 2
    return size


def round_name(rnd: int, rounds: int, double: bool = False) -> str:
    if rnd < 0:
        return pool_name(rnd - POOL)
    if rnd == GRAND:
        return "Grand final"
    if rnd == GRAND_RESET:
        return "Grand final reset"
    if rnd >= LOSERS:
        j = rnd - LOSERS
        return "Losers final" if j == losers_rounds(rounds) - 1 else f"Losers round {j + 1}"
    left = rounds - rnd
    name = {1: "Final", 2: "Semi-finals", 3: "Quarter-finals"}.get(left, f"Round {rnd + 1}")
    return "Winners " + name[0].lower() + name[1:] if double else name


def wins_needed(best_of: int) -> int:
    return (best_of + 1) // 2


def losers_rounds(rounds: int) -> int:
    return 2 * (rounds - 1)


def round_size(rnd: int, rounds: int) -> int:
    size = 1 << rounds
    if rnd >= GRAND:
        return 1
    if rnd >= LOSERS:
        return size >> (2 + (rnd - LOSERS) // 2)
    return size >> (rnd + 1)


def feeds(rnd: int, slot: int, rounds: int) -> tuple | None:
    if rnd == GRAND:
        return ((rounds - 1, 0, "w"), (LOSERS + losers_rounds(rounds) - 1, 0, "w") if rounds > 1 else (0, 0, "l"))
    if rnd == GRAND_RESET or rnd <= 0:
        return None
    if rnd < LOSERS:
        return ((rnd - 1, 2 * slot, "w"), (rnd - 1, 2 * slot + 1, "w"))
    j = rnd - LOSERS
    if j == 0:
        return ((0, 2 * slot, "l"), (0, 2 * slot + 1, "l"))
    if j % 2:
        k = (j + 1) // 2
        return ((rnd - 1, slot, "w"), (k, round_size(k, rounds) - 1 - slot, "l"))
    return ((rnd - 1, 2 * slot, "w"), (rnd - 1, 2 * slot + 1, "w"))


def round_best_of(t: dict, rnd: int, rounds: int) -> int:
    double = bool(t.get("losers"))
    if rnd < 0:
        return t["best_of"]
    if t.get("best_of_final") and (rnd >= GRAND or (not double and rnd == rounds - 1)):
        return t["best_of_final"]
    if t.get("best_of_top8") and rnd < GRAND and (round_size(rnd, rounds) <= 2 if rnd >= LOSERS
                                                   else (1 << (rounds - rnd)) <= 8):
        return t["best_of_top8"]
    return t["best_of"]


def out_place(rnd: int, rounds: int, double: bool) -> int | None:
    if rnd < 0:
        return None
    if rnd >= GRAND:
        return 2
    if not double:
        return 2 + sum(round_size(k, rounds) for k in range(rnd + 1, rounds))
    if rnd < LOSERS:
        return None
    return 3 + sum(round_size(LOSERS + k, rounds) for k in range(rnd - LOSERS + 1, losers_rounds(rounds)))


def placements(ms: list[dict], rounds: int, double: bool, winner: int | None) -> dict:
    reset = {s for m in ms if m["round"] == GRAND_RESET for s in (m["a"], m["b"]) if s is not None}
    out = {}
    for m in ms:
        if m["winner"] is None:
            continue
        for s in (m["a"], m["b"]):
            if s is None or s == m["winner"] or (m["round"] == GRAND and s in reset):
                continue
            place = out_place(m["round"], rounds, double)
            if place is not None:
                out[s] = min(place, out.get(s, place))
    if winner is not None:
        out[winner] = 1
    return out


def pool_name(p: int) -> str:
    return "Pool " + (chr(ord("A") + p) if p < 26 else str(p + 1))


def snake_pools(n: int, pools: int) -> list[int]:
    out = []
    for i in range(n):
        row, col = divmod(i, pools)
        out.append(col if row % 2 == 0 else pools - 1 - col)
    return out


def robin_rounds(k: int) -> list[list[tuple[int, int]]]:
    ids = list(range(k)) + ([None] if k % 2 else [])
    n = len(ids)
    out = []
    for _ in range(n - 1):
        pairs = [(ids[i], ids[n - 1 - i]) for i in range(n // 2)]
        out.append([(a, b) if a < b else (b, a) for a, b in pairs if a is not None and b is not None])
        ids = [ids[0], ids[-1]] + ids[1:-1]
    return out


def pool_bracket_rounds(n: int, advance: int) -> int:
    return max(0, (bracket_size(n) // advance).bit_length() - 1)


def pool_bracket_table(sides: list[int], ms: list[dict]) -> list[dict]:
    rows = {r["entrant"]: r for r in pool_table(sides, ms)}
    lost = {}
    for m in ms:
        if m["winner"] is not None and m["a"] is not None and m["b"] is not None:
            lost[m["b"] if m["winner"] == m["a"] else m["a"]] = m["slot"] // POOL_SLOTS
    seed = {s: i for i, s in enumerate(sides)}
    return sorted(rows.values(), key=lambda r: (r["entrant"] in lost, -lost.get(r["entrant"], 0), seed[r["entrant"]]))


def pool_table(sides: list[int], ms: list[dict]) -> list[dict]:
    rows = {s: {"entrant": s, "wins": 0, "losses": 0, "games_won": 0, "games_lost": 0, "played": 0} for s in sides}
    beat = set()
    for m in ms:
        if m["winner"] is None or m["a"] not in rows or m["b"] not in rows:
            continue
        loser = m["b"] if m["winner"] == m["a"] else m["a"]
        rows[m["winner"]]["wins"] += 1
        rows[loser]["losses"] += 1
        beat.add((m["winner"], loser))
        for s, won, lost in ((m["a"], m["wins_a"], m["wins_b"]), (m["b"], m["wins_b"], m["wins_a"])):
            rows[s]["games_won"] += won
            rows[s]["games_lost"] += lost
            rows[s]["played"] += 1
    seed = {s: i for i, s in enumerate(sides)}
    order = sorted(rows.values(), key=lambda r: (-r["wins"], r["games_lost"] - r["games_won"], seed[r["entrant"]]))
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and (order[j + 1]["wins"], order[j + 1]["games_won"] - order[j + 1]["games_lost"]) == \
                (order[i]["wins"], order[i]["games_won"] - order[i]["games_lost"]):
            j += 1
        if j == i + 1 and (order[j]["entrant"], order[i]["entrant"]) in beat:
            order[i], order[j] = order[j], order[i]
        i = j + 1
    return order


def round_alive(rnd: int, rounds: int, double: bool) -> int:
    if rnd >= GRAND:
        return 2
    if rnd >= LOSERS:
        return out_place(rnd, rounds, double) - 1 + round_size(rnd, rounds)
    return 1 << (rounds - rnd + (1 if double and rnd > 0 else 0))


def phase_cuts(text: str) -> list[int]:
    return sorted({int(x) for x in str(text or "").split(",") if x.strip().isdigit()}, reverse=True)


def phase_of(rnd: int, rounds: int, double: bool, cuts: list[int]) -> int:
    alive = round_alive(rnd, rounds, double)
    return sum(1 for c in cuts if alive <= c and c < (1 << rounds))


def bracket_rounds(rounds: int, double: bool) -> list[int]:
    every = list(range(rounds))
    if double:
        every += [LOSERS + j for j in range(losers_rounds(rounds))] + [GRAND, GRAND_RESET]
    return every


def phase_entries(rounds: int, cuts: list[int]) -> dict[int, list[tuple[int, int]]]:
    out: dict[int, list[tuple[int, int]]] = {}
    for rnd in bracket_rounds(rounds, True):
        if rnd >= GRAND:
            continue
        k = phase_of(rnd, rounds, True, cuts)
        src = feeds(rnd, 0, rounds)
        if k and src and any(phase_of(r, rounds, True, cuts) < k for r, _, _ in src):
            out.setdefault(k, []).extend((rnd, s) for s in range(round_size(rnd, rounds)))
    return out


def bracket_seeds(first_round: list[dict], rounds: int) -> dict[int, int]:
    order = bracket_order(1 << rounds)
    out = {}
    for m in first_round:
        for side, k in (("a", 0), ("b", 1)):
            if m[side] is not None:
                out[m[side]] = order[2 * m["slot"] + k]
    return out


def reseed(survivors: list[int], slots: list[int], seeds: dict[int, int]) -> dict[int, tuple]:
    ranked = sorted(survivors, key=lambda s: (seeds.get(s, 1 << 20), s))
    by_seed = {i + 1: s for i, s in enumerate(ranked)}
    order = bracket_order(2 * len(slots))
    return {sl: (by_seed.get(order[2 * i]), by_seed.get(order[2 * i + 1])) for i, sl in enumerate(sorted(slots))}


def phase_name(index: int, rounds: int, cuts: list[int]) -> str:
    live = [c for c in cuts if c < (1 << rounds)]
    top = ([1 << rounds] + live)[index]
    return "Finals" if top == 2 else f"Top {top}"


class Tournaments:

    def __init__(self, store, cfg: dict | None = None, log=None, ledger=None, social=None, clock=time.time) -> None:
        self.store, self.db = store, store.db
        self.cfg = cfg or {}
        self.log = log or (lambda line: None)
        self.ledger, self.social, self.clock = ledger, social, clock
        self.lock_hold = LOCK_HOLD
        if social is not None:
            social.answers["team"] = self._team_answered
            social.login_offers.append(self.offer_team_invites)
        self.lock = threading.RLock()
        self.random = random.Random()
        self.live: dict = {}
        self.room_invites: set = set()
        self.unchecked_told: set = set()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)
            for table, column, kind in (("tournaments", "mode", "TEXT NOT NULL DEFAULT '1v1'"),
                                        ("tournament_entrants", "partner", "INTEGER"),
                                        ("tournaments", "checkin", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "best_of_top8", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "best_of_final", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "min_rating", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "max_rating", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "losers", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "paused", "REAL"),
                                        ("tournaments", "stocks", "INTEGER NOT NULL DEFAULT 3"),
                                        ("tournaments", "minutes", "INTEGER NOT NULL DEFAULT 8"),
                                        ("tournaments", "seeding", "TEXT NOT NULL DEFAULT 'rating'"),
                                        ("tournaments", "rules", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "region", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "stream", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "prize", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "legend_rule", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "legends", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "weapon_rule", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "weapons", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournaments", "map_set", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "map_pick", "TEXT NOT NULL DEFAULT 'random'"),
                                        ("tournaments", "scoring", "INTEGER NOT NULL DEFAULT 2"),
                                        ("tournaments", "damage", "INTEGER NOT NULL DEFAULT 100"),
                                        ("tournaments", "variation", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "weapon_rate", "INTEGER NOT NULL DEFAULT 2"),
                                        ("tournaments", "gadget_rate", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "team_damage", "INTEGER NOT NULL DEFAULT 1"),
                                        ("tournaments", "pools_until", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournaments", "pool_advance", "INTEGER NOT NULL DEFAULT 2"),
                                        ("tournaments", "pool_kind", "TEXT NOT NULL DEFAULT 'robin'"),
                                        ("tournaments", "phases", "TEXT NOT NULL DEFAULT ''"),
                                        ("tournament_entrants", "disqualified", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournament_entrants", "waiting", "INTEGER NOT NULL DEFAULT 0"),
                                        ("tournament_matches", "ready_at", "REAL"),
                                        ("tournament_matches", "deadline", "REAL"),
                                        ("tournament_matches", "checked", "TEXT NOT NULL DEFAULT ''")):
                have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
                if column not in have:
                    self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    @classmethod
    def of(cls, rooms, store, cfg: dict | None = None, log=None, timers: bool = True) -> "Tournaments":
        from . import stats
        from .social import Social
        hub = getattr(rooms, "tournaments", None)
        if hub is None:
            results = stats.results_for(rooms, store, log or (lambda line: None), cfg)
            hub = cls(store, cfg, log, results.ledger, Social.of(rooms, store, cfg, log))
            hub.rooms = rooms
            results.listeners.append(hub.on_result)
            rooms.tournaments = hub
            hub.lock_hold = LOCK_HOLD if timers else 0.0
            if timers:
                hub._schedule()
        return hub

    def _schedule(self) -> None:
        def run():
            try:
                self.tick()
            except Exception as e:
                self.log(f"tournaments: tick failed: {type(e).__name__}: {e}")
            self._schedule()
        t = threading.Timer(TICK, run)
        t.daemon = True
        t.start()

    def get(self, tid: int) -> dict | None:
        row = self.db.execute("SELECT * FROM tournaments WHERE id = ?", (tid,)).fetchone()
        return dict(row) if row else None

    def entrants(self, tid: int) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM tournament_entrants WHERE tournament = ? AND waiting = 0 ORDER BY COALESCE(seed, 1e9), joined, "
            "account_id", (tid,))]

    def waitlist(self, tid: int) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM tournament_entrants WHERE tournament = ? AND waiting = 1 ORDER BY joined, account_id", (tid,))]

    def _promote(self, tid: int) -> list[int]:
        t = self.get(tid)
        told = []
        for w in self.waitlist(tid):
            if len(self.entrants(tid)) >= t["max_players"]:
                break
            with self.db:
                self.db.execute("UPDATE tournament_entrants SET waiting = 0 WHERE tournament = ? AND account_id = ?",
                                (tid, w["account_id"]))
            told += self.members(tid, w["account_id"])
            self.log(f"tournament {tid}: {w['account_id']} in from the waitlist")
        return told

    def matches(self, tid: int) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM tournament_matches WHERE tournament = ? ORDER BY round, slot", (tid,))]

    def listing(self, states=(OPEN, RUNNING, DONE)) -> list[dict]:
        q = ",".join("?" * len(states))
        rows = self.db.execute(f"SELECT * FROM tournaments WHERE state IN ({q}) ORDER BY "
                               "CASE state WHEN 'running' THEN 0 WHEN 'open' THEN 1 ELSE 2 END, id DESC", tuple(states))
        out = []
        for r in rows:
            t = dict(r)
            t["players"] = self.db.execute("SELECT COUNT(*) FROM tournament_entrants WHERE tournament = ? AND waiting = 0",
                                           (t["id"],)).fetchone()[0]
            out.append(t)
        return out

    def name_of(self, account_id: int | None) -> str:
        if account_id is None:
            return "-"
        if self.ledger is not None:
            return self.ledger.name_of(account_id)
        row = self.db.execute("SELECT name FROM players WHERE account_id = ? ORDER BY id LIMIT 1", (account_id,)).fetchone()
        return row[0] if row else f"player {account_id}"

    def rating_of(self, account_id: int, ladder: str = "1v1") -> int:
        row = self.ledger.rating(account_id, ladder) if self.ledger is not None else None
        return row["rating"] if row else START_RATING

    def members(self, tid: int, entrant: int | None) -> list[int]:
        if entrant is None:
            return []
        row = self.db.execute("SELECT partner FROM tournament_entrants WHERE tournament = ? AND account_id = ?",
                              (tid, entrant)).fetchone()
        return [entrant] + ([row[0]] if row and row[0] else [])

    def side_name(self, tid: int, entrant: int | None) -> str:
        if entrant is None:
            return "-"
        return " & ".join(self.name_of(a) for a in self.members(tid, entrant))

    def entrant_of(self, tid: int, account_id: int) -> int | None:
        row = self.db.execute("SELECT account_id FROM tournament_entrants WHERE tournament = ? AND waiting = 0 AND (account_id = ? OR "
                              "partner = ?)", (tid, account_id, account_id)).fetchone()
        return row[0] if row else None

    @staticmethod
    def legend_names(ids: str) -> str:
        names = hero_names()
        return ", ".join(names.get(int(x), f"Legend {x}") for x in str(ids or "").split(",") if x.strip().isdigit())

    def seed_rating(self, tid: int, mode: str, entrant: int) -> float:
        if mode == "2v2":
            accs = self.members(tid, entrant)
            return sum(self.rating_of(a, "2v2") for a in accs) / len(accs)
        return self.rating_of(entrant)

    def view(self, tid: int) -> dict | None:
        t = self.get(tid)
        if t is None:
            return None
        every = self.matches(tid)
        ms = [m for m in every if m["round"] >= 0]
        rounds = self._wrounds(tid)
        double = bool(t.get("losers"))
        places = placements(ms, rounds, double, t["winner"])
        pools = self.pool_tables(tid, t, every)
        if pools and all(p["done"] for p in pools):
            through = sum(1 for p in pools for r in p["standings"] if r["advances"])
            for p in pools:
                for r in p["standings"]:
                    if not r["advances"]:
                        places[r["entrant"]] = through + 1 + (r["place"] - t["pool_advance"] - 1) * len(pools)
        for p in pools:
            pm = [m for m in p.pop("matches") if m["note"] != "bye"]
            p["played"] = sum(1 for m in pm if m["winner"] is not None)
            p["total"] = len(pm)
            for r in p["standings"]:
                r["name"] = self.side_name(tid, r["entrant"])
        t["pools"] = pools
        if pools and rounds and not all(p["done"] for p in pools):
            order = bracket_order(1 << rounds)
            for m in ms:
                if m["round"] == 0:
                    for side, k in (("a", 0), ("b", 1)):
                        place, p = divmod(order[2 * m["slot"] + k] - 1, len(pools))
                        if m[side] is None and place < t["pool_advance"]:
                            m[side + "_from"] = f"{pool_name(p)} {('1st', '2nd', '3rd', '4th')[place]}"
        cuts = phase_cuts(t.get("phases"))
        if double and cuts and rounds:
            for k, keys in phase_entries(rounds, cuts).items():
                for m in ms:
                    if (m["round"], m["slot"]) in keys and m["a"] is None and m["b"] is None and m["winner"] is None \
                            and m["note"] != NOT_PLAYED:
                        m["a_from"] = m["b_from"] = (f"{phase_name(k - 1, rounds, cuts)} "
                                                     f"{'winners' if m['round'] < LOSERS else 'losers'} side")
        t["phase_list"] = ([{"phase": -1, "name": "Pools", "until": t["pools_until"]}] if self.pool_count(t) else []) + \
            [{"phase": i, "name": phase_name(i, rounds, cuts)} for i in
             range(1 + sum(1 for c in cuts if c < (1 << rounds)))] if rounds else []
        t["entrants"] = [{"account": e["account_id"], "name": self.side_name(tid, e["account_id"]), "seed": e["seed"],
                          "partner": e["partner"], "disqualified": bool(e["disqualified"]),
                          "placement": places.get(e["account_id"])} for e in self.entrants(tid)]
        t["admins"] = [{"account": a, "name": self.name_of(a)} for a in self.admins(tid)]
        t["waitlist"] = [{"account": w["account_id"], "partner": w["partner"],
                          "name": self.side_name(tid, w["account_id"])} for w in self.waitlist(tid)]
        t["rounds"] = [{"round": r, "bracket": "g" if r >= GRAND else "l" if r >= LOSERS else "w",
                        "name": round_name(r, rounds, double), "best_of": round_best_of(t, r, rounds),
                        "out_place": out_place(r, rounds, double), "phase": phase_of(r, rounds, double, cuts),
                        "phase_name": phase_name(phase_of(r, rounds, double, cuts), rounds, cuts)
                        if cuts or self.pool_count(t) else None,
                        "matches": [
            {"slot": m["slot"], "a": m["a"], "b": m["b"],
             "a_name": self.side_name(tid, m["a"]) if m["a"] else m.get("a_from"),
             "b_name": self.side_name(tid, m["b"]) if m["b"] else m.get("b_from"), "wins_a": m["wins_a"], "wins_b": m["wins_b"],
             "winner": m["winner"], "note": m["note"],
             "results": [int(x) for x in m["results"].split(",") if x]} | self.checkin_view(t, m)
            for m in ms if m["round"] == r]} for r in sorted({m["round"] for m in ms})]
        t["winner_name"] = self.side_name(tid, t["winner"]) if t["winner"] else None
        return t

    def games(self, tid: int, rnd: int, slot: int) -> dict | None:
        m = self._match(tid, rnd, slot)
        if m is None:
            return None
        side_a = set(self.members(tid, m["a"]))
        out = []
        for rid in [int(x) for x in m["results"].split(",") if x]:
            try:
                r = self.db.execute("SELECT map, duration FROM results WHERE id = ?", (rid,)).fetchone()
                rows = self.db.execute("SELECT account_id, name, legend, legend_name, outcome, kos, falls FROM result_players "
                                       "WHERE result_id = ? ORDER BY player_id", (rid,)).fetchall()
            except sqlite3.OperationalError:
                continue
            if r is None:
                continue
            sides = {"a": [], "b": []}
            won = None
            for p in rows:
                k = "a" if p["account_id"] in side_a else "b"
                sides[k].append({"name": self.name_of(p["account_id"]) if p["account_id"] else p["name"],
                                 "legend": self.ledger.legend_name(p["legend"], p["legend_name"]) if self.ledger is not None
                                 else p["legend_name"] or "", "legend_id": p["legend"] or 0, "kos": p["kos"] or 0,
                                 "falls": p["falls"] or 0})
                if p["outcome"] == "win":
                    won = k
            out.append({"result": rid, "map": r["map"] or "", "seconds": int((r["duration"] or 0) // 1000), "won": won,
                        "a": sides["a"], "b": sides["b"]})
        return {"id": tid, "round": rnd, "slot": slot, "a_name": self.side_name(tid, m["a"]) if m["a"] else None,
                "b_name": self.side_name(tid, m["b"]) if m["b"] else None, "note": m["note"], "games": out}

    def pool_view(self, tid: int, pool: int) -> dict | None:
        t = self.get(tid)
        if t is None:
            return None
        p = next((x for x in self.pool_tables(tid, t) if x["pool"] == pool), None)
        if p is None:
            return None
        for r in p["standings"]:
            r["name"] = self.side_name(tid, r["entrant"])
        p["matches"] = [{"round": m["round"], "slot": m["slot"], "pool_round": m["slot"] // POOL_SLOTS, "a": m["a"],
                         "b": m["b"], "a_name": self.side_name(tid, m["a"]), "b_name": self.side_name(tid, m["b"]),
                         "wins_a": m["wins_a"], "wins_b": m["wins_b"], "winner": m["winner"], "note": m["note"],
                         "results": [int(x) for x in m["results"].split(",") if x]} | self.checkin_view(t, m)
                        for m in p["matches"]]
        return {"id": tid, "advance": t["pool_advance"], "best_of": t["best_of"]} | p

    def my_pool_match(self, tid: int, side: int | None) -> dict | None:
        t = self.get(tid)
        if side is None or t is None or t["state"] != RUNNING or not self.pool_count(t):
            return None
        mine = [m for m in self.matches(tid) if m["round"] < 0 and m["winner"] is None and side in (m["a"], m["b"])]
        if not mine:
            return None
        ready = {(x["round"], x["slot"]) for x in self.ready() if x["tournament"] == tid}
        m = min(mine, key=lambda x: ((x["round"], x["slot"]) not in ready, x["slot"]))
        return {"round": m["round"], "slot": m["slot"], "name": pool_name(m["round"] - POOL), "a": m["a"], "b": m["b"],
                "a_name": self.side_name(tid, m["a"]), "b_name": self.side_name(tid, m["b"]), "wins_a": m["wins_a"],
                "wins_b": m["wins_b"], "best_of": t["best_of"], "ready": (m["round"], m["slot"]) in ready,
                "left": len(mine)} | self.checkin_view(t, m)

    def may_create(self, acc: dict | None) -> bool:
        if not acc:
            return False
        creators = (self.cfg.get("tournaments") or {}).get("creators", "everyone")
        return creators == "everyone" or str(acc.get("discord_id")) in {str(x) for x in creators}

    def create(self, owner: int, name: str, best_of: int = 3, max_players: int = 16, starts: float | None = None,
               about: str = "", mode: str = "1v1", checkin: int = CHECKIN_MINUTES, best_of_top8: int = 0,
               best_of_final: int = 0, min_rating: int = 0, max_rating: int = 0,
               losers: int = SINGLE, **more) -> tuple[int | None, str]:
        name = " ".join(str(name or "").split())[:MAX_NAME]
        if len(name) < 3:
            return None, "The name needs at least 3 letters."
        if best_of not in BEST_OF:
            return None, "Best of 1, 3 or 5."
        if mode not in MODES:
            return None, "1v1 or 2v2."
        if best_of_top8 not in LATER_BEST_OF or best_of_final not in LATER_BEST_OF:
            return None, "Top 8 and final: best of 3, 5 or 7 (or the same as the rest)."
        if not (0 <= min_rating <= MAX_RATING_LIMIT and 0 <= max_rating <= MAX_RATING_LIMIT) or \
                (max_rating and min_rating > max_rating):
            return None, f"Rating limits: 0 (none) to {MAX_RATING_LIMIT}, the minimum under the maximum."
        if losers not in ELIMINATION:
            return None, "Single or double elimination."
        more.setdefault("map_pick", DEFAULT_MAP_PICK)
        extra, why = self._more_settings(more)
        if why:
            return None, why
        if extra.get("map_set") and extra["map_set"] not in MAP_POOLS[mode]:
            return None, "That map pool isn't one of the mode's."
        if extra.get("pools_until") and extra["pools_until"] < extra.get("pool_advance", 2):
            return None, "Each pool can't send on more than the pools' top."
        if extra.get("scoring") in GAME_MODES and GAME_MODES[extra["scoring"]][1] != mode:
            return None, f"{GAME_MODES[extra['scoring']][0]} is a {GAME_MODES[extra['scoring']][1]} mode."
        if checkin not in CHECKIN_CHOICES:
            return None, "Check-in: " + ", ".join(str(c) for c in CHECKIN_CHOICES) + " minutes (0: none)."
        max_players = max(MIN_PLAYERS, min(MAX_PLAYERS, int(max_players)))
        with self.lock, self.db:
            cur = self.db.execute("INSERT INTO tournaments (name, owner, state, best_of, max_players, about, created, starts, "
                                  "mode, checkin, best_of_top8, best_of_final, min_rating, max_rating, losers) "
                                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                  (name, owner, OPEN, best_of, max_players, " ".join(str(about or "").split())[:MAX_ABOUT],
                                   self.clock(), starts, mode, checkin, best_of_top8, best_of_final, min_rating, max_rating,
                                   losers))
            if extra:
                self.db.execute("UPDATE tournaments SET " + ", ".join(f"{k} = ?" for k in extra) + " WHERE id = ?",
                                (*extra.values(), cur.lastrowid))
        self.log(f"tournament {cur.lastrowid} created by account {owner}: {name} ({mode}, best of {best_of}, up to "
                 f"{max_players}, {('single', 'double', 'double, no reset')[losers]} elimination, check-in {checkin} min, top 8 {best_of_top8 or best_of}, final {best_of_final or best_of})")
        return cur.lastrowid, "Created."

    def _more_settings(self, more: dict) -> tuple[dict, str | None]:
        out = {}
        for k, v in (more or {}).items():
            if v is None:
                continue
            if k in ("stocks", "minutes", "start_in") or k in RULES:
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    return {}, "Stocks, minutes, the start and the game rules are numbers."
            if k == "stocks":
                if v not in STOCKS:
                    return {}, "Stocks: " + ", ".join(map(str, STOCKS)) + "."
                out[k] = v
            elif k == "minutes":
                if v not in MINUTES:
                    return {}, "Match time: " + ", ".join(map(str, MINUTES)) + " minutes."
                out[k] = v
            elif k == "seeding":
                if v not in SEEDINGS:
                    return {}, "Seeding: by rating, random or sign-up order."
                out[k] = v
            elif k == "start_in":
                if not 0 <= v <= START_IN_DAYS * 24 * 60:
                    return {}, "Start: within a year."
                out["starts"] = self.clock() + v * 60 if v else None
            elif k in NOTES:
                out[k] = " ".join(str(v).split())[:NOTES[k]]
            elif k in ("legend_rule", "weapon_rule"):
                if v not in LIST_RULES:
                    return {}, "Legend and weapon rules: none, ban or only."
                out[k] = v
            elif k == "legends":
                items = v if isinstance(v, (list, tuple)) else [x for x in str(v).split(",") if x.strip()]
                named = hero_ids()
                try:
                    ids = sorted({int(x) if str(x).strip().isdigit() else named[str(x).strip().lower().replace(" ", "")]
                                  for x in items})
                except (TypeError, ValueError, KeyError):
                    return {}, "A legend in the list isn't known (names as in the game, e.g. Bodvar, Orion)."
                if any(i <= 2 for i in ids) or len(ids) > 200:
                    return {}, "That legend list isn't right."
                out[k] = ",".join(map(str, ids))
            elif k == "weapons":
                items = v if isinstance(v, (list, tuple)) else str(v).split(",")
                by_low = {w.lower(): w for w in WEAPONS}
                names = [by_low.get(str(x).strip().lower().replace(" ", "")) for x in items if str(x).strip()]
                if None in names:
                    return {}, "Weapons: " + ", ".join(WEAPONS) + "."
                out[k] = ",".join(w for w in WEAPONS if w in names)
            elif k == "map_set":
                try:
                    out[k] = int(v)
                except (TypeError, ValueError):
                    return {}, "That map pool isn't right."
            elif k == "map_pick":
                if v not in MAP_PICKS:
                    return {}, "Maps: tournament striking, random, turns striking or snake striking."
                out[k] = v
            elif k in ("pools_until", "pool_advance"):
                try:
                    v = int(v)
                except (TypeError, ValueError):
                    return {}, "Pools: numbers."
                if v not in (POOLS_UNTIL if k == "pools_until" else POOL_ADVANCE):
                    return {}, ("Pools until top " + ", ".join(map(str, POOLS_UNTIL[1:])) + " (or none)."
                                if k == "pools_until" else "Each pool sends 1, 2 or 4 on.")
                out[k] = v
            elif k == "pool_kind":
                if v not in POOL_KINDS:
                    return {}, "Pools: round robin or bracket."
                out[k] = v
            elif k == "phases":
                items = v if isinstance(v, (list, tuple)) else str(v).split(",")
                try:
                    cuts = sorted({int(x) for x in items if str(x).strip()}, reverse=True)
                except (TypeError, ValueError):
                    return {}, "Phases: top " + ", ".join(map(str, PHASE_CUTS)) + "."
                if any(c not in PHASE_CUTS for c in cuts):
                    return {}, "Phases: top " + ", ".join(map(str, PHASE_CUTS)) + "."
                out[k] = ",".join(map(str, cuts))
            elif k in RULES:
                allowed = {"scoring": ALL_SCORINGS, "variation": VARIATIONS, "weapon_rate": WEAPON_RATES,
                           "gadget_rate": GADGET_RATES, "team_damage": (0, 1)}.get(k)
                if k == "damage":
                    if not (DAMAGE_RANGE[0] <= v <= DAMAGE_RANGE[1] and v % 10 == 0):
                        return {}, "Damage: 50 to 300 %, in steps of 10."
                elif v not in allowed:
                    return {}, f"That {k.replace('_', ' ')} isn't one of the game's."
                out[k] = v
        return out, None

    def join(self, tid: int, account_id: int, partner: int | None = None) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if t is None:
                return False, "No such tournament."
            if t["state"] != OPEN:
                return False, "Sign-up is closed."
            ents = self.entrants(tid)
            every = ents + self.waitlist(tid)
            have = {e["account_id"] for e in every} | {e["partner"] for e in every if e["partner"]}
            if account_id in have:
                return False, "You're signed up already."
            full = len(ents) >= t["max_players"]
            if t["mode"] == "2v2":
                if partner is None or partner == account_id:
                    return False, "A 2v2 needs a teammate: invite them to your party, then sign up."
                if partner in have:
                    return False, f"{self.name_of(partner)} is signed up already."
            else:
                partner = None
            refused = self.rating_refusal(t, [account_id] + ([partner] if partner else []))
            if refused:
                return False, refused
            with self.db:
                self.db.execute("INSERT INTO tournament_entrants (tournament, account_id, joined, partner, waiting) "
                                "VALUES (?, ?, ?, ?, ?)", (tid, account_id, self.clock(), partner, int(full)))
        self.log(f"tournament {tid}: account {account_id} signed up" + (f" with {partner}" if partner else "")
                 + (" (waitlist)" if full else ""))
        if full:
            return True, f"It's full: you're number {len(self.waitlist(tid))} on the waitlist."
        return True, "Signed up." if partner is None else f"Signed up with {self.name_of(partner)}."

    def team_invite(self, tid: int, inviter: int, name: str = "", invitee: int | None = None) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if t is None:
                return False, "No such tournament."
            if t["mode"] != "2v2":
                return False, "Teams are only for 2v2 tournaments."
            if t["state"] != OPEN:
                return False, "Sign-up is closed."
            if invitee is None:
                invitee = self.account_by_name(name)
            if invitee is None:
                return False, f"Nobody here is called \"{name}\"."
            if invitee == inviter:
                return False, "Invite someone else."
            every = self.entrants(tid) + self.waitlist(tid)
            have = {e["account_id"] for e in every} | {e["partner"] for e in every if e["partner"]}
            if inviter in have:
                return False, "You're signed up already."
            if invitee in have:
                return False, f"{self.name_of(invitee)} is in a team already."
            with self.db:
                self.db.execute("INSERT OR REPLACE INTO tournament_team_invites (tournament, inviter, invitee, created) "
                                "VALUES (?, ?, ?, ?)", (tid, inviter, invitee, self.clock()))
        self.log(f"tournament {tid}: account {inviter} invited {invitee} to a team")
        for sess in self._sessions_of(invitee):
            self._offer_team(sess, t, inviter)
        return True, f"Invited {self.name_of(invitee)}. They'll see it in their notifications."

    def team_invites_for(self, account_id: int) -> list[dict]:
        rows = self.db.execute("SELECT i.tournament, i.inviter FROM tournament_team_invites i JOIN tournaments t "
                               "ON t.id = i.tournament WHERE i.invitee = ? AND t.state = ?", (account_id, OPEN))
        return [{"tournament": r[0], "inviter": r[1]} for r in rows]

    def _offer_team(self, sess, t: dict, inviter: int) -> None:
        self.social.offer(sess, "team", f"{self.name_of(inviter)} (2v2 team: {t['name']})", f"team:{t['id']}:{inviter}")

    def offer_team_invites(self, sess) -> None:
        acc = getattr(sess, "account", None)
        if not acc:
            return
        for inv in self.team_invites_for(acc["id"]):
            t = self.get(inv["tournament"])
            if t is not None:
                self._offer_team(sess, t, inv["inviter"])

    def team_answer(self, tid: int, inviter: int, invitee: int, accept: bool) -> tuple[bool, str]:
        with self.lock:
            row = self.db.execute("SELECT 1 FROM tournament_team_invites WHERE tournament = ? AND inviter = ? AND "
                                  "invitee = ?", (tid, inviter, invitee)).fetchone()
            if row is None:
                return False, "That invite isn't there any more."
            with self.db:
                self.db.execute("DELETE FROM tournament_team_invites WHERE tournament = ? AND inviter = ? AND invitee = ?",
                                (tid, inviter, invitee))
        if self.social is not None:
            self.social.withdraw("team", f"team:{tid}:{inviter}")
        t = self.get(tid)
        name = t["name"] if t else "the tournament"
        if not accept:
            self._toast([inviter], f"{self.name_of(invitee)} declined your team invite")
            return True, "Declined."
        ok, msg = self.join(tid, inviter, partner=invitee)
        if ok:
            with self.lock, self.db:
                self.db.execute("DELETE FROM tournament_team_invites WHERE tournament = ? AND (inviter IN (?, ?) OR "
                                "invitee IN (?, ?))", (tid, inviter, invitee, inviter, invitee))
            self._toast([inviter, invitee], f"{name}: {self.name_of(inviter)} & {self.name_of(invitee)} signed up")
        else:
            self._toast([inviter, invitee], f"{name}: {msg}")
        return ok, msg

    def _team_answered(self, sess, inv, accepted: bool) -> None:
        try:
            _, tid, inviter = inv.src_key.split(":")
            acc = sess.account["id"] if getattr(sess, "account", None) else None
        except (ValueError, KeyError, TypeError):
            return
        if acc is not None:
            ok, msg = self.team_answer(int(tid), int(inviter), acc, accepted)
            sess.log(f"team invite {inv.src_key} {'accepted' if accepted else 'declined'}: {msg}")

    def rating_refusal(self, t: dict, accounts: list) -> str | None:
        low, high = t.get("min_rating") or 0, t.get("max_rating") or 0
        if not low and not high:
            return None
        ladder = "2v2" if t["mode"] == "2v2" else "1v1"
        for acc in accounts:
            r = self.rating_of(acc, ladder)
            who = "Your" if acc == accounts[0] else f"{self.name_of(acc)}'s"
            if low and r < low:
                return f"{who} {ladder} rating is {r}: this one needs {low} or more."
            if high and r > high:
                return f"{who} {ladder} rating is {r}: this one is for {high} or less."
        return None

    def withdraw(self, tid: int, account_id: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if t is None or t["state"] != OPEN:
                return False, "You can only leave before it starts."
            with self.db:
                cur = self.db.execute("DELETE FROM tournament_entrants WHERE tournament = ? AND (account_id = ? OR "
                                      "partner = ?)", (tid, account_id, account_id))
            told = self._promote(tid) if cur.rowcount else []
        if told:
            self._toast(told, f"{t['name']}: a place opened, you're in")
        return (True, "You left it.") if cur.rowcount else (False, "You weren't signed up.")

    def cancel(self, tid: int, account_id: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if t is None or t["owner"] != account_id:
                return False, "Only its creator can cancel it."
            if t["state"] in (DONE, CANCELLED):
                return False, "It's over already."
            with self.db:
                self.db.execute("UPDATE tournaments SET state = ?, finished = ? WHERE id = ?", (CANCELLED, self.clock(), tid))
        self.log(f"tournament {tid} cancelled")
        return True, "Cancelled."

    def admins(self, tid: int) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT account_id FROM tournament_admins WHERE tournament = ? "
                                              "ORDER BY rowid", (tid,))]

    def account_by_name(self, name: str) -> int | None:
        name = " ".join(str(name or "").split())
        if not name:
            return None
        try:
            row = self.db.execute("SELECT account_id FROM players WHERE lower(name) = lower(?) AND account_id IS NOT NULL "
                                  "ORDER BY id DESC LIMIT 1", (name,)).fetchone()
        except sqlite3.OperationalError:
            row = None
        if row is None:
            row = self.db.execute("SELECT id FROM accounts WHERE lower(name) = lower(?) ORDER BY last_login DESC LIMIT 1",
                                  (name,)).fetchone()
        return row[0] if row else None

    def set_admin(self, tid: int, by: int, name: str = "", account: int | None = None,
                  add: bool = True) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if t is None or t["owner"] != by:
                return False, "Only its creator picks the admins."
            if t["state"] in (DONE, CANCELLED):
                return False, "It's over."
            acc = account if account is not None else self.account_by_name(name)
            if acc is None:
                return False, f"Nobody here is called {name}."
            have = self.admins(tid)
            with self.db:
                if not add:
                    if acc not in have:
                        return False, f"{self.name_of(acc)} isn't an admin."
                    self.db.execute("DELETE FROM tournament_admins WHERE tournament = ? AND account_id = ?", (tid, acc))
                else:
                    if acc == t["owner"] or acc in have:
                        return False, f"{self.name_of(acc)} runs it already."
                    if len(have) >= MAX_ADMINS:
                        return False, f"Up to {MAX_ADMINS} admins."
                    self.db.execute("INSERT INTO tournament_admins (tournament, account_id) VALUES (?, ?)", (tid, acc))
        self.log(f"tournament {tid}: account {acc} {'made' if add else 'no longer'} an admin by {by}")
        if add:
            self._toast([acc], f"{t['name']}: you're an admin now")
        return True, f"{self.name_of(acc)} is {'an admin now' if add else 'no longer an admin'}."

    def pause(self, tid: int, by: int, on: bool = True) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can pause it."
            if t["state"] != RUNNING:
                return False, "Only a running tournament can be paused."
            if bool(t["paused"]) == on:
                return False, "It's paused already." if on else "It isn't paused."
            now = self.clock()
            with self.db:
                if on:
                    self.db.execute("UPDATE tournaments SET paused = ? WHERE id = ?", (now, tid))
                else:
                    self.db.execute("UPDATE tournament_matches SET deadline = deadline + ? WHERE tournament = ? AND "
                                    "deadline IS NOT NULL AND winner IS NULL", (now - t["paused"], tid))
                    self.db.execute("UPDATE tournaments SET paused = NULL WHERE id = ?", (tid,))
            everyone = [a for e in self.entrants(tid) for a in self.members(tid, e["account_id"])]
        self.log(f"tournament {tid} {'paused' if on else 'resumed'} by account {by}")
        self._toast(everyone, f"{t['name']} is {'paused' if on else 'back on'}")
        return True, "Paused." if on else "Resumed."

    def downstream(self, tid: int, rnd: int, slot: int) -> list[tuple]:
        t = self.get(tid)
        rounds = self._wrounds(tid)
        if rnd < 0:
            nxt = self._pool_next(tid, rnd, slot)
            return [nxt] if nxt else []
        if not t.get("losers"):
            return [] if rnd >= rounds - 1 else [(rnd + 1, slot // 2, "a" if slot % 2 == 0 else "b")]
        out = []
        for m in self.matches(tid):
            if (m["round"], rnd) == (GRAND_RESET, GRAND):
                out += [(GRAND_RESET, 0, "a"), (GRAND_RESET, 0, "b")]
                continue
            for side, src in zip("ab", feeds(m["round"], m["slot"], rounds) or ()):
                if src[:2] == (rnd, slot):
                    out.append((m["round"], m["slot"], side))
        cuts = phase_cuts(t.get("phases"))
        for keys in (phase_entries(rounds, cuts) if cuts else {}).values():
            if any((r, sl) in keys for r, sl, _ in out):
                out = [x for x in out if (x[0], x[1]) not in keys] + [(r, sl, side) for r, sl in keys for side in "ab"]
        return out

    def _undo_plan(self, tid: int, rnd: int, slot: int, plan: list) -> bool:
        for r, sl, side in self.downstream(tid, rnd, slot):
            d = self._match(tid, r, sl)
            if d is None:
                continue
            if d["wins_a"] or d["wins_b"] or (d["winner"] is not None and d["note"] != "bye"):
                return False
            plan.append((r, sl, side, d["note"] == "bye"))
            if d["note"] == "bye" and not self._undo_plan(tid, r, sl, plan):
                return False
        return True

    def undo(self, tid: int, rnd: int, slot: int, by: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can undo a result."
            m = self._match(tid, rnd, slot)
            if t["state"] not in (RUNNING, DONE) or m is None or m["winner"] is None or m["note"] in ("bye", NOT_PLAYED):
                return False, "That match has no result to undo."
            down = []
            if rnd < 0 and any(x["a"] is not None or x["b"] is not None for x in self.matches(tid) if x["round"] == 0):
                return False, "The pools are over: their results can't change now."
            if not self._undo_plan(tid, rnd, slot, down):
                return False, "A later match has started: undo that one first."
            with self.db:
                for r, sl, side, bye in down:
                    self.db.execute(f"UPDATE tournament_matches SET {side} = NULL, note = '', ready_at = NULL, "
                                    "deadline = NULL, checked = ''" + (", winner = NULL" if bye else "") +
                                    " WHERE tournament = ? AND round = ? AND slot = ?", (tid, r, sl))
                self.db.execute("UPDATE tournament_matches SET winner = NULL, note = '', wins_a = 0, wins_b = 0, "
                                "results = '', ready_at = NULL, deadline = NULL, checked = '' WHERE tournament = ? AND "
                                "round = ? AND slot = ?", (tid, rnd, slot))
                if t["state"] == DONE:
                    self.db.execute("UPDATE tournaments SET state = ?, finished = NULL, winner = NULL WHERE id = ?",
                                    (RUNNING, tid))
            everyone = self.members(tid, m["a"]) + self.members(tid, m["b"])
        self.log(f"tournament {tid}: round {rnd} match {slot} result undone by account {by}")
        self._toast(everyone, f"{t['name']}: your match's result was taken back")
        return True, "Result undone: the match is open again."

    def disqualify(self, tid: int, by: int, entrant: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can disqualify."
            entrant = self.entrant_of(tid, entrant)
            if entrant is None:
                return False, "They aren't in it."
            name = self.side_name(tid, entrant)
            if t["state"] == OPEN:
                with self.db:
                    self.db.execute("DELETE FROM tournament_entrants WHERE tournament = ? AND account_id = ?", (tid, entrant))
                self.log(f"tournament {tid}: {entrant} taken off the list by {by}")
                return True, f"{name} is off the list."
            if t["state"] != RUNNING:
                return False, "It's over."
            with self.db:
                self.db.execute("UPDATE tournament_entrants SET disqualified = 1 WHERE tournament = ? AND account_id = ?",
                                (tid, entrant))
            self.settle_disqualified()
        self.log(f"tournament {tid}: {entrant} disqualified by account {by}")
        self._toast(self.members(tid, entrant), f"{t['name']}: you were disqualified")
        return True, f"{name} is disqualified."

    def start_due(self) -> int:
        n = 0
        for t in [dict(r) for r in self.db.execute("SELECT * FROM tournaments WHERE state = ? AND starts IS NOT NULL "
                                                  "AND starts <= ?", (OPEN, self.clock()))]:
            ok, msg = self.start(t["id"], t["owner"])
            if ok:
                n += 1
                everyone = [a for e in self.entrants(t["id"]) for a in self.members(t["id"], e["account_id"])]
                self._toast(everyone, f"{t['name']} has started")
            else:
                with self.db:
                    self.db.execute("UPDATE tournaments SET starts = NULL WHERE id = ?", (t["id"],))
                self.log(f"tournament {t['id']}: not started at its time ({msg})")
                self._toast([t["owner"]], f"{t['name']} didn't start: {msg}")
        return n

    def settle_disqualified(self) -> int:
        n = 0
        while True:
            dq = {(r[0], r[1]) for r in self.db.execute(
                "SELECT tournament, account_id FROM tournament_entrants WHERE disqualified = 1")}
            if not dq:
                return n
            m = next((x for x in self.ready() if (x["tournament"], x["a"]) in dq or (x["tournament"], x["b"]) in dq), None)
            if m is None:
                return n
            w = m["b"] if (m["tournament"], m["a"]) in dq else m["a"]
            self._decide(m["tournament"], m["round"], m["slot"], w, DISQUALIFIED)
            n += 1

    def settings(self, tid: int, by: int, fields: dict) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can change it."
            if t["state"] not in (OPEN, RUNNING):
                return False, "It's over."
            later = {"checkin", "best_of", "best_of_top8", "best_of_final", "about", "stocks", "minutes", *RULES} | set(NOTES)
            before = {"name", "max_players", "min_rating", "max_rating", "losers", "seeding", "start_in", "legend_rule",
                      "legends", "weapon_rule", "weapons", "map_set", "map_pick", "pools_until", "pool_advance",
                      "pool_kind", "phases"}
            new = {}
            for k, v in (fields or {}).items():
                if k not in later | before:
                    continue
                if k in before and t["state"] != OPEN:
                    return False, "The name, limits, rating, elimination and rules can only change before it starts."
                new[k] = v if k in ("legends", "weapons", "phases") else str(v) if k in (
                    "name", "about", "seeding", "legend_rule", "weapon_rule", "map_pick", "pool_kind") or k in NOTES \
                    else int(v)
            if not new:
                return False, "Nothing to change."
            more, why = self._more_settings({k: new.pop(k) for k in list(new) if k in MORE})
            if why:
                return False, why
            if more.get("map_set") and more["map_set"] not in MAP_POOLS[t["mode"]]:
                return False, "That map pool isn't one of the mode's."
            new.update(more)
            merged = {**t, **new}
            if merged.get("scoring") in GAME_MODES and GAME_MODES[merged["scoring"]][1] != merged["mode"]:
                return False, f"{GAME_MODES[merged['scoring']][0]} is a {GAME_MODES[merged['scoring']][1]} mode."
            name = " ".join(merged["name"].split())[:MAX_NAME]
            if len(name) < 3:
                return False, "The name needs at least 3 letters."
            if merged["best_of"] not in BEST_OF:
                return False, "Best of 1, 3 or 5."
            if merged["best_of_top8"] not in LATER_BEST_OF or merged["best_of_final"] not in LATER_BEST_OF:
                return False, "Top 8 and final: best of 3, 5 or 7 (or the same as the rest)."
            if not (0 <= merged["min_rating"] <= MAX_RATING_LIMIT and 0 <= merged["max_rating"] <= MAX_RATING_LIMIT) or \
                    (merged["max_rating"] and merged["min_rating"] > merged["max_rating"]):
                return False, f"Rating limits: 0 (none) to {MAX_RATING_LIMIT}, the minimum under the maximum."
            if merged["checkin"] not in CHECKIN_CHOICES:
                return False, "Check-in: " + ", ".join(str(c) for c in CHECKIN_CHOICES) + " minutes (0: none)."
            if merged["losers"] not in ELIMINATION:
                return False, "Single or double elimination."
            if "max_players" in new:
                new["max_players"] = max(MIN_PLAYERS, len(self.entrants(tid)), min(MAX_PLAYERS, new["max_players"]))
            if "name" in new:
                new["name"] = name
            if "about" in new:
                new["about"] = " ".join(new["about"].split())[:MAX_ABOUT]
            with self.db:
                self.db.execute("UPDATE tournaments SET " + ", ".join(f"{k} = ?" for k in new) + " WHERE id = ?",
                                (*new.values(), tid))
            told = self._promote(tid) if "max_players" in new else []
        if told:
            self._toast(told, f"{t['name']}: a place opened, you're in")
        self.log(f"tournament {tid}: settings {new} by account {by}")
        return True, "Saved."

    def set_seed(self, tid: int, by: int, entrant: int, seed: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can seed it."
            if t["state"] != OPEN:
                return False, "Seeds are set before it starts."
            if t["seeding"] != "manual":
                return False, "Set its seeding to manual first."
            ents = self.entrants(tid)
            ents.sort(key=lambda e: (e["seed"] is None, e["seed"] or 0, e["joined"], e["account_id"]))
            moved = next((e for e in ents if e["account_id"] == entrant), None)
            if moved is None:
                return False, "They aren't signed up."
            ents.remove(moved)
            seed = max(1, min(len(ents) + 1, int(seed)))
            ents.insert(seed - 1, moved)
            with self.db:
                for i, e in enumerate(ents):
                    self.db.execute("UPDATE tournament_entrants SET seed = ? WHERE tournament = ? AND account_id = ?",
                                    (i + 1, tid, e["account_id"]))
        self.log(f"tournament {tid}: {self.side_name(tid, entrant)} seeded {seed} by account {by}")
        return True, f"{self.side_name(tid, entrant)} is seed {seed}."

    def _seed_order(self, tid: int, t: dict, rule: str | None = None) -> list[dict]:
        ents = self.entrants(tid)
        rule = rule or t["seeding"]
        if rule == "manual":
            ents.sort(key=lambda e: (e["seed"] is None, e["seed"] or 0, e["joined"], e["account_id"]))
        elif rule in ("signup", "random"):
            ents.sort(key=lambda e: (e["joined"], e["account_id"]))
        else:
            ents.sort(key=lambda e: (-self.seed_rating(tid, t["mode"], e["account_id"]), e["joined"], e["account_id"]))
        return ents

    def _write_seeds(self, tid: int, ents: list[dict]) -> None:
        with self.db:
            for i, e in enumerate(ents):
                self.db.execute("UPDATE tournament_entrants SET seed = ? WHERE tournament = ? AND account_id = ?",
                                (i + 1, tid, e["account_id"]))
            self.db.execute("UPDATE tournaments SET seeding = 'manual' WHERE id = ?", (tid,))

    def reseed(self, tid: int, by: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can seed it."
            if t["state"] != OPEN:
                return False, "Seeds are set before it starts."
            self._write_seeds(tid, self._seed_order(tid, t, "rating"))
        self.log(f"tournament {tid}: reseeded by rating by account {by}")
        return True, "Seeded by rating."

    def swap_seeds(self, tid: int, by: int, a: int, b: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can seed it."
            if t["state"] != OPEN:
                return False, "Seeds are set before it starts."
            ents = self._seed_order(tid, t)
            ids = [e["account_id"] for e in ents]
            if a not in ids or b not in ids or a == b:
                return False, "Pick two players who are signed up."
            i, j = ids.index(a), ids.index(b)
            ents[i], ents[j] = ents[j], ents[i]
            self._write_seeds(tid, ents)
        self.log(f"tournament {tid}: seeds {i + 1} and {j + 1} swapped by account {by}")
        return True, f"{self.side_name(tid, b)} is seed {i + 1}, {self.side_name(tid, a)} seed {j + 1}."

    def start(self, tid: int, account_id: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, account_id):
                return False, "Only its creator and admins can start it."
            if t["state"] != OPEN:
                return False, "It has started already."
            ents = self.entrants(tid)
            if len(ents) < MIN_PLAYERS:
                return False, f"It needs at least {MIN_PLAYERS} players."
            if t["seeding"] == "random":
                self.random.shuffle(ents)
            elif t["seeding"] == "signup":
                ents.sort(key=lambda e: (e["joined"], e["account_id"]))
            elif t["seeding"] == "manual":
                ents.sort(key=lambda e: (e["seed"] is None, e["seed"] or 0, e["joined"], e["account_id"]))
            else:
                ents.sort(key=lambda e: (-self.seed_rating(tid, t["mode"], e["account_id"]), e["joined"], e["account_id"]))
            pools = self.pool_count(t)
            if t.get("pools_until") and not pools:
                return False, "Each pool can't send on more than the pools' top."
            if pools:
                if len(ents) <= t["pools_until"]:
                    return False, f"Pools until top {t['pools_until']} need more than {t['pools_until']} players."
                if len(ents) < 2 * pools or pools > MAX_POOLS:
                    return False, f"{pools} pools need at least {2 * pools} players."
                if -(-len(ents) // pools) > MAX_POOL_SIZE:
                    return False, (f"{pools} pools would hold more than {MAX_POOL_SIZE} players each: run the pools "
                                   "until a higher top, or send fewer on from each.")
                if t.get("pool_kind") == "bracket" and len(ents) // pools <= t["pool_advance"]:
                    return False, (f"Bracket pools need more than {t['pool_advance']} players each: "
                                   f"at least {pools * (t['pool_advance'] + 1)} players.")
            size = bracket_size(t["pools_until"] if pools else len(ents))
            by_seed = {} if pools else {i + 1: e["account_id"] for i, e in enumerate(ents)}
            order = bracket_order(size)
            rounds = size.bit_length() - 1
            with self.db:
                for i, e in enumerate(ents):
                    self.db.execute("UPDATE tournament_entrants SET seed = ? WHERE tournament = ? AND account_id = ?",
                                    (i + 1, tid, e["account_id"]))
                every = list(range(rounds))
                if t.get("losers"):
                    every += [LOSERS + j for j in range(losers_rounds(rounds))] + [GRAND]
                    every += [GRAND_RESET] if t["losers"] == DOUBLE else []
                for rnd in every:
                    for slot in range(round_size(rnd, rounds)):
                        a = b = None
                        if rnd == 0:
                            a, b = by_seed.get(order[2 * slot]), by_seed.get(order[2 * slot + 1])
                        self.db.execute("INSERT INTO tournament_matches (tournament, round, slot, a, b) VALUES (?, ?, ?, ?, ?)",
                                        (tid, rnd, slot, a, b))
                for p, sides in enumerate(self._pool_sides(tid, [e["account_id"] for e in ents], pools)):
                    if t.get("pool_kind") == "bracket":
                        size = bracket_size(len(sides))
                        seat = [sides[s - 1] if s <= len(sides) else None for s in bracket_order(size)]
                        for pr in range(pool_bracket_rounds(len(sides), t["pool_advance"])):
                            for i in range(size >> (pr + 1)):
                                a, b = (seat[2 * i], seat[2 * i + 1]) if pr == 0 else (None, None)
                                self.db.execute("INSERT INTO tournament_matches (tournament, round, slot, a, b) VALUES "
                                                "(?, ?, ?, ?, ?)", (tid, POOL + p, pr * POOL_SLOTS + i, a, b))
                        continue
                    for pr, pairs in enumerate(robin_rounds(len(sides))):
                        for i, (x, y) in enumerate(pairs):
                            self.db.execute("INSERT INTO tournament_matches (tournament, round, slot, a, b) VALUES "
                                            "(?, ?, ?, ?, ?)", (tid, POOL + p, pr * POOL_SLOTS + i, sides[x], sides[y]))
                self.db.execute("UPDATE tournaments SET state = ?, started = ? WHERE id = ?", (RUNNING, self.clock(), tid))
                self.db.execute("DELETE FROM tournament_entrants WHERE tournament = ? AND waiting = 1", (tid,))
            if not pools:
                self._byes(tid, size)
            else:
                for m in self.matches(tid):
                    if m["round"] < 0 and m["slot"] < POOL_SLOTS and (m["a"] is None) != (m["b"] is None):
                        self._decide(tid, m["round"], m["slot"], m["a"] or m["b"], "bye")
        self.log(f"tournament {tid} started: {len(ents)} players, bracket of {size}"
                 + (" (double elimination)" if t.get("losers") else "")
                 + (f" after {pools} round robin pools until top {t['pools_until']}" if pools else ""))
        return True, "Started."

    def _byes(self, tid: int, size: int) -> None:
        for slot in range(size >> 1):
            m = self._match(tid, 0, slot)
            if (m["a"] is None) != (m["b"] is None):
                self._decide(tid, 0, slot, m["a"] or m["b"], "bye")

    @staticmethod
    def _pool_sides(tid: int, seeded: list[int], pools: int) -> list[list[int]]:
        out = [[] for _ in range(pools)]
        for e, p in zip(seeded, snake_pools(len(seeded), pools) if pools else []):
            out[p].append(e)
        return out

    def _pool_next(self, tid: int, rnd: int, slot: int) -> tuple | None:
        pr, i = divmod(slot, POOL_SLOTS)
        nxt = (pr + 1) * POOL_SLOTS + i // 2
        if self.get(tid).get("pool_kind") != "bracket" or self._match(tid, rnd, nxt) is None:
            return None
        return rnd, nxt, "a" if i % 2 == 0 else "b"

    def pool_count(self, t: dict) -> int:
        return t["pools_until"] // t["pool_advance"] if t.get("pools_until") else 0

    def pool_tables(self, tid: int, t: dict | None = None, ms: list[dict] | None = None) -> list[dict]:
        t = t or self.get(tid)
        pools = self.pool_count(t)
        if not pools or t["state"] == OPEN:
            return []
        ms = self.matches(tid) if ms is None else ms
        seeded = [e["account_id"] for e in sorted(self.entrants(tid), key=lambda e: (e["seed"] or 1e9, e["account_id"]))]
        out = []
        for p, sides in enumerate(self._pool_sides(tid, seeded, pools)):
            pm = [m for m in ms if m["round"] == POOL + p]
            done = all(m["winner"] is not None or m["note"] == NOT_PLAYED for m in pm)
            rows = (pool_bracket_table if t.get("pool_kind") == "bracket" else pool_table)(sides, pm)
            for place, r in enumerate(rows, 1):
                r["place"] = place
                r["advances"] = done and place <= t["pool_advance"]
            out.append({"pool": p, "round": POOL + p, "name": pool_name(p), "done": done, "standings": rows,
                        "matches": pm})
        return out

    def _pools_over(self, tid: int) -> None:
        t = self.get(tid)
        tables = self.pool_tables(tid, t)
        if not tables or not all(x["done"] for x in tables):
            return
        if any(m["a"] is not None or m["b"] is not None for m in self.matches(tid) if m["round"] == 0):
            return
        seeds = []
        for place in range(t["pool_advance"]):
            seeds += [x["standings"][place]["entrant"] for x in tables if place < len(x["standings"])]
        size = 1 << self._wrounds(tid)
        order = bracket_order(size)
        by_seed = {i + 1: e for i, e in enumerate(seeds)}
        with self.db:
            for slot in range(size >> 1):
                self.db.execute("UPDATE tournament_matches SET a = ?, b = ? WHERE tournament = ? AND round = 0 AND slot = ?",
                                (by_seed.get(order[2 * slot]), by_seed.get(order[2 * slot + 1]), tid, slot))
        self.log(f"tournament {tid}: pools done, {len(seeds)} go on to the bracket")
        self._toast([a for e in seeds for a in self.members(tid, e)], f"{t['name']}: you're through to the bracket")
        self._byes(tid, size)

    def _match(self, tid: int, rnd: int, slot: int) -> dict | None:
        row = self.db.execute("SELECT * FROM tournament_matches WHERE tournament = ? AND round = ? AND slot = ?",
                              (tid, rnd, slot)).fetchone()
        return dict(row) if row else None

    def _decide(self, tid: int, rnd: int, slot: int, winner: int, note: str = "") -> None:
        with self.db:
            self.db.execute("UPDATE tournament_matches SET winner = ?, note = ? WHERE tournament = ? AND round = ? AND slot = ?",
                            (winner, note, tid, rnd, slot))
            m = self._match(tid, rnd, slot)
            if note in SCORED_NOTES and m is not None and winner in (m["a"], m["b"]):
                need = wins_needed(round_best_of(self.get(tid), rnd, self._wrounds(tid)))
                side = "wins_a" if winner == m["a"] else "wins_b"
                self.db.execute(f"UPDATE tournament_matches SET {side} = ? WHERE tournament = ? AND round = ? AND slot = ?",
                                (max(need, m[side]), tid, rnd, slot))
        if rnd < 0:
            nxt = self._pool_next(tid, rnd, slot)
            if nxt is not None:
                with self.db:
                    self.db.execute(f"UPDATE tournament_matches SET {nxt[2]} = ? WHERE tournament = ? AND round = ? "
                                    "AND slot = ?", (winner, tid, nxt[0], nxt[1]))
            self._pools_over(tid)
            return
        t = self.get(tid)
        if t.get("losers"):
            self._advance(tid)
            return
        with self.db:
            nxt = self._match(tid, rnd + 1, slot // 2)
            if nxt is None:
                self.db.execute("UPDATE tournaments SET state = ?, finished = ?, winner = ? WHERE id = ?",
                                (DONE, self.clock(), winner, tid))
            else:
                self.db.execute(f"UPDATE tournament_matches SET {'a' if slot % 2 == 0 else 'b'} = ? "
                                "WHERE tournament = ? AND round = ? AND slot = ?", (winner, tid, rnd + 1, slot // 2))
        if nxt is None:
            self.log(f"tournament {tid}: account {winner} wins it")

    def _advance(self, tid: int) -> None:
        rounds = self._wrounds(tid)
        ms = {(m["round"], m["slot"]): m for m in self.matches(tid) if m["round"] >= 0}
        cuts = phase_cuts(self.get(tid).get("phases"))
        entries = phase_entries(rounds, cuts) if cuts else {}
        entry_keys = {key for keys in entries.values() for key in keys}

        def settled(m) -> bool:
            return m["winner"] is not None or m["note"] == NOT_PLAYED

        def save(key, **cols) -> None:
            ms[key].update(cols)
            self.db.execute("UPDATE tournament_matches SET " + ", ".join(f"{c} = ?" for c in cols) +
                            " WHERE tournament = ? AND round = ? AND slot = ?", (*cols.values(), tid, *key))

        def survivor(key) -> int | None:
            r, sl, kind = key
            f = ms[(r, sl)]
            return f["winner"] if kind == "w" else (
                None if f["winner"] is None else f["b"] if f["winner"] == f["a"] else f["a"])

        def next_phase() -> bool:
            for k in sorted(entries):
                keys = entries[k]
                if any(ms[key]["a"] is not None or ms[key]["b"] is not None or settled(ms[key]) for key in keys):
                    continue
                if any(not settled(m) for key, m in ms.items()
                       if key[0] < GRAND and phase_of(key[0], rounds, True, cuts) < k):
                    return False
                seeds = bracket_seeds([m for key, m in ms.items() if key[0] == 0], rounds)
                for bracket in ({key for key in keys if key[0] < LOSERS}, {key for key in keys if key[0] >= LOSERS}):
                    if not bracket:
                        continue
                    rnd = min(key[0] for key in bracket)
                    came = [survivor(src) for key in sorted(bracket) for src in feeds(*key, rounds)]
                    for sl, (a, b) in reseed([s for s in came if s is not None], [key[1] for key in bracket],
                                             seeds).items():
                        save((rnd, sl), a=a, b=b)
                        if a is None or b is None:
                            lone = a if a is not None else b
                            save((rnd, sl), winner=lone, note="bye" if lone is not None else NOT_PLAYED)
                self.log(f"tournament {tid}: phase {k} seeded")
                return True
            return False

        changed = True
        with self.db:
            while changed:
                changed = False
                for key in sorted(ms):
                    m = ms[key]
                    if settled(m) or key in entry_keys:
                        continue
                    if key[0] == GRAND_RESET:
                        g = ms[(GRAND, 0)]
                        if g["winner"] is None:
                            continue
                        if g["winner"] == g["a"]:
                            save(key, note=NOT_PLAYED)
                            changed = True
                        elif m["a"] is None:
                            save(key, a=g["a"], b=g["b"])
                            changed = True
                        continue
                    src = feeds(key[0], key[1], rounds)
                    if src is None:
                        continue
                    known = 0
                    for side, (r, sl, kind) in zip("ab", src):
                        f = ms.get((r, sl))
                        if f is None or not settled(f):
                            continue
                        known += 1
                        got = survivor((r, sl, kind))
                        if m[side] != got:
                            save(key, **{side: got})
                            changed = True
                    if known < 2 or (m["a"] is not None and m["b"] is not None):
                        continue
                    lone = m["a"] if m["a"] is not None else m["b"]
                    save(key, winner=lone, note="bye" if lone is not None else NOT_PLAYED)
                    changed = True
                if not changed and entries:
                    changed = next_phase()
            last = ms.get((GRAND_RESET, 0))
            if last is None or last["note"] == NOT_PLAYED:
                last = ms.get((GRAND, 0))
            winner = last["winner"] if last is not None else None
            if winner is not None:
                self.db.execute("UPDATE tournaments SET state = ?, finished = ?, winner = ? WHERE id = ?",
                                (DONE, self.clock(), winner, tid))
        if winner is not None:
            self.log(f"tournament {tid}: account {winner} wins it")

    def award(self, tid: int, rnd: int, slot: int, account_id: int, winner: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, account_id):
                return False, "Only its creator and admins can award a match."
            m = self._match(tid, rnd, slot)
            if t["state"] != RUNNING or m is None or m["winner"] is not None or winner not in (m["a"], m["b"]) \
                    or None in (m["a"], m["b"]):
                return False, "That match can't be awarded now."
            self._decide(tid, rnd, slot, winner, AWARDED)
        self.log(f"tournament {tid}: round {rnd} match {slot} awarded to account {winner}")
        self._tell(self.members(tid, m["a"]) + self.members(tid, m["b"]),
                   f"{self.get(tid)['name']}: {self.side_name(tid, winner)} was awarded the match.")
        return True, "Awarded."

    def report(self, tid: int, rnd: int, slot: int, by: int, wins_a: int, wins_b: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can report a score."
            m = self._match(tid, rnd, slot)
            if t["state"] not in (RUNNING, DONE) or m is None or None in (m["a"], m["b"]) or \
                    m["note"] in ("bye", NOT_PLAYED):
                return False, "That match can't take a score now."
            need = wins_needed(round_best_of(t, rnd, self._wrounds(tid)))
            if not (0 <= wins_a <= need and 0 <= wins_b <= need) or max(wins_a, wins_b) != need or wins_a == wins_b:
                return False, f"One side needs {need} (best of {2 * need - 1}), the other fewer."
            if m["winner"] is not None:
                ok, msg = self.undo(tid, rnd, slot, by)
                if not ok:
                    return False, msg
            winner = m["a"] if wins_a == need else m["b"]
            with self.db:
                self.db.execute("UPDATE tournament_matches SET wins_a = ?, wins_b = ? WHERE tournament = ? AND round = ? "
                                "AND slot = ?", (wins_a, wins_b, tid, rnd, slot))
            self._decide(tid, rnd, slot, winner, REPORTED)
        self.log(f"tournament {tid}: round {rnd} match {slot} reported {wins_a}-{wins_b} by account {by}")
        self._tell(self.members(tid, m["a"]) + self.members(tid, m["b"]),
                   f"{t['name']}: score reported, {self.side_name(tid, m['a'])} {wins_a} - {wins_b} {self.side_name(tid, m['b'])}.")
        return True, f"Score reported: {wins_a} - {wins_b}."

    def check_in_for(self, tid: int, rnd: int, slot: int, by: int, entrant: int) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, by):
                return False, "Only its creator and admins can check a player in."
            m = self._match(tid, rnd, slot)
            if t["state"] != RUNNING or m is None or m["winner"] is not None or entrant not in (m["a"], m["b"]):
                return False, "That side has no match to check in for."
            if not t["checkin"]:
                return False, "This tournament has no check-in."
            got = self.checked_of(m) | set(self.members(tid, entrant))
            m["checked"] = ",".join(map(str, sorted(got)))
            with self.db:
                self.db.execute("UPDATE tournament_matches SET checked = ? WHERE tournament = ? AND round = ? AND slot = ?",
                                (m["checked"], tid, rnd, slot))
            both = self.match_checked(m, t["checkin"])
        name = self.side_name(tid, entrant)
        self.log(f"tournament {tid}: {name} checked in for round {rnd} match {slot} by account {by}")
        return True, f"{name} checked in." + (" Both sides are in: the room is on its way." if both else "")

    def ready(self) -> list[dict]:
        out = []
        for r in self.db.execute(
                "SELECT m.*, t.name, t.best_of, t.best_of_top8, t.best_of_final, t.mode, t.checkin, t.owner, t.losers, "
                "t.paused, t.stocks, t.minutes, t.map_set, t.map_pick, t.legend_rule, t.legends, t.weapon_rule, "
                "t.weapons, t.scoring, t.damage, t.variation, t.weapon_rate, t.gadget_rate, t.team_damage, t.phases, "
                "(SELECT MAX(x.round) FROM tournament_matches x WHERE x.tournament = m.tournament AND x.round < ?) "
                "AS last_round "
                "FROM tournament_matches m JOIN tournaments t ON t.id = m.tournament "
                "WHERE t.state = ? AND m.a IS NOT NULL AND m.b IS NOT NULL AND m.winner IS NULL", (LOSERS, RUNNING)):
            m = dict(r)
            m["best_of"] = round_best_of(m, m["round"], m["last_round"] + 1)
            out.append(m)
        busy = {}
        for m in out:
            if m["round"] < 0:
                for s in (m["a"], m["b"]):
                    k = (m["tournament"], s)
                    busy[k] = min(busy.get(k, (1e9, 0)), (m["slot"], m["round"]))
        gated = {}
        for m in out:
            if m["round"] >= 0 and m["phases"] and m["tournament"] not in gated:
                rounds, double = m["last_round"] + 1, bool(m["losers"])
                cuts = phase_cuts(m["phases"])
                open_phases = [phase_of(x["round"], rounds, double, cuts) for x in self.matches(m["tournament"])
                               if x["round"] >= 0 and x["winner"] is None and x["note"] != NOT_PLAYED]
                gated[m["tournament"]] = (rounds, cuts, min(open_phases, default=0), double)
        keep = []
        for m in out:
            if m["round"] < 0 and any(busy[(m["tournament"], s)] != (m["slot"], m["round"]) for s in (m["a"], m["b"])):
                continue
            g = gated.get(m["tournament"])
            if m["round"] >= 0 and g and phase_of(m["round"], g[0], g[3], g[1]) > g[2]:
                continue
            keep.append(m)
        return keep

    @staticmethod
    def checked_of(m: dict) -> set:
        return {int(x) for x in (m.get("checked") or "").split(",") if x}

    def side_checked(self, tid: int, m: dict, entrant: int | None) -> bool:
        got = self.checked_of(m)
        return entrant is not None and all(a in got for a in self.members(tid, entrant))

    def match_checked(self, m: dict, minutes: int) -> bool:
        return not minutes or (self.side_checked(m["tournament"], m, m["a"]) and self.side_checked(m["tournament"], m, m["b"]))

    def checkin_view(self, t: dict, m: dict) -> dict:
        pending = (t.get("checkin") and m["winner"] is None and m["a"] is not None and m["b"] is not None
                   and m.get("ready_at") is not None and not self.match_checked(m, t["checkin"]))
        left = max(0, int((m["deadline"] or 0) - self.clock())) if pending else None
        return {"checkin_left": left, "checked": sorted(self.checked_of(m))}

    def _toast(self, accounts: list, text: str) -> None:
        if self.social is None:
            return
        for acc in accounts:
            for s in self._sessions_of(acc):
                self.social._send(s, self.social.server_notice_packet(text))
                self.social._send(s, list_notice(text))

    def _wrounds(self, tid: int) -> int:
        last = self.db.execute("SELECT MAX(round) FROM tournament_matches WHERE tournament = ? AND round < ?",
                               (tid, LOSERS)).fetchone()[0]
        return 0 if last is None else last + 1

    def _round_label(self, tid: int, rnd: int) -> str:
        t = self.get(tid)
        return round_name(rnd, self._wrounds(tid), bool(t and t.get("losers")))

    def run_checkins(self) -> int:
        now = self.clock()
        tell, decided = [], 0
        with self.lock:
            for m in self.ready():
                if not m["checkin"] or m["paused"]:
                    continue
                tid, key = m["tournament"], (m["tournament"], m["round"], m["slot"])
                sides = (self.members(tid, m["a"]), self.members(tid, m["b"]))
                if m["ready_at"] is None:
                    with self.db:
                        self.db.execute("UPDATE tournament_matches SET ready_at = ?, deadline = ?, checked = ? "
                                        "WHERE tournament = ? AND round = ? AND slot = ?",
                                        (now, now + m["checkin"] * 60, "", *key))
                    label = self._round_label(tid, m["round"])
                    for mine, theirs in ((sides[0], m["b"]), (sides[1], m["a"])):
                        tell.append((mine, f"{m['name']} {label.lower()}: check in within {m['checkin']} min"))
                    self.log(f"tournament {tid}: round {m['round']} match {m['slot']} check-in open for {m['checkin']} min")
                    continue
                if self.match_checked(m, m["checkin"]) or now < (m["deadline"] or 0):
                    continue
                ok_a, ok_b = self.side_checked(tid, m, m["a"]), self.side_checked(tid, m, m["b"])
                if ok_a != ok_b:
                    w, gone = (m["a"], m["b"]) if ok_a else (m["b"], m["a"])
                    self._decide(tid, m["round"], m["slot"], w, NO_SHOW)
                    decided += 1
                    done = self.get(tid)["state"] == DONE
                    tell.append((sides[0] + sides[1], f"{m['name']}: no check-in from {self.side_name(tid, gone)}, "
                                 f"{self.side_name(tid, w)} " + ("wins it" if done else "goes through")))
                    self.log(f"tournament {tid}: round {m['round']} match {m['slot']}: {gone} didn't check in, {w} goes through")
                elif key not in self.unchecked_told:
                    self.unchecked_told.add(key)
                    tell.append(([m["owner"]], f"{m['name']}: neither side checked in (BRACKET: more time or award)"))
                    self.log(f"tournament {tid}: round {m['round']} match {m['slot']}: nobody checked in")
        for accs, text in tell:
            self._toast(accs, text)
        return decided

    def _my_match(self, tid: int, account_id: int) -> dict | None:
        side = self.entrant_of(tid, account_id)
        if side is None:
            return None
        return next((m for m in self.ready() if m["tournament"] == tid and side in (m["a"], m["b"])), None)

    def check_in(self, tid: int, account_id: int) -> tuple[bool, str]:
        with self.lock:
            m = self._my_match(tid, account_id)
            if m is not None and m["checkin"] and m["ready_at"] is None:
                self.run_checkins()
                m = self._my_match(tid, account_id)
            if m is None:
                return False, "You have no match to check in for."
            if not m["checkin"]:
                return False, "This tournament has no check-in."
            got = self.checked_of(m)
            if account_id in got:
                return True, "You're checked in already."
            if self.clock() > (m["deadline"] or 0):
                return False, "The check-in has closed."
            got.add(account_id)
            with self.db:
                self.db.execute("UPDATE tournament_matches SET checked = ? WHERE tournament = ? AND round = ? AND slot = ?",
                                (",".join(map(str, sorted(got))), tid, m["round"], m["slot"]))
            m["checked"] = ",".join(map(str, got))
            both = self.match_checked(m, m["checkin"])
            everyone = self.members(tid, m["a"]) + self.members(tid, m["b"])
        self.log(f"tournament {tid}: account {account_id} checked in for round {m['round']} match {m['slot']}")
        if both:
            self._toast([a for a in everyone if a != account_id],
                        f"{m['name']}: both sides checked in")
            return True, "Checked in. Both sides are in: the match room is on its way."
        return True, "Checked in. Waiting for the other side."

    def may_manage(self, t: dict | None, account_id: int | None) -> bool:
        return t is not None and account_id is not None and (t["owner"] == account_id or account_id in self.admins(t["id"]))

    def extend(self, tid: int, rnd: int, slot: int, account_id: int, minutes: int = EXTEND_MINUTES) -> tuple[bool, str]:
        with self.lock:
            t = self.get(tid)
            if not self.may_manage(t, account_id):
                return False, "Only its creator and admins can give more time."
            m = next((x for x in self.ready() if (x["tournament"], x["round"], x["slot"]) == (tid, rnd, slot)), None)
            if m is None or not m["checkin"] or m["ready_at"] is None or self.match_checked(m, m["checkin"]):
                return False, "That match isn't waiting for a check-in."
            deadline = max(m["deadline"] or 0, self.clock()) + minutes * 60
            with self.db:
                self.db.execute("UPDATE tournament_matches SET deadline = ? WHERE tournament = ? AND round = ? AND slot = ?",
                                (deadline, tid, rnd, slot))
            self.unchecked_told.discard((tid, rnd, slot))
            everyone = self.members(tid, m["a"]) + self.members(tid, m["b"])
        left = int((deadline - self.clock()) // 60)
        self._toast(everyone, f"{t['name']}: {left} min left to check in")
        self.log(f"tournament {tid}: round {rnd} match {slot} check-in extended by {minutes} min")
        return True, f"{minutes} more minutes to check in."

    def on_result(self, entry, verdict: str, out: dict, rep: dict | None) -> None:
        if verdict != COUNTED or not rep or len(entry.ids) not in (2, 4):
            return
        accounts = out.get("accounts") or {}
        accs = [accounts.get(pid) for pid in entry.ids]
        if None in accs or len(set(accs)) != len(accs):
            return
        result = outcomes(rep["players"])
        winners = {accounts[pid] for pid, r in result.items() if r == "win" and pid in accounts}
        told = []
        with self.lock:
            for m in self.ready():
                tid = m["tournament"]
                side_a, side_b = set(self.members(tid, m["a"])), set(self.members(tid, m["b"]))
                if side_a | side_b != set(accs) or len(side_a) + len(side_b) != len(accs):
                    continue
                won = m["a"] if winners and winners <= side_a else m["b"] if winners and winners <= side_b else None
                wins_a = m["wins_a"] + (won == m["a"])
                wins_b = m["wins_b"] + (won == m["b"])
                results = ",".join(x for x in (m["results"], str(out.get("id", ""))) if x)
                with self.db:
                    self.db.execute("UPDATE tournament_matches SET wins_a = ?, wins_b = ?, results = ? "
                                    "WHERE tournament = ? AND round = ? AND slot = ?",
                                    (wins_a, wins_b, results, tid, m["round"], m["slot"]))
                need = wins_needed(m["best_of"])
                score = f"{self.side_name(tid, m['a'])} {wins_a} - {wins_b} {self.side_name(tid, m['b'])}"
                if max(wins_a, wins_b) >= need:
                    w = m["a"] if wins_a >= need else m["b"]
                    self._decide(tid, m["round"], m["slot"], w)
                    t = self.get(tid)
                    if t["state"] == DONE:
                        text = f"{m['name']}: {self.side_name(tid, w)} wins the tournament! ({score})"
                    else:
                        text = f"{m['name']}: {self.side_name(tid, w)} wins the match ({score}) and goes through."
                else:
                    text = f"{m['name']}: {score} (first to {need})."
                self.log(f"tournament {tid} round {m['round']} match {m['slot']}: {score}")
                told.append((sorted(side_a | side_b), text))
                break
        for accs_, text in told:
            self._tell(accs_, text)

    def _sessions_of(self, account_id: int) -> list:
        if self.social is None:
            return []
        return [s for s in list(self.social.sessions) if s.account and s.account.get("id") == account_id]

    def _tell(self, accounts: list, text: str) -> None:
        for acc in accounts:
            for s in self._sessions_of(acc):
                self.social._notice(s, text)
                self.social._send(s, list_notice(text))

    def tick(self) -> int:
        with self.lock:
            self.settle_disqualified()
        self.start_due()
        self.run_checkins()
        if self.social is None:
            return 0
        from . import lobby
        sent = 0
        with self.social.lock, self.lock:
            ready = self.ready()
            keys = set()
            for m in ready:
                key = (m["tournament"], m["round"], m["slot"])
                if m["paused"]:
                    keys.add(key)
                    continue
                if not self.match_checked(m, m["checkin"]):
                    continue
                keys.add(key)
                sent += self.serve(key, m, lobby)
            for key in [k for k in self.live if k not in keys]:
                room = self.live.pop(key)
                room.fixed_teams = {}
                self.social.rooms.free(room)
                self.log(f"tournament {key[0]}: room {room.number} freed (round {key[1]} match {key[2]} is over)")
        return sent

    def serve(self, key: tuple, m: dict, lobby) -> int:
        tid, rooms = m["tournament"], self.social.rooms
        sides = (self.members(tid, m["a"]), self.members(tid, m["b"]))
        everyone = sides[0] + sides[1]
        room = self.live.get(key)
        if room is None or rooms.by_number.get(room.number) is not room:
            game = room_game(m)
            settings = lobby.default_settings(game, PRIVATE_ROOM, 1)
            settings["select_mode"] = MAP_PICKS.get(m.get("map_pick") or "random", 0)
            off, why = heroes_off(m)
            room = rooms.create_reserved(len(everyone), settings, tourney={"key": key, "heroes_off": off, "why": why})
            self.live[key] = room
            self.log(f"tournament {tid}: room {room.number} ({m['mode']}) for round {m['round']} match {m['slot']}: "
                     f"{self.side_name(tid, m['a'])} vs {self.side_name(tid, m['b'])}")
        sessions = {acc: self._sessions_of(acc) for acc in everyone}
        if m["mode"] == "2v2":
            room.fixed_teams = {self.social.pid(x): team for team, side in ((1, sides[0]), (2, sides[1]))
                                for acc in side for x in sessions[acc] if self.social.pid(x) is not None}
        ids = set(room.ids())
        present = {acc for acc in everyone if any(self.social.pid(x) in ids for x in sessions[acc])}
        games = m["wins_a"] + m["wins_b"]
        sent = 0
        for acc in everyone:
            if acc in present:
                continue
            for x in sessions[acc]:
                pid = self.social.pid(x)
                other = rooms.room_of(pid) if pid is not None else None
                if pid is None or (other is not None and other.started):
                    continue
                ikey = key + (room.number, id(x), games)
                if ikey in self.room_invites or room.started or len(room.members) >= room.limit:
                    continue
                self.room_invites.add(ikey)
                self.social.server_invite(x, room, m["name"])
                theirs = m["b"] if acc in sides[0] else m["a"]
                self.social._send(x, self.social.server_notice_packet(
                    f"{m['name']} {self._round_label(tid, m['round']).lower()} vs {self.side_name(tid, theirs)}: "
                    "accept the invite"))
                sent += 1
        locked = present == set(everyone) and not room.started and room.members and all(x.ready for x in room.members)
        if not locked:
            room.locked_since = None
        elif self.lock_hold > 0 and getattr(room, "locked_since", None) is None:
            room.locked_since = time.monotonic()
            t = threading.Timer(self.lock_hold + 0.1, self.tick)
            t.daemon = True
            t.start()
            room.lock_timer = t
            self.log(f"tournament {tid}: room {room.number} locked in; the map select in {self.lock_hold:g} s")
            return sent
        elif self.lock_hold > 0 and time.monotonic() - room.locked_since < self.lock_hold - 0.05:
            return sent
        if locked:
            room.locked_since = None
            if getattr(room, "lock_timer", None) is not None:
                room.lock_timer.cancel()
                room.lock_timer = None
            pool = list(map_pool(m))
            room.start_level = self.random.choice(pool)
            started, _seed, why = rooms.start(room.host)
            mode = MAP_PICKS.get(m.get("map_pick") or "random", 0)
            if why is None and mode and len(pool) > 1:
                started.candidates, started.random_tile = pool, 0
                lobby.open_level_select(rooms, started, mode, self.log)
            elif why is None:
                lobby.send_start(rooms, started, room.start_level, self.log)
            if why is None:
                for x in started.members:
                    x.ready = 0
                self.log(f"tournament {tid}: round {m['round']} match {m['slot']} game {games + 1} started in room "
                         f"{room.number} " + (f"(map striking over {len(pool)} levels)" if mode and len(pool) > 1
                                               else f"(level {room.start_level})"))
            elif why != "already started":
                self.log(f"tournament {tid}: room {room.number} not started ({why})")
        return sent

    def events_xml(self, public_url: str) -> str:
        now = self.clock()
        rows = []
        for t in self.listing():
            if t["state"] == DONE and (t["finished"] or 0) < now - 7 * 86400:
                continue
            start = t["started"] or t["starts"] or t["created"]
            end = t["finished"] if t["state"] == DONE else max(now + 3600, start + 3600)
            reg_end = (t["started"] or now + 86400) if t["state"] == OPEN else (t["started"] or start)
            who = "teams" if t["mode"] == "2v2" else "players"
            where = (f"{t['mode']}, {t['players']}/{t['max_players']} {who}, best of {t['best_of']}" if t["state"] != DONE
                     else f"Won by {self.side_name(t['id'], t['winner'])}")
            fields = {"Title": t["name"], "RegistrationEnds": int(reg_end), "StartTime": int(start),
                      "StreamTime": int(start) if t["state"] != OPEN else int(end) + 1, "EndTime": int(end),
                      "PrizeType": "N/A", "IconImageName": EVENT_IMAGE, "IsOfficial": "false",
                      "IsFeatured": "true" if t["state"] == RUNNING else "false", "IsPinned": "false",
                      "RegistrationURL": f"{public_url.rstrip('/')}/tournament/{t['id']}", "Location": where,
                      "LiveStreamURL": f"{public_url.rstrip('/')}/tournament/{t['id']}"}
            rows.append("<TournamentEventType>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in fields.items())
                        + "</TournamentEventType>")
        fields = {"Title": "Bifrost tournaments", "RegistrationEnds": int(now + 365 * 86400),
                  "StartTime": int(now), "StreamTime": int(now + 366 * 86400), "EndTime": int(now + 367 * 86400),
                  "PrizeType": "N/A", "IconImageName": EVENT_IMAGE, "IsOfficial": "false", "IsFeatured": "true",
                  "IsPinned": "true", "RegistrationURL": f"{public_url.rstrip('/')}/tournaments",
                  "Location": ""}
        rows.append("<TournamentEventType>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in fields.items())
                    + "</TournamentEventType>")
        if (self.cfg or {}).get("cups", False):
            fields = dict(fields, Title="Cups", Location="Double elimination brackets of 8", IsPinned="false",
                          RegistrationURL=f"{public_url.rstrip('/')}/cups")
            rows.append("<TournamentEventType>" + "".join(f"<{k}>{escape(str(v))}</{k}>" for k, v in fields.items())
                        + "</TournamentEventType>")
        return "<TournamentEventTypes>" + "".join(rows) + "</TournamentEventTypes>"

    def web_get(self, path: str, acc: dict | None, public_url: str,
                query: dict | None = None) -> tuple[int, str, str, str] | None:
        if path == "/api/tournaments":
            return 200, "application/json", json.dumps(self.listing()), ""
        if path == "/api/tournaments/events.xml":
            return 200, "application/xml; charset=utf-8", self.events_xml(public_url), ""
        if path.startswith("/api/tournament/") and path[16:].isdigit():
            v = self.view(int(path[16:]))
            return (200, "application/json", json.dumps(v), "") if v else (404, "application/json", '{"error": "no such tournament"}', "")
        if path == "/tournaments":
            return 200, "text/html", self.list_html(acc), "Tournaments"
        if path.startswith("/tournament/") and path[12:].isdigit():
            body = self.page_html(int(path[12:]), acc, (query or {}).get("find", ""))
            t = self.get(int(path[12:]))
            return (200, "text/html", body, t["name"]) if body else (404, "text/html", "<p>No such tournament.</p>", "Not found")
        return None

    def web_post(self, path: str, acc: dict | None, form: dict) -> tuple[str, str]:
        if not acc:
            return "/login", "Sign in first."
        if path == "/tournaments/new":
            if not self.may_create(acc):
                return "/tournaments", "This server doesn't let you create tournaments."
            try:
                best_of, limit = int(form.get("best_of", 3)), int(form.get("max_players", 16))
            except ValueError:
                return "/tournaments", "Best of and the player limit are numbers."
            try:
                checkin = int(form.get("checkin", CHECKIN_MINUTES))
                top8, final = int(form.get("best_of_top8", 0)), int(form.get("best_of_final", 0))
                low, high = int(form.get("min_rating") or 0), int(form.get("max_rating") or 0)
                losers = int(form.get("losers") or 0)
                more = {k: form.get(k) or None for k in MORE}
                for k in ("team_damage", "gadget_rate", "variation", "weapon_rate"):
                    if form.get(k) not in (None, ""):
                        more[k] = form.get(k)
            except ValueError:
                return "/tournaments", "Check-in and the best ofs are numbers."
            tid, msg = self.create(acc["id"], form.get("name", ""), best_of, limit, about=form.get("about", ""),
                                   mode=form.get("mode", "1v1"), checkin=checkin, best_of_top8=top8, best_of_final=final,
                                   min_rating=low, max_rating=high, losers=losers, **more)
            return (f"/tournament/{tid}" if tid else "/tournaments"), msg
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[0] == "tournament" and parts[1].isdigit():
            tid, action = int(parts[1]), parts[2]
            back = f"/tournament/{tid}"
            if action == "join":
                t = self.get(tid)
                if t and t["mode"] == "2v2":
                    return back, "Sign a 2v2 team up in the game, with your teammate in your party."
                return back, self.join(tid, acc["id"])[1]
            if action == "leave":
                return back, self.withdraw(tid, acc["id"])[1]
            if action == "start":
                return back, self.start(tid, acc["id"])[1]
            if action == "cancel":
                return back, self.cancel(tid, acc["id"])[1]
            if action == "checkin":
                return back, self.check_in(tid, acc["id"])[1]
            if action == "extend":
                try:
                    return back, self.extend(tid, int(form["round"]), int(form["slot"]), acc["id"])[1]
                except (KeyError, ValueError):
                    return back, "Pick a match."
            if action == "award":
                try:
                    rnd, slot, winner = int(form["round"]), int(form["slot"]), int(form["winner"])
                except (KeyError, ValueError):
                    return back, "Pick a match and a winner."
                return back, self.award(tid, rnd, slot, acc["id"], winner)[1]
            try:
                if action in ("pause", "resume"):
                    return back, self.pause(tid, acc["id"], action == "pause")[1]
                if action == "undo":
                    return back, self.undo(tid, int(form["round"]), int(form["slot"]), acc["id"])[1]
                if action == "disqualify":
                    return back, self.disqualify(tid, acc["id"], int(form["account"]))[1]
                if action in ("admin", "unadmin"):
                    return back, self.set_admin(tid, acc["id"], form.get("name", ""),
                                                int(form["account"]) if form.get("account") else None,
                                                action == "admin")[1]
                if action == "settings":
                    return back, self.settings(tid, acc["id"], {k: form[k] for k in (
                        "checkin", "best_of", "best_of_top8", "best_of_final") if form.get(k) not in (None, "")})[1]
            except (KeyError, ValueError):
                return back, "Those details aren't right."
        return "/tournaments", "Unknown action."

    def list_html(self, acc: dict | None) -> str:
        def later(name: str) -> str:
            return f"<select name={name}>" + "".join(f"<option value={b}>{f'best of {b}' if b else 'the same'}</option>"
                                                   for b in LATER_BEST_OF) + "</select>"

        def pick(name: str, choices: dict, chosen) -> str:
            return f"<select name={name}>" + "".join(f"<option value={k}{' selected' if k == chosen else ''}>{v}</option>"
                                                   for k, v in choices.items()) + "</select>"
        rows = "".join(
            f"<tr><td><a href='/tournament/{t['id']}'>{html.escape(t['name'])}</a></td><td>{t['state']}</td>"
            f"<td>{t['players']}/{t['max_players']}</td><td>{t['mode']}, best of {t['best_of']}</td>"
            f"<td>{html.escape(self.side_name(t['id'], t['winner'])) if t['winner'] else ''}</td></tr>"
            for t in self.listing())
        table = (f"<table><tr><th>Tournament</th><th>State</th><th>Players</th><th>Games</th><th>Winner</th></tr>{rows}</table>"
                 if rows else "<p>No tournaments yet.</p>")
        form = ""
        if self.may_create(acc):
            form = ("<h2>New tournament</h2><form method=post action='/tournaments/new'>"
                    "<p><input name=name placeholder='Name' maxlength=40 required></p>"
                    "<p><select name=mode><option>1v1</option><option>2v2</option></select> · best of <select name=best_of><option>1</option><option selected>3</option><option>5</option></select> "
                    "· up to <input name=max_players type=number min=2 max=256 value=16 style='width:4em'> players "
                    "(teams in a 2v2) · check-in <select name=checkin>" +
                    "".join(f"<option value={c}{' selected' if c == CHECKIN_MINUTES else ''}>"
                            f"{f'{c} minutes' if c else 'none'}</option>" for c in CHECKIN_CHOICES) + "</select></p>"
                    "<p><select name=losers><option value=0>single elimination</option><option value=1>double "
                    "elimination (grand final reset)</option><option value=2>double elimination, no reset</option>"
                    "</select></p>"
                    "<p>From the top 8: " + later("best_of_top8") + " · the final: " + later("best_of_final") + "</p>"
                    "<p><input name=about placeholder='About (optional)' maxlength=200 style='width:30em'></p>"
                    "<p>Stocks <select name=stocks>" + "".join(f"<option{' selected' if x == 3 else ''}>{x}</option>"
                                                                for x in STOCKS) + "</select> · minutes <select name=minutes>"
                    + "".join(f"<option{' selected' if x == 8 else ''}>{x}</option>" for x in MINUTES) + "</select></p>"
                    "<p>Game mode " + pick("scoring", ALL_SCORINGS, 2) + " · damage " + pick("damage", {
                        d: f"{d}%" for d in range(DAMAGE_RANGE[0], DAMAGE_RANGE[1] + 1, 10)}, 100) + " · variation " +
                    pick("variation", VARIATIONS, 0) + " · weapons " + pick("weapon_rate", WEAPON_RATES, 2) +
                    " · gadgets " + pick("gadget_rate", GADGET_RATES, 0) + " · team damage (2v2) " +
                    pick("team_damage", {1: "On", 0: "Off"}, 1) + "</p><p>"
                    "seeding <select name=seeding><option value=rating>by rating</option><option value=random>random"
                    "</option><option value=signup>sign-up order</option><option value=manual>manual</option></select>"
                    " · starts by itself in <input "
                    "name=start_in type=number min=0 max=525600 placeholder=never style='width:5em'> minutes</p>"
                    "<p>Legends <select name=legend_rule><option value=''>any</option><option value=ban>ban</option>"
                    "<option value=only>only</option></select> <input name=legends placeholder='e.g. Orion, Kaya' "
                    "style='width:16em'> · weapons <select name=weapon_rule><option value=''>any</option><option "
                    "value=ban>ban</option><option value=only>only</option></select> <input name=weapons "
                    "placeholder='e.g. Orb, Boots' style='width:12em'></p>"
                    "<p>Maps <select name=map_set><option value=0>Tournament</option><option value=98>Ranked 1v1"
                    "</option><option value=3>Standard 1v1</option><option value=33>Experimental 1v1</option><option "
                    "value=99>Ranked 2v2</option><option value=109>Unranked 2v2</option><option value=4>Standard 2v2"
                    "</option></select> picked by <select name=map_pick><option value=tournament>tournament striking</option>"
                    "<option value=random>random</option><option value=straight>turns striking</option><option "
                    "value=snake>snake striking</option></select></p>"
                    "<p><input name=rules placeholder='Rules (optional)' maxlength=300 style='width:30em'></p>"
                    "<p><input name=region placeholder='Region' maxlength=40> <input name=stream placeholder='Stream or "
                    "spectate link' maxlength=120 style='width:16em'> <input name=prize placeholder='Prize note' "
                    "maxlength=100></p>"
                    "<p><button>Create</button></p></form>")
        elif not acc:
            form = "<p><a href='/login'>Sign in</a> to sign up or create one.</p>"
        return table + form

    def page_html(self, tid: int, acc: dict | None, find: str = "") -> str | None:
        v = self.view(tid)
        if v is None:
            return None
        me = acc["id"] if acc else None
        find = " ".join(str(find or "").split())[:32]
        mine_side = self.entrant_of(tid, me) if me is not None else None
        mine_at, found = None, []
        owner = me == v["owner"]
        manage = self.may_manage(self.get(tid), me)
        e = html.escape
        head = (f"<p>{e(v['about'])}</p>" if v["about"] else "") + \
               f"<p>{v['state'].capitalize()} · Tournament {v['mode']} · best of {v['best_of']}" + \
               (f" (from the top 8: {v['best_of_top8']})" if v["best_of_top8"] else "") + \
               (f" (final: {v['best_of_final']})" if v["best_of_final"] else "") + \
               (f" · rating {v['min_rating'] or 0}{'-' + str(v['max_rating']) if v['max_rating'] else '+'}"
                if v["min_rating"] or v["max_rating"] else "") + \
               f" · {len(v['entrants'])}/{v['max_players']} {'teams' if v['mode'] == '2v2' else 'players'} · " \
               f"{'check-in ' + str(v['checkin']) + ' minutes' if v['checkin'] else 'no check-in'}" + \
               (" · double elimination" + ("" if v["losers"] == DOUBLE else ", no reset") if v["losers"] else "") + \
               (f" · <b>won by {e(v['winner_name'])}</b>" if v["winner_name"] else "") + "</p>"
        def game_line(i: int, g: dict) -> str:
            def who(k):
                return ", ".join(f"{e(p['name'])} ({e(p['legend'])}, {p['kos']} KO{'s' if p['kos'] != 1 else ''}, "
                                 f"{p['falls']} fall{'s' if p['falls'] != 1 else ''})" for p in g[k]) or "?"
            first, second = (("a", "b") if g["won"] != "b" else ("b", "a"))
            g = dict(g, map=re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", g["map"]))
            verb = "beat" if g["won"] else "drew with"
            return (f"G{i + 1} {e(g['map'])} {g['seconds'] // 60}:{g['seconds'] % 60:02d}: <b>{who(first)}</b> {verb} "
                    f"{who(second)}" if g["won"] else f"G{i + 1} {e(g['map'])} {g['seconds'] // 60}:{g['seconds'] % 60:02d}: "
                    f"{who('a')} {verb} {who('b')}")

        btn = lambda action, label: f"<form method=post action='/tournament/{tid}/{action}' style='display:inline'><button>{label}</button></form> "
        actions = ""
        if v["state"] == OPEN and me:
            signed = any(me in (x["account"], x["partner"]) for x in v["entrants"] + v["waitlist"])
            actions += btn("leave", "Leave") if signed else btn("join", "Sign up")
            if manage:
                actions += btn("start", "Start")
        if manage and v["state"] == RUNNING:
            actions += btn("resume", "Resume") if v["paused"] else btn("pause", "Pause")
        if owner and v["state"] in (OPEN, RUNNING):
            actions += btn("cancel", "Cancel the tournament")
        hidden = lambda **kv: "".join(f"<input type=hidden name={k} value={x}>" for k, x in kv.items())
        form = lambda action, label, **kv: (f"<form method=post action='/tournament/{tid}/{action}' style='display:inline'>"
                                            f"{hidden(**kv)}<button>{label}</button></form> ")
        dq_ok = manage and v["state"] in (OPEN, RUNNING)
        players = "".join(f"<li>{('#%d ' % x['seed']) if x['seed'] else ''}{e(x['name'])}"
                          + (" <i>(disqualified)</i>" if x["disqualified"] else "")
                          + (" " + form("disqualify", "Take off" if v["state"] == OPEN else "Disqualify", account=x["account"])
                             if dq_ok and not x["disqualified"] else "") + "</li>" for x in v["entrants"])
        admin_html = ""
        if v["admins"] or (owner and v["state"] in (OPEN, RUNNING)):
            admin_html = "<p>Admins: " + (", ".join(
                e(a["name"]) + (" " + form("unadmin", "Remove", account=a["account"]) if owner else "")
                for a in v["admins"]) or "none") + "</p>"
            if owner and v["state"] in (OPEN, RUNNING):
                admin_html += (f"<form method=post action='/tournament/{tid}/admin'><input name=name placeholder='Player "
                               f"name' maxlength=40> <button>Add an admin</button></form>")
        if manage and v["state"] in (OPEN, RUNNING):
            opt = lambda name, values, cur, label=str: f"<select name={name}>" + "".join(
                f"<option value={x}{' selected' if x == cur else ''}>{label(x)}</option>" for x in values) + "</select>"
            later = lambda x: f"best of {x}" if x else "the same"
            admin_html += (f"<form method=post action='/tournament/{tid}/settings'>Check-in "
                           + opt("checkin", CHECKIN_CHOICES, v["checkin"], lambda x: f"{x} min" if x else "none")
                           + " · best of " + opt("best_of", BEST_OF, v["best_of"])
                           + " · top 8 " + opt("best_of_top8", LATER_BEST_OF, v["best_of_top8"], later)
                           + " · final " + opt("best_of_final", LATER_BEST_OF, v["best_of_final"], later)
                           + " <button>Save</button></form>")
        brackets = {"w": "", "l": ""}
        for r in v["rounds"]:
            rnd = r["round"]
            cells = []
            for m in r["matches"]:
                def side(acc_id, name, wins):
                    if acc_id is None:
                        return "<i>bye</i>" if rnd == 0 or m["note"] in ("bye", NOT_PLAYED) else "<i>?</i>"
                    style = " style='font-weight:bold'" if m["winner"] == acc_id else ""
                    return f"<span{style}>{e(name)} ({wins})</span>"
                cell = f"{side(m['a'], m['a_name'], m['wins_a'])}<br>{side(m['b'], m['b_name'], m['wins_b'])}"
                if m["note"] in (NO_SHOW, DISQUALIFIED):
                    cell += f"<br><small>{e(m['note'])}</small>"
                if m["results"]:
                    played = self.games(tid, rnd, m["slot"])["games"]
                    cell += "".join(f"<br><small>{game_line(i, g)}</small>" for i, g in enumerate(played)) or \
                        f"<br><small>{len(m['results'])} game{'s' if len(m['results']) != 1 else ''} counted</small>"
                if m["checkin_left"] is not None:
                    names = [self.name_of(a) for a in m["checked"]]
                    cell += (f"<br><small>check-in: {m['checkin_left'] // 60}:{m['checkin_left'] % 60:02d} left"
                             + (f" · in: {e(', '.join(names))}" if names else "") + "</small>")
                    if me is not None and me not in m["checked"] and me in (
                            self.members(tid, m["a"]) + self.members(tid, m["b"])):
                        cell += btn("checkin", "Check in")
                    if manage:
                        cell += (f"<form method=post action='/tournament/{tid}/extend' style='margin:2px 0'>"
                                 f"<input type=hidden name=round value={rnd}><input type=hidden name=slot value={m['slot']}>"
                                 f"<button>+{EXTEND_MINUTES} minutes</button></form>")
                if manage and m["winner"] is not None and m["note"] not in ("bye", NOT_PLAYED):
                    cell += form("undo", "Undo the result", round=rnd, slot=m["slot"])
                if manage and v["state"] == RUNNING and m["winner"] is None and m["a"] and m["b"]:
                    cell += "".join(
                        f"<form method=post action='/tournament/{tid}/award' style='margin:2px 0'><input type=hidden name=round value={rnd}>"
                        f"<input type=hidden name=slot value={m['slot']}><input type=hidden name=winner value={w}>"
                        f"<button>Award to {e(n)}</button></form>" for w, n in ((m["a"], m["a_name"]), (m["b"], m["b_name"])))
                anchor = f"m{rnd}-{m['slot']}"
                border = "1px solid #888"
                if mine_side is not None and mine_side in (m["a"], m["b"]):
                    border = "3px solid #2a9"
                    if mine_at is None or not mine_at[1]:
                        mine_at = (anchor, m["winner"] is None and m["a"] is not None and m["b"] is not None)
                if find and any(find.lower() in (n or "").lower() for n in (m["a_name"], m["b_name"])):
                    border = "3px solid #d80"
                    found.append((anchor, f"{r['name']}: {m['a_name'] or '?'} vs {m['b_name'] or '?'}"))
                cells.append(f"<div id={anchor} style='border:{border};padding:6px;margin:8px 0'>{cell}</div>")
            brackets["l" if r["bracket"] == "l" else "w"] += (
                        f"<td style='vertical-align:middle;padding:0 12px'><h3>{e(r['name'])}</h3>"
                        f"<small>best of {r['best_of']}</small>{''.join(cells)}</td>")
        if v["paused"]:
            head += "<p><b>Paused</b>: no new rooms or check-ins until it's resumed.</p>"
        head += (f"<p>{v['stocks']} stock{'s' if v['stocks'] != 1 else ''}, {v['minutes']} minutes · seeding: "
                 f"{ {'rating': 'by rating', 'random': 'random', 'signup': 'sign-up order', 'manual': 'manual'}[v['seeding']] }"
                 + f" · maps: {MAP_POOL_NAMES.get(v['map_set'] or MAP_POOLS[v['mode']][0], '?')}, "
                 + {"random": "at random", "straight": "straight strikes", "snake": "snake strikes"}.get(v["map_pick"], "")
                 + "".join(f" · {what} {'banned' if v[rule] == 'ban' else 'only'}: {e(names)}"
                           for what, rule, names in (("legends", "legend_rule", self.legend_names(v["legends"])),
                                                     ("weapons", "weapon_rule", (v["weapons"] or "").replace(",", ", ")))
                           if v[rule] and names)
                 + (f" · starts by itself at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(v['starts']))}"
                    if v["state"] == OPEN and v["starts"] else "")
                 + (f" · region: {e(v['region'])}" if v["region"] else "") + "</p>"
                 + (f"<p>Rules: {e(v['rules'])}</p>" if v["rules"] else "")
                 + ("<p>Stream: " + (f"<a href='{e(v['stream'])}'>{e(v['stream'])}</a>"
                                      if v["stream"].startswith(("https://", "http://")) else e(v["stream"])) + "</p>"
                    if v["stream"] else "")
                 + (f"<p>Prize: {e(v['prize'])}</p>" if v["prize"] else ""))
        return (head + (f"<p>{actions}</p>" if actions else "") + admin_html +
                (f"<form method=get style='margin:4px 0'><input name=find value='{e(find)}' placeholder='Find a player'"
                 f" maxlength=32> <button>Find</button>" + (f" <a href='#{mine_at[0]}'>Your match</a>" if mine_at else "") +
                 "</form>" if v["rounds"] else "") +
                ("<p>" + (" · ".join(f"<a href='#{a}'>{e(t)}</a>" for a, t in found) if found else
                          f"Nobody named {e(find)} in this bracket.") + "</p>" if find else "") +
                "".join(f"<div style='overflow-x:auto'><table><tr>{row}</tr></table></div>"
                        for row in brackets.values() if row) +
                f"<h2>Players</h2><ol style='list-style:none;padding:0'>{players or '<li>Nobody yet.</li>'}</ol>"
                + (f"<p>Waitlist: {e(', '.join(w['name'] for w in v['waitlist']))}</p>" if v["waitlist"] else "") +
                "<p><small>When your match is ready, check in (here or in the game: ESPORTS); once both sides are in "
                "the server makes its room (Tournament 1v1 or 2v2) and invites both sides; a side that doesn't check "
                "in in time loses the match. Each game starts once everyone is in and locked in. Both games must finish (both PCs "
                "report the result) to count.</small></p>"
                "<p><a href='/tournaments'>All tournaments</a></p>")


LIST_NOTICE = 11096
ACT = 11047
PART_MARK, MAX_PARTS = "\x01", 8


def answer_parts(body: str, limit: int = MAX_JSON) -> list[str] | None:
    if len(body.encode()) <= limit:
        return [body]
    room = limit - 16
    chunks, start = [], 0
    while start < len(body):
        end = min(len(body), start + room)
        while len(body[start:end].encode()) > room:
            end -= max(1, (len(body[start:end].encode()) - room) // 3)
        chunks.append(body[start:end])
        start = end
        if len(chunks) > MAX_PARTS:
            return None
    return [f"{PART_MARK}{k} {len(chunks)}{PART_MARK}{c}" for k, c in enumerate(chunks, 1)]


class Desk:

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms

    def hub(self) -> Tournaments | None:
        s = self.session
        if s.store is None:
            return None
        hub = getattr(self.rooms, "tournaments", None)
        return hub if hub is not None else Tournaments.of(self.rooms, s.store, s.cfg, s.log, timers=False)

    def _answer(self, query: str, obj) -> None:
        pieces = answer_parts(json.dumps(obj, separators=(",", ":")))
        for piece in pieces or [json.dumps({"error": "too large"})]:
            self.session.send(Packet(ANSWER, BitWriter().string(query).string(piece).bytes()))

    def handle(self, p: Packet) -> bool:
        if p.type == ASK:
            try:
                query = BitReader(p.payload).string()[:64]
            except Exception:
                return False
            if query != "tournaments" and not query.startswith(("tournament:", "tmatch:", "tpool:")):
                return False
            self._answer(query, self.read(query))
            return True
        if p.type != ACT:
            return False
        try:
            r = BitReader(p.payload)
            action, args = r.string()[:16], json.loads(r.string() or "{}")
            if not isinstance(args, dict):
                raise ValueError("not an object")
        except Exception as e:
            self.session.log(f"tournament action unreadable: {e}")
            return True
        ok, msg, tid = self.act(action, args)
        self.session.log(f"tournament action {action} {args} by account "
                         f"{self.session.account['id'] if self.session.account else None}: {msg}")
        self._answer("tournament-act", {"ok": ok, "message": msg, "id": tid, "action": action})
        self._answer("tournaments", self.read("tournaments"))
        if tid:
            self._answer(f"tournament:{tid}", self.read(f"tournament:{tid}"))
        return True

    def read(self, query: str) -> dict:
        hub, acc = self.hub(), self.session.account
        if hub is None:
            return {"error": "This server keeps no accounts, so it has no tournaments."}
        me = acc["id"] if acc else None
        if query.startswith("tmatch:"):
            parts = query[7:].split(":")
            got = hub.games(*map(int, parts)) if len(parts) == 3 and all(x.lstrip("-").isdigit() for x in parts) \
                else None
            return got if got is not None else {"error": "No such match."}
        if query.startswith("tpool:"):
            parts = query[6:].split(":")
            got = hub.pool_view(*map(int, parts)) if len(parts) == 2 and all(x.isdigit() for x in parts) else None
            return got if got is not None else {"error": "No such pool."}
        if query == "tournaments":
            now = hub.clock()
            rows = [{k: t[k] for k in ("id", "name", "state", "best_of", "max_players", "players", "about", "owner", "winner",
                                       "mode", "checkin", "best_of_top8", "best_of_final", "min_rating", "max_rating",
                                       "losers", "starts", "stocks", "minutes", "seeding", "region", "created",
                                       "started", "finished", "pools_until", "pool_advance", "phases")}
                    | {"winner_name": hub.side_name(t["id"], t["winner"]) if t["winner"] else None, "mine": t["owner"] == me}
                    for t in hub.listing() if not (t["state"] == DONE and (t["finished"] or 0) < now - 30 * 86400)]
            signed = {r[0] for r in hub.db.execute("SELECT tournament FROM tournament_entrants WHERE waiting = 0 AND "
                                                   "(account_id = ? OR partner = ?)", (me, me))} if me else set()
            for row in rows:
                row["signed_up"] = row["id"] in signed
            return {"tournaments": rows, "me": me, "may_create": hub.may_create(acc),
                    "signed_in": acc is not None}
        tid = query[11:]
        v = hub.view(int(tid)) if tid.isdigit() else None
        if v is None:
            return {"error": "No such tournament."}
        v["me"] = me
        v["my_side"] = hub.entrant_of(v["id"], me) if me is not None else None
        v["my_pool_match"] = hub.my_pool_match(v["id"], v["my_side"])
        v["manage"] = hub.may_manage(hub.get(v["id"]), me)
        v["server_now"] = hub.clock()
        v["mine"] = me is not None and v["owner"] == me
        v["owner_name"] = hub.name_of(v["owner"])
        if me is not None and v["mode"] == "2v2":
            rows = hub.db.execute("SELECT inviter, invitee FROM tournament_team_invites WHERE tournament = ? AND "
                                  "(inviter = ? OR invitee = ?)", (v["id"], me, me)).fetchall()
            v["invites_in"] = [{"from_acc": r[0], "from_name": hub.name_of(r[0])} for r in rows if r[1] == me]
            v["invites_out"] = [hub.name_of(r[1]) for r in rows if r[0] == me]
        return v

    def teammate(self) -> int | None:
        social = getattr(self.rooms, "social", None)
        me = social.pid(self.session) if social is not None else None
        if me is None:
            return None
        others = [p for p in social.party(me) if p != me]
        if not others:
            room = self.rooms.room_of(me)
            others = [p for p in (room.ids() if room else []) if p != me]
        if len(others) != 1:
            return None
        s = next((x for x in list(social.sessions) if social.pid(x) == others[0] and x.account), None)
        return s.account["id"] if s else None

    def act(self, action: str, args: dict) -> tuple[bool, str, int | None]:
        hub, acc = self.hub(), self.session.account
        if hub is None:
            return False, "This server keeps no accounts.", None
        if not acc:
            return False, "Sign in with Discord (Options > Account) to take part in tournaments.", None
        try:
            if action == "create":
                if not hub.may_create(acc):
                    return False, "This server doesn't let you create tournaments.", None
                tid, msg = hub.create(acc["id"], str(args.get("name", "")), int(args.get("best_of", 3)),
                                      int(args.get("max_players", 16)), about=str(args.get("about", "")),
                                      mode=str(args.get("mode", "1v1")),
                                      checkin=int(args.get("checkin", CHECKIN_MINUTES)),
                                      best_of_top8=int(args.get("best_of_top8", 0)),
                                      best_of_final=int(args.get("best_of_final", 0)),
                                      min_rating=int(args.get("min_rating", 0)), max_rating=int(args.get("max_rating", 0)),
                                      losers=int(args.get("losers", 0)),
                                      **{k: args.get(k) for k in MORE})
                return tid is not None, msg, tid
            tid = int(args.get("id"))
            if action == "join":
                ok, msg = hub.join(tid, acc["id"], self.teammate())
            elif action == "leave":
                ok, msg = hub.withdraw(tid, acc["id"])
            elif action == "start":
                ok, msg = hub.start(tid, acc["id"])
            elif action == "cancel":
                ok, msg = hub.cancel(tid, acc["id"])
            elif action == "award":
                ok, msg = hub.award(tid, int(args["round"]), int(args["slot"]), acc["id"], int(args["winner"]))
            elif action == "checkin":
                ok, msg = hub.check_in(tid, acc["id"])
            elif action == "extend":
                ok, msg = hub.extend(tid, int(args["round"]), int(args["slot"]), acc["id"])
            elif action in ("pause", "resume"):
                ok, msg = hub.pause(tid, acc["id"], action == "pause")
            elif action == "undo":
                ok, msg = hub.undo(tid, int(args["round"]), int(args["slot"]), acc["id"])
            elif action == "disqualify":
                ok, msg = hub.disqualify(tid, acc["id"], int(args["account"]))
            elif action == "admin":
                ok, msg = hub.set_admin(tid, acc["id"], str(args.get("name", "")),
                                        int(args["account"]) if args.get("account") is not None else None,
                                        bool(args.get("add", True)))
            elif action == "team_invite":
                ok, msg = hub.team_invite(tid, acc["id"], str(args.get("name", "")))
            elif action == "team_answer":
                ok, msg = hub.team_answer(tid, int(args["inviter"]), acc["id"], bool(args.get("accept")))
            elif action == "seed":
                ok, msg = hub.set_seed(tid, acc["id"], int(args["account"]), int(args["seed"]))
            elif action == "swap_seeds":
                ok, msg = hub.swap_seeds(tid, acc["id"], int(args["a"]), int(args["b"]))
            elif action == "reseed":
                ok, msg = hub.reseed(tid, acc["id"])
            elif action == "score":
                ok, msg = hub.report(tid, int(args["round"]), int(args["slot"]), acc["id"], int(args["wins_a"]),
                                     int(args["wins_b"]))
            elif action == "checkin_for":
                ok, msg = hub.check_in_for(tid, int(args["round"]), int(args["slot"]), acc["id"], int(args["account"]))
            elif action == "settings":
                ok, msg = hub.settings(tid, acc["id"], {k: v for k, v in args.items() if k != "id"})
            else:
                return False, "Unknown action.", None
        except (KeyError, TypeError, ValueError):
            return False, "Those details aren't right.", None
        return ok, msg, tid
