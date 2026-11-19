from __future__ import annotations

import json
from pathlib import Path
import re
import secrets
import threading
import time

from .bits import BitReader
from .modshare import WORKSHOP_ASK, ModLibrary, library_packet, reviewer, uploader_tag

ASK, ANSWER = WORKSHOP_ASK, 11102
ACTIONS = ("list", "save", "delete", "add", "remove", "follow", "unfollow", "seen", "creator")
MAX_LISTS = 20
MAX_IN_LIST = 50
MAX_SHOWN = 100
MAX_FOLLOWS = 200
MAX_NEWS = 30
TITLE, NOTE = 60, 300
LIST_ID = re.compile(r"^c[0-9a-f]{10}$")
TAG = re.compile(r"^[0-9a-f]{12}$")
MOD_ID = re.compile(r"^[^@#\s]{1,80}$")


def tidy(text, most: int) -> str:
    return " ".join(str(text or "").split())[:most]


class Shelf:
    def __init__(self, library: ModLibrary, clock=None) -> None:
        self.library = library
        self._lock = threading.RLock()
        self._clock = clock or (lambda: time.strftime("%Y-%m-%d %H:%M:%S"))
        self._data: dict | None = None

    def _file(self) -> Path | None:
        return self.library.folder / "workshop.json" if self.library.folder is not None else None

    def data(self) -> dict:
        with self._lock:
            if self._data is None:
                path = self._file()
                try:
                    got = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else {}
                except (OSError, ValueError):
                    got = {}
                got = got if isinstance(got, dict) else {}
                self._data = {"lists": {}, "follows": {}}
                for cid, row in (got.get("lists") or {}).items():
                    clean = self._clean_list(cid, row)
                    if clean:
                        self._data["lists"][cid] = clean
                for who, tags in (got.get("follows") or {}).items():
                    if isinstance(tags, dict):
                        kept = {t: str(seen) for t, seen in tags.items() if isinstance(t, str) and TAG.match(t)}
                        if kept:
                            self._data["follows"][str(who)] = kept
            return self._data

    @staticmethod
    def _clean_list(cid, row) -> dict | None:
        if not isinstance(cid, str) or not LIST_ID.match(cid) or not isinstance(row, dict):
            return None
        mods = [str(m) for m in row.get("mods") or [] if isinstance(m, str) and MOD_ID.match(m)]
        title = tidy(row.get("title"), TITLE)
        if not title or row.get("owner") is None:
            return None
        return {"id": cid, "owner": row["owner"], "by": tidy(row.get("by"), 40), "title": title,
                "note": str(row.get("note") or "")[:NOTE], "mods": list(dict.fromkeys(mods))[:MAX_IN_LIST],
                "made": str(row.get("made") or ""), "at": str(row.get("at") or "")}

    def _save(self) -> None:
        path = self._file()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(self._data), encoding="utf-8")
        tmp.replace(path)

    def _listed(self) -> dict[str, dict]:
        return {r["id"]: r for r in self.library.catalog()}

    def _public_since(self) -> dict[str, str]:
        out = {}
        for n in self.library.notes():
            if n["visibility"] == "public":
                out[n["entry"]] = str(n.get("reviewed") or n.get("at") or "")
        return out

    def lists(self, account=None) -> list[dict]:
        with self._lock:
            rows = [dict(r) for r in self.data()["lists"].values()]
        for r in rows:
            r["mine"] = account is not None and r["owner"] == account
            del r["owner"]
        rows.sort(key=lambda r: r["at"], reverse=True)
        return [r for r in rows if r["mine"]] + [r for r in rows if not r["mine"]][:MAX_SHOWN]

    def save(self, account, by: str, cid: str, title, note, mods) -> tuple[str, str | None]:
        if account is None:
            return "Sign in with Discord to make collections.", None
        title, note = tidy(title, TITLE), str(note or "").strip()[:NOTE]
        if not title:
            return "A collection needs a name.", None
        if not isinstance(mods, list):
            mods = []
        wanted = list(dict.fromkeys(str(m) for m in mods if isinstance(m, str) and MOD_ID.match(m)))
        if len(wanted) > MAX_IN_LIST:
            return f"A collection holds at most {MAX_IN_LIST} mods.", None
        listed = self._listed()
        with self._lock:
            lists = self.data()["lists"]
            old = lists.get(cid) if cid else None
            if cid and old is None:
                return "That collection isn't on the server any more.", None
            if old is not None and old["owner"] != account:
                return "That collection isn't yours.", None
            if old is None and sum(1 for r in lists.values() if r["owner"] == account) >= MAX_LISTS:
                return f"You have {MAX_LISTS} collections already: delete one first.", None
            had = set(old["mods"]) if old else set()
            gone = [m for m in wanted if m not in listed and m not in had]
            if gone:
                return f"{gone[0]} isn't on the Community tab, so it can't go in a collection.", None
            now = self._clock()
            if old is None:
                cid = "c" + secrets.token_hex(5)
                while cid in lists:
                    cid = "c" + secrets.token_hex(5)
                old = lists[cid] = {"id": cid, "owner": account, "made": now}
            old.update({"by": tidy(by, 40), "title": title, "note": note, "mods": wanted, "at": now})
            self._save()
        return f"{title} is saved.", cid

    def change(self, account, cid: str, mod_id: str, add: bool) -> str:
        if account is None:
            return "Sign in with Discord to make collections."
        with self._lock:
            row = self.data()["lists"].get(cid)
            if row is None:
                return "That collection isn't on the server any more."
            if row["owner"] != account:
                return "That collection isn't yours."
            if add:
                if mod_id in row["mods"]:
                    return f"It is in {row['title']} already."
                if mod_id not in self._listed():
                    return "That mod isn't on the Community tab."
                if len(row["mods"]) >= MAX_IN_LIST:
                    return f"{row['title']} is full ({MAX_IN_LIST} mods)."
                row["mods"].append(mod_id)
            else:
                if mod_id not in row["mods"]:
                    return f"It isn't in {row['title']}."
                row["mods"].remove(mod_id)
            row["at"] = self._clock()
            self._save()
            return f"Added to {row['title']}." if add else f"Taken out of {row['title']}."

    def delete(self, account, cid: str, reviewer_ok: bool = False) -> str:
        with self._lock:
            row = self.data()["lists"].get(cid)
            if row is None:
                return "That collection isn't on the server any more."
            if row["owner"] != account and not reviewer_ok:
                return "That collection isn't yours."
            del self.data()["lists"][cid]
            self._save()
            return f"{row['title']} is deleted."

    def creators(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for r in self.library.catalog():
            tag = r["uploader"]
            if not tag:
                continue
            got = out.setdefault(tag, {"tag": tag, "name": "", "mods": [], "downloads": 0, "likes": 0, "newest": ""})
            got["mods"].append(r)
            got["downloads"] += r["downloads"]
            got["likes"] += r["likes"]
            if r["at"] >= got["newest"]:
                got["newest"] = r["at"]
                if r["author"]:
                    got["name"] = r["author"][:40]
        for got in out.values():
            got["name"] = got["name"] or "A creator"
        return out

    def followers(self, tag: str) -> int:
        with self._lock:
            return sum(1 for tags in self.data()["follows"].values() if tag in tags)

    def following(self, account) -> dict[str, str]:
        if account is None:
            return {}
        with self._lock:
            return dict(self.data()["follows"].get(str(account), {}))

    def follow(self, account, tag: str, on: bool) -> str:
        if account is None:
            return "Sign in with Discord to follow creators."
        tag = str(tag or "")
        creators = self.creators()
        with self._lock:
            follows = self.data()["follows"]
            mine = follows.get(str(account), {})
            if not on:
                if tag not in mine:
                    return "You don't follow them."
                del mine[tag]
                if not mine:
                    del follows[str(account)]
                self._save()
                name = creators[tag]["name"] if tag in creators else "them"
                return f"You don't follow {name} any more."
            if tag not in creators:
                return "That creator has no mods on the Community tab."
            if tag == uploader_tag(account):
                return "That's you."
            if tag in mine:
                return f"You follow {creators[tag]['name']} already."
            if len(mine) >= MAX_FOLLOWS:
                return f"You follow {MAX_FOLLOWS} creators already."
            follows.setdefault(str(account), mine)[tag] = self._clock()
            self._save()
            return f"You follow {creators[tag]['name']}: their new mods show under News."

    def news(self, account) -> list[dict]:
        follows = self.following(account)
        if not follows:
            return []
        since = self._public_since()
        rows = []
        for r in self.library.catalog():
            seen = follows.get(r["uploader"])
            when = since.get(r["entry"], r["at"])
            if seen is not None and when > seen:
                rows.append(dict(r, since=when))
        rows.sort(key=lambda r: r["since"], reverse=True)
        return rows[:MAX_NEWS]

    def seen(self, account) -> str:
        if account is None:
            return ""
        now = self._clock()
        with self._lock:
            mine = self.data()["follows"].get(str(account))
            if not mine:
                return ""
            for tag in mine:
                mine[tag] = max(mine[tag], now)
            self._save()
        return ""

    def profile(self, tag: str, account=None) -> dict | None:
        got = self.creators().get(str(tag or ""))
        if got is None:
            return None
        follows = self.following(account)
        return {"tag": got["tag"], "name": got["name"], "mods": got["mods"], "downloads": got["downloads"],
                "likes": got["likes"], "followers": self.followers(got["tag"]), "following": got["tag"] in follows,
                "you": account is not None and got["tag"] == uploader_tag(account),
                "lists": [r for r in self.lists(account) if any(m["id"] in r["mods"] for m in got["mods"])][:10]}

    def view(self, account, creator: str = "") -> dict:
        listed = self._listed()
        lists = self.lists(account)
        creators = self.creators()
        follows = self.following(account)
        news = self.news(account)
        wanted = {m for r in lists for m in r["mods"]}
        body = {"collections": lists,
                "rows": [listed[m] for m in sorted(wanted) if m in listed],
                "follows": [{"tag": t, "name": creators[t]["name"] if t in creators else "A creator",
                             "mods": len(creators[t]["mods"]) if t in creators else 0,
                             "news": sum(1 for r in news if r["uploader"] == t), "since": seen}
                            for t, seen in sorted(follows.items(), key=lambda kv: kv[1], reverse=True)],
                "news": news, "signed_in": account is not None, "max_lists": MAX_LISTS, "max_in_list": MAX_IN_LIST}
        if creator:
            body["creator"] = self.profile(creator, account)
        return body


def shelf_of(library: ModLibrary) -> Shelf:
    got = getattr(library, "_shelf", None)
    if got is None:
        got = library._shelf = Shelf(library)
    return got


class Desk:
    HANDLED = (ASK,)

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms

    def handle(self, p) -> bool:
        if p.type != ASK:
            return False
        s = self.session
        library = getattr(self.rooms, "library", None)
        web = int(s.cfg.get("web_port", 0) or 0) if s.cfg else 0
        me = s.player["id"] if s.player else s.player_id
        if me is None:
            s.log("workshop asked before login: ignored")
            return True
        if library is None or not web:
            s.send(library_packet(0, "", {"error": "This server doesn't share mods."}, ANSWER))
            return True
        if not s.whitelist.allows(s.account):
            s.send(library_packet(0, "", {"error": s.whitelist_refusal()}, ANSWER))
            return True
        try:
            r = BitReader(p.payload)
            action = r.string()[:20]
            args = json.loads(r.string()[:20000] or "{}") if r.left() > 0 else {}
        except Exception:
            s.log(f"workshop ask from player {me}: unreadable")
            return True
        if not isinstance(args, dict):
            args = {}
        shelf = shelf_of(library)
        account = s.account["id"] if s.account else None
        staff = reviewer(s.cfg, s.account)
        cid = str(args.get("id") or "")[:20]
        message, creator = "", ""
        if action == "save":
            message, cid = shelf.save(account, s.player_name or "", cid, args.get("title"), args.get("note"), args.get("mods"))
        elif action == "delete":
            message = shelf.delete(account, cid, staff)
        elif action in ("add", "remove"):
            message = shelf.change(account, cid, str(args.get("mod") or "")[:80], action == "add")
        elif action in ("follow", "unfollow"):
            message = shelf.follow(account, str(args.get("tag") or "")[:20], action == "follow")
            creator = str(args.get("tag") or "")[:20]
        elif action == "seen":
            shelf.seen(account)
        elif action == "creator":
            creator = str(args.get("tag") or "")[:20]
        elif action != "list":
            message = "Unknown action."
        body = shelf.view(account, creator)
        if creator and body.get("creator") is None:
            message = message or "That creator has no mods on the Community tab."
        if staff:
            body["reviewer"] = True
        if message:
            body["message"] = message
        if action == "save" and cid:
            body["saved"] = cid
        entries = {r["entry"] for r in body["rows"]} | {r["entry"] for r in body["news"]}
        if body.get("creator"):
            entries |= {r["entry"] for r in body["creator"]["mods"]}
        who = f"a{account}" if account is not None else f"p{me}"
        ticket = library.ticket("down", sorted(entries), owner=account, who=who) if entries else ""
        s.send(library_packet(web, ticket, body, ANSWER, ("news", "collections", "rows")))
        s.log(f"workshop {action} from player {me}: {message or 'ok'}")
        return True
