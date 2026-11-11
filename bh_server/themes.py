from __future__ import annotations

from pathlib import Path
import re

from . import gamedata
from .bits import BitWriter
from .frame import Packet

ADMIN_COMMAND = 2551
AUTO = "auto"

LIVE_11_00_49 = {
    "Default": 1, "Halloween": 6, "Anniversary": 21, "KFP": 23, "TMNT": 25, "ATLA": 34, "SpongeBob": 41,
    "StarWarsMT4": 45, "Megaman": 49, "DefaultEpicYumiko": 52, "DefaultEpicArtemis": 53, "StarWarsGrievous": 57,
    "AscendedNix": 62, "AscendedWu": 63, "DefaultEpicCass": 67, "StarWarsLuke": 70, "AscendedMordex": 72,
    "Shrek": 73, "BattlePass14": 75, "AscendedTeros": 76, "AnniversaryNoEvent": 77, "BCXKor": 78,
}

_ROW = re.compile(r'<ClientThemeType\s+ClientThemeName="([^"]*)"\s*>(.*?)</ClientThemeType>', re.S)
_cache: dict[Path, dict[str, int]] = {}


def load(data: Path = gamedata.GAME_DATA) -> dict[str, int] | None:
    try:
        text = (data / "ClientThemeTypes.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    out = {}
    for name, body in _ROW.findall(text):
        tid = re.search(r"<ClientThemeID>\s*(\d+)\s*</ClientThemeID>", body)
        art = re.search(r"<SplashArtTypeName>\s*([^<]*?)\s*</SplashArtTypeName>", body)
        if tid and int(tid.group(1)) > 0 and not (art and art.group(1) == "NotLive"):
            out[name] = int(tid.group(1))
    return dict(sorted(out.items(), key=lambda kv: kv[1])) or None


def live(data: Path = gamedata.GAME_DATA) -> dict[str, int]:
    if data not in _cache:
        _cache[data] = load(data) or dict(LIVE_11_00_49)
    return _cache[data]


def resolve(value, data: Path = gamedata.GAME_DATA) -> int | None:
    if value is None or value == "":
        return None
    themes = live(data)
    if isinstance(value, int) or str(value).strip().isdigit():
        n = int(value)
        return n if n in themes.values() else None
    want = str(value).strip().lower()
    for name, n in themes.items():
        if name.lower() == want:
            return n
    return None


def is_auto(value) -> bool:
    return value is None or str(value).strip().lower() in ("", AUTO)


def name_of(theme: int, data: Path = gamedata.GAME_DATA) -> str:
    if theme == 0:
        return "the game's default"
    return next((k for k, v in live(data).items() if v == theme), str(theme))


def command_packet(line: str) -> Packet:
    return Packet(ADMIN_COMMAND, BitWriter().string(line).bytes())


def theme_packet(theme: int) -> Packet:
    return command_packet(f"CLIENTTHEME {theme}")


def configured(cfg: dict | None, data: Path = gamedata.GAME_DATA) -> int:
    value = (cfg or {}).get("client_theme")
    return 0 if is_auto(value) else resolve(value, data) or 0


def config_problem(cfg: dict | None, data: Path = gamedata.GAME_DATA) -> str | None:
    value = (cfg or {}).get("client_theme")
    if is_auto(value) or resolve(value, data) is not None:
        return None
    return f"client_theme {value!r} isn't a live theme, so the game's default is sent: " + ", ".join(live(data))


def current(social, cfg: dict | None) -> int:
    chosen = getattr(social, "client_theme", None)
    if chosen is not None:
        return chosen
    return configured(cfg)
