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
    from ninjask.refsim import Battle as RefBattle, RefSim, seed_from_int

    d = ninjask.dex(FORMAT)
    with RefSim() as sim:
        rb = RefBattle(sim, FORMAT, seed_from_int(3, "gengar"), ("p1", t1), ("p2", t2))
        rb.start()
        eb = ninjask.Battle(d, t1, t2, seed=3)
        script = [("p1", "default"), ("p2", "default"), ("p1", "move 1 1, move 1 1"), ("p2", "move 3 mega, move 3")]
        for side, text in script:
            rb.choose(side, text)
            eb.choose(side, text)
        assert eb.turn == 2
        ref = rb.choose("p1", "switch 3, switch 4")
        eb.choose("p1", "switch 3, switch 4")
        assert ref.errors["p1"] is None
        ref = rb.undo("p1")
        assert ref.errors["p1"] and "Can't undo" in ref.errors["p1"]
        with pytest.raises(ValueError, match="Can't undo"):
            eb.undo("p1")


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


def test_a_request_is_the_same_after_the_side_has_chosen(d, teams):
    """Showdown keeps a side's request until the turn runs; only a side with
    nothing to decide gets "wait"."""
    battle = at_moves(d, teams)
    before = battle.request("p1")
    assert json.loads(before).get("wait") is None
    battle.choose("p1", battle.legal_choices("p1")[0])
    assert battle.request("p1") == before
    assert not battle.needs_choice("p1")


@pytest.mark.parametrize(
    "format_id",
    [
        "gen9championsbssregmc",  # singles: Showdown's getRandomTarget draws nothing there
        "gen9championsvgc2026regmb",
        "gen9championsvgc2026regmcots",  # the open team sheets show the gender drawn
    ],
)
def test_other_formats_match_showdown(format_id):
    if not (SHOWDOWN_DIR / "dist").exists():
        pytest.skip("Pokémon Showdown not built (scripts/setup_showdown.sh)")
    import os
    import subprocess
    import sys

    env = {**os.environ, "NINJASK_FORMAT": format_id}
    for script, extra in (("check_reference.py", ["--synthetic"]), ("check_chaos.py", ["--mix", "wide"])):
        done = subprocess.run(
            [sys.executable, str(ENGINE_ROOT / "scripts" / script), "--games", "25", "--workers", "2", "--seed", "5", *extra],
            capture_output=True, text=True, env=env, timeout=900,
        )
        assert done.returncode == 0, done.stdout[-2000:] + done.stderr[-500:]


def test_undo_takes_a_choice_back_and_a_hidden_trap_stops_it(d, teams):
    battle = at_moves(d, teams)
    battle.undo("p1")  # nothing chosen: nothing happens
    battle.choose("p1", battle.legal_choices("p1")[0])
    assert not battle.needs_choice("p1")
    battle.undo("p1")
    assert battle.needs_choice("p1")
    battle.choose("p1", battle.legal_choices("p1")[-1])
    battle.choose("p2", "default")
    assert battle.turn == 2
    battle.undo("p1")  # no request now: nothing to undo, no error


def test_the_chaos_games_other_formats_once_diverged_on(tmp_path):
    """(format, seed, game): a Healing Wish on a Pokémon whose Lum Berry cured Rest
    left Rest's times in the status state, as Showdown does (singles, then in any format)."""
    if not (SHOWDOWN_DIR / "dist").exists():
        pytest.skip("Pokémon Showdown not built (scripts/setup_showdown.sh)")
    import os
    import subprocess
    import sys

    for format_id, seed, game in (("gen9championsbssregmc", 703, 1624), ("gen9championsbssregmc", 703, 1639)):
        done = subprocess.run(
            [sys.executable, str(ENGINE_ROOT / "scripts" / "check_chaos.py"), "--game", str(game), "--seed", str(seed), "--mix", "mixed"],
            capture_output=True, text=True, env={**os.environ, "NINJASK_FORMAT": format_id}, timeout=600,
        )
        assert done.returncode == 0 and "matched" in done.stdout, done.stdout[-1500:]


def _mk(name, species, item, ability, moves):
    return f"{name}|{species}|{item}|{ability}|{moves}|Timid|||||50|"


