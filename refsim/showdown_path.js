// Where the pinned Pokémon Showdown checkout is, by the same rule as
// python/ninjask/paths.py: $NINJASK_SHOWDOWN; else
// $NINJASK_HOME/third_party/pokemon-showdown; else this checkout's own
// third_party/, or the enclosing repository's when this checkout is a
// subdirectory or a git submodule of another repository (the engine vendored
// into a project).
'use strict';

const fs = require('fs');
const path = require('path');

// A submodule's .git is a file; only a repository of its own has a .git directory.
function isDir(p) {
	try {
		return fs.statSync(p).isDirectory();
	} catch {
		return false;
	}
}

function showdownPath() {
	if (process.env.NINJASK_SHOWDOWN) return path.resolve(process.env.NINJASK_SHOWDOWN);
	const engine = path.resolve(__dirname, '..');
	let home = engine;
	if (process.env.NINJASK_HOME) {
		home = path.resolve(process.env.NINJASK_HOME);
	} else if (!isDir(path.join(engine, '.git')) && fs.existsSync(path.join(engine, '..', '.git'))) {
		home = path.resolve(engine, '..');
	}
	return path.join(home, 'third_party', 'pokemon-showdown');
}

module.exports = { showdownPath };
