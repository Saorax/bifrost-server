from __future__ import annotations

import os
import random
import re
import time
from pathlib import Path

from . import battlepass, missions, stats
from .bits import BitWriter
from .frame import Packet

HIGHLIGHT = 2926
DAYS = 21

SCHEMA = """
CREATE TABLE IF NOT EXISTS timed_events (event INTEGER PRIMARY KEY, started REAL NOT NULL, ends REAL NOT NULL);
CREATE TABLE IF NOT EXISTS event_dailies (
  player_id INTEGER NOT NULL, event INTEGER NOT NULL, day INTEGER NOT NULL, slot INTEGER NOT NULL,
  mission INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0, state INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (player_id, event, day, slot));
"""
ACTIVE, DONE = missions.ACTIVE, missions.DONE


def load_events(data: Path = missions.GAME_DATA) -> dict[int, dict]:
    try:
        events = (data / "TimedEventTypes.xml").read_text(encoding="utf-8", errors="replace")
        schemes = (data / "ColorSchemeTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    colour_ids = {name: int(cid) for name, cid in re.findall(
        r'<ColorSchemeType ColorSchemeName="([^"]+)"><ColorSchemeID>(\d+)<', schemes)}
    out = {}
    for name, body in re.findall(r'<TimedEventType TimedEventName="([^"]+)">(.*?)</TimedEventType>', events, re.S):
        eid = re.search(r"<TimedEventID>(\d+)<", body)
        if not eid or name == "Template":
            continue
        colour = re.search(r"<WinAndEarnColorScheme>([^<]*)<", body)
        dailies = []
        for _, spec in sorted(re.findall(r"<EventDailyMissionSet(\d+)>([^<]*)<", body), key=lambda t: int(t[0])):
            parts = spec.split(":")
            try:
                dailies.append((parts[0].strip(), int(parts[1])))
            except (IndexError, ValueError):
                continue
        out[int(eid.group(1))] = {"name": name, "colour": colour_ids.get(colour.group(1), 0) if colour else 0,
                                  "dailies": dailies}
    return out


