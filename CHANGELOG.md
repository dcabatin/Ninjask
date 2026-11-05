# Changelog

## Unreleased

## 0.4.0 (2026-11-04)

A native interface for training (`ninjask.Batch`, player-view legal
choices, numeric observations), experimental exact outcome enumeration
contributed by CalebBrody (dcabatin/Ninjask#8), and `research/` on what
trading parity for speed would buy. Every seeded game gives the same logs,
requests and draws as 0.3.0 except where the two fixes below apply; the
fingerprint is still `6e2f85907f97e4e9`.

Upgrading an editable checkout from before 0.3.0: delete
`python/ninjask/_core.cpython-*.so`, or Python keeps loading it instead of
the new `_core.abi3.so`.

### Added

- **Experimental:** Rust and Python outcome enumeration for search and regret minimization,
  with exhaustive chance probabilities or a damage-endpoint approximation,
  and hashable full battle-state keys.
  (dcabatin/Ninjask#8, by CalebBrody.) Each call explores at most
  `max_paths` chance paths (`DEFAULT_MAX_PATHS`, 20,000, about 0.7 s) and
  raises an error beyond that instead of running for minutes: on 40
  mid-game states of real teams, Precise fit the default budget on 14 and
  DamageEndpoints on 22 (`examples/validate_outcomes.rs`). Where it finished
  it was exact against 28,000 sampled resolutions. The damage-endpoint mode
  is `EnumerationMode.DamageEndpoints` (an approximation). Being
  experimental, the API may change in any release.
- **A native interface for training**, with no JSON and no strings in the
  loop:
  - `Battle.legal_choices(side)` lists the choices the side's request
    offers, in `ninjask.choices`' order, from the player's view: a hidden
    disable or trap never shapes the list, so a listed choice can be
    refused, as in Showdown. `.legal_actions(side)` gives them as int8 rows
    (`action_width(side)` per choice), and `.choose_index(side, i)` submits
    one by position.
  - `Tracker.encode()` and `.encode_ids()` give a player's view as float32
    and int16 bytes for `numpy.frombuffer` (layouts in `src/encode.rs`;
    `ninjask.OBS_LEN`, `IDS_LEN`, `ACTION_FIELDS`); `Dex.vocab()` gives
    the embedding-table sizes.
  - `ninjask.Batch(dex, n)` steps `n` battles together on all cores with
    the GIL released: `observe()` reports every pending decision as
    numeric buffers, and `step()` applies one choice index to each (every
    index is checked before any is applied). In Rust,
    `ninjask::batch::Batch`, `Battle::player_choices` and
    `for_each_player_choice`. About 700 random games/s with every decision
    observed on one core of a 4-vCPU VM, and 1,300 on four; a loop through
    the JSON requests plays about 45.
- `research/`: what trading parity for speed would buy (about 1.2 times
  in the engine, for real changes to play) and how much engine speed
  matters in training. The research-only switches behind it
  (`ninjask::approx`, feature `approx`) are off in every release.

### Fixed

- A Curse chosen before its user becomes a Ghost the same turn (Absol
  Mega Evolving into Absol-Mega-Z) keeps the user as its target, as in
  Showdown, whose queued move keeps the target it was resolved with: its
  onHit then curses a random foe directly, so Good as Gold doesn't stop
  it. The engine aimed it at a foe instead, which Good as Gold blocked.
- Metal Burst and Comeuppance deal 1.5 times an odd damage to a
  substitute unrounded, as in Showdown, which leaves the substitute with
  x.5 HP. Two such hits add up to a whole point a later hit no longer
  has to take, and a drain or recoil from breaking such a substitute is
  rounded from the x.5. The engine rounded the damage down. The
  projection shows the half (`"hp": 30.5`).

### Changed

- Enumerating joint choices and team-preview orders no longer allocates
  per choice; the internal random player is unchanged, and so is the
  fingerprint.

## 0.3.0 (2026-11-04)

Two fixes found by checking the engine on inputs nobody chose (games under
a fresh seed, and teams from public sources); every other seeded game gives
the same logs, requests and draws as 0.2.0, and the fingerprint is still
`6e2f85907f97e4e9`. The checks can now fail, and the wheels cover more of
the Python world.

### Changed

- **Wheels use the stable ABI**: one wheel per platform for CPython 3.12
  and every later version (3.14 included), and new wheels for Linux musl
  (Alpine; x86_64 and aarch64) and Windows on Arm.
- **The checks fail when they find a problem.** `check_reference.py`,
  `check_tracker.py` and `verify_engine.py` exited 0 whatever they found, so
  CI's parity step could not fail; they now exit 1 on a divergence, a
  tracker or world mismatch, or a failed comparison. CI also replays 200
  games of real teams (below).
- `ninjask.__all__` lists the package's public names, so
  `from ninjask import Battle` type-checks under `mypy --strict`.
- pyo3 0.25 → 0.30, which fixes RUSTSEC-2026-0176 and RUSTSEC-2026-0177
  (Ninjask called neither affected API). A crate that embeds Ninjask's
  classes (feature `python`) must build against pyo3 0.30 too.
- `scripts/verify_engine.py` checks Reg M-C by default (it checked the
  Champions doubles custom game); `--format` still picks another.

### Fixed

- Mimicry now changes its holder's type when the terrain ends while the
  holder is at 0 HP with its faint still pending, as Showdown does (the
  `-activate` and `-end typechange` lines were missing). Found by
  `check_reference.py` in 1 of 10,000 synthetic games; the fingerprint is
  unchanged.
- The damage calculation (`Battle::calc`, Python `Battle.damage`) gave Fling
  a base power of 0 (1 or 2 HP a roll) instead of its item's Fling power.
  `damage_check` on 704 teams from public replays found it: 6 of 87,591
  first hits disagreed with play, all Fling. Play itself was right.

### Added

- `pools/vgcpastes-regmc.txt`: 766 Reg M-C tournament teams from the
  [VGCPastes](https://x.com/VGCPastes) Repository, with each team's sheet ID
  and `scripts/build_vgcpastes_pool.py` to rebuild it. The README's
  real-team claims are measured on it (1,000 of 1,000 games; 766 of 766
  supported).
- README: known limitations, the stability promise before 1.0, where the
  Rust quick start's data comes from, legal choices and tracker feeding in
  Python, and speed figures re-measured with their spread and machine.

## 0.2.0 (2026-10-29)

Behaviour is unchanged: every seeded game gives the same logs, requests and
random draws as 0.1.0 (the fingerprint is still `6e2f85907f97e4e9`), except
for the one line noted under Changed.

### Faster

- About 21% fewer instructions per random game on a pool of real Reg M-C
  teams: roughly 7,000 random games per second on one core with logging off,
  and about 250 times Showdown's speed with logging on in both. The turn loop
  allocates far less (short handler and target lists are inline `SmallVec`s,
  a new dependency), event sorts and `eachEvent('Update')` skip work when no
  handler can act or draw, choice generation hoists per-Pokémon checks, and
  the release profile uses fat LTO.
- `ninjask.load()` parses each format export once per process (keyed on the
  file and its modification time), as `dex()` already did. The spec is
  shared, so treat it as read-only.

### Changed

- The open-team-sheets format `gen9championsvgc2026regmcots` reports the
  base format's name, as PKMN Arena and Showdown do, so its `|tier|` line no
  longer ends in "OTS". Its id and forced-open sheets are unchanged.
- LICENSE: the copyright holder's name is corrected to David Cabatingan
  (0.1.0's packages carry a misspelling).

### Added

- `Dex::mega_forme` and `EventHandlers::has_event`. No public type changed.
- `examples/quickstart.rs`, and `--log` for `examples/bench.rs`.
- `refsim/bench_showdown.js`: Showdown's own throughput on the same random
  games, for comparison.
- Documentation: the README reorganised around why to use Ninjask, how, and
  how to contribute; crate-level docs for docs.rs; CONTRIBUTING.md; issue and
  pull request templates.

## 0.1.0

First public release.

- Pokémon Champions VGC doubles (Reg M-C) engine in Rust, matching Pokémon
  Showdown at the pinned commit (`refsim/SHOWDOWN_COMMIT`) log line for log
  line in reference mode, on every supported move, item and ability.
- Python bindings (`ninjask`): `Dex`, `Battle`, `Tracker`, worlds for
  search, the damage and turn-order calculation, and the parity checks.
- The Python package bundles the format exports for Reg M-C (closed and open
  team sheets), Reg M-B and the Champions doubles custom game.
