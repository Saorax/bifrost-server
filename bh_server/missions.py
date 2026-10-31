from __future__ import annotations

import random
import re
import time
from pathlib import Path

from . import gamedata, stats
from .bits import BitReader, BitWriter
from .frame import Packet
from .playlists import BY_ID

LIST, REROLL = 2800, 139
ACTIVE, DONE = 0, 2
DAILY = 3

POOL = {
    1: (5, ("item.kill.Sword", "item.kill.Axe")),
    2: (5, ("item.kill.Pistol", "item.kill.Bow")),
    3: (5, ("item.kill.Cannon", "item.kill.RocketLance")),
    4: (5, ("item.kill.Hammer", "item.kill.Greatsword")),
    5: (5, ("item.kill.Hammer", "item.kill.RocketLance")),
    6: (5, ("item.kill.Scythe", "item.kill.Pistol")),
    7: (5, ("item.kill.Spear", "item.kill.Sword")),
    8: (5, ("item.kill.Orb", "item.kill.Axe")),
    9: (5, ("item.kill.Fists", "item.kill.Katar")),
    10: (5, ("item.kill.Chakram", "item.kill.Orb")),
    11: (5, ("item.kill.Hammer", "item.kill.Spear")),
    12: (5, ("item.kill.Fists", "item.kill.Cannon")),
    13: (4, ("playlistPlay.FFA",)),
    14: (4, ("playlistPlay.2v2Unranked",)),
    15: (4, ("playlistPlay.2v2Ranked",)),
    16: (6, ("play",)),
    17: (4, ("playlistPlay.1v1Ranked",)),
    18: (5, ("playlistPlay.Unranked",)),
    19: (2, ("playlistPlace.FFA.1", "playlistPlace.FFA.2")),
    20: (2, ("playlistPlace.2v2Unranked.1",)),
    21: (2, ("playlistPlace.2v2Ranked.1",)),
    22: (4, ("place.1",)),
    23: (2, ("playlistPlace.1v1Ranked.1",)),
    24: (3, ("playlistPlace.Unranked.1",)),
    25: (15, ("kill",)),
    36: (5, ("item.kill.Sword", "item.kill.Greatsword")),
    37: (5, ("item.kill.Pistol", "item.kill.Katar")),
    38: (5, ("item.kill.Bow", "item.kill.Axe")),
    39: (5, ("item.kill.Boots", "item.kill.Scythe")),
    40: (5, ("item.kill.RocketLance", "item.kill.Spear")),
    41: (5, ("item.kill.Orb", "item.kill.Katar")),
    49: (2, ("heroPlace.SwordUser.1",)),
    50: (2, ("heroPlace.HammerUser.1",)),
    51: (2, ("heroPlace.PistolUser.1",)),
    52: (2, ("heroPlace.LanceUser.1",)),
    53: (2, ("heroPlace.SpearUser.1",)),
    54: (2, ("heroPlace.KatarUser.1",)),
    55: (2, ("heroPlace.AxeUser.1",)),
    56: (2, ("heroPlace.BowUser.1",)),
    57: (2, ("heroPlace.FistsUser.1",)),
    58: (2, ("heroPlace.ScytheUser.1",)),
    59: (2, ("heroPlace.CannonUser.1",)),
    60: (2, ("heroPlace.OrbUser.1",)),
    65: (5, ("item.kill.Scythe", "item.kill.Greatsword")),
    66: (2, ("heroPlace.GreatswordUser.1",)),
    71: (2500, ("dmg",)),
    91: (4, ("playlistPlay.RotatingRanked",)),
    92: (2, ("playlistPlace.RotatingRanked.1",)),
    101: (5, ("item.kill.Cannon", "item.kill.Boots")),
    102: (5, ("item.kill.Fists", "item.kill.Boots")),
    103: (2, ("heroPlace.BootsUser.1",)),
    125: (5, ("item.kill.Chakram", "item.kill.Bow")),
    126: (2, ("heroPlace.ChakramUser.1",)),
}
MAX_PROGRESS = 0xFFFF

GAME_DATA = gamedata.GAME_DATA

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_missions (
  player_id INTEGER NOT NULL, day INTEGER NOT NULL, slot INTEGER NOT NULL, mission INTEGER NOT NULL,
  progress INTEGER NOT NULL DEFAULT 0, state INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (player_id, day, slot));
