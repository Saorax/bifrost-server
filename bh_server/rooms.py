from __future__ import annotations

from dataclasses import dataclass, field
import secrets
import threading

from .modshare import ModLibrary
from .relay import Tokens


RANDOM_HERO = 2
RANDOM_COSTUME = 225


@dataclass
class Loadout:
    hero: int = RANDOM_HERO
    random: bool = True
    costume: int = RANDOM_COSTUME
    p8: int = 0
    weapon_skins: int = 0


@dataclass
class Member:
    player_id: int
    name: str
    send: object = None
    team: int = 0
    ready: int = 0
    color: int = 0
    extra: tuple = (0, 0, 0)
    loadouts: list = field(default_factory=lambda: [Loadout()])
    mods: tuple | None = None
    cosmetics: list | None = None
    handicap: tuple | None = None
    scoreboard: bool = False
    clan: str = ""
    rtt: object = None


@dataclass
class Bot:
    level: int = 5
    team: int = 0
    color: int = 0
    ready: int = 0
    extra: tuple = (0, 0, 0)
    loadouts: list = field(default_factory=lambda: [Loadout()])
    handicap: tuple | None = None


@dataclass
class Room:
    number: int
    host: int
    members: list[Member] = field(default_factory=list)
    limit: int = 8
    settings: dict = field(default_factory=dict)
    searching: bool = False
    start_level: int = 0
    candidates: list = field(default_factory=list)
    random_tile: int = 0
    start_flags: int = 0
    pick: object = None
    started: bool = False
    rejoined: set = field(default_factory=set)
    spectators: list[Member] = field(default_factory=list)
    p2p: dict = field(default_factory=dict)
    p2p_choice: dict = field(default_factory=dict)
    choice_asked: set = field(default_factory=set)
    choice_pending: set = field(default_factory=set)
    choice_deadline: float = 0.0
    items_shared: set = field(default_factory=set)
    seed: int = 0
    level: int = 0
    lineup: list = field(default_factory=list)
    region: int = 0
    since: float = 0.0
    parties: list = field(default_factory=list)
    party_hosts: list = field(default_factory=list)
    bots: list[Bot] = field(default_factory=list)
    pending_level: int = 0
    countdown_over: bool = True
    fresh: bool = False
    series_rooms: list = field(default_factory=list)
    rematch_open: bool = False
    rematch_wants: dict = field(default_factory=dict)
    rematch_ms: int = 0
    rematch_group: object = None
    net: dict | None = None
    cup: dict | None = None
    tourney: dict | None = None
    fixed_teams: dict = field(default_factory=dict)

    def ids(self) -> list[int]:
        return [m.player_id for m in self.members]


NO_MODS_PRINT = 0x811C9DC5
NO_MODS = (NO_MODS_PRINT, [])


def mods_of(m: Member) -> tuple:
    return m.mods if m.mods is not None else NO_MODS


def _describe(base: Member, m: Member) -> str:
    mine = {e.split("#")[0]: e for e in mods_of(base)[1]}
    theirs = {e.split("#")[0]: e for e in mods_of(m)[1]}
    what = []
    if missing := sorted(set(mine) - set(theirs)):
        what.append("missing " + ", ".join(missing))
    if extra := sorted(set(theirs) - set(mine)):
        what.append("extra " + ", ".join(extra))
    if changed := sorted(k for k in set(mine) & set(theirs) if mine[k] != theirs[k]):
        what.append("different " + ", ".join(changed))
    return f"{m.name}: " + ("; ".join(what) or "a different list")


def _base(room: Room) -> Member | None:
    return next((m for m in room.members if m.player_id == room.host), room.members[0] if room.members else None)


def mods_difference(room: Room) -> str:
    if len({mods_of(m)[0] for m in room.members}) <= 1:
        return ""
    base = _base(room)
    return " / ".join(_describe(base, m) for m in room.members if m is not base and mods_of(m)[0] != mods_of(base)[0])


def spectator_difference(room: Room, spectator: Member) -> str:
    base = _base(room)
    if base is None or mods_of(spectator)[0] == mods_of(base)[0]:
        return ""
    return _describe(base, spectator)


def spectate_refusal(room: Room, spectator: Member) -> str | None:
    differ = spectator_difference(room, spectator)
    if not differ:
        return None
    return ("Content mods differ: " + differ + ". Spectators need the same gameplay mods switched on as the players "
            "(Mods window) to watch this room's matches.")


