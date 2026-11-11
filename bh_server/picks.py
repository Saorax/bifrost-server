from __future__ import annotations

import json
import re
from pathlib import Path

from . import gamedata, stats
from .bits import BitReader, BitWriter
from .frame import Packet

SNAPSHOT = 11021
LOADOUT, INVENTORY = 2471, 2901
TAUNT_SLOT, AVATAR, TITLE, EMOJI = 62, 64, 66, 166
SETS = [("koEffect", 60, "var_6605"), ("sidekick", 59, "var_3827"), ("frame", 126, "var_13887"),
        ("theme", 61, "var_9090"), ("podium", 65, "var_5174"), ("victory", 63, "var_3278"), ("defeat", 185, "var_4493")]
SET_OF = {t: name for name, t, _ in SETS}
INVENTORY_ORDER = ["koEffect", "theme", "frame", "victory", "defeat", "sidekick", "podium"]
OWNERSHIP_SETS = 14
HANDLED = {SNAPSHOT, TAUNT_SLOT, AVATAR, TITLE, EMOJI, *SET_OF}
MAX_WORDS = 64
EMOJI_CATEGORIES = ["Wait", "Laugh", "Wow", "Smart", "Rage", "BRB", "Wave", "GG", "WP", "RIP", "Think", "Shrug",
                    "ThumbsUp", "ThumbsDown", "Facepalm", "OneMore", "Cry", "Heart", "Sweat", "LookingGood"]
