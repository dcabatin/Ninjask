//! Many battles stepped together, for training: each battle keeps both
//! players' [`Tracker`]s, and one call reports every pending decision as
//! contiguous numeric buffers ([`crate::encode`]), one call applies a choice
//! to each, and both run the battles in parallel. Python's `ninjask.Batch`
//! wraps it.
//!
//! The choices offered are the player's view ([`Battle::player_choices`]),
//! so, as in Showdown, a choice can be refused for something hidden; the
//! refusal reveals it and the decision stays pending with the updated
//! options. Battles are independent and seeded, so results don't depend on
//! the thread count or the order of calls.

use std::sync::Arc;

use rayon::prelude::*;

use std::collections::HashMap;

use crate::battle::{Battle, Team};
use crate::dex::team::parse_packed;
use crate::dex::Dex;
use crate::encode;
use crate::state::RequestState;
use crate::tracker::Tracker;

/// One battle and what each player has seen of it.
struct Env {
    battle: Battle,
    trackers: [Tracker; 2],
    /// Protocol lines each tracker has been fed.
    seen: [usize; 2],
}

impl Env {
    /// Bring the side's tracker up to date with the log and its request,
    /// reading the battle's log lines in place (as `log_for` renders them).
    fn sync(&mut self, s: usize) {
        let viewer = crate::battle::log::Viewer::Side(s);
        let log = &self.battle.log;
        let t = &mut self.trackers[s];
        for e in &log[self.seen[s].min(log.len())..] {
            t.feed(e.render(viewer));
        }
        self.seen[s] = log.len();
        t.feed_engine(&self.battle);
    }
}

/// Width of an action row in [`Batch::observe`]: the larger of a doubles
/// move row and a team preview of six.
pub const ACTION_WIDTH: usize = 2 * encode::ACTION_FIELDS;

/// The pending decisions of a batch, as [`Batch::observe`] fills them.
/// Row `k` is the decision of side `who[2k + 1]` in battle `who[2k]`; its
/// legal choices are rows `offsets[k]..offsets[k + 1]` of `actions`.
#[derive(Default, Debug, Clone)]
pub struct Observation {
    /// (battle, side) pairs.
    pub who: Vec<u32>,
    /// 1 for a team preview decision, else 0.
    pub preview: Vec<u8>,
    /// `OBS_LEN` floats per decision.
    pub obs: Vec<f32>,
    /// `IDS_LEN` values per decision.
    pub ids: Vec<i16>,
    /// `ACTION_WIDTH` values per legal choice: [`encode::action_row`] for
    /// move and switch requests; at team preview the team positions sent
    /// (1-based, leads first), padded with 0.
    pub actions: Vec<i8>,
    /// Row offsets into `actions`, one more than the decisions.
    pub offsets: Vec<u32>,
}

/// The outcome of applying one choice in [`Batch::step`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct StepResult {
    /// Showdown refused the choice (a hidden disable or trap); the
    /// decision is still pending.
    pub refused: bool,
    /// The battle ended with this choice.
    pub ended: bool,
}

/// Prepared teams kept by [`Batch::reset`]: a training run reuses a pool.
const TEAM_CACHE: usize = 4096;

/// Many battles, stepped together.
pub struct Batch {
    dex: Arc<Dex>,
    envs: Vec<Option<Env>>,
    /// Packed team -> prepared team, so a reset parses each team once.
    teams: HashMap<String, Arc<Team>>,
    /// The turn after which a battle is stopped as a draw.
    pub turn_limit: u32,
}

impl Batch {
    /// `n` empty slots; [`Batch::reset`] puts a battle in one.
    pub fn new(dex: Arc<Dex>, n: usize) -> Batch {
        Batch {
            dex,
            envs: (0..n).map(|_| None).collect(),
            teams: HashMap::new(),
            turn_limit: 150,
        }
    }

    pub fn len(&self) -> usize {
        self.envs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.envs.is_empty()
    }

    /// A packed team, prepared (and kept for the next reset).
    fn team(&mut self, packed: &str) -> Result<Arc<Team>, String> {
        if let Some(t) = self.teams.get(packed) {
            return Ok(t.clone());
        }
        let team = Arc::new(Team::new(&self.dex, &parse_packed(&self.dex, packed)?));
        if self.teams.len() >= TEAM_CACHE {
            self.teams.clear();
        }
        self.teams.insert(packed.to_string(), team.clone());
        Ok(team)
    }

