from __future__ import annotations

import html
import http.cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import time
import urllib.parse

from . import discord, modshare, patches
from .tournaments import Tournaments
from .whitelist import Whitelist


def make_handler(cfg: dict, store, log, opener=None, logins=None, library=None, social=None):
    states: dict[str, float] = {}
    redirect_uri = discord.redirect_uri(cfg)
    secure = "; Secure" if str(cfg.get("public_url", "")).startswith("https://") else ""
    tours = Tournaments.of(social.rooms, store, cfg, log) if social is not None and store is not None else None
    whitelist = Whitelist(cfg)
    bifrost = patches.Patches(Path(cfg.get("patch_dir") or Path(__file__).resolve().parent.parent / "data" / "patches"))

    class Handler(BaseHTTPRequestHandler):
        server_version = "bh-private-server"
        timeout = 30

        def _form(self) -> dict | None:
            size = int(self.headers.get("Content-Length", 0) or 0)
            if size > MAX_FORM:
                self._send(413, page("Too large", "<p>That form is too large.</p>"))
                return None
            return dict(urllib.parse.parse_qsl(self.rfile.read(max(0, size)).decode(errors="replace")))

        def log_message(self, fmt, *args):
            pass

        def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8", headers: dict | None = None):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in SAFE_HEADERS.items():
                self.send_header(k, v)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj), "application/json")

        def _send_bytes(self, data: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _discord_user(self, code: str) -> dict:
            kw = {"opener": opener} if opener else {}
            return discord.user_for_code(code, cfg["discord_client_id"], cfg["discord_client_secret"], redirect_uri,
                                         guilds=discord.wants_guilds(cfg), **kw)

        def _sign_in(self, user: dict):
            acc, token = store.login(user["id"], user["name"])
            store.set_guilds(acc["id"], user.get("guilds"))
            return acc, token

        def _cookie(self, name: str) -> str | None:
            c = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
            return c[name].value if name in c else None

        def _bearer(self):
            auth = self.headers.get("Authorization", "")
            token = auth[7:] if auth.startswith("Bearer ") else self._cookie("bh_session")
            return store.account_for_token(token) if token else None

        def do_GET(self):
            url = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(url.query))
            if url.path == "/":
                acc = self._bearer()
                if not acc:
                    return self._send(200, page("Sign in", "<p>Sign in with Discord to keep your progress on this server. "
                                                "Only your Discord ID and name are stored.</p><p><a class=b href='/login'>Sign in "
                                                "with Discord</a></p>"))
                return self._send(200, page(f"Signed in: {html.escape(acc['name'])}", "<p>You can close this tab and go "
                                            "back to the game.</p><p><a href='/logout'>Sign out</a></p>"))
            if url.path == "/login":
                if not cfg.get("discord_client_secret"):
                    return self._send(500, page("Not set up", "<p>The server has no Discord client secret. Set <code>BH_DISCORD_CLIENT_SECRET</code> "
                                                "or put it in <code>server/config.local.json</code>.</p>"))
                if discord.setup_problem(cfg):
                    return self._send(500, page("Not set up", "<p>This server's Discord sign-in is set up wrong: its web "
                                                "address won't open in a browser. The server's log says how to fix it.</p>"))
                state = secrets.token_urlsafe(16)
                now = time.monotonic()
                for old in [s for s, at in states.items() if now - at > STATE_TTL]:
                    del states[old]
                while len(states) >= MAX_STATES:
                    del states[next(iter(states))]
                states[state] = now
                to = discord.authorize_url(cfg["discord_client_id"], redirect_uri, state, discord.wants_guilds(cfg))
                return self._send(302, "", headers={"Location": to})
            if url.path == "/callback" and logins is not None and str(q.get("state", "")).startswith("game-"):
                return self._game_callback(q)
            if url.path == "/callback":
                if q.get("state") not in states or "code" not in q:
                    return self._send(400, page("Sign-in failed", "<p>That sign-in link is not from this server, or it was used already. <a href='/login'>Try again</a>.</p>"))
                states.pop(q["state"], None)
                try:
                    user = self._discord_user(q["code"])
                except Exception as e:
                    log(f"discord sign-in failed: {type(e).__name__}")
                    return self._send(502, page("Sign-in failed", "<p>Discord didn't accept the sign-in. <a href='/login'>Try again</a>.</p>"))
                acc, token = self._sign_in(user)
                log(f"account {acc['id']} signed in ({acc['name']})")
                return self._send(302, "", headers={"Location": "/", "Set-Cookie": f"bh_session={token}; HttpOnly; Path=/; SameSite=Lax{secure}"})
            if url.path == "/logout":
                token = self._cookie("bh_session")
                if token and store is not None and store.end_session(token):
                    log("a web sign-in token signed out")
                return self._send(302, "", headers={"Location": "/", "Set-Cookie": f"bh_session=; Max-Age=0; Path=/{secure}"})
            if url.path == "/info":
                return self._json(200, {"name": cfg.get("server_name") or "Bifrost server",
                                        "whitelist": whitelist.on, "sign_in": bool(cfg.get("discord_client_secret")),
                                        "players": social.players_online() if social is not None else None,
                                        "activity": social.activity() if social is not None else None})
            if url.path == "/tls/ca.pem" and cfg.get("tls_dir"):
                return self._send(200, (Path(cfg["tls_dir"]) / "ca.pem").read_text(encoding="ascii"), "application/x-pem-file")
            if url.path == "/tls/ca-engine.pem" and cfg.get("tls_dir") and (Path(cfg["tls_dir"]) / "ca-engine.pem").exists():
                return self._send(200, (Path(cfg["tls_dir"]) / "ca-engine.pem").read_text(encoding="ascii"),
                                  "application/x-pem-file")
            if url.path == "/bifrost/latest":
                found = bifrost.latest(q.get("original", ""))
                return self._json(200, found) if found else self._json(404, {"error": "no Bifrost patch for this game version"})
            if url.path.startswith("/bifrost/patch/"):
                path = bifrost.path(url.path[len("/bifrost/patch/"):])
                if path is None:
                    return self._json(404, {"error": "no such patch"})
                data = path.read_bytes()
                self._send_bytes(data, "application/octet-stream")
                log(f"Bifrost patch {path.name} sent ({len(data)} bytes)")
                return
            if url.path == "/mods/get":
                entry = q.get("entry", "")
                if library is None or not library.allowed(q.get("ticket"), "down", entry):
                    return self._json(403, {"error": "no ticket for that mod"})
                pid = library.resolve(q.get("ticket"), "down", entry)
                data = library.get(pid) if pid else None
                if data is None:
                    return self._json(404, {"error": "not uploaded yet"})
                self._send_bytes(data, "application/octet-stream")
                library.count_download(q.get("ticket"), pid)
                log(f"mod package {entry} sent ({len(data)} bytes)")
                return
            if url.path == "/mods/preview":
                entry = q.get("entry", "")
                if library is None or not (library.allowed(q.get("ticket"), "down", entry) or
                                           library.allowed(q.get("ticket"), "preview", entry)):
                    return self._json(403, {"error": "no ticket for that mod"})
                pid = library.resolve(q.get("ticket"), "down", entry) or library.resolve(q.get("ticket"), "preview", entry)
                data = library.preview(pid) if pid else None
                if data is None:
                    return self._json(404, {"error": "no preview"})
                return self._send_bytes(data, "image/png" if data.startswith(b"\x89PNG") else "image/jpeg")
            if url.path == "/api/tournaments/events.xml" and tours is not None:
                found = tours.web_get(url.path, None, cfg["public_url"], q)
                if found is not None:
                    code, ctype, body, _ = found
                    return self._send(code, body, ctype)
            return self._send(404, page("Not found", "<p>Nothing here.</p>"))

        def _game_callback(self, q: dict):
            session = logins.take(q.get("state"))
            if session is None:
                return self._send(400, page("Sign-in expired", "<p>That sign-in link was used already or is more than ten "
                                            "minutes old. In the game, choose <b>Login with Discord</b> again.</p>"))
            if "code" not in q:
                try:
                    session.sign_in_failed("Discord sign-in was cancelled.")
                except OSError:
                    pass
                return self._send(200, page("Sign-in cancelled", "<p>Nothing changed. You can close this tab.</p>"))
            try:
                user = self._discord_user(q["code"])
            except Exception as e:
                log(f"discord sign-in failed: {type(e).__name__}")
                try:
                    session.sign_in_failed("Discord didn't accept the sign-in. Try again.")
                except OSError:
                    pass
                return self._send(502, page("Sign-in failed", "<p>Discord didn't accept the sign-in. In the game, choose "
                                            "<b>Login with Discord</b> again.</p>"))
            cid = logins.hold(session, str(q.get("state")), user)
            return self._send(200, page("One more step", confirm_form(cid, user["name"])))

        def _game_confirm(self):
            form = self._form()
            if form is None:
                return
            got = logins.confirm(form.get("id", ""), form.get("code", ""))
            if got == "wrong":
                return self._send(200, page("Wrong code", "<p>That isn't the code your game shows.</p>"
                                            + confirm_form(form.get("id", ""), "")))
            if got is None:
                return self._send(400, page("Sign-in ended", "<p>That sign-in ended (a wrong code three times, or more "
                                            "than ten minutes). In the game, choose <b>Login with Discord</b> again.</p>"))
            session, user = got
            acc, token = self._sign_in(user)
            log(f"account {acc['id']} signed in from the game ({acc['name']})")
            try:
                session.signed_in(acc, token)
            except OSError:
                return self._send(200, page("Game disconnected", "<p>You're signed in, but the game had disconnected. "
                                            "Choose <b>Login with Discord</b> again after it reconnects.</p>"))
            return self._send(200, page(f"Signed in: {html.escape(acc['name'])}", "<p>Your game is logged in. You can "
                                        "close this tab and go back to the game.</p>"),
                              headers={"Set-Cookie": f"bh_session={token}; HttpOnly; Path=/; SameSite=Lax{secure}"})

        def do_POST(self):
            url = urllib.parse.urlparse(self.path)
            if url.path == "/callback/confirm" and logins is not None:
                return self._game_confirm()
            if url.path == "/mods/upload":
                q = dict(urllib.parse.parse_qsl(url.query))
                entry = q.get("entry", "")
                up = library.upload_ticket(q.get("ticket"), entry) if library is not None else None
                if up is None or up["owner"] is None:
                    return self._json(403, {"error": "no ticket for that mod"})
                size = int(self.headers.get("Content-Length", 0) or 0)
                if size <= 0 or size > modshare.MAX_PACKAGE:
                    return self._json(413, {"error": f"too large (at most {modshare.MAX_PACKAGE >> 20} MB) or empty"})
                why = library.put(entry, self.rfile.read(size), up["owner"], up["visibility"], up["owner_name"],
                                  self.client_address[0])
                log(f"mod package {entry} upload ({size} bytes): " + (f"refused, {why}" if why else "kept"))
                return self._json(400, {"error": why}) if why else self._json(200, {"ok": True})
            return self._json(404, {"error": "not found"})

    return Handler


