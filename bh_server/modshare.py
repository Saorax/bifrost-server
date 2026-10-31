from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import secrets
import struct
import threading
import time
import zlib

from . import swfcheck
from .bits import BitReader, BitWriter
from .frame import Packet

TTL = 600
MAX_PACKAGE = 64 * 1024 * 1024
MAX_CONTENT = 256 * 1024 * 1024
MAX_FILES = 4096
MAX_OWN = 30
MAX_OWN_BYTES = 512 * 1024 * 1024
MAGIC, VERSION = b"BHMP", 1
PATH = re.compile(r"^[A-Za-z0-9 _.-]+(/[A-Za-z0-9 _.-]+)*$")
ENTRY = re.compile(r"^[^@#\s]+@[^#\s]+#[0-9a-f]{8}$")
PID = re.compile(r"^[0-9a-f]{32}$")
KEY_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
VISIBILITIES = ("private", "review", "public", "rejected")

TEXT = {"json", "xml", "txt", "csv", "md", "lang"}
KINDS = TEXT | {"png", "jpg", "jpeg", "swf", "mp3", "ogg", "wav", "bnk", "bin"}


def entry_key(entry: str) -> str:
    return hashlib.sha256(entry.encode()).hexdigest()[:32]


def entry_id_version(entry: str) -> tuple[str, str]:
    head = entry.rsplit("#", 1)[0]
    mod_id, _, version = head.partition("@")
    return mod_id, version


def normal_key(key: str) -> str:
    k = re.sub(r"[^A-Za-z0-9]", "", str(key or "")).upper()
    return k[:4] + "-" + k[4:] if len(k) == 8 else k


def new_key() -> str:
    raw = "".join(secrets.choice(KEY_LETTERS) for _ in range(8))
    return raw[:4] + "-" + raw[4:]


def pack(files: dict[str, bytes]) -> bytes:
    out = bytearray(MAGIC + struct.pack(">HI", VERSION, len(files)))
    for path, data in files.items():
        raw = path.encode()
        out += struct.pack(">H", len(raw)) + raw + struct.pack(">I", len(data)) + data
    return zlib.compress(bytes(out))


def unpack(package: bytes) -> dict[str, bytes]:
    d = zlib.decompressobj()
    try:
        body = d.decompress(package, MAX_CONTENT + 65536)
    except zlib.error as e:
        raise ValueError(f"not a package ({e})") from None
    if d.unconsumed_tail:
        raise ValueError("more than the size limit unpacked")
    if body[:4] != MAGIC or len(body) < 10:
        raise ValueError("not a package")
    version, n = struct.unpack_from(">HI", body, 4)
    if version != VERSION:
        raise ValueError(f"package version {version}")
    if n > MAX_FILES:
        raise ValueError(f"{n} files (at most {MAX_FILES})")
    at, files = 10, {}
    try:
        for _ in range(n):
            (ln,) = struct.unpack_from(">H", body, at)
            path = body[at + 2:at + 2 + ln].decode()
            at += 2 + ln
            (size,) = struct.unpack_from(">I", body, at)
            at += 4
            if at + size > len(body):
                raise ValueError("cut short")
            if not PATH.match(path) or any(part.startswith(".") for part in path.split("/")):
                raise ValueError(f"bad path {path!r}")
            if path.lower() in {p.lower() for p in files}:
                raise ValueError(f"{path!r} twice")
            files[path] = body[at:at + size]
            at += size
    except (struct.error, UnicodeDecodeError):
        raise ValueError("cut short") from None
    if at != len(body):
        raise ValueError("bytes after the last file")
    return files


def kind_problem(path: str, data: bytes) -> str | None:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path.rsplit("/", 1)[-1] else ""
    if ext not in KINDS:
        return f"{path}: .{ext or '(none)'} files aren't mod files"
    if ext in TEXT:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return f"{path}: not UTF-8 text"
        return f"{path}: not text" if "\0" in text else None
    if ext == "png" and not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return f"{path}: not a PNG"
    if ext in ("jpg", "jpeg") and not data.startswith(b"\xff\xd8\xff"):
        return f"{path}: not a JPEG"
    if ext == "swf":
        why = swfcheck.problem(data)
        return f"{path}: {why}" if why else None
    if ext == "mp3" and not (data.startswith(b"ID3") or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)):
        return f"{path}: not an MP3"
    if ext == "ogg" and not data.startswith(b"OggS"):
        return f"{path}: not an Ogg file"
    if ext == "wav" and not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
        return f"{path}: not a WAV"
    if ext == "bnk" and not data.startswith(b"BKHD"):
        return f"{path}: not a sound bank"
    return None


