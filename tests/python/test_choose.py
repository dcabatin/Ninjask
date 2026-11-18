"""How the engine reads a choice (src/battle/choose_text.rs, a port of Showdown's
`Side.choose`): replacing and refused choices, incomplete and over-long ones, the
request flags that come with them. These need a format export, not Showdown;
the last test replays games of scripts/check_choices.py, which compares the
engine's reading with Showdown's, and needs it."""

import importlib.util
import json
import random

import pytest

ninjask = pytest.importorskip("ninjask")

from ninjask.paths import ENGINE_ROOT, SHOWDOWN_DIR  # noqa: E402

FORMAT = "gen9championsvgc2026regmc"


@pytest.fixture(scope="module")
def d():
    try:
        return ninjask.dex(FORMAT)
    except Exception as e:  # no export and no Showdown to make one
        pytest.skip(f"no {FORMAT} export: {e}")


@pytest.fixture(scope="module")
def teams():
    return ninjask.verification_teams()


def at_moves(d, teams, seed=1, a=0, b=1):
    battle = ninjask.Battle(d, teams[a], teams[b], seed=seed)
    battle.choose("p1", "team 1234")
    battle.choose("p2", "team 1234")
    return battle


def test_a_side_may_choose_again_before_the_other_has(d, teams):
    """Showdown's Cancel Mod: the second choice replaces the first."""
    first = at_moves(d, teams)
    twin = at_moves(d, teams)
    options = first.legal_choices("p1")
    a, b = options[0], options[-1]
    assert a != b
    first.choose("p1", a)
    assert not first.needs_choice("p1")
    first.choose("p1", b)  # accepted
    twin.choose("p1", b)
    for battle in (first, twin):
        battle.choose("p2", "default")
    assert first.state_key() == twin.state_key()
    assert first.log("p1", 0) == twin.log("p1", 0)


def test_a_refused_choice_leaves_the_side_with_none(d, teams):
    battle = at_moves(d, teams)
    battle.choose("p1", battle.legal_choices("p1")[0])
    assert not battle.needs_choice("p1")
    with pytest.raises(ValueError, match="Can't move"):
        battle.choose("p1", "move 9, move 9")
    assert battle.needs_choice("p1")  # Showdown clears the old choice first


def test_team_preview_choice_can_be_replaced_and_refusal_clears_it(d, teams):
    battle = ninjask.Battle(d, teams[0], teams[1], seed=1)
    battle.choose("p1", "team 1234")
    battle.choose("p1", "team 4321")
    assert not battle.needs_choice("p1")
    with pytest.raises(ValueError, match="Team Preview"):
        battle.choose("p1", "team 1177")
    assert battle.needs_choice("p1")
    battle.choose("p1", "team 3412")
    battle.choose("p2", "team 1234")
    assert battle.turn == 1
    # The side's leads are the first two of its last accepted order.
    first_lines = [x for x in battle.log("p1", 0) if x.startswith("|switch|p1")]
    assert "p1a: " + json.loads(battle.projection())["sides"][0]["pokemon"][0]["name"] in first_lines[0]


@pytest.mark.parametrize(
    ("choice", "message"),
    [
        ("move 1 1", "Incomplete choice: move 1 1 - missing other pokemon"),
        ("move 1 1, move 1 1, move 1 1", "You sent choices for 3 Pokémon, but this is a doubles game!"),
        ("move 1 1,", "Unrecognized choice: "),  # the empty second part names no kind of choice
        ("dance", "Unrecognized choice: dance"),
        ("move 1 mega mega", 'Conflicting arguments for "move": 1 mega mega'),
        ("move 1 1 2", 'Conflicting arguments for "move": 1 1 2'),
        ("shift", "Can't shift: You can only shift to the center in triples"),
        ("pass, pass", "Can't pass"),
        ("team 1234", "Can't choose for Team Preview: You're not in a Team Preview phase"),
    ],
)
def test_choices_showdown_refuses(d, teams, choice, message):
    battle = at_moves(d, teams)
    with pytest.raises(ValueError) as e:
        battle.choose("p1", choice)
    assert message in str(e.value)


