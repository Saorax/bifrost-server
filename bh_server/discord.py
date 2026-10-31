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
