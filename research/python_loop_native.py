"""The same random self-play loop as python_loop_anatomy.py, through the prototype native interface:
Battle.legal_choices (Rust) and Tracker.encode (floats, no JSON)."""
import random, sys, time
from collections import defaultdict
import ninjask
d = ninjask.dex("gen9championsvgc2026regmc")
teams = [l for l in open(sys.argv[1]).read().split("\n") if l]
games = int(sys.argv[2]); observe = "--no-obs" not in sys.argv
rng = random.Random(0); T = defaultdict(float); N = defaultdict(int)
def tick(k, t0): T[k] += time.perf_counter() - t0; N[k] += 1
t_all = time.perf_counter(); decisions = 0
for g in range(games):
    t0 = time.perf_counter(); b = ninjask.Battle(d, teams[(2 * g) % len(teams)], teams[(2 * g + 1) % len(teams)], seed=g); b.set_logging(observe)
    tr = {s: ninjask.Tracker(d, s) for s in ("p1", "p2")} if observe else {}; seen = {"p1": 0, "p2": 0}; tick("setup", t0)
    while not b.ended and b.turn < 150:
        for s in ("p1", "p2"):
            if not b.needs_choice(s): continue
            decisions += 1
            if observe:
                t0 = time.perf_counter(); lines = b.log(s, seen[s]); seen[s] += len(lines); tr[s].feed(lines); tr[s].feed_battle(b); tick("observation: log + Tracker feed", t0)
                t0 = time.perf_counter(); x = tr[s].encode(); tick("observation: encode() (floats)", t0)
            for _ in range(30):
                t0 = time.perf_counter(); legal = b.legal_choices(s); tick("legal_choices() (Rust)", t0)
                c = rng.choice(legal); t0 = time.perf_counter()
                try: b.choose(s, c); tick("choose()", t0); break
                except ValueError: tick("choose()", t0)
wall = time.perf_counter() - t_all
print(f"{games} games, {decisions} decisions, observations {'on' if observe else 'off'}: {wall:.2f} s = {games / wall:.0f} games/s, {decisions / wall:.0f} decisions/s")
for k, v in sorted(T.items(), key=lambda kv: -kv[1]): print(f"  {k:34s} {100 * v / wall:5.1f}%  {1e6 * v / N[k]:7.1f} us/call")
print(f"  {'other Python':34s} {100 * (wall - sum(T.values())) / wall:5.1f}%")
