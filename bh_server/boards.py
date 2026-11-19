from __future__ import annotations

import json
import time
import urllib.parse

from . import stats as S

PAGE, PAGE_MAX = 25, 50
AROUND = 10
HISTORY_MAX = 300
MATCHES_PAGE, MATCHES_MAX = 10, 20
MOVERS = 10
MECHANICS_RECENT = 20
DAY = 86400
ONLINE_MAX_DAYS = 90
SERVER_DAYS = 30


def _q(n: int) -> str:
    return ",".join("?" * n)


def _like(text: str) -> str:
    return "%" + text.strip().replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"


def _day(t: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(t))


WEAPON_PREFIX = "W"


def _heroes() -> dict:
    from .missions import heroes
    return heroes()


def is_weapon_ladder(ladder: str | None) -> bool:
    return bool(ladder) and ladder[0] == WEAPON_PREFIX and ladder[1:].isalnum()


def weapon_legends(name: str) -> list[int]:
    return sorted(h for h, (names, _) in _heroes().items() if name in names[:2])


def weapon_names() -> list[str]:
    return sorted({n for names, _ in _heroes().values() for n in names[:2] if n.isalnum()})


def _ranked(led, ladder: str, season: int) -> tuple[str, tuple]:
    current = season == led._season_id()
    if is_weapon_ladder(ladder):
        legends = [f"{S.LEGEND_PREFIX}{h}" for h in weapon_legends(ladder[1:])]
        marks = _q(len(legends)) if legends else "NULL"
        if current:
            inner, args = f"SELECT account_id, ladder, rating, peak, games, wins FROM ratings WHERE ladder IN ({marks})", legends
        else:
            inner, args = (f"SELECT account_id, ladder, rating, peak, games, wins FROM season_ratings WHERE season = ? AND "
                           f"ladder IN ({marks})", [season, *legends])
        src = (f"SELECT account_id, rating, peak, games, wins, 1 AS placed, CAST(SUBSTR(ladder, 2) AS INTEGER) AS legend "
               f"FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY account_id ORDER BY rating DESC, games DESC, ladder) AS best "
               f"FROM ({inner} AND games > 0)) WHERE best = 1", tuple(args))
        return (f"SELECT *, ROW_NUMBER() OVER (ORDER BY rating DESC, games DESC, account_id) AS pos FROM ({src[0]})",
                src[1])
    if ladder == S.TEAM_LADDER:
        if current:
            src = ("SELECT a, b, rating, peak, games, wins, (games >= ?) AS placed FROM team_ratings "
                   "WHERE ladder = ? AND games > 0", (S.PLACEMENT_GAMES, S.TEAM_LADDER))
        else:
            src = ("SELECT a, b, rating, peak, games, wins, (rank > 0) AS placed FROM season_team_ratings "
                   "WHERE ladder = ? AND season = ? AND games > 0", (S.TEAM_LADDER, season))
        key = "a, b"
    else:
        if current:
            src = ("SELECT account_id, rating, peak, games, wins, (games >= ?) AS placed FROM ratings "
                   "WHERE ladder = ? AND games > 0", (S.placement_games(ladder), ladder))
        else:
            src = ("SELECT account_id, rating, peak, games, wins, (rank > 0) AS placed FROM season_ratings "
                   "WHERE ladder = ? AND season = ? AND games > 0", (ladder, season))
        key = "account_id"
    return (f"SELECT *, ROW_NUMBER() OVER (ORDER BY placed DESC, rating DESC, games DESC, {key}) AS pos "
            f"FROM ({src[0]})", src[1])


def _where(team: bool, only: set | None, region: int | None, search: str) -> tuple[str, list]:
    cols = ("ranked.a", "ranked.b") if team else ("ranked.account_id",)
    parts, args = [], []
    if only is not None:
        ids = sorted(only)
        parts.append("(" + " OR ".join(f"{c} IN ({_q(len(ids))})" for c in cols) + ")")
        for _ in cols:
            args += ids
    if region is not None:
        def one(c: str) -> str:
            extra = f" OR {c} NOT IN (SELECT account_id FROM profiles)" if region == 0 else ""
            return f"({c} IN (SELECT account_id FROM profiles WHERE region = ?){extra})"
        parts.append("(" + " OR ".join(one(c) for c in cols) + ")")
        args += [region] * len(cols)
    if (search or "").strip():
        like = _like(search)
        parts.append("(" + " OR ".join(
            f"EXISTS (SELECT 1 FROM players pl WHERE pl.account_id = {c} AND pl.name LIKE ? ESCAPE '!') OR "
            f"EXISTS (SELECT 1 FROM accounts ac WHERE ac.id = {c} AND ac.name LIKE ? ESCAPE '!')" for c in cols) + ")")
        args += [like, like] * len(cols)
    return " AND ".join(parts), args


