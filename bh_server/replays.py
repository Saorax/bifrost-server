from __future__ import annotations

import hashlib
import json
import re
import secrets
import threading
import time
import zlib

from .bits import BitReader, BitWriter
from .frame import Packet

PART, STORED, GET, DATA, MODS = 11060, 11061, 11062, 11063, 11064
SHARE, SHARED, GET_CODE = 11065, 11066, 11067
HANDLED = {PART, GET, SHARE, GET_CODE}
SHARES_KEPT = 2000
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 6
VERSION = 2
MAX_MODS = 60
PART_SIZE = 48 * 1024
MAX_REPLAY = 8 * 1024 * 1024
MAX_INFLATED = 64 * 1024 * 1024
KEEP = 2000
GAME_VERSION = "10.11"

SCHEMA = """
CREATE TABLE IF NOT EXISTS replays (
  room INTEGER NOT NULL, seed INTEGER NOT NULL, time REAL NOT NULL, uploader INTEGER NOT NULL, size INTEGER NOT NULL,
  sha256 TEXT NOT NULL, map TEXT NOT NULL DEFAULT '', copies INTEGER NOT NULL DEFAULT 1, differing INTEGER NOT NULL DEFAULT 0,
  data BLOB NOT NULL, mods TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (room, seed));
CREATE TABLE IF NOT EXISTS replay_shares (
  code TEXT PRIMARY KEY, time REAL NOT NULL, uploader INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
  name TEXT NOT NULL, data BLOB NOT NULL, mods TEXT NOT NULL DEFAULT '[]');
"""


def read_bytes(r: BitReader, n: int) -> bytes:
    start = r.pos
    if start + n * 8 > len(r.data) * 8:
        raise IndexError
    shift = start & 7
    lo = start >> 3
    if shift == 0:
        out = bytes(r.data[lo:lo + n])
    else:
        chunk = r.data[lo:lo + n + 1]
        v = int.from_bytes(chunk, "big")
        v >>= len(chunk) * 8 - shift - n * 8
        out = (v & ((1 << (n * 8)) - 1)).to_bytes(n, "big")
    r.pos += n * 8
    return out


def write_bytes(w: BitWriter, data: bytes) -> BitWriter:
    for b in data:
        w.bits_(8, b)
    return w


def read_part(payload: bytes) -> dict:
    r = BitReader(payload)
    try:
        version = r.uint()
        if version not in (1, VERSION):
            raise ValueError(f"replay part version {version}, expected {VERSION}")
        part = {"room": r.uint(), "seed": r.uint(), "total": r.uint(), "offset": r.uint()}
        n = r.uint()
        if n > PART_SIZE:
            raise ValueError(f"part of {n} bytes")
        part["data"] = read_bytes(r, n)
        part["mods"] = read_mods(r) if version >= 2 else []
        if r.pos > len(payload) * 8:
            raise IndexError
    except IndexError:
        raise ValueError("replay part too short") from None
    if r.left() >= 8:
        raise ValueError("bytes left over")
    return part


def read_share_part(payload: bytes) -> dict:
    r = BitReader(payload)
    try:
        version = r.uint()
        if version != VERSION:
            raise ValueError(f"share part version {version}, expected {VERSION}")
        part = {"nonce": r.uint(), "total": r.uint(), "offset": r.uint()}
        n = r.uint()
        if n > PART_SIZE:
            raise ValueError(f"part of {n} bytes")
        part["data"] = read_bytes(r, n)
        part["mods"] = read_mods(r)
        part["name"] = r.string()[:120]
        if r.pos > len(payload) * 8:
            raise IndexError
    except IndexError:
        raise ValueError("share part too short") from None
    if r.left() >= 8:
        raise ValueError("bytes left over")
    return part


def write_share_part(nonce: int, total: int, offset: int, data: bytes, mods: list[str], name: str) -> bytes:
    w = BitWriter().uint(VERSION).uint(nonce).uint(total).uint(offset).uint(len(data))
    return write_mods(write_bytes(w, data), mods).string(name).bytes()


SHARE_NAME = re.compile(r"^\[([0-9.]+)\] (.*?)(?: \(\d+\))?(?: \((?:shared|code) [^)]*\))?\.replay$")


def share_name(uploaded: str, code: str) -> str:
    m = SHARE_NAME.match(uploaded or "")
    version = m.group(1) if m else GAME_VERSION
    base = FILE_SAFE.sub("", (m.group(2) if m else "Unknown").replace(" ", ""))[:40] or "Unknown"
    return f"[{version}] {base} (code {code}).replay"


