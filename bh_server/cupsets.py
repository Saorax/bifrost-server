from __future__ import annotations

import random
import threading
import time

from .bits import BitWriter
from .cups import FLOW, OPEN, RUNNING, Cups
from .frame import Packet

SET_READY, CUP_STATE, SET_SCORE = 2956, 2957, 2959
WAITING = 3
LEVELS = (135, 42, 212, 223, 229, 233, 29, 28)
CHECK_IN = 600.0
ROOM_GAME = [0, 4, 480, 0, 3, 2, 0, 100, 100, 0, 2, 2, 4, 0, 0]
WINS = 2


def _clock_of(session) -> float:
    return getattr(session, "login_t0", None) or time.time()


class CupSets:

    def __init__(self, hub: Cups, rooms, social, log=print, clock=time.time) -> None:
        self.hub, self.rooms, self.social, self.log, self.clock = hub, rooms, social, log, clock
        self.live: dict[tuple, dict] = {}
        self.waiting_told: set = set()
        self.lock = threading.RLock()

    @classmethod
    def serve(cls, rooms, store, cfg: dict | None = None, log=print, timers: bool = True) -> "CupSets | None":
        from . import stats
        from .social import Social
        hub = Cups.of(store, log)
        if hub is None:
            return None
        runner = getattr(rooms, "cupsets", None)
        if runner is None:
            runner = cls(hub, rooms, Social.of(rooms, store, cfg or {}, log), log)
            rooms.cupsets = runner
            stats.results_for(rooms, store, log or (lambda line: None), cfg).listeners.append(runner.on_result)
            if timers:
                runner._schedule()
        return runner

    def _schedule(self) -> None:
        def run():
            try:
                self.tick()
            except Exception as e:
                self.log(f"cups: tick failed: {type(e).__name__}: {e}")
            self._schedule()
        t = threading.Timer(2.0, run)
        t.daemon = True
        t.start()

    def sessions(self, account_id: int) -> list:
        return [s for s in list(self.social.sessions) if s.account and s.account.get("id") == account_id]

    def seed_of(self, cup: int, account_id: int) -> int:
        row = self.hub.db.execute("SELECT seed FROM cup_entrants WHERE cup = ? AND account_id = ?",
                                  (cup, account_id)).fetchone()
        return row[0] if row and row[0] else 99

    @staticmethod
    def fields(key: tuple, s: dict) -> list[int]:
        cup, bracket, sid = key
        to_w, to_l = FLOW[sid]
        return [sid, cup, bracket, 0, WINS, to_w[0] if to_w else 0, to_l[0] if to_l else 0, s["score_a"], s["score_b"]]

    def state_of(self, cup: int) -> int | None:
        row = self.hub.db.execute("SELECT state FROM cups WHERE id = ?", (cup,)).fetchone()
        return row[0] if row else None

    def tick(self) -> None:
        from . import lobby
        hub, now = self.hub, self.clock()
        with self.lock:
            for c in hub.cups():
                if c["state"] == OPEN and c["starts"] <= now:
                    n = hub.db.execute("SELECT COUNT(*) FROM cup_entrants WHERE cup = ?", (c["id"],)).fetchone()[0]
                    if n >= 2:
                        self.log(f"cups: cup {c['id']} starts: {n} players, {hub.start(c['id'])} bracket(s)")
                    else:
                        with hub.lock:
                            hub.db.execute("UPDATE cups SET starts = ?, ends = ? WHERE id = ?",
                                           (now + 3600, now + 4 * 3600, c["id"]))
                            hub.db.commit()
                        self.log(f"cups: cup {c['id']} has {n} player(s): its start moved an hour")
            for c in hub.cups():
                if c["state"] != RUNNING:
                    continue
                brackets = [r[0] for r in hub.db.execute("SELECT DISTINCT bracket FROM cup_sets WHERE cup = ?",
                                                         (c["id"],))]
                for b in brackets:
                    for s in hub.sets(c["id"], b):
                        if not s["winner"] and s["a"] is not None and s["b"] is not None:
                            self.play((c["id"], b, s["set_id"]), s, now, lobby)
                self.tell_waiting(c)
            for key in [k for k in self.live if self.state_of(k[0]) != RUNNING]:
                room = self.live.pop(key).get("room")
                if room is not None:
                    self.rooms.free(room)

    def play(self, key: tuple, s: dict, now: float, lobby) -> None:
        cup, bracket, sid = key
        a, b = s["a"], s["b"]
        live = self.live.get(key)
        if live is None or live.get("room") is None:
            settings = lobby.default_settings(ROOM_GAME, 14, 1)
            room = self.rooms.create_reserved(2, settings, {"key": key, "fields": self.fields(key, s)})
            live = self.live[key] = {"room": room, "since": now, "told": set(), "invited": set(), "in": set()}
            self.log(f"cups: cup {cup} bracket {bracket} set {sid}: room {room.number} for "
                     f"{self.hub.name_of(a)} vs {self.hub.name_of(b)}")
        room = live["room"]
        ids = set(room.ids())
        present = {acc for acc in (a, b) if ids & set(self.hub.player_ids(acc))}
        live["in"] |= present
        for acc in (a, b):
            if acc in present:
                continue
            for ses in self.sessions(acc):
                if id(ses) in live["told"]:
                    continue
                live["told"].add(id(ses))
                deadline = int(live["since"] + CHECK_IN - _clock_of(ses))
                ses.send(Packet(SET_READY, BitWriter().uint(cup).uint(bracket).uint(room.number).uint(sid)
                                .uint(max(1, deadline)).bool(True).bytes()))
                self.log(f"cups: set {sid} ready for {self.hub.name_of(acc)}: room {room.number} (2956)")
        games = s["score_a"] + s["score_b"]
        if present == {a, b} and not room.started and room.members and all(m.ready for m in room.members):
            room.start_level = random.choice(LEVELS)
            started, _seed, why = self.rooms.start(room.host)
            if why is None:
                lobby.send_start(self.rooms, started, room.start_level, self.log)
                for m in started.members:
                    m.ready = 0
                self.log(f"cups: set {sid} game {games + 1} started in room {room.number}")
            elif why != "already started":
                self.log(f"cups: set {sid}: room {room.number} not started ({why})")
        if now - live["since"] > CHECK_IN and not live["in"] >= {a, b}:
            came = [acc for acc in (a, b) if acc in live["in"]]
            if len(came) == 1:
                self.decide(key, WINS if came[0] == a else 0, WINS if came[0] == b else 0, "the other didn't check in")
            elif not came:
                a_wins = self.seed_of(cup, a) <= self.seed_of(cup, b)
                self.decide(key, WINS if a_wins else 0, 0 if a_wins else WINS, "nobody checked in: the higher seed")

    def decide(self, key: tuple, score_a: int, score_b: int, why: str) -> None:
        cup, bracket, sid = key
        self.hub.result(cup, bracket, sid, score_a, score_b)
        live = self.live.pop(key, None)
        if live and live.get("room") is not None:
            self.rooms.free(live["room"])
        self.log(f"cups: cup {cup} bracket {bracket} set {sid}: {score_a}-{score_b} ({why})")

    def tell_waiting(self, c: dict) -> None:
        busy = set()
        for (cup, bracket, sid), live in self.live.items():
            if cup == c["id"]:
                s = next((x for x in self.hub.sets(cup, bracket) if x["set_id"] == sid), None)
                if s:
                    busy |= {s["a"], s["b"]}
        for acc, bracket in self.hub.db.execute("SELECT account_id, bracket FROM cup_entrants WHERE cup = ?",
                                                (c["id"],)).fetchall():
            if acc in busy:
                continue
            for ses in self.sessions(acc):
                key = (c["id"], id(ses), frozenset(busy))
                if key in self.waiting_told:
                    continue
                self.waiting_told.add(key)
                ses.send(Packet(CUP_STATE, BitWriter().uint(WAITING).uint(c["id"]).uint(bracket or 0)
                                .uint(max(1, int(c["ends"] - _clock_of(ses)))).bytes()))

    def on_result(self, entry, verdict: str, out: dict, rep: dict | None) -> None:
        from .stats import COUNTED, outcomes
        if verdict != COUNTED or not rep:
            return
        number = entry.key[0] if getattr(entry, "key", None) else None
        with self.lock:
            hit = next(((k, v) for k, v in self.live.items()
                        if v.get("room") is not None and v["room"].number == number), None)
            if hit is None:
                return
            key, live = hit
            accounts = out.get("accounts") or {}
            winner = next((accounts.get(pid) for pid, r in outcomes(rep["players"]).items() if r == "win"), None)
            cup, bracket, sid = key
            s = next((x for x in self.hub.sets(cup, bracket) if x["set_id"] == sid), None)
            if s is None or winner not in (s["a"], s["b"]):
                return
            sa, sb = s["score_a"] + (winner == s["a"]), s["score_b"] + (winner == s["b"])
            with self.hub.lock:
                self.hub.db.execute("UPDATE cup_sets SET score_a = ?, score_b = ? WHERE cup = ? AND bracket = ? AND "
                                    "set_id = ?", (sa, sb, cup, bracket, sid))
                self.hub.db.commit()
            s.update(score_a=sa, score_b=sb)
            room = live["room"]
            room.cup = dict(room.cup or {}, fields=self.fields(key, s))
            to_w, to_l = FLOW[sid]
            w = BitWriter().uint(sid).byte(0).byte(0).byte(0).byte(sa).byte(sb)
            w.uint(to_w[0] if to_w else 0).uint(to_l[0] if to_l else 0)
            self.rooms.broadcast(room, Packet(SET_SCORE, w.bytes()), spectators=False)
            self.log(f"cups: cup {cup} bracket {bracket} set {sid}: {self.hub.name_of(s['a'])} {sa} - {sb} "
                     f"{self.hub.name_of(s['b'])}")
            if max(sa, sb) >= WINS:
                self.decide(key, sa, sb, "played")
