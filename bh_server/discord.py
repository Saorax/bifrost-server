from __future__ import annotations

import json
import urllib.parse
import urllib.request

AUTHORIZE = "https://discord.com/oauth2/authorize"
TOKEN = "https://discord.com/api/oauth2/token"
ME = "https://discord.com/api/users/@me"
GUILDS = "https://discord.com/api/users/@me/guilds"


def wants_guilds(cfg: dict) -> bool:
    return bool(cfg.get("whitelist_discord_servers"))


def redirect_uri(cfg: dict) -> str:
    return cfg["public_url"].rstrip("/") + "/callback"


HTTPS_ONLY_TLDS = {"app", "dev", "page", "new", "day", "foo", "boo", "esq", "mov", "zip", "prof", "nexus", "phd",
                   "rsvp", "meme", "ing", "how", "fly", "eat", "dad", "channel", "soy", "ads", "bank", "insurance"}


def setup_problem(cfg: dict) -> str | None:
    raw = str(cfg.get("public_url") or "")
    url = urllib.parse.urlsplit(raw)
    if url.scheme not in ("http", "https") or not url.hostname:
        return f"public_url {raw!r} isn't an http:// or https:// address"
    try:
        port = url.port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return f"public_url {raw!r} has a port that isn't a number"
    tld = url.hostname.rsplit(".", 1)[-1].lower()
    if url.scheme == "http" and tld in HTTPS_ONLY_TLDS:
        return (f"public_url {raw!r} is http, but browsers only open https:// for .{tld} addresses, so the page they "
                "come back to from Discord fails with ERR_SSL_PROTOCOL_ERROR. Put a reverse proxy with a real "
                "certificate in front of the web port and set public_url to its https:// address (README.md, "
                "Discord sign-in)")
    serves = cfg.get("web_scheme") or ("https" if cfg.get("tls_dir") else "http")
    if cfg.get("behind_proxy") or port != int(cfg.get("web_port") or 0) or url.scheme == serves:
        return None
    return (f"public_url {raw!r} is {url.scheme} on port {port}, but this server serves "
            + ("plain http" if serves == "http" else "https") + " on that port, "
            "so the page browsers come back to from Discord fails"
            + (" with ERR_SSL_PROTOCOL_ERROR" if serves == "http" else "")
            + ". Point public_url at a reverse proxy with a real certificate (another port, such as 443), or set "
            "\"behind_proxy\": true if one already listens there (README.md, Discord sign-in)")


def local_only(cfg: dict) -> bool:
    host = urllib.parse.urlsplit(str(cfg.get("public_url") or "")).hostname or ""
    return host in ("localhost", "127.0.0.1", "::1") or host.endswith(".localhost")


def authorize_url(client_id: str, redirect_uri: str, state: str, guilds: bool = False) -> str:
    q = urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
                                "scope": "identify guilds" if guilds else "identify", "state": state})
    return f"{AUTHORIZE}?{q}"


def _open_json(req, opener) -> dict:
    with opener(req, timeout=15) as r:
        return json.loads(r.read().decode())


def _post_form(url: str, data: dict, opener=urllib.request.urlopen) -> dict:
    return _open_json(urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "bh-private-server"}), opener)


def _get_json(url: str, token: str, opener=urllib.request.urlopen) -> dict:
    return _open_json(urllib.request.Request(url, headers={"Authorization": f"Bearer {token}",
                                                           "User-Agent": "bh-private-server"}), opener)


def user_for_code(code: str, client_id: str, client_secret: str, redirect_uri: str, opener=urllib.request.urlopen,
                  guilds: bool = False) -> dict:
    tok = _post_form(TOKEN, {"client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code",
                             "code": code, "redirect_uri": redirect_uri}, opener)
    me = _get_json(ME, tok["access_token"], opener)
    user = {"id": str(me["id"]), "name": me.get("global_name") or me.get("username") or str(me["id"])}
    if guilds and "guilds" in str(tok.get("scope", "")).split():
        try:
            user["guilds"] = [str(g["id"]) for g in _get_json(GUILDS, tok["access_token"], opener)][:500]
        except Exception:
            user["guilds"] = None
    return user
