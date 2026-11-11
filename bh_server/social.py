from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import json
import threading
import time

from . import lobby, reports
from .bits import BitReader, BitWriter
from .frame import Packet
from .rooms import spectate_refusal

INVITE, INVITE_ACCEPT, INVITE_DECLINE, KICK, BAN = 34, 35, 36, 39, 40
KICKED = 2453
PARTY_CHAT, MAKE_LEADER, SPECTATE_FRIEND, JOIN_FRIEND, LOCAL_STATUS, INVITE_PLAYLIST = 77, 82, 83, 84, 125, 158
CHANNEL_CHAT = 76
TYPING = 78
FRIEND_ADD, FRIEND_REMOVE = 11050, 11051
FRIENDS_CMD, FRIENDS_STATE = 11052, 11053
HANDLED = {CHANNEL_CHAT, INVITE, INVITE_ACCEPT, INVITE_DECLINE, KICK, BAN, PARTY_CHAT, MAKE_LEADER, SPECTATE_FRIEND, JOIN_FRIEND,
           LOCAL_STATUS, INVITE_PLAYLIST, FRIEND_ADD, FRIEND_REMOVE, FRIENDS_CMD}
ROOM_MOVES = {INVITE, INVITE_ACCEPT, KICK, BAN, PARTY_CHAT, MAKE_LEADER, SPECTATE_FRIEND, JOIN_FRIEND, INVITE_PLAYLIST}

PRIVACY_FAILED, ACTION_FAILED, FRIEND_STATUS, FRIEND_LIST, FRIENDS_ADDED = 2403, 2404, 2405, 2406, 2407
CHAT_RESTRICTED = 2550
SERVER_NOTICE = 2426
TYPING_SHOWN = 2414
CHAT, INVITED, LOBBY_INVITED, SUGGESTED, INVITE_ACCEPTED, INVITE_DECLINED = 2413, 2439, 2440, 2441, 2442, 2443
NOTICE = 11013
SERVER_MESSAGE = 2465
SERVER_ERROR = 2545
CH_LEFT, CH_JOINED, CH_MEMBERS, CH_MESSAGE = 2415, 2416, 2417, 2418
STATUS_LINE = 2432
GENERAL = 1
TO_OFFLINE = 12002

OFFLINE, ONLINE, CUSTOM, IN_QUEUE, PLAYING, SPECTATING, IN_LOBBY, TRAINING, COUCH, TOURNAMENT = range(1, 11)
WITH_DETAIL = (CUSTOM, IN_QUEUE, PLAYING, IN_LOBBY)
LOCAL = (TRAINING, COUCH, TOURNAMENT)
STATUS_TEXT = {OFFLINE: "offline", ONLINE: "online", CUSTOM: "in a custom room", IN_QUEUE: "in a queue",
               PLAYING: "playing", SPECTATING: "spectating", IN_LOBBY: "in a lobby", TRAINING: "in Training",
               COUCH: "in a Couch Party", TOURNAMENT: "in a tournament"}
JOIN_FAIL_SPECTATE, JOIN_FAIL_JOIN, JOIN_FAIL_INVITE, JOIN_FAIL_SAME_GROUP, JOIN_FAIL_SUGGESTER_GONE = 1, 2, 3, 4, 11
PUBLIC, FRIENDS_MAY_JOIN, CLAN_MAY_JOIN = 1, 2, 8
INVITE_TTL = 200
MAX_CHAT = 200
MAX_LIST = 300
RECENT_DAYS, RECENT_MAX = 30, 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS friends (
  a TEXT NOT NULL, b TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (a, b));
CREATE TABLE IF NOT EXISTS friend_requests (
  src TEXT NOT NULL, dst TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY (src, dst));
CREATE TABLE IF NOT EXISTS chat_mutes (
  who TEXT PRIMARY KEY, until REAL NOT NULL, reason TEXT NOT NULL);
