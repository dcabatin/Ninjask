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
    _, inputs, kind, detail = chaos.run_game((game, seed))
    assert kind is None, detail
    assert inputs > 0
