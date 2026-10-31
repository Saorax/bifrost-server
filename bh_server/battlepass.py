from __future__ import annotations

import json
import re
import time
from pathlib import Path

from . import missions, stats
from .bits import BitReader, BitWriter
from .frame import Packet

MISSIONS, PROGRESS, LADDER, REWARDS, CHOOSE = 2803, 2804, 2805, 2914, 186
STARS_PER_TIER = 12
ACTIVE, DONE = 0, 2
MATCH_STARS, WIN_STARS = 2, 1
SKIPPED_REWARDS = ("MammothCoins", "RandomColor", "Mission")
MAX_SMALL = 0xFFFF

SCHEMA = """
CREATE TABLE IF NOT EXISTS battle_pass (
  player_id INTEGER NOT NULL, season INTEGER NOT NULL, stars INTEGER NOT NULL DEFAULT 0,
  path INTEGER NOT NULL DEFAULT 0, tiers TEXT NOT NULL DEFAULT '{}', quests TEXT NOT NULL DEFAULT '{}',
  PRIMARY KEY (player_id, season));
CREATE TABLE IF NOT EXISTS battle_pass_missions (
  player_id INTEGER NOT NULL, mission INTEGER NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
  state INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (player_id, mission));
"""


def daily_stars(data: Path = missions.GAME_DATA) -> dict[int, int]:
    try:
        text = (data / "MissionTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for block in re.findall(r"<MissionType[ >].*?</MissionType>", text, re.S):
        mid, stars = re.search(r"<MissionID>(\d+)<", block), re.search(r"<RewardBattleStars>(\d+)<", block)
        if mid and stars:
            out[int(mid.group(1))] = int(stars.group(1))
    return out


class Season:

    def __init__(self, season: int, entitlement: int, max_tiers: dict[int, int], rewards: list[dict],
                 quests: dict[int, dict], upgrades: dict[str, dict] | None = None) -> None:
        self.id, self.entitlement, self.max_tiers, self.rewards, self.quests = season, entitlement, max_tiers, rewards, quests
        self.upgrades = upgrades or {}
        self.max_path = max(max_tiers) if max_tiers else 0
        self.weeks = max((q["week"] for q in quests.values()), default=0)

    def rewards_at(self, path: int, tier: int) -> list[dict]:
        return [r for r in self.rewards if r["path"] == path and r["tier"] == tier]

    def boost(self, tiers: dict[int, int]) -> float:
        pct = sum(r["amount"] for r in self.rewards
                  if r["type"] == "BattlePointsMult" and 0 < r["tier"] <= tiers.get(r["path"], 0))
        return 1.0 + pct / 100.0


def load_upgrades(data: Path = missions.GAME_DATA) -> dict[str, dict]:
    try:
        text = (data / "MissionTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    out = {}
    for name, body in re.findall(r'<MissionType MissionName="([^"]+)">(.*?)</MissionType>', text, re.S):
        tags = dict(re.findall(r"<(\w+)>([^<]*)</\1>", body))
        if tags.get("Category") != "OneOff":
            continue
        try:
            mid, need = int(tags.get("MissionID", "")), int(tags.get("SuccessCount") or 0)
        except ValueError:
            continue
        events = tuple(v.strip() for k, v in sorted(tags.items()) if re.fullmatch(r"Event\d+", k) and v.strip()
                       and not v.startswith("-"))
        out[name] = {"id": mid, "need": max(1, need), "events": events, "next": tags.get("RewardMission", "").strip()}
    return out


def load_season(data: Path = missions.GAME_DATA) -> Season | None:
    try:
        quests_xml = (data / "QuestTypes.xml").read_text(encoding="utf-8", errors="replace")
        rewards_xml = (data / "BattlePassRewardTypes.xml").read_text(encoding="utf-8", errors="replace")
        ent_xml = (data / "EntitlementTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'<QuestTypes BattlePassID="(\d+)"', quests_xml)
    if not m:
        return None
    season = int(m.group(1))
    e = re.search(rf'EntitlementName="BattlePassSeason{season}"><EntitlementID>(\d+)<', ent_xml)
    if not e:
        return None

    def col(body: str, name: str) -> str:
        f = re.search(rf"<{name}>([^<]*)</{name}>", body)
        return f.group(1).strip() if f else ""

    rewards, max_tiers = [], {}
    for r in re.finditer(rf'<BattlePassRewardType BattlePassID="{season}" Path="(\d+)" Tier="(\d+)"[^>]*>(.*?)'
                         r"</BattlePassRewardType>", rewards_xml, re.S):
        path, tier, body = int(r.group(1)), int(r.group(2)), r.group(3)
        try:
            rid, amount = int(col(body, "RewardID")), int(col(body, "Amount") or 0)
        except ValueError:
            continue
        rewards.append({"id": rid, "path": path, "tier": tier, "type": col(body, "Type"), "amount": amount,
                        "item": col(body, "Item"), "completionist": col(body, "IsCompletionistBonus").lower() == "true"})
        max_tiers[path] = max(max_tiers.get(path, 0), tier)
    quests = {}
    for q in re.finditer(r'<QuestType Week="(\d+)">(.*?)</QuestType>', quests_xml, re.S):
        week, body = int(q.group(1)), q.group(2)
        try:
            qid, stars, need = int(col(body, "QuestID")), int(col(body, "RewardBattleStars") or 0), int(col(body, "SuccessCount") or 0)
        except ValueError:
            continue
        if week == 0 or qid == 0 or need <= 0:
            continue
        events = tuple(v.strip() for v in re.findall(r"<Event\d+>([^<]*)</Event\d+>", body) if v.strip() and not v.startswith("-"))
        quests[qid] = {"week": week, "stars": stars, "need": need, "events": events,
                       "as_bool": col(body, "CompleteAsBool").lower() == "true"}
    if not max_tiers:
        return None
    return Season(season, int(e.group(1)), max_tiers, rewards, quests, load_upgrades(data))


def count(event: str, facts: dict) -> int:
    if event.startswith("!"):
        return 0
    kind, _, rest = event.partition(".")
    hero, detail = facts.get("hero"), facts.get("detail") or {}
    if kind == "heroWeightedPlay":
        return (2 if facts.get("won") else 1) if hero is not None and rest in hero[1] else 0
    if kind == "singleMatch" and rest.startswith("KOs."):
        try:
            return int(facts["kos"] >= int(rest[len("KOs."):]))
        except ValueError:
            return 0
    if kind == "item" and rest.startswith("dmg.") and hero is not None and detail:
        weapon = rest[len("dmg."):]
        return sum(detail.get(f, 0) for w, f in zip(hero[0], ("damage_weapon1", "damage_weapon2")) if w == weapon)
    if kind == "place" and rest != "1":
        return int(not facts.get("won") and str(facts.get("place")) == rest)
    if event == "item.kill.Unarmed":
        return detail.get("ko_unarmed", 0)
    return missions.count(event, **facts)


def countable(quest: dict) -> bool:
    return not quest["as_bool"]


class Pass:

    def __init__(self, store, cfg: dict | None = None, season: Season | None = None, clock=time.time) -> None:
        self.store, self.cfg, self.clock = store, cfg or {}, clock
        self.season = season if season is not None else load_season()
        self.daily_stars = daily_stars()
        with self.lock:
            self.store.db.executescript(SCHEMA)

    @property
    def lock(self):
        return stats.ledger_for(self.store).lock

    def conf(self, key: str, default: int) -> int:
        try:
            return max(0, int(((self.cfg or {}).get("battlepass") or {}).get(key, default)))
        except (TypeError, ValueError):
            return default

    def on(self) -> bool:
        return self.season is not None and ((self.cfg or {}).get("battlepass") or {}).get("on", True) is not False

    def state(self, player_id: int) -> dict:
        with self.lock:
            row = self.store.db.execute("SELECT stars, path, tiers, quests FROM battle_pass WHERE player_id = ? AND "
                                        "season = ?", (player_id, self.season.id)).fetchone()
        if row is None:
            return {"stars": 0, "path": 0, "tiers": {0: 1}, "quests": {}}
        return {"stars": row[0], "path": row[1], "tiers": {int(k): v for k, v in json.loads(row[2]).items()} or {0: 1},
                "quests": {int(k): v for k, v in json.loads(row[3]).items()}}

    def save(self, player_id: int, st: dict) -> None:
        with self.lock, self.store.db:
            self.store.db.execute("INSERT OR REPLACE INTO battle_pass (player_id, season, stars, path, tiers, quests) "
                                  "VALUES (?, ?, ?, ?, ?, ?)", (player_id, self.season.id, st["stars"], st["path"],
                                                                json.dumps(st["tiers"]), json.dumps(st["quests"])))

    def maxed(self, st: dict, path: int) -> bool:
        return st["tiers"].get(path, 0) >= self.season.max_tiers.get(path, 0)

    def choosable(self, st: dict) -> list[int]:
        return [p for p in range(1, self.season.max_path) if not self.maxed(st, p)]

    def pending_choice(self, st: dict) -> bool:
        return self.maxed(st, st["path"]) and bool(self.choosable(st)) and self.maxed(st, 0)

    def apply(self, st: dict) -> list[tuple[int, int]]:
        earned = st["stars"] // STARS_PER_TIER
        spent = sum(t for p, t in st["tiers"].items()) - 1
        reached = []
        while spent < earned:
            p = st["path"]
            if self.maxed(st, p):
                if p != 0 and not self.choosable(st) and not self.maxed(st, self.season.max_path):
                    st["path"] = self.season.max_path
                    continue
                break
            st["tiers"][p] = st["tiers"].get(p, 0) + 1
            spent += 1
            reached.append((p, st["tiers"][p]))
        return reached

    def choose(self, player_id: int, path: int) -> tuple[bool, list[tuple[int, int]]]:
        st = self.state(player_id)
        if not (0 < path < self.season.max_path) or path == st["path"] or self.maxed(st, path) or not self.maxed(st, 0):
            return False, []
        st["path"] = path
        reached = self.apply(st)
        self.save(player_id, st)
        return True, reached

    def add_match(self, player_id: int, facts: dict, dailies: list[int] = ()) -> tuple[list[int], list[tuple[int, int]], int]:
        st = self.state(player_id)
        boost = self.season.boost(st["tiers"])
        gained = int((self.conf("match_stars", MATCH_STARS) + (self.conf("win_stars", WIN_STARS) if facts.get("won") else 0))
                     * boost)
        gained += sum(self.daily_stars.get(m, 0) for m in dailies)
        done = []
        for qid, q in self.season.quests.items():
            progress, state = st["quests"].get(qid, (0, ACTIVE))
            if state == DONE or not countable(q):
                continue
            add = sum(count(e, facts) for e in q["events"])
            if any(e.startswith("!") and count(e[1:], facts) for e in q["events"]):
                progress, add = 0, 0
            elif not add:
                continue
            progress = min(q["need"], progress + add)
            if progress >= q["need"]:
                state = DONE
                done.append(qid)
                gained += q["stars"]
            st["quests"][qid] = (progress, state)
        st["stars"] += gained
        reached = self.apply(st)
        self.save(player_id, st)
        return done, reached, gained

    def upgrade_rows(self, player_id: int) -> list[tuple[int, int, int]]:
        if not self.on():
            return []
        st, ups = self.state(player_id), self.season.upgrades
        start = [r["item"] for r in self.season.rewards
                 if r["type"] == "Mission" and 0 < r["tier"] <= st["tiers"].get(r["path"], 0)]
        by_id = {u["id"]: name for name, u in ups.items()}
        with self.lock, self.store.db:
            db = self.store.db
            have = {m: (p, s) for m, p, s in db.execute("SELECT mission, progress, state FROM battle_pass_missions "
                                                         "WHERE player_id = ?", (player_id,))}
            wanted = list(start) + [ups[by_id[m]]["next"] for m, (_, s) in list(have.items()) if s == DONE and m in by_id]
            for name in wanted:
                u = ups.get(name)
                if u is not None and u["id"] not in have:
                    db.execute("INSERT OR IGNORE INTO battle_pass_missions (player_id, mission) VALUES (?, ?)",
                               (player_id, u["id"]))
                    have[u["id"]] = (0, ACTIVE)
        return sorted((m, p, s) for m, (p, s) in have.items())

    def count_upgrades(self, player_id: int, facts: dict) -> list[int]:
        rows = self.upgrade_rows(player_id)
        by_id = {u["id"]: u for u in self.season.upgrades.values()}
        done = []
        with self.lock, self.store.db:
            for m, progress, state in rows:
                u = by_id.get(m)
                if u is None or state != ACTIVE:
                    continue
                add = sum(count(e, facts) for e in u["events"])
                if not add:
                    continue
                progress = min(u["need"], progress + add)
                state = DONE if progress >= u["need"] else ACTIVE
                if state == DONE:
                    done.append(m)
                self.store.db.execute("UPDATE battle_pass_missions SET progress = ?, state = ? WHERE player_id = ? AND "
                                      "mission = ?", (progress, state, player_id, m))
        if done:
            self.upgrade_rows(player_id)
        return done

    def header(self, w: BitWriter) -> BitWriter:
        return w.uint(self.season.id % 100).uint(self.season.id).uint(1).uint(max(1, self.season.weeks))

    def missions_packet(self, player_id: int) -> Packet:
        st = self.state(player_id)
        w = self.header(BitWriter().uint(0))
        for qid in sorted(self.season.quests):
            progress, state = st["quests"].get(qid, (0, ACTIVE))
            w.bool(True).uint(qid).small(state).small(min(progress, MAX_SMALL)).uint(0)
        return Packet(MISSIONS, w.bool(False).bytes())

    def progress_packet(self, player_id: int) -> Packet:
        st = self.state(player_id)
        w = BitWriter().uint(0)
        w.byte(min(255, st["tiers"].get(0, 1)))
        w.bool(True).bool(True)
        w.small(min(st["stars"], MAX_SMALL)).small(0)
        w.f32(self.season.boost(st["tiers"]))
        w.small(0).small(0).small(0)
        w.bool(False).uint(0)
        w.small(st["path"])
        w.small(self.season.max_path)
        w.bool(self.pending_choice(st)).bool(self.maxed(st, 0))
        for p in range(self.season.max_path + 1):
            w.small(st["tiers"].get(p, 1 if p == 0 else 0))
        return Packet(PROGRESS, w.bytes())

    @staticmethod
    def ladder_packet() -> Packet:
        return Packet(LADDER, BitWriter().small(0).small(0).small(0).small(0).small(0).bool(False).bytes())

    def rewards_packet(self, reached: list[tuple[int, int]]) -> Packet | None:
        ids = [r["id"] for p, t in reached for r in self.season.rewards_at(p, t)
               if r["type"] not in SKIPPED_REWARDS and r["id"] < 256 and not r["completionist"]]
        if not ids:
            return None
        w = BitWriter()
        for rid in ids:
            w.bool(True).byte(rid)
        return Packet(REWARDS, w.bool(False).bool(False).uint(0).bool(False).bytes())


class Hub:

    def __init__(self, social, store, cfg, log) -> None:
        self.social, self.cfg, self.log = social, cfg or {}, log or (lambda line: None)
        self.bp = Pass(store, self.cfg) if store is not None else None

    @classmethod
    def of(cls, social, store, cfg, log) -> "Hub":
        hub = getattr(social, "battlepass", None)
        if hub is None or (hub.bp is None and store is not None):
            hub = cls(social, store, cfg, log)
            social.battlepass = hub
            if store is not None and getattr(social, "rooms", None) is not None:
                stats.results_for(social.rooms, store, hub.log, social.cfg).listeners.append(hub.on_result)
            daily = getattr(getattr(social, "missions", None), "missions", None)
            if hub.bp is not None and daily is not None:
                daily.extra.append(hub.bp.upgrade_rows)
        return hub

    def on(self) -> bool:
        return self.bp is not None and self.bp.on()

    def _sessions(self, player_id: int) -> list:
        return [s for s in list(getattr(self.social, "sessions", ())) if getattr(s, "player_id", None) == player_id]

    def send_all(self, s, reached=()) -> None:
        if not self.on() or s.player_id is None:
            return
        s.send(self.bp.missions_packet(s.player_id))
        s.send(self.bp.ladder_packet())
        s.send(self.bp.progress_packet(s.player_id))
        r = self.bp.rewards_packet(list(reached))
        if r is not None:
            s.send(r)

    def after_login(self, s) -> None:
        self.send_all(s)
        daily = getattr(self.social, "missions", None)
        if self.on() and daily is not None:
            daily.send_list(s)

    def handle(self, s, p: Packet) -> bool:
        if p.type != CHOOSE:
            return False
        if not self.on() or s.player_id is None:
            return True
        r = BitReader(p.payload)
        season, path = r.uint(), r.small()
        if r.pos > len(p.payload) * 8:
            s.log(f"battle pass path: packet too short ({len(p.payload)} bytes)")
            return True
        ok, reached = (False, []) if season != self.bp.season.id else self.bp.choose(s.player_id, path)
        s.log(f"battle pass path {path} " + ("picked" if ok else "refused") + " (186)")
        self.send_all(s, reached)
        return True

    def on_result(self, entry, verdict, out, rep) -> None:
        if rep is None or not self.on():
            return
        for pid in entry.ids:
            facts = missions.facts_of(rep, pid, entry.playlist, missions.heroes())
            if facts is None:
                continue
            dailies = (out.get("missions") or {}).get(pid, ())
            done, reached, gained = self.bp.add_match(pid, facts, dailies)
            before = self.bp.upgrade_rows(pid)
            upgraded = self.bp.count_upgrades(pid, facts)
            self.log(f"player {pid}: battle pass +{gained} stars" + (f", quests {done}" if done else "")
                     + (f", tiers {reached}" if reached else "") + (f", upgrade missions done {upgraded}" if upgraded else ""))
            daily = getattr(self.social, "missions", None)
            changed = self.bp.upgrade_rows(pid) != before
            for s in self._sessions(pid):
                self.send_all(s, reached)
                if daily is not None and changed:
                    daily.send_list(s)
