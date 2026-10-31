from __future__ import annotations

import time

from .bits import BitReader, BitWriter
from .frame import Packet

UPLOAD, HELD, DELETE = 11022, 11023, 11024
HANDLED = {UPLOAD, DELETE}
FORMAT = 1
MAX_BLOB = 60000
MAX_UPLOADS = 2000

SCHEMA = """
CREATE TABLE IF NOT EXISTS cloud_saves (
  account_id INTEGER PRIMARY KEY REFERENCES accounts(id), format INTEGER NOT NULL, client_time INTEGER NOT NULL,
  saved REAL NOT NULL, what TEXT NOT NULL, data BLOB NOT NULL);
"""


def read_upload(payload: bytes) -> dict:
    r = BitReader(payload)
    fmt, when, what = r.uint(), r.uint(), r.string()
    n = r.small()
    if n > MAX_BLOB:
        raise ValueError(f"{n} bytes, more than {MAX_BLOB}")
    return {"format": fmt, "when": when, "what": what[:200], "data": r.raw(n)}


def held_packet(row: dict | None, with_data: bool) -> Packet:
    w = BitWriter().bool(row is not None)
    if row is not None:
        w.uint(row["client_time"]).uint(len(row["data"])).uint(int(row["saved"])).string(row["what"]).bool(with_data)
        if with_data:
            w.small(len(row["data"])).raw(bytes(row["data"]))
    return Packet(HELD, w.bytes())


class Locker:

    def __init__(self, session) -> None:
        self.session = session
        self.count = 0

    def _db(self):
        self.session.store.db.ensure(SCHEMA)
        return self.session.store.db

    def account_id(self) -> int | None:
        s = self.session
        return s.account["id"] if s.store is not None and s.account else None

    def load(self) -> dict | None:
        acc = self.account_id()
        if acc is None:
            return None
        row = self._db().execute("SELECT client_time, saved, what, data FROM cloud_saves WHERE account_id = ?",
                                 (acc,)).fetchone()
        return dict(row) if row is not None else None

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        s, acc = self.session, self.account_id()
        if acc is None:
            s.log(f"server save {p.type}: not signed in, ignored (a guest's save stays on its PC)")
            return True
        self.count += 1
        if self.count > MAX_UPLOADS:
            return True
        if p.type == DELETE:
            db = self._db()
            with db:
                gone = db.execute("DELETE FROM cloud_saves WHERE account_id = ?", (acc,)).rowcount
            s.log(f"server save of account {acc} deleted ({gone} row)")
            s.send(held_packet(None, False))
            return True
        try:
            up = read_upload(p.payload)
        except (IndexError, ValueError) as e:
            s.log(f"server save from account {acc} refused: {e}")
            return True
        db = self._db()
        with db:
            db.execute("INSERT INTO cloud_saves VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(account_id) DO UPDATE SET "
                       "format = excluded.format, client_time = excluded.client_time, saved = excluded.saved, "
                       "what = excluded.what, data = excluded.data",
                       (acc, up["format"], up["when"], time.time(), up["what"], up["data"]))
        s.log(f"server save of account {acc} kept ({len(up['data'])} bytes: {up['what']})")
        s.send(held_packet(self.load(), False))
        return True

    def after_login(self) -> None:
        if self.account_id() is None:
            return
        row = self.load()
        self.session.send(held_packet(row, True))
        self.session.log(f"server save for account {self.account_id()} sent (11023: "
                         + (f"{len(row['data'])} bytes)" if row else "none held)"))
