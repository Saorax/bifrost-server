from __future__ import annotations

import threading
from dataclasses import dataclass, field

VOTES, STRAIGHT, SNAKE, OPEN, ALL_BUT_X, RANDOM, LEADER = range(7)
HANDLED_MODES = {VOTES, STRAIGHT, SNAKE, OPEN, ALL_BUT_X, LEADER}
VOTE_TIMEOUT = 30.0
STRIKE_TIMEOUT = 180.0
MAX_CANDIDATES = 200


@dataclass
class Pick:
    mode: int
    candidates: list[int]
    random_tile: int = 0
    votes: dict = field(default_factory=dict)
    struck: list = field(default_factory=list)
    done: set = field(default_factory=set)
    finished: bool = False
    timer: threading.Timer | None = None

    def left(self) -> list[int]:
        return [c for c in self.candidates if c not in self.struck]


def wanted(mode: int, candidates: list[int]) -> bool:
    return mode in HANDLED_MODES and len(candidates) > 1


def vote(pick: Pick, player: int, level: int, all_but: bool, members: list[int], host: int) -> bool:
    if pick.finished or player not in members:
        return False
    if pick.mode in (VOTES, LEADER):
        if player in pick.votes:
            return False
        pick.votes[player] = level
        if pick.mode == LEADER and player == host:
            pick.finished = True
        elif pick.mode == VOTES and all(m in pick.votes for m in members):
            pick.finished = True
        return True
    if level not in pick.left():
        return False
    if all_but:
        if pick.mode != OPEN and not (pick.mode == ALL_BUT_X and len(pick.left()) <= 3):
            return False
        pick.struck.extend(c for c in pick.left() if c != level)
    else:
        if len(pick.left()) <= 1:
            return False
        pick.struck.append(level)
    if len(pick.left()) <= 1:
        pick.finished = True
    return True


def done(pick: Pick, player: int, members: list[int]) -> None:
    if pick.finished or player not in members or pick.mode not in (STRAIGHT, SNAKE, OPEN):
        return
    pick.done.add(player)
    if all(m in pick.done for m in members):
        pick.finished = True


def winner(pick: Pick, rng, host: int) -> int:
    pool = pick.candidates
    if pick.mode == VOTES and pick.votes:
        pool = list(pick.votes.values())
    elif pick.mode == LEADER and host in pick.votes:
        pool = [pick.votes[host]]
    elif pick.mode in (STRAIGHT, SNAKE, OPEN, ALL_BUT_X):
        pool = pick.left() or pick.candidates
    level = rng.choice(pool)
    if level not in pick.candidates:
        level = rng.choice(pick.candidates)
    return level
