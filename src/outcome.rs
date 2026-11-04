//! Weighted successor states for search and regret minimization.
use crate::Battle;

/// Whether to enumerate all damage rolls or approximate them by endpoints.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq, Hash)]
pub enum EnumerationMode {
    #[default]
    Precise,
    /// An approximation, not Showdown's distribution: each damage roll is
    /// replaced by its lowest and highest value, half the probability each.
    /// All other chance events remain exact. It undercounts or overcounts
    /// KOs when the roll range straddles one: on "who faints this turn" it
    /// was off by up to 42 percentage points in one of 28 real mid-game
    /// states measured (`examples/validate_outcomes.rs`), and exact in most.
    DamageEndpoints,
}

#[derive(Clone, Debug)]
pub struct Outcome {
    pub probability: f64,
    pub battle: Battle,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct EnumerationError(pub String);

impl std::fmt::Display for EnumerationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.0.fmt(f)
    }
}
impl std::error::Error for EnumerationError {}
