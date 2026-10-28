"""Checks of the protocol tracker against the true battle state.

The game loops behind `scripts/check_tracker.py`: random games in which each
player's tracker reads that player's log lines and requests, and at every
decision the tracker's view is compared with the simulator's projection. One's
own side must match exactly; what is known of the foe must be true (revealed
moves a subset of its moves, revealed item and ability its real ones, HP the
percentage Showdown shows). In engine games a second tracker per player reads
its own side straight from the engine (`feed_battle`, as self-play does) and
must hold the same view as the one fed requests.

Both loops take an `on_decision` callback, called at every decision with the
player's tracker, the request JSON, the counts, a label and (in engine games)
the battle and side, so other checks can run on the same games.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import ninjask

from ninjask import dex as engine_dex
from ninjask import load as load_format
from ninjask import focus_teams, verification_teams
from ninjask.choices import joint_choices
from ninjask.refsim import Battle, RefSim, seed_from_int

FORMAT = "gen9championsvgc2026regmc"
GAME_TYPE = load_format(FORMAT).game_type


# Volatiles Showdown announces in the log, so a player always knows them.
ANNOUNCED = {
    "confusion", "encore", "disable", "taunt", "yawn", "throatchop", "flashfire", "imprison",
    "mustrecharge", "twoturnmove", "glaiverush", "healblock", "substitute", "destinybond", "curse", "attract",
    "stockpile1", "stockpile2", "stockpile3", "charge", "gastroacid", "noretreat", "octolock", "powertrick", "uproar",
    "torment", "syrupbomb", "aquaring", "ingrain", "partiallytrapped", "magnetrise", "smackdown", "saltcure",
    "dragoncheer", "focusenergy", "minimize", "endure",
}

# Volatiles of a fixed length: turns left = length - turns since they started.
LENGTHS = {"magnetrise": 5, "uproar": 3, "syrupbomb": 4}
# Pseudo-weather and side conditions of five turns.
FIELD_LENGTHS = {"gravity": "gravity", "magicroom": "magicRoom", "wonderroom": "wonderRoom"}


CHARGE_MOVES = {"electroshot", "solarbeam", "solarblade", "meteorbeam", "skyattack", "phantomforce",
                "fly", "bounce", "dig", "dive"}


def shared_pct(hp: int, maxhp: int) -> int:
    if hp == 0:
        return 0
    return max(1, 100 * hp // maxhp)


def note_disguises(state: dict, disguised: set) -> None:
    """Record, for each Illusion (`illusion` in the projection), the Pokémon
    it shows and its user: what a player sees of either may be the other's.
    The user is clear again once its Illusion is broken in the log
    (`note_replaced`)."""
    for side in state["sides"]:
        for p in side["pokemon"]:
            # A benched Pokémon keeps its last Illusion: it left disguised.
            if p.get("illusion") and not p["fainted"]:
                disguised.add((side["id"], p["illusion"]))
                disguised.add((side["id"], p["name"], "user"))
            # An Illusion user can faint wearing its disguise, between two
            # decisions, and the log then names the disguise: no player can
            # tell the user is down. It counts as disguised unless a `faint`
            # line named it (`note_replaced` records those).
            elif p["fainted"] and p["ability"] == "illusion" and (side["id"], p["name"], "faint") not in disguised:
                disguised.add((side["id"], p["name"], "user"))


def note_replaced(lines: list[str], disguised: set) -> None:
    """`|replace|` shows the Illusion user for what it is; a `|faint|` line
    naming a Pokémon shows it fainted as itself."""
    for line in lines:
        if line.startswith("|faint|"):
            side, name = line.split("|")[2].split(": ", 1)
            disguised.add((side[:2], name, "faint"))
        if line.startswith("|replace|"):
            side, name = line.split("|")[2].split(": ", 1)
            disguised.discard((side[:2], name, "user"))


def compare_trapped(key: str, m: dict, t: dict, side: dict, foe: dict, foe_view: list, req: dict | None, counts: Counter | None) -> list[str]:
    """The view's `trapped` (can't switch out) against the request of the
    Pokémon's own player (`trapped`, or `maybeTrapped` for a trap Showdown
    hides: Shadow Tag). Locked moves and Struggle have no PP in the request
    and are left out.

    The request says nothing when the side has no one to switch to, so those
    Pokémon are left out. Two explained differences only. (1) The view says
    trapped, the request not: the holder has a Shed Shell (the view's
    `shedShell` says it could). (2) The request says
    trapped or `maybeTrapped` (Showdown hides the trap's cause) and the view
    not: the foe has a Shadow Tag its player has not yet seen."""
    if req is None or req.get("wait") or "active" not in req or m["slot"] is None:
        return []
    if not any(not p["isActive"] and not p["fainted"] for p in side["pokemon"]):
        return []
    acts = req["active"]
    a = acts[m["slot"]] if m["slot"] < len(acts) else None
    if not a or not a.get("moves") or any("pp" not in mv for mv in a["moves"]):
        return []
    sure, hidden = bool(a.get("trapped")), bool(a.get("maybeTrapped"))
    truth = sure or hidden
    derived = m["trapped"]
    if counts is not None:
        counts["trap_checked"] += 1
        counts["trap_true"] += truth
    if derived == truth:
        if derived and counts is not None:
            # What trapped it, for the coverage of a run.
            for v in sorted({"trapped", "ingrain", "noretreat", "partiallytrapped", "octolock"} & set(m["volatiles"])) or ["shadowtag_or_fairylock"]:
                counts["trap_via_" + v] += 1
        return []
    if derived:
        if t["item"] == "shedshell" and m["shedShell"]:
            if counts is not None:
                counts["trap_shedshell_holder"] += 1
            return []
        return [f"{key}: trapped {derived} vs request {a}"]
    seen = {x["name"]: x["ability"] for x in foe_view}
    tag = any(p["ability"] == "shadowtag" and p["isActive"] and not p["fainted"] and seen.get(p["name"]) is None for p in foe["pokemon"])
    if tag:
        if counts is not None:
            counts["trap_hidden_shadowtag"] += 1
        return []
    return [f"{key}: trapped {derived} vs request {a}"]


def compare(view: dict, state: dict, me: str, disguised: set = frozenset(), requests: dict | None = None, counts: Counter | None = None) -> list[str]:
    """Problems with the tracker's view of `state` for player `me`.

    A foe Pokémon an Illusion has disguised as is left out: what the log
    showed of it may have been the Illusion user, which no player can tell
    apart (a faint, moves, HP), so the view can't be held to the truth."""
    problems: list[str] = []
    for side_view, side in zip(view["sides"], state["sides"]):
        own = side["id"] == me
        foe_side = next(x for x in state["sides"] if x is not side)
        truth = {p["name"]: p for p in side["pokemon"]}
        for m in side_view["pokemon"]:
            if not m["name"]:
                continue
            if not own and ((side["id"], m["name"]) in disguised or (side["id"], m["name"], "user") in disguised):
                continue
            t = truth.get(m["name"])
            key = f"{side['id']}.{m['name']}"
            if t is None:
                problems.append(f"{key}: unknown Pokémon")
                continue
            active = t["isActive"] and not t["fainted"]
            slot = side["active"].index(t["name"]) if active and t["name"] in side["active"] else None
            if m["slot"] != slot and not t["fainted"]:
                problems.append(f"{key}: slot {m['slot']} vs {slot}")
            if m["fainted"] != t["fainted"]:
                problems.append(f"{key}: fainted {m['fainted']} vs {t['fainted']}")
            if t["fainted"]:
                continue
            hp = t["hp"] if own else shared_pct(t["hp"], t["maxhp"])
            if m["seen"] and m["hp"] != hp:
                problems.append(f"{key}: hp {m['hp']} vs {hp}")
            if m["seen"] and (m["status"] or None) != t["status"]:
                problems.append(f"{key}: status {m['status']} vs {t['status']}")
            if active and m["boosts"] != [t["boosts"][k] for k in ("atk", "def", "spa", "spd", "spe", "accuracy", "evasion")]:
                problems.append(f"{key}: boosts {m['boosts']} vs {t['boosts']}")
            true_moves = {mv["id"] for mv in t["moves"]}
            seen_moves = {mv["id"] for mv in m["moves"]}
            if own and seen_moves != true_moves:
                problems.append(f"{key}: moves {sorted(seen_moves)} vs {sorted(true_moves)}")
            if not own and not seen_moves <= true_moves:
                problems.append(f"{key}: revealed moves {sorted(seen_moves - true_moves)} not held")
            # Completeness: a move the foe was seen using is known, if it is
            # one of its moves (not Struggle, nor Transform once its moves are
            # the copied ones).
            last = t.get("lastMove")
            if not own and m["seen"] and last and last in true_moves and last not in seen_moves:
                problems.append(f"{key}: last move {t['lastMove']} missed")
            if m["ability"] is not None and m["ability"] != t["ability"]:
                problems.append(f"{key}: ability {m['ability']} vs {t['ability']}")
            if own and m["ability"] is None:
                problems.append(f"{key}: own ability unknown")
            # Open sheets show every set: nothing about the foe is unknown.
            if view.get("openSheets") and not own:
                if m["ability"] is None or m["item"] is None or seen_moves != true_moves:
                    problems.append(f"{key}: sheet not absorbed")
            item = m["item"]
            true_item = t["item"] or ""
            if item is not None and item != true_item:
                problems.append(f"{key}: item {item!r} vs {true_item!r}")
            if own and item is None:
                problems.append(f"{key}: own item unknown")
            # Soak's "typechange" is a message, not a volatile.
            vols = set(m["volatiles"]) - {"typechange"}
            true_vols = set(t["volatiles"])
            # The tracker counts Perish Song down as Showdown announces it:
            # "perish3" from the move until the first residual (duration 4).
            if "perishsong" in true_vols:
                true_vols.add(f"perish{min(t['volatiles']['perishsong']['duration'], 3)}")
            # Stockpile is announced by its layers ("stockpile1" to "stockpile3").
            if "stockpile" in true_vols:
                true_vols.discard("stockpile")
                true_vols.add(f"stockpile{t['volatiles']['stockpile']['layers']}")
            # Supreme Overlord announces its fallen-ally count as a silent
            # "fallenN" start; Showdown keeps the number in the ability state.
            fallen = (t.get("abilityState") or {}).get("fallen")
            if fallen and t["ability"] == "supremeoverlord":
                true_vols.add(f"fallen{fallen}")
            # After the strike, Showdown keeps `twoturnmove` until the residual
            # phase; the Pokémon is charging only while the move's own volatile is on.
            if "twoturnmove" in true_vols and not true_vols & CHARGE_MOVES:
                true_vols.discard("twoturnmove")
            if active and not vols <= true_vols:
                problems.append(f"{key}: volatiles {sorted(vols - true_vols)} not present")
            missed = (true_vols & ANNOUNCED) - vols
            if active and missed:
                problems.append(f"{key}: volatiles {sorted(missed)} missed")
            # Forest's Curse and Trick-or-Treat announce the type they add.
            if active and m.get("addedType") != t.get("addedType"):
                problems.append(f"{key}: added type {m.get('addedType')} vs {t.get('addedType')}")
            if active:
                problems += compare_field_state(key, m, t, own, view["turn"] + view["upkeepDone"])
                if requests is not None:
                    problems += compare_trapped(
                        key, m, t, side, foe_side, next(v for v in view["sides"] if v is not side_view)["pokemon"],
                        requests.get(side["id"]), counts,
                    )
        tconds = {k for k in side["sideConditions"] if k not in ("toxicspikes", "stealthrock")}
        if side_view["stealthRock"] != ("stealthrock" in side["sideConditions"]):
            problems.append(f"{side['id']}: stealth rock {side_view['stealthRock']}")
        vconds = set(side_view["conditions"])
        if tconds != vconds:
            problems.append(f"{side['id']}: side conditions {sorted(vconds)} vs {sorted(tconds)}")
        layers = side["sideConditions"].get("toxicspikes", {}).get("layers", 0)
        if side_view["toxicSpikes"] != layers:
            problems.append(f"{side['id']}: toxic spikes {side_view['toxicSpikes']} vs {layers}")
        layers = side["sideConditions"].get("spikes", {}).get("layers", 0)
        if side_view["spikes"] != layers:
            problems.append(f"{side['id']}: spikes {side_view['spikes']} vs {layers}")
        problems += compare_slots(side["id"], side_view, side, view["turn"])
        if side_view["conditions"].get("safeguard") is not None and "safeguard" in side["sideConditions"]:
            left = 5 - (view["turn"] + view["upkeepDone"] - side_view["conditions"]["safeguard"])
            if left != side["sideConditions"]["safeguard"]["duration"]:
                problems.append(f"{side['id']}: safeguard turns {left} vs {side['sideConditions']['safeguard']}")
    f, tf = view["field"], state["field"]
    for pw, k in FIELD_LENGTHS.items():
        if f[k] is not None and pw in tf["pseudoWeather"]:
            left = 5 - (view["turn"] + view["upkeepDone"] - f[k])
            if left != tf["pseudoWeather"][pw].get("duration"):
                problems.append(f"{pw} turns {left} vs {tf['pseudoWeather'][pw]}")
    if (f["fairyLock"] is not None) != ("fairylock" in tf["pseudoWeather"]):
        problems.append(f"fairy lock {f['fairyLock']} vs {tf['pseudoWeather']}")
    if (f["weather"] or [None])[0] != tf["weather"]:
        problems.append(f"weather {f['weather']} vs {tf['weather']}")
    if (f["terrain"] or [None])[0] != tf["terrain"]:
        problems.append(f"terrain {f['terrain']} vs {tf['terrain']}")
    if (f["trickRoom"] is not None) != ("trickroom" in tf["pseudoWeather"]):
        problems.append(f"trick room {f['trickRoom']} vs {tf['pseudoWeather']}")
    if (f["gravity"] is not None) != ("gravity" in tf["pseudoWeather"]):
        problems.append(f"gravity {f['gravity']} vs {tf['pseudoWeather']}")
    if (f["magicRoom"] is not None) != ("magicroom" in tf["pseudoWeather"]):
        problems.append(f"magic room {f['magicRoom']} vs {tf['pseudoWeather']}")
    if (f["wonderRoom"] is not None) != ("wonderroom" in tf["pseudoWeather"]):
        problems.append(f"wonder room {f['wonderRoom']} vs {tf['pseudoWeather']}")
    return problems


def compare_field_state(key: str, m: dict, t: dict, own: bool, turn: int) -> list[str]:
    """`turn` counts the residual phases: the turn, plus one once this
    turn's upkeep is over. What an active Pokémon's view holds beyond boosts and volatiles: the
    turns its timed volatiles have left, a rampage, Ally Switch's counter,
    a type change, the per-turn flags and Cud Chew. What the view claims
    must be true; one's own flags must also be complete."""
    problems: list[str] = []
    tv = t["volatiles"]
    for v, since in m["volatileSince"].items():
        if v in LENGTHS and v in tv:
            left = LENGTHS[v] - (turn - since)
            if left != tv[v].get("duration"):
                problems.append(f"{key}: {v} turns {left} vs {tv[v].get('duration')}")
        # Partial trapping lasts 5 or 6 turns (random).
        if v == "partiallytrapped" and v in tv:
            left = 5 - (turn - since)
            if tv[v].get("duration") not in (left, left + 1):
                problems.append(f"{key}: partiallytrapped turns {left} vs {tv[v].get('duration')}")
    if m["boundBand"] and "partiallytrapped" in tv and tv["partiallytrapped"].get("boundDivisor") != 6:
        problems.append(f"{key}: binding band {tv['partiallytrapped']}")
    # A rampage seen landing once is on; one's own is read from the request.
    locked = m["locked"]
    if locked is not None and (own or locked[0] == 1) and "lockedmove" not in tv:
        problems.append(f"{key}: locked {locked} not present")
    ally = m["allySwitch"]
    true_ally = tv.get("allyswitch", {}).get("counter")
    if (ally[0] if ally else None) != true_ally:
        problems.append(f"{key}: ally switch {ally} vs {true_ally}")
    types = m["types"]
    if types is not None and sorted(types) != sorted(t["types"]):
        problems.append(f"{key}: types {types} vs {t['types']}")
    flags = [("hurtThisTurn", t.get("hurtThisTurn") is not None), ("statsRaisedThisTurn", bool(t.get("statsRaisedThisTurn"))),
             ("statsLoweredThisTurn", bool(t.get("statsLoweredThisTurn")))]
    for name, truth in flags:
        # A foe's `-damage` line shows even when its HP percentage is unchanged.
        if m[name] != truth:
            problems.append(f"{key}: {name} {m[name]} vs {truth}")
    if t["ability"] == "cudchew" and m["cudChew"] != ("counter" in (t.get("abilityState") or {})):
        problems.append(f"{key}: cud chew {m['cudChew']} vs {t.get('abilityState')}")
    return problems


def compare_slots(side_id: str, side_view: dict, side: dict, turn: int) -> list[str]:
    """Wish, Healing Wish and Future Sight on each active slot."""
    problems: list[str] = []
    for x, (v, tc) in enumerate(zip(side_view["slots"], side.get("slotConditions") or [])):
        for name, held, truth in (("wish", v["wish"] is not None, "wish" in tc), ("healing wish", v["healingWish"], "healingwish" in tc),
                                  ("future sight", v["future"] is not None, "futuremove" in tc)):
            if held != truth:
                problems.append(f"{side_id}{'ab'[x]}: {name} {held} vs {sorted(tc)}")
    return problems


def report(counts: Counter, problem: str) -> None:
    counts[problem.split(":")[-1].split(" vs ")[0].strip()[:60]] += 1
    if counts["_shown"] < 15:
        counts["_shown"] += 1
        print(problem)


def engine_games(
    n: int,
    seed: int,
    ots: bool = False,
    teams: list[str] | None = None,
    require: tuple[str, ...] = (),
    pool: Path | None = None,
    on_decision: Callable | None = None,
) -> Counter:
    """Check `n` engine games; `teams` defaults to `verification_teams(pool)`."""
    d = engine_dex(FORMAT)
    teams = teams or verification_teams(pool)
    focus = focus_teams(FORMAT, require, teams)
    rng = random.Random(seed)
    counts: Counter = Counter()
    for g in range(n):
        b = ninjask.Battle(d, rng.choice(focus), rng.choice(teams), rng.randrange(1 << 30))
        b.set_logging(True)
        if ots:
            b.show_team_sheets()
        trackers = {s: ninjask.Tracker(d, s) for s in ("p1", "p2")}
        direct = {s: ninjask.Tracker(d, s) for s in ("p1", "p2")}
        read = {"p1": 0, "p2": 0}
        disguised: dict[str, set] = {"p1": set(), "p2": set()}
        for _ in range(200):
            if b.ended:
                break
            # Both players' requests as the round begins, for the truth of
            # whether a Pokémon can switch.
            round_requests = {s: json.loads(r) if (r := b.request(s)) else None for s in ("p1", "p2")}
            for s in ("p1", "p2"):
                r = b.request(s)
                if r is None:
                    continue
                lines = b.log(s, read[s])
                read[s] += len(lines)
                trackers[s].feed(lines)
                note_replaced(lines, disguised[s])
                trackers[s].feed_request(r)
                direct[s].feed(lines)
                direct[s].feed_battle(b)
                if direct[s].view() != trackers[s].view():
                    report(counts, f"game {g} {s}: view from the engine differs from the request's")
                req = json.loads(r)
                if req.get("wait"):
                    continue
                # (A player's choice can have advanced the battle since the
                # round began: its requests are then read again.)
                if round_requests[s] != req:
                    round_requests = {o: json.loads(x) if (x := b.request(o)) else None for o in ("p1", "p2")}
                    round_requests[s] = req
                if req.get("teamPreview"):
                    b.choose(s, "team " + "".join(map(str, rng.sample(range(1, len(req["side"]["pokemon"]) + 1), 4))))
                    continue
                if on_decision:
                    on_decision(trackers[s], r, counts, f"game {g} {s}", b, s)
                state = json.loads(b.projection())
                note_disguises(state, disguised[s])
                for p in compare(json.loads(trackers[s].view()), state, s, disguised[s], round_requests, counts):
                    counts[p.split(":")[-1].split(" vs ")[0].strip()] += 1
                    if counts["_shown"] < 15:
                        counts["_shown"] += 1
                        print(f"game {g} turn {state['turn']} {s}: {p}")
                counts["checks"] += 1
                # A hidden Imprison or trap (maybeDisabled, maybeTrapped) is
                # learned from a refused choice, each one revealing a flag in
                # the request, so the player chooses again until one is taken.
                while True:
                    try:
                        b.choose(s, rng.choice(joint_choices(req, game_type=GAME_TYPE)))
                        break
                    except ValueError as e:
                        if not str(e).startswith("[Unavailable choice]"):
                            raise
                        updated = json.loads(b.request(s))
                        if updated == req:
                            raise
                        req = updated
    return counts


def showdown_games(
    n: int,
    seed: int,
    ots: bool = False,
    require: tuple[str, ...] = (),
    pool: Path | None = None,
    on_decision: Callable | None = None,
) -> Counter:
    """Check `n` games played in the reference simulator."""
    d = engine_dex(FORMAT)
    teams = verification_teams(pool)
    focus = focus_teams(FORMAT, require, teams)
    rng = random.Random(seed)
    counts: Counter = Counter()
    with RefSim() as sim:
        for g in range(n):
            battle = Battle(
                sim, FORMAT, seed_from_int(rng.randrange(1 << 30)), ("Player 1", rng.choice(focus)), ("Player 2", rng.choice(teams)), ots=ots
            )
            obs = battle.start()
            trackers = {s: ninjask.Tracker(d, s) for s in ("p1", "p2")}
            requests: dict[str, dict | None] = {"p1": None, "p2": None}
            disguised: dict[str, set] = {"p1": set(), "p2": set()}

            def absorb(o) -> None:
                for s in ("p1", "p2"):
                    trackers[s].feed(o.logs[s])
                    note_replaced(o.logs[s], disguised[s])
                    if o.requests[s]:
                        requests[s] = o.requests[s]
                        trackers[s].feed_request(json.dumps(o.requests[s]))

            absorb(obs)
            for _ in range(300):
                if obs.ended:
                    break
                round_requests = dict(requests)
                for s in ("p1", "p2"):
                    r = requests[s]
                    if not r or r.get("wait"):
                        continue
                    requests[s] = None
                    if round_requests.get(s) is not r:
                        round_requests = {**requests, s: r}
                    if r.get("teamPreview"):
                        obs = battle.choose(s, "team " + "".join(map(str, rng.sample(range(1, len(r["side"]["pokemon"]) + 1), 4))))
                    else:
                        if on_decision:
                            on_decision(trackers[s], json.dumps(r), counts, f"game {g} {s}", None, None)
                        state = battle.state()
                        note_disguises(state, disguised[s])
                        for p in compare(json.loads(trackers[s].view()), state, s, disguised[s], round_requests, counts):
                            counts[p.split(":")[-1].split(" vs ")[0].strip()] += 1
                            if counts["_shown"] < 15:
                                counts["_shown"] += 1
                                print(f"game {g} turn {state['turn']} {s}: {p}")
                        counts["checks"] += 1
                        obs = battle.choose(s, rng.choice(joint_choices(r, game_type=GAME_TYPE)))
                    absorb(obs)
                    if obs.ended:
                        break
            battle.close()
    return counts
