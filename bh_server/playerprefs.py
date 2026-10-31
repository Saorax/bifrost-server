from __future__ import annotations

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

SET_REGION, SET_SERVERS, AGE = 96, 97, 164
REGION_SET = 2482
HANDLED = {SET_REGION, SET_SERVERS, AGE}

SCHEMA = """CREATE TABLE IF NOT EXISTS player_prefs (
  player_id INTEGER PRIMARY KEY, region INTEGER NOT NULL DEFAULT 0, servers INTEGER NOT NULL DEFAULT 0,
  age INTEGER NOT NULL DEFAULT 0);"""


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def load(store, player_id: int | None) -> dict:
    out = {"region": 0, "servers": 0, "age": 0}
    if store is None or player_id is None:
        return out
    with stats.ledger_for(store).lock:
        row = _db(store).execute("SELECT region, servers, age FROM player_prefs WHERE player_id = ?",
                                 (player_id,)).fetchone()
    if row:
        out.update(region=row[0], servers=row[1], age=row[2])
    return out


def _save(store, player_id: int, column: str, value: int) -> dict:
    assert column in ("region", "servers", "age")
    with stats.ledger_for(store).lock:
        db = _db(store)
        with db:
            db.execute(f"INSERT INTO player_prefs (player_id, {column}) VALUES (?, ?) "
                       f"ON CONFLICT(player_id) DO UPDATE SET {column} = excluded.{column}", (player_id, value))
    return load(store, player_id)


def region_packet(region: int, servers: int) -> Packet:
    return Packet(REGION_SET, BitWriter().uint(region).uint(servers).bytes())


def handle(session, p: Packet) -> bool:
    if p.type not in HANDLED:
        return False
    r = BitReader(p.payload)
    try:
        value = r.byte() if p.type == AGE else r.uint()
        if r.pos > len(p.payload) * 8:
            raise IndexError
    except IndexError:
        session.log(f"player setting {p.type}: packet too short ({len(p.payload)} bytes)")
        return True
    me = session.player["id"] if session.player else None
    if session.store is None or me is None:
        session.log(f"player setting {p.type} before login: ignored")
        return True
    if p.type == AGE:
        if not 1 <= value <= 85:
            session.log(f"age answer {value} from player {me}: out of range, ignored (164)")
            return True
        _save(session.store, me, "age", value)
        session.log(f"player {me} answered the age prompt (164)")
        return True
    kept = _save(session.store, me, "region" if p.type == SET_REGION else "servers", value & 0xFFFFFFFF)
    session.send(region_packet(kept["region"], kept["servers"]))
    session.log(f"player {me}: region {kept['region']}, servers {kept['servers']:#x} ({p.type}; sent 2482)")
    return True
