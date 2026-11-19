from __future__ import annotations

import threading
import time

from . import playerprefs
from .bits import BitReader, BitWriter
from .frame import V1101, Packet

LIST_ASK, HISTORY_ASK, BRACKET_ASK, REGISTER, UNREGISTER = 188, 191, 192, 193, 194
NOTIFY, UNNOTIFY = 195, 196
LIST, REGISTRATIONS, BRACKETS, BRACKET = 2950, 2953, 2954, 2955
OPEN, RUNNING, DONE = 0, 1, 2
SIZE, DOUBLE = 8, 1
SEED_1V1 = 0
PC_PLATFORM = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS cups (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, state INTEGER NOT NULL, starts REAL NOT NULL, ends REAL NOT NULL,
  seeding INTEGER NOT NULL DEFAULT 0, organizer INTEGER NOT NULL DEFAULT 0, art INTEGER NOT NULL DEFAULT 0,
  created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS cup_entrants (
  cup INTEGER NOT NULL, account_id INTEGER NOT NULL, bracket INTEGER, seed INTEGER, joined REAL NOT NULL,
  PRIMARY KEY (cup, account_id));
CREATE TABLE IF NOT EXISTS cup_sets (
  cup INTEGER NOT NULL, bracket INTEGER NOT NULL, set_id INTEGER NOT NULL, a INTEGER, b INTEGER,
  score_a INTEGER NOT NULL DEFAULT 0, score_b INTEGER NOT NULL DEFAULT 0, winner INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (cup, bracket, set_id));
CREATE TABLE IF NOT EXISTS cup_notify (
  cup INTEGER NOT NULL, account_id INTEGER NOT NULL, PRIMARY KEY (cup, account_id));
"""

SETS = {1: (0, 0, 0), 2: (0, 0, 1), 3: (0, 0, 2), 4: (0, 0, 3), 5: (0, 1, 0), 6: (0, 1, 1), 7: (0, 2, 0),
        8: (1, 0, 0), 9: (1, 0, 1), 10: (1, 1, 0), 11: (1, 1, 1), 12: (1, 2, 0), 13: (1, 3, 0), 14: (2, 0, 0)}
FLOW = {1: ((5, 0), (8, 0)), 2: ((5, 1), (8, 1)), 3: ((6, 0), (9, 0)), 4: ((6, 1), (9, 1)),
        5: ((7, 0), (10, 0)), 6: ((7, 1), (11, 0)), 7: ((14, 0), (13, 0)),
        8: ((11, 1), None), 9: ((10, 1), None), 10: ((12, 0), None), 11: ((12, 1), None), 12: ((13, 1), None),
        13: ((14, 1), None), 14: (None, None)}
FIRST_ROUND = ((1, 8), (4, 5), (2, 7), (3, 6))


class Cups:

    _hubs: dict[int, "Cups"] = {}
    _hubs_lock = threading.Lock()

    def __init__(self, store, log=print, clock=time.time) -> None:
        self.store, self.db, self.log, self.clock = store, store.db, log, clock
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.commit()

    @classmethod
    def of(cls, store, log=print) -> "Cups | None":
        if store is None:
            return None
        with cls._hubs_lock:
            hub = cls._hubs.get(id(store))
            if hub is None:
                hub = cls._hubs[id(store)] = cls(store, log)
            return hub

    def create(self, name: str, state: int, starts: float, ends: float, seeding: int = SEED_1V1, art: int = 0) -> int:
        with self.lock:
            cur = self.db.execute("INSERT INTO cups (name, state, starts, ends, seeding, art, created) VALUES "
                                  "(?, ?, ?, ?, ?, ?, ?)", (name, state, starts, ends, seeding, art, self.clock()))
            self.db.commit()
            return cur.lastrowid

    def cups(self) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM cups ORDER BY state, starts")]

    def registered(self, account_id: int) -> set[int]:
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT cup FROM cup_entrants WHERE account_id = ?", (account_id,))}

    def notified(self, account_id: int) -> set[int]:
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT cup FROM cup_notify WHERE account_id = ?", (account_id,))}

    def notify(self, cup: int, account_id: int, on: bool) -> bool:
        with self.lock:
            if self.db.execute("SELECT 1 FROM cups WHERE id = ?", (cup,)).fetchone() is None:
                return False
            if on:
                self.db.execute("INSERT OR IGNORE INTO cup_notify (cup, account_id) VALUES (?, ?)", (cup, account_id))
            else:
                self.db.execute("DELETE FROM cup_notify WHERE cup = ? AND account_id = ?", (cup, account_id))
            self.db.commit()
            return True

    def bracket_of(self, cup: int, account_id: int | None) -> int:
        if account_id is None:
            return -1
        with self.lock:
            row = self.db.execute("SELECT bracket FROM cup_entrants WHERE cup = ? AND account_id = ?",
                                  (cup, account_id)).fetchone()
        return row[0] if row and row[0] is not None else -1

    def avatar(self, account_id: int) -> int:
        try:
            row = self.db.execute("SELECT avatar FROM profiles WHERE account_id = ?", (account_id,)).fetchone()
            return int(row[0] or 0) if row else 0
        except Exception:
            return 0

    def register(self, cup: int, account_id: int, on: bool) -> bool:
        with self.lock:
            row = self.db.execute("SELECT state FROM cups WHERE id = ?", (cup,)).fetchone()
            if row is None or row[0] != OPEN:
                return False
            if on:
                self.db.execute("INSERT OR IGNORE INTO cup_entrants (cup, account_id, joined) VALUES (?, ?, ?)",
                                (cup, account_id, self.clock()))
            else:
                self.db.execute("DELETE FROM cup_entrants WHERE cup = ? AND account_id = ?", (cup, account_id))
            self.db.commit()
            return True

    def rating(self, account_id: int) -> int:
        try:
            row = self.db.execute("SELECT rating FROM ratings WHERE account_id = ? AND ladder = '1v1'",
                                  (account_id,)).fetchone()
            return int(row[0]) if row else 1000
        except Exception:
            return 1000

    def start(self, cup: int) -> int:
        with self.lock:
            accs = [r[0] for r in self.db.execute("SELECT account_id FROM cup_entrants WHERE cup = ? ORDER BY joined",
                                                  (cup,))]
            accs.sort(key=lambda a: -self.rating(a))
            brackets = max(1, (len(accs) + SIZE - 1) // SIZE)
            for i, acc in enumerate(accs):
                b, seed = i % brackets, i // brackets + 1
                self.db.execute("UPDATE cup_entrants SET bracket = ?, seed = ? WHERE cup = ? AND account_id = ?",
                                (b, seed, cup, acc))
            self.db.execute("DELETE FROM cup_sets WHERE cup = ?", (cup,))
            for b in range(brackets):
                by_seed = {r[1]: r[0] for r in self.db.execute(
                    "SELECT account_id, seed FROM cup_entrants WHERE cup = ? AND bracket = ?", (cup, b))}
                for sid in SETS:
                    self.db.execute("INSERT INTO cup_sets (cup, bracket, set_id) VALUES (?, ?, ?)", (cup, b, sid))
                for sid, (sa, sb) in zip((1, 2, 3, 4), FIRST_ROUND):
                    self.db.execute("UPDATE cup_sets SET a = ?, b = ? WHERE cup = ? AND bracket = ? AND set_id = ?",
                                    (by_seed.get(sa), by_seed.get(sb), cup, b, sid))
            self.db.execute("UPDATE cups SET state = ? WHERE id = ?", (RUNNING, cup))
            self.db.commit()
            for b in range(brackets):
                self._byes_after(cup, b)
            return brackets

    def _byes(self, cup: int, bracket: int) -> None:
        feeders = {}
        for src, dests in FLOW.items():
            for dest in dests:
                if dest is not None:
                    feeders[dest] = src
        changed = True
        while changed:
            changed = False
            by_id = {x["set_id"]: x for x in self.sets(cup, bracket)}
            for sid, s in by_id.items():
                if s["winner"] or (s["a"] is not None and s["b"] is not None):
                    continue
                final = all(feeders.get((sid, side)) is None or by_id[feeders[(sid, side)]]["winner"]
                            for side in (0, 1))
                if final:
                    self.result(cup, bracket, sid, 2 if s["a"] is not None else 0, 2 if s["b"] is not None else 0)
                    changed = True
                    break

    def _byes_after(self, cup: int, bracket: int) -> None:
        if getattr(self, "_settling", False):
            return
        self._settling = True
        try:
            self._byes(cup, bracket)
        finally:
            self._settling = False

    def sets(self, cup: int, bracket: int) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(
                "SELECT * FROM cup_sets WHERE cup = ? AND bracket = ? ORDER BY set_id", (cup, bracket))]

    def result(self, cup: int, bracket: int, set_id: int, score_a: int, score_b: int) -> None:
        with self.lock:
            row = self.db.execute("SELECT a, b FROM cup_sets WHERE cup = ? AND bracket = ? AND set_id = ?",
                                  (cup, bracket, set_id)).fetchone()
            if row is None:
                return
            a, b = row
            winner = 1 if score_a > score_b else 2
            self.db.execute("UPDATE cup_sets SET score_a = ?, score_b = ?, winner = ? WHERE cup = ? AND bracket = ? "
                            "AND set_id = ?", (score_a, score_b, winner, cup, bracket, set_id))
            won, lost = (a, b) if winner == 1 else (b, a)
            to_w, to_l = FLOW[set_id]
            for acc, dest in ((won, to_w), (lost, to_l)):
                if dest is not None:
                    col = "a" if dest[1] == 0 else "b"
                    self.db.execute(f"UPDATE cup_sets SET {col} = ? WHERE cup = ? AND bracket = ? AND set_id = ?",
                                    (acc, cup, bracket, dest[0]))
            self.db.commit()
        self._byes_after(cup, bracket)
        with self.lock:
            open_finals = self.db.execute("SELECT COUNT(*) FROM cup_sets WHERE cup = ? AND set_id = 14 AND winner = 0",
                                          (cup,)).fetchone()[0]
            if not open_finals:
                self.db.execute("UPDATE cups SET state = ? WHERE id = ?", (DONE, cup))
            self.db.commit()

    def placements(self, cup: int, bracket: int) -> list[int]:
        s = {x["set_id"]: x for x in self.sets(cup, bracket)}
        gf, lf = s.get(14), s.get(13)
        out = []
        if gf and gf["winner"]:
            out += [gf["a"], gf["b"]] if gf["winner"] == 1 else [gf["b"], gf["a"]]
        if lf and lf["winner"]:
            out.append(lf["b"] if lf["winner"] == 1 else lf["a"])
        return [x for x in out if x is not None]

    def name_of(self, account_id: int | None) -> str:
        if account_id is None:
            return ""
        row = self.db.execute("SELECT name FROM players WHERE account_id = ? ORDER BY id LIMIT 1", (account_id,)).fetchone()
        if row is None:
            row = self.db.execute("SELECT name FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return row[0] if row else f"player {account_id}"

    def player_ids(self, account_id: int) -> list[int]:
        return [r[0] for r in self.db.execute("SELECT id FROM players WHERE account_id = ?", (account_id,))]

    def list_packet(self, t0: float, region: int = 0) -> Packet:
        w, w11 = BitWriter(), BitWriter()
        for c in self.cups():
            when = c["starts"] if c["state"] == OPEN else c["ends"]
            for out in (w, w11):
                out.bool(True).uint(c["id"]).string(c["name"]).bool(c["organizer"] == 0)
                out.byte(c["state"]).uint(max(1, int(when - t0)) if c["state"] != DONE else 0).uint(0)
                out.byte(0).uint(SIZE).byte(DOUBLE).byte(0).byte(c["seeding"])
                out.uint(0).uint(c["organizer"]).uint(0).uint(0).uint(c["art"])
            w11.uint(region)
        w.bool(False)
        w11.bool(False)
        return Packet(LIST, w.bytes()).since(V1101, Packet(LIST, w11.bytes()))

    def registrations_packet(self, account_id: int | None) -> Packet:
        mine = self.registered(account_id) if account_id is not None else set()
        told = self.notified(account_id) if account_id is not None else set()
        w, w11 = BitWriter(), BitWriter()
        for c in self.cups():
            w.bool(True).uint(c["id"]).bool(c["id"] in mine)
            w11.bool(True).uint(c["id"]).bool(c["id"] in mine).bool(c["id"] in told)
        w.bool(False)
        w11.bool(False)
        return Packet(REGISTRATIONS, w.bytes()).since(V1101, Packet(REGISTRATIONS, w11.bytes()))

    def brackets_packet(self, cup: int, account_id: int | None = None) -> Packet:
        w, w11 = BitWriter(), BitWriter().uint(cup).signed(self.bracket_of(cup, account_id))
        with self.lock:
            row = self.db.execute("SELECT state FROM cups WHERE id = ?", (cup,)).fetchone()
            nums = [r[0] for r in self.db.execute("SELECT DISTINCT bracket FROM cup_entrants WHERE cup = ? AND bracket "
                                                  "IS NOT NULL ORDER BY bracket", (cup,))]
        for b in nums:
            ents = [r[0] for r in self.db.execute("SELECT account_id FROM cup_entrants WHERE cup = ? AND bracket = ? "
                                                  "ORDER BY seed", (cup, b))]
            avg = sum(self.rating(a) for a in ents) // max(1, len(ents))
            done = self.placements(cup, b)
            top = done if len(done) == 3 else ents[:3]
            names = [self.name_of(a) for a in top] + ["", "", ""]
            for out in (w, w11):
                out.bool(True).uint(b).uint(avg).string(names[0]).string(names[1]).string(names[2])
                out.uint(DONE if len(done) == 3 else (row[0] if row else OPEN))
        w.bool(False)
        w11.bool(False)
        return Packet(BRACKETS, w.bytes()).since(V1101, Packet(BRACKETS, w11.bytes()))

    def bracket_packet(self, cup: int, bracket: int, mine: bool = False) -> Packet:
        ents = {r[0]: r[1] for r in self.db.execute("SELECT account_id, seed FROM cup_entrants WHERE cup = ? AND "
                                                     "bracket = ?", (cup, bracket))}
        avg = sum(self.rating(a) for a in ents) // max(1, len(ents))
        sets = self.sets(cup, bracket)
        state = DONE if len(self.placements(cup, bracket)) == 3 else RUNNING
        w = BitWriter().uint(bracket).uint(avg).uint(state)
        w11 = BitWriter().bool(mine).uint(cup).uint(bracket).uint(avg).uint(state).uint(DOUBLE).uint(SIZE)
        for s in sets:
            if s["a"] is None and s["b"] is None:
                continue
            sub, rnd, pos = SETS[s["set_id"]]
            for out in (w, w11):
                out.bool(True).uint(s["set_id"]).uint(sub).uint(rnd).uint(pos).uint(1 if s["winner"] else 0)
                out.uint(s["score_a"]).uint(s["score_b"]).uint(0)
            for acc in (s["a"], s["b"]):
                if acc is None:
                    w.bool(False)
                    w11.bool(not s["winner"]).bool(bool(s["winner"]))
                    continue
                name, pids = self.name_of(acc), self.player_ids(acc)
                w.bool(True).uint(acc)
                w11.bool(False).bool(False).signed(acc)
                for pid in pids:
                    w.bool(True).uint(pid)
                    w11.bool(True).uint(pid).small(PC_PLATFORM).string(name)
                w.bool(False).string(name).uint(ents.get(acc) or 0)
                w11.bool(False).string(name).uint(ents.get(acc) or 0).uint(self.avatar(acc))
            w.uint(s["winner"])
            won = {1: s["a"], 2: s["b"]}.get(s["winner"])
            w11.signed(won if won is not None else -1)
        w.bool(False)
        w11.bool(False)
        return Packet(BRACKET, w.bytes()).since(V1101, Packet(BRACKET, w11.bytes()))


class Desk:

    def __init__(self, session) -> None:
        self.session = session

    def hub(self) -> Cups | None:
        return Cups.of(self.session.store, self.session.log)

    def handle(self, p: Packet) -> bool:
        s = self.session
        v1101 = getattr(s, "client_version", "") >= V1101
        if p.type not in (LIST_ASK, HISTORY_ASK, BRACKET_ASK, REGISTER, UNREGISTER) and not (
                v1101 and p.type in (NOTIFY, UNNOTIFY)):
            return False
        hub = self.hub()
        if hub is None:
            return True
        acc = s.account["id"] if s.account else None
        t0 = getattr(s, "login_t0", None) or time.time()
        try:
            r = BitReader(p.payload)
            if p.type == LIST_ASK:
                region = playerprefs.load(s.store, getattr(s, "player_id", None))["region"] if v1101 else 0
                s.send(hub.list_packet(t0, region))
                s.send(hub.registrations_packet(acc))
                s.log(f"cups: list sent ({len(hub.cups())} cups; 2950, 2953)")
            elif p.type in (REGISTER, UNREGISTER):
                cup = r.uint()
                ok = acc is not None and hub.register(cup, acc, p.type == REGISTER)
                s.send(hub.registrations_packet(acc))
                s.log(f"cups: {'register' if p.type == REGISTER else 'unregister'} cup {cup} "
                      f"{'ok' if ok else 'refused (not open or no account)'} (2953)")
            elif p.type in (NOTIFY, UNNOTIFY):
                cup = r.uint()
                ok = acc is not None and hub.notify(cup, acc, p.type == NOTIFY)
                s.send(hub.registrations_packet(acc))
                s.log(f"cups: {'notify' if p.type == NOTIFY else 'stop notifying'} cup {cup} "
                      f"{'ok' if ok else 'refused (no such cup or no account)'} (2953)")
            elif p.type == HISTORY_ASK:
                cup = r.uint()
                s.send(hub.brackets_packet(cup, acc))
                s.log(f"cups: brackets of cup {cup} sent (2954)")
            else:
                cup, bracket = r.uint(), r.uint()
                mine = r.bool() if v1101 else False
                s.send(hub.bracket_packet(cup, bracket, mine))
                s.log(f"cups: cup {cup} bracket {bracket} sent (2955)")
        except IndexError:
            s.log(f"cups: packet {p.type} too short ({len(p.payload)} bytes)")
        return True