    /// Start a battle in slot `i` from two packed teams, seeded: the battle
    /// `Battle::new` builds from them (each team is parsed once per batch).
    pub fn reset(&mut self, i: usize, teams: [&str; 2], seed: u64) -> Result<(), String> {
        if i >= self.envs.len() {
            return Err(format!("battle {i} out of range"));
        }
        let (a, b) = (self.team(teams[0])?, self.team(teams[1])?);
        let mut battle =
            Battle::from_teams(self.dex.clone(), [&a, &b], ["Player 1", "Player 2"], seed);
        battle.log_enabled = true;
        let trackers = [
            Tracker::new(self.dex.clone(), 0),
            Tracker::new(self.dex.clone(), 1),
        ];
        self.envs[i] = Some(Env {
            battle,
            trackers,
            seen: [0, 0],
        });
        Ok(())
    }

    /// Start several battles at once: `starts[k] = (slot, [team1, team2],
    /// seed)`, as many `reset` calls, with the battles built in parallel.
    /// Nothing is started when a slot or a team is bad.
    pub fn reset_many(&mut self, starts: &[(usize, [&str; 2], u64)]) -> Result<(), String> {
        let mut prepared = Vec::with_capacity(starts.len());
        for &(i, teams, seed) in starts {
            if i >= self.envs.len() {
                return Err(format!("battle {i} out of range"));
            }
            prepared.push((i, [self.team(teams[0])?, self.team(teams[1])?], seed));
        }
        let dex = self.dex.clone();
        let built: Vec<(usize, Env)> = prepared
            .into_par_iter()
            .map(|(i, [a, b], seed)| {
                let mut battle =
                    Battle::from_teams(dex.clone(), [&a, &b], ["Player 1", "Player 2"], seed);
                battle.log_enabled = true;
                let trackers = [Tracker::new(dex.clone(), 0), Tracker::new(dex.clone(), 1)];
                (
                    i,
                    Env {
                        battle,
                        trackers,
                        seen: [0, 0],
                    },
                )
            })
            .collect();
        for (i, env) in built {
            self.envs[i] = Some(env);
        }
        Ok(())
    }

    /// For each slot, whether its battle is over ([`Batch::done`]).
    pub fn dones(&self) -> Vec<bool> {
        (0..self.envs.len()).map(|i| self.done(i)).collect()
    }

    /// The battle in slot `i`, if one was started.
    pub fn battle(&self, i: usize) -> Option<&Battle> {
        self.envs.get(i)?.as_ref().map(|e| &e.battle)
    }

    /// Whether slot `i`'s battle is over: ended, or past the turn limit.
    pub fn done(&self, i: usize) -> bool {
        self.battle(i)
            .is_none_or(|b| b.ended || b.turn >= self.turn_limit)
    }

    /// Every pending decision: each side of each running battle that has to
    /// choose, with its observation and legal choices.
    pub fn observe(&mut self) -> Observation {
        let limit = self.turn_limit;
        // Each battle's decisions, in parallel; then concatenated in order.
        let parts: Vec<Observation> = self
            .envs
            .par_iter_mut()
            .enumerate()
            .map(|(i, env)| {
                let mut o = Observation::default();
                let Some(env) = env else { return o };
                if env.battle.ended || env.battle.turn >= limit {
                    return o;
                }
                for s in 0..2 {
                    if !env.battle.side_needs_choice(s) {
                        continue;
                    }
                    env.sync(s);
                    o.who.extend([i as u32, s as u32]);
                    encode::observation(&env.trackers[s], &mut o.obs);
                    encode::ids(&env.trackers[s], &mut o.ids);
                    o.offsets.push(o.actions.len() as u32 / ACTION_WIDTH as u32);
                    if env.battle.request == RequestState::TeamPreview {
                        o.preview.push(1);
                        let actions = &mut o.actions;
                        env.battle.for_each_team_preview_order(s, &mut |order| {
                            let mut row = [0i8; ACTION_WIDTH];
                            for (k, &p) in order.iter().take(ACTION_WIDTH).enumerate() {
                                row[k] = p as i8 + 1;
                            }
                            actions.extend(row);
                        });
                    } else {
                        o.preview.push(0);
                        let actions = &mut o.actions;
                        env.battle.for_each_player_choice(s, &mut |c| {
                            let start = actions.len();
                            encode::action_row(c, actions);
                            actions.resize(start + ACTION_WIDTH, 0);
                        });
                    }
                }
                o
            })
            .collect();
        let mut out = Observation::default();
        for p in parts {
            let base = out.actions.len() as u32 / ACTION_WIDTH as u32;
            out.who.extend(p.who);
            out.preview.extend(p.preview);
            out.obs.extend(p.obs);
            out.ids.extend(p.ids);
            out.offsets.extend(p.offsets.iter().map(|o| o + base));
            out.actions.extend(p.actions);
        }
        out.offsets
            .push(out.actions.len() as u32 / ACTION_WIDTH as u32);
        out
    }

