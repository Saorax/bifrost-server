from __future__ import annotations

import json
import queue
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Callable, NamedTuple

API = "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials"
MAX_TTL = 48 * 3600
MIN_TTL = 600
RETRY_AFTER = 60.0
TIMEOUT = 5.0
UDP_URL = re.compile(r"turn:([^:?/]+):(\d+)\?transport=udp")


class Credential(NamedTuple):
    host: str
    ports: tuple[int, ...]
    username: str
    credential: str
    expires: int


def parse_ice_servers(data: dict) -> tuple[str, tuple[int, ...], str, str] | None:
    servers = data.get("iceServers") if isinstance(data, dict) else None
    if isinstance(servers, dict):
        servers = [servers]
    for s in servers if isinstance(servers, list) else []:
        if not isinstance(s, dict) or not s.get("username") or not s.get("credential"):
            continue
        urls = s.get("urls")
        host, ports = None, []
        for u in [urls] if isinstance(urls, str) else (urls if isinstance(urls, list) else []):
            m = UDP_URL.fullmatch(str(u))
            if m and host in (None, m.group(1)) and 0 < int(m.group(2)) <= 0xFFFF and int(m.group(2)) not in ports:
                host = m.group(1)
                ports.append(int(m.group(2)))
        if host:
            return host, tuple(ports), str(s["username"]), str(s["credential"])
    return None


def ipv4_of(host: str) -> str | None:
    try:
        return socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
    except (OSError, IndexError, UnicodeError):
        return None


class Turn:
    def __init__(self, key_id: str, api_token: str, ttl: int, log, opener=urllib.request.urlopen,
                 clock=time.time, threaded: bool = True, resolve=ipv4_of) -> None:
        self.key_id, self.api_token, self.log = key_id, api_token, log
        self.ttl = max(MIN_TTL, min(int(ttl), MAX_TTL))
        self.opener, self.clock, self.threaded, self.resolve = opener, clock, threaded, resolve
        self.lock = threading.Lock()
        self.jobs: queue.Queue = queue.Queue()
        self.cache: dict[int, Credential] = {}
        self.waiting: dict[int, list[Callable[[Credential], None]]] = {}
        self.failed_at = float("-inf")
        self.thread: threading.Thread | None = None

    @classmethod
    def of(cls, cfg: dict, log, **kw) -> "Turn | None":
        mode = str(cfg.get("turn", "off"))
        if mode == "off":
            return None
        if mode != "cloudflare":
            log(f"turn: unknown mode {mode!r} (off or cloudflare): TURN is off")
            return None
        key, token = str(cfg.get("turn_key_id") or "").strip(), str(cfg.get("turn_api_token") or "").strip()
        if not key or not token:
            log("turn: \"cloudflare\" needs turn_key_id and turn_api_token (BH_TURN_KEY_ID, BH_TURN_API_TOKEN): "
                "TURN is off")
            return None
        return cls(key, token, int(cfg.get("turn_ttl", 7200)), log, **kw)

    @property
    def margin(self) -> int:
        return min(1800, self.ttl // 4)

    def offer(self, player_id: int, done: Callable[[Credential], None]) -> None:
        now = self.clock()
        with self.lock:
            hit = self.cache.get(player_id)
            if hit is not None and hit.expires - now <= self.margin:
                hit = None
            if hit is None:
                if now - self.failed_at < RETRY_AFTER:
                    return
                waiting = self.waiting.setdefault(player_id, [])
                waiting.append(done)
                if len(waiting) > 1:
                    return
        if hit is not None:
            done(hit)
            return
        self._queue(("mint", player_id))

    def revoke(self, player_id: int) -> None:
        with self.lock:
            c = self.cache.pop(player_id, None)
        if c is not None:
            self._queue(("revoke", c.username))

    def _queue(self, job: tuple) -> None:
        self.jobs.put(job)
        if not self.threaded:
            return
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self._work, name="turn", daemon=True)
                self.thread.start()

    def _work(self) -> None:
        while True:
            self._run(self.jobs.get())

    def drain(self) -> None:
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                return
            self._run(job)

    def _run(self, job: tuple) -> None:
        try:
            if job[0] == "mint":
                self._mint(job[1])
            elif job[0] == "revoke":
                self._post(f"{API.format(key=self.key_id)}/{job[1]}/revoke", None)
        except Exception as e:
            self.log(f"turn: {job[0]} failed: {e!r}")

    def _post(self, url: str, body: dict | None) -> dict:
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(url, data=data, method="POST", headers={
            "Authorization": f"Bearer {self.api_token}", "Content-Type": "application/json",
            "User-Agent": "bifrost-server"})
        with self.opener(req, timeout=TIMEOUT) as r:
            raw = r.read()
        return json.loads(raw.decode()) if raw else {}

    def _mint(self, player_id: int) -> None:
        try:
            answer = self._post(f"{API.format(key=self.key_id)}/generate-ice-servers",
                                {"ttl": self.ttl, "customIdentifier": f"player-{player_id}"})
            parsed = parse_ice_servers(answer)
            if parsed is None:
                raise ValueError("no TURN over UDP in Cloudflare's answer")
        except Exception as e:
            why = f"Cloudflare answered {e.code}" if isinstance(e, urllib.error.HTTPError) else repr(e)
            with self.lock:
                self.failed_at = self.clock()
                self.waiting.pop(player_id, None)
            self.log(f"turn: no credential for player {player_id} ({why}); matches use the server's relay, "
                     f"next try in {RETRY_AFTER:.0f} s")
            return
        address = self.resolve(parsed[0])
        c = Credential(address or parsed[0], parsed[1], parsed[2], parsed[3], int(self.clock()) + self.ttl)
        with self.lock:
            self.cache[player_id] = c
            callbacks = self.waiting.pop(player_id, [])
        self.log(f"turn: credential for player {player_id} ({parsed[0]} " + (f"= {address}" if address else
                 "with no IPv4 address found") + f", UDP {', '.join(map(str, c.ports))}, {self.ttl} s)")
        for done in callbacks:
            try:
                done(c)
            except Exception as e:
                self.log(f"turn: sending player {player_id} its credential failed: {e!r}")
