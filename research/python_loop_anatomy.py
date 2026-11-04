"""Where the time goes in a Python self-play loop on Ninjask (the README's pattern plus a Tracker
observation per decision). Times each stage with perf_counter over many games."""
import json, random, sys, time
from collections import defaultdict
import ninjask
from ninjask.choices import joint_choices, team_preview_choices

FORMAT = "gen9championsvgc2026regmc"
d = ninjask.dex(FORMAT)
teams = [l for l in open(sys.argv[1]).read().split("\n") if l]
games = int(sys.argv[2]) if len(sys.argv) > 2 else 300
observe = "--no-obs" not in sys.argv
rng = random.Random(0)
T = defaultdict(float); N = defaultdict(int)
def tick(k, t0):
    T[k] += time.perf_counter() - t0; N[k] += 1
t_all = time.perf_counter(); turns = decisions = 0
for g in range(games):
    t0 = time.perf_counter(); b = ninjask.Battle(d, teams[(2 * g) % len(teams)], teams[(2 * g + 1) % len(teams)], seed=g); b.set_logging(observe)
    tr = {s: ninjask.Tracker(d, s) for s in ("p1", "p2")} if observe else {}; seen = {"p1": 0, "p2": 0}; tick("setup (Battle, Tracker)", t0)
    while not b.ended and b.turn < 150:
        for s in ("p1", "p2"):
            if not b.needs_choice(s):
                continue
            decisions += 1
            if observe:
                t0 = time.perf_counter(); lines = b.log(s, seen[s]); seen[s] += len(lines); tr[s].feed(lines); tr[s].feed_battle(b); tick("observation: log + Tracker feed", t0)
                t0 = time.perf_counter(); obs = json.loads(tr[s].view()); tick("observation: view() + json.loads", t0)
            for _ in range(30):
                t0 = time.perf_counter(); text = b.request(s); tick("request() (Rust builds JSON)", t0)
                t0 = time.perf_counter(); req = json.loads(text); tick("json.loads(request)", t0)
                t0 = time.perf_counter(); legal = team_preview_choices(req) if req.get("teamPreview") else joint_choices(req, game_type="doubles"); tick("legal choices (Python)", t0)
                c = rng.choice(legal)
                t0 = time.perf_counter()
                try:
                    b.choose(s, c); tick("choose() (parse + engine step)", t0); break
                except ValueError:
                    tick("choose() (parse + engine step)", t0)
    turns += b.turn
wall = time.perf_counter() - t_all
print(f"{games} games, {turns} turns, {decisions} decisions, observations {'on' if observe else 'off'}: {wall:.2f} s = {games / wall:.0f} games/s, {decisions / wall:.0f} decisions/s")
acc = sum(T.values())
for k, v in sorted(T.items(), key=lambda kv: -kv[1]):
    print(f"  {k:36s} {100 * v / wall:5.1f}%  {1e6 * v / N[k]:7.1f} us/call  ({N[k]} calls)")
print(f"  {'other Python (loop, rng.choice)':36s} {100 * (wall - acc) / wall:5.1f}%")
