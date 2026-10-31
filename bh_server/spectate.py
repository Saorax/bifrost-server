from __future__ import annotations

from collections import deque
import secrets
import threading
import time

from . import lobby
from .bits import BitReader, BitWriter
from .frame import Packet

WATCHED_END = 10200
TICKET, HELLO, QUIT = 2420, 10202, 10201
START, INPUTS, BEGIN, ABRUPT = 10104, 10106, 10107, 10102
UP_START, UP_INPUTS, UP_END = 11080, 11081, 11082
HANDLED = {UP_START, UP_INPUTS, UP_END, HELLO, QUIT, WATCHED_END}
CLOSE_STREAMS = False
END_GRACE = 6.0
STALE_FEED = 600.0
MAX_BATCHES = 20000
MAX_SPARES = 4000
MAX_UPLOAD = 16 * 1024
MAX_FEED_BYTES = 24 * 1024 * 1024


def ticket_packet(player_id: int, ticket: str, room: int, host: str = "", port: int = 0) -> Packet:
    return Packet(TICKET, BitWriter().uint(player_id).string(host).small(port).string(ticket).uint(room).bytes())


def read_ticket(payload: bytes) -> dict:
    r = BitReader(payload)
    return {"player": r.uint(), "host": r.string(), "port": r.small(), "ticket": r.string(), "room": r.uint()}


def read_hello(payload: bytes) -> dict:
    r = BitReader(payload)
    return {"player": r.uint(), "ticket": r.string()}


def read_end(payload: bytes) -> tuple[bool, int]:
    if not payload:
        return False, 0
    try:
        r = BitReader(payload)
        return r.bool(), r.uint()
    except (IndexError, ValueError):
        return False, 0


def read_rows(payload: bytes) -> list[tuple[int, int, int]]:
    r = BitReader(payload)
    rows = []
    for _ in range(r.uint()):
        ent = r.bits_(4)
        rows.append((r.uint(), ent, r.bits_(14)))
    return rows


def rows_body(rows: list[tuple[int, int, int]]) -> bytes:
    w = BitWriter().uint(len(rows))
    for t, ent, inp in rows:
        w.bits_(4, ent).uint(t).bits_(14, inp)
    return w.bytes()


def newest_time(payload: bytes) -> int:
    try:
        return max((t for t, _, _ in read_rows(payload)), default=-1)
    except (IndexError, ValueError):
        return -1


def abrupt_packet(at: int) -> Packet:
    return Packet(ABRUPT, BitWriter().uint(at).bytes())


def begin_packet() -> Packet:
    return Packet(BEGIN, BitWriter().bool(True).bytes())


class Feed:

    def __init__(self, room: int, seed: int, start: bytes) -> None:
        self.room, self.seed, self.start = room, seed, start
        self.batches: list[bytes] = []
        self.size = 0
        self.streams: list = []
        self.closers: list = []
        self.tickets: dict[str, int] = {}
        self.ended = False
        self.abrupt = False
        self.uploader: int | None = None
        self.newest = -1
        self.spares: dict[int, deque] = {}
        self.touched = time.monotonic()

    def catch_up(self, send, begin_last: bool = False) -> None:
        send(Packet(START, self.start))
        if not begin_last:
            send(begin_packet())
        for b in self.batches:
            send(Packet(INPUTS, b))
        if begin_last:
            send(begin_packet())


