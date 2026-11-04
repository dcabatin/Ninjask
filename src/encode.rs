//! Fixed-size numeric encodings of a player's view and of its choices, for
//! models: no JSON and no strings. The Python classes hand them over as
//! `bytes` that `numpy.frombuffer` reads without copying (little-endian).
//!
//! - [`observation`]: `OBS_LEN` floats from a [`Tracker`]'s view, so only
//!   what that player knows.
//! - [`ids`]: `IDS_LEN` small integers, the categorical part of the same
//!   view (species, item, ability, moves) for embedding tables, `0` meaning
//!   unknown.
//! - [`action_row`]: `ACTION_FIELDS` integers per active slot of a joint
//!   choice, in the order [`Battle::player_choices`] lists them.
//!
//! The player's own side comes first and the foe's second, whichever side
//! the player is; each side has six Pokémon slots in team order.

use crate::dex::types::Status;
use crate::state::{SideCond, SlotChoice, RECHARGE};
use crate::tracker::{ItemView, Tracker};

/// Floats per Pokémon in [`observation`]: HP fraction, fainted, seen,
/// active, status one-hot (none, burn, paralysis, poison, toxic, sleep,
/// freeze), the seven boost stages over 6, Mega Evolved.
pub const MON_FLOATS: usize = 4 + 7 + 7 + 1;
/// Length of [`observation`]: two sides of six Pokémon and their six side
/// conditions (Tailwind, Reflect, Light Screen, Aurora Veil, Wide Guard,
/// Quick Guard), then weather (rain, sun, sand, snow), terrain (grassy,
/// psychic, electric, misty), Trick Room and Gravity.
pub const OBS_LEN: usize = 2 * (6 * MON_FLOATS + 6) + 4 + 4 + 2;
/// Integers per Pokémon in [`ids`]: species, item, ability, four moves.
pub const MON_IDS: usize = 7;
/// Length of [`ids`]: two sides of six Pokémon.
pub const IDS_LEN: usize = 2 * 6 * MON_IDS;
/// Integers per active slot in [`action_row`]: kind (0 pass, 1 move,
/// 2 switch, 3 Struggle or recharge), index (the move's slot 1 to 4, or the
/// team position 1 to 6 to switch to), target (foes 1 and 2, allies -1 and
/// -2, 0 for none), Mega Evolution (0 or 1).
pub const ACTION_FIELDS: usize = 4;

/// Append the view's [`OBS_LEN`] floats to `out`.
pub fn observation(t: &Tracker, out: &mut Vec<f32>) {
    let start = out.len();
    let flag = |b: bool| b as u8 as f32;
    for side in [t.me, 1 - t.me] {
        let sv = &t.sides[side];
        for i in 0..6 {
            let Some(m) = sv.mons.get(i) else {
                out.extend(std::iter::repeat_n(0.0, MON_FLOATS));
                continue;
            };
            out.push(if m.maxhp > 0 {
                m.hp as f32 / m.maxhp as f32
            } else {
                0.0
            });
            out.push(flag(m.fainted));
            out.push(flag(m.seen));
            out.push(flag(m.slot.is_some()));
            let status = match m.status {
                Status::None | Status::Fnt => 0,
                Status::Brn => 1,
                Status::Par => 2,
                Status::Psn => 3,
                Status::Tox => 4,
                Status::Slp => 5,
                Status::Frz => 6,
            };
            out.extend((0..7).map(|k| flag(k == status)));
            out.extend(m.boosts.iter().map(|&b| b as f32 / 6.0));
            out.push(flag(m.mega));
        }
        for c in SideCond::ALL {
            out.push(flag(sv.conditions.iter().any(|(k, _)| *k == c)));
        }
    }
    let weather = t.field.weather.map(|(w, _)| w as usize);
    out.extend((0..4).map(|k| flag(weather == Some(k))));
    let terrain = t.field.terrain.map(|(x, _)| x as usize);
    out.extend((0..4).map(|k| flag(terrain == Some(k))));
    out.push(flag(t.field.trick_room.is_some()));
    out.push(flag(t.field.gravity.is_some()));
    debug_assert_eq!(out.len() - start, OBS_LEN);
}

/// Append the view's [`IDS_LEN`] categorical values to `out`. Each is a
/// dex index plus one (0: unknown), except the item: 0 unknown, 1 known to
/// hold nothing, else the item's index plus two. The dex sizes bound them
/// (`Dex.vocab()` in Python).
pub fn ids(t: &Tracker, out: &mut Vec<i16>) {
    let start = out.len();
    for side in [t.me, 1 - t.me] {
        let sv = &t.sides[side];
        for i in 0..6 {
            let Some(m) = sv.mons.get(i) else {
                out.extend(std::iter::repeat_n(0, MON_IDS));
                continue;
            };
            out.push(m.species.map_or(0, |x| x as i16 + 1));
            out.push(match m.item {
                ItemView::Unknown => 0,
                ItemView::None => 1,
                ItemView::Known(x) => x as i16 + 2,
            });
            out.push(m.ability.map_or(0, |x| x as i16 + 1));
            for k in 0..4 {
                out.push(m.moves.get(k).map_or(0, |mv| mv.id as i16 + 1));
            }
        }
    }
    debug_assert_eq!(out.len() - start, IDS_LEN);
}

/// Append a joint choice's [`ACTION_FIELDS`] values per slot to `out`.
pub fn action_row(choice: &[SlotChoice], out: &mut Vec<i8>) {
    for c in choice {
        match *c {
            SlotChoice::Pass => out.extend([0, 0, 0, 0]),
            SlotChoice::Switch { index } => out.extend([2, index as i8 + 1, 0, 0]),
            SlotChoice::Move { slot, target, mega } if slot >= RECHARGE => {
                out.extend([3, 1, target, mega as i8])
            }
            SlotChoice::Move { slot, target, mega } => {
                out.extend([1, slot as i8 + 1, target, mega as i8])
            }
        }
    }
}