def _names(led, accs: list[int]) -> dict[int, str]:
    if not accs:
        return {}
    rows = led.db.execute(
        "SELECT account_id, name FROM (SELECT rp.account_id AS account_id, COALESCE(pl.name, rp.name) AS name, "
        "ROW_NUMBER() OVER (PARTITION BY rp.account_id ORDER BY r.time DESC, r.id DESC) AS n FROM result_players rp "
        "JOIN results r ON r.id = rp.result_id LEFT JOIN players pl ON pl.id = rp.player_id "
        f"WHERE rp.account_id IN ({_q(len(accs))})) WHERE n = 1", accs).fetchall()
    out = {r["account_id"]: r["name"] for r in rows if r["name"]}
    for acc in accs:
        if acc not in out:
            out[acc] = led.name_of(acc)
    return out


def _top_legends(led, accs: list[int], season: int | None) -> dict[int, int]:
    if not accs:
        return {}
    extra, args = (" AND r.season = ?", [season]) if season else ("", [])
    rows = led.db.execute(
        "SELECT account_id, legend FROM (SELECT rp.account_id AS account_id, rp.legend AS legend, ROW_NUMBER() OVER "
        "(PARTITION BY rp.account_id ORDER BY COUNT(*) DESC, SUM(rp.outcome = 'win') DESC, rp.legend) AS n "
        "FROM result_players rp JOIN results r ON r.id = rp.result_id "
        f"WHERE rp.account_id IN ({_q(len(accs))}) AND r.verdict = ? AND rp.legend IS NOT NULL{extra} "
        "GROUP BY rp.account_id, rp.legend) WHERE n = 1", [*accs, S.COUNTED, *args]).fetchall()
    return {r["account_id"]: r["legend"] for r in rows}


def _avatars(led, accs: list[int]) -> dict[int, int]:
    if not accs:
        return {}
    rows = led.db.execute(f"SELECT account_id, avatar FROM profiles WHERE account_id IN ({_q(len(accs))})", accs)
    return {r["account_id"]: r["avatar"] or 0 for r in rows}


def _ladder_filter(ladder: str | None, alias: str = "rp") -> tuple[str, list]:
    if not ladder or ladder == "all":
        return "", []
    if S.is_legend_ladder(ladder):
        return f" AND r.rated = 1 AND r.format = '1v1' AND {alias}.legend = ?", [int(ladder[1:])]
    if is_weapon_ladder(ladder):
        legends = weapon_legends(ladder[1:])
        return f" AND r.rated = 1 AND r.format = '1v1' AND {alias}.legend IN ({_q(len(legends)) or 'NULL'})", legends
    return " AND r.rated = 1 AND r.format = ?", [ladder]


ZERO = {k: 0 for k in ("matches", "wins", "losses", "draws", "kos", "falls", "self_kos", "damage_dealt", "damage_taken")}


def _totals(led, accs: list[int], ladder: str, season: int) -> dict[int, dict]:
    if not accs:
        return {}
    lw, la = _ladder_filter(ladder)
    rows = led.db.execute(
        f"SELECT rp.account_id AS account_id, {led._TOTALS} FROM result_players rp JOIN results r ON r.id = rp.result_id "
        f"WHERE rp.account_id IN ({_q(len(accs))}) AND r.verdict = ? AND r.season = ?{lw} GROUP BY rp.account_id",
        [*accs, S.COUNTED, season, *la]).fetchall()
    return {r["account_id"]: led._totals(r) for r in rows}


def _snapshot_days(led, ladder: str, season: int, now: float) -> tuple[str | None, str | None]:
    if ladder not in S.LADDERS or season != led._season_id():
        return None, None
    today = _day(now)
    last = led.db.execute("SELECT MAX(day) FROM rank_snapshots WHERE ladder = ? AND season = ? AND day <= ?",
                          (ladder, season, today)).fetchone()[0]
    week = led.db.execute("SELECT MIN(day) FROM rank_snapshots WHERE ladder = ? AND season = ? AND day >= ? AND day <= ?",
                          (ladder, season, _day(now - 7 * DAY), today)).fetchone()[0]
    return last, week


def _snapshot(led, ladder: str, day: str | None, accs: list[int]) -> dict[int, int]:
    if day is None or not accs:
        return {}
    rows = led.db.execute(f"SELECT account_id, rank FROM rank_snapshots WHERE day = ? AND ladder = ? AND "
                          f"account_id IN ({_q(len(accs))})", [day, ladder, *accs])
    return {r["account_id"]: r["rank"] for r in rows}


def _rating(row, ladder: str, team: bool) -> dict:
    need = S.PLACEMENT_GAMES if team else 0 if is_weapon_ladder(ladder) else S.placement_games(ladder)
    rank = row["pos"] if row["placed"] else 0
    games = row["games"] if team else max(row["games"], S.PLACEMENT_GAMES - need)
    return {"rating": row["rating"], "peak": row["peak"], "games": row["games"], "wins": row["wins"], "rank": rank,
            "tier": S.tier_of(row["rating"], games, rank, row["wins"]), "placement": need}


