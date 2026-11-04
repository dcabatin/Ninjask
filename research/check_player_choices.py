"""Battle.legal_choices (Rust, the player's view) against ninjask.choices on the request JSON, at every
decision of random games: the same list in the same order. Half the games are synthetic teams built around
Imprison and trapping abilities, where the engine knows things the player doesn't; a refused choice's
reveal is checked too (the list is recompared after every refusal)."""
import json, random, sys
import ninjask
from ninjask.choices import joint_choices, team_preview_choices
FORMAT = "gen9championsvgc2026regmc"
d = ninjask.dex(FORMAT)
pool = [l for l in open(sys.argv[1]).read().split("\n") if l]
games = int(sys.argv[2])
focus = ninjask.focus_teams(FORMAT, ("imprison", "shadowtag", "meanlook", "block"), [], synthetic=60, seed=3)
rng = random.Random(5)
stats = dict(decisions=0, identical=0, order_only=0, different=0, refusals=0, trapped_shown=0, maybe=0)
examples = []
for g in range(games):
    t1 = focus[g % len(focus)] if g % 2 else pool[(5 * g) % len(pool)]
    t2 = focus[(g * 7 + 1) % len(focus)] if g % 2 else pool[(5 * g + 3) % len(pool)]
    b = ninjask.Battle(d, t1, t2, seed=g)
    while not b.ended and b.turn < 150:
        for s in ("p1", "p2"):
            if not b.needs_choice(s):
                continue
            for _ in range(40):
                req = json.loads(b.request(s))
                py = team_preview_choices(req) if req.get("teamPreview") else joint_choices(req, include_ally_targets=True, game_type="doubles")
                rs = b.legal_choices(s)
                stats["decisions"] += 1
                if any(a.get("maybeDisabled") or a.get("maybeTrapped") for a in req.get("active", [])): stats["maybe"] += 1
                if any(a.get("trapped") for a in req.get("active", [])): stats["trapped_shown"] += 1
                if py == rs: stats["identical"] += 1
                elif sorted(py) == sorted(rs): stats["order_only"] += 1
                else:
                    stats["different"] += 1
                    if len(examples) < 3: examples.append({"game": g, "python_only": sorted(set(py) - set(rs))[:4], "rust_only": sorted(set(rs) - set(py))[:4]})
                c = rng.randrange(len(rs))
                try:
                    b.choose_index(s, c); break
                except ValueError:
                    stats["refusals"] += 1
print(json.dumps(stats)); print("examples:", examples)