def load_event_missions(data: Path = missions.GAME_DATA) -> dict[int, dict]:
    try:
        text = (data / "MissionTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for name, body in re.findall(r'<MissionType MissionName="([^"]+)">(.*?)</MissionType>', text, re.S):
        if "<Category>EventDaily</Category>" not in body:
            continue
        mid, need, sets = (re.search(rf"<{k}>([^<]*)<", body) for k in ("MissionID", "SuccessCount", "Sets"))
        try:
            out[int(mid.group(1))] = {
                "name": name, "need": max(1, int(need.group(1))),
                "sets": tuple(x.strip() for x in sets.group(1).split(",") if x.strip()) if sets else (),
                "events": tuple(e.strip() for e in re.findall(r"<Event\d+>([^<]*)</Event\d+>", body) if e.strip())}
        except (AttributeError, ValueError):
            continue
    return out


def countable_event(event: str, heroes: dict) -> bool:
    if event in ("kill", "dmg", "place.1", "play"):
        return True
    if not heroes:
        return False
    kind, _, rest = event.partition(".")
    if kind == "heroWeightedPlay":
        return rest in {t for _, ts in heroes.values() for t in ts}
    if kind == "singleMatch" and rest.startswith("KOs."):
        return rest[len("KOs."):].isdigit()
    if kind == "item":
        what, _, weapon = rest.partition(".")
        return rest == "kill.Unarmed" or (what in ("kill", "dmg")
                                          and weapon in {w for ws, _ in heroes.values() for w in ws if w})
    return False


def offered(mission: dict, heroes: dict) -> bool:
    return "Diff" not in mission["name"] and any(countable_event(e, heroes) for e in mission["events"])


def mission_count(mission: dict, facts: dict) -> int:
    single = [e for e in mission["events"] if e.startswith("singleMatch.")]
    rest = sum(battlepass.count(e, facts) for e in mission["events"] if not e.startswith("singleMatch."))
    return rest + int(any(battlepass.count(e, facts) for e in single))


class EventDailies:

    def __init__(self, store, cfg: dict | None = None, events: dict[int, dict] | None = None,
                 table: dict[int, dict] | None = None, rng=random, clock=time.time, heroes: dict | None = None) -> None:
        self.store, self.cfg, self.rng, self.clock = store, cfg or {}, rng, clock
        self.events = EVENTS if events is None else events
        self.table = load_event_missions() if table is None else table
        self.heroes = missions.heroes() if heroes is None else heroes
        with stats.ledger_for(store).lock:
            _db(store)

    def running(self) -> int | None:
        ev = event(self.store, self.cfg, self.clock(), self.events)
        return ev[0] if ev is not None and self.events.get(ev[0], {}).get("dailies") else None

    def today(self, player_id: int) -> list[tuple[int, int, int, int]]:
        eid = self.running()
        if eid is None:
            return []
        day = missions.day_of(self.clock())
        with stats.ledger_for(self.store).lock:
            db = self.store.db
            rows = [tuple(r) for r in db.execute("SELECT slot, mission, progress, state FROM event_dailies WHERE "
                                                 "player_id = ? AND event = ? AND day = ? ORDER BY slot",
                                                 (player_id, eid, day))]
            if rows:
                return rows
            slot = 0
            with db:
                db.execute("DELETE FROM event_dailies WHERE player_id = ? AND day < ?", (player_id, day))
                for set_name, n in self.events[eid]["dailies"]:
                    choices = sorted(m for m, q in self.table.items() if set_name in q["sets"] and offered(q, self.heroes))
                    self.rng.shuffle(choices)
                    for m in choices[:max(0, n)]:
                        db.execute("INSERT INTO event_dailies (player_id, event, day, slot, mission) VALUES (?, ?, ?, ?, ?)",
                                   (player_id, eid, day, slot, m))
                        rows.append((slot, m, 0, ACTIVE))
                        slot += 1
        return rows

    def rows(self, player_id: int) -> list[tuple[int, int, int]]:
        return [(m, p, st) for _, m, p, st in self.today(player_id)]

    def add_match(self, player_id: int, facts: dict) -> tuple[list[int], bool]:
        eid, rows = self.running(), self.today(player_id)
        if eid is None:
            return [], False
        day, done, changed = missions.day_of(self.clock()), [], False
        with stats.ledger_for(self.store).lock, self.store.db:
            for slot, m, progress, state in rows:
                q = self.table.get(m)
                if q is None or state != ACTIVE:
                    continue
                add = mission_count(q, facts)
                if not add:
                    continue
                progress = min(q["need"], progress + add, missions.MAX_PROGRESS)
                state = DONE if progress >= q["need"] else ACTIVE
                if state == DONE:
                    done.append(m)
                changed = True
                self.store.db.execute("UPDATE event_dailies SET progress = ?, state = ? WHERE player_id = ? AND event = ? "
                                      "AND day = ? AND slot = ?", (progress, state, player_id, eid, day, slot))
        return done, changed


EVENTS = load_events()


def resolve(value, events: dict[int, dict] | None = None) -> int | None:
    events = EVENTS if events is None else events
    if value is None or value == "" or value == 0:
        return None
    if isinstance(value, int) or str(value).strip().isdigit():
        n = int(value)
        return n if n in events else None
    want = str(value).strip().lower()
    return next((eid for eid, e in events.items() if e["name"].lower() == want), None)


def configured(cfg: dict | None, events: dict[int, dict] | None = None) -> dict | None:
    c = (cfg or {}).get("timed_event") or {}
    if not isinstance(c, dict):
        c = {"id": c}
    eid = resolve(c.get("id"), events)
    return dict(c, id=eid) if eid is not None else None


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def event(store, cfg: dict | None, now: float | None = None, events: dict[int, dict] | None = None) -> tuple[int, float] | None:
    c = configured(cfg, events)
    if c is None:
        return None
    now = time.time() if now is None else now
    if "ends" in c:
        ends = float(c["ends"])
    elif store is None:
        ends = now + float(c.get("days", DAYS)) * 86400
    else:
        with stats.ledger_for(store).lock:
            db = _db(store)
            row = db.execute("SELECT ends FROM timed_events WHERE event = ?", (c["id"],)).fetchone()
            if row is None:
                ends = now + float(c.get("days", DAYS)) * 86400
                with db:
                    db.execute("INSERT INTO timed_events VALUES (?, ?, ?)", (c["id"], now, ends))
            else:
                ends = row[0]
    return (c["id"], ends) if ends > now else None


def login_fields(store, cfg: dict | None, now: float | None = None) -> tuple[int, int]:
    ev = event(store, cfg, now)
    if ev is None:
        return 0, 0
    now = time.time() if now is None else now
    return ev[0], max(1, min(0xFFFFFFFF, int(ev[1] - now)))


def highlight_packet(hero: int, colour: int) -> Packet:
    return Packet(HIGHLIGHT, BitWriter().uint(hero).uint(colour).bytes())


class Hub:

    def __init__(self, social, store, cfg, log, events: dict[int, dict] | None = None) -> None:
        self.social, self.store, self.cfg, self.log = social, store, cfg or {}, log or (lambda *_: None)
        self.events = EVENTS if events is None else events
        self.dailies = EventDailies(store, self.cfg, self.events) if store is not None else None

    @classmethod
    def of(cls, social, store, cfg, log) -> "Hub":
        hub = getattr(social, "timed_event", None)
        if hub is None or (hub.store is None and store is not None):
            hub = cls(social, store, cfg, log)
            social.timed_event = hub
            daily = missions.Hub.of(social, store, cfg, log)
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
            if hub.dailies is not None and daily.missions is not None:
                daily.missions.extra.append(hub.dailies.rows)
        return hub

    def _sessions(self, player_id: int) -> list:
        return [s for s in list(getattr(self.social, "sessions", ())) if getattr(s, "player_id", None) == player_id]

    def colour(self) -> int:
        ev = event(self.store, self.cfg, events=self.events)
        return self.events[ev[0]]["colour"] if ev is not None else 0

    def on_result(self, entry, verdict, out, rep) -> None:
        if rep is None or self.store is None:
            return
        self.count_dailies(entry, rep)
        colour = self.colour()
        if not colour:
            return
        result = stats.outcomes(rep["players"], rep.get("bots", ()))
        ledger = stats.ledger_for(self.store)
        for p in rep["players"]:
            pid, legend = p["player"], int(p.get("legend") or 0)
            account = (out.get("accounts") or {}).get(pid)
            if result.get(pid) != "win" or not legend or not account:
                continue
            prof = ledger.profile(account)
            if (prof.get("legend") != legend or prof.get("colour") != colour) \
                    and os.environ.get("BH_TIMED_EVENT_ANY_COLOUR") != "1":
                continue
            for s in self._sessions(pid):
                s.send(highlight_packet(legend, colour))
            self.log(f"timed event: player {pid} won with legend {legend} in the event colour {colour} (2926)")

    def count_dailies(self, entry, rep) -> None:
        if self.dailies is None or self.dailies.running() is None:
            return
        daily = getattr(self.social, "missions", None)
        for pid in entry.ids:
            facts = missions.facts_of(rep, pid, entry.playlist, self.dailies.heroes)
            if facts is None:
                continue
            done, changed = self.dailies.add_match(pid, facts)
            if done:
                self.log(f"player {pid} finished event daily mission(s) {', '.join(map(str, done))}")
            if changed and daily is not None:
                for s in self._sessions(pid):
                    daily.send_list(s)