class Hub:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.feeds: dict[int, Feed] = {}

    def prune(self, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        stale = [n for n, f in self.feeds.items() if now - f.touched > STALE_FEED]
        for n in stale:
            self.feeds.pop(n).ended = True
        return len(stale)

    @classmethod
    def of(cls, rooms) -> "Hub":
        hub = getattr(rooms, "spectate_hub", None)
        if hub is None:
            hub = cls()
            rooms.spectate_hub = hub
        return hub


def late_ticket(rooms, room, member, log=None, rejoin: bool = False) -> bool:
    hub = Hub.of(rooms)
    with hub.lock:
        feed = hub.feeds.get(room.number)
        if feed is None or feed.ended or member.send is None:
            return False
        ticket = ("rejoin-" if rejoin else "") + secrets.token_hex(12)
        feed.tickets[ticket] = member.player_id
    try:
        member.send(ticket_packet(member.player_id, ticket, room.number))
    except OSError:
        return False
    if log is not None:
        log(f"spectate: player {member.player_id} joined room {room.number} during its match: 2420 (late"
            + (", rejoin)" if rejoin else ")"))
    return True


class Desk:

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms
        self.hub = Hub.of(rooms)
        self.watching: Feed | None = None
        self.watcher: int | None = None

    def _me(self) -> int | None:
        return self.session.player_id

    def _hosted(self):
        me = self._me()
        room = self.rooms.room_of(me) if me is not None else None
        if room is None or room.host != me or not room.started:
            return None
        return room

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        if p.type == HELLO:
            self._hello(p)
            return True
        if p.type == QUIT:
            self._quit()
            return True
        if p.type == WATCHED_END:
            self.session.log(f"spectate: player {self._me()} watched the match to its end screen (10200)")
            return True
        room = self._hosted()
        if room is None:
            if p.type == UP_START:
                self.session.log(f"spectate: 11080 from player {self._me()}, who hosts no started room: ignored")
            elif p.type == UP_INPUTS:
                self._spare(p.payload)
            return True
        with self.hub.lock:
            feed = self.hub.feeds.get(room.number)
            if p.type == UP_START:
                pruned = self.hub.prune()
                if pruned:
                    self.session.log(f"spectate: {pruned} feed(s) dropped after {STALE_FEED:.0f} s without uploads")
                feed = Feed(room.number, room.seed, p.payload)
                old = self.hub.feeds.get(room.number)
                if old is not None:
                    old.ended = True
                self.hub.feeds[room.number] = feed
                offered = 0
                for s in list(room.spectators):
                    if s.send is None:
                        continue
                    ticket = secrets.token_hex(12)
                    feed.tickets[ticket] = s.player_id
                    try:
                        s.send(ticket_packet(s.player_id, ticket, room.number))
                        offered += 1
                    except OSError:
                        pass
                self.session.log(f"spectate: room {room.number} match set up ({len(p.payload)} bytes), "
                                 f"2420 to {offered} spectator(s)")
            elif feed is None or feed.ended:
                return True
            elif p.type == UP_INPUTS:
                feed.touched = time.monotonic()
                if len(feed.batches) >= MAX_BATCHES or len(p.payload) > MAX_UPLOAD:
                    return True
                me = self._me()
                body = p.payload
                if feed.uploader is None:
                    feed.uploader = me
                elif feed.uploader != me:
                    body = self._handoff(feed, me, body)
                    if body is None:
                        return True
                if feed.size + len(body) > MAX_FEED_BYTES:
                    return True
                feed.size += len(body)
                feed.newest = max(feed.newest, newest_time(body))
                for q in feed.spares.values():
                    while q and q[0][0] <= feed.newest:
                        q.popleft()
                feed.batches.append(body)
                for send in list(feed.streams):
                    try:
                        send(Packet(INPUTS, body))
                    except OSError:
                        feed.streams.remove(send)
            elif p.type == UP_END:
                feed.ended = True
                self.hub.feeds.pop(room.number, None)
                abrupt, at = read_end(p.payload)
                feed.abrupt = abrupt
                if abrupt:
                    for send in list(feed.streams):
                        try:
                            send(abrupt_packet(at))
                        except OSError:
                            feed.streams.remove(send)
                self.session.log(f"spectate: room {room.number} feed ended after {len(feed.batches)} batches"
                                 + (f", early: spectators stop at {at} ms (10102)" if abrupt else ""))
                self.schedule(lambda: self.send_back(room, feed), END_GRACE)
        return True

    def _spare(self, payload: bytes) -> None:
        me = self._me()
        room = self.rooms.room_of(me) if me is not None else None
        if room is None or not room.started or me not in room.ids():
            return
        if len(payload) > MAX_UPLOAD:
            return
        t = newest_time(payload)
        with self.hub.lock:
            feed = self.hub.feeds.get(room.number)
            if feed is None or feed.ended or t <= feed.newest:
                return
            q = feed.spares.setdefault(me, deque(maxlen=MAX_SPARES))
            q.append((t, payload))

    def _handoff(self, feed: Feed, me: int, body: bytes) -> bytes | None:
        old = feed.uploader
        rows = []
        try:
            for _, b in list(feed.spares.pop(me, ())) + [(0, body)]:
                rows.extend(r for r in read_rows(b) if r[0] > feed.newest)
        except (IndexError, ValueError):
            return None
        feed.uploader = me
        rows.sort()
        self.session.log(f"spectate: room {feed.room}'s stream now from player {me} (was {old}): "
                         f"{len(rows)} rows after {feed.newest} ms")
        return rows_body(rows) if rows else None

    @staticmethod
    def schedule(fn, delay: float) -> None:
        t = threading.Timer(delay, fn)
        t.daemon = True
        t.start()

    def send_back(self, room, feed: Feed) -> None:
        sent = 0
        for s in list(room.spectators):
            if s.send is None:
                continue
            try:
                s.send(lobby.room_packet(room, spectator=True))
                sent += 1
            except OSError:
                pass
        with self.hub.lock:
            closers, feed.closers = list(feed.closers), []
            feed.streams.clear()
        for close in closers:
            try:
                close()
            except OSError:
                pass
        self.session.log(f"spectate: room {room.number}: the room again to {sent} spectator(s) (2445), "
                         f"{len(closers)} stream(s) closed")

    def _quit(self) -> None:
        feed, who = self.watching, self.watcher
        self.closed()
        if feed is None:
            return
        room = self.rooms.by_number.get(feed.room) if hasattr(self.rooms, "by_number") else None
        member = next((s for s in (room.spectators if room else []) if s.player_id == who), None)
        if member is not None and member.send is not None:
            try:
                member.send(lobby.room_packet(room, spectator=True))
            except OSError:
                pass
        self.session.log(f"spectate: player {who} left room {feed.room}'s match (10201)"
                         + (": the room again (2445)" if member is not None else ""))

    def _hello(self, p: Packet) -> None:
        try:
            h = read_hello(p.payload)
        except (IndexError, ValueError):
            return
        with self.hub.lock:
            feed = next((f for f in self.hub.feeds.values() if h["ticket"] in f.tickets and not f.ended), None)
            if feed is None or feed.tickets.get(h["ticket"]) != h["player"]:
                self.session.log(f"spectate: 10202 from player {h['player']} with an unknown ticket")
                return
            del feed.tickets[h["ticket"]]
            self.watching, self.watcher = feed, h["player"]
            feed.catch_up(self.session.send, begin_last=h["ticket"].startswith("rejoin-"))
            feed.streams.append(self.session.send)
            if CLOSE_STREAMS and callable(getattr(self.session, "close", None)):
                feed.closers.append(self.session.close)
        self.session.log(f"spectate: player {h['player']} watching room {feed.room} "
                         f"(caught up {len(feed.batches)} batches)")

    def closed(self) -> None:
        feed, self.watching = self.watching, None
        if feed is not None:
            with self.hub.lock:
                if self.session.send in feed.streams:
                    feed.streams.remove(self.session.send)
