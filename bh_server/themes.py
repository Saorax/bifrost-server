from __future__ import annotations

from .bits import BitWriter
from .frame import Packet

ADMIN_COMMAND = 2551

LIVE = {
    "Default": 1, "Halloween": 6, "KFP": 23, "TMNT": 25, "ATLA": 34, "SpongeBob": 41, "StarWarsMT4": 45,
    "Megaman": 49, "DefaultEpicYumiko": 52, "DefaultEpicArtemis": 53, "StarWarsGrievous": 57, "AscendedNix": 62,
    "AscendedWu": 63, "DefaultEpicCass": 67, "StarWarsLuke": 70, "AscendedMordex": 72, "Shrek": 73,
    "BattlePass14": 75,
}


def resolve(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int) or str(value).strip().isdigit():
        n = int(value)
        return n if n in LIVE.values() else None
    want = str(value).strip().lower()
    for name, n in LIVE.items():
        if name.lower() == want:
            return n
    return None


def name_of(theme: int) -> str:
    return next((k for k, v in LIVE.items() if v == theme), str(theme))


def command_packet(line: str) -> Packet:
    return Packet(ADMIN_COMMAND, BitWriter().string(line).bytes())


def theme_packet(theme: int) -> Packet:
    return command_packet(f"CLIENTTHEME {theme}")


def current(social, cfg: dict | None) -> int:
    chosen = getattr(social, "client_theme", None)
    if chosen is not None:
        return chosen
    return resolve((cfg or {}).get("client_theme")) or 0
