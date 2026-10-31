from __future__ import annotations

import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
ID = re.compile(r"^\d{5,25}$")


class Whitelist:
    def __init__(self, cfg: dict, base: Path = HERE) -> None:
        self.ids = {str(i).strip() for i in cfg.get("whitelist") or [] if ID.match(str(i).strip())}
        self.guilds = {str(i).strip() for i in cfg.get("whitelist_discord_servers") or [] if ID.match(str(i).strip())}
        name = cfg.get("whitelist_file")
        self.file = (base / name) if name else None
        self._mtime: float | None = None
        self._file_ids: set[str] = set()

    def _read_file(self) -> set[str]:
        if self.file is None:
            return set()
        try:
            mtime = os.path.getmtime(self.file)
        except OSError:
            self._mtime, self._file_ids = None, set()
            return set()
        if mtime != self._mtime:
            ids = set()
            for line in self.file.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.split("#", 1)[0].strip()
                if ID.match(line):
                    ids.add(line)
            self._mtime, self._file_ids = mtime, ids
        return self._file_ids

    @property
    def on(self) -> bool:
        return bool(self.ids) or bool(self.guilds) or self.file is not None

    def allows(self, account: dict | None) -> bool:
        if not self.on:
            return True
        if not account or not account.get("discord_id"):
            return False
        if str(account["discord_id"]) in (self.ids | self._read_file()):
            return True
        return bool(self.guilds & set(account.get("guilds") or ()))
