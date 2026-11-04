// Packs Showdown-format pastes with the pinned Showdown and keeps the teams
// its validator accepts for Reg M-C (scripts/build_vgcpastes_pool.py).
//   node refsim/pack_pastes.js <showdown dir> <dir of paste .json files> <out.txt>
// Writes <out.txt> (one packed team per line, duplicates dropped),
// <out.txt>.ids (the sheet's team ID of each line) and <out.txt>.status.tsv
// (each paste's verdict).
const fs = require('fs'), path = require('path');
const SD = process.argv[2], dir = process.argv[3], out = process.argv[4];
const {Teams} = require(path.join(SD, 'dist/sim/teams'));
const {TeamValidator} = require(path.join(SD, 'dist/sim/team-validator'));
const v = TeamValidator.get('gen9championsvgc2026regmc');
const files = fs.readdirSync(dir).filter(f => f.endsWith('.json')).sort((a, b) => parseInt(a.slice(2)) - parseInt(b.slice(2)));
const kept = [], why = {}, rows = [];
for (const f of files) {
  const d = JSON.parse(fs.readFileSync(path.join(dir, f)));
  const team = Teams.import(d.paste);
  let status;
  if (!team || !team.length) status = 'did not parse';
  else {
    const problems = v.validateTeam(team);
    if (problems && problems.length) status = 'illegal: ' + problems[0].slice(0, 90);
    else { status = 'legal'; kept.push({id: d._sheet.id, packed: Teams.pack(team)}); }
  }
  const k = status.startsWith('illegal') ? 'illegal' : status; why[k] = (why[k] || 0) + 1;
  rows.push(`${d._sheet.id}\t${status}`);
}
const seen = new Set(), uniq = kept.filter(t => !seen.has(t.packed) && seen.add(t.packed));
fs.writeFileSync(out, uniq.map(t => t.packed).join('\n') + '\n');
fs.writeFileSync(out + '.ids', uniq.map(t => t.id).join('\n') + '\n');
fs.writeFileSync(out + '.status.tsv', rows.join('\n') + '\n');
console.log(`pastes ${files.length}:`, JSON.stringify(why), `legal distinct ${uniq.length}`);
