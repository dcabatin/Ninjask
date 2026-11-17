#!/usr/bin/env python3
"""Choice handling against Showdown: refused, repeated and replaced choices.

check_reference.py plays one accepted choice per side and decision, so the
engine never sees what Showdown does with a choice it refuses, or with a side
that chooses again before the other has chosen. This check does. Per decision
it gives each side a short run of attempts (a legal choice from the request,
a mutated or malformed one, a second choice for a side that has already
chosen) and sends the same attempts to Showdown and the engine, comparing
after every one:

- whether it was accepted, and the error text if not;
- the side's request, when Showdown sent a new one (a refused choice that
  only hidden information ruled out comes with the request updated);
- once a turn runs, the log lines and the full state projection with the RNG
  state, as check_reference.py does.

    python scripts/check_choices.py --games 300 --workers 4
    python scripts/check_choices.py --games 1 --first 17 --verbose

Teams are the pool's, random legal ones and teams built around hidden traps
and Imprison, the cases where a choice can't be undone (Showdown's
`cantUndo`). Exits 1 if any game diverged.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import os
import multiprocessing.util
import random
import re
import sys
from pathlib import Path

import ninjask
from ninjask import focus_teams, supported_test_team, verification_teams
from ninjask import dex as engine_dex
from ninjask.choices import joint_choices, team_preview_choices
from ninjask.refsim import Battle, RefSim, seed_from_int

_spec = importlib.util.spec_from_file_location("check_reference", Path(__file__).resolve().parent / "check_reference.py")
assert _spec and _spec.loader
cr = importlib.util.module_from_spec(_spec)
sys.modules["check_reference"] = cr  # worker processes unpickle Divergence by module name
_spec.loader.exec_module(cr)

FORMAT = cr.FORMAT
Divergence = cr.Divergence

FOCUS = ("imprison", "shadowtag", "meanlook", "block", "fairylock", "whirlpool", "firespin", "sandtomb", "infestation")


def strip_tag(msg: str) -> str:
    """Showdown's "[Invalid choice] " / "[Unavailable choice] " tag, kept as a word."""
    for tag in ("[Invalid choice] ", "[Unavailable choice] "):
        if msg.startswith(tag):
            return tag[1:-9].lower() + ": " + msg[len(tag):]
    return msg


def weird_choice(req: dict, rng: random.Random) -> str:
    """A choice string in the grammar Showdown's `Side.choose` reads, with
    every kind of oddity: spaces, case, modifiers in any order and number,
    wrong kinds for the request, too many or too few parts."""
    names = ["tackle", "Tackle", "protect", "Hidden Power", "struggle", "Struggle", "fakeout", "x", ""]
    if not req.get("teamPreview"):
        for a in req.get("active") or []:
            names += [m["id"] for m in a.get("moves", [])] + [m["move"] for m in a.get("moves", [])]
        side_mons = (req.get("side") or {}).get("pokemon", [])
        mons = [m["ident"].split(": ", 1)[1] for m in side_mons]
    else:
        mons = []

    def part() -> str:
        k = rng.random()
        kind = rng.choice(["move"] * 8 + ["switch"] * 4 + ["pass", "skip", "auto", "default", "shift", "team", "testfight", "undo", "Move", "MOVE", "swap", "", "move1"])
        if kind in ("pass", "skip", "auto", "default", "shift", ""):
            return kind + rng.choice(["", "", "", " 1", " x", " mega"])
        if kind in ("team",):
            return "team " + rng.choice(["1234", "12", "1", "4321", "1,2,3,4", "[1,2,3,4]", "[1, 2]", "1111", "123456", "12345", "0", "9", "", "a", "1 2 3 4", "-1", "[1,2,3,4,5]", "1,2", "1,2,3,4,5,6", " 1234 ", "1.5", "14", "3412"])
        if kind == "switch":
            return "switch " + rng.choice([str(rng.randrange(0, 8)), rng.choice(mons or ["x"]), "", "-1", "1x", " 3", "3 ", "03", "3.0", rng.choice(mons or ["x"]).upper(), "+3"])
        data = rng.choice([str(rng.randrange(0, 6)), rng.choice(names), "", " 1", "1 "])
        tail = ""
        for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
            tail += rng.choice([" 1", " 2", " -1", " -2", " +1", " 3", " -3", " 0", " 01", " mega", " megax", " megay", " zmove", " dynamax", " max", " terastal", " terastallize", " ultra", " gigantamax", "  mega", " 12", " MEGA"])
        sep = rng.choice([" ", " ", " ", "  ", "\t"])
        return kind + sep + data + tail

    n = rng.choice([1, 1, 2, 2, 2, 3, 4])
    if req.get("teamPreview") and rng.random() < 0.3:
        return part() if rng.random() < 0.5 else "team " + rng.choice(["1234", "4321", "12", "[1,2,3,4]", "1,2,3,4", "0", "11", "[1,2,3,4,5]"])
    seps = [", ", ",", " , ", ",,", ", ,"]
    out = part()
    for _ in range(n - 1):
        more = part()
        if more.startswith("team") or out.lstrip().startswith("team"):
            break  # Showdown applies two team parts twice, in a tie-broken order: not worth reproducing
        out += rng.choice(seps) + more
    return rng.choice(["", "", "", " ", "\n"]) + out


