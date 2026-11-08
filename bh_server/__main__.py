from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import queue
import signal
import sys
import threading
import time

from . import backup, bugreports, discord, game, integrity, moderation, modshare, relay, themes, tls, tournaments, web
from .logins import GameLogins
from .modshare import ModLibrary
from .rooms import Rooms
from .social import Social
from .store import Store
from .tournaments import Tournaments

HERE = Path(__file__).resolve().parents[1]


class LogWriter:

    def __init__(self, path: Path, max_bytes: int = 10 << 20, keep: int = 5):
        self.path, self.max_bytes, self.keep = path, max_bytes, keep
        self.file = open(path, "a", encoding="utf-8")
        self.size = self.file.tell()
        self.lines: queue.SimpleQueue = queue.SimpleQueue()
        self.lock = threading.Lock()
        threading.Thread(target=self.run, name="log", daemon=True).start()
        atexit.register(self.drain)

    def __call__(self, line: str) -> None:
        self.lines.put(time.strftime("%Y-%m-%d %H:%M:%S ") + line)

    def run(self) -> None:
        while True:
            try:
                text = self.lines.get(timeout=1.0)
            except queue.Empty:
                continue
            with self.lock:
                self.write(text)
                if self.lines.empty():
                    self.file.flush()

    def write(self, text: str) -> None:
        try:
            print(text, flush=True)
        except Exception:
            pass
        self.file.write(text + "\n")
        self.size += len(text) + 1
        if self.max_bytes and self.size >= self.max_bytes:
            self.rotate()

    def rotate(self) -> None:
        self.file.close()
        try:
            for i in range(self.keep, 0, -1):
                older = self.path.with_name(f"{self.path.stem}.{i}{self.path.suffix}")
                newer = self.path if i == 1 else self.path.with_name(f"{self.path.stem}.{i - 1}{self.path.suffix}")
                if newer.exists():
                    newer.replace(older)
        except OSError:
            pass
        self.file = open(self.path, "a", encoding="utf-8")
        self.size = self.file.tell()

    def drain(self) -> None:
        with self.lock:
            while True:
                try:
                    self.write(self.lines.get_nowait())
                except queue.Empty:
                    break
            self.file.flush()


def game_tls_only(cfg: dict, data: Path, log) -> None:
    if not (tls.folder(data) / "ca.pem").exists():
        return
    try:
        d = tls.make(cfg, data, log)
    except Exception as e:
        log(f"tls: {e}: the game port stays plain")
        return
    if d is not None:
        cfg["game_tls_dir"] = str(d)
        log("tls: the web side is plain http (\"tls\": false); the game port still takes TLS for games that kept "
            "its certificate")


def use_tls(cfg: dict, data: Path, log) -> bool:
    cfg["web_scheme"] = "http"
    if not tls.enabled(cfg, data):
        game_tls_only(cfg, data, log)
        return False
    try:
        d = tls.make(cfg, data, log)
    except Exception as e:
        log(f"tls: {e}: the web side stays plain http")
        return False
    if d is None:
        return False
    cfg["tls_dir"], cfg["game_tls_dir"], cfg["web_scheme"] = str(d), str(d), "https"
    if cfg["public_url"].startswith("http://"):
        cfg["public_url"] = "https://" + cfg["public_url"][len("http://"):]
    log(f"tls: the web side is {cfg['public_url']} (certificate authority SHA-256 {tls.fingerprint(d)})")
    return True


def check_sign_in(cfg: dict, log) -> str | None:
    if not cfg.get("discord_client_secret"):
        return None
    problem = discord.setup_problem(cfg)
    if problem:
        log(f"DISCORD SIGN-IN IS OFF: {problem}. Players who choose Login with Discord are told the server is set up "
            "wrong.")
    elif discord.local_only(cfg):
        log(f"sign-in: public_url {cfg['public_url']!r} only works in a browser on this PC; set public_url to the "
            "address players reach (README.md, Discord sign-in)")
    else:
        log(f"sign-in: add {discord.redirect_uri(cfg)} to your Discord application's OAuth2 redirects")
    return problem


def load_config(here: Path = HERE, env=os.environ) -> dict:
    cfg = json.loads((here / "config.json").read_text(encoding="utf-8"))
    local = here / "config.local.json"
    if local.exists():
        cfg.update(json.loads(local.read_text(encoding="utf-8")))
    if env.get("BH_DISCORD_CLIENT_SECRET"):
        cfg["discord_client_secret"] = env["BH_DISCORD_CLIENT_SECRET"]
    if env.get("BH_PUBLIC_URL"):
        cfg["public_url"] = env["BH_PUBLIC_URL"]
    cfg.setdefault("public_url", f"http://localhost:{cfg['web_port']}")
    return cfg


