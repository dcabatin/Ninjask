"""Worlds against the truth.

A world (`Tracker.world`) is the battle a player's view describes, with an
assumption for each foe's hidden set. Given the true sets as assumptions, the
world must be the true battle as far as the player can see it. At each decision
of a tracker check (`scripts/check_tracker.py --world`) the world's projection
is compared with the true battle's, field by field:

- every Pokémon of the world exists in the truth (by name), and the active ones
  stand in the same slots;
- species, max HP, stats, status, fainted, item, ability and moves;
- HP: exact on one's own side; for the foe, the percentage Showdown shows;
- for the active Pokémon: boosts, types, the volatiles (by id), and the turns
  left of those whose length the view can count (Taunt, Encore, Yawn, Magnet
  Rise, Uproar, Syrup Bomb, Heal Block, Throat Chop);
- weather, terrain, Trick Room, Gravity, Magic Room, Wonder Room, Fairy Lock,
  side conditions and slot conditions, with their turns left.

Left out, because no player can see them: the RNG, sleep and confusion
counters, the length of a partial trap (5 or 6) or a rampage, an extending
item's effect on a weather or terrain set this turn, one's own nature and
stat points (the view holds the stats themselves), ability and item states,
the move history (`lastMove`, `attackedBy`, `timesAttacked`, `activeTurns`),
an Illusion not yet broken, and the set shown at team preview for a foe never
brought (it is not in the battle).
"""

from __future__ import annotations

import json
from collections import Counter

TIMED = {"taunt", "encore", "yawn", "magnetrise", "uproar", "syrupbomb", "healblock", "throatchop", "disable"}
# Volatiles whose presence the world can't know (one-turn or bookkeeping ones).
HIDDEN_VOLATILES = {
    "stall", "lockedmove", "choicelock", "twoturnmove", "trapper", "metronome", "gem", "counter", "mirrorcoat",
    "beakblast", "focuspunch", "fling", "sparklingaria", "allyswitch", "chillyreception", "electrify", "lockon",
    "protosynthesis", "quarkdrive", "flinch", "helpinghand", "followme", "ragepowder", "roost", "laserfocus",
    "tarshot", "unburden", "fallen", "attract", "leechseed",
    # Protection lasts the turn: a mid-turn (switch) decision can see it, but it no longer matters.
    "protect", "detect", "kingsshield", "spikyshield", "banefulbunker", "silktrap", "burningbulwark", "obstruct",
    "solarbeam", "solarblade", "electroshot", "meteorbeam", "skyattack", "phantomforce", "fly", "bounce", "dig", "dive",
}


