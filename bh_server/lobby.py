from __future__ import annotations

import json
import os
import threading
import time

from . import mappick, matchmaking, modshare, queueprefs
from .bits import BitReader, BitWriter
from .frame import Packet
from .playlists import BY_ID
from .rooms import RANDOM_HERO, Bot, Loadout, Member, Room, Rooms

READY_QUEUE, CANCEL_QUEUE, CREATE, SETTINGS, JOIN, LOADOUT, UNREADY, START, LEAVE_LOBBY, LEAVE_SLOT, EMOTE = 31, 32, 33, 37, 38, 41, 43, 55, 79, 81, 159
TEAM, ADD_BOT, REMOVE_BOT, BOT_LEVEL = 42, 44, 45, 46
BOT_LEVEL_CHANGED = 2461
HANDICAP, HANDICAP_CHANGED = 163, 2544
DEMOTE, TO_PLAYER = 93, 80
NOW_SPECTATOR, SPECTATOR_JOINED = 2409, 2411
LIST_PUBLIC, PUBLIC_ROOMS = 94, 2480
PUBLIC_FLAG = 1
MAX_LISTED = 100
ROOM_DEFAULTS = 11003
START_DETAILS, START_LOCAL = 11004, 11005
START_FLAGS_MARK, START_TRAINING, START_REJOIN, START_TOURNEY = 0x100, 1, 2, 4


def start_flags(r: BitReader) -> int:
    if r.left() < 8:
        return 0
    v = r.uint()
    return v & 0xFF if v & START_FLAGS_MARK else 0


SPECTATE = 11006
P2P_HELLO, P2P_PEERS = 11011, 11012
P2P_CHOICE, P2P_ASK_MARK = 11025, 0x100
MODS_LIST, START_REFUSED = 11013, 11014
MODS_UPLOAD, MODS_OFFER = 11015, 11016
MATCH_FOUND = 11020
ROOM_NET = 11017
COSMETICS = 11018
MAX_COSMETICS = 32
MOD_ITEMS = 11049
MAX_MOD_ITEMS = 64 * 1024
ROOM_NET_DEFAULT = {"rollback": True, "delay": 0, "smoothing": True, "smoothing_ms": 100}
MATCH_TICK = 2.0
QUEUE_MIN_WAIT = 3.0
MATCH_COUNTDOWN = 3.0
MATCH_FOUND_GAME = 2436
SERIES_WINS = 2
SERIES_BEST_OF = 3
RANKED_SERIES_WINS, RANKED_SERIES_BEST_OF = 3, 5


def _later(delay: float, fn, *args) -> None:
    if delay <= 0:
        fn(*args)
        return
    t = threading.Timer(delay, fn, args=args)
    t.daemon = True
    t.start()


def series_rules(playlist) -> tuple[int, int]:
    if playlist is not None and playlist.ranked:
        return RANKED_SERIES_WINS, RANKED_SERIES_BEST_OF
    return SERIES_WINS, SERIES_BEST_OF


REMATCH_WANT, REMATCH_STATE, REMATCH_WINDOW = 162, 2542, 2543
LEAVE_MATCH, LEAVE_MATCH_DONE, REMATCH_CANCELLED = 85, 2402, 2547
REMATCH_WINDOW_MS = 20000
PARTICIPANTS, PARTICIPANT_LEFT = 2401, 2429
CHAT_LEFT = 2474
PC_PLATFORM = 1
SCOREBOARD_DONE, BACK_FROM_SCOREBOARD = 2548, 11073
TRAINING_BOT_HERO = 3
REMATCH_AGREED_WAIT = 3.0
REMATCH_LOCKED_HOLD = 1.5
REMATCH_PICK_MS = 20000
LEVEL_DONE, LEVEL_VOTE, LEVEL_STRIKE_DONE = 56, 57, 58
LEVEL_SELECT_OPEN, LEVEL_VOTED, LEVEL_WINNER, LEVEL_STRUCK = 2460, 2459, 2424, 2425
LEVEL_SELECT_BACK = 11091
MAX_ADDRESSES = 8
MAX_MOD_ENTRIES = 60
ROOM, ROOM_FAILED, JOINED, SETTINGS_CHANGED, PLAYER_CHANGED = 2445, 2446, 2449, 2448, 2437
PICKS_RESET = 2458
OUT_OF_SYNC = 2456
NO_GAME_SERVER = 2427
ROOM_RATINGS = 2451
NAME_CHANGED = 2450
FULL_SPECTATE = 2447
LEFT_SELF, QUEUE_CANCELLED, LEFT_OTHER, NEW_HOST, QUEUE_WAITING, EMOTE_SHOWN = 2412, 2428, 2444, 2455, 2472, 2538
GAME_SETTINGS = 15
TEAMS_FLAG = 1
CUSTOM_FLAGS = (14, 15)
HANDLED = {BACK_FROM_SCOREBOARD, LEVEL_SELECT_BACK, HANDICAP, DEMOTE, TO_PLAYER, COSMETICS, MOD_ITEMS, ROOM_NET,
           LEAVE_MATCH, REMATCH_WANT, LIST_PUBLIC, TEAM, ADD_BOT, REMOVE_BOT, BOT_LEVEL, LEVEL_DONE, LEVEL_VOTE,
           LEVEL_STRIKE_DONE, ROOM_DEFAULTS, START_DETAILS, SPECTATE, P2P_HELLO, P2P_CHOICE, MODS_LIST, READY_QUEUE,
           CANCEL_QUEUE, CREATE, SETTINGS, JOIN, LOADOUT, UNREADY, START, LEAVE_LOBBY, LEAVE_SLOT, EMOTE}


def is_ipv4(text: str) -> bool:
    parts = text.split(".")
    return len(parts) == 4 and all(x.isdigit() and 0 <= int(x) <= 255 for x in parts)


def _packet(t: int, w: BitWriter) -> Packet:
    return Packet(t, w.bytes())


def default_settings(game: list[int], flags: int, custom_type: int) -> dict:
    return {"playlist": 0, "custom_type": custom_type, "n8309": 4, "game": list(game), "flags": flags,
            "b4224": 0, "description": "", "select_mode": 0, "b6100": False}


def queue_settings(playlist: int, flags: int) -> dict:
    return {"playlist": playlist, "custom_type": 0, "n8309": 0, "game": [], "flags": flags,
            "b4224": 0, "description": "", "select_mode": 0, "b6100": False}


def read_settings(r: BitReader, with_description: bool) -> dict:
    s = {"playlist": r.uint(), "custom_type": r.uint(), "n8309": 0, "game": []}
    if s["playlist"] == 0:
        s["n8309"] = r.small()
        s["game"] = [r.uint() for _ in range(GAME_SETTINGS)]
    s["flags"] = r.uint()
    s["b4224"] = r.byte()
    s["description"] = r.string() if with_description else ""
    s["select_mode"] = r.small()
    s["b6100"] = r.bool()
    s["handicaps"] = r.bool()
    return s


def write_settings(w: BitWriter, s: dict) -> None:
    w.uint(s["playlist"]).uint(s["custom_type"])
    if s["playlist"] == 0:
        w.small(s["n8309"])
        for v in s["game"]:
            w.uint(v)
    w.uint(s["flags"]).byte(s["b4224"]).small(s["select_mode"]).bool(s["b6100"])
    w.bool(handicaps_on(s))


def handicaps_on(s: dict) -> bool:
    return bool(s.get("handicaps")) and s.get("playlist", 0) == 0


def write_handicap(w: BitWriter, h: tuple | None) -> None:
    if h is None:
        w.bool(False)
    else:
        w.bool(True).small(h[0]).uint(h[1]).uint(h[2])


def write_entry(w: BitWriter, m: Member | Bot, bot_slot: int | None = None) -> None:
    if bot_slot is not None:
        w.bool(True).uint(bot_slot)
    else:
        w.bool(False)
        w.uint(m.player_id).string(m.name).string(getattr(m, "clan", ""))
        w.uint(0).uint(0).uint(0)
        w.string(m.name)
        w.uint(0).uint(0)
    w.uint(m.team).uint(m.ready).uint(m.color)
    for v in m.extra:
        w.uint(v)
    c = getattr(m, "cosmetics", None) or []
    pick = lambda i, limit: min(c[i], limit) if len(c) > i else 0
    w.small(pick(0, 0xFFFF)).uint(pick(15, 0xFFFFFFFF)).small(pick(6, 0xFFFF))
    w.bool(getattr(m, "scoreboard", False))
    w.uint(len(m.loadouts))
    for l in m.loadouts:
        hero = 0 if bot_slot is not None and l.hero == RANDOM_HERO else l.hero
        w.bool(l.random).bool(hero <= 0xFFFF).uint(hero & 0xFFFF).uint(l.costume)
        w.uint(l.p8).uint(l.weapon_skins).uint(0).uint(0)


def entry_changed(m: Member | Bot, bot_slot: int | None = None) -> Packet:
    w = BitWriter()
    write_entry(w, m, bot_slot)
    w.bool(False)
    return _packet(PLAYER_CHANGED, w)


def room_packet(room: Room, spectator: bool = False) -> Packet:
    p = _room_packet(room, spectator, False)
    p.v1100 = _room_packet(room, spectator, True)
    return p


def _room_packet(room: Room, spectator: bool, v1100: bool) -> Packet:
    w = BitWriter()
    w.uint(room.number).uint(0)
    write_settings(w, room.settings)
    playlist = room.settings.get("playlist", 0) != 0
    w.bool(False).uint(room.host)
    w.bool(spectator).bool(False).bool(False)
    w.uint(room.rematch_ms).string(room.settings.get("description", "")).bool(playlist)
    if v1100:
        for v in (room.cup or {}).get("fields") or [0] * 9:
            w.uint(v)
        w.bool(False)
    handicaps = handicaps_on(room.settings)
    for m in room.members:
        w.bool(True)
        write_entry(w, m)
        if handicaps:
            write_handicap(w, m.handicap)
    w.bool(False)
    for i, b in enumerate(room.bots):
        w.bool(True).uint(b.level).bool(True)
        write_entry(w, b, len(room.members) + i)
        if handicaps:
            write_handicap(w, b.handicap)
    w.bool(False).bool(False)
    for s in room.spectators:
        w.bool(True).uint(s.player_id).uint(0).string(s.name).string(s.name)
    w.bool(False)
    return _packet(ROOM, w)