CONSOLE_HELP = ("commands: say <text> (a notice to every game signed in), popup <text> (the same as a small popup), "
                "who (the players signed in), mute <name> [minutes] [reason] (no chat; no minutes: until unmuted), "
                "unmute <name>, mutes (the muted players), kick <name> [reason] (signs the game out, back to offline), theme [name|id] (the menus' seasonal theme for every game), "
                "backup (copies accounts.sqlite into the backups now), flags [days] (ranked integrity: repeated pairs, "
                "one-sided series, daily Elo cap hits, impossible stats), mods (the mods waiting for review), "
                "mods show|approve|reject|delete <key or id> [note] (Y.37: public mods are checked by you first), "
                "notify <text> (Y.52: a row in every signed-in game's notification list, kept until read), bugs [id] (Y.52: the players' bug reports; with an id, writes that one to data/bug-reports/<id>.txt), "
                "admins (Y.53: config admin_discord_ids), ban <name> [minutes] [reason], unban <name>, bans, "
                "resetrating <name> [ladder], reports [all] (Report Player), dismiss <id>, announce <text> (a row in every "
                "game's notification list, also at each login, until removed), unannounce <id>, announcements")


def mods_command(rest: str, library) -> str:
    if library is None:
        return "this server keeps no mods"
    words = rest.split()
    if not words:
        rows = library.queue()
        return f"{len(rows)} waiting for review" + "".join(
            f"\n  {r['key']}  {r['name']} {r['version']} by {r['owner_name']} ({r['size'] >> 10} KB, {r['files']} files, {r['at']})"
            for r in rows)
    action, target, note = words[0].lower(), (words[1] if len(words) > 1 else ""), " ".join(words[2:])
    found = library.by_key(target) or library.note(target.lower()) or next(
        (n for n in library.notes() if n.get("key") == modshare.normal_key(target)), None)
    if found is None or action not in ("show", "approve", "reject", "delete"):
        return "mods show|approve|reject|delete <key or id> [note]"
    if action == "show":
        package = library.get(found["pid"]) or b""
        try:
            files = modshare.unpack(package)
        except ValueError as e:
            return f"{found['entry']}: unreadable ({e})"
        lines = [f"{found['key']}  {found['entry']}  {found['visibility']}  owner {found['owner_name'] or found['owner']}",
                 f"  sha256 {found.get('sha256', '')}  {len(package)} bytes, {len(files)} files"]
        lines += [f"  {path}  {len(data)} bytes" for path, data in sorted(files.items())[:60]]
        why = modshare.check(found["entry"], package)
        lines.append("  checks: " + (why or "pass (file kinds, SWFs without code, mod.json)"))
        return "\n".join(lines)
    return library.set(found["pid"], action, reviewer=True, why=note)


def console_command(line: str, social, log) -> str:
    cmd, _, rest = line.strip().partition(" ")
    cmd, rest = cmd.lower(), rest.strip()
    if cmd in ("say", "popup") and rest:
        n = social.announce(rest, popup=cmd == "popup")
        return f"sent to {n} game{'' if n == 1 else 's'}"
    if cmd == "who":
        with social.lock:
            names = sorted({social.display(social.key(s)) for s in social.sessions})
        return f"{len(names)} signed in" + (": " + ", ".join(names) if names else "")
    if cmd in ("kick", "mute", "unmute") and rest:
        words = rest.split()
        for n in range(len(words), 0, -1):
            keys = social.keys_named(" ".join(words[:n]))
            if cmd == "unmute" and not keys:
                keys = [k for k in social.mutes if social.display(k).lower() == " ".join(words[:n]).lower()]
            if keys:
                break
        else:
            return f"nobody signed in is named {rest!r}"
        if len(keys) > 1:
            return "more than one player matches: " + ", ".join(sorted(social.display(k) for k in keys))
        key, more = keys[0], words[n:]
        if cmd == "kick":
            n_games = social.kick(key, " ".join(more))
            return f"{social.display(key)} signed out ({n_games} game{'' if n_games == 1 else 's'})"
        if cmd == "unmute":
            return f"{social.display(key)} unmuted" if social.unmute(key) else f"{social.display(key)} wasn't muted"
        minutes = 0.0
        if more:
            try:
                minutes, more = float(more[0]), more[1:]
            except ValueError:
                pass
        social.mute(key, minutes, " ".join(more))
        return f"{social.display(key)} muted " + (f"for {minutes:g} min" if minutes > 0 else "until unmuted")
    if cmd == "theme":
        if not rest:
            on = getattr(social, "client_theme", None)
            return ("theme now: " + (themes.name_of(on) if on is not None else "the config's (or the game's default)")
                    + "; live themes: " + ", ".join(themes.LIVE))
        theme = themes.resolve(rest)
        if theme is None:
            return f"{rest!r} isn't a live theme: " + ", ".join(themes.LIVE)
        social.client_theme = theme
        with social.lock:
            sessions = list(social.sessions)
        for s in sessions:
            social._send(s, themes.theme_packet(theme))
        log(f"client theme {themes.name_of(theme)} ({theme}) sent to {len(sessions)} game(s) (2551)")
        return f"theme {themes.name_of(theme)} sent to {len(sessions)} game{'' if len(sessions) == 1 else 's'}"
    if cmd == "flags":
        try:
            days = float(rest) if rest else 1.0
        except ValueError:
            return "flags [days]"
        return integrity.text(integrity.flags(social.store.db, days, social.cfg), days)
    if cmd == "mods":
        return mods_command(rest, getattr(getattr(social, "rooms", None), "library", None))
    if cmd == "notify" and rest:
        with social.lock:
            sessions = list(social.sessions)
        packet = tournaments.list_row(f"server:{int(time.time())}", "SERVER NEWS", rest)
        for s in sessions:
            social._send(s, packet)
        log(f"notify: {rest!r} sent to {len(sessions)} game(s) (11096)")
        return f"sent to {len(sessions)} game{'' if len(sessions) == 1 else 's'}"
    if cmd == "bugs":
        return bugreports.console(rest, social.store.db, HERE / "data" / "bug-reports")
    if cmd == "backup":
        out = backup.run_once(load_config(), HERE / "data", log)
        return f"backup written: {out}" if out else "backup failed (see the log)"
    if cmd == "mutes":
        rows = [(social.display(k), m) for k, m in list(social.mutes.items()) if social.muted(k)]
        return f"{len(rows)} muted" + "".join(
            f"\n  {name}: " + (f"{max(1, round((m[0] - social.wall()) / 60))} min left" if m[0] else "until unmuted")
            + (f" ({m[1]})" if m[1] else "") for name, m in sorted(rows))
    out = moderation.console(cmd, rest, social)
    if out is not None:
        return out
    return CONSOLE_HELP if cmd else ""


