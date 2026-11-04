"""Battle.legal_choices (Rust) against ninjask.choices (Python) at every decision of random games."""
import json, random, sys
import ninjask
from ninjask.choices import joint_choices, team_preview_choices
d = ninjask.dex("gen9championsvgc2026regmc")
teams = [l for l in open(sys.argv[1]).read().split("\n") if l]
rng = random.Random(1); same = diff = 0; ex = []
for g in range(int(sys.argv[2])):
    b = ninjask.Battle(d, teams[(3 * g) % len(teams)], teams[(3 * g + 1) % len(teams)], seed=g)
    while not b.ended and b.turn < 150:
        for s in ("p1", "p2"):
            if not b.needs_choice(s):
                continue
            for _ in range(30):
                req = json.loads(b.request(s))
                py = team_preview_choices(req) if req.get("teamPreview") else joint_choices(req, include_ally_targets=True, game_type="doubles")
                rs = b.legal_choices(s)
                if sorted(py) == sorted(rs): same += 1
                else:
                    diff += 1
                    if len(ex) < 3: ex.append((sorted(set(py) - set(rs))[:3], sorted(set(rs) - set(py))[:3]))
                try: b.choose(s, rng.choice(rs)); break
                except ValueError: pass
print(f"decisions compared {same + diff}: identical sets {same}, different {diff}; examples (python-only, rust-only): {ex}")
