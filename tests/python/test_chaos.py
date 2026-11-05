"""Chaos games that once diverged from Showdown (scripts/check_chaos.py), replayed.

Each (seed, game) pair names a game check_chaos.py builds and plays; every one
of these found a bug (Illusion and Reflect Type, Imposter's unknown types,
Instruct and Stalwart, Future Sight and Destiny Bond, Gastro Acid and
residual faints, ...). They must keep matching Showdown input for input.
"""

from __future__ import annotations

import importlib.util

import pytest

pytest.importorskip("ninjask")

from ninjask.paths import ENGINE_ROOT, SHOWDOWN_DIR  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (SHOWDOWN_DIR / "dist").exists(),
    reason="Pokémon Showdown not built (scripts/setup_showdown.sh)",
)

# (seed, game): one per bug found.
GAMES = [
    (1, 41),  # Reflect Type by a disguised Zoroark
    (1, 1033),  # a fainted Pokémon's queued move and Unaware
    (1, 1128),  # Reflect Type on an Imposter ally: types unknown
    (1, 1652),  # Shed Tail clears the newcomer's timesAttacked
    (1, 1766),  # Opportunist's onEnd on fainting
    (1, 1803),  # the type catch-up under a disguise's name
    (1, 2013),  # Iron Ball under Gravity
    (1, 2018),  # Revival Blessing fails after Libero
    (1, 2026),  # Gastro Acid and residual faint messages
    (1, 2094),  # Struggle is typeless
    (1, 2233),  # Instruct and Stalwart's original target
    (1, 2404),  # AfterMove holders with no HP
    (1, 2412),  # Destiny Bond and Future Sight
    (1, 2780),  # Copycat calling Memento: no AfterMove handlers
    (1, 1621),  # Wide Guard's stall: TryAddVolatile
    (1, 1988),  # Follow Me: TryAddVolatile
    (2, 522),  # Future Sight from a user that left: ModifyBoost
    (2, 647),  # Stalwart and Counter
    (2, 965),  # Cloud Nine's End under Gastro Acid
    (2, 1495),  # a status move blocked by a substitute isn't an attack
    (3, 913),  # Illusion's onFaint and Receiver
    (3, 971),  # Baton Pass clears the newcomer's move results
    (3, 986),  # Healing Wish fails after Libero
    (4, 1935),  # Cud Chew under Magic Room
    (9, 3075),  # Simple Beam breaks Illusion
    (9, 3418),  # an ignored White Herb still sorts
    (13, 1171),  # Future Sight on a revived Pokémon not yet sent in
    (13, 3209),  # TryBoost sorts once
    (16, 33),  # Baton Pass doesn't pass on unknown types
    (19, 3654),  # type immunity for a locked Thrash aimed at its own user
    (22, 2330),  # a revived Pokémon in its slot joins the residual sort
    (25, 1385),  # Leftovers given away by Symbiosis still heals that turn
    (28, 1663),  # a revived Pokémon's speed collects no handlers until sent in
    (28, 2496),  # a substitute's damage sees the last activeTarget's Unaware
    (31, 1198),  # Oblivious at 0 HP announces itself but can't end Taunt
    (31, 2086),  # Symbiosis mid-switch-in: the balloon announced for its old holder
    (37, 543),  # Pickup at perish 0: the message, but no item
    (37, 3301),  # Instructed Mirror Coat queues only its before-turn action
]
# (seed, game, mix) from the wider pools.
MIXED = [
    (6, 196, "mixed"),  # Ally Switch: AfterMoveSecondary follows the user
    (7, 3783, "wide"),  # Magic Bounce from a holder mid-Dive
    (8, 939, "mixed"),  # Sheer Force lost to Wandering Spirit: Life Orb recoil
    (8, 2608, "mixed"),  # Flower Veil announces Octolock's drop
    (8, 3306, "mixed"),  # TryHitSide sorts Sap Sipper and Soundproof
    (10, 1620, "wide"),  # Flower Veil before a slower Mirror Armor
    (11, 476, "mixed"),  # ModifyDamage sorts ignored items
    (11, 767, "mixed"),  # Stench's flinch roll on an ally Pollen Puff
    (11, 1126, "mixed"),  # Overcoat defers to a Grass type's powder immunity
    (11, 1628, "mixed"),  # ModifySecondaries: Shield Dust and Parental Bond
    (11, 3136, "mixed"),  # tryTrap's Immunity sort, Magnet Rise and Dive
    (11, 3793, "mixed"),  # no status for a Future Sight user off the field
    (12, 1774, "wide"),  # a partial trap remembers its user off the field
    (14, 877, "mixed"),  # the ??? type never gets STAB (Struggle after Burn Up)
    (14, 3733, "mixed"),  # Burn Up from a non-Fire user doesn't thaw it
    (15, 774, "wide"),  # Seed Sower's terrain mid-residual keeps its 5 turns
    (17, 2999, "mixed"),  # Stench's flinch on Syrup Bomb, past the move's own handler
    (21, 2117, "wide"),  # Shell Side Arm reads boosted stats without ModifyBoost (Unaware)
    (23, 1613, "mixed"),  # a fractional per-hit accuracy keeps no Gravity modifier
    (24, 2128, "wide"),  # a status move's AfterMoveSecondary sort: two Pickpockets
    (24, 2476, "wide"),  # Skill Link lost mid-move: still no per-hit accuracy
    (24, 3874, "wide"),  # Heal Bell and a fainted Good as Gold ally
    (26, 3998, "mixed"),  # Bind ends when its user is revived but not sent in
    (30, 3738, "wide"),  # Teatime's forced eat skips Ripen's TryEatItem
    (32, 2911, "mixed"),  # Symbiosis handlers collected in slot order
    (33, 1967, "wide"),  # Syrup Bomb stays on a holder with no HP
    (33, 2886, "wide"),  # Sheer Cold through No Guard on a Pokémon mid-Fly
    (36, 1128, "wide"),  # Liquid Voice Perish Song into Dry Skin
    (41, 4691, "mixed"),  # no Pressure PP from a fainted Future Sight target
]


@pytest.fixture(scope="module")
def chaos():
    spec = importlib.util.spec_from_file_location("check_chaos", ENGINE_ROOT / "scripts" / "check_chaos.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._init(False)
    return module


@pytest.mark.parametrize(("seed", "game"), GAMES)
def test_chaos_game_matches_showdown(chaos, seed: int, game: int) -> None:
    chaos._worker["mix"] = "chaos"
    _, inputs, kind, detail = chaos.run_game((game, seed))
    assert kind is None, detail
    assert inputs > 0


@pytest.mark.parametrize(("seed", "game", "mix"), MIXED)
def test_wider_chaos_game_matches_showdown(chaos, seed: int, game: int, mix: str) -> None:
    chaos._worker["mix"] = mix
    try:
        _, inputs, kind, detail = chaos.run_game((game, seed))
    finally:
        chaos._worker["mix"] = "chaos"
    assert kind is None, detail
    assert inputs > 0
