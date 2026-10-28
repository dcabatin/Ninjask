// Pokémon Showdown's own throughput on random games, for comparison with
// examples/bench.rs: `node refsim/bench_showdown.js <teams.txt> [games]`.
// It drives the pinned Showdown's Battle class directly (no streams or
// processes) with random move choices on the same teams; Showdown always
// writes its protocol log, so compare with the engine bench run with
// logging on, and compare turns per second (the two random policies differ,
// so games run to different lengths).
'use strict';

const path = require('path');
const {showdownPath} = require('./showdown_path.js');
const SD = showdownPath();
const {Battle} = require(path.join(SD, 'dist/sim/battle.js'));
const {Teams} = require(path.join(SD, 'dist/sim/teams.js'));
const {PRNG} = require(path.join(SD, 'dist/sim/prng.js'));
const fs = require('fs');
const teams = fs.readFileSync(process.argv[2], 'utf8').split('\n').filter(l => l.trim());
const games = +process.argv[3] || 500;
const prng = new PRNG([1, 2, 3, 4]);
function pick(arr) { return arr[prng.random(arr.length)]; }
function choices(req) {
  // A random legal-looking choice; Showdown rejects illegal ones and we retry with "default".
  if (req.teamPreview) return 'default';
  if (req.forceSwitch) return 'default';
  if (req.active) {
    return req.active.map((a, i) => {
      const moves = a.moves.map((m, j) => ({m, j})).filter(x => !x.m.disabled);
      if (!moves.length) return 'move 1';
      const x = pick(moves);
      const tgt = ['normal', 'any', 'adjacentFoe'].includes(x.m.target) ? ' ' + (1 + prng.random(2)) : '';
      return `move ${x.j + 1}${tgt}`;
    }).join(', ');
  }
  return 'default';
}
let turns = 0;
const t0 = process.hrtime.bigint();
for (let g = 0; g < games; g++) {
  const b = new Battle({formatid: 'gen9championsvgc2026regmc', seed: [g, 1, 2, 3]});
  b.setPlayer('p1', {name: 'A', team: teams[g % teams.length]});
  b.setPlayer('p2', {name: 'B', team: teams[(g * 7 + 3) % teams.length]});
  let guard = 0;
  while (!b.ended && b.turn < 150 && guard++ < 2000) {
    for (const side of b.sides) {
      const req = side.activeRequest;
      if (!req || req.wait || side.isChoiceDone()) continue;
      if (!b.choose(side.id, choices(req))) b.choose(side.id, 'default');
    }
  }
  turns += b.turn;
}
const s = Number(process.hrtime.bigint() - t0) / 1e9;
console.log(`showdown: ${games} games, ${turns} turns in ${s.toFixed(2)}s: ` +
	`${(games / s).toFixed(1)} games/s, ${(turns / s).toFixed(0)} turns/s (one thread)`);
