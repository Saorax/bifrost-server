from __future__ import annotations

import json
import time

from . import reports as reports_mod
from . import stats
from .bits import BitReader, BitWriter
from .frame import Packet
from .tournaments import LIST_NOTICE

ASK, ANSWER, STATE = 11097, 11098, 11099
MAX_TEXT = 400
MAX_ROWS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS bans (who TEXT PRIMARY KEY, until REAL NOT NULL, reason TEXT NOT NULL, by TEXT NOT NULL,
  time REAL NOT NULL);
CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY, time REAL NOT NULL, text TEXT NOT NULL,
  by TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS reports_done (id INTEGER PRIMARY KEY, by TEXT NOT NULL, time REAL NOT NULL);
"""


def admin_ids(cfg: dict | None) -> set[str]:
    return {str(i) for i in ((cfg or {}).get("admin_discord_ids") or [])}


def is_admin(session) -> bool:
    account = getattr(session, "account", None)
    return bool(account and str(account.get("discord_id")) in admin_ids(getattr(session, "cfg", None)))


def _db(store):
    store.db.ensure(SCHEMA)
    return store.db


def _lock(store):
    return stats.ledger_for(store).lock


def wall() -> float:
    return time.time()


def banned(store, key: str) -> tuple[float, str] | None:
    if store is None or not key:
        return None
    with _lock(store):
        db = _db(store)
        row = db.execute("SELECT until, reason FROM bans WHERE who = ?", (key,)).fetchone()
        if row is None:
            return None
        if row[0] and row[0] <= wall():
            with db:
                db.execute("DELETE FROM bans WHERE who = ?", (key,))
            return None
        return float(row[0]), str(row[1])


def ban_text(until: float, reason: str) -> str:
    left = "" if not until else f" for {max(1, round((until - wall()) / 60))} more minute(s)"
    return f"You are banned from this server{left}." + (f" Reason: {reason}" if reason else "")


def ban(social, key: str, minutes: float = 0, reason: str = "", by: str = "console") -> int:
    until = wall() + minutes * 60 if minutes > 0 else 0.0
    store = social.store
    with _lock(store):
        db = _db(store)
        with db:
            db.execute("INSERT OR REPLACE INTO bans VALUES (?, ?, ?, ?, ?)", (key, until, reason, by, wall()))
    n = sign_out(social, key, ban_text(until, reason))
    social.log(f"ban: {social.display(key)} ({key}) " + (f"for {minutes:g} min" if until else "until unbanned")
               + f" by {by}" + (f": {reason}" if reason else "") + f"; {n} game(s) signed out")
    return n


def unban(social, key: str, by: str = "console") -> bool:
    store = social.store
    with _lock(store):
        db = _db(store)
        with db:
            gone = db.execute("DELETE FROM bans WHERE who = ?", (key,)).rowcount
    if gone:
        social.log(f"ban lifted: {social.display(key)} ({key}) by {by}")
    return bool(gone)


def bans(store) -> list[dict]:
    if store is None:
        return []
    with _lock(store):
        rows = _db(store).execute("SELECT who, until, reason, by FROM bans ORDER BY time DESC").fetchall()
    now = wall()
    return [{"key": r[0], "until": r[1], "reason": r[2], "by": r[3]} for r in rows if not r[1] or r[1] > now]


def sign_out_packets(text: str) -> list[Packet]:
    from .social import SERVER_MESSAGE, TO_OFFLINE
    return [Packet(SERVER_MESSAGE, BitWriter().string(text).bool(False).bytes()),
            Packet(TO_OFFLINE, BitWriter().uint(0).bytes())]


def sign_out(social, key: str, text: str) -> int:
    with social.lock:
        found = social._sessions_for(key)
    for s in found:
        for pk in sign_out_packets(text):
            social._send(s, pk)
    return len(found)


def account_of(store, key: str) -> int | None:
    if store is None or not key.startswith("d:"):
        return None
    with _lock(store):
        row = store.db.execute("SELECT id FROM accounts WHERE discord_id = ?", (key[2:],)).fetchone()
    return int(row[0]) if row else None


def reset_rating(store, key: str, ladder: str = "") -> int:
    account = account_of(store, key)
    if account is None:
        return 0
    with _lock(store):
        db = store.db
        with db:
            if ladder:
                return db.execute("DELETE FROM ratings WHERE account_id = ? AND ladder = ?", (account, ladder)).rowcount
            return db.execute("DELETE FROM ratings WHERE account_id = ?", (account,)).rowcount


def player_key(store, player_id) -> str:
    if player_id is None:
        return ""
    row = store.db.execute("SELECT a.discord_id FROM players p JOIN accounts a ON a.id = p.account_id WHERE p.id = ?",
                           (int(player_id),)).fetchone()
    return f"d:{row[0]}" if row else f"g:{int(player_id)}"


def player_name(store, player_id) -> str:
    if player_id is None:
        return ""
    row = store.db.execute("SELECT name FROM players WHERE id = ?", (int(player_id),)).fetchone()
    return str(row[0]) if row else f"player {player_id}"


def report_rows(store, include_done: bool = False, limit: int = MAX_ROWS) -> list[dict]:
    if store is None:
        return []
    with _lock(store):
        db = _db(store)
        db.ensure(reports_mod.SCHEMA)
        rows = db.execute("SELECT r.id, r.time, r.player_id, r.target, r.text, d.id IS NOT NULL FROM client_reports r "
                          "LEFT JOIN reports_done d ON d.id = r.id WHERE r.kind = 'player' "
                          + ("" if include_done else "AND d.id IS NULL ") + "ORDER BY r.id DESC LIMIT ?",
                          (limit,)).fetchall()
        return [{"id": r[0], "time": r[1], "reporter": player_name(store, r[2]), "target": player_name(store, r[3]),
                 "key": player_key(store, r[3]), "text": r[4], "done": bool(r[5])} for r in rows]


def dismiss(store, report_id: int, by: str) -> bool:
    with _lock(store):
        db = _db(store)
        db.ensure(reports_mod.SCHEMA)
        if db.execute("SELECT 1 FROM client_reports WHERE id = ? AND kind = 'player'", (report_id,)).fetchone() is None:
            return False
        with db:
            db.execute("INSERT OR REPLACE INTO reports_done VALUES (?, ?, ?)", (report_id, by, wall()))
    return True


def notice_row(row: dict) -> Packet:
    return Packet(LIST_NOTICE, BitWriter().string(f"announce:{row['id']}").string("ANNOUNCEMENT").string(row["text"])
                  .bytes())


def announcements(store) -> list[dict]:
    if store is None:
        return []
    with _lock(store):
        rows = _db(store).execute("SELECT id, time, text, by FROM announcements WHERE active = 1 ORDER BY id").fetchall()
    return [{"id": r[0], "time": r[1], "text": r[2], "by": r[3]} for r in rows]


def announce(social, text: str, by: str = "console") -> tuple[int, int]:
    text = text.strip()[:MAX_TEXT]
    store = social.store
    with _lock(store):
        db = _db(store)
        with db:
            rid = db.execute("INSERT INTO announcements (time, text, by) VALUES (?, ?, ?)", (wall(), text, by)).lastrowid
    with social.lock:
        sessions = list(social.sessions)
    row = notice_row({"id": rid, "text": text})
    for s in sessions:
        social._send(s, row)
    social.log(f"announcement #{rid} by {by}: {text!r} sent to {len(sessions)} game(s) (11096)")
    return rid, len(sessions)


def unannounce(social, rid: int, by: str = "console") -> bool:
    store = social.store
    with _lock(store):
        db = _db(store)
        with db:
            gone = db.execute("UPDATE announcements SET active = 0 WHERE id = ? AND active = 1", (rid,)).rowcount
    if gone:
        social.log(f"announcement #{rid} removed by {by}")
    return bool(gone)


def after_login(session) -> bool:
    social, store = getattr(session, "social", None), getattr(session, "store", None)
    if social is None or store is None:
        return True
    key = social.key(session)
    b = banned(store, key)
    if b is not None:
        for pk in sign_out_packets(ban_text(*b)):
            session.send(pk)
        session.log(f"banned player {key} refused (2465, 12002)")
        return False
    if is_admin(session):
        session.send(Packet(STATE, BitWriter().bool(True).bytes()))
        session.log(f"account {session.account['id']} is a server admin (sent 11099)")
    for row in announcements(store):
        session.send(notice_row(row))
    return True


def find(social, name: str) -> list[dict]:
    want = name.strip().lower()
    out: dict[str, dict] = {}
    if not want:
        return []
    for k in social.keys_named(want):
        out[k] = {"key": k, "name": social.display(k), "online": True}
    store = social.store
    if store is not None:
        with _lock(store):
            rows = store.db.execute("SELECT discord_id, name FROM accounts WHERE lower(name) LIKE ? ORDER BY "
                                    "lower(name) = ? DESC, last_login DESC LIMIT 8", (f"%{want}%", want)).fetchall()
            rows += [(None, r[0], r[1]) for r in store.db.execute(
                "SELECT id, name FROM players WHERE account_id IS NULL AND lower(name) LIKE ? ORDER BY lower(name) = ? "
                "DESC, id DESC LIMIT 8", (f"%{want}%", want)).fetchall()]
        for r in rows:
            k = f"d:{r[0]}" if r[0] is not None else f"g:{r[1]}"
            out.setdefault(k, {"key": k, "name": r[-1], "online": False})
    found = list(out.values())[:8]
    for f in found:
        f["muted"] = social.muted(f["key"]) is not None
        f["banned"] = banned(store, f["key"]) is not None
        f["account"] = f["key"].startswith("d:")
    return found


def run(social, action: str, args: dict, by: str) -> dict:
    store = social.store
    key = str(args.get("key") or "")
    if action == "reports":
        return {"ok": True, "reports": report_rows(store, bool(args.get("all")))}
    if action == "dismiss":
        ok = dismiss(store, int(args.get("id") or 0), by)
        return {"ok": ok, "message": "Report dismissed." if ok else "No such report."}
    if action == "find":
        return {"ok": True, "players": find(social, str(args.get("name") or ""))}
    if action == "announcements":
        return {"ok": True, "announcements": announcements(store)}
    if action == "announce":
        text = str(args.get("text") or "").strip()
        if not text:
            return {"ok": False, "message": "Nothing to announce."}
        rid, n = announce(social, text, by)
        return {"ok": True, "id": rid, "message": f"Announced to {n} game{'' if n == 1 else 's'}."}
    if action == "unannounce":
        ok = unannounce(social, int(args.get("id") or 0), by)
        return {"ok": ok, "message": "Announcement removed." if ok else "No such announcement."}
    if not key.startswith(("d:", "g:")):
        return {"ok": False, "message": "No player picked."}
    who = social.display(key)
    minutes = max(0.0, float(args.get("minutes") or 0))
    reason = str(args.get("reason") or "")[:200]
    span = f"for {minutes:g} min" if minutes > 0 else "until lifted"
    if action == "mute":
        social.mute(key, minutes, reason)
        return {"ok": True, "message": f"{who} muted {span}."}
    if action == "unmute":
        ok = social.unmute(key)
        return {"ok": ok, "message": f"{who} unmuted." if ok else f"{who} wasn't muted."}
    if action == "ban":
        if key.startswith("d:") and key[2:] in admin_ids(social.cfg):
            return {"ok": False, "message": "Admins can't be banned here."}
        ban(social, key, minutes, reason, by)
        return {"ok": True, "message": f"{who} banned {span}."}
    if action == "unban":
        ok = unban(social, key, by)
        return {"ok": ok, "message": f"{who} unbanned." if ok else f"{who} wasn't banned."}
    if action == "resetrating":
        ladder = str(args.get("ladder") or "")
        n = reset_rating(store, key, ladder)
        social.log(f"rating reset: {who} ({key}) {ladder or 'every ladder'} by {by}: {n} row(s)")
        return {"ok": n > 0, "message": f"{who}: {n} rating{'' if n == 1 else 's'} reset." if n else
                f"{who} has no {ladder + ' ' if ladder else ''}rating to reset."}
    return {"ok": False, "message": f"Unknown action {action!r}."}


def handle(session, p: Packet) -> bool:
    if p.type != ASK:
        return False
    try:
        r = BitReader(p.payload)
        action, raw = r.string(), r.string()
        args = json.loads(raw) if raw else {}
        if not isinstance(args, dict):
            raise ValueError("args not an object")
    except (ValueError, IndexError):
        session.log("moderation ask unreadable: ignored")
        return True
    if not is_admin(session) or getattr(session, "social", None) is None or session.store is None:
        session.log(f"moderation {action!r} refused: not an admin")
        out = {"ok": False, "message": "Only the server's admins can do that."}
    else:
        by = f"d:{session.account['discord_id']}"
        try:
            out = run(session.social, action, args, by)
        except (ValueError, TypeError) as e:
            out = {"ok": False, "message": f"Couldn't do that ({e})."}
        session.log(f"moderation {action} by account {session.account['id']}: {out.get('message', 'ok')}")
    out["action"] = action
    session.send(Packet(ANSWER, BitWriter().string(json.dumps(out)[:60000]).bytes()))
    return True


def named(social, words: list[str]) -> tuple[str | None, list[str], str]:
    for n in range(len(words), 0, -1):
        name = " ".join(words[:n])
        keys = social.keys_named(name)
        if not keys:
            keys = [f["key"] for f in find(social, name) if f["name"].lower() == name.lower()]
        if len(keys) == 1:
            return keys[0], words[n:], ""
        if len(keys) > 1:
            return None, [], "more than one player matches: " + ", ".join(sorted(social.display(k) for k in keys))
    return None, [], f"no player named {' '.join(words)!r}"


def console(cmd: str, rest: str, social) -> str | None:
    store = social.store
    if cmd == "admins":
        ids = sorted(admin_ids(social.cfg))
        return f"{len(ids)} admin(s) by Discord ID (config admin_discord_ids)" + "".join(f"\n  {i}" for i in ids)
    if cmd in ("ban", "unban", "resetrating") and rest:
        key, more, err = named(social, rest.split())
        if key is None:
            return err
        if cmd == "unban":
            return f"{social.display(key)} unbanned" if unban(social, key) else f"{social.display(key)} wasn't banned"
        if cmd == "resetrating":
            out = run(social, "resetrating", {"key": key, "ladder": " ".join(more)}, "console")
            return out["message"]
        minutes = 0.0
        if more:
            try:
                minutes, more = float(more[0]), more[1:]
            except ValueError:
                pass
        out = run(social, "ban", {"key": key, "minutes": minutes, "reason": " ".join(more)}, "console")
        return out["message"]
    if cmd == "bans":
        rows = bans(store)
        return f"{len(rows)} banned" + "".join(
            f"\n  {social.display(r['key'])} ({r['key']}): " + (f"{max(1, round((r['until'] - wall()) / 60))} min left"
                                                                 if r["until"] else "until unbanned")
            + (f" ({r['reason']})" if r["reason"] else "") for r in rows)
    if cmd == "reports":
        rows = report_rows(store, rest == "all")
        return f"{len(rows)} report(s)" + "".join(
            f"\n  #{r['id']} {time.strftime('%m-%d %H:%M', time.localtime(r['time']))} {r['reporter']} reported "
            f"{r['target']}: {r['text'] or '(no reason)'}" + (" [dismissed]" if r["done"] else "") for r in rows)
    if cmd == "dismiss" and rest.lstrip("#").isdigit():
        return "dismissed" if dismiss(store, int(rest.lstrip("#")), "console") else "no such report"
    if cmd == "announce" and rest:
        rid, n = announce(social, rest)
        return f"announcement #{rid} sent to {n} game{'' if n == 1 else 's'} (shown at each login until unannounce {rid})"
    if cmd == "unannounce" and rest.lstrip("#").isdigit():
        return "removed" if unannounce(social, int(rest.lstrip("#"))) else "no such announcement"
    if cmd == "announcements":
        rows = announcements(store)
        return f"{len(rows)} announcement(s)" + "".join(f"\n  #{r['id']} {r['text']}" for r in rows)
    return None
