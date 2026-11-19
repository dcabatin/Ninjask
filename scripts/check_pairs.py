#!/usr/bin/env python3
"""Pairwise sweep: every pair of the mechanics that interact the most, one on
each side, played against Showdown.

Random teams reach a given pair of rare mechanics once in thousands of games.
Here each unordered pair (A, B) from the pool gets games where player 1's team
holds A and player 2's holds B (both orders over the games), compared with
Showdown after every input as `check_reference.py` does. The pool is
`check_chaos.py`'s moves, abilities and items plus the entities that
`report_coverage.py` finds the random games rarely reach.

    python scripts/check_pairs.py --workers 4 --games-per-pair 1
    python scripts/check_pairs.py --ids instruct,destinybond,revivalblessing  # a small sweep
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import multiprocessing
import multiprocessing.util
import random
import sys
import zlib
from pathlib import Path

import ninjask
from ninjask import focus_teams
from ninjask.refsim import RefSim, seed_from_int

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cr = _load("check_reference")
FORMAT = cr.FORMAT
_w: dict = {}


def _init(seed: int, ids: list[str]) -> None:
    sim = RefSim()
    multiprocessing.util.Finalize(None, sim.close, exitpriority=10)
    _w.update(sim=sim, d=ninjask.dex(FORMAT), teams={}, ids=ids, seed=seed)


def teams_for(i: str) -> list[str]:
    if i not in _w["teams"]:
        try:
            _w["teams"][i] = [t for t in focus_teams(FORMAT, (i,), [], synthetic=6, seed=zlib.crc32(i.encode()) & 0xFFFF) if 4 <= len(t.split("]")) <= 6]
        except Exception:
            _w["teams"][i] = []
    return _w["teams"][i]


def run_pair(job):
    n, a, b = job
    rng = random.Random(f"{_w['seed']}:{a}:{b}:{n}")
    ta, tb = teams_for(a), teams_for(b)
    if not ta or not tb:
        return a, b, "no team", None
    t1, t2 = (rng.choice(ta), rng.choice(tb)) if n % 2 == 0 else (rng.choice(tb), rng.choice(ta))
    try:
        cr.play(_w["sim"], _w["d"], seed_from_int(zlib.crc32(f"{a}:{b}:{n}".encode()), "pair"), t1, t2, rng, False)
        return a, b, None, None
    except cr.Divergence as e:
        return a, b, e.kind, str(e)[:1500] + f"\n  teams (player 1, player 2):\n    {t1}\n    {t2}"
    except Exception as e:  # a crash is a finding
        return a, b, "crash", repr(e)


def default_ids() -> list[str]:
    chaos = _load("check_chaos")
    d = ninjask.dex(FORMAT)
    spec = None
    pools = [chaos.CHAOS_MOVES, chaos.CHAOS_ABILITIES, chaos.CHAOS_ITEMS]
    supported = set(d.implemented_moves()) | set(d.generic_moves()) | set(d.supported_abilities()) | set(d.supported_items())
    ids = [x for p in pools for x in p if x in supported]
    return sorted(set(ids))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ids", help="comma-separated ids instead of the default pool")
    p.add_argument("--extra", help="a file of more ids (one 'kind id,id,...' line each, as report_coverage.py --out writes)")
    p.add_argument("--games-per-pair", type=int, default=1)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--show", type=int, default=5)
    p.add_argument("--shard", default="0/1", help="i/N: play every N-th game starting at i (run shards as separate processes)")
    p.add_argument("--report", help="append each divergence to this file as it happens")
    args = p.parse_args()
    ids = args.ids.split(",") if args.ids else default_ids()
    if args.extra:
        for line in Path(args.extra).read_text().splitlines():
            ids += [x for x in line.split(" ", 1)[1].split(",") if x]
    ids = sorted(set(ids))
    jobs = [(n, a, b) for a, b in itertools.combinations(ids, 2) for n in range(args.games_per_pair)]
    random.Random(args.seed).shuffle(jobs)
    shard, shards = (int(x) for x in args.shard.split("/"))
    jobs = jobs[shard::shards]
    print(f"{len(ids)} ids, {len(jobs)} games", flush=True)
    pool = multiprocessing.Pool(args.workers, _init, (args.seed, ids))
    bad: dict[str, list] = {}
    done = skipped = 0
    for a, b, kind, text in pool.imap_unordered(run_pair, jobs, chunksize=4):
        done += 1
        if kind == "no team":
            skipped += 1
        elif kind:
            bad.setdefault(kind, []).append((a, b, text))
            print(f"DIVERGED {kind}: {a} vs {b}", flush=True)
            if args.report:
                with open(args.report, "a") as f:
                    f.write(f"=== {kind}: {a} vs {b}\n{text}\n\n")
        if done % 2000 == 0:
            print(f"  {done}/{len(jobs)} played, {sum(map(len, bad.values()))} diverged, {skipped} pairs with no team", flush=True)
    print({"games": done, "no team": skipped, "diverged": sum(map(len, bad.values()))})
    for kind, items in sorted(bad.items(), key=lambda kv: -len(kv[1])):
        print(f"{len(items):5d}  {kind}  e.g. {[(a, b) for a, b, _ in items[:4]]}")
    for kind, items in list(bad.items())[: args.show]:
        a, b, text = items[0]
        print(f"\n=== {kind}: {a} vs {b}\n{text}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
