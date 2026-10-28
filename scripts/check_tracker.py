"""Check the protocol tracker against the true battle state.

Plays random games, feeds each player's tracker that player's log lines and
requests, and at every decision compares the tracker's view with the
simulator's projection: one's own side must match exactly; what is known of
the foe must be true (revealed moves a subset of its moves, revealed item and
ability its real ones, HP the percentage Showdown shows).

In engine games a second tracker per player reads its own side straight from
the engine (`feed_battle`, as self-play does) and must hold the same view as
the one fed requests. The loops are in `ninjask.tracker_check`; the
observation's action masks are checked by `scripts/check_obs.py`.

    uv run python scripts/check_tracker.py --games 200            # engine games
    uv run python scripts/check_tracker.py --games 50 --showdown  # Showdown games
    uv run python scripts/check_tracker.py --games 50 --ots       # open team sheets
    uv run python scripts/check_tracker.py --games 50 --showdown --require magicbounce
    uv run python scripts/check_tracker.py --games 200 --world      # worlds (engine games)

`--world` also builds, at every decision, the world of the player's view with
the foe's true sets as assumptions (`Tracker.world`) and compares its
projection with the true battle's (`ninjask.world_check`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ninjask.tracker_check import engine_games, note_disguises, showdown_games
from ninjask.world_check import check_world


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--games", type=int, default=100)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--showdown", action="store_true", help="check on Showdown's logs instead of the engine's")
    p.add_argument("--ots", action="store_true", help="open team sheets")
    p.add_argument("--require", default="", help="comma-separated move, item or ability ids player 1's team must use one of")
    p.add_argument("--world", action="store_true", help="also check worlds built from the views (engine games)")
    p.add_argument("--pool", type=Path, help="team pool: a file or a directory of supported-*.txt (default: ninjask.default_pool())")
    args = p.parse_args()
    run = showdown_games if args.showdown else engine_games
    if args.world and args.showdown:
        p.error("--world runs on engine games")

    def world(tracker, request, counts, label, battle, side):
        truth = json.loads(battle.projection())
        disguised: set = set()
        note_disguises(truth, disguised)
        check_world(tracker, truth, side, counts, label, json.loads(request), disguised)

    counts = run(args.games, args.seed, ots=args.ots, require=tuple(r for r in args.require.split(",") if r), pool=args.pool,
                 on_decision=world if args.world else None)
    print(dict(counts))


if __name__ == "__main__":
    main()