def confirm_form(cid: str, name: str) -> str:
    who = f"Discord says you are <b>{html.escape(name)}</b>. " if name else ""
    return (f"<p>{who}Type the 4-digit code your game shows to finish signing in. If you didn't start this sign-in "
            "in your own game, close this tab: someone may have sent you their link.</p>"
            "<form method=post action='/callback/confirm'>"
            f"<input type=hidden name=id value='{html.escape(cid, quote=True)}'>"
            "<input name=code inputmode=numeric autocomplete=off maxlength=4 autofocus "
            "style='font-size:28px;width:5em;letter-spacing:6px;text-align:center'> "
            "<button class=b style='font-size:18px;padding:10px 16px'>Finish</button></form>")


STATE_TTL, MAX_STATES = 600, 1000
SAFE_HEADERS = {"X-Frame-Options": "DENY", "Content-Security-Policy": "frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"}
MAX_FORM = 64 * 1024


def page(title: str, body: str) -> str:
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>body{{font:16px system-ui;background:#0f1115;color:#e8ebf1;max-width:560px;"
            "margin:48px auto;padding:0 16px}a{color:#4cc2b6}a.b{display:inline-block;background:#5865f2;color:#fff;padding:10px 16px;"
            "border-radius:8px;text-decoration:none}code{background:#1f232c;padding:2px 5px;border-radius:4px;word-break:break-all}</style>"
            f"<h1>{html.escape(title)}</h1>{body}")


class TlsServer(ThreadingHTTPServer):

    def __init__(self, address, handler, context) -> None:
        self.context = context if callable(context) else (lambda: context)
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.context().wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def finish_request(self, request, client_address) -> None:
        try:
            request.settimeout(30)
            request.do_handshake()
        except (OSError, ValueError):
            return
        super().finish_request(request, client_address)


def serve(cfg: dict, store, log, opener=None, logins=None, library=None, social=None) -> ThreadingHTTPServer:
    handler = make_handler(cfg, store, log, opener, logins, library, social)
    if cfg.get("web_cert"):
        from . import tls
        return TlsServer((cfg["web_host"], cfg["web_port"]), handler, tls.CertFiles(*cfg["web_cert"], log=log))
    if cfg.get("tls_dir"):
        from . import tls
        return TlsServer((cfg["web_host"], cfg["web_port"]), handler, tls.server_context(Path(cfg["tls_dir"])))
    return ThreadingHTTPServer((cfg["web_host"], cfg["web_port"]), handler)
