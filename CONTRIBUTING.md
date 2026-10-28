# Contributing

Pokémon Showdown at the commit in `refsim/SHOWDOWN_COMMIT` is the
specification: a change is correct when the engine does what that commit does.
Set up a checkout as in the README's Quick start, then:

- **Porting a move, item or ability.** Follow `docs/adding-a-mechanic.md`:
  port from Showdown's code, not from memory or another simulator, and mark
  the entity supported only when the checks it lists pass.
- **Changes that should not change behaviour** (refactors, speed) must leave
  the fingerprint unchanged (README, "Run the checks").

Before opening a pull request, run what CI runs:

```sh
cargo fmt --check
cargo clippy --release --all-targets -- -D warnings
cargo clippy --release --all-targets --features python-module -- -D warnings
cargo test --release
pytest -q
python scripts/check_reference.py --synthetic --games 200 --workers 4
```

Bug reports are most useful with a seed and teams that reproduce the
difference from Showdown: `check_reference.py --verbose` prints both
simulators' logs and RNG draws at the first divergence.