def _dress(led, ladder: str, season: int, rows: list, now: float) -> list[dict]:
    team = ladder == S.TEAM_LADDER
    accs = sorted({x for r in rows for x in ((r["a"], r["b"]) if team else (r["account_id"],))})
    names, legends, avatars = _names(led, accs), _top_legends(led, accs, season), _avatars(led, accs)
    out = []
    if team:
        for r in rows:
            a, b = r["a"], r["b"]
            losses = r["games"] - r["wins"]
            out.append({"id": f"{a}-{b}", "accounts": [a, b], "rank": r["pos"], "place": r["place"],
                        "name": f"{names[a]} & {names[b]}", "rating": _rating(r, ladder, True), "matches": r["games"],
                        "wins": r["wins"], "losses": losses,
                        "win_rate": round(r["wins"] / r["games"], 3) if r["games"] else 0.0,
                        "members": [{"id": x, "name": names[x], "top_legend": legends.get(x), "avatar": avatars.get(x, 0)}
                                    for x in (a, b)]})
        return out
    totals = _totals(led, accs, ladder, season)
    day, week = _snapshot_days(led, ladder, season, now)
    was_day, was_week = _snapshot(led, ladder, day, accs), _snapshot(led, ladder, week, accs)
    for r in rows:
        acc = r["account_id"]
        d = {"id": acc, "rank": r["pos"], "place": r["place"], "name": names[acc], "rating": _rating(r, ladder, False),
             **totals.get(acc, led._totals(ZERO)), "top_legend": legends.get(acc), "avatar": avatars.get(acc, 0)}
        if day is not None:
            d["rank_day"], d["rank_week"] = was_day.get(acc), was_week.get(acc)
        if is_weapon_ladder(ladder):
            d["legend"] = r["legend"]
        out.append(d)
    return out


def _filter(led, filt: str, me: int | None) -> tuple[set | None, int | None, str | None]:
    if filt == "friends":
        if not me:
            return None, None, "Sign in with Discord (Options) to see your friends here."
        return led.friend_accounts(me), None, None
    if filt.startswith("r") and filt[1:].isdigit():
        return None, int(filt[1:]), None
    return None, None, None


def _known(ladder: str) -> bool:
    if is_weapon_ladder(ladder):
        return bool(weapon_legends(ladder[1:]))
    return ladder in S.LADDERS or ladder == S.TEAM_LADDER or S.is_legend_ladder(ladder)


def weapon_ladders(led, season: int | None = None) -> dict:
    with led.lock:
        season = season or led._season_id()
        current = season == led._season_id()
        table, args = (("ratings", []) if current else ("season_ratings", [season]))
        where = "" if current else " AND season = ?"
        out = []
        for name in weapon_names():
            legends = weapon_legends(name)
            if not legends:
                continue
            players = led.db.execute(
                f"SELECT COUNT(DISTINCT account_id) FROM {table} WHERE ladder IN ({_q(len(legends))}) AND games > 0{where}",
                [*(f"{S.LEGEND_PREFIX}{h}" for h in legends), *args]).fetchone()[0]
            out.append({"weapon": name, "ladder": WEAPON_PREFIX + name, "legends": legends, "players": players})
        out.sort(key=lambda w: (-w["players"], w["weapon"]))
        return {"season": season, "weapons": out}