def describe(g: "Game", side: str | None = None) -> str:
    log = [l for l in cr.norm(g.eng.log("omniscient", 0)) if l.startswith(("|faint", "|turn", "|move|", "|switch", "|-activate", "|-start"))]
    tail = " ".join(log)[-900:] if os.environ.get("CHOICES_LOG") else ""
    return _describe(g) + ("\n      log: " + tail if tail else "")


def _describe(g: "Game") -> str:
    """The active Pokémon of both sides in Showdown, for a report."""
    out = []
    for sd_side in g.sd.state()["sides"]:
        for mon in sd_side["pokemon"]:
            if mon["isActive"]:
                moves = ",".join(f"{m['id']}{'!' if m['disabled'] else ''}{'(0pp)' if m['pp'] == 0 else ''}" for m in mon["moves"])
                out.append(f"{sd_side['id']} {mon['name']} ({mon['species']}) hp={mon['hp']} ability={mon['ability']} item={mon['item']} status={mon['status']} vol={sorted(mon['volatiles'])} moves=[{moves}]")
    return "\n      ".join(out)


def mutate(choice: str, req: dict, rng: random.Random) -> str:
    """A legal choice made a little wrong, or some other shape of choice."""
    k = rng.random()
    parts = choice.split(", ")
    if k < 0.5:
        return weird_choice(req, rng)
    if k < 0.7:
        i = rng.randrange(len(parts))
        parts[i] = weird_choice(req, rng).split(",")[0]
        return ", ".join(parts)
    if k < 0.8:
        return ", ".join(parts[:1])
    if k < 0.9:
        return choice + ", " + choice.split(", ")[0]
    return choice


class Game:
    """One battle in both simulators, rebuilt from its agreed history when the
    two disagree about a choice (so one game yields many samples)."""

    def __init__(self, sim: RefSim, d, seed: str, t1: str, t2: str) -> None:
        self.sim, self.d, self.seed, self.t1, self.t2 = sim, d, seed, t1, t2
        self.history: list[tuple[str, str]] = []
        self.reset()

    def reset(self) -> None:
        self.sd = Battle(self.sim, FORMAT, self.seed, ("Player 1", self.t1), ("Player 2", self.t2))
        self.eng = ninjask.Battle.reference(self.d, self.t1, self.t2, self.seed)
        self.read = 0
        self.obs = self.sd.start(omniscient=True, trace=False)
        self.requests = dict(self.obs.requests)
        for side, choice in self.history:
            self.obs = self.sd.choose(side, choice, omniscient=True)
            try:
                self.eng.choose(side, choice)
            except ValueError:
                pass
            for s in ("p1", "p2"):
                if self.obs.requests.get(s):
                    self.requests[s] = self.obs.requests[s]
            if self.obs.accepted:
                self.requests[side] = None
                for s in ("p1", "p2"):
                    if self.obs.requests.get(s):
                        self.requests[s] = self.obs.requests[s]
        self.read = 0

    def close(self) -> None:
        self.sd.close()