CREATE TABLE IF NOT EXISTS daily_rerolls (player_id INTEGER NOT NULL, day INTEGER NOT NULL, PRIMARY KEY (player_id, day));
"""

_heroes: dict[int, tuple[tuple[str, ...], frozenset]] | None = None


def heroes(data: Path = GAME_DATA) -> dict[int, tuple[tuple[str, ...], frozenset]]:
    global _heroes
    if _heroes is None or data != GAME_DATA:
        out = {}
        try:
            text = (data / "HeroTypes.xml").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r"<HeroType HeroName=\"[^\"]*\">(.*?)</HeroType>", text, re.S):
                body = m.group(1)

                def col(name: str) -> str:
                    f = re.search(rf"<{name}>([^<]*)</{name}>", body)
                    return f.group(1).strip() if f else ""
                try:
                    hid = int(col("HeroID"))
                except ValueError:
                    continue
                out[hid] = ((col("BaseWeapon1"), col("BaseWeapon2")),
                            frozenset(t.strip() for t in col("MissionTags").split(",") if t.strip()))
        except OSError:
            pass
        if data != GAME_DATA:
            return out
        _heroes = out
    return _heroes


def pool(have_heroes: bool) -> list[int]:
    return [m for m, (_, evs) in POOL.items()
            if have_heroes or not any(e.startswith(("item.kill.", "heroPlace.")) for e in evs)]


def day_of(now: float) -> int:
    return int(now // 86400)


def next_day_at(now: float) -> float:
    return (day_of(now) + 1) * 86400.0


def configured_count(cfg: dict) -> int:
    try:
        return max(0, min(8, int(((cfg or {}).get("missions") or {}).get("daily", DAILY))))
    except (TypeError, ValueError):
        return DAILY


def playlist_tags(playlist: int) -> set[str]:
    pl = BY_ID.get(playlist)
    if playlist == 0 or pl is None:
        return set()
    tags = {pl.name} | ({pl.game_type} if pl.game_type else set())
    if not pl.ranked:
        tags.add("Unranked")
    return tags


def count(event: str, *, place: int, won: bool, kos: int, damage: int, tags: set, hero, detail: dict | None) -> int:
    def placed(k: str) -> bool:
        return won if k == "1" else str(place) == k
    if event == "play":
        return 1
    if event == "place.1":
        return int(won)
    if event == "kill":
        return kos
    if event == "dmg":
        return damage
    kind, _, rest = event.partition(".")
    if kind == "playlistPlay":
        return int(rest in tags)
    if kind == "playlistPlace":
        tag, _, k = rest.rpartition(".")
        return int(tag in tags and placed(k))
    if kind == "heroPlace":
        tag, _, k = rest.rpartition(".")
        return int(hero is not None and tag in hero[1] and placed(k))
    if kind == "item" and rest.startswith("kill.") and hero is not None and detail:
        weapon = rest[len("kill."):]
        return sum(detail.get(f, 0) for w, f in zip(hero[0], ("ko_weapon1", "ko_weapon2")) if w == weapon)
    return 0


class Missions:

    def __init__(self, store, cfg: dict | None = None, rng=random, clock=time.time, data: Path = GAME_DATA) -> None:
        self.store, self.cfg, self.rng, self.clock = store, cfg or {}, rng, clock
        self.heroes = heroes(data)
        self.extra: list = []
        with self.lock:
            self.store.db.executescript(SCHEMA)

    @property
    def lock(self):
        return stats.ledger_for(self.store).lock

    def today(self, player_id: int) -> tuple[int, list[tuple[int, int, int, int]]]:
        day = day_of(self.clock())
        n = configured_count(self.cfg)
        with self.lock:
            db = self.store.db
            rows = [tuple(r) for r in db.execute("SELECT slot, mission, progress, state FROM daily_missions WHERE "
                                                 "player_id = ? AND day = ? ORDER BY slot", (player_id, day))]
            if len(rows) < n:
                held = {r[1] for r in rows}
                choices = [m for m in pool(bool(self.heroes)) if m not in held]
                self.rng.shuffle(choices)
                used = {r[0] for r in rows}
                with db:
                    db.execute("DELETE FROM daily_missions WHERE player_id = ? AND day < ?", (player_id, day))
                    for slot in range(n):
                        if slot not in used and choices:
                            m = choices.pop()
                            db.execute("INSERT INTO daily_missions (player_id, day, slot, mission) VALUES (?, ?, ?, ?)",
                                       (player_id, day, slot, m))
                            rows.append((slot, m, 0, ACTIVE))
                rows.sort()
        return day, rows[:n] if n else []

    def reroll_left(self, player_id: int, day: int) -> bool:
        with self.lock:
            return self.store.db.execute("SELECT 1 FROM daily_rerolls WHERE player_id = ? AND day = ?",
                                         (player_id, day)).fetchone() is None

    def reroll(self, player_id: int, mission: int) -> int | None:
        day, rows = self.today(player_id)
        slot = next((s for s, m, _, st in rows if m == mission and st == ACTIVE), None)
        if slot is None or not self.reroll_left(player_id, day):
            return None
        held = {m for _, m, _, _ in rows}
        choices = [m for m in pool(bool(self.heroes)) if m not in held]
        if not choices:
            return None
        new = self.rng.choice(choices)
        with self.lock, self.store.db:
            self.store.db.execute("UPDATE daily_missions SET mission = ?, progress = 0, state = ? WHERE player_id = ? "
                                  "AND day = ? AND slot = ?", (new, ACTIVE, player_id, day, slot))
            self.store.db.execute("INSERT OR IGNORE INTO daily_rerolls VALUES (?, ?)", (player_id, day))
        return new

    def add_match(self, player_id: int, **facts) -> list[int]:
        day, rows = self.today(player_id)
        done = []
        with self.lock, self.store.db:
            for slot, mission, progress, state in rows:
                if state != ACTIVE or mission not in POOL:
                    continue
                need, events = POOL[mission]
                gained = sum(count(e, **facts) for e in events)
                if not gained:
                    continue
                progress = min(need, progress + gained)
                state = DONE if progress >= need else ACTIVE
                if state == DONE:
                    done.append(mission)
                self.store.db.execute("UPDATE daily_missions SET progress = ?, state = ? WHERE player_id = ? AND day = ? "
                                      "AND slot = ?", (progress, state, player_id, day, slot))
        return done

    def packet(self, player_id: int, since_login: float) -> Packet:
        day, rows = self.today(player_id)
        w = BitWriter().uint(0)
        for _, mission, progress, state in rows:
            w.bool(True).uint(mission).small(state).small(min(progress, MAX_PROGRESS)).uint(0)
        for source in self.extra:
            for mission, progress, state in source(player_id):
                w.bool(True).uint(mission).small(state).small(min(progress, MAX_PROGRESS)).uint(0)
        refresh = max(1, int(since_login + next_day_at(self.clock()) - self.clock()))
        w.bool(False).bool(False).bool(bool(rows) and self.reroll_left(player_id, day)).bool(False).uint(refresh).bool(False)
        return Packet(LIST, w.bytes())


def facts_of(rep: dict, player_id: int, playlist: int, hero_table: dict) -> dict | None:
    p = next((x for x in rep["players"] if x["player"] == player_id), None)
    if p is None:
        return None
    result = stats.outcomes(rep["players"], rep.get("bots", ()))
    return {"place": p["place"], "won": result.get(player_id) == "win", "kos": p["kos"], "damage": p["damage_dealt"],
            "tags": playlist_tags(playlist), "hero": hero_table.get(p["legend"]),
            "detail": (rep.get("detail") or {}).get(player_id)}


class Hub:

    def __init__(self, social, store, cfg, log) -> None:
        self.social, self.cfg, self.log = social, cfg or {}, log or (lambda line: None)
        self.missions = Missions(store, self.cfg) if store is not None else None

    @classmethod
    def of(cls, social, store, cfg, log) -> "Hub":
        hub = getattr(social, "missions", None)
        if hub is None or (hub.missions is None and store is not None):
            hub = cls(social, store, cfg, log)
            social.missions = hub
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
        return hub

    def on(self) -> bool:
        return self.missions is not None and configured_count(self.cfg) > 0

    def _sessions(self, player_id: int) -> list:
        return [s for s in list(getattr(self.social, "sessions", ())) if getattr(s, "player_id", None) == player_id]

    def send_list(self, s) -> None:
        if self.on() and s.player_id is not None:
            s.missions_day = day_of(self.missions.clock())
            s.send(self.missions.packet(s.player_id, time.time() - getattr(s, "missions_t0", time.time())))

    def after_login(self, s) -> None:
        s.missions_t0 = time.time()
        self.send_list(s)

    def handle(self, s, p: Packet) -> bool:
        if p.type != REROLL:
            return False
        if not self.on() or s.player_id is None:
            return True
        r = BitReader(p.payload)
        mission = r.uint()
        if r.pos > len(p.payload) * 8:
            s.log(f"mission reroll: packet too short ({len(p.payload)} bytes)")
            return True
        new = self.missions.reroll(s.player_id, mission)
        s.log(f"daily mission {mission} " + (f"replaced by {new}" if new else "not replaced (not today's, or the "
                                                                           "day's reroll is used)") + " (139)")
        self.send_list(s)
        return True

    def on_result(self, entry, verdict, out, rep) -> None:
        if rep is None or not self.on():
            return
        for pid in entry.ids:
            facts = facts_of(rep, pid, entry.playlist, self.missions.heroes)
            if facts is None:
                continue
            done = self.missions.add_match(pid, **facts)
            if done:
                out.setdefault("missions", {})[pid] = done
                self.log(f"player {pid} finished daily mission(s) {', '.join(map(str, done))}")
            for s in self._sessions(pid):
                self.send_list(s)

    def tick(self, s) -> None:
        if self.on() and s.player_id is not None and getattr(s, "missions_day", None) not in (None, day_of(self.missions.clock())):
            self.send_list(s)
