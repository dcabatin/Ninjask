#!/usr/bin/env node
/**
 * Export a format's rules and legal game data from the pinned Showdown as
 * JSON, so other code (the engine, agents, team tools) can read the
 * format as data instead of hardcoding it.
 *
 * Usage: node refsim/export_format.js gen9championsvgc2026regmc > out.json
 *
 * Contents: the resolved rule table, legal species (with base stats, types,
 * abilities and Mega/forme links), every move, item and ability those
 * species can use, per-species learnsets restricted to legal moves,
 * natures, and the type chart. "Legal" means the format's validator accepts
 * it; battle-only formes (Megas) are included and flagged.
 */
'use strict';

const path = require('path');
const PS = require('./showdown_path').showdownPath();
const { Dex, TeamValidator, toID } = require(path.join(PS, 'dist', 'sim'));
const { Format } = require(path.join(PS, 'dist', 'sim', 'dex-formats'));

const formatId = process.argv[2];
if (!formatId) {
	console.error('usage: export_format.js <formatid>');
	process.exit(2);
}
// Format ids Showdown does not define, derived from a base format by
// swapping a ruleset entry. PKMN Arena's open-team-sheets ladder for Reg M-C
// is the base format with sheets forced open; Showdown's own spelling of
// that swap is its Bo3 format, which also adds "Best of = 3", so the arena
// format is derived here instead. Legality and game data are the base
// format's; only the sheet rule (and the recorded `derivedFrom`) differ.
const DERIVED_FORMATS = {
	gen9championsvgc2026regmcots: {
		base: 'gen9championsvgc2026regmc',
		swap: ['Open Team Sheets', 'Force Open Team Sheets'],
	},
};
let format = Dex.formats.get(formatId, true);
let derivedFrom = null;
if (!format.exists && DERIVED_FORMATS[formatId]) {
	const { base: baseId, swap: [from, to] } = DERIVED_FORMATS[formatId];
	const base = Dex.formats.get(baseId, true);
	if (!base.exists) throw new Error(`unknown base format ${baseId}`);
	if (!base.ruleset.includes(from)) throw new Error(`${baseId} has no "${from}" rule to swap`);
	format = new Format({
		id: formatId,
		name: `${base.name} OTS`,
		mod: base.mod,
		effectType: 'Format',
		gameType: base.gameType,
		ruleset: base.ruleset.map(r => (r === from ? to : r)),
	});
	derivedFrom = baseId;
}
if (!format.exists) throw new Error(`unknown format ${formatId}`);
const dex = Dex.forFormat(format);
const ruleTable = Dex.formats.getRuleTable(format);
const validator = TeamValidator.get(format);

// Ids quoted anywhere in the battle core or the mod's scripts: their behaviour
// may be hard-coded outside their own entry, so they need review by hand.
// Files that don't run battles (team validation, packing, streams, state
// serialization, text) are skipped.
const fs = require('fs');
const NON_BATTLE_SOURCES = new Set([
	'team-validator.ts', 'teams.ts', 'battle-stream.ts', 'state.ts', 'dex-text.ts', 'global-types.ts',
	'index.ts', 'prng.ts', 'dex-formats.ts',
	'dex-species.ts', // species data and old-generation HM lists for learnset checks
]);
// Quoted ids in battle files that don't change battle behaviour.
const NON_MECHANIC_REFS = new Set([
	'ironhead', // battle.ts showOpenTeamSheets: renamed for Zacian/Zamazenta-Crowned sheets only
]);
const coreSources = [
	...fs.readdirSync(path.join(PS, 'sim'))
		.filter(f => f.endsWith('.ts') && !NON_BATTLE_SOURCES.has(f))
		.map(f => path.join(PS, 'sim', f)),
	path.join(PS, 'data', 'mods', format.mod, 'scripts.ts'),
].filter(f => fs.existsSync(f));
const coreIds = new Set();
for (const file of coreSources) {
	for (const m of fs.readFileSync(file, 'utf8').matchAll(/'([a-z0-9]+)'/g)) {
		if (!NON_MECHANIC_REFS.has(m[1])) coreIds.add(m[1]);
	}
}

/**
 * Names of function-valued fields: where Showdown attaches custom code,
 * including code nested in a move's condition, secondary effects and self effect.
 */
function callbacks(entry) {
	const out = Object.keys(entry).filter(k => typeof entry[k] === 'function');
	const nested = { condition: entry.condition, self: entry.self, secondary: entry.secondary };
	(entry.secondaries || []).forEach((sec, i) => { nested[`secondaries.${i}`] = sec; });
	for (const [prefix, obj] of Object.entries(nested)) {
		if (!obj || typeof obj !== 'object') continue;
		for (const k of Object.keys(obj)) {
			if (typeof obj[k] === 'function') out.push(`${prefix}.${k}`);
		}
	}
	return [...new Set(out)].sort();
}

function plain(obj, keys) {
	const out = {};
	for (const k of keys) if (obj[k] !== undefined && obj[k] !== null && obj[k] !== '') out[k] = obj[k];
	return out;
}

