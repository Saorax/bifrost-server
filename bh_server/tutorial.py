from __future__ import annotations

from . import stats
from .bits import BitReader
from .frame import Packet

TUTORIAL = 135

SCHEMA = "CREATE TABLE IF NOT EXISTS tutorial (player_id INTEGER PRIMARY KEY, mask INTEGER NOT NULL);"


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def mask(store, player_id: int | None) -> int:
    if store is None or player_id is None:
        return 0
    with stats.ledger_for(store).lock:
        row = _db(store).execute("SELECT mask FROM tutorial WHERE player_id = ?", (player_id,)).fetchone()
    return row[0] if row else 0


def add(store, player_id: int, bits: int) -> int:
    with stats.ledger_for(store).lock:
        db = _db(store)
        with db:
            db.execute("INSERT INTO tutorial VALUES (?, ?) ON CONFLICT(player_id) DO UPDATE SET mask = mask | excluded.mask",
                       (player_id, bits & 0xFFFFFFFF))
            return db.execute("SELECT mask FROM tutorial WHERE player_id = ?", (player_id,)).fetchone()[0]


def handle(session, p: Packet) -> bool:
    if p.type != TUTORIAL:
        return False
    r = BitReader(p.payload)
    try:
        bits = r.uint()
        if r.pos > len(p.payload) * 8:
            raise IndexError
    except IndexError:
        session.log(f"tutorial progress: packet too short ({len(p.payload)} bytes)")
        return True
    me = session.player["id"] if session.player else None
    if session.store is None or me is None:
        return True
    kept = add(session.store, me, bits)
    session.log(f"tutorial progress: player {me} mask {bits:#x}, kept {kept:#x} (135)")
    return True