"""


def signed(w: BitWriter, v: int) -> BitWriter:
    return w.bool(v < 0).uint(abs(v))


def read_signed(r: BitReader) -> int:
    neg = r.bool()
    v = r.uint()
    return -v if neg else v


def write_row(w: BitWriter, fid: int, name: str, status: int, detail: int = 0, portrait: int = 0) -> BitWriter:
    w.uint(fid).string(name).string("").uint(status)
    if status in WITH_DETAIL:
        w.uint(detail)
    return w.uint(portrait)


def read_rows(payload: bytes, counted: bool) -> list[dict]:
    r, rows = BitReader(payload), []
    n = r.uint() if counted else -1
    while (len(rows) < n) if counted else r.bool():
        fid, name, _, status = r.uint(), r.string(), r.string(), r.uint()
        detail = r.uint() if status in WITH_DETAIL else 0
        rows.append({"id": fid, "name": name, "status": status, "detail": detail, "portrait": r.uint()})
    return rows


def read_status(payload: bytes) -> dict:
    r = BitReader(payload)
    fid, status = r.uint(), r.uint()
    return {"id": fid, "status": status, "detail": r.uint() if status in WITH_DETAIL else 0}


def read_invite(payload: bytes) -> dict:
    r = BitReader(payload)
    out = {"from": r.uint(), "name": r.string(), "invite": r.uint(), "playlist": read_signed(r)}
    if r.bool():
        out["name"] += " + " + r.string()
    return out


def custom_detail(flags: int) -> int:
    return PUBLIC if flags & PUBLIC else flags & (FRIENDS_MAY_JOIN | CLAN_MAY_JOIN)


class FriendsDB:

    def __init__(self, db) -> None:
        self.db = db
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.executescript(SCHEMA)

    @staticmethod
    def _pair(x: str, y: str) -> tuple[str, str]:
        return (x, y) if x < y else (y, x)

    def are_friends(self, x: str, y: str) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM friends WHERE a = ? AND b = ?", self._pair(x, y)).fetchone() is not None

    def friends(self, x: str) -> set[str]:
        with self.lock:
            rows = self.db.execute("SELECT a, b FROM friends WHERE a = ? OR b = ?", (x, x)).fetchall()
        return {r[1] if r[0] == x else r[0] for r in rows}

    def befriend(self, x: str, y: str) -> bool:
        if x == y:
            return False
        with self.lock, self.db:
            self.db.execute("DELETE FROM friend_requests WHERE (src = ? AND dst = ?) OR (src = ? AND dst = ?)", (x, y, y, x))
            cur = self.db.execute("INSERT OR IGNORE INTO friends VALUES (?, ?, ?)", (*self._pair(x, y), time.time()))
            return cur.rowcount > 0

    def request(self, src: str, dst: str) -> str:
        if src == dst:
            return "self"
        if self.are_friends(src, dst):
            return "already"
        with self.lock:
            back = self.db.execute("SELECT 1 FROM friend_requests WHERE src = ? AND dst = ?", (dst, src)).fetchone()
        if back:
            self.befriend(src, dst)
            return "friends"
        with self.lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO friend_requests VALUES (?, ?, ?)", (src, dst, time.time()))
        return "requested"

    def accept(self, dst: str, src: str) -> bool:
        with self.lock:
            ok = self.db.execute("SELECT 1 FROM friend_requests WHERE src = ? AND dst = ?", (src, dst)).fetchone()
        return bool(ok) and self.befriend(src, dst)

    def decline(self, dst: str, src: str) -> bool:
        with self.lock, self.db:
            return self.db.execute("DELETE FROM friend_requests WHERE src = ? AND dst = ?", (src, dst)).rowcount > 0

    def remove(self, x: str, y: str) -> bool:
        with self.lock, self.db:
            gone = self.db.execute("DELETE FROM friends WHERE a = ? AND b = ?", self._pair(x, y)).rowcount
            gone += self.db.execute("DELETE FROM friend_requests WHERE (src = ? AND dst = ?) OR (src = ? AND dst = ?)",
                                    (x, y, y, x)).rowcount
        return gone > 0

    def incoming(self, x: str) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT src FROM friend_requests WHERE dst = ? ORDER BY created", (x,))]

    def outgoing(self, x: str) -> list[str]:
        with self.lock:
            return [r[0] for r in self.db.execute("SELECT dst FROM friend_requests WHERE src = ? ORDER BY created", (x,))]


@dataclass
class Invite:
    id: int
    kind: str
    src: int
    src_name: str
    dst_key: str
    dst: int
    room: int = 0
    playlist: int = 0
    src_key: str = ""
    created: float = field(default_factory=time.monotonic)
    party: bool = True


class Social:

    def __init__(self, rooms, store=None, cfg: dict | None = None, log=None, clock=time.monotonic) -> None:
        self.rooms, self.store, self.cfg = rooms, store, cfg or {}
        self.log = log or (lambda line: None)
        self.clock = clock
        opts = self.cfg.get("friends") or {}
        self.everyone = bool(opts.get("everyone"))
        self.include_guests = bool(opts.get("include_guests"))
        self.groups = [{str(x) for x in g} for g in opts.get("groups", []) if isinstance(g, (list, tuple))]
        follow = opts.get("party_follow", "pull")
        self.follow = follow if follow in ("pull", "invite", "off") else "pull"
        self.requests_in_game = bool(opts.get("requests_in_game", True))
        self.db = FriendsDB(store.db) if store is not None else None
        self.lock = threading.RLock()
        self.sessions: list = []
        self.state: dict = {}
        self.status_listeners: list = []
        self.shown: dict[str, tuple[int, int]] = {}
        self.invites: dict[int, Invite] = {}
        self.ids = itertools.count(1)
        self.answers: dict = {}
        self.login_offers: list = []
        self.leader_of: dict[int, int] = {}
        self.parties: dict[int, list[int]] = {}
        self.banned: dict[int, set[int]] = {}
        self.suggested: dict[int, set[int]] = {}
        self.typing_in: dict[int, int] = {}
        self.general: dict[int, str] = {}
        self.mutes: dict[str, tuple[float, str]] = {}
        self.wall = time.time
        if self.db is not None:
            with self.db.lock:
                self.mutes = {r[0]: (r[1], r[2]) for r in self.db.db.execute("SELECT who, until, reason FROM chat_mutes")}

    @classmethod
    def of(cls, rooms, store=None, cfg: dict | None = None, log=None) -> "Social":
        hub = getattr(rooms, "social", None)
        if hub is None:
            hub = cls(rooms, store, cfg, log)
            rooms.social = hub
        elif hub.db is None and store is not None:
            hub.store, hub.db = store, FriendsDB(store.db)
        return hub

    @staticmethod
    def pid(session) -> int | None:
        return session.lobby.me

    @staticmethod
    def name(session) -> str:
        return session.player["name"] if session.player else session.player_name

    @staticmethod
    def key(session) -> str:
        return f"d:{session.account['discord_id']}" if session.account else f"g:{session.lobby.me}"

    def _sessions_for(self, key: str) -> list:
        return [s for s in self.sessions if self.key(s) == key]

    def revoke_turn(self, sessions: list) -> None:
        turn = getattr(self.rooms, "turn", None)
        for s in sessions if turn is not None else []:
            pid = s.player["id"] if getattr(s, "player", None) else getattr(s, "player_id", None)
            if pid is not None:
                turn.revoke(pid)

    def _latest(self, key: str):
        found = self._sessions_for(key)
        return found[-1] if found else None

    def _query(self, sql: str, args: tuple) -> list:
        if self.store is None:
            return []
        with self.db.lock:
            return self.store.db.execute(sql, args).fetchall()

    def stable_id(self, key: str) -> int | None:
        if key.startswith("g:"):
            return int(key[2:])
        rows = self._query("SELECT MIN(p.id) FROM players p JOIN accounts a ON a.id = p.account_id WHERE a.discord_id = ?",
                           (key[2:],))
        if rows and rows[0][0] is not None:
            return int(rows[0][0])
        s = self._latest(key)
        return self.pid(s) if s else None

    def display(self, key: str) -> str:
        s = self._latest(key)
        if s is not None:
            return self.name(s)
        fid = self.stable_id(key) if key.startswith("d:") else None
        rows = self._query("SELECT name FROM players WHERE id = ?", (fid,)) if fid else []
        if rows:
            return rows[0][0]
        rows = self._query("SELECT name FROM accounts WHERE discord_id = ?", (key[2:],))
        return rows[0][0] if rows else key[2:]

    def key_for_pid(self, pid: int) -> str | None:
        for s in self.sessions:
            if self.pid(s) == pid:
                return self.key(s)
        rows = self._query("SELECT a.discord_id FROM players p JOIN accounts a ON a.id = p.account_id WHERE p.id = ?", (pid,))
        return f"d:{rows[0][0]}" if rows else None

    def session_for_id(self, pid: int):
        for s in reversed(self.sessions):
            if self.pid(s) == pid:
                return s
        key = self.key_for_pid(pid)
        return self._latest(key) if key else None

    def resolve(self, spec: str) -> tuple[str | None, str]:
        spec = (spec or "").strip()
        if spec.isdigit() and len(spec) >= 15:
            return f"d:{spec}", ""
        if not spec:
            return None, "Give a Discord user ID or a player name."
        found = {self.key(s) for s in self.sessions if self.name(s).lower() == spec.lower() and s.account}
        for (d,) in self._query("SELECT a.discord_id FROM players p JOIN accounts a ON a.id = p.account_id "
                                "WHERE lower(p.name) = lower(?) UNION SELECT discord_id FROM accounts WHERE lower(name) = lower(?)",
                                (spec, spec)):
            found.add(f"d:{d}")
        if not found:
            return None, f"Nobody called {spec!r} has signed in on this server."
        if len(found) > 1:
            return None, f"Several players are called {spec!r}: use their Discord user ID."
        return found.pop(), ""

    def friends_of(self, key: str) -> set[str]:
        if key.startswith("g:") and not (self.everyone and self.include_guests):
            return set()
        out: set[str] = set()
        if self.everyone:
            out |= {f"d:{d}" for (d,) in self._query(
                "SELECT DISTINCT a.discord_id FROM accounts a JOIN players p ON p.account_id = a.id", ())}
            out |= {self.key(s) for s in self.sessions if s.account or self.include_guests}
        if key.startswith("d:"):
            me = key[2:]
            if self.db is not None:
                out |= {f"d:{d}" for d in self.db.friends(me)}
            for g in self.groups:
                if me in g:
                    out |= {f"d:{d}" for d in g}
        out.discard(key)
        return out

    def are_friends(self, a: str, b: str) -> bool:
        if a != b and self.everyone and (a.startswith("d:") or self.include_guests) and (b.startswith("d:") or self.include_guests):
            return True
        return self.are_real_friends(a, b)

    def are_real_friends(self, a: str, b: str) -> bool:
        if a == b or not (a.startswith("d:") and b.startswith("d:")):
            return False
        if any(a[2:] in g and b[2:] in g for g in self.groups):
            return True
        return self.db is not None and self.db.are_friends(a[2:], b[2:])

    def request_friend(self, src_key: str, dst_key: str) -> tuple[bool, str]:
        if not src_key.startswith("d:"):
            return False, "Sign in with Discord first (Options, Login with Discord): friends belong to accounts."
        if not dst_key.startswith("d:"):
            return False, "That player isn't signed in with Discord, so they can't have friends yet."
        if self.db is None:
            return False, "This server keeps no accounts."
        with self.lock:
            who = self.display(dst_key)
            if src_key == dst_key:
                return False, "That's you."
            if self.are_friends(src_key, dst_key):
                return True, f"You and {who} are already friends."
            result = self.db.request(src_key[2:], dst_key[2:])
            if result == "self":
                return False, "That's you."
            if result == "already":
                return True, f"You and {who} are already friends."
            if result == "friends":
                self.befriended(src_key, dst_key)
                return True, f"You and {who} are now friends."
            self.log(f"friend request {src_key} -> {dst_key}")
            for s in self._sessions_for(dst_key):
                self._offer_request(s, src_key)
            return True, f"Friend request sent to {who}."

    def answer_request(self, dst_key: str, src_key: str, accept: bool) -> bool:
        if self.db is None or not (dst_key.startswith("d:") and src_key.startswith("d:")):
            return False
        with self.lock:
            for inv in [i for i in self.invites.values() if i.kind == "friend" and i.src_key == src_key and i.dst_key == dst_key]:
                del self.invites[inv.id]
            if accept:
                ok = self.db.accept(dst_key[2:], src_key[2:])
                if ok:
                    self.befriended(src_key, dst_key)
                return ok
            return self.db.decline(dst_key[2:], src_key[2:])

    def remove_friend(self, a: str, b: str) -> bool:
        if self.db is None or not (a.startswith("d:") and b.startswith("d:")):
            return False
        with self.lock:
            gone = self.db.remove(a[2:], b[2:])
            for x, y in ((a, b), (b, a)):
                fid = self.stable_id(y)
                for s in self._sessions_for(x):
                    if fid is not None and fid in self.state.get(s, {}).get("sent", ()):
                        self._send(s, Packet(FRIEND_STATUS, BitWriter().uint(fid).uint(OFFLINE).bytes()))
            self.log(f"friendship {a} / {b} ended")
            return gone

    def befriended(self, a: str, b: str) -> None:
        for x, y in ((a, b), (b, a)):
            for s in self._sessions_for(x):
                self._send_rows(s, [y], FRIENDS_ADDED)
                self._notice(s, f"You and {self.display(y)} are now friends.")
        self.log(f"friends: {a} and {b}")

    def _offer_request(self, s, src_key: str) -> None:
        if not self.requests_in_game:
            return
        if any(i.kind == "friend" and i.src_key == src_key and i.dst_key == self.key(s) for i in self.invites.values()):
            return
        inv = Invite(next(self.ids), "friend", self.stable_id(src_key) or 0, self.display(src_key), self.key(s),
                     self.pid(s), playlist=-1, src_key=src_key)
        self.invites[inv.id] = inv
        w = BitWriter().uint(inv.src).string(f"{inv.src_name} (friend request)").uint(inv.id)
        self._send(s, Packet(INVITED, signed(w, -1).bool(False).bytes()))

    def offer(self, s, kind: str, text: str, tag: str) -> bool:
        with self.lock:
            if any(i.kind == kind and i.src_key == tag and i.dst_key == self.key(s) for i in self.invites.values()):
                return False
            inv = Invite(next(self.ids), kind, 0, text, self.key(s), self.pid(s) or 0, playlist=-1, src_key=tag)
            self.invites[inv.id] = inv
        w = BitWriter().uint(0).string(text).uint(inv.id)
        self._send(s, Packet(INVITED, signed(w, -1).bool(False).bytes()))
        return True

    def withdraw(self, kind: str, tag: str) -> None:
        with self.lock:
            for iid in [i for i, inv in self.invites.items() if inv.kind == kind and inv.src_key == tag]:
                del self.invites[iid]

    def status(self, session) -> tuple[int, int]:
        me = self.pid(session)
        room = self.rooms.room_of(me) if me is not None else None
        if room is not None:
            if self.rooms.is_spectator(me):
                return SPECTATING, 0
            playlist = room.settings.get("playlist", 0)
            if room.started:
                return PLAYING, playlist
            if playlist:
                return (IN_QUEUE if room.searching else IN_LOBBY), playlist
            return CUSTOM, custom_detail(room.settings.get("flags", 0))
        return self.state.get(session, {}).get("local", (ONLINE, 0))

    def key_status(self, key: str) -> tuple[int, int]:
        s = self._latest(key)
        return self.status(s) if s is not None else (OFFLINE, 0)

    def _send(self, s, p: Packet) -> None:
        try:
            s.send(p)
        except OSError:
            pass

    def _notice(self, s, text: str) -> None:
        self._send(s, Packet(NOTICE, BitWriter().string(text).bytes()))

    @staticmethod
    def server_notice_packet(text: str) -> Packet:
        return Packet(SERVER_NOTICE, BitWriter().string(text).bool(False).bytes())

    def _announce_to(self, s, text: str, popup: bool) -> None:
        if popup:
            self._notice(s, text)
        else:
            self._send(s, self.server_notice_packet(text))

    def announce(self, text: str, popup: bool = False) -> int:
        with self.lock:
            sessions = list(self.sessions)
        for s in sessions:
            self._announce_to(s, text, popup)
        self.log(f"notice to {len(sessions)} game(s): {text!r}")
        return len(sessions)

    def login_notice(self, session) -> None:
        cfg = self.cfg.get("notice")
        text, popup = (cfg.get("text", ""), bool(cfg.get("popup"))) if isinstance(cfg, dict) else (cfg or "", False)
        if isinstance(text, str) and text.strip():
            self._announce_to(session, text.strip(), popup)

    def _send_rows(self, s, keys, kind: int) -> int:
        rows = []
        for k in sorted(keys)[:MAX_LIST]:
            fid = self.stable_id(k)
            if fid is not None:
                rows.append((fid, self.display(k), *self.key_status(k), self.portrait(fid)))
        w = BitWriter()
        if kind == FRIENDS_ADDED:
            w.uint(len(rows))
        for row in rows:
            if kind == FRIEND_LIST:
                w.bool(True)
            write_row(w, *row)
        if kind == FRIEND_LIST:
            w.bool(False)
        if rows:
            self._send(s, Packet(kind, w.bytes()))
        self.state.setdefault(s, {"local": (ONLINE, 0), "sent": set()})["sent"].update(r[0] for r in rows)
        return len(rows)

    def refresh(self) -> None:
        with self.lock:
            latest = {}
            for s in self.sessions:
                latest[self.key(s)] = s
            keys = set(latest) | set(self.shown)
            for key in sorted(keys):
                s = latest.get(key)
                now = self.status(s) if s is not None else (OFFLINE, 0)
                if self.shown.get(key) == now:
                    continue
                if now[0] == OFFLINE:
                    self.shown.pop(key, None)
                else:
                    self.shown[key] = now
                for listener in self.status_listeners:
                    try:
                        listener(key, now)
                    except Exception as e:
                        self.log(f"status listener failed for {key}: {e!r}")
                fid = self.stable_id(key)
                if fid is None:
                    continue
                for fk in self.friends_of(key):
                    for s in self._sessions_for(fk):
                        if fid in self.state.get(s, {}).get("sent", ()):
                            w = BitWriter().uint(fid).uint(now[0])
                            if now[0] in WITH_DETAIL:
                                w.uint(now[1])
                            self._send(s, Packet(FRIEND_STATUS, w.bytes()))
                        elif now[0] != OFFLINE:
                            self._send_rows(s, [key], FRIENDS_ADDED)

    def players_online(self) -> int:
        with self.lock:
            return len({self.key(s) for s in self.sessions})

    def activity(self) -> dict:
        from .playlists import BY_ID
        counts = {"online": 0, "menu": 0, "training": 0, "couch": 0, "custom": 0, "spectating": 0, "tournament": 0,
                  "casual": 0, "ranked": 0}
        playlists: dict[str, int] = {}
        with self.lock:
            keys = {self.key(s) for s in self.sessions}
            for key in keys:
                status, detail = self.key_status(key)
                if status == OFFLINE:
                    continue
                counts["online"] += 1
                if status in (IN_QUEUE, IN_LOBBY) or (status == PLAYING and detail):
                    playlists[str(detail)] = playlists.get(str(detail), 0) + 1
                    pl = BY_ID.get(detail)
                    counts["ranked" if pl is not None and pl.ranked else "casual"] += 1
                elif status in (CUSTOM, PLAYING):
                    counts["custom"] += 1
                else:
                    counts[{TRAINING: "training", COUCH: "couch", SPECTATING: "spectating",
                            TOURNAMENT: "tournament"}.get(status, "menu")] += 1
        counts["offline"] = counts["training"] + counts["couch"]
        counts["playlists"] = playlists
        return counts

    def online(self, session) -> None:
        if self.pid(session) is None:
            return
        with self.lock:
            if session not in self.sessions:
                self.sessions.append(session)
            st = self.state.setdefault(session, {"local": (ONLINE, 0), "sent": set()})
            st["sent"] = set()
            key = self.key(session)
            n = self._send_rows(session, self.friends_of(key), FRIEND_LIST)
            session.log(f"friends: sent player {self.pid(session)} its list of {n} (2406)")
            if self.db is not None and key.startswith("d:"):
                for src in self.db.incoming(key[2:]):
                    self._offer_request(session, f"d:{src}")
            self.refresh()
        for offer in list(self.login_offers):
            try:
                offer(session)
            except Exception as e:
                session.log(f"login offers: {e!r}")
        if self.muted(key):
            self._send(session, self.restriction_packet(True))
            self._tell_muted(session)

    @staticmethod
    def restriction_packet(on: bool) -> Packet:
        return Packet(CHAT_RESTRICTED, BitWriter().bool(on).bytes())

    def muted(self, key: str) -> tuple[float, str] | None:
        m = self.mutes.get(key)
        if m is None:
            return None
        if m[0] and m[0] <= self.wall():
            self.unmute(key)
            return None
        return m

    def _tell_muted(self, s) -> None:
        m = self.muted(self.key(s))
        if m is None:
            return
        left = "" if not m[0] else f" for {max(1, round((m[0] - self.wall()) / 60))} more minute(s)"
        self._notice(s, f"The server has muted your chat{left}." + (f" Reason: {m[1]}" if m[1] else ""))

    def mute(self, key: str, minutes: float = 0, reason: str = "") -> int:
        until = self.wall() + minutes * 60 if minutes > 0 else 0.0
        self.mutes[key] = (until, reason)
        if self.db is not None:
            with self.db.lock, self.db.db:
                self.db.db.execute("INSERT OR REPLACE INTO chat_mutes VALUES (?, ?, ?)", (key, until, reason))
        with self.lock:
            found = self._sessions_for(key)
        for s in found:
            self._send(s, self.restriction_packet(True))
            self._tell_muted(s)
        self.log(f"chat mute: {self.display(key)} ({key}) " + (f"for {minutes:g} min" if until else "until unmuted")
                 + (f": {reason}" if reason else ""))
        return len(found)

    def unmute(self, key: str) -> bool:
        if self.mutes.pop(key, None) is None:
            return False
        if self.db is not None:
            with self.db.lock, self.db.db:
                self.db.db.execute("DELETE FROM chat_mutes WHERE who = ?", (key,))
        with self.lock:
            found = self._sessions_for(key)
        for s in found:
            self._send(s, self.restriction_packet(False))
            self._notice(s, "The server has unmuted your chat.")
        self.log(f"chat mute lifted: {self.display(key)} ({key})")
        return True

    def error_key(self, s, key: str, toast: bool = False) -> None:
        self._send(s, Packet(SERVER_ERROR, BitWriter().string(key).bool(toast).bytes()))

    def kick(self, key: str, reason: str = "") -> int:
        text = "The server has signed you out." + (f" Reason: {reason}" if reason else "")
        with self.lock:
            found = self._sessions_for(key)
        for s in found:
            self._send(s, Packet(SERVER_MESSAGE, BitWriter().string(text).bool(False).bytes()))
            self._send(s, Packet(TO_OFFLINE, BitWriter().uint(0).bytes()))
        self.revoke_turn(found)
        self.log(f"kicked {self.display(key)} ({key}) from the server: {len(found)} game(s)" + (f": {reason}" if reason else ""))
        return len(found)

    def lift_expired(self) -> None:
        for key in [k for k, m in list(self.mutes.items()) if m[0] and m[0] <= self.wall()]:
            self.unmute(key)

    def keys_named(self, name: str) -> list[str]:
        want = name.strip().lower()
        with self.lock:
            seen = {self.key(s): self.name(s) for s in self.sessions}
        exact = [k for k, n in seen.items() if n.lower() == want]
        return exact or [k for k, n in seen.items() if n.lower().startswith(want)]

    def portrait(self, player_id: int) -> int:
        values = getattr(self.rooms, "cosmetics", {}).get(player_id)
        return values[0] if values else 0

    def renamed(self, session) -> None:
        if self.pid(session) is None:
            return
        with self.lock:
            key = self.key(session)
            fid = self.stable_id(key)
            for fk in self.friends_of(key):
                for s in self._sessions_for(fk):
                    if fid in self.state.get(s, {}).get("sent", ()):
                        self._send_rows(s, [key], FRIENDS_ADDED)

    def account_changed(self, session) -> None:
        if session in self.sessions:
            with self.lock:
                self.online(session)

    def offline(self, session) -> None:
        with self.lock:
            if session in self.sessions:
                self.sessions.remove(session)
            self.state.pop(session, None)
            me = self.pid(session)
            if me is not None and not any(self.pid(s) == me for s in self.sessions):
                self._leave_party(me)
                for inv in [i for i in self.invites.values() if i.dst == me or (i.kind == "room" and i.src == me)]:
                    del self.invites[inv.id]
            self.refresh()
            stopped = me is not None and me in self.typing_in and not any(self.pid(s) == me for s in self.sessions)
        if stopped:
            self._typing(session, False)
        if me is not None and not any(self.pid(s) == me for s in self.sessions):
            self._leave_general(session, me)

    def party(self, pid: int) -> list[int]:
        with self.lock:
            leader = self.leader_of.get(pid)
            return list(self.parties.get(leader, [])) if leader is not None else []

    def _room_leader(self, room) -> int:
        host = room.host
        lead = self.leader_of.get(host)
        if lead is not None and lead != host and self.rooms.room_of(lead) is not room:
            self._leave_party(host)
            lead = None
        return host if lead is None else lead

    def _join_party(self, leader: int, pid: int) -> None:
        if pid == leader or self.leader_of.get(pid) == leader:
            return
        self._leave_party(pid)
        if leader not in self.parties:
            self.parties[leader] = [leader]
            self.leader_of[leader] = leader
        self.parties[leader].append(pid)
        self.leader_of[pid] = leader
        self.log(f"party of {leader}: {self.parties[leader]}")

    def _leave_party(self, pid: int) -> None:
        leader = self.leader_of.pop(pid, None)
        if leader is None:
            return
        if leader == pid:
            for m in self.parties.pop(pid, []):
                self.leader_of.pop(m, None)
            self.log(f"party of {pid} ended")
            return
        members = self.parties.get(leader, [])
        if pid in members:
            members.remove(pid)
        if len(members) <= 1:
            self.parties.pop(leader, None)
            self.leader_of.pop(leader, None)

    def _move_into(self, session, room) -> str | None:
        me = self.pid(session)
        if room is None or room.number not in self.rooms.by_number:
            return "the room is gone"
        if me in room.ids():
            return None
        if room.started or room.searching:
            return "the room has already started" if room.started else "the room is already searching"
        if len(room.members) >= room.limit:
            return "the room is full"
        if me in self.banned.get(room.number, ()):
            return "you are banned from that room"
        if self.rooms.room_of(me) is not None:
            session.lobby.leave(notify_self=True)
        joined, why = self.rooms.join(room.number, session.lobby.member())
        if joined is None:
            return why
        lobby.assign_teams(joined)
        session.lobby.send_members_items(joined)
        self._send(session, lobby.room_packet(joined))
        entry = next(m for m in joined.members if m.player_id == me)
        w = BitWriter().bool(False)
        lobby.write_entry(w, entry)
        w.bool(False)
        self.rooms.broadcast(joined, Packet(lobby.JOINED, w.bytes()), but=me)
        session.lobby.room_ratings(joined)
        return None

    def lobby_packet(self, session, p: Packet, handle) -> bool:
        if p.type not in lobby.HANDLED:
            return False
        me = self.pid(session)
        if p.type == lobby.JOIN and me is not None:
            number = BitReader(p.payload).uint()
            if me in self.banned.get(number, ()):
                session.send(lobby.failed(f"Room {number}: the host banned you from this room."))
                return True
        before = self.rooms.room_of(me) if me is not None else None
        took = handle(p)
        if not took or me is None:
            return took
        after = self.rooms.room_of(me)
        with self.lock:
            leader = self.leader_of.get(me)
            if p.type in (lobby.CREATE, lobby.JOIN, lobby.SPECTATE) and after is not None and after is not before:
                if leader is not None and leader != me and self.rooms.room_of(leader) is not after:
                    self._leave_party(me)
                elif leader == me and p.type != lobby.SPECTATE and not self.rooms.is_spectator(me):
                    self._bring_party(session, after)
            elif p.type in (lobby.LEAVE_LOBBY, lobby.LEAVE_SLOT) and before is not None:
                if leader is not None and leader != me and self.rooms.room_of(leader) is before:
                    self._leave_party(me)
            self.refresh()
        return True

    def _busy(self, s) -> bool:
        if self.state.get(s, {}).get("local", (ONLINE, 0))[0] in LOCAL:
            return True
        room = self.rooms.room_of(self.pid(s))
        return room is not None and (room.started or room.searching)

    def _bring_party(self, leader_session, room) -> None:
        if self.follow == "off":
            return
        leader = self.pid(leader_session)
        for m in list(self.parties.get(leader, []))[1:]:
            ms = next((s for s in reversed(self.sessions) if self.pid(s) == m), None)
            if ms is None or m in room.ids():
                continue
            if self.follow == "invite" or self._busy(ms):
                self._invite(leader_session, ms, room, check_friends=False)
                continue
            why = self._move_into(ms, room)
            if why is None:
                ms.log(f"party: player {m} followed its leader {leader} into room {room.number}")
            else:
                self._notice(ms, f"Couldn't follow {self.name(leader_session)}: {why}.")
                ms.log(f"party: player {m} couldn't follow {leader} into room {room.number} ({why})")

    def handle(self, session, p: Packet) -> bool:
        if p.type == TYPING:
            if self.pid(session) is not None:
                try:
                    self._typing(session, BitReader(p.payload).bool())
                except IndexError:
                    pass
            return True
        if p.type not in HANDLED:
            return False
        if self.pid(session) is None:
            session.log(f"social packet {p.type} before login: ignored")
            return True
        if p.type in ROOM_MOVES and hasattr(session, "whitelist") and not session.whitelist.allows(session.account):
            session.log(f"packet {p.type} refused (whitelisted server)")
            session.notice(session.whitelist_refusal())
            return True
        r = BitReader(p.payload)
        try:
            with self.lock:
                self._dispatch(session, p.type, r)
                self.refresh()
        except IndexError:
            session.log(f"social packet {p.type} too short ({len(p.payload)} bytes)")
        return True

    def _fail(self, session, code: int, kind: int = ACTION_FAILED) -> None:
        self._send(session, Packet(kind, BitWriter().uint(code).bytes()))

    def _dispatch(self, session, t: int, r: BitReader) -> None:
        me, log = self.pid(session), session.log
        self._expire()
        if t == LOCAL_STATUS:
            status, detail = r.uint(), r.uint()
            st = self.state.setdefault(session, {"local": (ONLINE, 0), "sent": set()})
            st["local"] = (status, detail) if status in LOCAL else (ONLINE, 0)
            log(f"player {me} local status {status}")
        elif t in (INVITE, INVITE_PLAYLIST):
            target = r.uint()
            playlist = r.uint() if t == INVITE_PLAYLIST else None
            ts = self.session_for_id(target)
            room = self.rooms.room_of(me)
            if room is None or self.rooms.is_spectator(me):
                log(f"invite of {target} from outside a room: refused (2404)")
                return self._fail(session, JOIN_FAIL_INVITE)
            if ts is None:
                log(f"invite of {target}: not online (2404)")
                return self._fail(session, JOIN_FAIL_INVITE)
            if self.pid(ts) in room.ids():
                return self._fail(session, JOIN_FAIL_SAME_GROUP)
            if room.host != me:
                return self._suggest(session, ts, room)
            self._invite(session, ts, room, check_friends=True, playlist=playlist)
        elif t == INVITE_ACCEPT:
            iid = r.uint()
            read_signed(r)
            self._accept(session, iid)
        elif t == INVITE_DECLINE:
            iid = r.uint()
            inv = self.invites.get(iid)
            if inv is None or inv.dst_key != self.key(session):
                return
            del self.invites[iid]
            if inv.kind == "friend":
                self.answer_request(inv.dst_key, inv.src_key, accept=False)
                log(f"player {me} declined {inv.src_key}'s friend request")
                return
            if inv.kind in self.answers:
                return self.answers[inv.kind](session, inv, False)
            room = self.rooms.by_number.get(inv.room)
            if room is not None:
                self.rooms.broadcast(room, Packet(INVITE_DECLINED, BitWriter().uint(me).string(self.name(session)).bytes()),
                                     spectators=False)
            log(f"player {me} declined the invite to room {inv.room} (2443)")
        elif t in (JOIN_FRIEND, SPECTATE_FRIEND):
            target = r.uint()
            self._to_friend(session, target, spectate=t == SPECTATE_FRIEND)
        elif t in (KICK, BAN):
            target = r.uint()
            self._kick(session, target, ban=t == BAN)
        elif t == MAKE_LEADER:
            self._make_leader(session, r.uint())
        elif t == CHANNEL_CHAT:
            channel, text = r.uint(), r.string()[:MAX_CHAT]
            if channel == GENERAL:
                self._general_say(session, text)
            else:
                log(f"channel chat for channel {channel} from {me}: not a channel here (ignored)")
        elif t == PARTY_CHAT:
            r.uint()
            sub, text = r.byte(), r.string()[:MAX_CHAT]
            self._chat(session, sub, text)
        elif t == FRIENDS_CMD:
            action, who = r.string()[:20], r.string()[:60]
            ok, text = self.window_action(session, action, who)
            self._send(session, Packet(FRIENDS_STATE, BitWriter().string(json.dumps(
                self.friends_state(self.key(session), ok, text), separators=(",", ":"))).bytes()))
        elif t in (FRIEND_ADD, FRIEND_REMOVE):
            target = r.uint()
            other = self.key_for_pid(target)
            if other is None:
                return self._notice(session, "That player isn't signed in with Discord, so they can't have friends yet.")
            if t == FRIEND_ADD:
                ok, text = self.request_friend(self.key(session), other)
                self._notice(session, text)
            else:
                gone = self.remove_friend(self.key(session), other)
                self._notice(session, f"You and {self.display(other)} are no longer friends." if gone else "You weren't friends.")

    def _expire(self) -> None:
        now = self.clock()
        for inv in [i for i in self.invites.values() if i.kind == "room" and now - i.created > INVITE_TTL]:
            del self.invites[inv.id]

    def _invite(self, session, ts, room, check_friends: bool, playlist: int | None = None, party: bool = True) -> None:
        me, target = self.pid(session), self.pid(ts)
        if check_friends and not self.are_friends(self.key(session), self.key(ts)) \
                and target not in self.suggested.get(room.number, ()):
            session.log(f"invite of {target}: not a friend (2404)")
            return self._fail(session, JOIN_FAIL_INVITE)
        if target in reports.muted_by(self.store, me):
            session.log(f"invite of {target}: {target} muted {me} (2404)")
            return self._fail(session, JOIN_FAIL_INVITE)
        for old in [i for i in self.invites.values() if i.kind == "room" and i.dst == target and i.room == room.number]:
            del self.invites[old.id]
        playlist = room.settings.get("playlist", 0) if playlist is None else playlist
        inv = Invite(next(self.ids), "room", me, self.name(session), self.key(ts), target, room.number, playlist,
                     self.key(session), self.clock(), party)
        self.invites[inv.id] = inv
        w = BitWriter().uint(me).string(inv.src_name).uint(inv.id)
        self._send(ts, Packet(INVITED, signed(w, playlist).bool(False).bytes()))
        w = BitWriter().uint(me).string(inv.src_name).uint(target).uint(0).string(self.name(ts))
        self.rooms.broadcast(room, Packet(LOBBY_INVITED, signed(w, playlist).bool(False).bytes()), spectators=False)
        session.log(f"player {me} invited {target} to room {room.number} (2439, 2440)")

    def server_invite(self, ts, room, src_name: str, party: bool = False) -> None:
        target = self.pid(ts)
        for old in [i for i in self.invites.values() if i.kind == "room" and i.dst == target and i.room == room.number]:
            del self.invites[old.id]
        playlist = room.settings.get("playlist", 0)
        inv = Invite(next(self.ids), "room", room.host or 0, src_name, self.key(ts), target, room.number, playlist,
                     "", self.clock(), party)
        self.invites[inv.id] = inv
        w = BitWriter().uint(inv.src).string(src_name).uint(inv.id)
        self._send(ts, Packet(INVITED, signed(w, playlist).bool(False).bytes()))
        if room.members:
            w = BitWriter().uint(inv.src).string(src_name).uint(target).uint(0).string(self.name(ts))
            self.rooms.broadcast(room, Packet(LOBBY_INVITED, signed(w, playlist).bool(False).bytes()), spectators=False)
        self.log(f"server invite of player {target} to room {room.number} ({src_name}; 2439)")

    def _suggest(self, session, ts, room) -> None:
        host = next((s for s in reversed(self.sessions) if self.pid(s) == room.host), None)
        if host is None:
            return self._fail(session, JOIN_FAIL_SUGGESTER_GONE)
        target = self.pid(ts)
        self.suggested.setdefault(room.number, set()).add(target)
        w = BitWriter().uint(next(self.ids)).uint(target).string(self.name(session)).uint(target).uint(0).string(self.name(ts))
        self._send(host, Packet(SUGGESTED, signed(w, room.settings.get("playlist", 0)).bytes()))
        session.log(f"player {self.pid(session)} suggested {target} to the host of room {room.number} (2441)")

    def _accept(self, session, iid: int) -> None:
        me = self.pid(session)
        inv = self.invites.pop(iid, None)
        if inv is None or inv.dst_key != self.key(session):
            session.log(f"player {me} accepted an invite that isn't there ({iid}): 2404")
            return self._fail(session, JOIN_FAIL_JOIN)
        if inv.kind == "friend":
            if self.answer_request(inv.dst_key, inv.src_key, accept=True):
                session.log(f"player {me} accepted {inv.src_key}'s friend request")
            return
        if inv.kind in self.answers:
            return self.answers[inv.kind](session, inv, True)
        room = self.rooms.by_number.get(inv.room)
        why = self._move_into(session, room)
        if why is not None:
            session.log(f"player {me} couldn't take the invite to room {inv.room}: {why} (2404)")
            if why == "the room is full":
                self.error_key(session, "Error_INVITE_FAILURE_ROOM_FULL")
            else:
                self._notice(session, f"Couldn't join: {why}.")
            return self._fail(session, JOIN_FAIL_JOIN)
        self.rooms.broadcast(room, Packet(INVITE_ACCEPTED, BitWriter().uint(me).string(self.name(session)).bytes()),
                             spectators=False)
        if inv.party:
            self._join_party(self._room_leader(room), me)
        session.log(f"player {me} accepted the invite into room {inv.room} (2445, 2442)")

    def _to_friend(self, session, target: int, spectate: bool) -> None:
        me, code = self.pid(session), JOIN_FAIL_SPECTATE if spectate else JOIN_FAIL_JOIN
        fs = self.session_for_id(target)
        if fs is None or not self.are_friends(self.key(session), self.key(fs)):
            session.log(f"{'spectate' if spectate else 'join'} friend {target}: not an online friend (2404)")
            return self._fail(session, code)
        room = self.rooms.room_of(self.pid(fs))
        if room is None or self.rooms.is_spectator(self.pid(fs)):
            return self._fail(session, code)
        if me in room.ids():
            return self._fail(session, JOIN_FAIL_SAME_GROUP)
        custom = room.settings.get("playlist", 0) == 0
        if custom and not custom_detail(room.settings.get("flags", 0)) & (PUBLIC | FRIENDS_MAY_JOIN):
            return self._fail(session, code, PRIVACY_FAILED)
        if spectate:
            if not custom:
                return self._fail(session, code)
            who = session.lobby.member()
            who.mods = getattr(session.lobby, "last_mods", None)
            refusal = spectate_refusal(room, who)
            if refusal is not None:
                session.log(f"spectate friend {target}: content mods differ (2446)")
                self._send(session, lobby.failed(refusal))
                return
            if self.rooms.room_of(me) is not None:
                session.lobby.leave(notify_self=True)
            joined, why = self.rooms.spectate(room.number, who)
            if joined is None:
                session.log(f"spectate friend {target}: {why} (2404)")
                return self._fail(session, code)
            self._send(session, lobby.room_packet(joined, spectator=True))
            session.log(f"player {me} is spectating friend {target} in room {room.number}")
            member = next((s for s in joined.spectators if s.player_id == me), None)
            from . import spectate
            if joined.started and member is not None:
                spectate.late_ticket(self.rooms, joined, member, session.log)
            return
        why = self._move_into(session, room)
        if why is not None:
            session.log(f"join friend {target}: {why} (2404)")
            return self._fail(session, code)
        self._join_party(self._room_leader(room), me)
        session.log(f"player {me} joined friend {target} in room {room.number}")

    def _kick(self, session, target: int, ban: bool) -> None:
        me = self.pid(session)
        room = self.rooms.room_of(me)
        if room is None or room.host != me or target == me or target not in room.ids():
            return session.log(f"{'ban' if ban else 'kick'} of {target}: not this host's to do (ignored)")
        if ban:
            self.banned.setdefault(room.number, set()).add(target)
        ts = next((s for s in reversed(self.sessions) if self.pid(s) == target), None)
        if ts is not None:
            ts.lobby.leave(notify_self=False)
            ts.send(Packet(KICKED, BitWriter().uint(1 if ban else 2).uint(0).bytes()))
        else:
            self.rooms.leave(target)
            self.rooms.broadcast(room, Packet(lobby.LEFT_OTHER, BitWriter().bool(False).uint(target).uint(0).bool(False).bytes()))
        if self.leader_of.get(target) not in (None, target):
            self._leave_party(target)
        session.log(f"player {me} {'banned' if ban else 'kicked'} {target} from room {room.number}")

    def _make_leader(self, session, target: int) -> None:
        me = self.pid(session)
        room = self.rooms.room_of(me)
        if room is None or room.host != me or target == me or target not in room.ids():
            return session.log(f"make {target} leader: not this host's to do (ignored)")
        with self.rooms.lock:
            room.host = target
        self.rooms.broadcast(room, Packet(lobby.NEW_HOST, BitWriter().uint(target).uint(0).bytes()))
        if me in self.parties:
            members = self.parties.pop(me)
            if target in members:
                members.remove(target)
            self.parties[target] = [target] + members
            for m in self.parties[target]:
                self.leader_of[m] = target
        session.log(f"room {room.number}: player {me} made {target} the leader (2455)")

    def _chat(self, session, sub: int, text: str) -> None:
        me = self.pid(session)
        room = self.rooms.room_of(me)
        if text.startswith(("!", "/")):
            return self._command(session, text)
        if room is None or not text.strip():
            return
        if self.muted(self.key(session)):
            session.log(f"party chat in room {room.number} from {me} dropped: muted by the server")
            return self._tell_muted(session)
        n = self.rooms.broadcast(room, Packet(CHAT, BitWriter().uint(me).byte(sub & 0xFF).bool(True).string(text).bytes()))
        session.log(f"party chat in room {room.number} from {me} ({n} got it)")

    def _typing(self, session, typing: bool) -> None:
        me = self.pid(session)
        with self.lock:
            if typing:
                room = self.rooms.room_of(me)
                if room is None:
                    return
                self.typing_in[me] = room.number
            else:
                number = self.typing_in.pop(me, None)
                room = self.rooms.by_number.get(number) if number is not None else None
                if room is None:
                    return
        skip = reports.muted_by(self.store, me)
        self.rooms.broadcast(room, Packet(TYPING_SHOWN, BitWriter().uint(me).bool(typing).bytes()), but=me, skip=skip)

    def _status(self, session, text: str) -> None:
        self._send(session, Packet(STATUS_LINE, BitWriter().string(text).bytes()))

    def _general_sessions(self) -> list:
        with self.lock:
            return [s for s in self.sessions if self.pid(s) in self.general]

    def _join_general(self, session) -> bool:
        me = self.pid(session)
        if me is None or me in self.general:
            return False
        with self.lock:
            self.general[me] = self.name(session)
            rows = list(self.general.items())
        w = BitWriter().uint(GENERAL).uint(len(rows))
        for pid, name in rows:
            w.uint(pid).string(name).string("")
        self._send(session, Packet(CH_MEMBERS, w.bytes()))
        joined = Packet(CH_JOINED, BitWriter().uint(GENERAL).uint(me).string(self.name(session)).string("").bytes())
        for s in self._general_sessions():
            self._send(s, joined)
        session.log(f"player {me} joined the General channel ({len(rows)} in it; 2417, 2416)")
        return True

    def _leave_general(self, session, me: int) -> bool:
        with self.lock:
            name = self.general.pop(me, None)
        if name is None:
            return False
        left = Packet(CH_LEFT, BitWriter().uint(GENERAL).uint(me).string(name).bytes())
        for s in self._general_sessions() + [x for x in [session] if self.pid(x) == me and x in self.sessions]:
            self._send(s, left)
        self.log(f"player {me} left the General channel (2415)")
        return True

    def _general_say(self, session, text: str) -> None:
        me = self.pid(session)
        if me is None or not text.strip():
            return
        if self.muted(self.key(session)):
            session.log(f"General channel line from {me} dropped: muted by the server")
            return self._tell_muted(session)
        self._join_general(session)
        skip = reports.muted_by(self.store, me)
        line = Packet(CH_MESSAGE, BitWriter().uint(me).uint(GENERAL).string(text).bytes())
        targets = [s for s in self._general_sessions() if self.pid(s) not in skip]
        for s in targets:
            self._send(s, line)
        session.log(f"General channel line from {me} ({len(targets)} got it)")

    def _command(self, session, text: str) -> None:
        word, _, rest = text[1:].partition(" ")
        word = word.lower()
        if word in ("g", "general"):
            if rest.strip():
                return self._general_say(session, rest.strip())
            if not self._join_general(session):
                self._status(session, "You are in the General channel. !g TEXT says something there, !leave leaves it.")
            return
        if word == "leave":
            if not self._leave_general(session, self.pid(session)):
                self._status(session, "You aren't in the General channel (!g TEXT joins it).")
            return
        if word in ("friend", "addfriend", "unfriend", "removefriend"):
            other, why = self.resolve(rest)
            if other is None:
                return self._notice(session, why)
            if word in ("friend", "addfriend"):
                return self._notice(session, self.request_friend(self.key(session), other)[1])
            gone = self.remove_friend(self.key(session), other)
            return self._notice(session, f"You and {self.display(other)} are no longer friends." if gone else "You weren't friends.")
        if word == "friends":
            keys = self.friends_of(self.key(session))
            on = [f"{self.display(k)} ({STATUS_TEXT[self.key_status(k)[0]]})" for k in sorted(keys) if self.key_status(k)[0] != OFFLINE]
            return self._notice(session, "Friends online: " + (", ".join(on) if on else "none") + f" ({len(keys)} friends)")
        self._status(session, "Commands: !g TEXT (the General channel), !leave, !friend NAME, !unfriend NAME, !friends")

    def window_action(self, session, action: str, who: str) -> tuple[bool, str]:
        key = self.key(session)
        if action == "list":
            return True, ""
        if not key.startswith("d:"):
            return False, "Sign in with Discord first (Options > Account): friends belong to accounts."
        if action == "add":
            other, why = self.resolve(who)
            return (False, why) if other is None else self.request_friend(key, other)
        if not (0 < len(who) <= 64 and all(c.isascii() and (c.isalnum() or c in "-_") for c in who)):
            return False, "Unknown player."
        other = f"d:{who}"
        if action in ("accept", "decline"):
            ok = self.answer_request(key, other, accept=action == "accept")
            if not ok:
                return False, "There's no such request."
            return True, (f"You and {self.display(other)} are now friends." if action == "accept"
                          else f"Declined {self.display(other)}'s request.")
        if action == "cancel":
            gone = self.db is not None and self.db.decline(other[2:], key[2:])
            for inv in [i for i in self.invites.values() if i.kind == "friend" and i.src_key == key and i.dst_key == other]:
                del self.invites[inv.id]
            return gone, (f"Request to {self.display(other)} withdrawn." if gone else "There's no such request.")
        if action == "remove":
            gone = self.remove_friend(key, other)
            return gone, (f"You and {self.display(other)} are no longer friends." if gone else "You weren't friends.")
        return False, "Unknown action."

    def friends_state(self, key: str, ok: bool = True, message: str = "") -> dict:
        def entry(k: str) -> dict:
            status, _ = self.key_status(k)
            return {"id": k[2:] if k.startswith("d:") else "", "pid": self.stable_id(k) or 0, "name": self.display(k),
                    "status": STATUS_TEXT[status], "online": status != OFFLINE}
        with self.lock:
            mine = self.friends_of(key)
            db_friends = self.db.friends(key[2:]) if self.db is not None and key.startswith("d:") else set()
            friends = []
            for k in mine:
                e = entry(k)
                e["removable"] = k.startswith("d:") and k[2:] in db_friends
                friends.append(e)
            friends.sort(key=lambda e: (not e["online"], e["name"].lower()))
            incoming = [entry("d:" + d) for d in self.db.incoming(key[2:])] if self.db is not None and key.startswith("d:") else []
            outgoing = [entry("d:" + d) for d in self.db.outgoing(key[2:])] if self.db is not None and key.startswith("d:") else []
            pending = {e["id"] for e in incoming + outgoing}
            recent = []
            for p in self.played_with_rows(key):
                if "d:" + p["id"] in mine or p["id"] in pending:
                    continue
                e = entry("d:" + p["id"])
                e["ago"] = max(0, int(time.time() - p["time"]))
                e["teammate"] = p["teammate"]
                recent.append(e)
            recent = [e for e in recent if e["online"]] + [e for e in recent if not e["online"]]
            seen, online = {"d:" + e["id"] for e in recent}, []
            for s in reversed(self.sessions):
                k = self.key(s)
                if k == key or k in mine or k in seen or not k.startswith("d:") or k[2:] in pending:
                    continue
                seen.add(k)
                online.append(entry(k))
            online.sort(key=lambda e: e["name"].lower())
        return {"signed_in": key.startswith("d:"), "me": key[2:] if key.startswith("d:") else "", "everyone": self.everyone,
                "friends": friends[:MAX_LIST], "incoming": incoming[:50], "outgoing": outgoing[:50], "online": online[:40],
                "recent": recent[:RECENT_MAX], "ok": ok, "message": message}

    def played_with(self, key: str) -> list[str]:
        return [p["id"] for p in self.played_with_rows(key)]

    def played_with_rows(self, key: str) -> list[dict]:
        if self.store is None or not key.startswith("d:"):
            return []
        try:
            rows = self.store.db.execute(
                "SELECT a.discord_id AS d, MAX(r.time) AS t, "
                "COALESCE(mine.team, 0) != 0 AND o.team = mine.team AS mate FROM accounts me "
                "JOIN result_players mine ON mine.account_id = me.id "
                "JOIN result_players o ON o.result_id = mine.result_id AND o.account_id IS NOT NULL AND o.account_id != me.id "
                "JOIN results r ON r.id = mine.result_id JOIN accounts a ON a.id = o.account_id "
                "WHERE me.discord_id = ? AND r.time > ? GROUP BY a.discord_id ORDER BY t DESC LIMIT ?",
                (key[2:], time.time() - RECENT_DAYS * 86400, MAX_LIST)).fetchall()
        except Exception:
            return []
        return [{"id": str(r[0]), "time": float(r[1]), "teammate": bool(r[2])} for r in rows if str(r[0]) != key[2:]]

