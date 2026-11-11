from __future__ import annotations

import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from . import missions, picks, stats
from .bits import BitReader, BitWriter
from .frame import Packet
from .playlists import BY_ID
from .store import level_for

UNLOCK, ACK = 2802, 129
MAX_GG = 4096

SCHEMA = """
CREATE TABLE IF NOT EXISTS achievements (
  player_id INTEGER NOT NULL, achievement INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
  done REAL, acked REAL, PRIMARY KEY (player_id, achievement));
"""


@dataclass(frozen=True)
class Achievement:
    id: int
    name: str
    steam: str
    need: int
    events: tuple[str, ...]
    at_once: bool = False
    as_bool: bool = False
    offline: bool = False


def load(data: Path = missions.GAME_DATA) -> dict[int, Achievement]:
    try:
        text = (data / "AchievementTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for m in re.finditer(r'<AchievementType AchievementName="([^"]+)">(.*?)</AchievementType>', text, re.S):
        name, body = m.group(1), m.group(2)
        if name == "--Template--":
            continue

        def col(tag: str) -> str:
            f = re.search(rf"<{tag}>([^<]*)</{tag}>", body)
            return f.group(1).strip() if f else ""
        try:
            aid, need = int(col("AchievementID")), int(col("SuccessCount") or 1)
        except ValueError:
            continue
        events = tuple(e.strip() for e in re.findall(r"<Event\d+>([^<]*)</Event\d+>", body)
                       if e.strip() and not e.strip().startswith("--"))
        out[aid] = Achievement(aid, name, col("SteamLinkageName"), max(1, need), events,
                               col("CompleteAtOnce").upper() == "TRUE", col("CompleteAsBool").upper() == "TRUE",
                               col("TrackOffline").upper() == "TRUE")
    return out


COMBAT = ("place", "kill", "playlist", "playlistPerfect", "multiKill", "tripleHit", "singleMatch", "item", "special",
          "rematch")
NO_PROGRESS_TAG = "BrawlOfTheWeek"
SERIES_HOURS = 6


def match_count(event: str, facts: dict) -> int:
    kind, _, rest = event.partition(".")
    if not facts.get("matchmade", True):
        return 0
    if NO_PROGRESS_TAG in facts["tags"] and kind in COMBAT:
        return 0
    if event == "rematch.WinSeries":
        return int(bool(facts.get("series_won")))
    if event == "rematch.Ranked1v1SwitchWin":
        return int(bool(facts.get("switch_win")))
    if event == "kill.Throw":
        return int((facts.get("detail") or {}).get("ko_thrown", 0))
    if kind == "playlistPerfect":
        return int(rest in facts["tags"] and facts["won"] and not facts.get("falls"))
    if kind == "playlist" and rest.startswith("KOs."):
        n, _, tag = rest[len("KOs."):].partition(".")
        return int(n.isdigit() and tag in facts["tags"] and facts["kos"] >= int(n))
    if kind in ("item", "heroPlace", "playlistPlace", "dmg"):
        return 0
    return missions.count(event, **{k: facts[k] for k in ("place", "won", "kos", "damage", "tags", "hero", "detail")})


def state_met(event: str, account_level: int, legend_levels: dict[int, int], heroes: dict) -> int:
    kind, _, rest = event.partition(".")
    if kind == "accountLevel" and rest.isdigit():
        return int(account_level >= int(rest))
    if kind == "heroLevel":
        tag, _, n = rest.rpartition(".")
        if not n.isdigit():
            return 0
        at = [h for h, lv in legend_levels.items() if lv >= int(n)]
        if not tag:
            return len(at)
        return int(any(tag in heroes.get(h, ((), frozenset()))[1] for h in at))
    return 0


def is_state(a: Achievement) -> bool:
    return a.at_once or a.as_bool


class Achievements:

    def __init__(self, store, table: dict[int, Achievement] | None = None, heroes: dict | None = None,
                 clock=time.time) -> None:
        self.store, self.clock = store, clock
        self.table = load() if table is None else table
        self.heroes = missions.heroes() if heroes is None else heroes
        with self.lock:
            self.store.db.executescript(SCHEMA)

    @property
    def lock(self):
        return stats.ledger_for(self.store).lock

    def rows(self, player_id: int) -> dict[int, tuple[int, float | None, float | None]]:
        with self.lock:
            return {r[0]: (r[1], r[2], r[3]) for r in self.store.db.execute(
                "SELECT achievement, progress, done, acked FROM achievements WHERE player_id = ?", (player_id,))}

    def _levels(self, player_id: int) -> tuple[int, dict[int, int]]:
        ledger = stats.ledger_for(self.store)
        acc = ledger.account_of_player(player_id)
        if acc is None:
            return 0, {}
        with self.lock:
            row = self.store.db.execute("SELECT xp FROM accounts WHERE id = ?", (acc,)).fetchone()
            legends = self.store.db.execute("SELECT legend, xp FROM legend_xp WHERE account_id = ?", (acc,)).fetchall()
        return level_for(row[0] if row else 0), {r[0]: level_for(r[1]) for r in legends}

    def _save(self, player_id: int, changes: dict[int, int]) -> list[int]:
        have = self.rows(player_id)
        now, done = self.clock(), []
        with self.lock, self.store.db:
            for aid, progress in changes.items():
                a = self.table[aid]
                old = have.get(aid, (0, None, None))
                if old[1] is not None:
                    continue
                finished = progress >= a.need
                if finished:
                    done.append(aid)
                self.store.db.execute(
                    "INSERT INTO achievements (player_id, achievement, progress, done) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(player_id, achievement) DO UPDATE SET progress = excluded.progress, done = excluded.done",
                    (player_id, aid, min(progress, a.need), now if finished else None))
        return done

    def check_state(self, player_id: int) -> list[int]:
        account, legends = self._levels(player_id)
        changes = {}
        for a in self.table.values():
            if is_state(a):
                met = [state_met(e, account, legends, self.heroes) for e in a.events]
                n = sum(1 for m in met if m) if a.as_bool else sum(met)
                if n:
                    changes[a.id] = n
        return self._save(player_id, changes)

    def _add(self, player_id: int, gain) -> list[int]:
        have = self.rows(player_id)
        changes = {}
        for a in self.table.values():
            if is_state(a):
                continue
            gained = sum(gain(e) for e in a.events)
            if gained:
                changes[a.id] = have.get(a.id, (0, None, None))[0] + gained
        return self._save(player_id, changes)

    def add_match(self, player_id: int, facts: dict) -> list[int]:
        return self._add(player_id, lambda e: match_count(e, facts)) + self.check_state(player_id)

    def add_event(self, player_id: int, event: str, n: int = 1) -> list[int]:
        return self._add(player_id, lambda e: n if e == event else 0)

    def series_of(self, player_id: int, rooms: list[int], result_id: int | None) -> list[tuple[int, str, int | None]]:
        if not rooms:
            return []
        with self.lock:
            rows = self.store.db.execute(
                "SELECT r.id, rp.outcome, rp.legend FROM results r JOIN result_players rp ON rp.result_id = r.id "
                f"WHERE r.room IN ({','.join('?' * len(rooms))}) AND r.verdict = ? AND rp.player_id = ? AND r.time >= ? "
                "ORDER BY r.id", (*rooms, stats.COUNTED, player_id, self.clock() - SERIES_HOURS * 3600)).fetchall()
        rows = [(r[0], r[1], r[2]) for r in rows]
        if result_id is not None:
            rows = [r for r in rows if r[0] <= result_id]
        return rows

    def acked(self, player_id: int, aid: int) -> bool:
        if aid not in self.table:
            return False
        now = self.clock()
        with self.lock, self.store.db:
            self.store.db.execute(
                "INSERT INTO achievements (player_id, achievement, progress, done, acked) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(player_id, achievement) DO UPDATE SET done = COALESCE(done, excluded.done), "
                "acked = excluded.acked", (player_id, aid, self.table[aid].need, now, now))
        return True

    def unacked(self, player_id: int) -> list[int]:
        return sorted(aid for aid, (_, done, acked) in self.rows(player_id).items()
                      if done is not None and acked is None and aid in self.table and not self.table[aid].offline)


def unlock_packet(aid: int) -> Packet:
    return Packet(UNLOCK, BitWriter().uint(0).uint(aid).bytes())


class Hub:

    def __init__(self, social, store, cfg, log) -> None:
        self.social, self.cfg, self.log = social, cfg or {}, log or (lambda line: None)
        self.achievements = Achievements(store) if store is not None else None
        self.gg: OrderedDict = OrderedDict()

    @classmethod
    def of(cls, social, store, cfg, log) -> "Hub":
        hub = getattr(social, "achievements", None)
        if hub is None or (hub.achievements is None and store is not None):
            hub = cls(social, store, cfg, log)
            social.achievements = hub
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
        return hub

    def on(self) -> bool:
        return self.achievements is not None and bool(self.achievements.table) and \
            ((self.cfg.get("achievements") or {}).get("on", True) is not False)

    def _sessions(self, player_id: int) -> list:
        return [s for s in list(getattr(self.social, "sessions", ())) if getattr(s, "player_id", None) == player_id]

    def _send(self, s, ids) -> None:
        for aid in ids:
            s.send(unlock_packet(aid))
        if ids:
            s.log(f"achievements {', '.join(self.achievements.table[a].name for a in ids)} sent (2802)")

    def after_login(self, s) -> None:
        if not self.on() or s.player_id is None:
            return
        self.achievements.check_state(s.player_id)
        self._send(s, self.achievements.unacked(s.player_id))

    def handle(self, s, p: Packet) -> bool:
        if p.type != ACK:
            return False
        if not self.on() or s.player_id is None:
            return True
        r = BitReader(p.payload)
        aid = r.small()
        if r.pos > len(p.payload) * 8:
            s.log(f"achievement ack: packet too short ({len(p.payload)} bytes)")
            return True
        known = self.achievements.acked(s.player_id, aid)
        name = self.achievements.table[aid].name if known else "unknown"
        s.log(f"achievement {aid} ({name}) unlocked on Steam (129)")
        return True

    def _earned(self, pid: int, done: list[int]) -> None:
        if done:
            self.log(f"player {pid} earned achievement(s) {', '.join(self.achievements.table[a].name for a in done)}")
            for s in self._sessions(pid):
                self._send(s, done)

    def _series_facts(self, entry, out: dict, pid: int) -> dict:
        playlist = BY_ID.get(entry.playlist)
        if playlist is None or playlist.rematch_key is None:
            return {}
        from .lobby import series_rules
        rows = self.achievements.series_of(pid, list(getattr(entry, "series", None) or [entry.key[0]]),
                                           (out or {}).get("id"))
        if len(rows) < 2 or rows[-1][1] != "win":
            return {}
        wins = sum(1 for r in rows if r[1] == "win")
        prev = rows[-2]
        return {"series_won": wins == series_rules(playlist)[0],
                "switch_win": playlist.name == "1v1Ranked" and prev[1] == "loss" and None not in (prev[2], rows[-1][2])
                and prev[2] != rows[-1][2]}

    def on_result(self, entry, verdict, out, rep) -> None:
        if rep is None or not self.on():
            return
        for pid in entry.ids:
            facts = missions.facts_of(rep, pid, entry.playlist, self.achievements.heroes)
            if facts is None:
                continue
            p = next((x for x in rep["players"] if x["player"] == pid), {})
            facts["falls"] = p.get("falls", 0)
            facts["matchmade"] = entry.playlist in BY_ID
            if facts["matchmade"]:
                facts.update(self._series_facts(entry, out, pid))
            self._earned(pid, self.achievements.add_match(pid, facts))

    def said_gg(self, player_id: int, room, emoji: int, mod_items: str | None = None) -> bool:
        if not self.on() or room is None or not room.parties or not room.started:
            return False
        if picks.emoji_category(emoji, mod_items) != picks.GG_CATEGORY:
            return False
        key = (room.number, room.seed, player_id)
        if key in self.gg:
            return False
        self.gg[key] = True
        while len(self.gg) > MAX_GG:
            self.gg.popitem(last=False)
        self._earned(player_id, self.achievements.add_event(player_id, "special.SayGG"))
        return True
