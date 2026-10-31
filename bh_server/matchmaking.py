from __future__ import annotations

from dataclasses import dataclass, field
from itertools import combinations

START_RATING = 180
WINDOW_START = 100
WINDOW_GROWTH = 10
WINDOW_MAX = 1000
FFA_FILL_WAIT = 20.0
MAX_PARTIES = 8
PING_TIE_ELO = 25
UNKNOWN_PING = 150.0


@dataclass
class Party:
    key: object
    size: int
    rating: float
    since: float
    mods: int | None = None
    region: int = 0
    ids: list = field(default_factory=list)
    ping: float | None = None
    blocks: frozenset = frozenset()


def window(party: Party, now: float) -> float:
    return min(WINDOW_MAX, WINDOW_START + WINDOW_GROWTH * max(0.0, now - party.since))


def split_teams(sizes: list[int], team_size: int) -> list[int] | None:
    n = len(sizes)
    for mask in range(1 << n):
        if sum(s for i, s in enumerate(sizes) if mask >> i & 1) == team_size:
            return [1 if mask >> i & 1 else 2 for i in range(n)]
    return None


def fits(group: list[Party], now: float) -> bool:
    mean = sum(p.rating * p.size for p in group) / sum(p.size for p in group)
    return all(abs(p.rating - mean) <= window(p, now) for p in group)


def spread(group: list[Party]) -> float:
    return max(p.rating for p in group) - min(p.rating for p in group)


def match_ping(group: list[Party]) -> float:
    pings = sorted((p.ping if p.ping is not None else UNKNOWN_PING for p in group), reverse=True)
    return sum(pings[:2])


def find_match(parties: list[Party], players: int, teams: bool, now: float) -> tuple[list[Party], list[int]] | None:
    waiting = sorted(parties, key=lambda p: p.since)[:MAX_PARTIES]
    for anchor in waiting:
        others = [p for p in waiting if p is not anchor and p.region == anchor.region]
        found = []
        for k in range(1, len(others) + 1):
            for rest in combinations(others, k):
                group = [anchor, *rest]
                total = sum(p.size for p in group)
                if total != players:
                    short_ffa = (not teams and players > 2 and total >= 2 and total < players
                                 and all(now - p.since >= FFA_FILL_WAIT for p in group))
                    if not short_ffa:
                        continue
                if len({p.mods for p in group if p.mods is not None}) > 1:
                    continue
                if any(set(a.ids) & b.blocks for a in group for b in group if a is not b):
                    continue
                if teams:
                    sides = split_teams([p.size for p in group], players // 2)
                    if sides is None:
                        continue
                else:
                    sides = [0] * len(group)
                if not fits(group, now):
                    continue
                found.append((total, spread(group), match_ping(group), group, sides))
        if found:
            most = max(f[0] for f in found)
            found = [f for f in found if f[0] == most]
            closest = min(f[1] for f in found)
            near = [f for f in found if f[1] <= closest + PING_TIE_ELO]
            best = min(near, key=lambda f: (f[2], f[1]))
            return best[3], best[4]
    return None
