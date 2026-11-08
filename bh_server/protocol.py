from __future__ import annotations

import hashlib
import secrets
import threading
import time

from . import (achievements, battlepass, bugreports, cloudsave, cups, discord, missions, moderation, modshare, picks,
               playerprefs, queueprefs, rejoin, replays, reports, skirmish, spectate, stats, themes, timedevent,
               tournaments, tutorial)
from . import lobby as lobby_mod
from .bits import BitReader, BitWriter
from .frame import Packet
from .lobby import HANDLED as LOBBY_PACKETS, Lobby
from .playlists import write_catalog, write_initial_ratings
from .rooms import Rooms
from .social import Social
from .store import level_span
from .clans import Clans
from .whitelist import Whitelist

HELLO, VERSION, CHALLENGE, LOGIN, LOGIN_ALT, LOGIN_OK, KEEPALIVE = 178, 30, 12000, 20, 88, 2431, 12100
LINK_TOKEN, SET_NAME = 11001, 11002
WEB_SCHEME = 11093
NAME_CHANGED = 2430
LOGIN_START, LOGIN_LINK, LOGIN_RESULT, LOGIN_NEEDED = 11007, 11008, 11009, 11010
NOTICE = 11013
PERKS = 11090
FEATURE_FLAGS = 2528
REGIONS_FOR_CUSTOM = 9

PROTOCOL = "Brawlhalla client to server protocol 1.0"
SIGNED_IN_ELSEWHERE = "You signed in from another game, so this one was signed out."
SIGN_IN_MISCONFIGURED = ("This server's Discord sign-in is set up wrong: its web address won't open in a "
                         "browser. Tell the server's host to check the server log.")
SIGNED_OUT_CLOSE_AFTER = 10.0


def feature_flags(cfg: dict) -> Packet:
    w = BitWriter().bool(True).string("disabledna").string("TRUE")
    for k, v in (cfg.get("feature_flags") or {}).items():
        if str(k).split("-")[0] in ("disabledna", "terminatedna"):
            continue
        w.bool(True).string(str(k)).string(str(v))
    return packet(FEATURE_FLAGS, w.bool(False))


def packet(t: int, w: BitWriter | None = None) -> Packet:
    return Packet(t, w.bytes() if w else b"")


def read_login(payload: bytes) -> dict:
    r = BitReader(payload)
    out = {"name": r.string(), "hash_len": len(r.string()), "s3": r.string(), "persona_name": r.string()}
    n = r.uint()
    for _ in range(n):
        r.byte()
    out["ticket_len"] = n
    out["persona_id"] = r.string()
    out["platform"] = r.byte()
    out["version"] = r.uint()
    out["rest_bits"] = r.left()
    return out


def login_ok(user_id: int, name: str, level: int = 1, xp: int = 0, regions: int = REGIONS_FOR_CUSTOM, display: str | None = None,
             prefs: list | None = None, tutorial_mask: int = 0, client_theme: int = 0,
             settings: dict | None = None, timed_event: tuple[int, int] = (0, 0), v1100: bool = False) -> BitWriter:
    settings = settings or {}
    w = BitWriter()
    w.string(name).uint(max(1, user_id)).bool(False)
    w.string(name if display is None else display)
    level, start, nxt = level_span(xp)
    w.uint(xp).small(level).uint(start).uint(nxt)
    w.uint(tutorial_mask)
    w.string("").string("").string("")
    w.byte(0)
    w.bool(False).uint(0).bool(False)
    w.uint(settings.get("region", 0)).uint(settings.get("servers", 0))
    for _ in range(regions):
        w.bool(True)
    write_catalog(w)
    w.bool(False).bool(False)
    write_initial_ratings(w, max(1, user_id))
    queueprefs.write_rows(w, prefs or [])
    w.bool(False)
    w.bool(False)
    w.bool(False).bool(False).bool(False)
    w.uint(1).string("")
    w.uint(0)
    w.uint(0)
    w.uint(timed_event[0]).uint(timed_event[1])
    w.bool(False)
    w.bool(False)
    w.uint(client_theme)
    if v1100:
        w.bool(False)
    w.byte(settings.get("age", 0))
    w.uint(0).uint(0).uint(0).uint(0).uint(0).uint(0)
    w.string("")
    w.bool(False)
    return w


def player_key(salt: bytes, persona_id: str) -> str:
    return hashlib.sha256(salt + b"steam:" + persona_id.encode()).hexdigest()


def usable_persona(persona_id: str) -> bool:
    return bool(str(persona_id or "").strip().strip("0"))


def account_player_key(account_id: int) -> str:
    return f"account:{account_id}"