def check(entry: str, package: bytes) -> str | None:
    if len(package) > MAX_PACKAGE:
        return "too large"
    try:
        files = unpack(package)
    except ValueError as e:
        return str(e)
    if "mod.json" not in files:
        return "no mod.json"
    for path, data in files.items():
        why = kind_problem(path, data)
        if why:
            return why
    try:
        manifest = json.loads(files["mod.json"].decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return "mod.json is not JSON"
    mod_id, version = entry_id_version(entry)
    if not isinstance(manifest, dict) or manifest.get("id") != mod_id or manifest.get("version") != version:
        return "mod.json is a different mod or version"
    if entry.rsplit("#", 1)[-1] != manifest_hash(files["mod.json"]):
        return "mod.json is not the one listed"
    return None


def manifest_hash(raw: bytes) -> str:
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    h = 2166136261
    for b in text.encode():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return f"{h:08x}"


class ModLibrary:
    def __init__(self, folder: Path | str | None, clock=time.monotonic) -> None:
        self.folder = Path(folder) if folder is not None else None
        self._tickets: dict[str, dict] = {}
        self._lock = threading.RLock()
        self._clock = clock
        self._memory: dict[str, tuple[dict, bytes]] = {}
        self._asked: dict[object, float] = {}
        self._stats: dict[str, dict] | None = None

    def _prune(self) -> None:
        now = self._clock()
        for t in [t for t, got in self._tickets.items() if now - got["at"] > TTL]:
            del self._tickets[t]

    def ticket(self, kind: str, entries, owner=None, visibility: str = "private", pins: dict | None = None,
               owner_name: str = "", who: str | None = None) -> str:
        t = secrets.token_urlsafe(18)
        with self._lock:
            self._prune()
            self._tickets[t] = {"kind": kind, "entries": frozenset(entries), "at": self._clock(), "owner": owner,
                                "visibility": visibility, "pins": dict(pins or {}), "owner_name": owner_name, "who": who}
        return t

    def _ticket(self, ticket: str | None, kind: str, entry: str) -> dict | None:
        with self._lock:
            self._prune()
            got = self._tickets.get(ticket or "")
        return got if got is not None and got["kind"] == kind and entry in got["entries"] else None

    def allowed(self, ticket: str | None, kind: str, entry: str) -> bool:
        return self._ticket(ticket, kind, entry) is not None

    def upload_ticket(self, ticket: str | None, entry: str) -> dict | None:
        return self._ticket(ticket, "up", entry)

    def resolve(self, ticket: str | None, kind: str, entry: str) -> str | None:
        got = self._ticket(ticket, kind, entry)
        if got is None:
            return None
        pid = got["pins"].get(entry)
        if pid is not None:
            note = self.note(pid)
            return pid if note is not None and note["visibility"] != "rejected" else None
        return self.find(entry, got["owner"])

    def recently(self, key, seconds: float) -> bool:
        now = self._clock()
        with self._lock:
            for k in [k for k, at in self._asked.items() if now - at > TTL]:
                del self._asked[k]
            if key in self._asked and now - self._asked[key] < seconds:
                return True
            self._asked[key] = now
        return False

    MAX_COUNTED = 100000

    def _stats_file(self) -> Path | None:
        return self.folder / "stats.json" if self.folder is not None else None

    def stats(self) -> dict[str, dict]:
        with self._lock:
            if self._stats is None:
                self._stats = {}
                path = self._stats_file()
                try:
                    got = json.loads(path.read_text(encoding="utf-8")) if path is not None and path.exists() else {}
                except (OSError, ValueError):
                    got = {}
                if isinstance(got, dict):
                    for mod_id, row in got.items():
                        if isinstance(row, dict):
                            self._stats[str(mod_id)] = {"likes": [str(x) for x in row.get("likes") or []],
                                                        "got": [str(x) for x in row.get("got") or []]}
            return self._stats

    def _save_stats(self) -> None:
        path = self._stats_file()
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_text(json.dumps(self._stats), encoding="utf-8")
        tmp.replace(path)

    def _public_ids(self) -> set[str]:
        return {entry_id_version(n["entry"])[0] for n in self.notes() if n["visibility"] == "public"}

    def like(self, mod_id: str, account, on: bool) -> str | None:
        if account is None:
            return "Sign in with Discord (Options > Account) to like mods."
        mod_id = str(mod_id or "")
        if mod_id not in self._public_ids():
            return "That mod isn't on the Community tab."
        who = f"a{account}"
        with self._lock:
            row = self.stats().setdefault(mod_id, {"likes": [], "got": []})
            if on and who not in row["likes"] and len(row["likes"]) < self.MAX_COUNTED:
                row["likes"].append(who)
            elif not on and who in row["likes"]:
                row["likes"].remove(who)
            else:
                return None
            self._save_stats()
        return None

    def count_download(self, ticket: str | None, pid: str) -> None:
        with self._lock:
            got = self._tickets.get(ticket or "")
        who = got.get("who") if got else None
        note = self.note(pid)
        if not who or note is None or note["visibility"] != "public" or who == f"a{note['owner']}":
            return
        mod_id = entry_id_version(note["entry"])[0]
        with self._lock:
            row = self.stats().setdefault(mod_id, {"likes": [], "got": []})
            if who not in row["got"] and len(row["got"]) < self.MAX_COUNTED:
                row["got"].append(who)
                self._save_stats()

    def _path(self, pid: str) -> Path:
        return self.folder / (pid + ".bhmp")

    @staticmethod
    def _filled(note: dict, pid: str) -> dict:
        note["pid"] = pid
        note.setdefault("owner", None)
        note.setdefault("owner_name", "")
        if note.get("visibility") not in VISIBILITIES:
            note["visibility"] = "review"
        note.setdefault("review_note", "")
        return note

    def note(self, pid: str) -> dict | None:
        if not PID.match(str(pid or "")):
            return None
        if self.folder is None:
            got = self._memory.get(pid)
            return dict(got[0]) if got else None
        p = self._path(pid).with_suffix(".json")
        try:
            note = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(note, dict) or not isinstance(note.get("entry"), str) or not self._path(pid).exists():
            return None
        return self._filled(note, pid)

    def _write_note(self, note: dict) -> None:
        pid = note["pid"]
        if self.folder is None:
            if pid in self._memory:
                self._memory[pid] = (dict(note), self._memory[pid][1])
            return
        self._path(pid).with_suffix(".json").write_text(json.dumps(note), encoding="utf-8")

    def get(self, pid: str) -> bytes | None:
        if not PID.match(str(pid or "")):
            return None
        if self.folder is None:
            got = self._memory.get(pid)
            return got[1] if got else None
        p = self._path(pid)
        return p.read_bytes() if p.exists() else None

    def find(self, entry: str, owner=None) -> str | None:
        own, public = None, None
        for note in self.notes():
            if note["entry"] != entry or note["visibility"] == "rejected":
                continue
            if owner is not None and note["owner"] == owner and (own is None or note["at"] >= own["at"]):
                own = note
            if note["visibility"] == "public" and (public is None or note["at"] >= public["at"]):
                public = note
        got = own or public
        return got["pid"] if got else None

    def own_copy(self, entry: str, owner) -> dict | None:
        best = None
        for note in self.notes():
            if note["entry"] == entry and owner is not None and note["owner"] == owner and (best is None or note["at"] >= best["at"]):
                best = note
        return best

    def usage(self, owner) -> tuple[int, int]:
        rows = [n for n in self.notes() if owner is not None and n["owner"] == owner]
        return len(rows), sum(int(n.get("size") or 0) for n in rows)

    def room_for(self, owner, size: int = 0) -> str | None:
        n, used = self.usage(owner)
        if n >= MAX_OWN:
            return f"you have {n} mods on the server (at most {MAX_OWN}): delete one first"
        if used + size > MAX_OWN_BYTES:
            return f"that would make {(used + size) >> 20} MB on the server (at most {MAX_OWN_BYTES >> 20} MB)"
        return None

    def put(self, entry: str, package: bytes, owner=None, visibility: str = "private", owner_name: str = "",
            address: str = "") -> str | None:
        why = check(entry, package)
        if why:
            return why
        if visibility not in ("private", "review"):
            visibility = "private"
        pid = hashlib.sha256(f"{owner}\n".encode() + package).hexdigest()[:32]
        with self._lock:
            old = self.note(pid)
            if old is not None:
                if visibility == "review" and old["visibility"] == "private":
                    old["visibility"] = "review"
                    self._write_note(old)
                return None
            if owner is not None:
                why = self.room_for(owner, len(package))
                if why:
                    return why
            note = {"pid": pid, "entry": entry, "owner": owner, "owner_name": str(owner_name)[:40], "visibility": visibility,
                    "key": self._unused_key(), "uploader": address, "size": len(package),
                    "sha256": hashlib.sha256(package).hexdigest(), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "review_note": ""}
            note.update(summary(package))
            if self.folder is None:
                self._memory[pid] = (note, package)
                return None
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self._path(pid).with_suffix(".part")
            tmp.write_bytes(package)
            tmp.replace(self._path(pid))
            self._keep_preview(pid, package)
            self._write_note(note)
        return None

    def _unused_key(self) -> str:
        taken = {n.get("key") for n in self.notes()}
        while True:
            k = new_key()
            if k not in taken:
                return k

    def _keep_preview(self, pid: str, package: bytes | None) -> None:
        data = preview_of(package) if package else None
        if data is not None and self.folder is not None:
            self._path(pid).with_suffix(".preview").write_bytes(data)

    def preview(self, pid: str) -> bytes | None:
        if self.folder is None:
            package = self.get(pid)
            return preview_of(package) if package else None
        if not PID.match(str(pid or "")):
            return None
        p = self._path(pid).with_suffix(".preview")
        return p.read_bytes() if p.exists() else None

    def notes(self) -> list[dict]:
        if self.folder is None:
            return [self._filled(dict(note), pid) for pid, (note, _) in self._memory.items()]
        if not self.folder.exists():
            return []
        out = []
        with self._lock:
            for path in sorted(self.folder.glob("*.json")):
                note = self.note(path.stem)
                if note is None:
                    continue
                changed = False
                if note.get("summary") != SUMMARY:
                    package = self.get(note["pid"])
                    note.update(summary(package) if package else {})
                    self._keep_preview(note["pid"], package)
                    changed = True
                if not note.get("key"):
                    taken = {n.get("key") for n in out}
                    while not note.get("key") or note["key"] in taken:
                        note["key"] = new_key()
                    changed = True
                if changed:
                    try:
                        self._write_note(note)
                    except OSError:
                        pass
                out.append(note)
        return out

    def by_key(self, key: str) -> dict | None:
        k = normal_key(key)
        for note in self.notes():
            if note.get("key") == k and note["visibility"] != "rejected":
                return note
        return None

    def delete(self, pid: str) -> None:
        if self.folder is None:
            self._memory.pop(pid, None)
            return
        for suffix in (".bhmp", ".json", ".preview"):
            try:
                self._path(pid).with_suffix(suffix).unlink()
            except FileNotFoundError:
                pass

    def set(self, pid: str, action: str, owner=None, reviewer: bool = False, why: str = "") -> str:
        with self._lock:
            note = self.note(pid)
            if note is None:
                return "That mod isn't on the server."
            name = str(note.get("name") or note["entry"].split("@")[0])
            mine = owner is not None and note["owner"] == owner
            if action in ("approve", "reject"):
                if not reviewer:
                    return "Only the server's reviewers can do that."
                note["visibility"] = "public" if action == "approve" else "rejected"
                note["review_note"] = str(why or "")[:200]
                note["reviewed"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._write_note(note)
                return f"{name} is public now." if action == "approve" else f"{name} is rejected."
            if not mine and not (reviewer and action == "delete"):
                return "That mod isn't yours."
            if action == "delete":
                self.delete(pid)
                return f"{name} is deleted from the server."
            if note["visibility"] == "rejected":
                return f"{name} was rejected" + (f" ({note['review_note']})" if note["review_note"] else "") + "."
            if action == "public":
                if note["visibility"] == "public":
                    return f"{name} is public already."
                note["visibility"] = "review"
                self._write_note(note)
                return f"{name} is waiting for review: it shows on the Community tab once a reviewer lets it through."
            if action == "private":
                note["visibility"] = "private"
                self._write_note(note)
                return f"{name} is private: only people with its key can get it."
            return "Unknown action."

    def catalog(self, account=None) -> list[dict]:
        newest: dict[str, dict] = {}
        for note in self.notes():
            if note["visibility"] != "public":
                continue
            row = row_of(note)
            if row["id"] not in newest or row["at"] >= newest[row["id"]]["at"]:
                newest[row["id"]] = row
        stats = self.stats()
        for row in newest.values():
            got = stats.get(row["id"], {})
            row["likes"] = len(got.get("likes") or [])
            row["downloads"] = len(got.get("got") or [])
            row["liked"] = account is not None and f"a{account}" in (got.get("likes") or [])
        return sorted(newest.values(), key=lambda r: (-r["likes"], -r["downloads"], r["name"].lower(), r["id"]))

    def mine(self, owner) -> list[dict]:
        rows = [dict(row_of(n), pid=n["pid"], key=n.get("key", ""), visibility=n["visibility"],
                     review_note=n["review_note"]) for n in self.notes() if owner is not None and n["owner"] == owner]
        return sorted(rows, key=lambda r: r["at"], reverse=True)

    def queue(self) -> list[dict]:
        rows = []
        for n in self.notes():
            if n["visibility"] != "review":
                continue
            package = self.get(n["pid"])
            try:
                files = sorted(unpack(package)) if package else []
            except ValueError:
                files = []
            rows.append(dict(row_of(n), pid=n["pid"], key=n.get("key", ""), visibility="review",
                             owner_name=n["owner_name"] or ("(kept before Y.37)" if n["owner"] is None else f"account {n['owner']}"),
                             sha256=n.get("sha256", ""), file_list=files[:40]))
        return sorted(rows, key=lambda r: r["at"])


def row_of(note: dict) -> dict:
    mod_id, version = entry_id_version(note["entry"])
    return {"entry": note["entry"], "id": mod_id, "version": version, "name": str(note.get("name") or mod_id),
            "author": str(note.get("author") or ""), "description": str(note.get("description") or "")[:300],
            "size": int(note.get("size") or 0), "files": int(note.get("files") or 0), "at": str(note.get("at") or ""),
            "preview": bool(note.get("preview")), "kinds": list(note.get("kinds") or ["Other"]),
            "uploader": uploader_tag(note.get("owner"))}


def uploader_tag(owner) -> str:
    return hashlib.sha256(f"bifrost uploader {owner}".encode()).hexdigest()[:12] if owner is not None else ""


MAX_PREVIEW = 4 * 1024 * 1024
SUMMARY = 3

KIND_TABLES = {"ScoringTypes": "Modes", "GameModeTypes": "Modes", "HeroTypes": "Legends", "costumeTypes": "Skins",
               "weaponSkinTypes": "Weapons", "avatarTypes": "Avatars", "TauntTypes": "Emotes", "EmojiTypes": "Emotes",
               "LevelTypes": "Maps"}
KIND_ORDER = ("Modes", "Legends", "Skins", "Weapons", "Avatars", "Emotes", "Maps", "Map art", "Music", "Other")


def kinds_of(manifest: dict) -> list[str]:
    out = set()
    tables = manifest.get("tables") if isinstance(manifest.get("tables"), dict) else {}
    for table, rows in tables.items():
        if rows:
            out.add(KIND_TABLES.get(table, "Other"))
    if manifest.get("mapArt") is not None:
        out.add("Map art")
    if manifest.get("music") is not None or manifest.get("sounds") is not None:
        out.add("Music")
    return [k for k in KIND_ORDER if k in out] or ["Other"]


def _manifest(package: bytes) -> tuple[dict, object] | None:
    try:
        files = unpack(package)
        return files, json.loads(files["mod.json"].decode("utf-8-sig"))
    except (ValueError, KeyError, UnicodeDecodeError):
        return None


def preview_of(package: bytes) -> bytes | None:
    got = _manifest(package)
    if got is None:
        return None
    files, manifest = got
    name = manifest.get("preview") if isinstance(manifest, dict) else None
    data = files.get(name) if isinstance(name, str) else None
    if data is None or len(data) > MAX_PREVIEW:
        return None
    return data if data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff") else None


def summary(package: bytes) -> dict:
    got = _manifest(package)
    if got is None or not isinstance(got[1], dict):
        return {}
    files, manifest = got
    author = manifest.get("author") or manifest.get("authors") or ""
    if isinstance(author, list):
        author = ", ".join(str(a) for a in author)
    return {"name": str(manifest.get("name") or manifest.get("id") or ""), "author": str(author)[:80],
            "description": str(manifest.get("description") or "")[:300], "files": len(files),
            "preview": preview_of(package) is not None, "kinds": kinds_of(manifest), "summary": SUMMARY}


def offers(room, library: ModLibrary, owner=None) -> tuple[list[str], dict[int, list[str]]]:
    host = next((m for m in room.members if m.player_id == room.host and m.mods is not None), None)
    if host is None:
        return [], {}
    wanted = list(host.mods[1])
    need: set[str] = set()
    per_member: dict[int, list[str]] = {}
    for m in room.members:
        if m is host or m.mods is None or m.mods[0] == host.mods[0]:
            continue
        need |= set(wanted) - set(m.mods[1])
        per_member[m.player_id] = wanted
    upload = [e for e in wanted if e in need and owner is not None and library.find(e, owner) is None]
    return upload, per_member


UPLOAD_PACKET, OFFER_PACKET = 11015, 11016
UPLOAD_AGAIN, OFFER_AGAIN = 120, 30

LIBRARY_ASK, LIBRARY, SHARE = 11070, 11071, 11072
KEY_ASK, KEY, MINE_ASK, MINE, SET = 11084, 11085, 11086, 11087, 11088
MAX_SHARE = 20
MAX_JSON = 60000


def library_packet(web: int, ticket: str, obj: dict, kind: int = LIBRARY) -> Packet:
    body = json.dumps(obj, separators=(",", ":"))
    for field in ("review", "mods"):
        while len(body.encode()) > MAX_JSON and obj.get(field):
            obj[field].pop()
            body = json.dumps(obj, separators=(",", ":"))
    return Packet(kind, BitWriter().uint(web).string(ticket).string(body).bytes())


def reviewer(cfg: dict | None, account: dict | None) -> bool:
    if not account or not account.get("discord_id") or not cfg:
        return False
    ids = {str(i) for i in (cfg.get("developer_discord_ids") or [])} | {str(i) for i in (cfg.get("mod_reviewer_discord_ids") or [])}
    return str(account["discord_id"]) in ids


class Desk:

    HANDLED = (LIBRARY_ASK, SHARE, KEY_ASK, MINE_ASK, SET)

    def __init__(self, session, rooms) -> None:
        self.session, self.rooms = session, rooms

    def handle(self, p: Packet) -> bool:
        if p.type not in self.HANDLED:
            return False
        s = self.session
        library = getattr(self.rooms, "library", None)
        web = int(s.cfg.get("web_port", 0) or 0) if s.cfg else 0
        me = s.player["id"] if s.player else s.player_id
        answer = {LIBRARY_ASK: LIBRARY, KEY_ASK: KEY}.get(p.type, MINE)
        if me is None:
            s.log("mod library asked before login: ignored")
            return True
        if library is None or not web:
            s.send(library_packet(0, "", {"error": "This server doesn't share mods."}, answer))
            return True
        if not s.whitelist.allows(s.account):
            s.send(library_packet(0, "", {"error": s.whitelist_refusal()}, answer))
            return True
        if p.type == SHARE:
            self._share(p, library, web, me)
        elif p.type == KEY_ASK:
            self._by_key(p, library, web, me)
        elif p.type == MINE_ASK:
            self.send_mine(library, web)
        elif p.type == SET:
            self._set(p, library, web, me)
        else:
            self.send_library(library, web, me)
        return True

    def send_library(self, library: ModLibrary, web: int, me, message: str = "") -> None:
        s, owner = self.session, self._owner()
        mods = library.catalog(owner)
        who = f"a{owner}" if owner is not None else f"p{me}"
        ticket = library.ticket("down", [m["entry"] for m in mods], owner=owner, who=who) if mods else ""
        body = {"mods": mods}
        if message:
            body["message"] = message
        s.send(library_packet(web, ticket, body))
        s.log(f"mod library listed for player {me}: {len(mods)} mods")

    def _owner(self):
        acc = self.session.account
        return acc["id"] if acc else None

    def send_mine(self, library: ModLibrary, web: int, message: str = "") -> None:
        s, owner = self.session, self._owner()
        is_reviewer = reviewer(s.cfg, s.account)
        if owner is None:
            s.send(library_packet(web, "", {"error": "Sign in with Discord (Options > Account) to put mods on the server.",
                                            "message": message}, MINE))
            return
        mods = library.mine(owner)
        review = library.queue() if is_reviewer else []
        pins = {m["entry"]: m["pid"] for m in review}
        pins.update({m["entry"]: m["pid"] for m in mods})
        ticket = library.ticket("down", list(pins), owner=owner, pins=pins) if pins else ""
        used, size = library.usage(owner)
        s.send(library_packet(web, ticket, {"mods": mods, "used": used, "bytes": size, "max": MAX_OWN,
                                            "max_bytes": MAX_OWN_BYTES, "reviewer": is_reviewer, "review": review,
                                            "message": message}, MINE))

    def _share(self, p: Packet, library: ModLibrary, web: int, me: int) -> None:
        s = self.session
        r = BitReader(p.payload)
        try:
            entries = [r.string()[:200] for _ in range(min(r.uint(), MAX_SHARE))]
            public = r.uint() == 1 if r.left() > 0 else False
        except Exception:
            s.log(f"mod share from player {me}: unreadable")
            return
        owner = self._owner()
        if owner is None:
            self.send_mine(library, web, "Sign in with Discord (Options > Account) to put mods on the server.")
            return
        wanted, notes = [], []
        for e in entries:
            if not ENTRY.match(e):
                continue
            have = library.own_copy(e, owner)
            if have is None:
                if not library.recently(("share", me, e), UPLOAD_AGAIN):
                    wanted.append(e)
            elif public and have["visibility"] == "private":
                notes.append(library.set(have["pid"], "public", owner))
        why = library.room_for(owner) if wanted else None
        s.log(f"mod share from player {me} (account {owner}, {'public' if public else 'private'}): {entries}, asked for {wanted}"
              + (f", refused: {why}" if why else ""))
        if why:
            self.send_mine(library, web, "Not sent: " + why + ".")
            return
        if wanted:
            ticket = library.ticket("up", wanted, owner=owner, visibility="review" if public else "private",
                                    owner_name=s.account.get("name", ""))
            w = BitWriter().uint(web).string(ticket).uint(len(wanted))
            for e in wanted:
                w.string(e)
            s.send(Packet(UPLOAD_PACKET, w.bytes()))
        if notes:
            self.send_mine(library, web, " ".join(notes))

    def _by_key(self, p: Packet, library: ModLibrary, web: int, me: int) -> None:
        s = self.session
        try:
            key = BitReader(p.payload).string()[:40]
        except Exception:
            return
        note = library.by_key(key)
        s.log(f"mod key {normal_key(key)!r} from player {me}: " + (note["entry"] if note else "no such mod"))
        if note is None:
            s.send(library_packet(web, "", {"error": "No mod on this server has the key " + normal_key(key) + "."}, KEY))
            return
        owner = self._owner()
        ticket = library.ticket("down", [note["entry"]], pins={note["entry"]: note["pid"]},
                                who=f"a{owner}" if owner is not None else f"p{me}")
        s.send(library_packet(web, ticket, {"mod": dict(row_of(note), key=note["key"])}, KEY))

    def _set(self, p: Packet, library: ModLibrary, web: int, me: int) -> None:
        s = self.session
        try:
            r = BitReader(p.payload)
            pid, action, why = r.string()[:200], r.string()[:20], r.string()[:200]
        except Exception:
            return
        if action in ("like", "unlike"):
            msg = library.like(pid, self._owner(), action == "like")
            s.log(f"mod {pid} {action} by player {me}: {msg or 'ok'}")
            self.send_library(library, web, me, msg or "")
            return
        msg = library.set(pid[:40], action, self._owner(), reviewer(s.cfg, s.account), why)
        s.log(f"mod {pid} {action} by player {me}: {msg}")
        self.send_mine(library, web, msg)
