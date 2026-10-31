from __future__ import annotations

from dataclasses import dataclass
import threading
import time

from . import lobby, spectate
from .bits import BitWriter
from .frame import Packet
from .rooms import RANDOM_HERO

PROMPT, REJOIN, DECLINE, OVERLAY = 2477, 53, 54, 70
NOTICE = 11013
REBUILD_INPUTS = 11083
HANDLED = {REJOIN, DECLINE}
SEAT_TTL = 20 * 60
TICKET_PREFIX = "rejoin-"
PROMPT_AFTER = 2.0
PROMPT_FALLBACK = 15.0


def schedule(fn, delay: float) -> None:
    t = threading.Timer(delay, fn)
    t.daemon = True
    t.start()


@dataclass
class Seat:
    player_id: int
    room: int
    seed: int
    slot: int
    name: str
    left_at: float
    rejoined: bool = False
    member: object = None


class Seats:

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.by_player: dict[int, Seat] = {}

    @classmethod
    def of(cls, rooms) -> "Seats":
        seats = getattr(rooms, "rejoin_seats", None)
        if seats is None:
            seats = cls()
            rooms.rejoin_seats = seats
        return seats


def _feed_live(rooms, room) -> bool:
    hub = spectate.Hub.of(rooms)
    with hub.lock:
        feed = hub.feeds.get(room.number)
        return feed is not None and not feed.ended


def holding(rooms, player_id: int, clock=time.time) -> tuple[Seat, object] | None:
    seats = Seats.of(rooms)
    with seats.lock:
        seat = seats.by_player.get(player_id)
    if seat is None:
        return None
    room = rooms.by_number.get(seat.room)
    ok = (room is not None and room.started and room.seed == seat.seed and room.members
          and clock() - seat.left_at <= SEAT_TTL and _feed_live(rooms, room))
    if not ok:
        with seats.lock:
            if seats.by_player.get(player_id) is seat:
                del seats.by_player[player_id]
        return None
    return seat, room


def connection_lost(session, clock=time.time) -> Seat | None:
    me = session.lobby.me
    rooms = session.lobby.rooms
    if me is None:
        return None
    room = rooms.room_of(me)
    if room is None or not room.started:
        return None
    seats = Seats.of(rooms)
    with seats.lock:
        old = seats.by_player.get(me)
    playing = me in room.ids()
    rejoined = old is not None and old.room == room.number and old.seed == room.seed and rooms.is_spectator(me)
    if not (playing or rejoined):
        return None
    lineup = [m.player_id for m in room.lineup] or room.ids()
    if me not in lineup:
        return None
    member = next((m for m in room.members if m.player_id == me), None)
    if member is not None and member.scoreboard:
        return None
    if not [m for m in room.members if m.player_id != me]:
        return None
    if not _feed_live(rooms, room):
        return None
    name = member.name if member is not None else (old.name if old is not None else "")
    seat = Seat(me, room.number, room.seed, lineup.index(me), name, clock(),
                member=member if member is not None else (old.member if old is not None else None))
    with seats.lock:
        seats.by_player[me] = seat
    session.log(f"rejoin: player {me} lost its connection during room {room.number}'s match (seat {seat.slot}); "
                f"kept for {SEAT_TTL // 60} min")
    return seat


def after_login(session) -> bool:
    me = session.lobby.me
    if me is None or holding(session.lobby.rooms, me) is None:
        return False
    session.rejoin_prompt = "pending"
    session.log(f"rejoin: player {me} has a seat in a running match; 2477 once the menu is up")
    schedule(lambda: prompt(session), PROMPT_FALLBACK)
    return True


def prompt(session) -> bool:
    if getattr(session, "rejoin_prompt", None) != "pending":
        return False
    session.rejoin_prompt = "sent"
    me = session.lobby.me
    found = holding(session.lobby.rooms, me)
    if found is None:
        session.log(f"rejoin: player {me}'s match ended before the prompt; none sent")
        return False
    seat, room = found
    seat.rejoined = False
    try:
        session.send(Packet(PROMPT, b""))
    except OSError:
        return False
    session.log(f"rejoin: player {me} may rejoin room {room.number}'s match (sent 2477)")
    return True


