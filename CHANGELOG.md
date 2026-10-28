# Changelog

## Unreleased

- README: speed figures re-measured after the throughput work.
- `examples/quickstart.rs`, and `--log` for `examples/bench.rs` (logging on).
- `refsim/bench_showdown.js`: Showdown's own throughput on the same random
  games, for comparison.
- README reorganised around why to use Ninjask, how, and how to contribute;
  crate-level documentation for docs.rs; issue and pull request templates.
- LICENSE: the copyright holder's name is corrected to David Cabatingan
  (0.1.0's packages carry the misspelled name).

## 0.1.0

First public release.

- Pokémon Champions VGC doubles (Reg M-C) engine in Rust, matching Pokémon
  Showdown at the pinned commit (`refsim/SHOWDOWN_COMMIT`) log line for log
  line in reference mode, on every supported move, item and ability.
- Python bindings (`ninjask`): `Dex`, `Battle`, `Tracker`, worlds for
  search, the damage and turn-order calculation, and the parity checks.
- The Python package bundles the format exports for Reg M-C (closed and open
  team sheets), Reg M-B and the Champions doubles custom game.
