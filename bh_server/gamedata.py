from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re

HERE = Path(__file__).resolve().parents[1]
RANDOM_COSTUME = 225


def folder(here: Path = HERE, env=os.environ) -> Path:
    if env.get("BH_GAME_DATA"):
        return Path(env["BH_GAME_DATA"]).resolve()
    for name in ("config.local.json", "config.json"):
        try:
            value = json.loads((here / name).read_text(encoding="utf-8")).get("game_data")
        except (OSError, ValueError, AttributeError):
            value = None
        if value:
            return (here / value).resolve()
    return here / "data" / "game"


GAME_DATA = folder() / "Game"


def default_costume(hero: int, data: Path = GAME_DATA) -> int:
    if hero == 2:
        return RANDOM_COSTUME
    try:
        heroes = (data / "HeroTypes.xml").read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r'<HeroType HeroName="[^"]*">(.*?)</HeroType>', heroes, re.S):
            if re.search(rf"<HeroID>{hero}</HeroID>", m.group(1)):
                name = re.search(r"<CostumeName>([^<]*)</CostumeName>", m.group(1)).group(1)
                with open(data / "costumeTypes.csv", encoding="utf-8", errors="replace", newline="") as f:
                    rows = list(csv.reader(f))
                head = rows[1]
                for row in rows[2:]:
                    if len(row) > head.index("CostumeID") and row[head.index("CostumeName")] == name:
                        return int(row[head.index("CostumeID")])
    except (OSError, AttributeError, ValueError):
        pass
    return 0