class Rooms:
    def __init__(self, rng=None) -> None:
        self.by_number: dict[int, Room] = {}
        self.of_player: dict[int, int] = {}
        self.lock = threading.Lock()
        self.rng = rng or secrets.SystemRandom()
        self.tokens = Tokens()
        self.cosmetics: dict[int, list[int]] = {}
        self.mod_items: dict[int, str] = {}
        self.library = ModLibrary(None)
        self.turn = None

    def _new_number(self) -> int:
        number = self.rng.randrange(100000, 1000000)
        while number in self.by_number:
            number = self.rng.randrange(100000, 1000000)
        return number

    def create(self, host: Member, limit: int = 8, settings: dict | None = None) -> Room:
        with self.lock:
            self._leave(host.player_id)
            number = self._new_number()
            room = Room(number, host.player_id, [host], max(1, limit), dict(settings or {}))
            self.by_number[number] = room
            self.of_player[host.player_id] = number
            return room

    def create_reserved(self, limit: int, settings: dict, cup: dict | None = None, tourney: dict | None = None) -> Room:
        with self.lock:
            number = self._new_number()
            room = Room(number, 0, [], max(1, limit), dict(settings))
            room.cup, room.tourney = cup, tourney
            self.by_number[number] = room
            return room

    def free(self, room: Room) -> None:
        with self.lock:
            room.cup = room.tourney = None
            if not room.members and self.by_number.get(room.number) is room:
                del self.by_number[room.number]
                self.tokens.forget_room(room)

    def join(self, number: int, who: Member) -> tuple[Room | None, str | None]:
        with self.lock:
            room = self.by_number.get(number)
            if room is None:
                return None, "no such room"
            if who.player_id in room.ids():
                return room, None
            if room.searching or room.started:
                return None, "the room has already started" if room.started else "the room is already searching"
            if len(room.members) + len(room.bots) >= room.limit:
                return None, "the room is full"
            self._leave(who.player_id)
            room.members.append(who)
            self.of_player[who.player_id] = number
            if room.host == 0 or room.host not in room.ids():
                room.host = who.player_id
            return room, None

    def spectate(self, number: int, who: Member, any_room: bool = False) -> tuple[Room | None, str | None]:
        with self.lock:
            room = self.by_number.get(number)
            if room is None:
                return None, "no such room"
            if room.settings.get("playlist", 0) != 0 and not any_room:
                return None, "only custom rooms can be spectated"
            if who.player_id in room.ids():
                return None, "you are already playing in this room"
            if any(s.player_id == who.player_id for s in room.spectators):
                return room, None
            if room.searching:
                return None, "the room is already searching"
            self._leave(who.player_id)
            room.spectators.append(who)
            self.of_player[who.player_id] = number
            return room, None

    def is_spectator(self, player_id: int) -> bool:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            return room is not None and any(s.player_id == player_id for s in room.spectators)

    def leave(self, player_id: int) -> Room | None:
        with self.lock:
            return self._leave(player_id)

    def _leave(self, player_id: int) -> Room | None:
        number = self.of_player.pop(player_id, None)
        room = self.by_number.get(number) if number is not None else None
        if room is None:
            return None
        if any(s.player_id == player_id for s in room.spectators):
            room.spectators = [s for s in room.spectators if s.player_id != player_id]
            return room
        room.searching = False
        room.members = [m for m in room.members if m.player_id != player_id]
        room.p2p.pop(player_id, None)
        self.tokens.forget_player(room, player_id)
        if room.parties:
            room.parties = [p for p in ([i for i in ids if i != player_id] for ids in room.parties) if p]
        if not room.members and (room.cup is not None or room.tourney is not None):
            room.host, room.started = 0, False
            room.p2p.clear()
        elif not room.members:
            del self.by_number[number]
            self.tokens.forget_room(room)
            for s in room.spectators:
                self.of_player.pop(s.player_id, None)
        elif room.host == player_id:
            room.host = room.members[0].player_id
        return room

    def room_of(self, player_id: int) -> Room | None:
        with self.lock:
            number = self.of_player.get(player_id)
            return self.by_number.get(number) if number is not None else None

    def cancel_search(self, player_id: int) -> Room | None:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is not None:
                room.searching = False
                for member in room.members:
                    member.ready = 0
            return room

    def set_start_level(self, player_id: int, level: int, candidates: list[int] | None = None,
                        random_tile: int = 0, flags: int = 0) -> tuple[Room | None, str | None]:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is None:
                return None, "no room"
            if room.host != player_id:
                return room, "not the host"
            if room.settings.get("playlist", 0) != 0:
                return room, "not a custom room"
            if room.started:
                return room, "already started"
            if level == 0:
                return room, "level 0 is not selectable"
            room.start_level = level
            room.candidates = list(candidates or [])
            room.random_tile = random_tile
            room.start_flags = flags
            return room, None

    def start(self, player_id: int) -> tuple[Room | None, int | None, str | None]:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is None:
                return None, None, "no room"
            if room.host != player_id:
                return room, None, "not the host"
            if room.settings.get("playlist", 0) != 0:
                return room, None, "not a custom room"
            if room.started:
                return room, None, "already started"
            if room.start_level == 0:
                return room, None, "no host-selected level"
            if not all(m.ready for m in room.members):
                return room, None, "not everyone is locked in"
            differ = mods_difference(room)
            if differ:
                return room, None, "content mods differ: " + differ
            seed = self.rng.randrange(1, 0x80000000)
            room.started = True
            room.seed, room.lineup = seed, list(room.members)
            return room, seed, None

    def merge(self, rooms: list[Room], teams: list[int], settings: dict) -> Room | None:
        with self.lock:
            if any(self.by_number.get(r.number) is not r or not r.searching or r.started for r in rooms):
                return None
            number = self._new_number()
            members = []
            for r, team in zip(rooms, teams):
                for m in r.members:
                    m.team = team
                    members.append(m)
            merged = Room(number, rooms[0].host, members, len(members), dict(settings), region=rooms[0].region,
                          parties=[r.ids() for r in rooms], party_hosts=[r.host for r in rooms])
            for r in rooms:
                r.searching = False
                del self.by_number[r.number]
                self.tokens.forget_room(r)
            self.by_number[number] = merged
            for m in members:
                self.of_player[m.player_id] = number
            return merged

    def start_matched(self, player_id: int, level: int) -> tuple[Room | None, int | None, str | None]:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is None or not room.parties:
                return room, None, "not a matched room"
            if room.started:
                return room, None, "already started"
            if level == 0:
                return room, None, "level 0 is not selectable"
            room.start_level = level
            seed = self.rng.randrange(1, 0x80000000)
            room.started = True
            room.seed, room.lineup = seed, list(room.members)
            for m in room.members:
                m.ready = 0
            return room, seed, None

    def split(self, player_id: int, settings: dict) -> Room | None:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is None or not room.parties:
                return None
            party = next((ids for ids in room.parties if player_id in ids), [player_id])
            moving = [m for m in room.members if m.player_id in party]
            room.members = [m for m in room.members if m.player_id not in party]
            room.parties = [ids for ids in room.parties if ids is not party]
            for m in moving:
                room.p2p.pop(m.player_id, None)
            if not room.members:
                del self.by_number[room.number]
                self.tokens.forget_room(room)
            elif room.host not in room.ids():
                room.host = room.members[0].player_id
            number = self._new_number()
            for m in moving:
                m.ready, m.team = 0, 0
            own = Room(number, moving[0].player_id, moving, len(moving), dict(settings), region=room.region)
            self.by_number[number] = own
            for m in moving:
                self.of_player[m.player_id] = number
            return own

    def match_over(self, room: Room | None, seed: int | None = None) -> bool:
        with self.lock:
            if (room is None or self.by_number.get(room.number) is not room or room.parties or not room.started
                    or (seed is not None and room.seed != seed)):
                return False
            room.started = False
            room.start_level, room.p2p = 0, {}
            room.rejoined = set()
            self.tokens.forget_room(room)
            return True

    def rematch(self, room: Room) -> bool:
        with self.lock:
            if (self.by_number.get(room.number) is not room or not room.parties or not room.started
                    or len(room.parties) < 2 or not all(m.ready for m in room.members)):
                return False
            room.started = False
            room.start_level, room.p2p = 0, {}
            room.rejoined = set()
            self.tokens.forget_room(room)
            for m in room.members:
                for l in m.loadouts:
                    if l.random and l.hero != RANDOM_HERO:
                        l.random = False
            return True

    def searching_rooms(self) -> list[Room]:
        with self.lock:
            return [r for r in self.by_number.values() if r.searching and not r.started and not r.parties]

    def p2p_hello(self, player_id: int, port: int, addresses: list[str]) -> Room | None:
        with self.lock:
            number = self.of_player.get(player_id)
            room = self.by_number.get(number) if number is not None else None
            if room is None or not (room.started or room.parties) or player_id not in room.ids():
                return None
            entry = {"port": port, "addresses": addresses}
            if room.p2p.get(player_id, {}).get("turn"):
                entry["turn"] = room.p2p[player_id]["turn"]
            room.p2p[player_id] = entry
            return room

    def p2p_turn(self, room: Room, player_id: int, address: tuple[str, int] | None) -> dict[int, tuple[str, int]] | None:
        with self.lock:
            entry = room.p2p.get(player_id)
            if self.by_number.get(room.number) is not room or entry is None:
                return None
            if address is None:
                entry.pop("turn", None)
            else:
                entry["turn"] = address
            return {pid: e["turn"] for pid, e in room.p2p.items() if e.get("turn")}

    def broadcast(self, room: Room, packet, but: int | None = None, spectators: bool = True, skip=()) -> int:
        sent = 0
        for m in list(room.members) + (list(room.spectators) if spectators else []):
            if m.player_id == but or m.player_id in skip or m.send is None:
                continue
            try:
                m.send(packet)
                sent += 1
            except OSError:
                pass
        return sent
