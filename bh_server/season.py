"""seasons of the server's ladders: list them, or end the current one.

    python -m bh_server.season list
    python -m bh_server.season end ["Season 2 name"]

ending a season keeps its final standings (every ladder's ratings and ranks), moves ratings above 1400 halfway back to
it, makes everyone play their placement games again and opens the next season. it's safe while the server runs.
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

from . import stats
from .store import Store

DB = Path(__file__).resolve().parents[1] / "data" / "accounts.sqlite"


def main(argv: list[str], db: Path = DB) -> int:
    if not argv or argv[0] not in ("list", "end"):
        print(__doc__)
        return 2
    store = Store(str(db))
    try:
        ledger = stats.ledger_for(store)
        if argv[0] == "end":
            new = ledger.end_season(" ".join(argv[1:]).strip() or None)
            print(f"season ended; now {new['name']} (id {new['id']})")
        for z in ledger.seasons():
            span = time.strftime("%Y-%m-%d", time.localtime(z["started"])) + " - " + (
                time.strftime("%Y-%m-%d", time.localtime(z["ended"])) if z["ended"] else "now")
            print(f"{z['id']:>3}  {z['name']:<24} {span}" + ("  (current)" if z["current"] else ""))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
