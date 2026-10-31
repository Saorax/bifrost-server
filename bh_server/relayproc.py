"""The P2P relay as a process of its own: what ``relay.Relay`` does inside the server, fed with the valid tokens over
the control connection (``relay.Control``; protocol in relay.py).

started by the server when its config says ``"relay": "process"``; tools/relay (C#) is the native build (``"native"``).

    python -m bh_server.relayproc --port 7910 --control 7912        (BH_RELAY_SECRET in the environment)
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time

from bh_server.relay import HEADER, MAGIC, MAX_BYTES, MAX_DATAGRAM, MAX_RATE, REGISTER, RELAY, TO, Limiter


class Grants:

    def __init__(self, limiter: Limiter | None = None) -> None:
        self.limiter = limiter
        self.by_token: dict[int, tuple[int, int]] = {}
        self.token_of: dict[tuple[int, int], int] = {}
        self.addr: dict[int, tuple[str, int]] = {}
        self.lock = threading.Lock()

    def line(self, text: str) -> None:
        parts = text.split()
        if not parts:
            return
        with self.lock:
            if parts[0] == "GRANT" and len(parts) == 4:
                token, room, player = int(parts[1]), int(parts[2]), int(parts[3])
                old = self.token_of.get((room, player))
                if old is not None and old != token:
                    self.by_token.pop(old, None)
                    self.addr.pop(old, None)
                self.by_token[token] = (room, player)
                self.token_of[(room, player)] = token
            elif parts[0] == "REVOKE" and len(parts) == 2:
                who = self.by_token.pop(int(parts[1]), None)
                if who is not None:
                    self.token_of.pop(who, None)
                self.addr.pop(int(parts[1]), None)
            elif parts[0] == "CLOSE" and len(parts) == 2:
                room = int(parts[1])
                for key in [k for k in self.token_of if k[0] == room]:
                    t = self.token_of.pop(key)
                    self.by_token.pop(t, None)
                    self.addr.pop(t, None)

    def handle(self, data: bytes, addr: tuple[str, int], send) -> str:
        if len(data) > MAX_DATAGRAM or len(data) < HEADER.size:
            return "not ours"
        magic, kind, hi, lo = HEADER.unpack_from(data)
        if magic != MAGIC:
            return "not ours"
        token = (hi << 32) | lo
        with self.lock:
            who = self.by_token.get(token)
            if who is None:
                return "unknown token"
            self.addr[token] = addr
            if self.limiter is not None and not self.limiter.allow(token, len(data)):
                return "over the cap"
            if kind == REGISTER:
                return "registered"
            if kind != RELAY or len(data) < HEADER.size + TO.size:
                return "unknown type"
            (to,) = TO.unpack_from(data, HEADER.size)
            room, sender = who
            if to == sender:
                return "not in the room"
            target_token = self.token_of.get((room, to))
            target = self.addr.get(target_token) if target_token is not None else None
        if target is None:
            return "target not registered"
        send(data[HEADER.size + TO.size:], target)
        return "relayed"


def control(port: int, secret: str, grants: Grants) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(2)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve_control, args=(conn, secret, grants), daemon=True).start()


def serve_control(conn: socket.socket, secret: str, grants: Grants) -> None:
    with conn, conn.makefile("r", encoding="utf-8", errors="replace") as f:
        first = f.readline().strip()
        if first != f"AUTH {secret}":
            print("control: refused a connection (wrong secret)", flush=True)
            return
        print("control: the server connected", flush=True)
        for line in f:
            grants.line(line.strip())
    print("control: the server disconnected (grants kept)", flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--control", type=int, required=True)
    p.add_argument("--max-rate", type=int, default=MAX_RATE, help="datagrams a second per token (O.1)")
    p.add_argument("--max-bytes", type=int, default=MAX_BYTES, help="bytes a second per token (O.1)")
    a = p.parse_args(argv)
    secret = os.environ.get("BH_RELAY_SECRET", "")
    if not secret:
        print("BH_RELAY_SECRET is not set", flush=True)
        return 2
    grants = Grants(Limiter(a.max_rate, a.max_bytes, lambda m: print(m, flush=True)))
    threading.Thread(target=control, args=(a.control, secret, grants), daemon=True).start()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((a.host, a.port))
    try:
        sock.ioctl(socket.SIO_UDP_CONNRESET, False)
    except (AttributeError, OSError, ValueError):
        pass
    for opt in (socket.SO_RCVBUF, socket.SO_SNDBUF):
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, 1 << 20)
        except OSError:
            pass
    print(f"relay (python process) on UDP {a.host}:{a.port}, control 127.0.0.1:{a.control}, cap {a.max_rate} "
          f"datagrams / {a.max_bytes} bytes a second per token", flush=True)
    relayed, last = 0, time.monotonic()
    while True:
        try:
            data, addr = sock.recvfrom(2048)
        except ConnectionResetError:
            continue
        try:
            if grants.handle(data, addr, sock.sendto) == "relayed":
                relayed += 1
        except OSError:
            pass
        now = time.monotonic()
        if now - last >= 300:
            print(f"relayed {relayed} datagrams in the last {int(now - last)} s ({grants.limiter.dropped} over the cap "
                  f"so far)", flush=True)
            relayed, last = 0, now


if __name__ == "__main__":
    sys.exit(main())
