from __future__ import annotations

from pathlib import Path
import time
import zlib

from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet

SEND, ANSWER = 11094, 11095
FORMAT = 1
MAX_PACKED = 60000
MAX_TEXT = 4 * 1024 * 1024
MAX_NOTE = 500
MAX_PER_SESSION = 5
KEEP_PER_PLAYER = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS bug_reports (
  id INTEGER PRIMARY KEY, time REAL NOT NULL, player_id INTEGER, account_id INTEGER, name TEXT NOT NULL,
  note TEXT NOT NULL, files TEXT NOT NULL, size INTEGER NOT NULL, data BLOB NOT NULL);
"""


def read_send(payload: bytes) -> dict:
    r = BitReader(payload)
    fmt, note, files, _ = r.uint(), r.string(), r.string(), r.uint()
    n = r.small()
    if n <= 0 or n > MAX_PACKED:
        raise ValueError(f"{n} packed bytes (1 to {MAX_PACKED})")
    data = r.raw(n)
    if r.pos > len(payload) * 8:
        raise ValueError("cut short")
    try:
        text = zlib.decompressobj().decompress(data, MAX_TEXT + 1)
    except zlib.error as e:
        raise ValueError(f"not deflated ({e})") from None
    if len(text) > MAX_TEXT:
        raise ValueError(f"more than {MAX_TEXT} bytes unpacked")
    return {"format": fmt, "note": note[:MAX_NOTE], "files": files[:400], "size": len(text), "data": data}


def answer(report_id: int, message: str) -> Packet:
    return Packet(ANSWER, BitWriter().uint(report_id).string(message).bytes())


def handle(session, p: Packet) -> bool:
    if p.type != SEND:
        return False
    log = session.log
    me = session.player["id"] if session.player else session.player_id
    count = getattr(session, "bug_reports_sent", 0) + 1
    session.bug_reports_sent = count
    if count > MAX_PER_SESSION:
        log(f"bug report from player {me} refused: more than {MAX_PER_SESSION} this session")
        session.send(answer(0, f"Not sent: at most {MAX_PER_SESSION} reports per session."))
        return True
    try:
        got = read_send(p.payload)
    except (ValueError, IndexError) as e:
        log(f"bug report from player {me} refused: {e}")
        session.send(answer(0, "Not sent: the server couldn't read it."))
        return True
    store = session.store
    if store is None:
        log(f"bug report from player {me} ({got['size']} bytes): this server keeps no store, not kept")
        session.send(answer(0, "This server keeps no reports."))
        return True
    account = session.account["id"] if session.account else None
    name = (session.player or {}).get("name") or (session.account or {}).get("name") or ""
    with stats.ledger_for(store).lock:
        db = store.db
        db.ensure(SCHEMA)
        with db:
            cur = db.execute("INSERT INTO bug_reports (time, player_id, account_id, name, note, files, size, data) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (time.time(), me, account, str(name), got["note"],
                                                                 got["files"], got["size"], got["data"]))
            rid = cur.lastrowid
            if me is not None:
                db.execute("DELETE FROM bug_reports WHERE player_id = ? AND id NOT IN (SELECT id FROM bug_reports "
                           "WHERE player_id = ? ORDER BY id DESC LIMIT ?)", (me, me, KEEP_PER_PLAYER))
    first = got["note"].splitlines()[0][:120] if got["note"] else "(no note)"
    log(f"bug report #{rid} from player {me} ({got['size']} bytes, {len(got['data'])} packed; {got['files']}): {first}")
    session.send(answer(rid, f"Bug report #{rid} received by the server. Thank you!"))
    return True


def listing(db, limit: int = 20) -> str:
    db.ensure(SCHEMA)
    rows = db.execute("SELECT id, time, player_id, name, note, size FROM bug_reports ORDER BY id DESC LIMIT ?",
                      (limit,)).fetchall()
    if not rows:
        return "no bug reports"
    out = [f"{len(rows)} newest bug report{'' if len(rows) == 1 else 's'} (bugs <id> writes one out):"]
    for r in rows:
        note = (r["note"].splitlines()[0][:70] if r["note"] else "(no note)")
        out.append(f"  #{r['id']} {time.strftime('%Y-%m-%d %H:%M', time.localtime(r['time']))} "
                   f"{r['name'] or 'player ' + str(r['player_id'])}: {note} ({r['size'] // 1024} KB)")
    return "\n".join(out)


def write_out(db, report_id: int, folder: Path) -> str:
    db.ensure(SCHEMA)
    r = db.execute("SELECT id, time, player_id, account_id, name, note, files, data FROM bug_reports WHERE id = ?",
                   (report_id,)).fetchone()
    if r is None:
        return f"no bug report #{report_id}"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{r['id']}.txt"
    head = (f"Bug report #{r['id']}, {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(r['time']))}, "
            f"{r['name'] or '?'} (player {r['player_id']}, account {r['account_id']})\nNote: {r['note'] or '(none)'}\n"
            f"Files: {r['files']}\n\n")
    out.write_text(head + zlib.decompress(bytes(r["data"])).decode("utf-8", errors="replace"), encoding="utf-8")
    return f"bug report #{r['id']} written to {out}"


def console(rest: str, db, folder: Path) -> str:
    rest = rest.strip().lstrip("#")
    if not rest:
        return listing(db)
    if not rest.isdigit():
        return "bugs [id]"
    return write_out(db, int(rest), folder)
