from __future__ import annotations

import datetime as dt
import re
import sqlite3
import threading
from pathlib import Path

NAME = re.compile(r"^accounts-(\d{4}-\d{2}-\d{2})\.sqlite$")


def backup_dir(cfg: dict, data: Path) -> Path:
    d = cfg.get("backup_dir")
    return Path(d) if d else data / "backups"


def make(db: Path, dest_dir: Path, today: dt.date | None = None) -> Path:
    today = today or dt.date.today()
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"accounts-{today.isoformat()}.sqlite"
    part = final.with_suffix(".sqlite.part")
    src = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(part)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    part.replace(final)
    return final


def prune(dest_dir: Path, keep_days: int, today: dt.date | None = None) -> list[Path]:
    today = today or dt.date.today()
    gone = []
    if not dest_dir.is_dir():
        return gone
    for f in dest_dir.iterdir():
        m = NAME.match(f.name)
        if not m:
            continue
        try:
            day = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if (today - day).days >= keep_days:
            f.unlink()
            gone.append(f)
    return gone


def due(dest_dir: Path, hour: int, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now()
    return now.hour >= hour and not (dest_dir / f"accounts-{now.date().isoformat()}.sqlite").exists()


def run_once(cfg: dict, data: Path, log) -> Path | None:
    dest = backup_dir(cfg, data)
    try:
        out = make(data / "accounts.sqlite", dest)
        gone = prune(dest, int(cfg.get("backup_keep_days") or 14))
        log(f"backup: {out} ({out.stat().st_size // 1024} kB)" + (f"; removed {len(gone)} older" if gone else ""))
        return out
    except Exception as e:
        log(f"backup failed: {e!r}")
        return None


def start(cfg: dict, data: Path, log, every: float = 600.0) -> threading.Thread:
    hour = int(cfg.get("backup_hour", 4))
    stop = threading.Event()

    def loop() -> None:
        while True:
            if due(backup_dir(cfg, data), hour):
                run_once(cfg, data, log)
            if stop.wait(every):
                return

    t = threading.Thread(target=loop, name="backup", daemon=True)
    t.stop = stop
    t.start()
    return t
