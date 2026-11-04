"""A small learning experiment on Ninjask: how many games does a simple agent need, and where does
the time go while it trains?

The agent is a softmax policy over the legal joint choices. A joint choice's score is the sum of
its two slot actions' linear scores; each slot action is described by a dozen features (base power
times type effectiveness against its target, STAB, status move, Protect, priority, ally target,
switch, Mega Evolution). It learns by REINFORCE with a running baseline, as player 1, against a
player 2 that is either uniformly random or a damage heuristic (--opponent heuristic: the joint
choice with the most base power times type effectiveness against its targets, and a random one 10%
of the time), on random pairs of teams from a pool. Every --eval-every games it plays --eval-games
fixed evaluation games greedily (argmax) against that opponent.

Output: one JSON line per evaluation (games trained, win rate, its 95% Wilson interval) and, at the
end, the share of wall-clock time spent in the engine, the interface (requests, JSON, legal choices,
observations) and the agent (features, scores, updates).

    python research/train_reinforce.py --pool pools/vgcpastes-regmc.txt --games 15000 --seed 0
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import defaultdict

import numpy as np

import ninjask
import ninjask._core
from ninjask.choices import joint_choices, team_preview_choices

FORMAT = "gen9championsvgc2026regmc"
PROTECTS = {"protect", "detect", "spikyshield", "kingsshield", "banefulbunker", "silktrap", "burningbulwark", "obstruct", "wideguard", "quickguard"}
NF = 13  # features per slot action

T = defaultdict(float)


class Clock:
    def __init__(self, key: str):
        self.key = key

    def __enter__(self):
        self.t = time.perf_counter()

    def __exit__(self, *a):
        T[self.key] += time.perf_counter() - self.t


def wilson(k: float, n: int) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p, z = k / n, 1.96
    c = (p + z * z / (2 * n)) / (1 + z * z / n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (c - h, c + h)


class Agent:
    def __init__(self, spec, seed: int, lr: float):
        self.spec = spec
        self.w = np.zeros(NF)
        self.rng = np.random.default_rng(seed)
        self.lr = lr
        self.baseline = 0.0

    def slot_features(self, act: str, slot: int, req: dict, foes: list) -> np.ndarray:
        """Features of one slot's action. `foes` holds the foe's active (species, hp fraction) by slot."""
        f = np.zeros(NF)
        parts = act.split()
        if parts[0] == "switch":
            f[1] = 1.0
            return f
        if parts[0] != "move":
            f[2] = 1.0  # pass, or anything else
            return f
        f[0] = 1.0
        mon = req["side"]["pokemon"][slot]
        moves = req["active"][slot]["moves"]
        mv_id = moves[int(parts[1]) - 1]["id"]
        mv = self.spec.moves.get(mv_id, {})
        mine = self.spec.species.get(ninjask.to_id(mon["details"].split(",")[0]), {}).get("types", [])
        target = int(parts[2]) if len(parts) > 2 and parts[2].lstrip("-").isdigit() else None
        f[7] = 1.0 if "mega" in parts else 0.0
        hp = mon["condition"].split()[0]
        f[11] = (int(hp.split("/")[0]) / max(1, int(hp.split("/")[1]))) if "/" in hp else 0.0
        if mv.get("category") == "Status":
            f[4] = 1.0
            f[9] = 1.0 if mv_id in PROTECTS else 0.0
            return f
        bp = (mv.get("basePower") or 0) / 100.0
        f[5] = 1.0 if mv.get("type") in mine else 0.0
        f[8] = 1.0 if (mv.get("priority") or 0) > 0 else 0.0
        if target is not None and target < 0:
            f[6] = 1.0  # aimed at the ally
            return f
        aimed = [foes[target - 1]] if target and 0 < target <= len(foes) and foes[target - 1] else [x for x in foes if x]
        if not aimed:
            return f
        effs = [self.spec.effectiveness(mv.get("type", "Normal"), self.spec.species.get(sp, {}).get("types", [])) for sp, _ in aimed]
        spread = 0.75 if mv.get("target") in ("allAdjacentFoes", "allAdjacent") and len(aimed) > 1 else 1.0
        f[3] = bp * spread * sum(effs)
        f[10] = sum(math.log2(e) if e > 0 else -3.0 for e in effs) / len(effs)
        f[12] = sum(h for _, h in aimed) / len(aimed)
        return f

    def joint_features(self, legal: list[str], req: dict, foes: list) -> np.ndarray:
        cache: dict = {}
        out = np.zeros((len(legal), NF))
        for i, c in enumerate(legal):
            for slot, act in enumerate(x.strip() for x in c.split(",")):
                key = (slot, act)
                if key not in cache:
                    cache[key] = self.slot_features(act, slot, req, foes)
                out[i] += cache[key]
        return out

    def choose(self, phi: np.ndarray, greedy: bool) -> tuple[int, np.ndarray]:
        s = phi @ self.w
        if greedy:
            return int(np.argmax(s)), None
        p = np.exp(s - s.max())
        p /= p.sum()
        i = int(self.rng.choice(len(p), p=p))
        return i, phi[i] - p @ phi  # the score function's gradient

    def update(self, grads: list, reward: float) -> None:
        if grads:
            adv = reward - self.baseline
            self.w += self.lr * adv * np.sum(grads, axis=0)
        self.baseline += 0.01 * (reward - self.baseline)


def foes_of(tracker) -> list:
    v = json.loads(tracker.view())
    foe = v["sides"][1 if v["me"] == "p1" else 0]["pokemon"]  # sides are always p1, p2
    out = [None, None]
    for m in foe:
        if m.get("slot") is not None and not m.get("fainted") and m["slot"] < 2:
            out[m["slot"]] = (m["species"], (m["hp"] / m["maxhp"]) if m.get("maxhp") else 0.0)
    return out


HEURISTIC = np.zeros(NF)
HEURISTIC[3] = 1.0  # base power x effectiveness against the targets


def play(d, agent: Agent, t1: str, t2: str, seed: int, greedy: bool, rng: random.Random, opponent: str = "random") -> tuple[float, list]:
    with Clock("engine"):
        b = ninjask.Battle(d, t1, t2, seed=seed)
    trs = {"p1": ninjask.Tracker(d, "p1"), "p2": ninjask.Tracker(d, "p2")}
    seen = {"p1": 0, "p2": 0}
    grads: list = []
    while not b.ended and b.turn < 150:
        for s in ("p1", "p2"):
            if not b.needs_choice(s):
                continue
            for _ in range(30):
                with Clock("interface"):
                    req = json.loads(b.request(s))
                    preview = bool(req.get("teamPreview"))
                    legal = team_preview_choices(req) if preview else joint_choices(req, game_type="doubles")
                g = None
                if preview or (s == "p2" and (opponent == "random" or rng.random() < 0.1)):
                    c = rng.choice(legal)
                else:
                    with Clock("interface"):
                        lines = b.log(s, seen[s])
                        seen[s] += len(lines)
                        trs[s].feed(lines)
                        trs[s].feed_battle(b)
                        foes = foes_of(trs[s])
                    with Clock("agent"):
                        phi = agent.joint_features(legal, req, foes)
                        if s == "p2":
                            c = legal[int(np.argmax(phi @ HEURISTIC))]
                        else:
                            i, g = agent.choose(phi, greedy)
                            c = legal[i]
                try:
                    with Clock("engine"):
                        b.choose(s, c)
                    if g is not None:
                        grads.append(g)
                    break
                except ValueError:
                    pass
    w = b.winner_side
    return (1.0 if w == "p1" else -1.0 if w == "p2" else 0.0), grads


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pool", required=True)
    p.add_argument("--games", type=int, default=15000)
    p.add_argument("--eval-every", type=int, default=1000)
    p.add_argument("--eval-games", type=int, default=400)
    p.add_argument("--lr", type=float, default=0.02)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--opponent", choices=["random", "heuristic"], default="random")
    p.add_argument("--train-approx", type=int, default=0, help="train on ninjask.approx switches (a research build); evaluation is always exact")
    args = p.parse_args()
    d = ninjask.dex(FORMAT)
    spec = ninjask.load(FORMAT)
    teams = [line for line in open(args.pool).read().split("\n") if line]
    agent = Agent(spec, args.seed, args.lr)
    rng = random.Random(args.seed)
    erng = random.Random(10_000)
    evals = [(erng.randrange(len(teams)), erng.randrange(len(teams)), 9_000_000 + k) for k in range(args.eval_games)]

    set_approx = getattr(ninjask._core, "set_approx", None)
    if args.train_approx and set_approx is None:
        raise SystemExit("--train-approx needs a build with the approx feature")

    def evaluate(trained: int) -> None:
        if set_approx:
            set_approx(0)
        wins = 0.0
        r = random.Random(12345)
        for i, j, seed in evals:
            res, _ = play(d, agent, teams[i], teams[j], seed, greedy=True, rng=r, opponent=args.opponent)
            wins += 1.0 if res > 0 else 0.5 if res == 0 else 0.0
        lo, hi = wilson(wins, len(evals))
        if set_approx:
            set_approx(args.train_approx)
        print(json.dumps({"seed": args.seed, "opponent": args.opponent, "train_approx": args.train_approx, "games": trained, "win_rate": round(wins / len(evals), 4), "ci95": [round(lo, 4), round(hi, 4)], "w": [round(x, 3) for x in agent.w]}), flush=True)

    t0 = time.perf_counter()
    evaluate(0)
    t_eval = 0.0
    for g in range(1, args.games + 1):
        res, grads = play(d, agent, teams[rng.randrange(len(teams))], teams[rng.randrange(len(teams))], 1_000_000 * (args.seed + 1) + g, greedy=False, rng=rng, opponent=args.opponent)
        with Clock("agent"):
            agent.update(grads, res)
        if g % args.eval_every == 0:
            te = time.perf_counter()
            snapshot = dict(T)
            evaluate(g)
            T.clear()
            T.update(snapshot)
            t_eval += time.perf_counter() - te
    wall = time.perf_counter() - t0 - t_eval
    shares = {k: round(v / wall, 4) for k, v in T.items()}
    shares["other"] = round(1 - sum(shares.values()), 4)
    print(json.dumps({"seed": args.seed, "training_games": args.games, "training_wall_s": round(wall, 1), "ms_per_game": round(1000 * wall / args.games, 3), "time_share": shares}), flush=True)


if __name__ == "__main__":
    main()