def spectator_packet(m: Member, demoted: bool = False) -> Packet:
    return _packet(SPECTATOR_JOINED, BitWriter().uint(m.player_id).uint(0).string(m.name).string(m.name).bool(demoted))


def renamed(rooms: Rooms, player_id: int, name: str) -> int:
    room = rooms.room_of(player_id)
    if room is None:
        return 0
    clan = ""
    with rooms.lock:
        for m in list(room.members) + list(room.spectators):
            if m.player_id == player_id:
                m.name = name
                clan = getattr(m, "clan", "")
    return rooms.broadcast(room, _packet(NAME_CHANGED, BitWriter().uint(player_id).string(name).string(clan)))


def public_rooms_packet(rooms: Rooms) -> tuple[Packet, int]:
    with rooms.lock:
        listed = [r for r in rooms.by_number.values()
                  if r.settings.get("playlist", 0) == 0 and r.settings.get("flags", 0) & PUBLIC_FLAG and r.members
                  and len(r.settings.get("game", [])) == GAME_SETTINGS][:MAX_LISTED]
        rows = []
        for r in listed:
            host = next((m for m in r.members if m.player_id == r.host), r.members[0])
            rows.append((r.number, len(r.members) + len(r.bots), r.limit, r.settings.get("description", ""), host,
                         r.settings, [m.player_id for m in r.members], [m.player_id for m in r.spectators], r.started))
    w = BitWriter()
    now = int(time.time())
    for number, count, limit, description, host, settings, players, spectators, started in rows:
        w.bool(True).uint(number).uint(number).uint(count).uint(limit)
        w.string(description).string("").uint(0).string(host.name)
        w.uint(settings.get("b4224", 0)).uint(host.player_id).uint(now)
        w.bool(bool(settings.get("b6100", False))).small(settings.get("select_mode", 0))
        for v in settings["game"]:
            w.uint(v)
        for ids in (players, spectators):
            for i in ids:
                w.bool(True).uint(i)
            w.bool(False)
        w.bool(started)
    w.bool(False)
    return _packet(PUBLIC_ROOMS, w), len(rows)


def failed(message: str) -> Packet:
    return _packet(ROOM_FAILED, BitWriter().uint(0).bool(True).string(message))


def read_loadout(payload: bytes) -> dict:
    r = BitReader(payload)
    out = {"bot": r.bool(), "slot": r.uint(), "color": r.uint(), "flag": r.bool(), "extra": (r.uint(), r.uint(), r.uint())}
    picks = []
    for _ in range(min(r.uint(), 5)):
        random, small = r.bool(), r.bool()
        hero = r.uint()
        picks.append(Loadout(hero=hero if small else hero | 0x10000, random=random, costume=r.uint(), p8=r.uint(),
                             weapon_skins=r.uint()))
    out["loadouts"] = picks
    return out


def teams_on(room: Room) -> bool:
    return bool(room.settings.get("game") and room.settings["game"][0] & TEAMS_FLAG)


def assign_teams(room: Room) -> None:
    slots = list(room.members) + list(room.bots)
    if not teams_on(room):
        for m in slots:
            m.team = 0
        return
    for m in slots:
        if getattr(m, "player_id", None) in room.fixed_teams:
            m.team = room.fixed_teams[m.player_id]
    if all(m.team == 0 for m in slots):
        half = (len(slots) + 1) // 2
        for i, m in enumerate(slots):
            m.team = 1 if i < half else 2
        return
    for m in slots:
        if m.team not in (1, 2):
            red = sum(1 for x in slots if x.team == 1)
            blue = sum(1 for x in slots if x.team == 2)
            m.team = 1 if red <= blue else 2