    /// Apply a choice to each pending decision: `picks[k] = (battle, side,
    /// index)` with `index` into that decision's legal choices as
    /// [`Batch::observe`] listed them. Every pick is checked before any is
    /// applied, so an error leaves the batch as it was. The battles advance
    /// in parallel.
    pub fn step(&mut self, picks: &[(usize, usize, usize)]) -> Result<Vec<StepResult>, String> {
        // Group the picks by battle, keeping each one's position in `picks`.
        let mut by_env: Vec<Vec<(usize, usize, usize)>> = vec![Vec::new(); self.envs.len()];
        for (k, &(i, s, index)) in picks.iter().enumerate() {
            if i >= self.envs.len() || s > 1 {
                return Err(format!("no battle {i}, side {s}"));
            }
            by_env[i].push((k, s, index));
        }
        // First every choice, as Showdown syntax: an index out of range
        // fails the call before anything moves.
        let resolved: Vec<Result<Resolved, String>> = self
            .envs
            .par_iter()
            .zip(by_env.par_iter())
            .enumerate()
            .map(|(i, (env, mine))| {
                if mine.is_empty() {
                    return Ok(Vec::new());
                }
                let b = &env
                    .as_ref()
                    .ok_or_else(|| format!("battle {i} was never reset"))?
                    .battle;
                mine.iter()
                    .map(|&(k, s, index)| Ok((k, s, choice_at(b, i, s, index)?)))
                    .collect()
            })
            .collect();
        let mut chosen = Vec::with_capacity(resolved.len());
        for r in resolved {
            chosen.push(r?);
        }
        // Then apply them, battle by battle in parallel.
        let results: Vec<Vec<(usize, StepResult)>> = self
            .envs
            .par_iter_mut()
            .zip(chosen.into_par_iter())
            .map(|(env, mine)| {
                let mut out = Vec::with_capacity(mine.len());
                let Some(env) = env else { return out };
                for (k, s, choice) in mine {
                    let b = &mut env.battle;
                    let refused = b.choose(s, &choice).is_err();
                    out.push((
                        k,
                        StepResult {
                            refused,
                            ended: b.ended,
                        },
                    ));
                }
                out
            })
            .collect();
        let mut out = vec![
            StepResult {
                refused: false,
                ended: false,
            };
            picks.len()
        ];
        for r in results {
            for (k, res) in r {
                out[k] = res;
            }
        }
        Ok(out)
    }
}

/// One battle's picks resolved to choices: (position in `picks`, side,
/// Showdown syntax).
type Resolved = Vec<(usize, usize, String)>;

/// Choice `index` of side `s` in battle `i` (named in errors), as Showdown
/// syntax: a team-preview order, or a joint choice of the player's view.
fn choice_at(b: &Battle, i: usize, s: usize, index: usize) -> Result<String, String> {
    let mut seen = 0usize;
    let mut picked = None;
    if b.request == RequestState::TeamPreview {
        b.for_each_team_preview_order(s, &mut |o| {
            if seen == index {
                let p: String = o.iter().map(|p| (p + 1).to_string()).collect();
                picked = Some(format!("team {p}"));
            }
            seen += 1;
        });
    } else {
        b.for_each_player_choice(s, &mut |c| {
            if seen == index {
                picked = Some(b.choice_string(c));
            }
            seen += 1;
        });
    }
    picked.ok_or_else(|| format!("battle {i} side {s}: choice {index} of {seen}"))
}