def _team(*mons):
    return "]".join(_mk(*m) for m in mons)


def test_an_exploding_unaware_user_has_no_handlers_left(choices):
    """Misty Explosion sets its user's HP to 0 before the damage, and a Pokémon with
    no HP has no `onAnyModifyBoost`: its Unaware doesn't ignore the target's stages."""
    t1 = _team(
        ("c", "Clefable", "", "unaware", "MistyExplosion,Protect,Moonlight,IceBeam"),
        ("v", "Vivillon", "lifeorb", "compoundeyes", "Round,Protect,AirSlash,Tailwind"),
        ("a", "Alakazam", "", "innerfocus", "Psychic,Protect,Recover,ShadowBall"),
        ("b", "Absol", "", "pressure", "Protect,Recover,NightSlash,Psychic"),
    )
    t2 = _team(
        ("s", "Slowbro", "", "regenerator", "Amnesia,Protect,Surf,Psychic"),
        ("n", "Noivern", "lifeorb", "infiltrator", "Tailwind,Protect,AirSlash,Uturn"),
        ("g", "Gengar", "", "cursedbody", "Protect,ShadowBall,Hypnosis,Recover"),
        ("h", "Hatterene", "", "magicbounce", "Protect,Psychic,Recover,DazzlingGleam"),
    )
    found, _ = _custom_games(choices, t1, t2, 80, "unaware")
    assert not found, found[:2]


def test_run_away_does_not_clear_what_a_mega_gengars_species_may_have(choices):
    """Showdown compares an ability's name with an id, so a foe's species that may have
    Shadow Tag marks the Pokémon `maybeTrapped` after Run Away has cleared it."""
    t1 = _team(
        ("r", "Rillaboom", "", "overgrow", "DrumBeating,FakeOut,BulkUp,LeafStorm"),
        ("t", "Thievul", "payapaberry", "runaway", "QuickGuard,Attract,DoubleTeam,HyperBeam"),
        ("m", "Manectric", "", "static", "ElectricTerrain,Thunder,Crunch,HyperVoice"),
        ("s", "Steelix", "", "rockhead", "MagnetRise,GigaImpact,HeadSmash,IronHead"),
    )
    t2 = _team(
        ("v", "Vivillon-Jungle", "", "friendguard", "Tailwind,CalmMind,SkitterSmack,EnergyBall"),
        ("g", "Gengar", "gengarite", "cursedbody", "TrickRoom,DestinyBond,Hypnosis,DazzlingGleam"),
        ("k", "Slowking-Galar", "", "owntempo", "PsychUp,WeatherBall,Amnesia,FoulPlay"),
        ("c", "Castform", "focussash", "forecast", "GuardSwap,IcyWind,IceBeam,ChillingWater"),
    )
    found, _ = _custom_games(choices, t1, t2, 80, "runaway")
    assert not found, found[:2]


def test_a_benched_future_sight_users_item_still_joins_the_damage_sort(choices):
    """Two Vivillon formes of one Speed tie, a benched Future Sight user's Life Orb against
    the target's Haban Berry, and Showdown draws to break it."""
    t1 = _team(
        ("a", "Vivillon-Archipelago", "habanberry", "cursedbody", "Round,Protect,AirSlash,Tailwind"),
        ("b", "Sceptile", "shellbell", "keeneye", "Protect,Recover,Slash,Surf"),
        ("c", "Ditto", "silkscarf", "imposter", "Transform"),
        ("d", "Sinistcha", "ironball", "firemane", "Protect,Recover,Psychic,ShadowBall"),
    )
    t2 = _team(
        ("e", "Pyroar", "shedshell", "keeneye", "Protect,Recover,HeatWave,Psychic"),
        ("f", "Arbok", "quickclaw", "friendguard", "Protect,Recover,Crunch,Psychic"),
        ("g", "Vivillon-Savanna", "lifeorb", "armortail", "ForestsCurse,FutureSight,PowerSwap,MysticalFire"),
        ("h", "Krookodile", "lifeorb", "sweetveil", "Haze,Snore,BurningJealousy,EnergyBall"),
    )
    found, _ = _custom_games(choices, t1, t2, 80, "futuresight")
    assert not found, found[:2]