class Lobby:

    def __init__(self, session, rooms: Rooms) -> None:
        self.session, self.rooms = session, rooms
        self.defaults: list[int] | None = None

    def regional(self, settings: dict) -> dict:
        settings["b4224"] = 0
        return settings

    @property
    def me(self) -> int | None:
        p = self.session.player
        return p["id"] if p else self.session.player_id

    def member(self) -> Member:
        s = self.session
        return Member(self.me, s.player["name"] if s.player else s.player_name, s.send,
                      cosmetics=self.rooms.cosmetics.get(self.me), clan=self.clan_name(), rtt=getattr(s, "rtt", None))

    def clan_name(self) -> str:
        social = getattr(self.session, "social", None)
        try:
            from .clans import Clans
            hub = Clans.of(social, self.session.store) if social is not None else None
            key = social.key(self.session) if hub is not None and hub.db is not None else None
            row = hub.db.membership(key) if key else None
            clan = hub.db.clan(row[0]) if row else None
            return clan["name"] if clan else ""
        except Exception:
            return ""

    def offer_mods(self, room) -> None:
        library, log = self.rooms.library, self.session.log
        web = int(self.session.cfg.get("web_port", 0) or 0)
        if library is None or not web or room is None:
            return
        acc = self.session.account
        owner = acc["id"] if acc else None
        upload, per_member = modshare.offers(room, library, owner)
        upload = [e for e in upload if not library.recently(("up", owner, e), modshare.UPLOAD_AGAIN)]
        if upload:
            ticket = library.ticket("up", upload, owner=owner, owner_name=acc.get("name", ""))
            w = BitWriter().uint(web).string(ticket).uint(len(upload))
            for e in upload:
                w.string(e)
            self.session.send(_packet(MODS_UPLOAD, w))
            log(f"room {room.number}: host asked to upload {len(upload)} mods ({', '.join(upload)})")
        host = next((m for m in room.members if m.player_id == room.host), None)
        for m in room.members:
            wanted = per_member.get(m.player_id)
            if wanted is None or m.send is None or library.recently(("down", m.player_id, tuple(wanted)), modshare.OFFER_AGAIN):
                continue
            w = BitWriter().uint(web).string(library.ticket("down", wanted, owner=owner)).string(host.name if host else "").uint(len(wanted))
            for e in wanted:
                w.string(e)
            try:
                m.send(_packet(MODS_OFFER, w))
                log(f"room {room.number}: player {m.player_id} offered the host's {len(wanted)} mods")
            except OSError:
                pass

    def handle(self, p: Packet) -> bool:
        if p.type not in HANDLED:
            return False
        log = self.session.log
        if self.me is None:
            log(f"lobby packet {p.type} before login: ignored")
            return True
        r = BitReader(p.payload)
        if p.type in (READY_QUEUE, CANCEL_QUEUE, LOADOUT, UNREADY) and self.back_from_match(p.type):
            return True
        if p.type == LIST_PUBLIC:
            packet, n = public_rooms_packet(self.rooms)
            self.session.send(packet)
            log(f"public games list for player {self.me}: {n} rooms (2480)")
        elif p.type == ROOM_DEFAULTS:
            self.defaults = [r.uint() for _ in range(GAME_SETTINGS)]
            log(f"custom room defaults: {self.defaults}")
        elif p.type == REMATCH_WANT:
            self.rematch_want(r.bool())
        elif p.type == COSMETICS:
            n = min(r.uint(), MAX_COSMETICS)
            values = [r.uint() for _ in range(n)]
            changed = self.rooms.cosmetics.get(self.me) != values
            self.rooms.cosmetics[self.me] = values
            room = self.rooms.room_of(self.me)
            me = next((m for m in room.members if m.player_id == self.me), None) if room is not None else None
            if me is not None and changed:
                me.cosmetics = values
                self.rooms.broadcast(room, entry_changed(me))
            social = getattr(self.session, "social", None)
            if changed and social is not None:
                social.renamed(self.session)
                hub = getattr(social, "clans", None)
                if hub is not None:
                    hub.avatar_changed(social.key(self.session))
        elif p.type == MOD_ITEMS:
            self.on_mod_items(r.string())
        elif p.type == ROOM_NET:
            room = self.rooms.room_of(self.me)
            if room is None or room.host != self.me or room.started:
                log(f"room network settings from player {self.me}, not a waiting room's host: ignored")
                return True
            room.net = {"rollback": r.bool(), "delay": min(r.uint(), 240), "smoothing": r.bool(),
                        "smoothing_ms": min(r.uint(), 1000)}
            n = self.rooms.broadcast(room, room_net_packet(room, announce=True), spectators=False)
            log(f"room {room.number} network settings {room.net} (11017 to {n})")
        elif p.type == LEAVE_MATCH:
            self.leave_match()
        elif p.type == BACK_FROM_SCOREBOARD:
            self.back_from_scoreboard()
        elif p.type == START_DETAILS:
            level = r.uint()
            candidates, random_tile, flags = [], 0, 0
            if r.left() >= 8:
                n = r.uint()
                candidates = [r.uint() for _ in range(min(n, mappick.MAX_CANDIDATES))]
                random_tile = r.uint() if r.left() >= 8 else 0
                flags = start_flags(r)
            mine = self.rooms.room_of(self.me)
            if mine is not None and mine.parties:
                banned = queueprefs.bans(getattr(self.session, "store", None),
                                         [m.player_id for m in mine.members], mine.settings.get("playlist", 0))
                if mine.started or mine.pending_level:
                    if not mine.started and mine.pending_level in banned and candidates:
                        picked = queueprefs.pick(mine.pending_level, candidates, banned)
                        if picked != mine.pending_level:
                            log(f"matched room level {mine.pending_level} is banned by a player (180), playing {picked}"
                                f" (from player {self.me}'s candidates)")
                            mine.pending_level = picked
                    else:
                        log(f"matched room level {level} from player {self.me}: another member's pick came first")
                elif level == 0:
                    log(f"matched room level {level} rejected (level 0 is not selectable)")
                else:
                    picked = queueprefs.pick(level, candidates, banned)
                    if picked != level:
                        log(f"matched room level {level} is banned by a player (180), playing {picked}")
                    mine.pending_level = picked
                    if mine.countdown_over:
                        begin_matched(self.rooms, mine, self.session.log)
                return True
            if mine is not None and mine.started and mine.host == self.me and self.rooms.match_over(mine):
                log(f"room {mine.number}: the host picks a level again, so its last match is over")
            room, why = self.rooms.set_start_level(self.me, level, candidates, random_tile, flags)
            if why is not None:
                log(f"match level {level} rejected ({why})")
            else:
                log(f"room {room.number} host selected level {level}"
                    + (f", {len(candidates)} candidates" if candidates else "")
                    + (" (Training)" if flags & START_TRAINING else ""))
                if flags & START_TRAINING:
                    self.training_bot(room)
        elif p.type == CREATE:
            flags, playlist, custom_type, region, _, _ = r.uint(), r.uint(), r.uint(), r.uint(), r.bool(), r.bool()
            log(f"create room: flags {flags}, playlist {playlist}, custom type {custom_type}, region {region}")
            queued = BY_ID.get(playlist)
            if playlist != 0 and queued is not None and flags == 14:
                self.leave()
                room = self.rooms.create(self.member(), limit=queued.maximum_party,
                                         settings=self.regional(queue_settings(playlist, flags)))
                room.region = region
                assign_teams(room)
                self.session.send(room_packet(room))
                log(f"player {self.me} entered {queued.name} queue in room {room.number} (sent 2445)")
            elif flags not in CUSTOM_FLAGS or playlist != 0:
                self.session.send(failed("That playlist isn't available on this server."))
                log(f"unknown queue request for playlist {playlist}: sent 2446")
            elif self.defaults is None:
                self.session.send(failed("Update the client: the server needs its room settings."))
                log("no custom room defaults (11003) from this client: sent 2446")
            else:
                room = self.rooms.create(self.member(), limit=8, settings=self.regional(default_settings(self.defaults, flags, custom_type)))
                assign_teams(room)
                self.session.send(room_packet(room))
                log(f"room {room.number} created by player {self.me} (sent 2445)")
        elif p.type == JOIN:
            number = r.uint()
            ask = r.left() < 1 or r.bool()
            self.join_room(number, ask_if_full=ask)
        elif p.type == SPECTATE:
            number = r.uint()
            play = r.left() >= 1 and r.bool()
            target = self.rooms.by_number.get(number)
            if (play and target is not None and not target.started and not target.parties
                    and len(target.members) + len(target.bots) < target.limit):
                log(f"player {self.me}: Discord join of room {number} as a player")
                self.join_room(number)
                return True
            self.spectate_room(number)
        elif p.type == P2P_HELLO:
            port, n = r.uint(), r.uint()
            addresses = [r.string() for _ in range(min(n, MAX_ADDRESSES))]
            addresses = [a for a in addresses if is_ipv4(a)]
            seen = getattr(self.session, "peer_ip", None)
            if seen and is_ipv4(seen) and not seen.startswith("127.") and seen not in addresses:
                addresses.append(seen)
            room = self.rooms.p2p_hello(self.me, port & 0xFFFF, addresses)
            if room is None:
                log(f"P2P hello from player {self.me} outside a started room: ignored")
                return True
            n = self.send_peers(room)
            log(f"P2P hello from player {self.me} in room {room.number}: UDP {port}, {addresses} (11012 to {n})")
        elif p.type == P2P_CHOICE:
            number, want = r.uint(), r.uint() != 0
            room = self.rooms.room_of(self.me)
            if room is None or room.number != number or self.me not in room.p2p:
                log(f"P2P choice from player {self.me} for room {number}, not its started match: ignored")
                return True
            mine = room.p2p_choice.setdefault(self.me, {})
            for pid in room.p2p:
                if pid != self.me and not self._friends(self.me, pid):
                    mine[pid] = want
            both = [pid for pid, w in mine.items() if w and room.p2p_choice.get(pid, {}).get(self.me) is True]
            n = self.send_peers(room) if both else 0
            log(f"P2P choice from player {self.me} in room {number}: {'DIRECT' if want else 'relay'}"
                + (f"; both chose DIRECT with {both} (11012 to {n})" if both else ""))
        elif p.type == SETTINGS:
            room = self.rooms.room_of(self.me)
            if room is None or room.host != self.me:
                log("settings from someone who isn't a room's host: ignored")
                return True
            s = read_settings(r, with_description=True)
            if room.tourney is not None:
                w = BitWriter()
                write_settings(w, room.settings)
                self.session.send(_packet(SETTINGS_CHANGED, w))
                log(f"room {room.number} is a tournament match's: settings change refused (2448 back)")
                return True
            if s["playlist"] == 0 and len(s["game"]) == GAME_SETTINGS:
                room.settings = s
                assign_teams(room)
                w = BitWriter()
                write_settings(w, s)
                n = self.rooms.broadcast(room, _packet(SETTINGS_CHANGED, w))
                log(f"room {room.number} settings changed (2448 to {n}): game {s['game']}")
        elif p.type == MODS_LIST:
            fingerprint, n = r.uint(), r.uint()
            entries = [r.string() for _ in range(min(n, MAX_MOD_ENTRIES))]
            room = self.rooms.room_of(self.me)
            me = next((m for m in room.members if m.player_id == self.me), None) if room else None
            if me is not None:
                me.mods = (fingerprint, entries)
                log(f"player {self.me} in room {room.number}: {len(entries)} content mods, fingerprint {fingerprint:08x}")
        elif p.type in (LOADOUT, UNREADY):
            room = self.rooms.room_of(self.me)
            me = next((m for m in room.members if m.player_id == self.me), None) if room else None
            if me is None:
                self.session.send(_packet(OUT_OF_SYNC, BitWriter()))
                log(f"packet {p.type} outside a room: the game is out of sync (sent 2456)")
                return True
            if p.type == LOADOUT:
                l = read_loadout(p.payload)
                if l["bot"]:
                    self.bot_loadout(room, l)
                    return True
                off = disabled_heroes(self.session.cfg) if getattr(self.session, "cfg", None) else set()
                bad = [i for i, x in enumerate(l["loadouts"]) if not x.random and x.hero in off]
                t_off = room.tourney.get("heroes_off") if room.tourney else None
                if t_off and not bad:
                    bad = [i for i, x in enumerate(l["loadouts"]) if x.hero in t_off]
                    if bad and room.tourney.get("why"):
                        self.session.send(_packet(2426, BitWriter().string(room.tourney["why"]).bool(False)))
                if bad:
                    me.ready = 0
                    for i in bad:
                        self.rooms.broadcast(room, picks_reset_packet(self.me, i))
                    log(f"player {self.me} in room {room.number} locked in a switched-off legend "
                        f"({', '.join(str(l['loadouts'][i].hero) for i in bad)}): refused (2458)")
                    return True
                me.color, me.extra, me.ready = l["color"], l["extra"], 1
                me.loadouts = l["loadouts"] or [Loadout()]
            else:
                me.ready = 0
            n = self.rooms.broadcast(room, entry_changed(me))
            log(f"player {self.me} in room {room.number} " + ("locked in " + ", ".join(
                f"{x.hero}" + (f" (random, skin {x.costume})" if x.random else "") for x in me.loadouts)
                if p.type == LOADOUT else "un-readied") + f" (2437 to {n})")
            if p.type == LOADOUT and room.parties and room.started:
                self.rematch(room)
            if room.rematch_group is not None:
                self.rematch_ready()
        elif p.type == EMOTE:
            emote, sub = r.uint(), r.uint()
            room = self.rooms.room_of(self.me)
            if room is not None and not self.rooms.is_spectator(self.me):
                self.rooms.broadcast(room, _packet(EMOTE_SHOWN, BitWriter().uint(self.me).uint(sub).uint(emote)))
        elif p.type == START:
            room, seed, why = self.rooms.start(self.me)
            mode = room.settings.get("select_mode", 0) if room is not None else 0
            if why is None and mappick.wanted(mode, room.candidates):
                self.open_level_select(room, mode)
            elif why is None:
                self.send_start(room, room.start_level)
            else:
                log(f"start requested in room {room.number if room else '?'}: rejected ({why})")
                if why.startswith("content mods differ"):
                    sharing = self.rooms.library is not None and self.session.cfg.get("web_port") and modshare.offers(room, self.rooms.library)[1]
                    self.session.send(_packet(START_REFUSED, BitWriter().string(
                        why[0].upper() + why[1:] + ". Everyone needs the same mods switched on (Mods window), then lock in again."
                        + (" The others have been offered your mods through the server." if sharing else ""))))
                    self.offer_mods(room)
        elif p.type == READY_QUEUE and self.rematch_ready():
            pass
        elif p.type == READY_QUEUE:
            room = self.rooms.room_of(self.me)
            queued = BY_ID.get(room.settings.get("playlist", 0)) if room is not None else None
            accepted = (queued is not None and room.host == self.me
                        and queued.minimum_party <= len(room.members) <= queued.maximum_party
                        and all(m.ready for m in room.members))
            modded = [m for m in room.members if m.mods and m.mods[1]] if accepted else []
            if modded:
                accepted = False
                names = ", ".join(sorted({e.split("@")[0] for m in modded for e in m.mods[1]}))
                for m in modded:
                    if m.send is not None:
                        m.send(_packet(START_REFUSED, BitWriter().string(
                            "Queues are played without gameplay mods (" + names + "). Switch them off in the Mods window "
                            "and restart the game to queue; custom rooms still allow them when everyone has the same.")))
                log(f"queue room {room.number}: gameplay mods ({names}), Ready for Battle refused")
            w = BitWriter().bool(accepted)
            if accepted:
                w.uint(0).bool(False)
            if room is not None and queued is not None:
                room.searching = accepted
                room.since = time.monotonic()
                self.rooms.broadcast(room, _packet(QUEUE_WAITING, w), spectators=False)
                log(f"queue room {room.number} Ready for Battle {'accepted' if accepted else 'rejected'} (2472)")
                if accepted:
                    matchmaker_for(self.rooms, self.session).run()
            else:
                self.session.send(_packet(QUEUE_WAITING, w))
                log("Ready for Battle outside a queue: rejected (2472)")
        elif p.type == CANCEL_QUEUE and getattr(self.rooms.room_of(self.me), "rematch_group", None) is not None:
            log(f"player {self.me}: queue cancel in its rematch lobby, ignored (the rematch is starting)")
        elif p.type == CANCEL_QUEUE:
            room = self.rooms.cancel_search(self.me)
            if room is not None and room.settings.get("playlist", 0) != 0:
                closed = _packet(QUEUE_CANCELLED, BitWriter().bool(False))
                n = self.rooms.broadcast(room, closed, spectators=False)
                self.rooms.broadcast(room, room_packet(room), spectators=False)
                log(f"player {self.me} cancelled matchmaking for playlist {room.settings['playlist']} "
                    f"(2428 false + 2445 to {n}; party retained and un-readied)")
            else:
                self.session.send(_packet(LEFT_SELF, BitWriter().uint(self.me).bool(False)))
                log("cancel/leave outside a queue: exited stale lobby (2412)")
        elif p.type in (LEAVE_LOBBY, LEAVE_SLOT):
            self.leave(notify_self=True)
        elif p.type in (TEAM, ADD_BOT, REMOVE_BOT, BOT_LEVEL):
            self.manage_party(p.type, r)
        elif p.type == HANDICAP:
            self.set_handicap(r)
        elif p.type == DEMOTE:
            self.demote(r.uint(), r.uint())
        elif p.type == TO_PLAYER:
            self.spectator_plays()
        elif p.type in (LEVEL_VOTE, LEVEL_STRIKE_DONE, LEVEL_DONE):
            self.level_select(p.type, r)
        elif p.type == LEVEL_SELECT_BACK:
            self.level_select_back()
        return True

    def level_select_back(self) -> None:
        log = self.session.log
        room = self.rooms.room_of(self.me)
        if room is None or room.settings.get("playlist", 0) != 0 or getattr(room, "tourney", None):
            log(f"player {self.me}: level select back outside a custom room, ignored")
            return
        with self.rooms.lock:
            pick = room.pick
            if pick is None or pick.finished or getattr(pick, "sent", False):
                why = "no vote in progress"
            else:
                why = None
                pick.finished = pick.sent = True
                if pick.timer is not None:
                    pick.timer.cancel()
                room.pick = None
                room.started = False
        if why is not None:
            log(f"player {self.me}: level select back in room {room.number}: {why}, ignored")
            return
        me = next((m for m in room.members if m.player_id == self.me), None)
        if me is not None:
            me.ready = 0
        n = self.rooms.broadcast(room, _packet(LEVEL_SELECT_BACK, BitWriter().uint(self.me)), spectators=False)
        self.rooms.broadcast(room, room_packet(room), spectators=False)
        log(f"player {self.me} went back from room {room.number}'s level select: vote off (11091 + 2445 to {n})")

    def bot_room(self) -> tuple[Room | None, str | None]:
        number = self.rooms.of_player.get(self.me)
        room = self.rooms.by_number.get(number) if number is not None else None
        if room is None:
            return None, "not in a room"
        if room.host != self.me:
            return None, "not the host"
        if room.settings.get("playlist", 0) != 0:
            return None, "bots only in custom rooms"
        if room.searching or room.pick is not None and not room.pick.finished:
            return None, "the match is starting"
        return room, None

    def manage_party(self, kind: int, r: BitReader) -> None:
        log = self.session.log
        if kind == TEAM:
            self.change_team(r)
            return
        slot = level = 0
        if kind == ADD_BOT:
            if not r.bool():
                log(f"player {self.me}: local guests (44, device {r.uint()}) aren't supported: ignored")
                return
            level = r.uint()
        elif kind == REMOVE_BOT and not r.bool():
            log(f"player {self.me}: guest {r.uint()} left (45): no guests here, ignored")
            return
        elif kind == BOT_LEVEL:
            slot, level = r.uint(), r.uint()
        bot = None
        with self.rooms.lock:
            room, why = self.bot_room()
            if room is not None and kind == ADD_BOT:
                if len(room.members) + len(room.bots) >= room.limit:
                    why = "the room is full"
                else:
                    bot = Bot(level=level)
                    room.bots.append(bot)
                    assign_teams(room)
            elif room is not None and kind == REMOVE_BOT:
                if not room.bots:
                    why = "no bots"
                else:
                    room.bots.pop()
            elif room is not None and kind == BOT_LEVEL:
                i = slot - len(room.members)
                if not 0 <= i < len(room.bots):
                    why = f"slot {slot} is no bot"
                else:
                    room.bots[i].level = level
        if why is not None:
            log(f"Manage Party packet {kind} from player {self.me}: {why}, ignored")
            return
        if kind == ADD_BOT:
            packet = _packet(JOINED, BitWriter().bool(True).uint(bot.level).uint(bot.team).uint(bot.ready))
            what = f"added a level {level} bot"
        elif kind == REMOVE_BOT:
            packet = _packet(LEFT_OTHER, BitWriter().bool(True))
            what = "removed a bot"
        else:
            packet = _packet(BOT_LEVEL_CHANGED, BitWriter().uint(slot).uint(level))
            what = f"set slot {slot}'s bot to level {level}"
        n = self.rooms.broadcast(room, packet)
        log(f"room {room.number}: host {what} ({len(room.bots)} bots; {packet.type} to {n})")

    def set_handicap(self, r: BitReader) -> None:
        log = self.session.log
        h = (r.small(), r.uint(), r.uint()) if r.bool() else None
        is_bot = r.bool()
        a, b = r.uint(), r.uint()
        if h is not None and not (h[0] <= 99 and all(v == 0 or 10 <= v <= 1000 for v in h[1:])):
            log(f"handicap from player {self.me} out of range {h}: ignored")
            return
        with self.rooms.lock:
            number = self.rooms.of_player.get(self.me)
            room = self.rooms.by_number.get(number) if number is not None else None
            why, target = None, None
            if room is None or room.settings.get("playlist", 0) != 0:
                why = "not in a custom room"
            elif not handicaps_on(room.settings):
                why = "handicaps are off"
            elif room.started or room.searching:
                why = "the match is starting"
            elif is_bot:
                i = a - len(room.members)
                if room.host != self.me:
                    why = "not the host"
                elif not 0 <= i < len(room.bots):
                    why = f"slot {a} is no bot"
                else:
                    target = room.bots[i]
            else:
                target = next((m for m in room.members if m.player_id == a), None)
                if target is None or b != 0:
                    why, target = f"no player {a}/{b} here", None
                elif room.host != self.me and a != self.me:
                    why, target = "not the host", None
            if target is not None:
                target.handicap = h
        if why is not None:
            log(f"handicap from player {self.me}: {why}, ignored")
            return
        w = BitWriter().bool(is_bot)
        if is_bot:
            w.uint(a)
        else:
            w.uint(a).uint(0)
        write_handicap(w, h)
        n = self.rooms.broadcast(room, _packet(HANDICAP_CHANGED, w))
        log(f"room {room.number}: {'bot slot' if is_bot else 'player'} {a} handicap {h or 'default'} (2544 to {n})")

    def demote(self, player_id: int, sub_slot: int) -> None:
        log = self.session.log
        with self.rooms.lock:
            room, why = self.bot_room()
            target = next((m for m in room.members if m.player_id == player_id), None) if room else None
            if room is not None and (target is None or sub_slot != 0):
                why = f"no player {player_id}/{sub_slot} here"
            elif room is not None and player_id == room.host:
                why = "the host can't be demoted"
            if why is None:
                room.members.remove(target)
                target.ready, target.handicap = 0, None
                room.spectators.append(target)
                room.p2p.pop(player_id, None)
                assign_teams(room)
        if why is not None:
            log(f"demote from player {self.me}: {why}, ignored")
            return
        if target.send is not None:
            try:
                target.send(_packet(NOW_SPECTATOR, BitWriter()))
            except OSError:
                pass
        left = _packet(LEFT_OTHER, BitWriter().bool(False).uint(player_id).uint(0).bool(True))
        n = self.rooms.broadcast(room, left, but=player_id)
        self.rooms.broadcast(room, spectator_packet(target, demoted=True), but=player_id)
        log(f"room {room.number}: host demoted player {player_id} to a spectator (2409; 2444 + 2411 to {n})")

    def spectator_plays(self) -> None:
        log = self.session.log
        why = None
        with self.rooms.lock:
            number = self.rooms.of_player.get(self.me)
            room = self.rooms.by_number.get(number) if number is not None else None
            me = next((s for s in room.spectators if s.player_id == self.me), None) if room else None
            if me is None:
                why = "not a spectator"
            elif room.started or room.searching or room.pick is not None and not room.pick.finished:
                why = "the match is starting"
            elif len(room.members) + len(room.bots) >= room.limit:
                why = "the room is full"
            else:
                room.spectators.remove(me)
                me.ready = 0
                room.members.append(me)
                assign_teams(room)
        if why is not None:
            log(f"player {self.me} asked to play: {why}, ignored")
            return
        w = BitWriter().bool(False)
        write_entry(w, me)
        w.bool(True)
        n = self.rooms.broadcast(room, _packet(JOINED, w))
        log(f"spectator {self.me} in room {room.number} now plays (2449 to {n})")

    def spectate_room(self, number: int) -> None:
        log = self.session.log
        room, why = self.rooms.spectate(number, self.member())
        if room is None:
            self.session.send(failed(f"Room {number}: {why}."))
            log(f"spectate room {number}: {why} (sent 2446)")
            return
        for m in room.members:
            theirs = self.rooms.mod_items.get(m.player_id)
            if theirs is not None:
                self.session.send(_packet(MOD_ITEMS, BitWriter().uint(m.player_id).string(theirs)))
        self.session.send(room_packet(room, spectator=True))
        me = next((s for s in room.spectators if s.player_id == self.me), None)
        n = self.rooms.broadcast(room, spectator_packet(me), but=self.me) if me is not None else 0
        log(f"player {self.me} is spectating room {number} (sent 2445; 2411 to {n})")
        from . import spectate
        if room.started and me is not None:
            spectate.late_ticket(self.rooms, room, me, log)

    def room_ratings(self, room: Room) -> int:
        playlist = BY_ID.get(room.settings.get("playlist", 0))
        store = getattr(self.session, "store", None)
        if (store is None or playlist is None or not playlist.ranked or not playlist.team_rating
                or len(room.members) != 2):
            return 0
        try:
            from . import stats
            ladder = stats.LADDER_OF_PLAYLIST.get(playlist.name)
            if ladder is None:
                return 0
            ledger = stats.ledger_for(store)
            with ledger.lock:
                accs = {m.player_id: ledger.account_of_player(m.player_id) for m in room.members}
                if not all(accs.values()):
                    return 0
                rows = {pid: ledger.rating(acc, ladder) for pid, acc in accs.items()}
                ids = sorted(accs)
                team = ledger.team_rating(accs[ids[0]], accs[ids[1]])
        except Exception:
            return 0
        def stars(row):
            return row["shown"] if row.get("shown") is not None else stats.stars_for(row["rating"])
        w = BitWriter()
        for pid in ids:
            row = rows[pid] or {"rating": stats.START_RATING, "peak": stats.START_RATING, "games": 0, "wins": 0}
            w.bool(True).uint(pid).small(playlist.elo_id).uint(row["games"]).uint(row["wins"])
            w.small(row["rating"]).small(stars(row)).small(row["peak"])
        team = team or {"rating": stats.START_RATING, "peak": stats.START_RATING, "games": 0, "wins": 0}
        w.bool(False).bool(True).small(playlist.elo_id).uint(ids[0]).uint(ids[1]).uint(team["games"]).uint(team["wins"])
        w.small(team["rating"]).small(stars(team)).small(team["peak"])
        n = self.rooms.broadcast(room, _packet(ROOM_RATINGS, w), spectators=False)
        self.session.log(f"room {room.number}: the pair's team rating {team['rating']} (2451 to {n})")
        return n

    def join_room(self, number: int, ask_if_full: bool = True) -> None:
        log = self.session.log
        room, why = self.rooms.join(number, self.member())
        if room is None:
            full = self.rooms.by_number.get(number) if why == "the room is full" else None
            if full is not None and full.settings.get("playlist", 0) == 0:
                if not ask_if_full:
                    log(f"join room {number}: {why}, spectating instead")
                    return self.spectate_room(number)
                self.session.send(_packet(FULL_SPECTATE, BitWriter().uint(number)))
                log(f"join room {number}: {why}, offered to spectate (sent 2447)")
                return
            self.session.send(failed(f"Room {number}: {why}."))
            log(f"join room {number}: {why} (sent 2446)")
            return
        assign_teams(room)
        self.send_members_items(room)
        self.session.send(room_packet(room))
        me = next(m for m in room.members if m.player_id == self.me)
        w = BitWriter().bool(False)
        write_entry(w, me)
        w.bool(False)
        n = self.rooms.broadcast(room, _packet(JOINED, w), but=self.me)
        log(f"player {self.me} joined room {number} (sent 2445; 2449 to {n})")
        self.room_ratings(room)
        if room.net is not None:
            self.session.send(room_net_packet(room))

    def training_bot(self, room: Room) -> None:
        with self.rooms.lock:
            if room.bots or len(room.members) >= room.limit:
                return
            bot = Bot()
            room.bots.append(bot)
            assign_teams(room)
            slot = len(room.members) + len(room.bots) - 1
        n = self.rooms.broadcast(room, _packet(JOINED, BitWriter().bool(True).uint(bot.level).uint(bot.team).uint(bot.ready)))
        from .gamedata import default_costume
        bot.loadouts = [Loadout(hero=TRAINING_BOT_HERO, random=False, costume=default_costume(TRAINING_BOT_HERO))]
        bot.ready = 1
        self.rooms.broadcast(room, entry_changed(bot, slot))
        self.session.log(f"room {room.number}: added the Training bot, legend {TRAINING_BOT_HERO} (2449 + 2437 to {n})")

    def change_team(self, r: BitReader) -> None:
        log = self.session.log
        is_bot = r.bool()
        slot = r.uint() if is_bot else None
        player = None if is_bot else r.uint()
        if not is_bot:
            r.uint()
        team = r.uint()
        room = self.rooms.room_of(self.me)
        if room is None or team not in (0, 1, 2):
            log(f"team change from player {self.me} outside a room or to team {team}: ignored")
            return
        if is_bot:
            i = slot - len(room.members)
            who = room.bots[i] if 0 <= i < len(room.bots) else None
        else:
            who = next((m for m in room.members if m.player_id == player), None)
        if who is None or (room.host != self.me and player != self.me):
            log(f"team change from player {self.me} for {'bot slot ' + str(slot) if is_bot else player}: refused")
            return
        who.team = team
        n = self.rooms.broadcast(room, entry_changed(who, slot), but=self.me)
        name = f"bot slot {slot}" if is_bot else f"player {player}"
        log(f"room {room.number}: {name} on team {team} (2437 to {n})")

    def bot_loadout(self, room: Room, l: dict) -> None:
        log = self.session.log
        i = l["slot"]
        if room.host != self.me or not 0 <= i < len(room.bots):
            log(f"bot lock-in for bot {i} from player {self.me}: no such bot or not the host, ignored")
            return
        bot = room.bots[i]
        l["slot"] = len(room.members) + i
        bot.color, bot.extra, bot.ready = l["color"], l["extra"], 1
        bot.loadouts = l["loadouts"] or [Loadout()]
        n = self.rooms.broadcast(room, entry_changed(bot, l["slot"]))
        heroes = ", ".join(str(x.hero) for x in bot.loadouts)
        log(f"room {room.number}: bot slot {l['slot']} locked in {heroes} (2437 to {n})")

    def back_from_match(self, kind: int) -> bool:
        room = self.rooms.room_of(self.me)
        if room is None or not room.parties or not room.started:
            return False
        if kind != CANCEL_QUEUE and len(room.parties) >= 2:
            if kind == READY_QUEUE:
                self.rematch(room)
                return True
            return False
        own = self.rooms.split(self.me, self.regional(queue_settings(room.settings["playlist"], room.settings.get("flags", 14))))
        if own is not None:
            self.rooms.broadcast(own, room_packet(own), spectators=False)
            self.session.log(f"party {own.ids()} back from matched room {room.number} in queue room {own.number}")
        return False

    def match_reported(self, room: Room | None) -> None:
        if room is None or not room.parties or not room.started or room.rematch_open:
            return
        playlist = BY_ID.get(room.settings.get("playlist", 0))
        if playlist is None or playlist.rematch_key is None:
            return
        series = matchmaker_for(self.rooms, self.session).series(room)
        if series and max(series.values()) >= series_rules(playlist)[0]:
            self.session.log(f"room {room.number}: the series is decided ({series}); no rematch")
            return
        room.rematch_open, room.rematch_wants = True, {}
        w = BitWriter().bool(True).uint(REMATCH_WINDOW_MS).bool(bool(series))
        if series:
            w.bool(bool(playlist.teams)).small(series_rules(playlist)[1])
            if playlist.teams:
                by_team: dict[int, int] = {}
                for m in room.members:
                    by_team[m.team] = max(by_team.get(m.team, 0), series.get(m.player_id, 0))
                for team, won in by_team.items():
                    w.bool(True).small(won).uint(team)
            else:
                for pid in room.ids():
                    w.bool(True).small(series.get(pid, 0)).uint(pid)
            w.bool(False)
        n = self.rooms.broadcast(room, _packet(REMATCH_WINDOW, w), spectators=False)
        self.session.log(f"room {room.number}: rematch window open for {REMATCH_WINDOW_MS // 1000} s, series {series} "
                         f"(2543 to {n})")

        def close():
            if room.rematch_open:
                room.rematch_open = False
                self.session.log(f"room {room.number}: rematch window closed without everyone")
        t = threading.Timer(REMATCH_WINDOW_MS / 1000 + 1, close)
        t.daemon = True
        t.start()

    def back_from_scoreboard(self) -> None:
        room = self.rooms.room_of(self.me)
        m = next((m for m in room.members if m.player_id == self.me), None) if room is not None else None
        if m is None or not m.scoreboard:
            return
        m.scoreboard = False
        n = self.rooms.broadcast(room, _packet(SCOREBOARD_DONE, BitWriter().uint(self.me)), but=self.me, spectators=False)
        if m.ready and getattr(room, "rematch_group", None) is None:
            m.ready = 0
            self.rooms.broadcast(room, entry_changed(m), but=self.me)
        self.session.send(room_packet(room))
        self.session.log(f"player {self.me} back from room {room.number}'s end screen (11073; 2548 to {n}, 2445 to it)")

    def leave_match(self) -> None:
        room = self.rooms.room_of(self.me)
        if room is not None and room.rematch_open:
            room.rematch_wants[self.me] = False
            self.rooms.broadcast(room, _packet(REMATCH_STATE, BitWriter().uint(self.me).byte(2)), but=self.me,
                                 spectators=False)
        if room is not None and room.parties and room.started:
            self.rooms.leave(self.me)
            self.session.log(f"player {self.me} left room {room.number}'s end screen for the menu (85)")
            if room.members and any(m.player_id == self.me for m in room.lineup):
                self.rooms.broadcast(room, participant_left_packet(self.me))
                self.rooms.broadcast(room, chat_left_packet(self.me))
        else:
            self.session.log(f"player {self.me}: leave the match (85)" + (f" in room {room.number}" if room else ""))
        self.session.send(_packet(LEAVE_MATCH_DONE, BitWriter()))

    def rematch_want(self, want: bool) -> None:
        room = self.rooms.room_of(self.me)
        if room is None or not room.rematch_open:
            self.session.log(f"player {self.me}: rematch {'wanted' if want else 'withdrawn'} outside a rematch window")
            return
        room.rematch_wants[self.me] = want
        self.rooms.broadcast(room, _packet(REMATCH_STATE, BitWriter().uint(self.me).byte(1 if want else 2)),
                             spectators=False)
        self.session.log(f"room {room.number}: player {self.me} {'wants' if want else 'declines'} a rematch")
        if all(room.rematch_wants.get(pid) for pid in room.ids()):
            room.rematch_open = False
            self.session.log(f"room {room.number}: everyone wants a rematch; own lobbies in {REMATCH_AGREED_WAIT:g} s")
            _later(REMATCH_AGREED_WAIT, self.rematch_pick, room)

    def rematch_pick(self, room: Room) -> None:
        room.rematch_open = False
        playlist = BY_ID.get(room.settings.get("playlist", 0))
        group = RematchGroup(playlist=playlist, series_rooms=list(room.series_rooms or [room.number]),
                             teams=[next((m.team for m in room.members if m.player_id in ids), 0) for ids in room.parties])
        for ids in [list(ids) for ids in room.parties]:
            here = [m for m in room.members if m.player_id in ids]
            if not here:
                continue
            own = self.rooms.split(here[0].player_id, self.regional(queue_settings(room.settings["playlist"], 14)))
            if own is None:
                continue
            own.rematch_ms, own.rematch_group = REMATCH_PICK_MS, group
            group.rooms.append(own)
            self.rooms.broadcast(own, room_packet(own), spectators=False)
        self.session.log(f"room {room.number}: rematch; {len(group.rooms)} parties back in their own lobbies for "
                         f"{REMATCH_PICK_MS // 1000} s, series rooms {group.series_rooms}")
        log = self.session.log
        rooms = self.rooms
        session = self.session
        t = threading.Timer(REMATCH_PICK_MS / 1000, lambda: rematch_begin(rooms, session, group, log, "time is up"))
        t.daemon = True
        t.start()

    def rematch_ready(self) -> bool:
        room = self.rooms.room_of(self.me)
        group = getattr(room, "rematch_group", None)
        if group is None:
            return False
        room.searching = all(m.ready for m in room.members)
        if room.searching:
            self.session.log(f"rematch lobby {room.number}: locked in")
        if all(r.searching for r in group.rooms):
            rooms, session, log = self.rooms, self.session, self.session.log

            def still_locked() -> None:
                if all(r.searching and all(m.ready for m in r.members) for r in group.rooms):
                    rematch_begin(rooms, session, group, log, "everyone stayed locked in")
            _later(REMATCH_LOCKED_HOLD, still_locked)
        return True

    def rematch(self, room: Room) -> None:
        playlist = BY_ID.get(room.settings.get("playlist", 0))
        mm = matchmaker_for(self.rooms, self.session)
        series = mm.series(room)
        if series and max(series.values()) >= series_rules(playlist)[0]:
            self.session.log(f"room {room.number}: the series is decided ({series}); no rematch, back to the queue screens")
            for ids in [list(ids) for ids in room.parties]:
                here = [m for m in room.members if m.player_id in ids]
                if here:
                    own = self.rooms.split(here[0].player_id, self.regional(queue_settings(room.settings["playlist"], 14)))
                    if own is not None:
                        self.rooms.broadcast(own, room_packet(own), spectators=False)
            return
        if not self.rooms.rematch(room):
            return
        n = mm.found(room, playlist)
        self.session.log(f"rematch in {playlist.name if playlist else 'a queue'}: room {room.number}, series {series} "
                         f"(2436 + 11020 to {n})")

    def send_start(self, room: Room, level: int) -> None:
        send_start(self.rooms, room, level, self.session.log)

    def open_level_select(self, room: Room, mode: int) -> None:
        open_level_select(self.rooms, room, mode, self.session.log)

    def level_select(self, kind: int, r: BitReader) -> None:
        log = self.session.log
        room = self.rooms.room_of(self.me)
        pick = room.pick if room is not None else None
        if pick is None or pick.finished:
            log(f"level select packet {kind} with no vote running: ignored")
            return
        if kind == LEVEL_DONE:
            log(f"player {self.me} level select timer ran out (56)")
            if pick.mode == mappick.ALL_BUT_X and self.me == room.host:
                self.finish_level_select(room, pick, False)
            elif self.me == room.host:
                self.level_select_back()
            return
        if kind == LEVEL_STRIKE_DONE and pick.mode not in (mappick.STRAIGHT, mappick.SNAKE, mappick.OPEN):
            log(f"player {self.me} done (58) in map selection mode {pick.mode}: ignored")
            return
        slot = r.uint()
        with self.rooms.lock:
            members = room.ids()
            if kind == LEVEL_VOTE:
                level, all_but = r.uint(), r.bool()
                ok = mappick.vote(pick, self.me, level, all_but, members, room.host)
            else:
                mappick.done(pick, self.me, members)
                ok = True
        if kind == LEVEL_VOTE:
            if not ok:
                log(f"player {self.me} vote/strike for level {level} refused")
                return
            shown = level
            if level not in pick.candidates and pick.random_tile:
                shown = pick.random_tile
            w = BitWriter().bool(True).uint(self.me).uint(slot).uint(shown).bool(all_but).small(0).bool(False)
            self.rooms.broadcast(room, _packet(LEVEL_VOTED, w), spectators=False)
            log(f"player {self.me} {'voted for' if pick.mode in (mappick.VOTES, mappick.LEADER) else 'struck'} "
                f"level {level}{' (all but)' if all_but else ''} (2459)")
        else:
            self.rooms.broadcast(room, _packet(LEVEL_STRUCK, BitWriter().uint(self.me).uint(slot)), spectators=False)
            log(f"player {self.me} done striking (2425)")
        if pick.finished:
            self.finish_level_select(room, pick, False)

    def finish_level_select(self, room: Room, pick, timed_out: bool) -> None:
        finish_level_select(self.rooms, room, pick, timed_out, self.session.log)

    def send_peers(self, room: Room) -> int:
        cfg = getattr(self.session, "cfg", None) or {}
        relay_port = int(cfg.get("game_port", 0))
        direct = str(cfg.get("p2p_direct", "friends"))
        ask = direct == "friends" and bool(cfg.get("p2p_ask", True))
        sent = 0
        for m in list(room.members):
            if m.player_id not in room.p2p or m.send is None:
                continue
            token = self.rooms.tokens.issue(room, m.player_id)
            w = BitWriter().uint(relay_port).uint(token >> 32).uint(token & 0xFFFFFFFF)
            others = [(pid, info) for pid, info in room.p2p.items() if pid != m.player_id]
            w.uint(len(others))
            askable = []
            for pid, info in others:
                friends = direct == "friends" and self._friends(m.player_id, pid)
                both = ask and room.p2p_choice.get(m.player_id, {}).get(pid) is True \
                    and room.p2p_choice.get(pid, {}).get(m.player_id) is True
                shown = info["addresses"] if direct == "all" or friends or both else []
                if ask and not friends and pid not in room.p2p_choice.get(m.player_id, {}):
                    askable.append(pid)
                w.uint(pid).uint(info["port"]).uint(len(shown))
                for a in shown:
                    w.string(a)
            w.uint(P2P_ASK_MARK | min(len(askable), 0xFF))
            for pid in askable[:0xFF]:
                w.uint(pid)
            try:
                m.send(_packet(P2P_PEERS, w))
                sent += 1
            except OSError:
                pass
        return sent

    def send_members_items(self, room: Room) -> None:
        for m in room.members:
            theirs = self.rooms.mod_items.get(m.player_id)
            if m.player_id != self.me and theirs is not None:
                self.session.send(_packet(MOD_ITEMS, BitWriter().uint(m.player_id).string(theirs)))

    def on_mod_items(self, text: str) -> None:
        if len(text) > MAX_MOD_ITEMS:
            self.session.log(f"player {self.me}: mod items too long ({len(text)} bytes), ignored")
            return
        changed = self.rooms.mod_items.get(self.me) != text
        self.rooms.mod_items[self.me] = text
        room = self.rooms.room_of(self.me)
        if room is None:
            return
        shared = room.items_shared
        if not changed and self.me in shared:
            return
        shared.add(self.me)
        self.rooms.broadcast(room, _packet(MOD_ITEMS, BitWriter().uint(self.me).string(text)), but=self.me)
        for m in room.members:
            theirs = self.rooms.mod_items.get(m.player_id)
            if m.player_id != self.me and theirs is not None:
                self.session.send(_packet(MOD_ITEMS, BitWriter().uint(m.player_id).string(theirs)))
        if room.started and self.me in room.rejoined:
            self.session.log(f"player {self.me}: mod items shared in room {room.number} (rejoined its running match: "
                             f"no room resend)")
            return
        if room.fresh:
            self.session.log(f"player {self.me}: mod items shared in room {room.number} (its room comes with the start)")
            return
        self.rooms.broadcast(room, room_packet(room), spectators=False)
        if not room.started:
            self.rooms.broadcast(room, room_packet(room, spectator=True), but=self.me, skip={m.player_id for m in room.members})
        self.session.log(f"player {self.me}: mod items shared in room {room.number}")

    def _friends(self, a: int, b: int) -> bool:
        hub = getattr(self.rooms, "social", None)
        if hub is None:
            return False
        keys = {}
        for s in list(hub.sessions):
            pid = hub.pid(s)
            if pid in (a, b) and s.account:
                keys[pid] = hub.key(s)
        ka, kb = keys.get(a), keys.get(b)
        return bool(ka and kb) and hub.are_real_friends(ka, kb)

    def leave(self, notify_self: bool = False) -> None:
        me = self.me
        if me is None:
            return
        room = self.rooms.room_of(me)
        if room is None:
            if notify_self:
                self.session.send(_packet(LEFT_SELF, BitWriter().uint(me).bool(False)))
            return
        if room.parties and not room.started:
            self.session.log(f"player {me} left matched room {room.number} before its start: the others re-queue")
            self.rooms.leave(me)
            if notify_self:
                self.session.send(_packet(LEFT_SELF, BitWriter().uint(me).bool(False)))
            for ids in [list(ids) for ids in room.parties]:
                others = [m for m in room.members if m.player_id in ids]
                if others:
                    own = self.rooms.split(others[0].player_id, self.regional(queue_settings(room.settings["playlist"], 14)))
                    if own is not None:
                        self.rooms.broadcast(own, _packet(QUEUE_CANCELLED, BitWriter().bool(False)), spectators=False)
                        self.rooms.broadcast(own, room_packet(own), spectators=False)
            return
        old_host = room.host
        was_searching = room.searching
        spectator = self.rooms.is_spectator(me)
        self.rooms.leave(me)
        if notify_self:
            self.session.send(_packet(LEFT_SELF, BitWriter().uint(me).bool(False)))
        if not spectator and room.started and room.members and any(m.player_id == me for m in room.lineup):
            self.rooms.broadcast(room, participant_left_packet(me))
            self.rooms.broadcast(room, chat_left_packet(me))
        if spectator:
            self.session.log(f"spectator {me} left room {room.number}")
            return
        if not room.members:
            for s in room.spectators:
                try:
                    s.send(_packet(LEFT_SELF, BitWriter().uint(s.player_id).bool(False)))
                except (OSError, TypeError):
                    pass
            room.spectators = []
        pick = room.pick
        if room.members and len(room.members) < 2 and pick is not None and not pick.finished:
            self.session.log(f"room {room.number}: player {me} left during the level select, ending it")
            self.finish_level_select(room, pick, False)
        if room.members:
            assign_teams(room)
            if was_searching:
                for member in room.members:
                    member.ready = 0
                self.rooms.broadcast(room, _packet(QUEUE_CANCELLED, BitWriter().bool(False)), spectators=False)
                self.rooms.broadcast(room, room_packet(room), spectators=False)
            else:
                self.rooms.broadcast(room, _packet(LEFT_OTHER, BitWriter().bool(False).uint(me).uint(0).bool(False)))
                if room.host != old_host:
                    self.rooms.broadcast(room, _packet(NEW_HOST, BitWriter().uint(room.host).uint(0)))
        self.session.log(f"player {me} left room {room.number}" + ("" if room.members else " (room closed)"))