GG_CATEGORY = EMOJI_CATEGORIES.index("GG")
MAX_EMOJI = 0xFFFF
GAME_DATA = gamedata.GAME_DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_picks (owner TEXT PRIMARY KEY, data TEXT NOT NULL);
"""

_emoji_slots: dict[int, int] | None = None


def emoji_slots(data: Path = GAME_DATA) -> dict[int, int]:
    global _emoji_slots
    if _emoji_slots is None:
        out: dict[int, int] = {}
        try:
            text = (data / "EmojiTypes.xml").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"<EmojiID>(\d+)</EmojiID>.*?<Category>([^<]*)</Category>", text, re.S):
                if m.group(2) in EMOJI_CATEGORIES:
                    out[int(m.group(1))] = EMOJI_CATEGORIES.index(m.group(2))
        except OSError:
            pass
        _emoji_slots = out
    return _emoji_slots


def mod_emoji_fallback(emoji: int, mod_items: str | None) -> int | None:
    if not mod_items:
        return None
    try:
        rows = json.loads(mod_items)
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    for row in rows:
        if (isinstance(row, list) and len(row) >= 4 and row[0] == "EmojiTypes" and row[1] == emoji
                and isinstance(row[3], int)):
            return row[3]
    return None


def emoji_category(emoji: int, mod_items: str | None = None) -> int | None:
    if not 0 < emoji <= MAX_EMOJI:
        return None
    slot = emoji_slots().get(emoji)
    if slot is None:
        stand_in = mod_emoji_fallback(emoji, mod_items)
        slot = emoji_slots().get(stand_in) if stand_in is not None else None
    return slot


def empty() -> dict:
    return {"avatar": 0, "title": 0, "taunts": [0] * 8, "emojis": [0] * 20, "sets": {name: [] for name, _, _ in SETS}}


def read_snapshot(payload: bytes) -> dict:
    r = BitReader(payload)
    out = {"avatar": r.uint(), "title": r.uint(), "taunts": [r.uint() for _ in range(8)],
           "emojis": [r.uint() for _ in range(20)], "sets": {}}
    for name, _, _ in SETS:
        n = r.uint()
        if n > MAX_WORDS:
            raise IndexError(f"set {name}: {n} words")
        out["sets"][name] = [r.uint() for _ in range(n)]
    if r.pos > len(payload) * 8:
        raise IndexError("11021 too short")
    return out


def write_snapshot(picks: dict) -> bytes:
    w = BitWriter().uint(picks["avatar"]).uint(picks["title"])
    for v in picks["taunts"]:
        w.uint(v)
    for v in picks["emojis"]:
        w.uint(v)
    for name, _, _ in SETS:
        words = picks["sets"].get(name, [])
        w.uint(len(words))
        for v in words:
            w.uint(v)
    return w.bytes()


def set_bit(words: list[int], item: int, on: bool) -> list[int]:
    words = list(words)
    i, bit = item // 32, 1 << (item % 32)
    if i >= MAX_WORDS:
        return words
    if on:
        while len(words) <= i:
            words.append(0)
        words[i] |= bit
    elif i < len(words):
        words[i] &= ~bit & 0xFFFFFFFF
    return words


def apply(picks: dict, t: int, payload: bytes, mod_items: str | None = None) -> str | None:
    r = BitReader(payload)
    if t in SET_OF:
        item, exclusive, selected = r.uint(), r.bool(), r.bool()
        name = SET_OF[t]
        if exclusive:
            picks["sets"][name] = set_bit([], item, True)
        else:
            picks["sets"][name] = set_bit(picks["sets"].get(name, []), item, selected)
        return f"{name} {item} " + ("only" if exclusive else "on" if selected else "off")
    if t == TAUNT_SLOT:
        slot, taunt = r.uint(), r.uint()
        if slot >= 8:
            return None
        picks["taunts"][slot] = taunt
        return f"emote slot {slot} = {taunt}"
    if t == AVATAR:
        avatar, for_clan = r.uint(), r.bool()
        if for_clan:
            return None
        picks["avatar"] = avatar
        return f"avatar {avatar}"
    if t == TITLE:
        picks["title"] = r.small()
        return f"title {picks['title']}"
    if t == EMOJI:
        emoji = r.uint()
        slot = emoji_category(emoji, mod_items)
        if slot is None:
            return None
        picks["emojis"][slot] = emoji
        return f"emoji slot {slot} = {emoji}"
    return None


def _words(w: BitWriter, words: list[int]) -> None:
    words = list(words) + ([0] if len(words) % 2 else [])
    for i in range(0, len(words), 2):
        w.bool(True).uint(words[i]).uint(words[i + 1])
    w.bool(False)


def loadout_packet(picks: dict, account_key: str) -> Packet:
    _, _, first_next = stats.level_span(0)
    w = BitWriter().string(account_key).uint(first_next).uint(0).bool(False)
    for v in picks["taunts"]:
        w.uint(v)
    for v in picks["emojis"]:
        w.uint(v)
    w.uint(picks["avatar"]).uint(picks["title"])
    return Packet(LOADOUT, w.bytes())


def inventory_packet(picks: dict, legends: list[tuple[int, int]]) -> Packet:
    w = BitWriter().uint(0)
    for _ in range(OWNERSHIP_SETS):
        _words(w, [])
    for name in INVENTORY_ORDER:
        _words(w, picks["sets"].get(name, []))
    w.bool(False).bool(False)
    for hero, xp in legends:
        level, start, nxt = stats.level_span(xp)
        w.bool(True).uint(hero).uint(level).uint(xp).uint(start).uint(nxt).uint(0).uint(0).bool(False)
    w.bool(False)
    w.bool(False)
    return Packet(INVENTORY, w.bytes())


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


class Desk:

    def __init__(self, session) -> None:
        self.session = session
        self.count = 0

    def owner(self) -> str | None:
        s = self.session
        if s.account:
            return f"a:{s.account['id']}"
        return f"p:{s.player['id']}" if s.player else None

    def load(self) -> dict | None:
        s, key = self.session, self.owner()
        if s.store is None or key is None:
            return None
        with stats.ledger_for(s.store).lock:
            row = _db(s.store).execute("SELECT data FROM profile_picks WHERE owner = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            picks = json.loads(row[0])
        except ValueError:
            return None
        base = empty()
        base.update({k: picks[k] for k in base if k in picks})
        base["sets"] = {name: list(picks.get("sets", {}).get(name, [])) for name, _, _ in SETS}
        return base

    def save(self, picks: dict) -> None:
        s, key = self.session, self.owner()
        with stats.ledger_for(s.store).lock:
            db = _db(s.store)
            with db:
                db.execute("INSERT INTO profile_picks VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET data = excluded.data",
                           (key, json.dumps(picks, separators=(",", ":"))))

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        s = self.session
        if s.store is None or self.owner() is None:
            return p.type == SNAPSHOT
        self.count += 1
        if self.count > 5000:
            return p.type == SNAPSHOT
        try:
            if p.type == SNAPSHOT:
                picks = read_snapshot(p.payload)
                self.save(picks)
                s.log(f"profile picks from player {s.player['id']} kept (avatar {picks['avatar']}, title {picks['title']})")
                return True
            picks = self.load()
            if picks is None:
                return False
            rooms = getattr(getattr(s, "lobby", None), "rooms", None)
            mod_items = getattr(rooms, "mod_items", {}).get(getattr(s, "player_id", None)) if p.type == EMOJI else None
            what = apply(picks, p.type, p.payload, mod_items)
            if what is None:
                return False
            self.save(picks)
            s.log(f"profile pick of player {s.player['id']}: {what}")
        except IndexError:
            s.log(f"profile pick {p.type}: packet too short ({len(p.payload)} bytes)")
        return p.type != AVATAR

    def after_login(self) -> None:
        s = self.session
        picks = self.load()
        if picks is None:
            return
        legends: list[tuple[int, int]] = []
        if s.account:
            ledger = stats.ledger_for(s.store)
            with ledger.lock:
                legends = [(r["legend"], r["xp"]) for r in ledger.db.execute(
                    "SELECT legend, xp FROM legend_xp WHERE account_id = ? ORDER BY legend", (s.account["id"],))]
        s.send(loadout_packet(picks, str(s.player["id"])))
        s.send(inventory_packet(picks, legends))
        s.log(f"profile picks for player {s.player['id']} sent (2471, 2901; {len(legends)} legend levels)")
