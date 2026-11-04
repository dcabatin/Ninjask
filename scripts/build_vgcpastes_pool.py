"""Rebuild pools/vgcpastes-regmc.txt from the VGCPastes Repository.

Downloads the "Champions M-C" tab of the VGCPastes Repository sheet (CSV
export), fetches each team's pokepast.es paste, then packs the pastes and
keeps the teams the pinned Showdown's validator accepts for Reg M-C
(refsim/pack_pastes.js). Pastes are cached in <data>/vgcpastes/, so a rerun
fetches only new teams.

Usage:
    python scripts/build_vgcpastes_pool.py [--out pools/vgcpastes-regmc.txt]
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import time
import urllib.request
from pathlib import Path

from ninjask.paths import DATA_DIR, ENGINE_ROOT, SHOWDOWN_DIR

SHEET = "1axlwmzPA49rYkqXh7zHvAtSP-TKbM0ijGYBPRflLSWw"
TAB = "2001945654"  # Champions M-C
HEADERS = {"User-Agent": "ninjask-pool-builder (https://github.com/dcabatin/Ninjask)"}


def get(url: str) -> bytes:
    return urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=60).read()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, default=ENGINE_ROOT / "pools" / "vgcpastes-regmc.txt")
    args = p.parse_args()
    cache = DATA_DIR / "vgcpastes"
    cache.mkdir(parents=True, exist_ok=True)
    sheet = get(f"https://docs.google.com/spreadsheets/d/{SHEET}/export?format=csv&gid={TAB}").decode("utf-8")
    header = None
    rows = []
    for r in csv.reader(io.StringIO(sheet)):
        if r and r[0] == "Team ID":
            header = r
        elif header and r and r[0].startswith("MC"):
            rows.append(dict(zip(header, r)))
    fetched = 0
    for r in rows:
        path = cache / f"{r['Team ID']}.json"
        if path.exists():
            continue
        paste = json.loads(get(r["Pokepaste"].strip().rstrip("/") + "/json"))
        paste["_sheet"] = {"id": r["Team ID"], "url": r["Pokepaste"].strip()}
        path.write_text(json.dumps(paste))
        fetched += 1
        time.sleep(0.3)
    print(f"{len(rows)} teams in the sheet, {fetched} pastes fetched")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["node", str(ENGINE_ROOT / "refsim" / "pack_pastes.js"), str(SHOWDOWN_DIR), str(cache), str(args.out)], check=True)


if __name__ == "__main__":
    main()