class RematchGroup:

    def __init__(self, playlist, series_rooms: list[int], teams: list[int]) -> None:
        self.playlist, self.series_rooms, self.teams = playlist, series_rooms, teams
        self.rooms: list[Room] = []
        self.begun = False
        self.lock = threading.Lock()


def rematch_begin(rooms: Rooms, session, group: RematchGroup, log, why: str) -> None:
    with group.lock:
        if group.begun:
            return
        group.begun = True
    live = [r for r in group.rooms if rooms.by_number.get(r.number) is r and r.members]
    if len(live) < 2:
        log(f"rematch: a party left its lobby ({why}); no rematch")
        for r in live:
            r.rematch_ms, r.rematch_group, r.searching = 0, None, False
            rooms.broadcast(r, _packet(REMATCH_CANCELLED, BitWriter()), spectators=False)
            rooms.broadcast(r, room_packet(r), spectators=False)
        return
    for r in live:
        r.searching = True
        r.rematch_ms, r.rematch_group = 0, None
        for m in r.members:
            m.ready = 1
    merged = rooms.merge(live, group.teams[:len(live)], live[0].settings)
    if merged is None:
        log("rematch: the lobbies changed; no rematch")
        return
    merged.series_rooms = group.series_rooms + [merged.number]
    merged.fresh = True
    mm = matchmaker_for(rooms, session)
    n = mm.found(merged, group.playlist)
    log(f"rematch ({why}): room {merged.number}, series rooms {merged.series_rooms} (2436 + 11020 to {n})")