def board(led, ladder: str, season: int | None = None, only: set | None = None, region: int | None = None,
          search: str = "", offset: int = 0, count: int = PAGE, me: int | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with led.lock:
        season = season or led._season_id()
        team = ladder == S.TEAM_LADDER
        ranked, rargs = _ranked(led, ladder, season)
        where, wargs = _where(team, only, region, search)
        head = (f"WITH ranked AS ({ranked}), shown AS (SELECT *, ROW_NUMBER() OVER (ORDER BY pos) AS place FROM ranked"
                + (f" WHERE {where}" if where else "") + ") ")
        args = [*rargs, *wargs]
        total = led.db.execute(head + "SELECT COUNT(*) FROM shown", args).fetchone()[0]
        count, offset = max(1, min(PAGE_MAX, count)), max(0, offset)
        rows = led.db.execute(head + "SELECT * FROM shown ORDER BY place LIMIT ? OFFSET ?", [*args, count, offset]).fetchall()
        out = {"ladder": ladder, "season": season, "total": total, "offset": offset, "count": count,
               "rows": _dress(led, ladder, season, rows, now)}
        if search.strip():
            out["search"] = search.strip()
        if me:
            mine = ("a = ? OR b = ?", [me, me]) if team else ("account_id = ?", [me])
            row = led.db.execute(head + f"SELECT * FROM shown WHERE {mine[0]} ORDER BY place LIMIT 1",
                                 [*args, *mine[1]]).fetchone()
            if row is None:
                row = led.db.execute(f"WITH ranked AS ({ranked}) SELECT *, 0 AS place FROM ranked WHERE {mine[0]} "
                                     "ORDER BY pos LIMIT 1", [*rargs, *mine[1]]).fetchone()
            out["you"] = _dress(led, ladder, season, [row], now)[0] if row is not None else None
        return out


def around(led, ladder: str, season: int | None, only: set | None, region: int | None, me: int,
           span: int = AROUND, now: float | None = None) -> dict:
    first = board(led, ladder, season, only, region, "", 0, 1, me, now)
    you = first.get("you")
    if not you or not you["place"]:
        out = board(led, ladder, season, only, region, "", 0, 2 * span + 1, me, now)
        out["missing"] = True
        return out
    return board(led, ladder, season, only, region, "", max(0, you["place"] - 1 - span), 2 * span + 1, me, now)


def movers(led, ladder: str, limit: int = MOVERS, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with led.lock:
        season = led._season_id()
        _, week = _snapshot_days(led, ladder, season, now)
        if week is None:
            return {"ladder": ladder, "season": season, "since": None, "up": [], "down": []}
        ranked, rargs = _ranked(led, ladder, season)
        lists = {}
        for name, order in (("up", "DESC"), ("down", "ASC")):
            rows = led.db.execute(
                f"WITH ranked AS ({ranked}) SELECT k.*, s.rank AS was, 0 AS place FROM ranked k JOIN rank_snapshots s ON "
                "s.account_id = k.account_id AND s.ladder = ? AND s.day = ? WHERE k.placed AND s.rank != k.pos "
                f"AND (s.rank - k.pos) {'>' if order == 'DESC' else '<'} 0 ORDER BY (s.rank - k.pos) {order}, k.pos LIMIT ?",
                [*rargs, ladder, week, limit]).fetchall()
            dressed = _dress(led, ladder, season, rows, now)
            for d, r in zip(dressed, rows):
                d["was"] = r["was"]
            lists[name] = dressed
        return {"ladder": ladder, "season": season, "since": week, **lists}


def history(led, acc: int, ladder: str, season: int | None = None) -> dict:
    with led.lock:
        season = season or led._season_id()
        if S.is_legend_ladder(ladder):
            cols, where, args = ("rp.legend_before", "rp.legend_after"), "r.format = '1v1' AND rp.legend = ?", [int(ladder[1:])]
        else:
            cols, where, args = ("rp.rating_before", "rp.rating_after"), "r.format = ?", [ladder]
        rows = led.db.execute(
            f"SELECT r.id AS id, r.time AS time, {cols[0]} AS before, {cols[1]} AS after, rp.outcome AS outcome, "
            "(SELECT COALESCE(pl.name, o.name) FROM result_players o LEFT JOIN players pl ON pl.id = o.player_id "
            "WHERE o.result_id = r.id AND o.player_id != rp.player_id AND (r.format = '1v1') LIMIT 1) AS opponent "
            "FROM result_players rp JOIN results r ON r.id = rp.result_id WHERE rp.account_id = ? AND r.season = ? AND "
            f"r.rated = 1 AND {cols[1]} IS NOT NULL AND {where} ORDER BY r.time, r.id",
            [acc, season, *args]).fetchall()
        mark = {"win": "W", "loss": "L"}
        points = [[r["id"], int(r["time"]), r["before"], r["after"], mark.get(r["outcome"], "D"), r["opponent"]]
                  for r in rows[-HISTORY_MAX:]]
        peak = max((r["after"] for r in rows), default=None)
        return {"account": acc, "ladder": ladder, "season": season, "total": len(rows), "peak": peak, "points": points,
                "tiers": [[name, start] for name, start in S.TIER_STARTS],
                "seasons": [{"id": s["id"], "name": s["name"], "started": s["started"], "ended": s["ended"]}
                            for s in led.seasons()]}


def team_history(led, a: int, b: int, season: int | None = None) -> dict:
    a, b = sorted((a, b))
    with led.lock:
        season = season or led._season_id()
        rows = led.db.execute(
            "SELECT r.id AS id, r.time AS time, t.rating_before AS before, t.rating_after AS after, x.outcome AS outcome, "
            "(SELECT GROUP_CONCAT(COALESCE(pl.name, o.name), ' & ') FROM result_players o LEFT JOIN players pl ON "
            "pl.id = o.player_id WHERE o.result_id = r.id AND o.team IS NOT x.team) AS opponent "
            "FROM result_teams t JOIN results r ON r.id = t.result_id JOIN result_players x ON x.result_id = r.id AND "
            "x.account_id = t.a WHERE t.a = ? AND t.b = ? AND r.season = ? ORDER BY r.time, r.id",
            (a, b, season)).fetchall()
        mark = {"win": "W", "loss": "L"}
        points = [[r["id"], int(r["time"]), r["before"], r["after"], mark.get(r["outcome"], "D"), r["opponent"]]
                  for r in rows[-HISTORY_MAX:]]
        peak = max((r["after"] for r in rows), default=None)
        return {"team": f"{a}-{b}", "accounts": [a, b], "ladder": S.TEAM_LADDER, "season": season, "total": len(rows),
                "peak": peak, "points": points, "tiers": [[name, start] for name, start in S.TIER_STARTS],
                "seasons": [{"id": s["id"], "name": s["name"], "started": s["started"], "ended": s["ended"]}
                            for s in led.seasons()]}


def _detail_of(led, rid: int) -> dict[int, dict]:
    rows = led.db.execute("SELECT * FROM result_details WHERE result_id = ?", (rid,)).fetchall()
    return {r["player_id"]: {f: r[f] for f in S.DETAIL_FIELDS} for r in rows}


def matches(led, acc: int, offset: int = 0, count: int = MATCHES_PAGE, mode: str = "", map_: str = "",
            legend: int | None = None, vs: int | None = None, ladder: str = "") -> dict:
    with led.lock:
        where, args = ["rp.account_id = ?"], [acc]
        if mode:
            where.append("r.mode = ?")
            args.append(mode)
        if map_:
            where.append("r.map = ?")
            args.append(map_)
        if legend:
            where.append("rp.legend = ?")
            args.append(legend)
        if vs:
            where.append("EXISTS (SELECT 1 FROM result_players o WHERE o.result_id = r.id AND o.account_id = ? AND "
                         "(rp.team IS NULL OR rp.team = 0 OR rp.team != o.team))")
            args.append(vs)
        lw, la = _ladder_filter(ladder)
        sql = "FROM results r JOIN result_players rp ON rp.result_id = r.id WHERE " + " AND ".join(where) + lw
        args += la
        total = led.db.execute("SELECT COUNT(*) " + sql, args).fetchone()[0]
        count, offset = max(1, min(MATCHES_MAX, count)), max(0, offset)
        heads = led.db.execute("SELECT r.* " + sql + " ORDER BY r.time DESC, r.id DESC LIMIT ? OFFSET ?",
                               [*args, count, offset]).fetchall()
        return {"account": acc, "total": total, "offset": offset, "count": count,
                "matches": [led._match(h) for h in heads]}


def _sees_mechanics(acc: int | None, me: int | None, public: bool) -> bool:
    return public or (me is not None and acc is not None and acc == me)


def match(led, rid: int, me: int | None = None, public: bool = False) -> dict | None:
    with led.lock:
        head = led.db.execute("SELECT * FROM results WHERE id = ?", (rid,)).fetchone()
        if head is None:
            return None
        out = led._match(head)
        detail = _detail_of(led, rid)
        mech = {r["player_id"]: (r["account_id"], r["data"]) for r in led.db.execute(
            "SELECT player_id, account_id, data FROM result_mechanics WHERE result_id = ?", (rid,))}
        for p in out["players"]:
            if p["player"] in detail:
                p["detail"] = detail[p["player"]]
            acc, data = mech.get(p["player"], (None, None))
            if data is not None and _sees_mechanics(acc, me, public):
                p["mechanics"] = json.loads(data)
        out["mechanics_public"] = public
        out["teams"] = [dict(r) for r in led.db.execute("SELECT a, b, rating_before, rating_after FROM result_teams "
                                                         "WHERE result_id = ?", (rid,))]
        out["format"] = head["format"]
        out["season"] = head["season"]
        return out


def h2h(led, me: int, them: int, offset: int = 0, count: int = MATCHES_PAGE) -> dict:
    with led.lock:
        sql = ("FROM results r JOIN result_players x ON x.result_id = r.id AND x.account_id = ? JOIN result_players y ON "
               "y.result_id = r.id AND y.account_id = ? AND (x.team IS NULL OR x.team = 0 OR x.team != y.team) "
               "WHERE r.verdict = ?")
        args = [me, them, S.COUNTED]
        count, offset = max(1, min(MATCHES_MAX, count)), max(0, offset)
        heads = led.db.execute("SELECT r.* " + sql + " ORDER BY r.time DESC, r.id DESC LIMIT ? OFFSET ?",
                               [*args, count, offset]).fetchall()
        legends = led.db.execute(
            "SELECT x.legend AS mine, y.legend AS theirs, COUNT(*) AS n, SUM(x.outcome = 'win') AS w " + sql +
            " GROUP BY x.legend, y.legend ORDER BY n DESC LIMIT 20", args).fetchall()
        names = _names(led, sorted({me, them}))
        return {"a": me, "b": them, "names": {str(k): v for k, v in names.items()}, **led.head_to_head(me, them),
                "offset": offset, "count": count, "results": [led._match(h) for h in heads],
                "legends": [{"mine": r["mine"], "theirs": r["theirs"], "matches": r["n"], "wins": r["w"] or 0}
                            for r in legends]}


def weapons(led, acc: int, season: int | None = None, heroes: dict | None = None) -> dict:
    if heroes is None:
        from .missions import heroes as load
        heroes = load()
    with led.lock:
        extra, args = (" AND r.season = ?", [season]) if season else ("", [])
        rows = led.db.execute(
            "SELECT rp.legend AS legend, COUNT(*) AS games, SUM(rp.outcome = 'win') AS wins, COUNT(d.result_id) AS known, "
            "SUM(d.ko_weapon1) AS k1, SUM(d.ko_weapon2) AS k2, SUM(d.damage_weapon1) AS d1, SUM(d.damage_weapon2) AS d2, "
            "SUM(d.held_weapon1) AS h1, SUM(d.held_weapon2) AS h2, SUM(COALESCE(r.duration, 0)) AS played "
            "FROM result_players rp JOIN results r ON r.id = rp.result_id LEFT JOIN result_details d ON "
            "d.result_id = rp.result_id AND d.player_id = rp.player_id WHERE rp.account_id = ? AND r.verdict = ? "
            f"AND rp.legend IS NOT NULL{extra} GROUP BY rp.legend", [acc, S.COUNTED, *args]).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        names = heroes.get(r["legend"], (("", ""), None))[0]
        for slot, name in enumerate(names[:2], 1):
            if not name:
                continue
            w = out.setdefault(name, {"weapon": name, "games": 0, "wins": 0, "known": 0, "kos": 0, "damage": 0,
                                      "held": 0, "played": 0})
            w["games"] += r["games"]
            w["wins"] += r["wins"] or 0
            w["known"] += r["known"] or 0
            w["kos"] += r[f"k{slot}"] or 0
            w["damage"] += r[f"d{slot}"] or 0
            w["held"] += r[f"h{slot}"] or 0
            w["played"] += r["played"] or 0
    for w in out.values():
        w["win_rate"] = round(w["wins"] / w["games"], 3) if w["games"] else 0.0
    return {"account": acc, "season": season,
            "weapons": sorted(out.values(), key=lambda w: (-w["games"], -w["kos"], w["weapon"]))}


def mechanics(led, acc: int, season: int | None = None, recent: int = MECHANICS_RECENT) -> dict:
    with led.lock:
        extra, args = (" AND r.season = ?", [season]) if season else ("", [])
        rows = led.db.execute(
            "SELECT m.result_id AS id, m.data AS data, r.time AS time, r.duration AS duration, r.mode AS mode, "
            "rp.legend AS legend, rp.outcome AS outcome FROM result_mechanics m JOIN results r ON r.id = m.result_id "
            "JOIN result_players rp ON rp.result_id = m.result_id AND rp.player_id = m.player_id "
            f"WHERE m.account_id = ? AND r.verdict = ?{extra} ORDER BY r.time DESC, r.id DESC",
            [acc, S.COUNTED, *args]).fetchall()
    total = {f: 0 for f in S.MECHANIC_FIELDS if f not in ("best_combo_hits", "best_combo_damage")}
    seen = {f: 0 for f in total}
    played = 0
    best = None
    listed = []
    for r in rows:
        data = json.loads(r["data"])
        played += int(r["duration"] or 0)
        for f in total:
            if f in data:
                total[f] += data[f]
                seen[f] += 1
        hits = data.get("best_combo_hits", 0)
        if hits and (best is None or (hits, data.get("best_combo_damage", 0)) > (best["hits"], best["damage"])):
            best = {"hits": hits, "damage": data.get("best_combo_damage", 0), "result": r["id"], "time": r["time"],
                    "legend": r["legend"]}
        if len(listed) < recent:
            listed.append({"result": r["id"], "time": r["time"], "duration": r["duration"], "mode": r["mode"],
                           "legend": r["legend"], "outcome": r["outcome"],
                           **{f: data[f] for f in S.MECHANIC_FIELDS if f in data}})
    minutes = played / 60000.0
    return {"account": acc, "season": season, "matches": len(rows), "played": played,
            "per_match": {f: round(total[f] / seen[f], 2) for f in total if seen[f]},
            "per_minute": {f: round(total[f] / minutes, 2) for f in ("dodges", "dashes", "jumps", "combos", "clashes")
                           if seen[f] and minutes > 0},
            "air_share": round(total["air_ms"] / max(1, total["air_ms"] + total["ground_ms"] + total["wall_ms"]), 3)
            if seen["air_ms"] else None,
            "best_combo": best, "recent": listed}


def online(led, days: int, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    days = max(1, min(ONLINE_MAX_DAYS, days))
    step = S.ONLINE_STEP if days <= 2 else 3600 if days <= 14 else 4 * 3600
    with led.lock:
        rows = led.db.execute(
            "SELECT (time / ?) * ? AS t, MAX(online) AS online, MAX(playing) AS playing, MAX(ranked) AS ranked "
            "FROM online_samples WHERE time >= ? GROUP BY t ORDER BY t", (step, step, int(now - days * DAY))).fetchall()
        peak = led.db.execute("SELECT time, online FROM online_samples ORDER BY online DESC, time LIMIT 1").fetchone()
        last = led.db.execute("SELECT time, online, playing, ranked FROM online_samples ORDER BY time DESC LIMIT 1").fetchone()
    return {"days": days, "step": step, "points": [[r["t"], r["online"], r["playing"], r["ranked"]] for r in rows],
            "peak": dict(peak) if peak else None, "latest": dict(last) if last else None}


def server_stats(led, season: int | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    with led.lock:
        season = season or led._season_id()
        base = "FROM results r WHERE r.verdict = ? AND r.season = ?"
        args = [S.COUNTED, season]
        head = led.db.execute(f"SELECT COUNT(*) AS matches, SUM(r.rated) AS rated, AVG(r.duration) AS duration {base}",
                              args).fetchone()
        players = led.db.execute("SELECT COUNT(DISTINCT rp.account_id) FROM result_players rp JOIN results r ON "
                                 "r.id = rp.result_id WHERE r.verdict = ? AND r.season = ? AND rp.account_id IS NOT NULL",
                                 args).fetchone()[0]
        legends = led.db.execute(
            "SELECT rp.legend AS legend, COUNT(*) AS picks, SUM(rp.outcome = 'win') AS wins, "
            "SUM(r.rated = 1 AND r.format = '1v1') AS ranked, SUM(r.rated = 1 AND r.format = '1v1' AND rp.outcome = 'win') "
            "AS ranked_wins, COUNT(DISTINCT rp.account_id) AS players FROM result_players rp JOIN results r ON "
            "r.id = rp.result_id WHERE r.verdict = ? AND r.season = ? AND rp.legend IS NOT NULL AND rp.legend > 0 "
            "GROUP BY rp.legend ORDER BY picks DESC, rp.legend", args).fetchall()
        maps = led.db.execute(f"SELECT r.map AS map, COUNT(*) AS n, AVG(r.duration) AS duration {base} AND r.map IS NOT "
                              "NULL GROUP BY r.map ORDER BY n DESC, r.map LIMIT 40", args).fetchall()
        modes = led.db.execute(f"SELECT r.kind AS kind, r.mode AS mode, COUNT(*) AS n, AVG(r.duration) AS duration {base} "
                               "GROUP BY r.kind, r.mode ORDER BY n DESC LIMIT 30", args).fetchall()
        days = led.db.execute(
            "SELECT date(r.time, 'unixepoch') AS day, COUNT(DISTINCT r.id) AS n, COUNT(DISTINCT rp.account_id) AS players "
            "FROM results r "
            "JOIN result_players rp ON rp.result_id = r.id WHERE r.verdict = ? AND r.season = ? AND r.time >= ? "
            "GROUP BY day ORDER BY day", [*args, now - SERVER_DAYS * DAY]).fetchall()
        start = next((s for s in led.seasons() if s["id"] == season), None)
        span = (start["started"], start["ended"] or now) if start else (0, now)
        peak = led.db.execute("SELECT time, online FROM online_samples WHERE time >= ? AND time <= ? ORDER BY online DESC, "
                              "time LIMIT 1", (int(span[0]), int(span[1]))).fetchone()
        names = {r["legend"]: led.legend_name(r["legend"]) for r in legends}
    rate = lambda w, n: round((w or 0) / n, 3) if n else 0.0
    return {"season": season, "matches": head["matches"] or 0, "rated": head["rated"] or 0,
            "average_duration": round(head["duration"] or 0), "players": players,
            "legends": [{"legend": r["legend"], "name": names[r["legend"]], "picks": r["picks"], "players": r["players"],
                         "win_rate": rate(r["wins"], r["picks"]), "ranked": r["ranked"] or 0,
                         "ranked_win_rate": rate(r["ranked_wins"], r["ranked"] or 0)} for r in legends],
            "maps": [{"map": r["map"], "matches": r["n"], "average_duration": round(r["duration"] or 0)} for r in maps],
            "modes": [{"kind": r["kind"], "mode": r["mode"], "matches": r["n"], "average_duration": round(r["duration"] or 0)}
                      for r in modes],
            "days": [{"day": r["day"], "matches": r["n"], "players": r["players"]} for r in days],
            "peak_online": dict(peak) if peak else None}


def _int(text: str, default: int = 0) -> int | None:
    if text == "":
        return default
    return int(text) if text.isdigit() else None


def answer(led, query: str, me: int | None, cfg: dict | None = None) -> dict | None:
    parts = query.split(":")
    kind = parts[0]
    if kind in ("board", "around") and len(parts) >= 2:
        ladder = parts[1]
        if not _known(ladder):
            return {"error": "unknown ladder"}
        season = _int(parts[2]) if len(parts) > 2 else 0
        filt = parts[3] if len(parts) > 3 and parts[3] else "all"
        if season is None or (filt not in ("all", "friends") and not (filt[:1] == "r" and filt[1:].isdigit())):
            return {"error": "unknown query"}
        only, region, refusal = _filter(led, filt, me)
        if refusal:
            return {"ladder": ladder, "season": season or led.season_id(), "filter": filt, "rows": [], "total": 0,
                    "error": refusal}
        if kind == "around":
            if not me:
                return {"error": "not signed in"}
            out = around(led, ladder, season or None, only, region, me)
        else:
            offset = _int(parts[4]) if len(parts) > 4 else 0
            count = _int(parts[5], PAGE) if len(parts) > 5 else PAGE
            if offset is None or count is None:
                return {"error": "unknown query"}
            search = urllib.parse.unquote(":".join(parts[6:]))[:S.MAX_TEXT] if len(parts) > 6 else ""
            out = board(led, ladder, season or None, only, region, search, offset, count, me)
        out["filter"] = filt
        return out
    if kind == "weaponladders" and len(parts) in (1, 2) and all(x.isdigit() for x in parts[1:]):
        return weapon_ladders(led, int(parts[1]) or None if len(parts) > 1 else None)
    if kind == "movers" and len(parts) == 2 and parts[1] in S.LADDERS:
        return movers(led, parts[1])
    if kind == "history" and len(parts) in (3, 4) and parts[2] == S.TEAM_LADDER:
        ab = parts[1].split("-")
        season = _int(parts[3]) if len(parts) > 3 else 0
        if len(ab) != 2 or not all(x.isdigit() for x in ab) or season is None:
            return {"error": "unknown query"}
        return team_history(led, int(ab[0]), int(ab[1]), season or None)
    if kind == "history" and len(parts) in (3, 4) and parts[1].isdigit():
        ladder = parts[2]
        if ladder not in S.LADDERS and not S.is_legend_ladder(ladder):
            return {"error": "unknown ladder"}
        season = _int(parts[3]) if len(parts) > 3 else 0
        if season is None:
            return {"error": "unknown query"}
        return history(led, int(parts[1]), ladder, season or None)
    if kind == "matches" and len(parts) in (2, 3, 4, 5) and parts[1].isdigit():
        offset = _int(parts[2]) if len(parts) > 2 else 0
        count = _int(parts[3], MATCHES_PAGE) if len(parts) > 3 else MATCHES_PAGE
        if offset is None or count is None:
            return {"error": "unknown query"}
        f = urllib.parse.parse_qs(parts[4]) if len(parts) > 4 else {}
        one = lambda k: (f.get(k) or [""])[0][:S.MAX_TEXT]
        legend, vs = one("legend"), one("vs")
        if (legend and not legend.isdigit()) or (vs and not vs.isdigit()):
            return {"error": "unknown query"}
        ladder = one("ladder")
        if ladder and not (ladder in S.LADDERS or S.is_legend_ladder(ladder)):
            return {"error": "unknown ladder"}
        return matches(led, int(parts[1]), offset, count, one("mode"), one("map"), int(legend) if legend else None,
                       int(vs) if vs else None, ladder)
    public = bool((cfg or {}).get("public_mechanics", False))
    if kind == "match" and len(parts) == 2 and parts[1].isdigit():
        return match(led, int(parts[1]), me, public) or {"error": "no such match"}
    if kind == "mechanics" and len(parts) in (2, 3) and all(x.isdigit() for x in parts[1:]):
        acc = int(parts[1])
        if not _sees_mechanics(acc, me, public):
            return {"account": acc, "error": "only the player can see these numbers on this server"}
        return mechanics(led, acc, int(parts[2]) or None if len(parts) > 2 else None)
    if kind == "h2h" and len(parts) in (3, 4, 5) and parts[1].isdigit() and parts[2].isdigit():
        offset = _int(parts[3]) if len(parts) > 3 else 0
        count = _int(parts[4], MATCHES_PAGE) if len(parts) > 4 else MATCHES_PAGE
        if offset is None or count is None:
            return {"error": "unknown query"}
        return h2h(led, int(parts[1]), int(parts[2]), offset, count)
    if kind == "weapons" and len(parts) in (2, 3) and all(x.isdigit() for x in parts[1:]):
        return weapons(led, int(parts[1]), int(parts[2]) or None if len(parts) > 2 else None)
    if kind == "server" and len(parts) in (2, 3) and parts[1] == "stats" and all(x.isdigit() for x in parts[2:]):
        return server_stats(led, int(parts[2]) or None if len(parts) > 2 else None)
    if kind == "online" and len(parts) == 2 and parts[1].isdigit():
        return online(led, int(parts[1]))
    return None
