#!/usr/bin/env bash
# Clone Pokémon Showdown at the commit pinned in refsim/SHOWDOWN_COMMIT and build it.
# This checkout is the engine's reference simulator: mechanics, legality and
# protocol are whatever this commit does.
#
# It goes to $NINJASK_SHOWDOWN if set, else to third_party/pokemon-showdown
# under $NINJASK_HOME, else under this checkout, or under the enclosing
# repository's checkout when this one is a subdirectory or a git submodule of
# it (the engine vendored into a project). python/ninjask/paths.py follows the
# same rule.
set -euo pipefail

ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
COMMIT="$(tr -d '[:space:]' < "$ENGINE/refsim/SHOWDOWN_COMMIT")"
if [ -n "${NINJASK_HOME:-}" ]; then
	HOME_DIR="$NINJASK_HOME"
elif [ ! -d "$ENGINE/.git" ] && [ -e "$ENGINE/../.git" ]; then
	HOME_DIR="$(cd "$ENGINE/.." && pwd)"
else
	HOME_DIR="$ENGINE"
fi
DEST="${NINJASK_SHOWDOWN:-$HOME_DIR/third_party/pokemon-showdown}"

if [ ! -d "$DEST/.git" ]; then
	mkdir -p "$(dirname "$DEST")"
	git clone --filter=blob:none https://github.com/smogon/pokemon-showdown.git "$DEST"
fi

cd "$DEST"
if [ "$(git rev-parse HEAD)" != "$COMMIT" ]; then
	git fetch --quiet origin "$COMMIT" || git fetch --quiet origin
	git checkout --quiet --detach "$COMMIT"
fi

# `node build` installs dependencies (npm ci) when missing and compiles to dist/.
node build
echo "Pokémon Showdown ready at $DEST ($(git rev-parse --short HEAD))"