def room_net_packet(room: Room, announce: bool = False) -> Packet:
    net = room.net or ROOM_NET_DEFAULT
    w = BitWriter().uint(room.number).uint(room.host if announce else 0)
    w.bool(net["rollback"]).uint(net["delay"]).bool(net["smoothing"]).uint(net["smoothing_ms"])
    return _packet(ROOM_NET, w)


def cosmetics_packet(player_id: int, values: list[int]) -> Packet:
    w = BitWriter().uint(player_id).uint(len(values))
    for v in values:
        w.uint(v)
    return _packet(COSMETICS, w)


def participants_packet(room: Room, heroes: dict | None = None) -> Packet:
    w = BitWriter()
    for m in (room.lineup or room.members):
        hero = (heroes or {}).get(m.player_id, 0)
        if not hero and m.loadouts and not m.loadouts[0].random:
            hero = m.loadouts[0].hero
        w.bool(True).uint(m.player_id).uint(PC_PLATFORM).string(m.name).uint(hero).string(m.name)
    w.bool(False)
    for s in room.spectators:
        w.bool(True).uint(s.player_id).uint(PC_PLATFORM).string(s.name).string(s.name)
    w.bool(False)
    return _packet(PARTICIPANTS, w)


def participant_left_packet(player_id: int) -> Packet:
    return _packet(PARTICIPANT_LEFT, BitWriter().uint(player_id))