// Species: legal for a team (not battle-only) or reachable in battle (Megas).
const species = [];
const usable = new Set();
for (const s of dex.species.all()) {
	if (s.isNonstandard || ruleTable.isBannedSpecies(s)) continue;
	const battleOnly = !!s.battleOnly;
	const problem = battleOnly ? null : validator.checkSpecies({ species: s.name }, s, s, {});
	if (problem) continue;
	species.push({
		id: s.id,
		name: s.name,
		baseSpecies: s.baseSpecies,
		forme: s.forme || '',
		types: s.types,
		baseStats: s.baseStats,
		abilities: s.abilities,
		weightkg: s.weightkg,
		battleOnly: s.battleOnly || null,
		requiredItem: s.requiredItem || null,
		requiredItems: s.requiredItems || null,
		isMega: !!s.isMega,
		gender: s.gender || '',
		otherFormes: s.otherFormes || [],
		changesFrom: s.changesFrom || null,
		tags: s.tags || [],
	});
	if (!battleOnly) usable.add(s.id);
}

// Learnsets restricted to moves legal in the format.
const learnsets = {};
const moveIds = new Set();
for (const s of species) {
	if (s.battleOnly) continue;
	const sp = dex.species.get(s.id);
	const moves = [];
	for (const m of dex.moves.all()) {
		if (m.isNonstandard || ruleTable.isBanned(`move:${m.id}`)) continue;
		if (validator.checkCanLearn(m, sp, validator.allSources(sp), { species: sp.name })) continue;
		moves.push(m.id);
		moveIds.add(m.id);
	}
	learnsets[s.id] = moves.sort();
}

/** Every data field Showdown sets, minus functions and unset defaults. */
function dataFields(entry) {
	const out = {};
	for (const [k, v] of Object.entries(entry)) {
		if (typeof v === 'function' || v === undefined || v === null || v === false || v === '') continue;
		out[k] = v;
	}
	return out;
}

const moves = [...moveIds].sort().map(id => {
	const m = dex.moves.get(id);
	const out = { ...dataFields(m), id, callbacks: callbacks(m), coreRef: coreIds.has(id) };
	// Status moves ignore type immunity by default; an explicit false (Thunder Wave) matters.
	if (m.category === 'Status' && m.ignoreImmunity === false) out.ignoreImmunity = false;
	return out;
});

const items = [];
for (const it of dex.items.all()) {
	if (it.isNonstandard || ruleTable.isBanned(`item:${it.id}`)) continue;
	items.push({ ...dataFields(it), id: it.id, callbacks: callbacks(it), coreRef: coreIds.has(it.id) });
}

const abilityIds = new Set();
for (const s of species) for (const a of Object.values(s.abilities)) abilityIds.add(toID(a));
// Abilities only a move gives (Simple Beam's Simple) go after the sorted
// list, so the ids of the others stay as they were.
const movesGive = { simplebeam: 'simple' };
const given = Object.entries(movesGive)
	.filter(([move, id]) => moves.some(m => m.id === move) && !abilityIds.has(id))
	.map(([, id]) => id);
const abilities = [...[...abilityIds].sort(), ...given].map(id => {
	const a = dex.abilities.get(id);
	return { ...dataFields(a), id, callbacks: callbacks(a), coreRef: coreIds.has(id) };
});

const natures = {};
for (const n of dex.natures.all()) natures[n.id] = { name: n.name, plus: n.plus || null, minus: n.minus || null };

const typechart = {};
for (const t of dex.types.all()) {
	if (t.isNonstandard) continue;
	typechart[t.name] = t.damageTaken;
}

const rules = [];
for (const [rule, source] of ruleTable) rules.push({ rule, source: source || null });

// Conditions: statuses, volatiles, weather, terrain, side and field effects,
// with their durations and residual orders. Includes conditions defined on
// moves, items and abilities.
const conditions = {};
for (const id of Object.keys(dex.data.Conditions)) {
	const c = dex.conditions.getByID(id);
	if (c.exists) conditions[id] = { ...dataFields(c), id, callbacks: callbacks(c) };
}
for (const m of moves) {
	const c = dex.moves.get(m.id).condition;
	if (c) conditions[m.id] = { ...dataFields(c), id: m.id, callbacks: callbacks(c) };
}

const out = {
	format: {
		id: format.id,
		name: format.name,
		...(derivedFrom ? { derivedFrom } : {}),
		mod: format.mod,
		gen: dex.gen,
		gameType: format.gameType,
		rules,
		valueRules: Object.fromEntries(ruleTable.valueRules),
		pickedTeamSize: ruleTable.pickedTeamSize,
		evLimit: ruleTable.evLimit,
		adjustLevel: ruleTable.adjustLevel,
		maxTeamSize: ruleTable.maxTeamSize,
		minTeamSize: ruleTable.minTeamSize,
		showdownCommit: require('fs').readFileSync(path.resolve(__dirname, 'SHOWDOWN_COMMIT'), 'utf8').trim(),
	},
	species,
	learnsets,
	moves,
	items,
	abilities,
	natures,
	typechart,
	conditions,
};
process.stdout.write(JSON.stringify(out));
