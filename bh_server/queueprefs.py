from __future__ import annotations

import random

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

QUEUE_PREFS = 180
MAX_ROWS = 32

SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_prefs (
  player_id INTEGER NOT NULL, playlist INTEGER NOT NULL, flags INTEGER NOT NULL, ban1 INTEGER NOT NULL,
  ban2 INTEGER NOT NULL, PRIMARY KEY (player_id, playlist));
"""


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def load(store, player_id: int | None) -> list[tuple[int, int, int, int]]:
    if store is None or player_id is None:
        return []
    with stats.ledger_for(store).lock:
        return [tuple(r) for r in _db(store).execute(
            "SELECT playlist, flags, ban1, ban2 FROM queue_prefs WHERE player_id = ? ORDER BY playlist LIMIT ?",
            (player_id, MAX_ROWS))]


def write_rows(w: BitWriter, rows: list[tuple[int, int, int, int]]) -> None:
    for playlist, flags, ban1, ban2 in rows:
        w.bool(True).small(playlist).uint(flags).small(ban1).small(ban2)
    w.bool(False)


def read(payload: bytes) -> tuple[int, int, int, int]:
    r = BitReader(payload)
    row = (r.small(), r.uint(), r.small(), r.small())
    if r.pos > len(payload) * 8:
        raise IndexError("180 too short")
    return row


def save(store, player_id: int, row: tuple[int, int, int, int]) -> None:
    playlist, flags, ban1, ban2 = row
    with stats.ledger_for(store).lock:
        db = _db(store)
        with db:
            db.execute("INSERT INTO queue_prefs VALUES (?, ?, ?, ?, ?) ON CONFLICT(player_id, playlist) DO UPDATE SET "
                       "flags = excluded.flags, ban1 = excluded.ban1, ban2 = excluded.ban2",
                       (player_id, playlist, flags, ban1, ban2))


def bans(store, player_ids, playlist: int) -> set[int]:
    ids = [i for i in player_ids if i]
    if store is None or not ids or not playlist:
        return set()
    with stats.ledger_for(store).lock:
        rows = _db(store).execute(
            f"SELECT ban1, ban2 FROM queue_prefs WHERE playlist = ? AND player_id IN ({','.join('?' * len(ids))})",
            (playlist, *ids)).fetchall()
    return {b for r in rows for b in r if b}


def pick(level: int, candidates: list[int], banned: set[int], rng=random) -> int:
    if level not in banned:
        return level
    free = [c for c in candidates if c and c not in banned]
    return rng.choice(free) if free else level


class Desk:

    def __init__(self, session) -> None:
        self.session = session
        self.count = 0

    def handle(self, p: Packet) -> bool:
        if p.type != QUEUE_PREFS:
            return False
        s = self.session
        me = s.player["id"] if s.player else None
        try:
            row = read(p.payload)
        except IndexError:
            s.log(f"queue preferences: packet too short ({len(p.payload)} bytes)")
            return True
        self.count += 1
        if s.store is None or me is None or self.count > 500:
            s.log(f"queue preferences {row} not kept (no store, player or too many)")
            return True
        save(s.store, me, row)
        playlist, flags, ban1, ban2 = row
        s.log(f"queue preferences: player {me} playlist {playlist} flags {flags} bans {ban1}, {ban2} (180)")
        return True