def play(sim: RefSim, d, seed: str, t1: str, t2: str, rng: random.Random, verbose: bool, found: list, stats: list) -> int:
    """Play one game; every disagreement goes to `found` as (kind, text) and the
    game goes on from the agreed history. Returns the inputs sent."""
    g = Game(sim, d, seed, t1, t2)
    inputs = 0
    try:
        def compare_state(where: str) -> None:
            eng_lines = g.eng.log("omniscient", g.read)
            g.read += len(eng_lines)
            a, b = cr.norm(g.obs.logs["omniscient"]), cr.norm(eng_lines)
            sd_state, eng_state = g.sd.state(), json.loads(g.eng.projection())
            if a != b or sd_state["prng"] != g.eng.rng_seed:
                import difflib
                diff = "\n".join(difflib.unified_diff(a, b, "showdown", "engine", lineterm="", n=1))
                raise Divergence(f"{where}\nlogs differ:\n{diff}\nrng showdown {sd_state['prng']} engine {g.eng.rng_seed}", "log after a choice")
            fields = cr.state_diff(sd_state, eng_state)
            if fields:
                raise Divergence(f"{where}\nstate projections differ:\n  " + "\n  ".join(fields[:20]), "state: " + fields[0].split(":")[0])

        compare_state("start")
        rounds = 0
        while not g.obs.ended and g.obs.turn < 60 and rounds < 400:
            rounds += 1
            acted = False
            for side in ("p1", "p2"):
                req = g.requests.get(side)
                if not req or req.get("wait"):
                    continue
                attempts = rng.choice([1, 1, 2, 3, 4, 6])
                answered = False
                for attempt in range(attempts + 8):
                    req = g.requests.get(side) or req
                    choices = team_preview_choices(req) if req.get("teamPreview") else joint_choices(req, include_ally_targets=True, game_type=cr.GAME_TYPE)
                    choice = rng.choice(choices)
                    forced = attempt >= attempts  # then legal choices until one is accepted
                    if not forced and rng.random() < 0.75:
                        choice = mutate(choice, req, rng)
                    g.read = len(g.eng.log("omniscient", 0))  # compare only what this attempt logs
                    obs = g.sd.choose(side, choice, omniscient=True)
                    g.obs = obs
                    try:
                        g.eng.choose(side, choice)
                        eng_err = None
                    except ValueError as e:
                        eng_err = str(e)
                    where = f"turn {obs.turn} {side} attempt {attempt} {choice!r}"
                    sd_err = obs.errors.get(side) if not obs.accepted else None
                    stats.append(re.sub(r"[0-9]+|\"[^\"]*\"|'[^']*'|: [A-Z][^ ]* [^ ]*", "#", (sd_err or "accepted").replace("[Unavailable choice]", "UNAVAILABLE").replace("[Invalid choice]", ""))[:70])
                    problem = None
                    if bool(obs.accepted) != (eng_err is None):
                        problem = ("acceptance", f"{where}: showdown {'accepted' if obs.accepted else 'refused: ' + str(sd_err)}; engine {'accepted' if eng_err is None else 'refused: ' + eng_err}\n      " + describe(g))
                    elif not obs.accepted and sd_err is None and eng_err.startswith("Hack to avoid"):
                        pass  # `testfight` refused without an error message, as in Showdown
                    elif not obs.accepted:
                        sd_text = (sd_err or "").split("] ", 1)[-1] if (sd_err or "").startswith("[") else (sd_err or "")
                        e_text = (eng_err or "").split("] ", 1)[-1] if (eng_err or "").startswith("[") else (eng_err or "")
                        sd_tag = (sd_err or "")[:12] if (sd_err or "").startswith("[") else ""
                        e_tag = (eng_err or "")[:12] if (eng_err or "").startswith("[") else "[Invalid cho"
                        if sd_text != e_text or sd_tag != e_tag:
                            problem = ("error text", f"{where}:\n    showdown {sd_err!r}\n    engine   {eng_err!r}\n      recent inputs: {g.history[-6:]}\n      " + describe(g))
                    if problem is None and obs.requests.get(side) and not obs.requests[side].get("wait"):
                        mine = g.eng.request(side)
                        theirs = dict(obs.requests[side])
                        mine_j = json.loads(mine) if mine else None
                        if mine_j is not None:
                            mine_j.pop("rqid", None)  # the harness leaves it out of Showdown's
                            theirs.pop("rqid", None)
                        if mine_j != theirs:
                            fields = cr.state_diff(theirs, mine_j)
                            problem = ("request: " + re.sub(r"\[\d+\]", "[]", fields[0].split(":")[0]), f"{where}: request differs:\n  " + "\n  ".join(fields[:6]) + f"\n  recent inputs: {g.history[-6:]}\n      " + describe(g) + f"\n  showdown active {json.dumps((theirs.get('active') or [None])[:1])[:600]}\n  engine   active {json.dumps(((mine_j or {}).get('active') or [None])[:1])[:600]}")
                    if problem is None:
                        try:
                            compare_state(where)
                        except Divergence as e:
                            problem = (e.kind, str(e))
                    if problem is not None:
                        found.append(problem)
                        if len(found) > 60:
                            return inputs
                        # Rebuild both from the agreed history, without this attempt.
                        g.close()
                        g.reset()
                        continue
                    g.history.append((side, choice))
                    if sd_err and "Can't undo" in sd_err and forced:
                        return inputs  # Showdown's cantUndo deadlock, which the engine shares: nothing more to play
                    if sd_err and "It's not your turn" in sd_err:
                        g.requests[side] = None  # a stale request: the side has nothing to choose now
                        answered = True
                        break
                    for s in ("p1", "p2"):
                        if obs.requests.get(s):
                            g.requests[s] = obs.requests[s]
                    if obs.accepted:
                        g.requests[side] = None  # answered, unless the turn ran and sent a new one
                        for s in ("p1", "p2"):
                            if obs.requests.get(s):
                                g.requests[s] = obs.requests[s]
                    inputs += 1
                    if obs.ended:
                        break
                    if obs.accepted and (forced or attempt + 1 >= attempts or rng.random() < 0.5):
                        answered = True
                        break
                if not answered and not g.obs.ended:
                    found.append(("stuck", f"turn {g.obs.turn} {side}: no legal choice was accepted; last error {g.obs.errors.get(side)!r}; request {json.dumps(g.requests.get(side))[:300]}"))
                    return inputs
                acted = True
                if g.obs.ended:
                    break
            if not acted:
                found.append(("stuck", "no side can act"))
                return inputs
        return inputs
    finally:
        g.close()