def new_code() -> str:
    return "".join(secrets.choice(CODE_CHARS) for _ in range(CODE_LEN))


def read_mods(r: BitReader) -> list[str]:
    m = r.uint()
    if m > MAX_MODS:
        raise ValueError(f"{m} mods")
    return [r.string()[:200] for _ in range(m)]


def write_mods(w: BitWriter, mods: list[str]) -> BitWriter:
    w.uint(len(mods))
    for e in mods:
        w.string(e)
    return w


def write_part(room: int, seed: int, total: int, offset: int, data: bytes, mods: list[str] | None = None) -> bytes:
    w = BitWriter().uint(VERSION).uint(room).uint(seed).uint(total).uint(offset).uint(len(data))
    return write_mods(write_bytes(w, data), mods or []).bytes()


def data_packets(room: int, seed: int, blob: bytes | None, name: str, mods: list[str] | None = None) -> list[Packet]:
    if not blob:
        return [Packet(DATA, write_mods(BitWriter().uint(room).uint(seed).uint(0).uint(0).uint(0).string(""), []).bytes())]
    out = []
    for off in range(0, len(blob), PART_SIZE):
        piece = blob[off:off + PART_SIZE]
        w = BitWriter().uint(room).uint(seed).uint(len(blob)).uint(off).uint(len(piece))
        out.append(Packet(DATA, write_mods(write_bytes(w, piece).string(name), mods or []).bytes()))
    return out


def mods_packet(room: int, seed: int, web: int, ticket: str, entries: list[str]) -> Packet:
    w = BitWriter().uint(room).uint(seed).uint(web).string(ticket)
    return Packet(MODS, write_mods(w, entries).bytes())


def read_mods_packet(payload: bytes) -> dict:
    r = BitReader(payload)
    return {"room": r.uint(), "seed": r.uint(), "web": r.uint(), "ticket": r.string(), "mods": read_mods(r)}


def read_data(payload: bytes) -> dict:
    r = BitReader(payload)
    d = {"room": r.uint(), "seed": r.uint(), "total": r.uint(), "offset": r.uint()}
    d["data"] = read_bytes(r, r.uint())
    d["name"] = r.string()
    d["mods"] = read_mods(r)
    return d


def check(blob: bytes) -> str | None:
    if len(blob) < 8:
        return "too short"
    d = zlib.decompressobj()
    try:
        out = d.decompress(blob, MAX_INFLATED + 1)
    except zlib.error:
        return "not a replay (not compressed data)"
    if len(out) > MAX_INFLATED or d.unconsumed_tail:
        return "too large once unpacked"
    if not d.eof:
        return "cut short"
    return None


FILE_SAFE = re.compile(r"[^A-Za-z0-9_-]+")


def file_name(map_name: str, room: int, seed: int) -> str:
    m = FILE_SAFE.sub("", (map_name or "Unknown").replace(" ", ""))[:40] or "Unknown"
    return f"[{GAME_VERSION}] {m} (shared {room}-{seed}).replay"


