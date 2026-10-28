# Ninjask

[![CI](https://github.com/dcabatin/Ninjask/actions/workflows/ci.yml/badge.svg)](https://github.com/dcabatin/Ninjask/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ninjask)](https://pypi.org/project/ninjask/)
[![crates.io](https://img.shields.io/crates/v/ninjask)](https://crates.io/crates/ninjask)

A fast simulator of Pokémon Champions VGC doubles (Regulation M-C), written in
Rust with Python bindings. Named after Ninjask, a famously fast Pokémon. It reproduces [Pokémon Showdown](https://github.com/smogon/pokemon-showdown)
exactly, and is built to run self-play, search and team evaluation at
thousands of battles per second per CPU core.

## Scope

- **Format.** Champions doubles under the Reg M-C rules: bring 6, pick 4,
  Mega Evolution, no Terastallization, the Champions stat-point system, and its
  changed status and damage mechanics. Other regulations (M-A, M-B, and later
  ones) are data changes: the rules, species, moves, items and abilities are
  loaded from a JSON export of Showdown's data, not compiled in.
- **Parity.** For the entities it supports, the engine matches one pinned
  Showdown commit (`refsim/SHOWDOWN_COMMIT`) exactly. Run in *reference mode*, it
  uses Showdown's own random number generator and makes the same draws in
  the same order, so a seeded game gives the same log lines and the same state
  after every input. The normal mode uses a faster generator
  (xoshiro256++); its outcome *distributions* are checked against Showdown's.
- **Honest coverage.** Every move, item and ability is *generic* (fully
  described by its data fields), *implemented* (ported from Showdown's code),
  or *unsupported*. The engine never silently simulates an effect it lacks:
  `Dex.team_problems(team)` lists what a team uses that is unsupported, and
  `scripts/report_support.py` reports which entities block the most teams.
- **Not included.** No agent, no search, no UI. It is a simulator, with the
  tools to prove it matches the reference.

## Install

```sh
pip install ninjask      # Python 3.12+: the engine, with the Reg M-C formats bundled
cargo add ninjask        # Rust
```

The Python package bundles the format exports from the pinned Showdown
(`gen9championsvgc2026regmc`, its open-team-sheets variant
`gen9championsvgc2026regmcots`, `gen9championsvgc2026regmb` and
`gen9championsdoublescustomgame`) and a few test teams, so it plays battles
with no Showdown checkout; see [Use from Python](#use-from-python). The Rust
crate loads a format from its JSON export (`Dex::from_json`), which you take
from the Python package (`ninjask/data/formats/`) or generate from a checkout.
The parity checks, and exporting other formats, need the source checkout and
Showdown: see Quick start.

## How parity is verified

Showdown at the pinned commit is the specification. Four checks compare the
engine with it; all need Node.js and the Showdown checkout (see Quick start).

| Check | What it compares |
| --- | --- |
| `scripts/check_reference.py` | Reference mode against Showdown, game by game. Both simulators get the same seed and the same choices. After every input it compares each log line, each side's request and the full state projection, the RNG state included. On a divergence it prints both sequences of RNG draws with their call sites. |
| `cargo run --release --example fingerprint` | A hash of the logs, requests and RNG draws of 2000 seeded engine games. It must not change in a refactor that preserves behaviour. |
| `scripts/verify_engine.py` | Outcome distributions. At sampled decision points the Showdown state is imported into the engine and one joint choice is played N times in each simulator. A χ² test of homogeneity per feature (HP, status, boosts, volatiles, fainting, field, the winner) plus an exact-support check, with false-discovery-rate control across all comparisons. `--focus ids` concentrates on named moves, abilities and items. |
| `scripts/check_tracker.py` | The protocol tracker (below) against the true state: a player's view of its own side matches exactly, and everything it believes about the foe is true. It runs on engine games, on Showdown games (`--showdown`) and with open team sheets (`--ots`). |

The reference and tracker checks run on a pool of real-looking teams and on
`--synthetic` teams: random legal teams built over every supported move,
ability and item, so entities no popular team uses are exercised too.

The tests (`cargo test`, `pytest`) cover units of the same machinery: the RNG
against Node's output, the choice syntax, format loading and the reference
harness on a few games.

## Quick start

Requirements: Python 3.12 or later, Rust (stable), Node.js 22 or later, git.

```sh
git clone https://github.com/dcabatin/Ninjask.git
cd Ninjask
python3 -m venv .venv
source .venv/bin/activate

# The package, with its compiled module built from the crate (optimised)
pip install -e '.[test]'

# Pokémon Showdown at the pinned commit, cloned and built into third_party/
./scripts/setup_showdown.sh

# The Reg M-C format, exported from Showdown to data/derived/format/
python -c "import ninjask; ninjask.load('gen9championsvgc2026regmc')"

# Rust and Python tests
cargo test --release
pytest -q
```

`import ninjask` then works from anywhere in that environment. The install is
editable because the checks below drive Showdown through `refsim/` and
`scripts/`, which live in the checkout; after changing Rust code, rebuild with
`pip install -e .` again (or `maturin develop --release`). The Rust tests
that play battles read the format export and skip without it; the rest of
`cargo test --release` needs neither Python nor Showdown. The Python tests
that need Showdown skip themselves when it is not built.

The engine loads a format from a JSON export of Showdown's data,
`data/derived/format/<format>.json`, which `ninjask.dex(format)` and
`ninjask.load(format)` generate from the Showdown checkout on first use. One
exported format is derived rather than Showdown's own:
`gen9championsvgc2026regmcots` (PKMN Arena's open-team-sheets Reg M-C) is the
base format with its sheet rule forced open, built by `refsim/export_format.js`;
its `FormatSpec` reports `open_team_sheets` and the base format it derives from.

### Run the checks

```sh
# Exact parity with Showdown, on 1000 seeded games of synthetic teams
# (4 processes, each with its own Showdown)
python scripts/check_reference.py --synthetic --games 1000 --workers 4

# Outcome distributions, tracker, support report
python scripts/verify_engine.py --cases 100 --n 300
python scripts/check_tracker.py --games 100
python scripts/report_support.py --pool tests/fixtures/regmc_teams.txt

# Throughput and fingerprint
cargo run --release --example bench -- data/derived/format/gen9championsvgc2026regmc.json tests/fixtures/regmc_teams.txt 20000
cargo run --release --example fingerprint -- data/derived/format/gen9championsvgc2026regmc.json tests/fixtures/regmc_teams.txt 2000
```

On the bundled fixture teams (`tests/fixtures/regmc_teams.txt`) the fingerprint is
`6e2f85907f97e4e9`. A change that preserves behaviour must leave it unchanged.

The bench plays random legal games on one thread with logging off: about
5,000 to 6,000 games/s on one core of a 4-core cloud VM, on the fixture teams
or on a pool of real tournament teams (about 935,000 instructions per game
under callgrind on the latter). Speed on shared hosts varies by 10% or
more between runs, so compare instruction counts (`valgrind --tool=callgrind`
on the bench, a release build with debug info) rather than games/s across
days.

`check_reference.py` and `check_tracker.py` play on a pool of real teams when
you have one (`--pool`, a file with one packed team per line; or the
`supported-*.txt` pools that `report_support.py` writes to
`data/derived/teams/`), else on the fixture teams, and on random legal teams
with `--synthetic`. `check_reference.py` also takes `--first N` (start at game
N), `--require ids` (player 1's team must use one of those moves, items or
abilities) and `--verbose`; run any script with `--help`.
`report_support.py` reads a pool of packed teams, ranks the moves, items and
abilities that keep the most teams unsupported, and writes the supported ones
as a pool to verify on.

### Use from Python

```python
import json
import ninjask
from ninjask import Battle, dex
from ninjask.policy import RandomPolicy

FORMAT = "gen9championsvgc2026regmc"
team1, team2 = ninjask.verification_teams()[:2]   # the bundled test teams

d = dex(FORMAT)                      # loads the format export (bundled, or generated in a checkout)
assert d.team_problems(team1) == []  # [] when the engine fully supports the team

b = Battle(d, team1, team2, seed=1)  # teams in Showdown's packed format
b.set_logging(True)
policy = RandomPolicy(seed=1)        # any object with choose(request, log) -> choice string
while not b.ended and b.turn < 150:
    for side in ("p1", "p2"):
        if b.needs_choice(side):
            while True:
                request = json.loads(b.request(side))            # Showdown's request JSON
                try:
                    b.choose(side, policy.choose(request, []))   # e.g. "move 1 1, move 2 2"
                    break
                except ValueError:                               # rejected, as Showdown rejects it
                    pass                                         # (a hidden disable); the request now shows it
print(b.winner, b.turn)
print("\n".join(b.log("p1", 0)[:10]))                           # p1's view of the protocol log
```

`Battle.reference(d, team1, team2, "1,2,3,4")` runs the same battle in reference
mode, seeded with a Showdown seed string. `ninjask.play_engine_game` and
`ninjask.policy.RandomPolicy` wrap the loop above.

## API overview

**Rust** (`ninjask` crate; `cargo doc --open`)

- `Dex::from_json(text)`: the loaded format. Immutable, shared between
  battles through an `Arc`. Looks up species, moves, items, abilities and the
  type chart, and reports support.
- `Battle::new(dex, [team1, team2], names, seed)`: a battle from two packed
  teams. `Battle::new_with_rng` takes a given `Rng` (reference mode uses Showdown's);
  `Battle::from_sets` takes parsed teams. For many games over one pool,
  prepare each team once (`battle::Team::new(&dex, &sets)`: its Pokémon and
  open team sheet) and build each battle with `Battle::from_teams`, which
  copies them and plays exactly as `from_sets` would;
  `Battle::from_projection` imports a state exported by the reference.
  `Battle` is plain data and `Clone`, so copying it for search is cheap.
- `battle.request_json(side)`, `side_needs_choice(side)`, `choose(side, "move 1 1, switch 3")`
  accept and produce Showdown's request and choice formats.
  `joint_choices`, `sample_joint_choice` and `random_choice` enumerate or draw legal choices.
- `battle.log_lines(viewer, from)`: the protocol log as one viewer sees it
  (the spectator, or either player, hiding what that player has not seen).
- `battle.projection()`: the full state as JSON, in the shape of the reference
  harness's projection.
- `Tracker::new(dex, side)`: builds one player's observation from the protocol
  lines and requests it is fed (`feed`, `feed_request`), or from the engine
  directly (`feed_engine`, for self-play). The same tracker reads Showdown's
  logs, so live play and simulation give the same observations. `to_json()`
  exports the view.
- `world::World::from_view(&tracker, &assumptions) -> Result<Battle, _>`: a
  *world*, the battle one player's view describes. One's own side and the
  field are as the view holds them; the foe's hidden sets (item, ability,
  unrevealed moves, nature, stat points) come from a `SetAssumption` per
  Pokémon, or by default: nothing unrevealed, the sheet's or a neutral nature,
  and a placeholder spread rule (`world::placeholder_points`). Search over
  hidden information can run on worlds. `check_tracker.py --world`
  checks that a world built with the true sets matches the true battle.
- `calc`: exact damage and turn order, read-only. `battle.calc(attacker,
  move, target)` (or `damage_calc` on a copy) gives the 16 rolls of each hit
  without and with a critical hit, the crit chance, the spread reduction,
  the hit-count odds, accuracy, immunity, the expected damage and the
  chances to KO this turn and within two uses (no end-of-turn effects
  between them). It runs `getDamage`'s own two phases (`damage_base`,
  `damage_mods`) for every roll, so it can't drift from play.
  `effective_speed`, `order_odds(a, plan, b, plan)` (switches, priority,
  Quick Claw and the like, Speed, Trick Room, ties) and `speed_order`.
  `cargo run --release --example damage_check -- <dex> <teams> 5000` plays
  random games recording every hit with its predicted distribution: on the
  fixture teams, 0 of 99,176 damages fell outside it, and critical hits, KOs
  and rolls came as often as predicted.
- `rng::Rng`: xoshiro256++ from `Rng::new(seed)`, or Showdown's generator from `Rng::from_showdown_seed`.

**Python, compiled classes** (`ninjask.Dex`, `ninjask.Battle`, `ninjask.Tracker`;
built from `src/python.rs` as `ninjask._core` and re-exported by the package)

- `Dex.load(path)`, `.team_problems(packed)`, `.move_support(id)`,
  `.species_ids()`, `.generic_moves()`, `.implemented_moves()`,
  `.supported_abilities()`, `.supported_items()`.
- `Battle(dex, team1, team2, seed)`, `Battle.reference(...)`,
  `Battle.from_projection(...)`, `.request(side)`, `.needs_choice(side)`,
  `.choose(side, choice)` (raises `ValueError` on an illegal choice, as
  Showdown rejects it), `.log(viewer, start)`, `.log_len()`, `.set_logging(on)`,
  `.projection()`, `.copy()`, `.reseed(seed)`, `.ended`, `.winner`, `.turn`.
- `Tracker(dex, side)` with `.feed(lines)`, `.feed_request(json)`,
  `.feed_battle(battle)`, `.view()` and `.world(assumptions=None, seed=0)`
  (a `Battle`; assumptions as JSON, see `world::Assumptions::from_json`).
- `Battle.damage(attacker, move, target)` (slots like "p1a"; JSON),
  `.order(a, plan_a, b, plan_b)` (a plan is a move id, "switch" or
  "priority:N"; returns `(first, tie)`) and `.speeds()`.

**Python, the rest of the package** (`python/ninjask/`)

- `format`: `load(format_id)` returns a `FormatSpec` (species, moves, learnsets,
  legal species) from the Showdown export.
- `__init__`: `dex`, `play_engine_game`, and generators of random supported
  teams (`supported_test_team`).
- `refsim`: a client for the Showdown reference server (`RefSim`, `Battle`).
- `choices`, `policy`, `play`, `verify`, `tracker_check`: legal-choice
  enumeration, the policy interface, game loops for either simulator, and the
  distribution and tracker checks.
- `paths`: where Showdown and generated data live. Defaults are inside the
  checkout (`third_party/`, `data/derived/`); override with `NINJASK_HOME`,
  `NINJASK_SHOWDOWN` and `NINJASK_DATA`. `NINJASK_POOL` sets the default team
  pool of the checks.

## Repository layout

```
Cargo.toml, pyproject.toml   crate and Python package (built with maturin)
src/                         the engine
  dex/                         format data, team validation, support reporting
  state/                       battle state: field, sides, Pokémon, the move in use
  battle/                      construction, the action queue, choices and requests, the log
  moves/                       move execution: targeting, hits, damage, multi-hit, two-turn moves
  events/                      Showdown's event hooks: modifiers, switch-in, residual, immunity, ...
  abilities/ items/ conditions/  ported handlers; weather, terrain, status, volatiles, side conditions
  kinds/                       the ids of every special-cased move, item and ability
  projection/                  state export and import, in the reference harness's format
  tracker.rs                   per-player observation built from protocol lines
  rng.rs                       xoshiro256++, and Showdown's ChaCha20 generator
  python.rs                    PyO3 bindings
examples/                    bench (throughput), fingerprint (behaviour hash), damage_check
python/ninjask/              the Python package; the compiled module lands here as _core
refsim/                      Node.js driver for the pinned Showdown: server, format export
scripts/                     the checks above, and setup_showdown.sh
tests/python/, tests/fixtures/   pytest files, a small team pool
```

Each ported handler cites the Showdown function it follows. To add an entity,
port it from the pinned commit, then run `check_reference.py --require <id>`
and `verify_engine.py --focus <id>`: a port is done when they pass.

## Embedding in another extension

A Rust extension of your own whose functions take Ninjask's objects (say, an
observation encoder that reads a `Battle` and a `Tracker`) must be compiled
into the same module as Ninjask's classes: PyO3 only accepts an object of the
class compiled into the module that receives it, so a second copy of `Battle`
would be incompatible. Build one module holding both, and tell the `ninjask`
package to take its classes from it:

1. Depend on the crate with the feature `python` (not `python-module`):
   `ninjask = { git = "https://github.com/dcabatin/Ninjask", features = ["python"] }`.
2. In your `#[pymodule]`, call `ninjask::python::register(m)?`, then add your
   own classes. Their methods take `&ninjask::python::PyBattle`,
   `&PyTracker` and `&PyDex`, whose `inner` fields are the Rust objects.
3. Declare your module under the `ninjask.extension` entry-point group in
   your `pyproject.toml`:

   ```toml
   [project.entry-points."ninjask.extension"]
   myproject = "my_module"
   ```

`import ninjask` then re-exports `my_module.Dex`, `.Battle` and `.Tracker`
(`ninjask/_extension.py`), so `ninjask.Battle is my_module.Battle`, and
everything in the package, the checks included, uses them. `ninjask._core` is
used only when no module is declared, so the package's Python files are all
you need besides your own module (for example, `python/` on your project's
path).

## Documentation

In [`docs/`](docs/):

- [`design.md`](docs/design.md): requirements, architecture, how it is verified,
  and the findings of reference mode (every non-obvious Showdown behaviour the
  engine reproduces).
- [`adding-a-mechanic.md`](docs/adding-a-mechanic.md): how to add a move,
  ability or item, and how to verify it.

## License

MIT; see [LICENSE](LICENSE). The engine ports Pokémon Showdown's logic and
data, which is also MIT-licensed; see [NOTICE](NOTICE).

## Disclaimer

Ninjask is an unofficial fan project. It is not affiliated with, endorsed,
sponsored or approved by Nintendo, Game Freak, Creatures or The Pokémon
Company, nor by Pokémon Showdown. Pokémon and Pokémon character names are
trademarks of their respective owners, used here only to identify the game
mechanics the engine simulates. No official artwork, sprites, sounds or other
game assets are included.
