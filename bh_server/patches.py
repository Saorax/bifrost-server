from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import threading
import zipfile

SHA = re.compile(r"^[0-9a-f]{64}$")


class Patches:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, int, dict]] = {}

    def _info(self, path: Path) -> dict | None:
        st = path.stat()
        hit = self.cache.get(path.name)
        if hit and hit[0] == st.st_mtime and hit[1] == st.st_size:
            return hit[2]
        try:
            with zipfile.ZipFile(path) as z:
                recipe = json.loads(z.read("recipe.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            return None
        info = {"patch_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": st.st_size,
                "game_version": recipe.get("game_version"), "original_sha256": recipe.get("original_sha256"),
                "mods": recipe.get("mods", []), "made": st.st_mtime, "file": path.name}
        self.cache[path.name] = (st.st_mtime, st.st_size, info)
        return info

    def all(self) -> list[dict]:
        with self.lock:
            if not self.folder.is_dir():
                return []
            infos = [self._info(p) for p in sorted(self.folder.glob("*.bhpatch"))]
            return sorted((i for i in infos if i), key=lambda i: i["made"], reverse=True)

    def latest(self, original_sha: str) -> dict | None:
        if not SHA.match(original_sha or ""):
            return None
        info = next((i for i in self.all() if i["original_sha256"] == original_sha), None)
        if info is None:
            return None
        return {k: info[k] for k in ("patch_sha256", "size", "game_version", "mods")} | {
            "url": f"/bifrost/patch/{info['patch_sha256']}"}

    def path(self, patch_sha: str) -> Path | None:
        if not SHA.match(patch_sha or ""):
            return None
        info = next((i for i in self.all() if i["patch_sha256"] == patch_sha), None)
        return self.folder / info["file"] if info else None