class Shelf:

    def __init__(self, store, keep: int = KEEP) -> None:
        self.db, self.keep = store.db, keep
        self.lock = threading.Lock()
        with self.lock:
            self.db.executescript(SCHEMA)
            cols = {row[1] for row in self.db.execute("PRAGMA table_info(replays)")}
            if "mods" not in cols:
                self.db.execute("ALTER TABLE replays ADD COLUMN mods TEXT NOT NULL DEFAULT '[]'")

    def share(self, uploader: int, blob: bytes, name: str, mods: list[str] | None, keep: int = SHARES_KEPT) -> str:
        sha = hashlib.sha256(blob).hexdigest()
        with self.lock, self.db:
            row = self.db.execute("SELECT code FROM replay_shares WHERE sha256 = ?", (sha,)).fetchone()
            if row is not None:
                self.db.execute("UPDATE replay_shares SET time = ? WHERE code = ?", (time.time(), row["code"]))
                return row["code"]
            code = new_code()
            while self.db.execute("SELECT 1 FROM replay_shares WHERE code = ?", (code,)).fetchone() is not None:
                code = new_code()
            self.db.execute("INSERT INTO replay_shares (code, time, uploader, size, sha256, name, data, mods) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (code, time.time(), uploader, len(blob), sha, share_name(name, code), blob, json.dumps(mods or [])))
            extra = self.db.execute("SELECT COUNT(*) FROM replay_shares").fetchone()[0] - keep
            if extra > 0:
                self.db.execute("DELETE FROM replay_shares WHERE code IN (SELECT code FROM replay_shares ORDER BY time LIMIT ?)",
                                (extra,))
        return code

    def shared(self, code: str) -> dict | None:
        row = self.db.execute("SELECT code, name, size, data, mods FROM replay_shares WHERE code = ?",
                              ((code or "").strip().upper(),)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["mods"] = json.loads(d["mods"] or "[]")
        return d

    def has(self, room: int, seed: int) -> bool:
        return self.db.execute("SELECT 1 FROM replays WHERE room = ? AND seed = ?", (room, seed)).fetchone() is not None

    def get(self, room: int, seed: int) -> dict | None:
        row = self.db.execute("SELECT room, seed, time, size, sha256, map, data, mods FROM replays WHERE room = ? AND seed = ?",
                              (room, seed)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["mods"] = json.loads(d["mods"] or "[]")
        return d

    def put(self, room: int, seed: int, uploader: int, blob: bytes, map_name: str = "", mods: list[str] | None = None) -> str:
        sha = hashlib.sha256(blob).hexdigest()
        with self.lock, self.db:
            row = self.db.execute("SELECT sha256 FROM replays WHERE room = ? AND seed = ?", (room, seed)).fetchone()
            if row is not None:
                same = row["sha256"] == sha
                self.db.execute("UPDATE replays SET copies = copies + 1, differing = differing + ? WHERE room = ? AND seed = ?",
                                (0 if same else 1, room, seed))
                return "same" if same else "differs"
            self.db.execute("INSERT INTO replays (room, seed, time, uploader, size, sha256, map, data, mods) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (room, seed, time.time(), uploader, len(blob), sha, map_name, blob, json.dumps(mods or [])))
            extra = self.db.execute("SELECT COUNT(*) FROM replays").fetchone()[0] - self.keep
            if extra > 0:
                self.db.execute("DELETE FROM replays WHERE rowid IN (SELECT rowid FROM replays ORDER BY time LIMIT ?)", (extra,))
        return "kept"


_shelves_lock = threading.Lock()


def shelf_for(store, cfg: dict | None = None) -> Shelf:
    with _shelves_lock:
        sh = getattr(store, "_replay_shelf", None)
        if sh is None:
            sh = Shelf(store, int((cfg or {}).get("replays_kept", KEEP)))
            store._replay_shelf = sh
        return sh


def match_of(store, results, room: int, seed: int) -> tuple[list[int], str] | None:
    if results is not None:
        with results.lock:
            entry = results.pending.get((room, seed))
            if entry is not None:
                maps = [r.get("map", "") for r in entry.reports.values()]
                return list(entry.ids), (maps[0] if maps else "")
    head = store.db.execute("SELECT id, map FROM results WHERE room = ? AND seed = ? ORDER BY id DESC LIMIT 1",
                            (room, seed)).fetchone()
    if head is None:
        return None
    ids = [r["player_id"] for r in store.db.execute("SELECT player_id FROM result_players WHERE result_id = ?", (head["id"],))]
    return ids, head["map"] or ""


class Desk:

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms
        self.upload: dict | None = None
        self.share_upload: dict | None = None

    def _me(self) -> int | None:
        p = self.session.player
        return p["id"] if p else self.session.player_id

    def _stored(self, ok: bool, text: str) -> None:
        self.session.send(Packet(STORED, BitWriter().bool(ok).string(text).bytes()))

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        s, me = self.session, self._me()
        if me is None or s.store is None:
            s.log(f"replay packet {p.type} " + ("before login" if me is None else "without a store") + ": ignored")
            return True
        from . import stats
        results = stats.results_for(self.rooms, s.store, s.log, s.cfg)
        stats.ledger_for(s.store)
        shelf = shelf_for(s.store, s.cfg)
        if p.type == GET_CODE:
            code = BitReader(p.payload).string()[:16].strip().upper()
            row = shelf.shared(code)
            if row and row["mods"]:
                self._offer_mods(0, 0, row["mods"])
            for pk in data_packets(0, 0, row["data"] if row else None, row["name"] if row else "", row["mods"] if row else None):
                s.send(pk)
            s.log(f"replay code {code} asked by player {me}: " + (f"sent {row['size']} bytes" if row else "none"))
            return True
        if p.type == SHARE:
            self._share_part(p, shelf, me)
            return True
        if p.type == GET:
            r = BitReader(p.payload)
            room, seed = r.uint(), r.uint()
            row = shelf.get(room, seed)
            name = file_name(row["map"], room, seed) if row else ""
            if row and row["mods"]:
                self._offer_mods(room, seed, row["mods"])
            for pk in data_packets(room, seed, row["data"] if row else None, name, row["mods"] if row else None):
                s.send(pk)
            s.log(f"replay {room}-{seed} asked by player {me}: " + (f"sent {row['size']} bytes" if row else "none kept"))
            return True
        try:
            part = read_part(p.payload)
        except ValueError as e:
            s.log(f"replay part from player {me} unreadable: {e}")
            self.upload = None
            return True
        key = (part["room"], part["seed"])
        if part["offset"] == 0:
            if not 0 < part["total"] <= MAX_REPLAY:
                s.log(f"replay {key[0]}-{key[1]} from player {me}: refused, {part['total']} bytes")
                self._stored(False, "The replay is too large to share.")
                return True
            self.upload = {"key": key, "total": part["total"], "buf": bytearray(), "mods": part["mods"]}
        up = self.upload
        if up is None or up["key"] != key or up["total"] != part["total"] or len(up["buf"]) != part["offset"]:
            s.log(f"replay {key[0]}-{key[1]} from player {me}: part at {part['offset']} out of order, dropped")
            self.upload = None
            return True
        up["buf"] += part["data"]
        if len(up["buf"]) > up["total"]:
            s.log(f"replay {key[0]}-{key[1]} from player {me}: longer than announced, dropped")
            self.upload = None
            return True
        if len(up["buf"]) < up["total"]:
            return True
        self.upload = None
        blob = bytes(up["buf"])
        match = match_of(s.store, results, *key)
        if match is None or me not in match[0]:
            why = "no such match" if match is None else "not a player of that match"
            s.log(f"replay {key[0]}-{key[1]} from player {me} ({len(blob)} bytes): refused, {why}")
            self._stored(False, "The server doesn't know that match, so its replay wasn't kept." if match is None
                         else "You weren't a player in that match.")
            return True
        why = check(blob)
        if why:
            s.log(f"replay {key[0]}-{key[1]} from player {me} ({len(blob)} bytes): refused, {why}")
            self._stored(False, "The server couldn't read that replay.")
            return True
        what = shelf.put(key[0], key[1], me, blob, match[1], up["mods"])
        s.log(f"replay {key[0]}-{key[1]} from player {me} ({len(blob)} bytes): {what}")
        if what == "kept":
            self._stored(True, "Replay shared: it's on the server's match page.")
        return True

    def _shared(self, code: str, text: str) -> None:
        self.session.send(Packet(SHARED, BitWriter().string(code).string(text).bytes()))

    def _share_part(self, p: Packet, shelf: Shelf, me: int) -> None:
        s = self.session
        try:
            part = read_share_part(p.payload)
        except ValueError as e:
            s.log(f"replay share part from player {me} unreadable: {e}")
            self.share_upload = None
            self._shared("", "The server couldn't read that replay.")
            return
        if part["offset"] == 0:
            if not 0 < part["total"] <= MAX_REPLAY:
                self._shared("", "The replay is too large to share.")
                return
            self.share_upload = {"nonce": part["nonce"], "total": part["total"], "buf": bytearray(), "mods": part["mods"],
                                 "name": part["name"]}
        up = self.share_upload
        if up is None or up["nonce"] != part["nonce"] or up["total"] != part["total"] or len(up["buf"]) != part["offset"]:
            s.log(f"replay share from player {me}: part at {part['offset']} out of order, dropped")
            self.share_upload = None
            return
        up["buf"] += part["data"]
        if len(up["buf"]) > up["total"]:
            self.share_upload = None
            return
        if len(up["buf"]) < up["total"]:
            return
        self.share_upload = None
        blob = bytes(up["buf"])
        why = check(blob)
        if why:
            s.log(f"replay share from player {me} ({len(blob)} bytes): refused, {why}")
            self._shared("", "The server couldn't read that replay.")
            return
        keep = int((s.cfg or {}).get("replay_shares_kept", SHARES_KEPT))
        code = shelf.share(me, blob, up["name"], up["mods"], keep)
        s.log(f"replay share from player {me} ({len(blob)} bytes, {up['name']}): code {code}")
        self._shared(code, "Replay shared.")

    def _library(self):
        library = getattr(self.rooms, "library", None)
        web = int(self.session.cfg.get("web_port", 0) or 0) if self.session.cfg else 0
        return (library, web) if library is not None and web else (None, 0)

    def _offer_mods(self, room: int, seed: int, mods: list[str]) -> None:
        library, web = self._library()
        if library is None:
            return
        acc = self.session.account
        owner = acc["id"] if acc else None
        have = [e for e in mods if library.find(e, owner)]
        self.session.send(mods_packet(room, seed, web, library.ticket("down", have, owner=owner) if have else "", have))