class Session:

    def __init__(self, send, log, challenge: str | None = None, store=None, salt: bytes = b"", rooms: Rooms | None = None,
                 cfg: dict | None = None, logins=None, whitelist: Whitelist | None = None):
        self.send, self.log, self.store, self.salt = self._with_colours(send), log, store, salt
        self.cfg, self.logins = cfg or {}, logins
        self.whitelist = whitelist if whitelist is not None else Whitelist(self.cfg)
        self._noticed = 0.0
        self.account: dict | None = None
        self.player: dict | None = None
        self.player_id: int | None = None
        self.player_name = ""
        self.lobby = Lobby(self, rooms if rooms is not None else Rooms())
        self.stats = stats.Desk(self, self.lobby.rooms)
        self.tournaments = tournaments.Desk(self, self.lobby.rooms)
        self.replays = replays.Desk(self, self.lobby.rooms)
        self.library = modshare.Desk(self, self.lobby.rooms)
        self.spectate = spectate.Desk(self, self.lobby.rooms)
        self.reports = reports.Desk(self)
        self.cups = cups.Desk(self)
        self.queueprefs = queueprefs.Desk(self)
        self.picks = picks.Desk(self)
        self.cloudsave = cloudsave.Locker(self)
        self.social = Social.of(self.lobby.rooms, store, self.cfg, log)
        self.clans = Clans.of(self.social, store, log)
        self.skirmish = skirmish.Hub.of(self.social, store, self.cfg, log)
        self.timed_event = timedevent.Hub.of(self.social, store, self.cfg, log)
        self.missions = missions.Hub.of(self.social, store, self.cfg, log)
        self.battlepass = battlepass.Hub.of(self.social, store, self.cfg, log)
        self.achievements = achievements.Hub.of(self.social, store, self.cfg, log)
        self.challenge = challenge or secrets.token_hex(16)
        self.hello = False
        self.login: dict | None = None
        self.client_version = "10.11"
        self.superseded = False

    def _with_colours(self, raw):
        def send(p):
            if p.v1100 is not None and getattr(self, "client_version", "10.11") == "11.00":
                p = p.v1100
            raw(p)
            if p.type in (lobby_mod.ROOM, lobby_mod.JOINED) and getattr(self, "lobby", None) is not None \
                    and self.lobby.me is not None:
                for q in lobby_mod.owned_colours_packets(self.lobby.rooms.room_of(self.lobby.me)):
                    raw(q)
        return send

    def closed(self) -> None:
        if not self.superseded:
            self._leave_everything()
        if self.logins is not None:
            self.logins.forget(self)

    def _leave_everything(self) -> None:
        rejoin.connection_lost(self)
        self.lobby.leave()
        self.spectate.closed()
        self.social.offline(self)

    def sign_out_others(self) -> int:
        if self.store is None or self.player is None:
            return 0
        me, acc = self.player["id"], (self.account["id"] if self.account else None)
        with self.social.lock:
            older = [s for s in self.social.sessions if s is not self and not getattr(s, "superseded", False)
                     and s.player is not None
                     and (s.player["id"] == me or (acc is not None and s.account and s.account["id"] == acc))]
        for s in older:
            s.signed_out_elsewhere()
        if older:
            self.log(f"player {me}: a newer sign-in; signed out {len(older)} older game(s)")
        return len(older)

    def signed_out_elsewhere(self) -> None:
        self.superseded = True
        self._leave_everything()
        from .social import SERVER_MESSAGE, TO_OFFLINE
        try:
            self.send(packet(SERVER_MESSAGE, BitWriter().string(SIGNED_IN_ELSEWHERE).bool(False)))
            self.send(packet(TO_OFFLINE, BitWriter().uint(0)))
        except OSError:
            pass
        self.log(f"player {self.player['id'] if self.player else self.player_id}: signed in from another game; this "
                 "one was signed out (2465, 12002)")
        close = getattr(self, "close", None)
        if close is not None:
            t = threading.Timer(SIGNED_OUT_CLOSE_AFTER, close)
            t.daemon = True
            t.start()

    def renamed(self, name: str) -> None:
        self.player["name"] = self.player_name = name
        self.social.renamed(self)
        n = lobby_mod.renamed(self.lobby.rooms, self.player["id"], name)
        if n:
            self.log(f"rename shown in the room (2450 to {n})")

    def sign_in_available(self) -> bool:
        return bool(self.store is not None and self.logins is not None and self.cfg.get("discord_client_secret")
                    and self.cfg.get("discord_client_id"))

    def signed_in(self, account: dict, token: str) -> None:
        self.account = account
        if self.player is not None and self.store is not None:
            self.store.link_player(self.player["id"], account["id"])
            if getattr(self, "no_persona", False):
                self.store.claim_key(self.player["id"], account_player_key(account["id"]))
            named = self.store.name_at_sign_in(self.player["id"], account)
            if named is not None:
                self.log(f"player {self.player['id']} named {named!r} at sign-in")
                self.send(packet(NAME_CHANGED, BitWriter().string(named)))
                self.renamed(named)
            self.cloudsave.after_login()
        self.sign_out_others()
        self.social.account_changed(self)
        self.clans.online(self)
        self.send_perks()
        moderation.after_login(self)
        self.send(packet(LOGIN_RESULT, BitWriter().bool(True).string(token).string(account["name"])))
        self.log(f"in-game sign-in: player {self.player_id} is now account {account['id']} ({account['name']}) (sent 11009)")
        if not self.whitelist.allows(account):
            self._noticed = 0.0
            self.notice(self.whitelist_refusal())

    def is_developer(self) -> bool:
        ids = {str(i) for i in (self.cfg.get("developer_discord_ids") or [])}
        return bool(self.account and str(self.account.get("discord_id")) in ids)

    def send_perks(self) -> None:
        if self.is_developer():
            self.send(packet(PERKS, BitWriter().bool(True)))
            self.log(f"account {self.account['id']} may use the Developer title (sent 11090)")

    def sign_in_failed(self, message: str) -> None:
        self.send(packet(LOGIN_RESULT, BitWriter().bool(False).string(message).string("")))
        self.log(f"in-game sign-in failed: {message} (sent 11009)")

    def notice(self, text: str) -> None:
        if time.monotonic() - self._noticed < 5:
            return
        self._noticed = time.monotonic()
        self.send(packet(NOTICE, BitWriter().string(text)))

    def whitelist_refusal(self) -> str:
        if not self.account:
            return "This server is whitelisted. Sign in with Discord (an account on its whitelist) to play here."
        return f"Your account ({self.account['name']}) isn't on this server's whitelist."

    def on_packet(self, p: Packet) -> None:
        if self.superseded:
            return
        self.missions.tick(self)
        if p.type in LOBBY_PACKETS and not self.whitelist.allows(self.account):
            self.log(f"packet {p.type} refused: " + (f"account {self.account['id']} not whitelisted" if self.account else "not signed in") +
                     " (whitelisted server)")
            self.notice(self.whitelist_refusal())
            return
        if (self.social.handle(self, p) or self.clans.handle(self, p) or self.tournaments.handle(p)
                or self.stats.handle(p) or self.replays.handle(p) or self.library.handle(p) or self.spectate.handle(p)
                or self.reports.handle(p) or self.queueprefs.handle(p) or self.cups.handle(p) or self.picks.handle(p)
                or self.cloudsave.handle(p) or tutorial.handle(self, p) or playerprefs.handle(self, p)
                or self.skirmish.handle(self, p) or self.missions.handle(self, p) or self.battlepass.handle(self, p)
                or self.achievements.handle(self, p) or rejoin.handle(self, p) or bugreports.handle(self, p)
                or moderation.handle(self, p) or self.social.lobby_packet(self, p, self.lobby.handle)):
            return
        if p.type == HELLO:
            text = BitReader(p.payload).string()
            self.hello = text == PROTOCOL
            self.log(f"hello: {text!r}" + ("" if self.hello else " (unexpected protocol string)"))
        elif p.type == VERSION:
            r = BitReader(p.payload)
            a, b = r.uint(), r.uint()
            self.client_version = "11.00" if a >= 1100000000 else "10.11"
            self.log(f"version packet: {a}, {b} (client {self.client_version}); sending the login challenge (12000)")
            self.send(packet(CHALLENGE, BitWriter().string(self.challenge)))
        elif p.type == KEEPALIVE:
            self.send(packet(KEEPALIVE))
        elif p.type == LINK_TOKEN:
            token = BitReader(p.payload).string()
            self.account = self.store.account_for_token(token) if (self.store is not None and token) else None
            self.log("account token: " + (f"account {self.account['id']} ({self.account['name']})" if self.account else "unknown token, playing as a guest"))
            if self.account and self.player and self.store is not None:
                self.store.link_player(self.player["id"], self.account["id"])
                self.social.account_changed(self)
                self.clans.online(self)
        elif p.type == LOGIN_START:
            if self.player is None:
                self.send(packet(LOGIN_LINK, BitWriter().bool(False).string("Connect to the server first.")))
                self.log("sign-in requested before login: refused (sent 11008)")
            elif not self.sign_in_available():
                self.send(packet(LOGIN_LINK, BitWriter().bool(False).string("This server has no Discord sign-in set up.")))
                self.log("sign-in requested, but the server has no Discord client secret (sent 11008)")
            elif discord.setup_problem(self.cfg):
                self.send(packet(LOGIN_LINK, BitWriter().bool(False).string(SIGN_IN_MISCONFIGURED)))
                self.log(f"sign-in requested, but {discord.setup_problem(self.cfg)} (sent 11008)")
            else:
                state = self.logins.start(self)
                url = discord.authorize_url(self.cfg["discord_client_id"], discord.redirect_uri(self.cfg), state,
                                            discord.wants_guilds(self.cfg))
                self.send(packet(LOGIN_LINK, BitWriter().bool(True).string(url).string(self.logins.code_for(state))))
                self.log(f"sign-in link for player {self.player_id} (sent 11008)")
        elif p.type == SET_NAME:
            name = BitReader(p.payload).string()
            if self.store is None or self.player is None:
                self.log("rename before login: ignored")
            else:
                why = self.store.set_player_name(self.player["id"], name)
                self.log(f"rename player {self.player['id']} to {name!r}: " + ("ok" if why is None else "refused, " + why))
                self.send(packet(NAME_CHANGED, BitWriter().string(name if why is None else self.player["name"])))
                if why is not None:
                    self.notice(f"Your name was not changed: {why}.")
                if why is None and name != self.player["name"]:
                    self.renamed(name)
        elif p.type in (LOGIN, LOGIN_ALT):
            try:
                self.login = read_login(p.payload)
                l = self.login
                self.log(f"login: Steam name {l['persona_name']!r}, platform {l['platform']}, version {l['version']}, "
                         f"Steam ticket {l['ticket_len']} bytes (ignored), {l['rest_bits']} more bits")
                self.no_persona = not usable_persona(l["persona_id"])
                if not self.no_persona:
                    key = player_key(self.salt, l["persona_id"])
                elif self.account:
                    key = account_player_key(self.account["id"])
                else:
                    key = "guest:" + secrets.token_hex(8)
                if self.no_persona:
                    self.log("login without a Steam ID: " + (f"the player of account {self.account['id']}"
                                                             if self.account else "a new guest player"))
                if self.store is not None:
                    self.player = self.store.player(key)
                    owner = self.player["account_id"]
                    if owner and (not self.account or self.account["id"] != owner):
                        self.log(f"login names player {self.player['id']}, which belongs to account {owner}; this "
                                 + ("connection is account " + str(self.account["id"]) if self.account else "connection isn't signed in")
                                 + ": playing as a guest instead")
                        self.player = self.store.player("guest:" + secrets.token_hex(8))
                        self.pending_notice = ("This Steam account's player is tied to a Discord account: sign in with it "
                                               "(Options > Account) to play as that player. You are playing as a guest.")
                    if self.account and self.player["account_id"] != self.account["id"]:
                        self.store.link_player(self.player["id"], self.account["id"])
                    if self.account:
                        named = self.store.name_at_sign_in(self.player["id"], self.account)
                        if named is not None:
                            self.log(f"player {self.player['id']} named {named!r} from the account at login")
                            self.player["name"] = named
                    uid, name = self.player["id"], self.player["name"]
                    acc = self.store.account(self.account["id"]) if self.account else None
                    level, xp = (acc["level"], acc["xp"]) if acc else (1, 0)
                else:
                    uid, name, level, xp = 1, l["persona_name"] or "Player", 1, 0
                self.player_id, self.player_name = uid, name
                self.sign_out_others()
                prefs = queueprefs.load(self.store, uid) if self.store is not None else []
                done = tutorial.mask(self.store, uid) if self.store is not None else 0
                kept = playerprefs.load(self.store, uid)
                self.send(packet(LOGIN_OK, login_ok(uid, name, level, xp, prefs=prefs, tutorial_mask=done, settings=kept,
                                                           client_theme=themes.current(self.social, self.cfg),
                                                           timed_event=timedevent.login_fields(self.store, self.cfg),
                                                           v1100=self.client_version == "11.00")))
                self.login_t0 = time.time()
                self.log(f"login accepted: player {uid} {name!r}" + (f", account {self.account['id']}" if self.account else "") + " (sent 2431)")
                self.send(feature_flags(self.cfg))
                if self.cfg.get("web_scheme") == "https":
                    self.send(packet(WEB_SCHEME, BitWriter().string("https")))
                if getattr(self, "pending_notice", None):
                    self.notice(self.pending_notice)
                    self.pending_notice = None
                self.picks.after_login()
                self.stats.after_login()
                self.reports.after_login()
                self.send_perks()
                moderation.after_login(self)
                self.social.online(self)
                self.clans.online(self)
                self.skirmish.after_login(self)
                self.missions.after_login(self)
                self.battlepass.after_login(self)
                self.achievements.after_login(self)
                self.cloudsave.after_login()
                self.social.login_notice(self)
                rejoin.after_login(self)
                if not self.whitelist.allows(self.account):
                    self.notice(self.whitelist_refusal())
                if self.player is not None and self.player.get("account_id") is None and not self.account \
                        and self.sign_in_available():
                    self.send(packet(LOGIN_NEEDED))
                    self.log(f"player {uid} has no account yet (sent 11010)")
            except IndexError:
                self.log(f"login packet too short ({len(p.payload)} bytes)")