def chat_left_packet(player_id: int) -> Packet:
    return _packet(CHAT_LEFT, BitWriter().uint(player_id))


def left_participants(room: Room) -> list[int]:
    here = set(room.ids())
    return [m.player_id for m in room.lineup if m.player_id not in here]


def on_scoreboard(rooms: Rooms, room: Room, player_id: int) -> bool:
    m = next((m for m in room.members if m.player_id == player_id), None)
    if m is None or room.parties or m.scoreboard:
        return False
    m.scoreboard = True
    rooms.broadcast(room, entry_changed(m), but=player_id, spectators=False)
    return True


def send_start(rooms: Rooms, room: Room, level: int, log) -> None:
    for m in room.members:
        values = rooms.cosmetics.get(m.player_id)
        if values is not None:
            rooms.broadcast(room, cosmetics_packet(m.player_id, values), but=m.player_id, spectators=False)
    if room.net is None:
        room.net = dict(ROOM_NET_DEFAULT)
        rooms.broadcast(room, room_net_packet(room), spectators=False)
    for m in room.members:
        m.scoreboard = False
    room.level = level
    w = BitWriter().uint(room.number).uint(room.seed).uint(level)
    flags = room.start_flags if not room.parties else 0
    if room.tourney is not None:
        flags |= START_TOURNEY
    if flags:
        w.uint(START_FLAGS_MARK | flags)
    start = _packet(START_LOCAL, w)
    n = rooms.broadcast(room, start, spectators=False)
    rooms.broadcast(room, participants_packet(room))
    log(f"room {room.number} starting local match: level {level}, seed {room.seed}"
        + (" as Training" if flags & START_TRAINING else "") + f" (11005 to {n})")


