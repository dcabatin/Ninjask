#!/usr/bin/env python3
"""Which moves, abilities and items do the fuzzers' games actually exercise?

Plays engine games between random supported teams (`supported_test_team`, the
same generator `check_reference.py --synthetic` uses) and counts from the logs
each move used, ability announced and item activated. Entities seen fewer than
--min times are listed: those are where a difference from Showdown could hide
because no game reached them. Feed them to `check_reference.py --require`.

    python scripts/report_coverage.py --games 20000
"""

from __future__ import annotations

import argparse
import collections
import random
import re

import ninjask
from ninjask import to_id

FORMAT = "gen9championsvgc2026regmc"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--games", type=int, default=5000)
    p.add_argument("--min", type=int, default=30, help="list entities used in fewer games than this")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--focus", action="store_true", help="also play teams built around each entity that is rare")
    p.add_argument("--out", help="append the rare ids, one line per kind, for `check_reference.py --require`")
    args = p.parse_args()
    d = ninjask.dex(FORMAT)
    rng = random.Random(args.seed)
    moves = set(d.implemented_moves()) | set(d.generic_moves())
    abilities, items = set(d.supported_abilities()), set(d.supported_items())
    seen = {k: collections.Counter() for k in ("move", "ability", "item")}

    def play(t1: str, t2: str, seed: int) -> None:
        b = ninjask.Battle(d, t1, t2, seed)
        b.set_logging(True)
        local = {k: set() for k in seen}
        for _ in range(60):
            if b.ended:
                break
            for s in ("p1", "p2"):
                if b.needs_choice(s):
                    for _ in range(30):
                        try:
                            b.choose(s, rng.choice(b.legal_choices(s)))
                            break
                        except ValueError:
                            pass
                    else:
                        b.choose(s, "default")
        # Held by a Pokémon that was sent out (a passive ability never logs).
        sent = {to_id(line.split("|")[2].split(": ", 1)[1]) for line in b.log("spectator", 0) if line.startswith("|switch|")}
        for t in (t1, t2):
            for packed in t.split("]"):
                f = packed.split("|")
                if len(f) > 4 and (to_id(f[0]) in sent or to_id(f[1]) in sent):
                    local["ability"].add(to_id(f[3]))
                    if f[2]:
                        local["item"].add(to_id(f[2]))
        for line in b.log("spectator", 0):
            if line.startswith("|move|"):
                local["move"].add(to_id(line.split("|")[3]))
            for m in re.finditer(r"ability: ([^|\]]+)", line):
                local["ability"].add(to_id(m.group(1)))
            for m in re.finditer(r"item: ([^|\]]+)", line):
                local["item"].add(to_id(m.group(1)))
            if line.startswith(("|-enditem|", "|-item|")):
                local["item"].add(to_id(line.split("|")[3]))
            if line.startswith("|-ability|"):
                local["ability"].add(to_id(line.split("|")[3]))
        for k, v in local.items():
            seen[k].update(v)

    for g in range(args.games):
        play(ninjask.supported_test_team(FORMAT, rng), ninjask.supported_test_team(FORMAT, rng), rng.randrange(2**32))
    if args.focus:
        rare = [x for k, pool in (("move", moves), ("ability", abilities), ("item", items)) for x in pool if seen[k][x] < args.min]
        for k in range(0, len(rare), 2):
            for t in ninjask.focus_teams(FORMAT, tuple(rare[k : k + 2]), [], synthetic=6, seed=k):
                play(t, ninjask.supported_test_team(FORMAT, rng), rng.randrange(2**32))
    for kind, pool in (("move", moves), ("ability", abilities), ("item", items)):
        few = sorted((seen[kind][x], x) for x in pool if seen[kind][x] < args.min)
        print(f"{kind}s: {len(pool) - len(few)} of {len(pool)} seen in at least {args.min} games; "
              f"{sum(1 for c, _ in few if c == 0)} never")
        print("   rarest:", ", ".join(f"{x}={c}" for c, x in few[:60]))
        if args.out:
            with open(args.out, "a") as f:
                f.write(f"{kind} " + ",".join(x for _, x in few) + "\n")


if __name__ == "__main__":
    main()
