from __future__ import annotations

import random
import time

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

JOIN, JOINED, FINAL, STATUS, INFLUENCE = 181, 2921, 2922, 2923, 2924

FACTIONS = {1: (1, 2), 2: (3, 4), 3: (5, 6), 4: (7, 8), 5: (9, 10), 6: (11, 12), 7: (13, 14), 8: (15, 16)}
POINTS = {"win": 1500, "loss": 750, "draw": 1000}
DAYS = 14
RESULTS_SHOWN = 14 * 86400

SCHEMA = """
CREATE TABLE IF NOT EXISTS skirmish_events (skirmish INTEGER PRIMARY KEY, started REAL NOT NULL, ends REAL NOT NULL);
CREATE TABLE IF NOT EXISTS skirmish_members (
  skirmish INTEGER NOT NULL, player_id INTEGER NOT NULL, faction INTEGER NOT NULL, influence INTEGER NOT NULL DEFAULT 0,
  joined REAL NOT NULL, PRIMARY KEY (skirmish, player_id));
"""


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def _lock(store):
    return stats.ledger_for(store).lock


def configured(cfg: dict) -> dict | None:
    c = (cfg or {}).get("skirmish") or {}
    try:
        sid = int(c.get("id", 0))
    except (TypeError, ValueError):
        return None
    return dict(c, id=sid) if sid in FACTIONS else None


def event(store, cfg: dict, now: float | None = None) -> tuple[int, float] | None:
    c = configured(cfg)
    if c is None or store is None:
        return None
    now = time.time() if now is None else now
    with _lock(store):
        db = _db(store)
        row = db.execute("SELECT ends FROM skirmish_events WHERE skirmish = ?", (c["id"],)).fetchone()
        if "ends" in c:
            ends = float(c["ends"])
            if row is None or row[0] != ends:
                with db:
                    db.execute("INSERT INTO skirmish_events VALUES (?, ?, ?) ON CONFLICT(skirmish) DO UPDATE SET "
                               "ends = excluded.ends", (c["id"], now, ends))
        elif row is None:
            ends = now + float(c.get("days", DAYS)) * 86400
            with db:
                db.execute("INSERT INTO skirmish_events VALUES (?, ?, ?)", (c["id"], now, ends))
        else:
            ends = row[0]
    return (c["id"], ends) if ends > now else None


def member(store, sid: int, player_id: int) -> tuple[int, int] | None:
    with _lock(store):
        row = _db(store).execute("SELECT faction, influence FROM skirmish_members WHERE skirmish = ? AND player_id = ?",
                                 (sid, player_id)).fetchone()
    return tuple(row) if row else None


def standings(store, sid: int) -> list[tuple[int, int, int, float]]:
    with _lock(store):
        rows = dict(((f, (n, s)) for f, n, s in _db(store).execute(
            "SELECT faction, COUNT(*), COALESCE(SUM(influence), 0) FROM skirmish_members WHERE skirmish = ? "
            "GROUP BY faction", (sid,))))
    total = sum(s for _, s in rows.values())
    out = []
    for f in FACTIONS[sid]:
        n, s = rows.get(f, (0, 0))
        out.append((f, n, s, s / total if total else 1.0 / len(FACTIONS[sid])))
    return out


def status_packet(store, cfg: dict, player_id: int | None, since_login: float, rng=random) -> Packet:
    ev = event(store, cfg)
    w = BitWriter()
    if ev is None or player_id is None:
        return Packet(STATUS, w.small(0).bytes())
    sid, ends = ev
    w.small(sid).uint(max(1, int(since_login + (ends - time.time()))))
    mine = member(store, sid, player_id)
    rows = standings(store, sid)
    if mine:
        w.small(mine[0]).uint(mine[1])
    else:
        fewest = min(n for _, n, _, _ in rows)
        w.small(0).small(rng.choice([f for f, n, _, _ in rows if n == fewest]))
    for f, _, s, share in rows:
        w.bool(True).small(f).string(f"{s:,}").f32(share)
        if not mine:
            w.uint(0)
    return Packet(STATUS, w.bool(False).bytes())