def open_level_select(rooms: Rooms, room: Room, mode: int, log) -> None:
    pick = mappick.Pick(mode, list(room.candidates), room.random_tile)
    room.pick = pick
    opened = _packet(LEVEL_SELECT_OPEN, BitWriter().bool(True).uint(0).bool(False))
    opened.v1100 = _packet(LEVEL_SELECT_OPEN, BitWriter().bool(True).uint(0).bool(False).uint(0))
    n = rooms.broadcast(room, opened, spectators=False)
    timeout = mappick.VOTE_TIMEOUT if mode in (mappick.VOTES, mappick.LEADER) else mappick.STRIKE_TIMEOUT
    pick.timer = threading.Timer(timeout, finish_level_select, args=(rooms, room, pick, True, log))
    pick.timer.daemon = True
    pick.timer.start()
    log(f"room {room.number} level select open: mode {mode}, {len(pick.candidates)} levels (2460 to {n})")


def finish_level_select(rooms: Rooms, room: Room, pick, timed_out: bool, log) -> None:
    with rooms.lock:
        if getattr(pick, "sent", False) or room.pick is not pick:
            return
        pick.sent = pick.finished = True
        if pick.timer is not None and not timed_out:
            pick.timer.cancel()
        level = mappick.winner(pick, rooms.rng, room.host)
    w = BitWriter().uint(level).uint(room.host).uint(0)
    rooms.broadcast(room, _packet(LEVEL_WINNER, w), spectators=False)
    log(f"room {room.number} level select over{' (time up)' if timed_out else ''}: level {level} "
        f"({len(pick.votes)} votes, {len(pick.struck)} struck) (2424)")
    send_start(rooms, room, level, log)


START_GAP_MS = int(os.environ.get("BH_START_GAP_MS", "0") or 0)


_hero_ids: dict[str, int] | None = None
_colour_ids: list[int] | None = None
OWNED_COLOURS = 2915


def colour_ids() -> list[int]:
    global _colour_ids
    if _colour_ids is None:
        import re
        from .missions import GAME_DATA
        try:
            text = (GAME_DATA / "ColorSchemeTypes.xml").read_text(encoding="utf-8", errors="replace")
            _colour_ids = sorted({int(x) for x in re.findall(r"<ColorSchemeID>(\d+)</ColorSchemeID>", text)} - {0})
        except OSError:
            _colour_ids = []
    return _colour_ids


def owned_colours_packets(room: Room | None) -> list[Packet]:
    ids = colour_ids()
    if room is None or not ids:
        return []
    out = []
    for m in list(room.members):
        p = _owned_colours.get(m.player_id)
        if p is None:
            w = BitWriter().uint(m.player_id)
            for c in ids:
                w.bool(True).uint(c)
            p = _owned_colours[m.player_id] = _packet(OWNED_COLOURS, w.bool(False).bool(False))
        out.append(p)
    return out


_owned_colours: dict[int, Packet] = {}


def disabled_heroes(cfg: dict) -> set[int]:
    global _hero_ids
    names = {str(v) for k, v in (cfg.get("feature_flags") or {}).items() if str(k).split("-")[0] == "disablehero"}
    if not names:
        return set()
    if _hero_ids is None:
        import re
        from .missions import GAME_DATA
        _hero_ids = {}
        try:
            text = (GAME_DATA / "HeroTypes.xml").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'<HeroType HeroName="([^"]*)">.*?<HeroID>(\d+)</HeroID>', text, re.S):
                _hero_ids[m.group(1)] = int(m.group(2))
        except OSError:
            pass
    return {_hero_ids[n] for n in names if n in _hero_ids}


def picks_reset_packet(player_id: int, sub_slot: int = 0) -> Packet:
    return _packet(PICKS_RESET, BitWriter().bool(False).uint(player_id).uint(sub_slot))


def begin_matched(rooms: Rooms, room: Room, log) -> None:
    if room.started or not room.members:
        return
    level = room.pending_level
    if room.fresh:
        rooms.broadcast(room, _packet(QUEUE_CANCELLED, BitWriter().bool(False)), spectators=False)
        if START_GAP_MS > 0:
            time.sleep(START_GAP_MS / 1000)
        rooms.broadcast(room, room_packet(room), spectators=False)
        room.fresh = False
    started, seed, why = rooms.start_matched(room.members[0].player_id, level)
    if why is not None and why != "already started":
        members = [m.player_id for m in room.members]
        n = rooms.broadcast(room, _packet(NO_GAME_SERVER, BitWriter()), spectators=False)
        for pid in members:
            rooms.leave(pid)
        log(f"matched room {room.number}: not started ({why}); 2427 to {n}, room closed")
        return
    if why is not None:
        log(f"matched room {room.number}: not started ({why})")
        return
    send_start(rooms, started, level, log)


_matchmakers_lock = threading.Lock()


def matchmaker_for(rooms: Rooms, session) -> "Matchmaker":
    with _matchmakers_lock:
        mm = getattr(rooms, "matchmaker", None)
        if mm is None:
            mm = Matchmaker(rooms, session.log, getattr(session, "store", None))
            rooms.matchmaker = mm
        return mm


