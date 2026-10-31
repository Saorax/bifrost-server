from __future__ import annotations

import atexit
import os
import queue
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time

MAGIC = 0xBF02
REGISTER, RELAY = 0x11, 0x10
HEADER = struct.Struct(">HBII")
TO = struct.Struct(">I")
MAX_DATAGRAM = 1400

MAX_RATE = 400
MAX_BYTES = 160_000


class Limiter:

    def __init__(self, max_rate: int = MAX_RATE, max_bytes: int = MAX_BYTES, log=None, clock=None) -> None:
        self.max_rate, self.max_bytes, self.log = int(max_rate), int(max_bytes), log
        self.clock = clock or time.monotonic
        self._win: dict[int, list] = {}
        self._swept = self.clock()
        self._lock = threading.Lock()
        self.dropped = 0

    def allow(self, token: int, size: int) -> bool:
        now = self.clock()
        with self._lock:
            w = self._win.get(token)
            if w is None or now - w[0] >= 1.0:
                if w is None:
                    w = self._win[token] = [now, 0, 0, False]
                else:
                    w[0], w[1], w[2] = now, 0, 0
            w[1] += 1
            w[2] += size
            if w[1] <= self.max_rate and w[2] <= self.max_bytes:
                ok = True
            else:
                ok = False
                self.dropped += 1
                first = not w[3]
                w[3] = True
            if now - self._swept >= 60.0:
                self._swept = now
                for t in [t for t, v in self._win.items() if now - v[0] >= 60.0]:
                    del self._win[t]
        if not ok and first and self.log is not None:
            self.log(f"relay: token {token:016x} over the cap ({self.max_rate} datagrams / {self.max_bytes} bytes a "
                     f"second): dropping its excess")
        return ok


def limiter_for(cfg: dict, log=None) -> Limiter:
    return Limiter(int(cfg.get("relay_max_rate") or MAX_RATE), int(cfg.get("relay_max_bytes") or MAX_BYTES), log)


class Tokens:

    def __init__(self) -> None:
        self._by_token: dict[int, tuple[object, int]] = {}
        self._token_of: dict[tuple[int, int], int] = {}
        self._addr: dict[int, tuple[str, int]] = {}
        self._lock = threading.Lock()
        self.listeners: list = []

    def _tell(self, *event) -> None:
        for f in list(self.listeners):
            try:
                f(event)
            except Exception:
                pass

    def issue(self, room, player_id: int) -> int:
        with self._lock:
            key = (id(room), player_id)
            token = self._token_of.get(key)
            if token is None:
                token = secrets.randbits(64) or 1
                self._token_of[key] = token
                self._by_token[token] = (room, player_id)
        self._tell("grant", token, id(room), player_id)
        return token

    def grants(self) -> list[tuple[int, int, int]]:
        with self._lock:
            return [(t, id(room), pid) for t, (room, pid) in self._by_token.items()]

    def forget_player(self, room, player_id: int) -> None:
        with self._lock:
            token = self._token_of.pop((id(room), player_id), None)
            if token is None:
                return
            self._by_token.pop(token, None)
            self._addr.pop(token, None)
        self._tell("revoke", token)

    def register(self, token: int, addr: tuple[str, int]) -> tuple[object, int] | None:
        with self._lock:
            who = self._by_token.get(token)
            if who is not None:
                self._addr[token] = addr
            return who

    def address_of(self, room, player_id: int) -> tuple[str, int] | None:
        with self._lock:
            token = self._token_of.get((id(room), player_id))
            return self._addr.get(token) if token is not None else None

    def forget_room(self, room) -> None:
        with self._lock:
            for key in [k for k in self._token_of if k[0] == id(room)]:
                token = self._token_of.pop(key)
                self._by_token.pop(token, None)
                self._addr.pop(token, None)
        self._tell("close", id(room))


def split(data: bytes) -> tuple[int, int] | None:
    if len(data) < HEADER.size:
        return None
    magic, kind, hi, lo = HEADER.unpack_from(data)
    return (kind, (hi << 32) | lo) if magic == MAGIC else None


