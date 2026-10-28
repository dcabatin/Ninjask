# Adding a move, ability or item

How to port one more Pokémon Showdown mechanic into the engine, and how to
show that the port is right. Commands run from the repository's root with
the Python environment active (see the README's Quick start).

## When a port is done

An entity (move, ability or item) is done when all of these hold:

1. The engine reports it supported. A move maps to a `MoveKind`, an ability to
   an `AbilityKind` and an item to an `ItemKind` (all in `src/kinds/`).
   `src/dex/support.rs` then stops listing it as a problem.
2. `scripts/check_reference.py --require <id>` matches Showdown on every game:
   each log line and the state projection, RNG state included, after every
   input. Run it with `--games 300` or more, and with `--synthetic` mixed in
   (below).
3. `scripts/check_tracker.py --require <id>` passes: the player's view, built
   from the protocol log alone, agrees with the true state.
4. `cargo test --release`, `pytest -q` and a 1,000-game `--synthetic` run
   pass.
5. If Showdown did something non-obvious, add one bullet to the "Reference
   mode" section of `design.md`, in the style of the ones there.

"Close enough" does not count. One mismatched log line or RNG draw fails the
check, and every failure is a porting bug until shown otherwise.

## Sources

- **Authority:** the pinned Showdown checkout in `third_party/pokemon-showdown`
  (`scripts/setup_showdown.sh` fetches it).
  - `data/moves.ts`, `data/abilities.ts`, `data/items.ts` and
    `data/conditions.ts` hold the handlers.
  - `sim/battle.ts` holds `runEvent`, `speedSort` and `comparePriority`.
  - `sim/battle-actions.ts` holds `useMove`, `trySpreadMoveHit` and the hit
    steps.
  - `sim/pokemon.ts` holds `addVolatile`, `setStatus`, `setItem` and the rest
    of the Pokémon methods.
  - `data/mods/champions*` holds the format's overrides, which win over the
    base data.

  Read the handler and every event it hooks. Showdown's ordering (event
  priorities, `subOrder`, speed ties) matters as much as the effect itself.
- **Orientation only:**
  [pokemon-showdown-rs](https://github.com/vjeux/pokemon-showdown-rs) is
  another Rust port of the same simulator. Look at it to see how someone else
  structured a mechanic. Do not copy code from it: port from Showdown and
  verify against Showdown.
- **What the engine already does:** the "Reference mode" section of
  `design.md` lists the ordering subtleties found so far (tie draws,
  `effectOrder`, insertion order of volatiles and side conditions, the
  Champions overrides). A new port usually needs the same tools:
  - `event_sort` for a `runEvent` with several handlers;
  - `try_add_volatile_sort` before adding a volatile;
  - `speed_sort_by` with `ModHandler`;
  - the `added` stamps for insertion order.

## Where code goes

Showdown's events map to modules:

| Showdown | Engine |
| --- | --- |
| `useMove`, hit steps, `getDamage`, multi-hit, two-turn moves | `src/moves/` |
| `onModifyDamage`, `onBasePower`, `onSwitchIn`, residual, immunity, `onUpdate` | `src/events/` |
| Ability and item handlers (`abilities.ts`, `items.ts`) | `src/abilities/`, `src/items/` |
| Weather, terrain, status, volatiles, side and slot conditions | `src/conditions/` |
| Action queue, choices, requests, switching | `src/battle/` |
| State exported to and imported from the harness | `src/projection/` |
| What a player can infer from the protocol log | `src/tracker.rs` |

To add the entity:

1. **Register it.** Add a variant to the right kind enum in `src/kinds/` and
   its `from_id` arm, keyed by the Showdown id.
2. **Write the handler** as an `impl Battle` method in the module above,
   called from the fixed hook point where Showdown would run the event. Cite
   the Showdown function it follows in a comment. Keep the hook at the call
   site to a one-line call.
3. **Add state** the mechanic needs: new `Volatiles` fields, `ResidualVol`
   variants (`RESIDUAL_VOLS` follows the enum, so there is no count to
   update) and `Battle` fields. Reset them where Showdown does (switch-out,
   ability change, end of turn).
4. **Project the state.** Anything that lives in Showdown state must appear in
   the engine's projection (`src/projection/`) and be compared: volatile
   durations, counters, side-condition layers, item and ability states. The
   harness compares Showdown's `project()` (`refsim/server.js`) against
   `Battle.projection()`. When Showdown's projection already includes the
   field, match it; when it does not, extend both sides together.
5. **Teach the tracker** if the entity writes new log lines or reveals hidden
   information (`src/tracker.rs`). The tracker must reach the same view a
   human would build from the log.

Make the same draws as Showdown, in the same order. Showdown also draws for
every speed tie it sorts, not only for damage, accuracy and crits.

## Verify

Rebuild the extension after each change. Do not rebuild while a check runs: it
swaps the module under the check.

```bash
maturin develop --release
python scripts/check_reference.py --require <ids> --games 300 --workers 4
python scripts/check_reference.py --synthetic --games 1000 --first <n> --workers 4
python scripts/check_tracker.py --require <ids> --games 100
python scripts/check_tracker.py --require <ids> --games 100 --showdown
python scripts/verify_engine.py --focus <ids> --cases 100 --n 300
cargo test --release && pytest -q
```

- `--require` takes comma-separated ids. It puts a team using one of them on
  player 1's side: real pool teams if any exist locally, plus up to 200
  synthetic legal teams built around each id.
- `--synthetic` draws random legal teams over everything the engine supports.
  Your new entities are included, and so is everything they might interact
  with.
- Vary `--first` so each run plays new games.
- `verify_engine.py --focus` checks the outcome distributions of the named
  entities with a statistical test, as a second opinion on the exact check.

On a mismatch:

- `--show 1` prints the diverging input with both RNG call sequences and their
  call sites.
- `--verbose` prints every log line.
- Fix, rebuild, rerun the failing game with `--first <index> --games 1`, then
  rerun the full set.

Never weaken a check to get a pass: no new tolerances, no skipped comparisons,
no special-casing an entity in the checker. If the checker itself is wrong, fix
it to match Showdown and say why.

## Local data

Real-team pools are optional; the checks above work on `--synthetic` teams
alone. `scripts/report_support.py` builds a supported-team pool from any list
of packed teams you provide, and `tests/fixtures/regmc_teams.txt` is a small
pool for trying things out.
