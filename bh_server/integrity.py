from __future__ import annotations

import html
import time

REPEAT = 15
SERIES_MIN, SERIES_SHARE = 8, 0.9
SHORTEST_MS = 20_000
MAX_DPS = 50.0


def flags(db, days: float = 1.0, cfg: dict | None = None, now: float | None = None) -> list[dict]:
    cfg = cfg or {}
    since = (now or time.time()) - days * 86400
    repeat = int(cfg.get("flag_repeat", REPEAT))
    out: list[dict] = []
    rows = db.execute(
        "SELECT a.account_id AS x, b.account_id AS y, COUNT(*) AS n, SUM(a.outcome = 'win') AS xw, "
        "SUM(b.outcome = 'win') AS yw, MAX(COALESCE(pa.name, a.name)) AS xn, MAX(COALESCE(pb.name, b.name)) AS yn "
        "FROM results r JOIN result_players a ON a.result_id = r.id JOIN result_players b ON b.result_id = r.id "
        "LEFT JOIN accounts pa ON pa.id = a.account_id LEFT JOIN accounts pb ON pb.id = b.account_id "
        "WHERE r.rated = 1 AND r.time >= ? AND a.account_id < b.account_id AND COALESCE(a.team, -1) != COALESCE(b.team, -2) "
        "GROUP BY a.account_id, b.account_id", (since,)).fetchall()
    for r in rows:
        n, xw, yw = int(r["n"]), int(r["xw"] or 0), int(r["yw"] or 0)
        who = f"{r['xn']} (#{r['x']}) vs {r['yn']} (#{r['y']})"
        if n >= SERIES_MIN and max(xw, yw) / n >= SERIES_SHARE:
            winner = r["xn"] if xw >= yw else r["yn"]
            out.append({"kind": "one-sided series", "who": who, "detail": f"{max(xw, yw)} of {n} won by {winner}"})
        elif n >= repeat:
            out.append({"kind": "repeat pair", "who": who, "detail": f"{n} rated matches ({xw}-{yw})"})
    day_from = time.strftime("%Y-%m-%d", time.gmtime(since))
    cap = int(cfg.get("daily_elo_cap", 200))
    if cap > 0:
        for r in db.execute(
                "SELECT l.day, l.ladder, l.gained, a.name AS an, l.account_id AS a, o.name AS on_, l.opponent_id AS o "
                "FROM elo_ledger l LEFT JOIN accounts a ON a.id = l.account_id LEFT JOIN accounts o ON o.id = l.opponent_id "
                "WHERE l.day >= ? AND l.gained >= ? ORDER BY l.day", (day_from, cap)).fetchall():
            out.append({"kind": "daily cap", "who": f"{r['an']} (#{r['a']}) from {r['on_']} (#{r['o']})",
                        "detail": f"{r['day']} {r['ladder']}: reached the {cap} Elo cap"})
    short: dict[int, list] = {}
    for r in db.execute(
            "SELECT r.id, r.duration, p.account_id AS a, COALESCE(acc.name, p.name) AS name, p.kos, p.damage_dealt "
            "FROM results r JOIN result_players p ON p.result_id = r.id LEFT JOIN accounts acc ON acc.id = p.account_id "
            "WHERE r.rated = 1 AND r.time >= ? ORDER BY r.id", (since,)).fetchall():
        dur, kos, dmg = r["duration"] or 0, r["kos"] or 0, r["damage_dealt"] or 0
        if 0 < dur < SHORTEST_MS:
            short.setdefault(r["id"], [dur]).append(f"{r['name']} (#{r['a']})")
        why = []
        if kos > 0 and dmg == 0:
            why.append(f"{kos} KOs with no damage dealt")
        if dur > 0 and dmg / (dur / 1000) > MAX_DPS:
            why.append(f"{dmg / (dur / 1000):.0f} damage a second")
        if why:
            out.append({"kind": "impossible stats", "who": f"{r['name']} (#{r['a']})",
                        "detail": f"match {r['id']}: " + ", ".join(why)})
    for rid, (dur, *names) in short.items():
        out.append({"kind": "impossible stats", "who": " vs ".join(names),
                    "detail": f"match {rid}: {dur / 1000:.1f} s long"})
    order = {"one-sided series": 0, "daily cap": 1, "impossible stats": 2, "repeat pair": 3}
    out.sort(key=lambda f: order.get(f["kind"], 9))
    return out


def text(found: list[dict], days: float) -> str:
    if not found:
        return f"no integrity flags in the last {days:g} day(s)"
    return f"{len(found)} flag(s) in the last {days:g} day(s):" + "".join(
        f"\n  [{f['kind']}] {f['who']}: {f['detail']}" for f in found)


def page_html(found: list[dict], days: float) -> str:
    links = " · ".join(f"<a href='/flags?days={d}'>{d} day{'s' if d > 1 else ''}</a>" for d in (1, 7, 30))
    if not found:
        return f"<p>{links}</p><p>No flags in the last {days:g} day(s).</p>"
    rows = "".join(f"<tr><td>{html.escape(f['kind'])}</td><td>{html.escape(f['who'])}</td>"
                   f"<td>{html.escape(f['detail'])}</td></tr>" for f in found)
    return (f"<p>{links}</p><p>Patterns in the rated matches worth a look (nothing is changed automatically; the daily "
            f"cap already limits what one opponent can give).</p><table><tr><th>Kind</th><th>Who</th><th>What</th></tr>"
            f"{rows}</table>")