def test_a_part_may_be_left_to_the_fainted_slot(d, teams):
    """A fainted active Pokémon is passed over (`getChoiceIndex`): one part
    for the Pokémon left is a whole choice, and so is a leading "pass"."""
    rng = random.Random(4)
    seen = 0
    for seed in range(60):
        battle = ninjask.Battle(d, teams[seed % len(teams)], teams[(seed * 7 + 3) % len(teams)], seed=seed)
        while not battle.ended and battle.turn < 40:
            for side in ("p1", "p2"):
                if not battle.needs_choice(side):
                    continue
                request = json.loads(battle.request(side))
                options = battle.legal_choices(side)
                if request.get("active") and any(
                    mon["condition"].endswith("fnt") for mon in request["side"]["pokemon"][:2]
                ):
                    fainted = [i for i, mon in enumerate(request["side"]["pokemon"][:2]) if mon["condition"].endswith("fnt")]
                    pick = rng.choice(options)
                    parts = pick.split(", ")
                    if len(fainted) == 1 and parts[fainted[0]] == "pass":
                        short = parts[1 - fainted[0]]
                        probe = battle.copy()
                        probe.choose(side, short)  # not "Incomplete choice"
                        seen += 1
                try:
                    battle.choose(side, rng.choice(options))
                except ValueError:
                    battle.choose(side, "default")
    assert seen > 0


def test_a_request_is_noCancel_only_when_it_is_alone(d, teams):
    alone = both = 0
    for seed in range(40):
        battle = ninjask.Battle(d, teams[seed % len(teams)], teams[(seed + 11) % len(teams)], seed=seed)
        rng = random.Random(seed)
        while not battle.ended and battle.turn < 30:
            need = [s for s in ("p1", "p2") if battle.needs_choice(s)]
            for side in need:
                request = json.loads(battle.request(side))
                if len(need) == 1:
                    assert request.get("noCancel") is True
                    alone += 1
                else:
                    assert "noCancel" not in request
                    both += 1
            for side in need:
                for _ in range(20):
                    try:  # a hidden disable or trap can refuse a listed choice
                        battle.choose(side, rng.choice(battle.legal_choices(side)))
                        break
                    except ValueError:
                        pass
                else:
                    battle.choose(side, "default")
    assert alone > 0 and both > 0


def test_a_projection_with_no_maxhp_is_refused_not_a_panic(d, teams):
    battle = at_moves(d, teams)
    for field, value in (("maxhp", 0), ("maxhp", 70000), ("maxhp", -1), ("hp", 99999)):
        projection = json.loads(battle.projection())
        projection["sides"][0]["pokemon"][0][field] = value
        with pytest.raises(ValueError):
            ninjask.Battle.from_projection(d, json.dumps(projection), seed=1)
    clean = ninjask.Battle.from_projection(d, battle.projection(), seed=1)
    assert clean.projection() == battle.projection() or clean.turn == battle.turn


# (seed, game) of scripts/check_choices.py: each found a difference from Showdown.
CHOICE_GAMES = [
    (1, 0),  # a second choice replaces the first; wrong kinds of choice; the messages
    (1, 2),  # three parts in doubles; a part for a Pokémon locked into Dig
    (1, 4),  # a single part for two active Pokémon
    (1, 158),  # a Pokémon that fainted this turn shows the disabled flags it had
    (2, 22),  # a fainted Pokémon isn't the last active: no maybeDisabled for it
    (2, 51),  # the first refusal of a disabled move is "Unavailable"
    (2, 17),  # testfight
]