class Matchmaker:

    def __init__(self, rooms: Rooms, log, store=None, clock=time.monotonic) -> None:
        self.rooms, self.log, self.store, self.clock = rooms, log, store, clock
        self.timer: threading.Timer | None = None
        self.lock = threading.Lock()

    def rating(self, player_id: int, playlist) -> float:
        if self.store is None or not playlist.ranked:
            return matchmaking.START_RATING
        try:
            from . import stats
            ladder = stats.LADDER_OF_PLAYLIST.get(playlist.name, stats.LADDER)
            ledger = stats.ledger_for(self.store)
            account = ledger.account_of_player(player_id)
            row = ledger.rating(account, ladder) if account is not None else None
            return row["rating"] if row else matchmaking.START_RATING
        except Exception:
            return matchmaking.START_RATING

    def party(self, room: Room, playlist) -> matchmaking.Party:
        fingerprints = {m.mods[0] for m in room.members if m.mods is not None}
        ratings = [self.rating(m.player_id, playlist) for m in room.members]
        mods = None if not fingerprints else fingerprints.pop() if len(fingerprints) == 1 else -1
        pings = []
        for m in room.members:
            try:
                pings.append(m.rtt() if callable(m.rtt) else None)
            except Exception:
                pings.append(None)
        known = [x for x in pings if x is not None]
        try:
            from . import reports
            blocks = frozenset(reports.mutes_of(self.store, room.ids()))
        except Exception:
            blocks = frozenset()
        return matchmaking.Party(room, len(room.members), sum(ratings) / len(ratings), room.since,
                                 mods, room.region, room.ids(), max(known) if known else None, blocks)

    def run(self) -> int:
        made = 0
        with self.lock:
            while True:
                by_playlist: dict[int, list[Room]] = {}
                for r in self.rooms.searching_rooms():
                    by_playlist.setdefault(r.settings.get("playlist", 0), []).append(r)
                found = None
                for pid, rs in by_playlist.items():
                    playlist = BY_ID.get(pid)
                    if playlist is None:
                        continue
                    now = self.clock()
                    rs = [r for r in rs if now - r.since >= QUEUE_MIN_WAIT]
                    if not rs:
                        continue
                    hit = matchmaking.find_match([self.party(r, playlist) for r in rs], playlist.match_players,
                                                 playlist.teams, now)
                    if hit is not None:
                        found = (playlist, hit)
                        break
                if found is None:
                    break
                playlist, (parties, teams) = found
                rooms = [p.key for p in parties]
                merged = self.rooms.merge(rooms, teams, rooms[0].settings)
                if merged is None:
                    continue
                made += 1
                self.announce(merged, playlist, parties)
            self.schedule(bool(self.rooms.searching_rooms()))
        return made

    def announce(self, room: Room, playlist, parties) -> None:
        for m in room.members:
            for l in m.loadouts:
                if l.random and l.hero != RANDOM_HERO:
                    l.random = False
        room.fresh = True
        n = self.found(room, playlist)
        self.log(f"queue match in {playlist.name}: room {room.number}, parties "
                 + ", ".join(f"{p.ids} ({p.rating:.0f})" for p in parties) + f", host {room.host} (11020 to {n})")

    def history(self, player_id: int, room: Room, playlist) -> tuple[int, int] | None:
        if self.store is None or playlist.teams:
            return None
        others = [m.player_id for m in room.members if m.player_id != player_id]
        if len(others) != 1:
            return None
        try:
            from . import stats
            ledger = stats.ledger_for(self.store)
            me, them = ledger.account_of_player(player_id), ledger.account_of_player(others[0])
            if me is None or them is None:
                return None
            h = ledger.head_to_head(me, them, stats.LADDER_OF_PLAYLIST.get(playlist.name) if playlist.ranked else None)
            return h["matches"], h["wins"]
        except Exception:
            return None

    def found(self, room: Room, playlist) -> int:
        room.pending_level, room.countdown_over = 0, False
        self.rooms.broadcast(room, _packet(MATCH_FOUND_GAME, BitWriter().uint(1).string("")), spectators=False)
        n = self.send_found(room, playlist)

        def over():
            room.countdown_over = True
            if room.pending_level and not room.started:
                begin_matched(self.rooms, room, self.log)
        if MATCH_COUNTDOWN <= 0:
            over()
        else:
            t = threading.Timer(MATCH_COUNTDOWN, over)
            t.daemon = True
            t.start()
        return n

    def series(self, room: Room) -> dict[int, int]:
        if self.store is None:
            return {}
        try:
            from . import stats
            ledger = stats.ledger_for(self.store)
            wins: dict[int, int] = {}
            with ledger.lock:
                numbers = list(room.series_rooms or [room.number])
                rows = ledger.db.execute(
                    "SELECT rp.player_id AS player, rp.outcome AS outcome FROM results r JOIN result_players rp ON "
                    f"rp.result_id = r.id WHERE r.room IN ({','.join('?' * len(numbers))}) AND r.verdict = ?",
                    (*numbers, stats.COUNTED)).fetchall()
            for row in rows:
                if row["player"] in room.ids():
                    wins[row["player"]] = wins.get(row["player"], 0) + (row["outcome"] == "win")
            return wins if any(wins.values()) else {}
        except Exception:
            return {}

    def ratings(self, room: Room, playlist) -> dict[int, tuple[int, int, int]]:
        if self.store is None or playlist is None or not playlist.ranked:
            return {}
        try:
            from . import stats
            ladder = stats.LADDER_OF_PLAYLIST.get(playlist.name)
            if ladder is None:
                return {}
            ledger = stats.ledger_for(self.store)
            out = {}
            with ledger.lock:
                for m in room.members:
                    acc = ledger.account_of_player(m.player_id)
                    mate = self.pair_account(ledger, room, playlist, m.player_id)
                    row = ((ledger.team_rating(acc, mate) or {"rating": stats.START_RATING, "peak": stats.START_RATING,
                                                              "games": 0, "wins": 0}) if acc and mate else
                           ledger.rating(acc, ladder) if acc else None)
                    rating = row["rating"] if row else stats.START_RATING
                    stars = row["shown"] if row and row.get("shown") is not None else stats.stars_for(rating)
                    out[m.player_id] = (rating, stars, row["games"] if row else 0)
            return out
        except Exception:
            return {}

    @staticmethod
    def pair_account(ledger, room: Room, playlist, player_id: int) -> int | None:
        if playlist is None or not playlist.team_rating:
            return None
        me = next((m for m in room.members if m.player_id == player_id), None)
        party = next((p for p in room.parties if player_id in p and len(p) == 2), None)
        if me is None or party is None:
            return None
        mate = next((m for m in room.members if m.player_id in party and m.player_id != player_id), None)
        if mate is None or not me.team or mate.team != me.team:
            return None
        if sum(1 for m in room.members if m.team == me.team) != 2:
            return None
        acc = ledger.account_of_player(mate.player_id)
        return acc if acc and acc != ledger.account_of_player(player_id) else None

    def cards(self, room: Room, playlist) -> dict[str, dict]:
        if self.store is None or playlist is None or not playlist.ranked:
            return {}
        try:
            from . import stats
            ladder = stats.LADDER_OF_PLAYLIST.get(playlist.name)
            if ladder is None:
                return {}
            ledger = stats.ledger_for(self.store)
            out = {}
            for m in room.members:
                acc = ledger.account_of_player(m.player_id)
                if not acc:
                    continue
                mate = self.pair_account(ledger, room, playlist, m.player_id)
                card = ledger.team_summary(acc, mate) if mate else ledger.ranked_summary(acc, ladder)
                hero = m.loadouts[0].hero if m.loadouts and not m.loadouts[0].random else 0
                if ladder == "1v1" and 0 < hero <= 0xFFFF:
                    row = ledger.rating(acc, f"{stats.LEGEND_PREFIX}{hero}")
                    card["legend"] = {"id": hero, "rating": row["rating"], "games": row["games"],
                                      "wins": row["wins"]} if row else {"id": hero, "rating": None}
                out[str(m.player_id)] = {k: card[k] for k in ("rating", "peak", "games", "wins", "streak", "rank",
                                                               "last", "placement", "legend") if k in card}
            return out
        except Exception:
            return {}

    def send_found(self, room: Room, playlist) -> int:
        sent = 0
        cards = self.cards(room, playlist)
        for m in list(room.members):
            if m.send is None:
                continue
            w = BitWriter().uint(room.number).uint(room.host).uint(playlist.playlist_id if playlist else 0)
            h = self.history(m.player_id, room, playlist) if playlist else None
            w.bool(h is not None).uint(h[0] if h else 0).uint(h[1] if h else 0)
            series = self.series(room)
            w.uint(len(series))
            for pid, won in series.items():
                w.uint(pid).uint(won)
            rated = self.ratings(room, playlist)
            w.uint(len(rated))
            for pid, (rating, stars, games) in rated.items():
                w.uint(pid).uint(rating).uint(stars).uint(games)
            w.string(json.dumps(cards, separators=(",", ":")))
            leaders = self.team_leaders(room, playlist, rated)
            w.uint(len(leaders))
            for team, pid in sorted(leaders.items()):
                w.uint(team).uint(pid)
            try:
                m.send(_packet(MATCH_FOUND, w))
                sent += 1
            except OSError:
                pass
        return sent

    def team_leaders(self, room: Room, playlist, rated: dict) -> dict[int, int]:
        if playlist is None or not playlist.teams:
            return {}
        out: dict[int, int] = {}
        for team in sorted({m.team for m in room.members if m.team}):
            ids = [m.player_id for m in room.members if m.team == team]
            party = next((i for i, p in enumerate(room.parties) if set(ids) <= set(p) and len(p) > 1), None)
            if party is not None and party < len(room.party_hosts) and room.party_hosts[party] in ids:
                out[team] = room.party_hosts[party]
            else:
                out[team] = max(ids, key=lambda pid: (rated.get(pid, (0,))[0], -ids.index(pid)))
        return out

    def schedule(self, again: bool) -> None:
        if not again or (self.timer is not None and self.timer.is_alive()
                         and self.timer is not threading.current_thread()):
            return
        self.timer = threading.Timer(MATCH_TICK, self.run)
        self.timer.daemon = True
        self.timer.start()
