from __future__ import annotations

import secrets
import threading
import time

TTL = 600


class GameLogins:
    def __init__(self, clock=time.monotonic) -> None:
        self._pending: dict[str, tuple[object, float]] = {}
        self._codes: dict[str, str] = {}
        self._held: dict[str, list] = {}
        self._lock = threading.Lock()
        self._clock = clock

    def _prune(self) -> None:
        now = self._clock()
        for state in [s for s, (_, at) in self._pending.items() if now - at > TTL]:
            del self._pending[state]
            self._codes.pop(state, None)
        for cid in [c for c, h in self._held.items() if now - h[4] > TTL]:
            del self._held[cid]

    def start(self, session) -> str:
        state = "game-" + secrets.token_urlsafe(16)
        with self._lock:
            self._prune()
            self._pending[state] = (session, self._clock())
            self._codes[state] = f"{secrets.randbelow(10000):04d}"
        return state

    def code_for(self, state: str) -> str:
        with self._lock:
            return self._codes.get(state, "")

    def hold(self, session, state: str, user: dict) -> str:
        cid = secrets.token_urlsafe(18)
        with self._lock:
            self._prune()
            self._held[cid] = [session, self._codes.pop(state, ""), user, 0, self._clock()]
        return cid

    def confirm(self, cid: str, typed: str):
        with self._lock:
            self._prune()
            held = self._held.get(cid or "")
            if held is None:
                return None
            if held[1] and secrets.compare_digest(held[1], "".join(ch for ch in str(typed) if ch.isdigit())[:8]):
                del self._held[cid]
                return held[0], held[2]
            held[3] += 1
            if held[3] >= 3:
                del self._held[cid]
                return None
            return "wrong"

    def take(self, state: str | None):
        if not state:
            return None
        with self._lock:
            self._prune()
            entry = self._pending.pop(state, None)
        return entry[0] if entry else None

    def forget(self, session) -> None:
        with self._lock:
            for state in [s for s, (who, _) in self._pending.items() if who is session]:
                del self._pending[state]