@pytest.fixture(scope="module")
def choices():
    if not (SHOWDOWN_DIR / "dist").exists():
        pytest.skip("Pokémon Showdown not built (scripts/setup_showdown.sh)")
    spec = importlib.util.spec_from_file_location("check_choices", ENGINE_ROOT / "scripts" / "check_choices.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._init_worker(False, 0)
    return module


@pytest.mark.parametrize(("seed", "game"), CHOICE_GAMES)
def test_choice_handling_matches_showdown(choices, seed: int, game: int) -> None:
    choices._init_worker(False, seed)
    _, inputs, found = choices.run_game((game, seed))
    found = [f for f in found if f[0] != "_stat"]  # the rest are statistics of what Showdown answered
    assert not found, found[:3]
    assert inputs > 0


def test_a_ditto_transformed_into_a_mega_has_not_used_the_sides_mega(d):
    """Importing a projection read a Ditto copying a Mega as the side's Mega
    Evolution, so the real Mega Evolution of its teammate was refused."""
    fixture = json.loads((ENGINE_ROOT / "tests" / "fixtures" / "ditto_as_mega.json").read_text())
    battle = ninjask.Battle(d, fixture["t1"], fixture["t2"], fixture["seed"])
    for step in fixture["history"]:
        for side, choice in step:
            battle.choose(side, choice)
    imported = ninjask.Battle.from_projection(d, battle.projection(), seed=1)
    assert json.loads(imported.projection())["sides"][0]["pokemon"] == json.loads(battle.projection())["sides"][0]["pokemon"]
    sheet = [m for m in json.loads(battle.projection())["sides"][0]["pokemon"] if m["canMegaEvo"]]
    assert sheet, "the side can still Mega Evolve"


def test_a_scrambled_log_does_not_crash_the_tracker(d):
    """`clearpoke` moved the Pokémon a switch's snapshot named by position; a
    later `replace` then read past the end. A real log never sends it mid-game."""
    lines = [
        "|poke|p2|Annihilape, L50, F|",
        "|poke|p2|Zoroark-Hisui, L50, F|",
        "|switch|p2a: Noivern|Noivern, L50, F|100/100",
        "|clearpoke",
        "|replace|p2a: Zoroark|Zoroark-Hisui, L50, F",
    ]
    for side in ("p1", "p2"):
        tracker = ninjask.Tracker(d, side)
        tracker.feed(lines)
        json.loads(tracker.view())


def _custom_games(choices, t1, t2, n, tag):
    """Games on fixed teams, each choice sent to Showdown and the engine; returns
    (disagreements, what Showdown answered)."""
    import random as _random

    from ninjask.refsim import RefSim, seed_from_int

    d = ninjask.dex(FORMAT)
    found: list = []
    seen: list = []
    with RefSim() as sim:
        for g in range(n):
            choices.play(sim, d, seed_from_int(g, tag), t1, t2, _random.Random(g), False, found, seen)
    return found, seen


def test_a_struggle_chosen_under_a_hidden_imprison_cannot_be_undone(choices):
    """Every move of the last active is disabled by a foe's unseen Imprison: its
    choice is Struggle, made with `cantUndo`, as Showdown has it."""
    moves = "Imprison,Protect,ShadowBall,Recover"
    t = lambda side: "]".join(f"{side}{i}|Alakazam||innerfocus|{moves}|Timid|||||50|" for i in range(6))  # noqa: E731
    found, seen = _custom_games(choices, t("x"), t("y"), 40, "undo")
    assert not found, found[:2]
    assert any("Can't undo" in s for s in seen)


def test_a_foe_that_lost_shadow_tag_still_makes_a_switch_uncancellable(choices):
    """Showdown marks `maybeTrapped` for a foe whose species may have Shadow Tag
    (here a Mega Gengar after Worry Seed), and a switch chosen then can't be undone."""
    t1 = "]".join(f"x{i}|Alakazam||innerfocus|WorrySeed,SkillSwap,Recover,Psychic|Timid|||||50|" for i in range(6))
    t2 = "]".join(f"y{i}|Gengar|gengarite|cursedbody|ShadowBall,Protect,Recover,Round|Timid|||||50|" for i in range(6))
    found, seen = _custom_games(choices, t1, t2, 60, "gengar")
    assert not found, found[:2]
    assert any("Can't undo" in s for s in seen)


def test_only_one_team_choice_per_input(d, teams):
    battle = ninjask.Battle(d, teams[0], teams[1], seed=1)
    for text in ("default, team 12", " team 12, team 34"):
        with pytest.raises(ValueError, match="Only one team choice per input"):
            battle.choose("p1", text)
        assert battle.needs_choice("p1")
    battle.choose("p1", "default")


def test_an_imported_battle_draws_what_the_original_would(d, teams):
    """The projection carries each Pokémon's cached Speed (Showdown's
    `speed`, which can be stale and which switch-in sorts read), so a battle
    imported at a decision uses the same random draws as the one it came from."""
    rng = random.Random(8)
    checked = 0
    for g in range(24):
        battle = ninjask.Battle(d, teams[g % len(teams)], teams[(g * 5 + 2) % len(teams)], seed=g)
        while not battle.ended and battle.turn < 25:
            need = [s for s in ("p1", "p2") if battle.needs_choice(s)]
            if battle.turn >= 1 and not json.loads(battle.request(need[0])).get("teamPreview"):
                try:
                    imported = ninjask.Battle.from_projection(d, battle.projection(), seed=5)
                except ValueError:
                    imported = None
                if imported is not None:
                    original = battle.copy()
                    for b in (imported, original):
                        b.reseed(77)
                        b.set_rng_trace(True)
                    probe = random.Random(g * 1000 + battle.turn)
                    for side in need:
                        options = original.legal_choices(side)
                        for _ in range(30):
                            pick = probe.choice(options)
                            try:
                                original.choose(side, pick)
                                break
                            except ValueError:
                                pass
                        imported.choose(side, pick)
                    draws = lambda b: [x for x in b.rng_trace() if not x.startswith("#")]  # noqa: E731
                    assert draws(imported) == draws(original), (g, battle.turn)
                    checked += 1
            for side in need:
                for _ in range(30):
                    try:
                        battle.choose(side, rng.choice(battle.legal_choices(side)))
                        break
                    except ValueError:
                        pass
                else:
                    battle.choose(side, "default")
    assert checked > 100
