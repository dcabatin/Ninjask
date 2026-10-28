"""Where Ninjask finds Pokémon Showdown and keeps the data it generates.

In a source checkout (an editable install, `pip install -e .`), everything is
inside the checkout: Showdown in `third_party/pokemon-showdown`
(`scripts/setup_showdown.sh` puts it there) and generated data (format
exports, team pools) in `data/derived/`. When the checkout is a subdirectory
of another repository's (the engine vendored into a larger project), that
repository's `third_party/` and `data/derived/` are used instead, so the
project and the engine share one Showdown and one copy of the data.
`refsim/showdown_path.js` and `scripts/setup_showdown.sh` follow the same
rule.

The reference harness (`refsim/`, `scripts/`, `tests/`) lives in the
checkout, not in the installed package. A built wheel carries the format
exports and the fixture teams in `ninjask/data/` (`scripts/bundle_data.sh`
puts them there), so it can load the formats and play battles without
Showdown; it uses the current directory as its home.

Environment variables override each setting:
- `NINJASK_HOME`: the directory holding `third_party/` and `data/`;
- `NINJASK_SHOWDOWN`: the Showdown checkout;
- `NINJASK_DATA`: the generated-data directory (`data/derived/`).
"""

from __future__ import annotations

import os
from pathlib import Path

# python/ninjask/paths.py -> the checkout's root
ENGINE_ROOT = Path(__file__).resolve().parents[2]
REFSIM_DIR = ENGINE_ROOT / "refsim"
IN_CHECKOUT = (REFSIM_DIR / "SHOWDOWN_COMMIT").is_file()
# The data bundled into a built wheel (scripts/bundle_data.sh)
PACKAGE_DATA = Path(__file__).resolve().parent / "data"
FIXTURE_POOL = (
    ENGINE_ROOT / "tests" / "fixtures" / "regmc_teams.txt" if IN_CHECKOUT else PACKAGE_DATA / "regmc_teams.txt"
)


def _home() -> Path:
    if env := os.environ.get("NINJASK_HOME"):
        return Path(env)
    if not IN_CHECKOUT:
        return Path.cwd()
    if not (ENGINE_ROOT / ".git").exists() and (ENGINE_ROOT.parent / ".git").exists():
        return ENGINE_ROOT.parent
    return ENGINE_ROOT


HOME = _home()
SHOWDOWN_DIR = Path(os.environ.get("NINJASK_SHOWDOWN") or HOME / "third_party" / "pokemon-showdown")
DATA_DIR = Path(os.environ.get("NINJASK_DATA") or HOME / "data" / "derived")
FORMAT_DIR = DATA_DIR / "format"
TEAMS_DIR = DATA_DIR / "teams"


def require_checkout(what: str) -> None:
    """Fail clearly when `what` needs the reference harness and this is an installed wheel."""
    if not IN_CHECKOUT:
        raise RuntimeError(
            f"{what} needs Ninjask's source checkout (refsim/); install it from the checkout with `pip install -e .`"
        )


def node_env() -> dict[str, str]:
    """The environment for the refsim Node scripts: this process's, with the Showdown path."""
    return {**os.environ, "NINJASK_SHOWDOWN": str(SHOWDOWN_DIR)}