def handle(data: bytes, addr: tuple[str, int], tokens: Tokens, rooms, send, limiter: Limiter | None = None) -> str:
    if len(data) > MAX_DATAGRAM:
        return "too long"
    head = split(data)
    if head is None:
        return "not ours"
    kind, token = head
    who = tokens.register(token, addr)
    if who is None:
        return "unknown token"
    if limiter is not None and not limiter.allow(token, len(data)):
        return "over the cap"
    room, sender = who
    if room is not rooms.room_of(sender) or not (room.started or room.parties):
        return "room over"
    if kind == REGISTER:
        return "registered"
    if kind != RELAY or len(data) < HEADER.size + TO.size:
        return "unknown type"
    (to,) = TO.unpack_from(data, HEADER.size)
    if to == sender or rooms.room_of(to) is not room:
        return "not in the room"
    target = tokens.address_of(room, to)
    if target is None:
        return "target not registered"
    send(data[HEADER.size + TO.size:], target)
    return "relayed"


class Relay:

    def __init__(self, cfg: dict, rooms, tokens: Tokens, log) -> None:
        self.rooms, self.tokens, self.log = rooms, tokens, log
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((cfg["game_host"], cfg["game_port"]))
        try:
            self.sock.ioctl(socket.SIO_UDP_CONNRESET, False)
        except (AttributeError, OSError, ValueError):
            pass
        for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
            try:
                self.sock.setsockopt(socket.SOL_SOCKET, opt, 1 << 20)
            except OSError:
                pass
        self.port = self.sock.getsockname()[1]
        self.relayed = 0
        self.limiter = limiter_for(cfg, log)

    def serve_forever(self) -> None:
        while True:
            try:
                data, addr = self.sock.recvfrom(2048)
            except ConnectionResetError:
                continue
            except OSError:
                return
            try:
                if handle(data, addr, self.tokens, self.rooms, self.sock.sendto, self.limiter) == "relayed":
                    self.relayed += 1
            except OSError:
                pass

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, daemon=True)
        t.start()
        return t

    def close(self) -> None:
        self.sock.close()


class Control:

    def __init__(self, port: int, secret: str, tokens: Tokens, log) -> None:
        self.port, self.secret, self.tokens, self.log = port, secret, tokens, log
        self.queue: "queue.Queue[str]" = queue.Queue()
        tokens.listeners.append(self.on_event)
        threading.Thread(target=self.run, daemon=True).start()

    def on_event(self, event: tuple) -> None:
        self.queue.put(" ".join(str(x) for x in (event[0].upper(),) + tuple(event[1:])))

    def run(self) -> None:
        while True:
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
                    s.settimeout(None)
                    lines = [f"AUTH {self.secret}"] + [f"GRANT {t} {r} {p}" for t, r, p in self.tokens.grants()]
                    s.sendall(("\n".join(lines) + "\n").encode())
                    self.log(f"relay process: connected on control port {self.port} ({len(lines) - 1} grants)")
                    while True:
                        line = self.queue.get()
                        s.sendall((line + "\n").encode())
            except OSError:
                time.sleep(1)


def control_port(cfg: dict) -> int:
    return int(cfg.get("relay_control_port") or int(cfg["game_port"]) + 2)


def command(cfg: dict) -> list[str] | None:
    mode = str(cfg.get("relay", "inprocess"))
    args = ["--host", str(cfg["game_host"]), "--port", str(cfg["game_port"]), "--control", str(control_port(cfg)),
            "--max-rate", str(int(cfg.get("relay_max_rate") or MAX_RATE)),
            "--max-bytes", str(int(cfg.get("relay_max_bytes") or MAX_BYTES))]
    if mode == "process":
        return [sys.executable, "-u", "-m", "bh_server.relayproc"] + args
    if mode == "native":
        exe = str(cfg.get("relay_native") or "")
        if not os.path.isfile(exe):
            return None
        return [exe] + args
    return None


def launch(cfg: dict, tokens: Tokens, log) -> object:
    cmd = command(cfg)
    secret = secrets.token_hex(16)
    env = dict(os.environ, BH_RELAY_SECRET=secret)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    state = {"proc": None}

    def watch() -> None:
        while True:
            log(f"relay process: starting {' '.join(cmd)}")
            p = subprocess.Popen(cmd, cwd=here, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                 errors="replace")
            state["proc"] = p
            for line in p.stdout:
                log(f"relay process: {line.rstrip()}")
            log(f"relay process: stopped (exit {p.wait()}); restarting in 1 s")
            time.sleep(1)

    atexit.register(lambda: state["proc"] is not None and state["proc"].poll() is None and state["proc"].kill())
    threading.Thread(target=watch, daemon=True).start()
    Control(control_port(cfg), secret, tokens, log)
    return state
