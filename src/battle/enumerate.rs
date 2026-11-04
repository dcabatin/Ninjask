//! Enumerate by replaying explicit chance paths; the Rust stack runs normally.
//!
//! **Experimental:** exact, but too slow for most full turns, and the API
//! may change in any release (README, "Stability before 1.0").
//!
//! Each chance path replays the whole turn, and paths multiply with every
//! hit (accuracy, critical hit, 16 damage rolls), so a turn with several
//! attacks can have millions of them even when few outcomes differ. Every
//! enumeration therefore runs within a budget of chance paths
//! ([`DEFAULT_MAX_PATHS`] unless a `_within` variant names one) and fails
//! with an [`EnumerationError`] when it would exceed it, instead of running
//! for minutes. A path costs about 33 µs (one core of a 4-vCPU VM), so the
//! default is about 0.7 s. On 40 mid-game states of real teams with random
//! choices (`examples/validate_outcomes.rs`), Precise finished within it on
//! 14 and DamageEndpoints, which keeps only the two extreme damage rolls, on
//! 22; the rest have more paths. Endgames and turns with few attacks fit;
//! for the others, raise `max_paths` or sample the turn.
use super::{Battle, BattleStateKey};
use crate::rng::{PreciseState, Rng};
use crate::state::{RequestState, SlotChoice};
use crate::{EnumerationError, EnumerationMode, Outcome};
use std::collections::HashMap;

/// The chance paths an enumeration explores unless told otherwise: about
/// 0.7 s of work at 33 µs a path (each path replays the turn).
pub const DEFAULT_MAX_PATHS: u64 = 20_000;

impl Battle {
    /// Enumerate until the next player request or battle end without mutating self.
    /// Experimental (see the module docs).
    /// Supply an empty choice for a side that does not currently need one.
    /// Team preview uses its separate team-order API and is not accepted here.
    /// Fails when the turn has more than [`DEFAULT_MAX_PATHS`] chance paths;
    /// [`Battle::enumerate_outcomes_within`] takes another budget.
    pub fn enumerate_outcomes(
        &self,
        choice_p1: &[SlotChoice],
        choice_p2: &[SlotChoice],
        mode: EnumerationMode,
    ) -> Result<Vec<Outcome>, EnumerationError> {
        self.enumerate_outcomes_within(choice_p1, choice_p2, mode, DEFAULT_MAX_PATHS)
    }

    /// [`Battle::enumerate_outcomes`] exploring at most `max_paths` chance
    /// paths: an [`EnumerationError`] when the turn has more.
    pub fn enumerate_outcomes_within(
        &self,
        choice_p1: &[SlotChoice],
        choice_p2: &[SlotChoice],
        mode: EnumerationMode,
        max_paths: u64,
    ) -> Result<Vec<Outcome>, EnumerationError> {
        let choices = [choice_p1, choice_p2];
        if self.request == RequestState::TeamPreview {
            return Err(EnumerationError(
                "enumeration requires a move or switch request".into(),
            ));
        }
        let needed = [self.side_needs_choice(0), self.side_needs_choice(1)];
        for s in 0..2 {
            if needed[s] {
                if !self.joint_choices(s).iter().any(|c| c == choices[s]) {
                    return Err(EnumerationError(format!(
                        "invalid structured choice for p{}",
                        s + 1
                    )));
                }
            } else if !choices[s].is_empty() {
                return Err(EnumerationError(format!(
                    "p{} does not need a choice; supply an empty choice",
                    s + 1
                )));
            }
        }
        self.enumerate_with(mode, max_paths, |b| {
            for s in 0..2 {
                if needed[s] {
                    b.choose_structured(s, choices[s].to_vec());
                }
            }
            Ok(())
        })
    }

    /// String choices reuse Showdown's existing parser, including hidden reveals.
    /// Within [`DEFAULT_MAX_PATHS`], as [`Battle::enumerate_outcomes`].
    pub fn enumerate_outcomes_text(
        &self,
        choice_p1: &str,
        choice_p2: &str,
        mode: EnumerationMode,
    ) -> Result<Vec<Outcome>, EnumerationError> {
        self.enumerate_outcomes_text_within(choice_p1, choice_p2, mode, DEFAULT_MAX_PATHS)
    }

    /// [`Battle::enumerate_outcomes_text`] exploring at most `max_paths`
    /// chance paths.
    pub fn enumerate_outcomes_text_within(
        &self,
        choice_p1: &str,
        choice_p2: &str,
        mode: EnumerationMode,
        max_paths: u64,
    ) -> Result<Vec<Outcome>, EnumerationError> {
        if self.request == RequestState::TeamPreview {
            return Err(EnumerationError(
                "enumeration requires a move or switch request".into(),
            ));
        }
        let needed = [self.side_needs_choice(0), self.side_needs_choice(1)];
        let choices = [choice_p1, choice_p2];
        for s in 0..2 {
            if !needed[s] && !choices[s].is_empty() {
                return Err(EnumerationError(format!(
                    "p{} does not need a choice; supply an empty choice",
                    s + 1
                )));
            }
        }
        self.enumerate_with(mode, max_paths, |b| {
            for s in 0..2 {
                if needed[s] {
                    b.choose(s, choices[s]).map_err(EnumerationError)?;
                }
            }
            Ok(())
        })
    }

    fn enumerate_with(
        &self,
        mode: EnumerationMode,
        max_paths: u64,
        apply: impl Fn(&mut Battle) -> Result<(), EnumerationError>,
    ) -> Result<Vec<Outcome>, EnumerationError> {
        let mut path = PreciseState::new(mode);
        let mut outcomes: Vec<Outcome> = Vec::new();
        let mut indices: HashMap<BattleStateKey, usize> = HashMap::new();
        let mut paths = 0u64;
        loop {
            if paths == max_paths {
                return Err(EnumerationError(format!(
                    "the turn has more than {max_paths} chance paths (max_paths); \
                     raise the budget, use DamageEndpoints, or sample the turn instead"
                )));
            }
            paths += 1;
            let mut battle = self.clone();
            battle.rng = Rng::precise(path);
            apply(&mut battle)?;
            path = std::mem::replace(&mut battle.rng, self.rng.clone()).into_precise();
            let probability = path.probability;
            let key = battle.state_key();
            if let Some(&index) = indices.get(&key) {
                outcomes[index].probability += probability;
            } else {
                indices.insert(key, outcomes.len());
                outcomes.push(Outcome {
                    probability,
                    battle,
                });
            }
            if !path.advance() {
                break;
            }
        }
        Ok(outcomes)
    }
}