def shared_pct(hp: int, maxhp: int) -> int:
    if hp == 0:
        return 0
    return max(1, 100 * hp // maxhp)


def true_assumptions(view: dict, truth: dict) -> str:
    """Assumptions JSON for `Tracker.world` that equal the truth: per foe
    Pokémon of the view, the true current item, ability, moves, nature and
    stat points; and the Pokémon really brought."""
    me = view["me"]
    foe_view = next(s for s in view["sides"] if s is not view["sides"][0 if me == "p1" else 1])
    foe = next(s for s in truth["sides"] if s["id"] != me)
    by_name = {p["name"]: p for p in foe["pokemon"]}
    foe_out, brought = [], []
    for k, m in enumerate(foe_view["pokemon"]):
        p = by_name.get(m["name"]) if m["name"] else None
        if p is None and not m["name"]:
            # Unseen: the brought Pokémon of its species not otherwise named.
            named = {x["name"] for x in foe_view["pokemon"] if x["name"]}
            cands = [x for x in foe["pokemon"] if x["name"] not in named and same_species(m["preview"], x["baseSpecies"], x["species"])]
            p = cands[0] if cands else None
        if p is None:
            foe_out.append(None)
            continue
        brought.append(k)
        foe_out.append({
            "item": p["item"] or "",
            "ability": p["ability"],
            "moves": [mv["id"] for mv in p["moves"]],
            "nature": p["set"]["nature"],
            "evs": p["set"]["evs"],
        })
    return json.dumps({"foe": foe_out, "brought": brought})


def same_species(preview: str, base: str, species: str) -> bool:
    preview = preview.split("-Mega")[0]
    if preview in (base, species):
        return True
    if preview.endswith("-*"):
        return species.startswith(preview[:-2])
    return species.startswith(preview + "-") or base.startswith(preview)


def compare_world(world: dict, truth: dict, me: str, disguised: set = frozenset(), pp: bool = True) -> list[str]:
    """Differences between a world's projection and the true one, as
    `label: detail` strings. `pp`: compare one's own PP of the active
    Pokémon (only a move request shows them; otherwise the view's are from the
    last one)."""
    problems: list[str] = []
    for ws, ts in zip(world["sides"], truth["sides"]):
        own = ts["id"] == me
        truth_mons = {p["name"]: p for p in ts["pokemon"]}
        world_names = {w["name"] for w in ws["pokemon"]}
        for w in ws["pokemon"]:
            t = truth_mons.get(w["name"])
            if t is None and not own:
                # A foe never seen: its nickname is unknown, its species is not.
                t = next((p for p in ts["pokemon"] if p["name"] not in world_names
                          and same_species(w["species"], p["baseSpecies"], p["species"])), None)
            key = f"{ts['id']}.{w['name']}"
            if (ts["id"], w["name"]) in disguised or (ts["id"], w["name"], "user") in disguised:
                continue
            if t is None:
                problems.append(f"unknown pokemon: {key}")
                continue
            if w["fainted"] != t["fainted"]:
                problems.append(f"fainted: {key} {w['fainted']} vs {t['fainted']}")
                continue
            if t["fainted"]:
                continue
            if w["isActive"] != t["isActive"]:
                problems.append(f"active: {key} {w['isActive']} vs {t['isActive']}")
                continue
            for f in ("species", "maxhp", "status", "item", "ability"):
                if (w[f] or None) != (t[f] or None):
                    problems.append(f"{f}: {key} {w[f]} vs {t[f]}")
            if w["storedStats"] != t["storedStats"]:
                problems.append(f"stats: {key} {w['storedStats']} vs {t['storedStats']}")
            if own and w["hp"] != t["hp"]:
                problems.append(f"hp: {key} {w['hp']} vs {t['hp']}")
            if not own and shared_pct(w["hp"], w["maxhp"]) != shared_pct(t["hp"], t["maxhp"]):
                problems.append(f"hp%: {key} {w['hp']}/{w['maxhp']} vs {t['hp']}/{t['maxhp']}")
            if sorted(m["id"] for m in w["moves"]) != sorted(m["id"] for m in t["moves"]):
                problems.append(f"moves: {key}")
            elif own and pp and t["isActive"]:
                wpp = {m["id"]: m["pp"] for m in w["moves"]}
                if any(wpp[m["id"]] != m["pp"] for m in t["moves"]):
                    problems.append(f"pp: {key}")
            if not t["isActive"]:
                continue
            slot_w = ws["active"].index(w["name"]) if w["name"] in ws["active"] else None
            slot_t = ts["active"].index(t["name"]) if t["name"] in ts["active"] else None
            if slot_w != slot_t:
                problems.append(f"slot: {key} {slot_w} vs {slot_t}")
            if w["boosts"] != t["boosts"]:
                problems.append(f"boosts: {key}")
            if sorted(w["types"]) != sorted(t["types"]):
                problems.append(f"types: {key} {w['types']} vs {t['types']}")
            wv = {k for k in w["volatiles"] if k not in HIDDEN_VOLATILES}
            tv = {k for k in t["volatiles"] if k not in HIDDEN_VOLATILES}
            if wv != tv:
                problems.append(f"volatiles: {key} {sorted(wv ^ tv)}")
            for v in TIMED & wv & tv:
                if w["volatiles"][v].get("duration") != t["volatiles"][v].get("duration"):
                    problems.append(f"{v} turns: {key} {w['volatiles'][v]} vs {t['volatiles'][v]}")
        wc, tc = ws["sideConditions"], ts["sideConditions"]
        if set(wc) != set(tc):
            problems.append(f"side conditions: {ts['id']} {sorted(set(wc) ^ set(tc))}")
        for c in set(wc) & set(tc):
            for f in ("duration", "layers"):
                if wc[c].get(f) != tc[c].get(f):
                    problems.append(f"{c} {f}: {ts['id']} {wc[c]} vs {tc[c]}")
        for i, (a, b) in enumerate(zip(ws["slotConditions"], ts["slotConditions"])):
            # Revival Blessing's lasts only while its player picks whom to revive.
            a, b = set(a) - {"revivalblessing"}, set(b) - {"revivalblessing"}
            if a != b:
                problems.append(f"slot conditions: {ts['id']}{'ab'[i]} {sorted(set(a) ^ set(b))}")
    wf, tf = world["field"], truth["field"]
    for f in ("weather", "terrain"):
        if wf[f] != tf[f]:
            problems.append(f"{f}: {wf[f]} vs {tf[f]}")
        elif wf[f] and wf[f + "State"].get("duration") != tf[f + "State"].get("duration"):
            problems.append(f"{f} turns: {wf[f + 'State']} vs {tf[f + 'State']}")
    wp, tp = wf["pseudoWeather"], tf["pseudoWeather"]
    if set(wp) != set(tp):
        problems.append(f"pseudo weather: {sorted(set(wp) ^ set(tp))}")
    for c in set(wp) & set(tp):
        if wp[c].get("duration") != tp[c].get("duration"):
            problems.append(f"{c} turns: {wp[c]} vs {tp[c]}")
    return problems


def check_world(tracker, truth: dict, me: str, counts: Counter, label: str, request: dict | None = None,
                disguised: set = frozenset()) -> None:
    """Build the world of `tracker` with the true foe sets and count the
    differences from `truth` (a projection) in `counts` (`world_*` keys).
    `request`: the player's current request (PP are compared at move
    requests only)."""
    view = json.loads(tracker.view())
    world = json.loads(tracker.world(true_assumptions(view, truth)).projection())
    counts["world_checks"] += 1
    # The request shows PP at a move request, and not for a locked move (a recharge).
    req = request or {}
    pp = not req.get("forceSwitch") and all("pp" in mv for a in req.get("active", []) for mv in a.get("moves", []))
    for p in compare_world(world, truth, me, disguised, pp=pp):
        counts["world_" + p.split(":")[0]] += 1
        if counts["_world_shown"] < 15:
            counts["_world_shown"] += 1
            print(f"{label} world: {p}")