_worker: dict = {}


def _init_worker(verbose: bool, seed: int) -> None:
    sim = RefSim()
    multiprocessing.util.Finalize(None, sim.close, exitpriority=10)
    pool = verification_teams()
    r = random.Random("synthetic")
    synthetic = [supported_test_team(FORMAT, r) for _ in range(300)]
    focus = [t for t in focus_teams(FORMAT, FOCUS, pool + synthetic, synthetic=200, seed=seed) if len(t.split("]")) == 6]
    _worker.update(sim=sim, dex=engine_dex(FORMAT), teams=pool + synthetic, focus=focus, verbose=verbose)


def run_game(job: tuple[int, int]) -> tuple[int, int, list]:
    n, seed = job
    rng = random.Random(f"{seed}:{n}")
    w = _worker
    pools = [w["teams"], w["focus"] or w["teams"]]
    if os.environ.get("CHOICES_FOCUS"):  # only the teams built around hidden traps and Imprison
        pools = [w["focus"] or w["teams"]]
    t1 = rng.choice(rng.choice(pools))
    t2 = rng.choice(rng.choice(pools))
    found: list = []
    stats: list = []
    try:
        inputs = play(w["sim"], w["dex"], seed_from_int(n, f"choices{seed}"), t1, t2, rng, w["verbose"], found, stats)
    except Divergence as e:
        found.append((e.kind, str(e)))
        inputs = 0
    except Exception as e:  # a crash is a finding too
        import traceback

        found.append(("crash", f"{e}\n{traceback.format_exc()}"))
        inputs = 0
    out = [(k, f"game {n}: {t}\n    teams: {t1} // {t2}" if k in ("crash", "stuck") else f"game {n}: {t}") for k, t in found]
    return n, inputs, out + [("_stat", x) for x in stats]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--first", type=int, default=0)
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--show", type=int, default=2, help="examples to print per kind of disagreement")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()
    jobs = [(n, args.seed) for n in range(args.first, args.first + args.games)]
    if args.workers <= 1:
        _init_worker(args.verbose, args.seed)
        results = map(run_game, jobs)
    else:
        pool = multiprocessing.Pool(args.workers, _init_worker, (args.verbose, args.seed))
        results = pool.imap_unordered(run_game, jobs, chunksize=1)
    kinds: dict[str, list[str]] = {}
    inputs = 0
    bad_games = 0
    seen: dict[str, int] = {}
    for n, i, found in results:
        inputs += i
        bad_games += any(k != "_stat" for k, _ in found)
        for k, text in found:
            if k == "_stat":
                seen[text] = seen.get(text, 0) + 1
            else:
                kinds.setdefault(k, []).append(text)
    print({"games": args.games, "games with a disagreement": bad_games, "inputs": inputs, "disagreements": sum(len(v) for v in kinds.values())})
    print("what Showdown answered, most common first (both simulators agreed on each unless listed below):")
    for text, count in sorted(seen.items(), key=lambda kv: -kv[1])[:400]:
        print(f"  {count:7d}  {text}")
    for k, texts in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
        print(f"\n{len(texts):5d}  {k}")
        for t in texts[: args.show]:
            print("       " + t.replace("\n", "\n       "))
    sys.exit(1 if kinds else 0)


if __name__ == "__main__":
    main()
