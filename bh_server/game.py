from __future__ import annotations

import collections
from pathlib import Path
import socket
import socketserver
import threading
import traceback

from . import tcprtt, tls
from .bits import BitWriter
from .frame import Decoder, Packet, encode
from .protocol import KEEPALIVE, Session
from .rooms import Rooms
from .whitelist import Whitelist


SERVER_MESSAGE = 16
RESTARTING = "Server is restarting, please wait!"

QUIET = {KEEPALIVE, 11081}

OUTBOX_LIMIT = 32 * 1024 * 1024


class Outbox:

    def __init__(self, sock, limit: int = OUTBOX_LIMIT):
        self.sock, self.limit = sock, limit
        self.frames: collections.deque = collections.deque()
        self.queued = 0
        self.cond = threading.Condition()
        self.failed = False
        self.closing = False
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def send(self, pk) -> None:
        data = encode(pk)
        with self.cond:
            if self.failed:
                raise OSError("connection closed")
            if self.queued + len(data) > self.limit:
                self.failed = True
                self.cond.notify()
                self.shutdown()
                raise OSError(f"connection not reading ({self.queued} bytes waiting)")
            self.frames.append(data)
            self.queued += len(data)
            self.cond.notify()

    def run(self) -> None:
        while True:
            with self.cond:
                while not self.frames and not self.failed and not self.closing:
                    self.cond.wait()
                if self.failed:
                    return
                if not self.frames:
                    self.failed = True
                    self.shutdown()
                    return
                batch = b"".join(self.frames)
                self.frames.clear()
                self.queued = 0
            try:
                self.sock.sendall(batch)
            except OSError:
                with self.cond:
                    self.failed = True
                    self.frames.clear()
                    self.queued = 0
                self.shutdown()
                return

    def close_after_writes(self) -> None:
        with self.cond:
            self.closing = True
            self.cond.notify()

    def shutdown(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


def restart_packet() -> Packet:
    return Packet(SERVER_MESSAGE, BitWriter().string(RESTARTING).bool(False).bytes())


def make_handler(log, on_packet=None, store=None, salt: bytes = b"", rooms: Rooms | None = None, cfg: dict | None = None,
                 logins=None, live: set | None = None):
    rooms = rooms if rooms is not None else Rooms()
    live = live if live is not None else set()
    whitelist = Whitelist(cfg or {})
    tls_dir = cfg and (cfg.get("game_tls_dir") or cfg.get("tls_dir"))
    tls_context = tls.server_context(Path(tls_dir)) if tls_dir else None

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            peer = f"{self.client_address[0]}:{self.client_address[1]}"
            log(f"game client connected from {peer}")
            self.request.settimeout(LOGIN_WITHIN)
            try:
                self.request.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            try:
                conn = tls.accept_game(self.request, tls_context)
            except OSError as e:
                log(f"game client {peer}: " + (f"sent nothing within {LOGIN_WITHIN} s: closed" if isinstance(e, socket.timeout)
                                               else f"no TLS connection ({e})"))
                return
            if conn is not self.request:
                log(f"game client {peer}: TLS")
            dec, count, quiet = Decoder(), 0, {}
            outbox = Outbox(conn)
            send = outbox.send

            session = Session(send, log, store=store, salt=salt, rooms=rooms, cfg=cfg, logins=logins, whitelist=whitelist)
            session.peer_ip = self.client_address[0]
            sock = self.request
            session.rtt = lambda: tcprtt.rtt_ms(sock)

            session.close = outbox.close_after_writes
            live.add(send)
            try:
                while True:
                    try:
                        data = conn.recv(65536)
                    except socket.timeout:
                        if session.player is None:
                            log(f"game client {peer} sent no login within {LOGIN_WITHIN} s: closed")
                            break
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    for p in dec.feed(data):
                        count += 1
                        extra = f" 960={p.field_960}" if p.field_960 else (f" 924={p.field_924}" if p.field_924 else "")
                        if p.type in QUIET:
                            quiet[p.type] = quiet.get(p.type, 0) + 1
                        else:
                            log(f"packet type {p.type}{extra}, {len(p.payload)} payload bytes (from {peer})")
                        try:
                            session.on_packet(p)
                        except Exception:
                            log(f"packet type {p.type} from {peer} failed: {traceback.format_exc().rstrip()}")
                        if on_packet:
                            on_packet(self, p)
            finally:
                live.discard(send)
                session.closed()
                outbox.close_after_writes()
                counted = ", ".join(f"{n} of type {t}" for t, n in sorted(quiet.items()) if t != KEEPALIVE)
                log(f"game client {peer} disconnected after {count} packets" + (f" ({counted} not logged)" if counted else ""))
    return Handler


LOGIN_WITHIN = 60


class GameServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    live: set = set()

    def announce_restart(self) -> int:
        sent = 0
        for send in list(self.live):
            try:
                send(restart_packet())
                sent += 1
            except OSError:
                pass
        return sent


def serve(cfg: dict, log, on_packet=None, store=None, salt: bytes = b"", logins=None,
          rooms: Rooms | None = None) -> GameServer:
    live: set = set()
    server = GameServer((cfg["game_host"], cfg["game_port"]),
                        make_handler(log, on_packet, store, salt, rooms=rooms, cfg=cfg, logins=logins, live=live))
    server.live = live
    return server


def start(server) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return t