def tick(social, stop=None, every: float = 30.0) -> None:
    while not (stop or threading.Event()).wait(every):
        try:
            social.lift_expired()
        except Exception:
            pass
        try:
            clans = getattr(social, "clans", None)
            if clans is not None:
                clans.week_turn()
        except Exception:
            pass


def console(social, log, stream=None) -> None:
    stream = stream or sys.stdin
    if stream is None:
        return
    for line in stream:
        try:
            out = console_command(line, social, log)
        except Exception as e:
            out = f"command failed: {e!r}"
        if out:
            print(out, flush=True)


def main() -> int:
    cfg = load_config()
    try:
        sys.stdout.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    data = HERE / "data"
    data.mkdir(exist_ok=True)
    log = LogWriter(data / "server-log.txt", int(float(cfg.get("log_max_mb", 10)) * (1 << 20)), int(cfg.get("log_keep", 5)))
    use_tls(cfg, data, log)
    check_sign_in(cfg, log)

    store = Store(str(data / "accounts.sqlite"))
    salt_file = data / "salt"
    if not salt_file.exists():
        salt_file.write_bytes(os.urandom(32))
    logins = GameLogins()
    rooms = Rooms()
    rooms.library = ModLibrary(data / "mods")
    social = Social.of(rooms, store, cfg, log)
    Tournaments.of(rooms, store, cfg, log)
    if cfg.get("cups", False):
        from .cupsets import CupSets
        CupSets.serve(rooms, store, cfg, log)
    g = game.serve(cfg, log, store=store, salt=salt_file.read_bytes(), logins=logins, rooms=rooms)
    game.start(g)
    if relay.command(cfg) is None:
        r = relay.Relay(cfg, rooms, rooms.tokens, log)
        r.start()
        relay_where = f"P2P relay on UDP {r.port}"
        if cfg.get("relay") == "native":
            log(f"relay_native {cfg.get('relay_native')!r} not found: the relay runs in this process")
    else:
        relay.launch(cfg, rooms.tokens, log)
        relay_where = f"P2P relay process ({cfg.get('relay')}) on UDP {cfg['game_port']}"
    backup.start(cfg, data, log)
    w = web.serve(cfg, store, log, logins=logins, library=rooms.library, social=social)
    log(f"game connection on {cfg['game_host']}:{cfg['game_port']} ({relay_where}); sign-in page {cfg['public_url']}/"
        + ("" if cfg.get("discord_client_secret") else " (no Discord client secret set: sign-in is off)"))
    print(CONSOLE_HELP, flush=True)
    threading.Thread(target=console, args=(social, log), daemon=True).start()
    threading.Thread(target=tick, args=(social,), daemon=True).start()
    if hasattr(signal, "SIGBREAK"):
        def on_break(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGBREAK, on_break)
    try:
        w.serve_forever()
    except KeyboardInterrupt:
        n = g.announce_restart()
        if n:
            time.sleep(0.5)
        log(f"stopped (told {n} connected game{'' if n == 1 else 's'} the server is restarting)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
