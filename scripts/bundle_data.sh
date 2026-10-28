#!/usr/bin/env bash
# Put the data an installed wheel needs into the package, python/ninjask/data/,
# before building it (the release workflow runs this): each format's export
# from the pinned Showdown (scripts/setup_showdown.sh first), and the fixture
# teams. The files are generated, so git ignores them; pyproject.toml's
# [tool.maturin] include puts them in the wheel and the sdist.
set -euo pipefail

ENGINE="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ENGINE/python/ninjask/data"
FORMATS="gen9championsvgc2026regmc gen9championsvgc2026regmcots gen9championsvgc2026regmb gen9championsdoublescustomgame"

mkdir -p "$OUT/formats"
for f in $FORMATS; do
	node "$ENGINE/refsim/export_format.js" "$f" > "$OUT/formats/$f.json.tmp"
	mv "$OUT/formats/$f.json.tmp" "$OUT/formats/$f.json"
done
cp "$ENGINE/tests/fixtures/regmc_teams.txt" "$OUT/regmc_teams.txt"
echo "bundled $(ls "$OUT/formats" | wc -l) formats and the fixture teams into $OUT"