def handle(session, p: Packet) -> bool:
    if p.type == OVERLAY:
        if getattr(session, "rejoin_prompt", None) == "pending":
            schedule(lambda: prompt(session), PROMPT_AFTER)
        return False
    if p.type not in HANDLED:
        return False
    me = session.lobby.me
    rooms = session.lobby.rooms
    seats = Seats.of(rooms)
    if me is None:
        return True
    if p.type == DECLINE:
        with seats.lock:
            seat = seats.by_player.pop(me, None)
        session.log(f"rejoin: player {me} declined (54)" + (f"; room {seat.room}'s seat forgotten" if seat else ""))
        return True
    found = holding(rooms, me)
    if found is None:
        session.send(Packet(NOTICE, BitWriter().string("The match you left has ended.").bytes()))
        session.log(f"rejoin: player {me} asked to rejoin (53), but its match is over (sent 11013)")
        return True
    seat, room = found
    if seat.rejoined:
        session.log(f"rejoin: player {me}'s second 53 ignored")
        return True
    seat.rejoined = True
    if rebuild_on(session):
        return rebuild(session, seat, room)
    room, why = rooms.spectate(room.number, session.lobby.member(), any_room=True)
    if room is None:
        session.send(Packet(NOTICE, BitWriter().string(f"Couldn't rejoin: {why}.").bytes()))
        session.log(f"rejoin: player {me} couldn't rejoin room {seat.room}: {why}")
        return True
    session.send(lobby.room_packet(room, spectator=True))
    member = next((s for s in room.spectators if s.player_id == me), None)
    n = rooms.broadcast(room, lobby.spectator_packet(member), but=me) if member is not None else 0
    ticketed = member is not None and spectate.late_ticket(rooms, room, member, session.log, rejoin=True)
    session.log(f"rejoin: player {me} back in room {room.number} (seat {seat.slot}) as a spectator to catch up "
                f"(sent 2445; 2411 to {n}" + ("; 2420 rejoin ticket)" if ticketed else "; no stream)"))
    return True


def rebuild_on(session) -> bool:
    return str((getattr(session, "cfg", None) or {}).get("rejoin", "rebuild")).lower() == "rebuild"


def own_pick_revealed(room, member) -> Packet:
    flagged = [l for l in member.loadouts if l.random and l.hero != RANDOM_HERO]
    for l in flagged:
        l.random = False
    try:
        return lobby.room_packet(room)
    finally:
        for l in flagged:
            l.random = True


def rebuild(session, seat: Seat, room) -> bool:
    me = session.lobby.me
    rooms = session.lobby.rooms
    hub = spectate.Hub.of(rooms)
    member = seat.member if seat.member is not None else session.lobby.member()
    member.send = session.send
    member.scoreboard = False
    with rooms.lock:
        if rooms.of_player.get(me) != room.number:
            rooms._leave(me)
        room.spectators = [s for s in room.spectators if s.player_id != me]
        if me not in room.ids():
            room.members.insert(min(seat.slot, len(room.members)), member)
        rooms.of_player[me] = room.number
        room.rejoined.add(me)
    for m in room.members:
        theirs = rooms.mod_items.get(m.player_id)
        if m.player_id != me and theirs is not None:
            session.send(lobby._packet(lobby.MOD_ITEMS, BitWriter().uint(m.player_id).string(theirs)))
    session.send(own_pick_revealed(room, member))
    flags = lobby.START_FLAGS_MARK | (room.start_flags if not room.parties else 0) | lobby.START_REJOIN
    if room.tourney is not None:
        flags |= lobby.START_TOURNEY
    session.send(Packet(lobby.START_LOCAL, BitWriter().uint(room.number).uint(room.seed).uint(room.level)
                        .uint(flags).bytes()))

    def forward(p: Packet) -> None:
        if p.type == spectate.INPUTS:
            session.send(Packet(REBUILD_INPUTS, p.payload))

    with hub.lock:
        feed = hub.feeds.get(room.number)
        batches = list(feed.batches) if feed is not None and not feed.ended else []
        for b in batches:
            session.send(Packet(REBUILD_INPUTS, b))
        if feed is not None and not feed.ended:
            feed.streams.append(forward)
    session.log(f"rejoin: player {me} back in room {room.number} in slot {seat.slot} to rebuild its match "
                f"(2445, 11005 level {room.level} seed {room.seed} as a rejoin, {len(batches)} batches as 11083)")
    return True
