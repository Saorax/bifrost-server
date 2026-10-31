from __future__ import annotations

import json
import time

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

CRASH, REPORT_PLAYER, MUTE = 87, 89, 90
OPENED_SETTINGS, IDENTITY, SCOREBOARD_SPECTATOR, LOCAL_RESULT = 47, 91, 167, 176
MUTE_STATE = 2470
MUTED, UNMUTED = 1, 2
HANDLED = {CRASH, REPORT_PLAYER, MUTE, OPENED_SETTINGS, IDENTITY, SCOREBOARD_SPECTATOR, LOCAL_RESULT}
MAX_TEXT = 16000
MAX_PER_SESSION = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS client_reports (
  id INTEGER PRIMARY KEY, time REAL NOT NULL, kind TEXT NOT NULL, player_id INTEGER, account_id INTEGER,
  target INTEGER, live INTEGER, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS mutes (player_id INTEGER NOT NULL, target INTEGER NOT NULL, PRIMARY KEY (player_id, target));
"""


def muted_by(store, target: int) -> set[int]:
    if store is None:
        return set()
    with stats.ledger_for(store).lock:
        store.db.ensure(SCHEMA)
        return {r[0] for r in store.db.execute("SELECT player_id FROM mutes WHERE target = ?", (target,))}


def mutes_of(store, players) -> set[int]:
    ids = [int(p) for p in players]
    if store is None or not ids:
        return set()
    with stats.ledger_for(store).lock:
        store.db.ensure(SCHEMA)
        marks = ",".join("?" * len(ids))
        return {r[0] for r in store.db.execute(f"SELECT target FROM mutes WHERE player_id IN ({marks})", ids)}


class Desk:

    def __init__(self, session) -> None:
        self.session = session
        self.count = 0
        self._ready = False

    def _me(self) -> int | None:
        p = self.session.player
        return p["id"] if p else self.session.player_id

    def _db(self):
        store = self.session.store
        if store is None:
            return None
        if not self._ready:
            store.db.executescript(SCHEMA)
            self._ready = True
        return store.db

    def after_login(self) -> None:
        me = self._me()
        if me is None or self.session.store is None:
            return
        with stats.ledger_for(self.session.store).lock:
            targets = [r[0] for r in self._db().execute("SELECT target FROM mutes WHERE player_id = ?", (me,))]
        for t in targets:
            self.session.send(Packet(MUTE_STATE, BitWriter().uint(t).uint(MUTED).bytes()))

    def mute(self, target: int) -> None:
        log, me = self.session.log, self._me()
        if me is None or target == me:
            return
        muted = True
        if self.session.store is not None:
            with stats.ledger_for(self.session.store).lock:
                db = self._db()
                with db:
                    if db.execute("DELETE FROM mutes WHERE player_id = ? AND target = ?", (me, target)).rowcount:
                        muted = False
                    else:
                        db.execute("INSERT INTO mutes (player_id, target) VALUES (?, ?)", (me, target))
        self.session.send(Packet(MUTE_STATE, BitWriter().uint(target).uint(MUTED if muted else UNMUTED).bytes()))
        log(f"player {me} {'muted' if muted else 'unmuted'} player {target} (2470)")

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        log, me = self.session.log, self._me()
        r = BitReader(p.payload)
        if p.type == MUTE:
            _, target = r.uint(), r.uint()
            if r.pos <= len(p.payload) * 8 and me is not None:
                self.mute(target)
            return True
        if p.type == OPENED_SETTINGS:
            log(f"player {me} opened the lobby settings (47)")
            return True
        if p.type == IDENTITY:
            log(f"player {me} sent a platform identity record (91, {len(p.payload)} bytes): official, not kept")
            return True
        if p.type == SCOREBOARD_SPECTATOR:
            log(f"spectator {me} is at a rematch scoreboard (167)")
            return True
        if p.type == LOCAL_RESULT:
            fields = [r.uint() for _ in range(6)]
            if r.pos > len(p.payload) * 8:
                log(f"local match result from player {me} unreadable: ignored")
                return True
            _, ms, mode, level, entities, flagged = fields
            log(f"local match by player {me}: {ms / 1000:.0f} s, mode {mode}, level {level}, {entities} entities (176)")
            self.count += 1
            if self.session.store is not None and self.count <= MAX_PER_SESSION:
                self._keep("local", None, None, json.dumps({"ms": ms, "mode": mode, "level": level,
                                                            "entities": entities, "flagged": flagged}))
            return True
        try:
            if p.type == CRASH:
                text, live, target = r.string()[:MAX_TEXT], int(r.bool()), None
            else:
                reporter, target, text, live = r.uint(), r.uint(), r.string()[:200], None
            if r.pos > len(p.payload) * 8:
                raise IndexError
        except IndexError:
            log(f"client report {p.type} from player {me} unreadable: ignored")
            return True
        if p.type == REPORT_PLAYER and reporter != me:
            log(f"player report from {me} names reporter {reporter}: kept as {me}")
        kind = "crash" if p.type == CRASH else "player"
        first = text.splitlines()[0][:160] if text else ""
        if p.type == CRASH:
            log(f"client error report from player {me} ({'live' if live else 'kept from earlier'}): {first}")
        else:
            log(f"player {me} reported player {target}: {text!r}")
        self.count += 1
        if self.session.store is not None and self.count <= MAX_PER_SESSION:
            self._keep(kind, target, live, text)
        return True

    def _keep(self, kind: str, target, live, text: str) -> None:
        account = self.session.account["id"] if self.session.account else None
        with stats.ledger_for(self.session.store).lock:
            db = self._db()
            with db:
                db.execute("INSERT INTO client_reports (time, kind, player_id, account_id, target, live, text) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?)", (time.time(), kind, self._me(), account, target, live, text))