def final_packet(store, cfg: dict, now: float | None = None) -> Packet | None:
    if store is None:
        return None
    now = time.time() if now is None else now
    with _lock(store):
        row = _db(store).execute("SELECT skirmish FROM skirmish_events WHERE ends <= ? AND ends > ? ORDER BY ends DESC "
                                 "LIMIT 1", (now, now - RESULTS_SHOWN)).fetchone()
    if row is None or row[0] not in FACTIONS:
        return None
    sid = row[0]
    rows = standings(store, sid)
    top = max(s for _, _, s, _ in rows)
    w = BitWriter().small(sid).small(0).uint(0).bool(False)
    for f, _, s, share in rows:
        w.bool(True).small(f).string(f"{s:,}").f32(share).bool(top > 0 and s == top)
    return Packet(FINAL, w.bool(False).bytes())


def join(store, sid: int, player_id: int, faction: int) -> bool:
    if faction not in FACTIONS.get(sid, ()):
        return False
    with _lock(store):
        db = _db(store)
        with db:
            return db.execute("INSERT OR IGNORE INTO skirmish_members (skirmish, player_id, faction, joined) "
                              "VALUES (?, ?, ?, ?)", (sid, player_id, faction, time.time())).rowcount > 0


def add_influence(store, sid: int, player_id: int, points: int) -> int | None:
    with _lock(store):
        db = _db(store)
        with db:
            if db.execute("UPDATE skirmish_members SET influence = influence + ? WHERE skirmish = ? AND player_id = ?",
                          (points, sid, player_id)).rowcount == 0:
                return None
            return db.execute("SELECT influence FROM skirmish_members WHERE skirmish = ? AND player_id = ?",
                              (sid, player_id)).fetchone()[0]


def points_for(cfg: dict, outcome: str | None) -> int:
    c = configured(cfg) or {}
    try:
        return max(0, int(c.get(outcome or "loss", POINTS.get(outcome or "loss", 0))))
    except (TypeError, ValueError):
        return POINTS.get(outcome or "loss", 0)


class Hub:

    def __init__(self, social, store, cfg, log) -> None:
        self.social, self.store, self.cfg, self.log = social, store, cfg or {}, log or (lambda line: None)

    @classmethod
    def of(cls, social, store, cfg, log) -> "Hub":
        hub = getattr(social, "skirmish", None)
        if hub is None or (hub.store is None and store is not None):
            hub = cls(social, store, cfg, log)
            social.skirmish = hub
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
        return hub

    def _sessions(self, player_id: int) -> list:
        return [s for s in list(getattr(self.social, "sessions", ())) if getattr(s, "player_id", None) == player_id]

    def send_status(self, s) -> None:
        s.send(status_packet(self.store, s.cfg, s.player_id, time.time() - getattr(s, "skirmish_t0", time.time())))

    def after_login(self, s) -> None:
        s.skirmish_t0 = time.time()
        if self.store is None:
            return
        pk = final_packet(self.store, s.cfg)
        if pk is not None:
            s.send(pk)
        if event(self.store, s.cfg) is not None:
            self.send_status(s)

    def handle(self, s, p: Packet) -> bool:
        if p.type != JOIN:
            return False
        r = BitReader(p.payload)
        try:
            faction, sid = r.small(), r.small()
            if r.pos > len(p.payload) * 8:
                raise IndexError
        except IndexError:
            s.log(f"skirmish join: packet too short ({len(p.payload)} bytes)")
            return True
        ev = event(self.store, s.cfg) if self.store is not None else None
        if ev is None or ev[0] != sid or s.player_id is None:
            s.log(f"skirmish join to faction {faction} of {sid}: no such event running (181)")
            self.send_status(s)
            return True
        ok = join(self.store, sid, s.player_id, faction)
        s.log(f"skirmish {sid}: player {s.player_id} " + (f"joined faction {faction}" if ok else
              f"asked for faction {faction}: " + ("already in one" if faction in FACTIONS[sid] else "not this event's")) + " (181)")
        s.send(Packet(JOINED, BitWriter().small(sid).small(faction).uint(0).bytes()))
        self.send_status(s)
        return True

    def on_result(self, entry, verdict, out, rep) -> None:
        if rep is None or self.store is None:
            return
        ev = event(self.store, self.cfg)
        if ev is None:
            return
        sid = ev[0]
        result = stats.outcomes(rep["players"], rep.get("bots", ()))
        gained = []
        for pid in entry.ids:
            points = points_for(self.cfg, result.get(pid))
            total = add_influence(self.store, sid, pid, points) if points else None
            if total is not None:
                gained.append((pid, points))
                self.log(f"skirmish {sid}: player {pid} +{points} influence ({result.get(pid)}), now {total}")
        for pid, points in gained:
            for s in self._sessions(pid):
                self.send_status(s)
                s.send(Packet(INFLUENCE, BitWriter().uint(points).uint(1).bytes()))
