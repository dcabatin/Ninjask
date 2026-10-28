//! A fast simulator of Pokémon Champions VGC doubles (Reg M-C), checked against
//! Pokémon Showdown at a pinned commit (see README.md).

/// Append a line to the battle log, formatting it only when logging is on
/// (self-play runs with logging off).
macro_rules! log_line {
    ($battle:expr, $($arg:tt)*) => {
        if $battle.log_enabled {
            let line = format!($($arg)*);
            $battle.add(line);
        }
    };
}

pub mod abilities;
pub mod battle;
pub mod boosts;
pub mod calc;
pub mod conditions;
pub mod dex;
pub mod events;
pub mod items;
pub mod kinds;
pub mod moves;
pub mod projection;
pub mod rng;
pub mod state;
pub mod tracker;
pub mod transform;
pub mod world;

#[cfg(feature = "python")]
pub mod python;

pub use battle::Battle;
pub use dex::Dex;
