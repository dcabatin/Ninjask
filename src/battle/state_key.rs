//! Structural solver state. Logs, PRNG position and diagnostics are excluded.
use super::*;
use std::hash::{Hash, Hasher};

/// Keys are scoped to a shared Dex allocation, retained to prevent address reuse.
#[derive(Clone, Debug)]
struct DexIdentity(Arc<Dex>);
impl PartialEq for DexIdentity {
    fn eq(&self, other: &Self) -> bool {
        Arc::ptr_eq(&self.0, &other.0)
    }
}
impl Eq for DexIdentity {}
impl Hash for DexIdentity {
    fn hash<H: Hasher>(&self, state: &mut H) {
        Arc::as_ptr(&self.0).hash(state);
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct SlotKey {
    wish: Option<(u64, u32, usize, String)>,
    healing_wish: Option<usize>,
    healing_wish_detached: bool,
    future: Option<FutureState>,
}
impl From<&SlotConds> for SlotKey {
    fn from(s: &SlotConds) -> Self {
        // Every field named, so a new one fails to compile until it is keyed.
        let SlotConds {
            wish,
            healing_wish,
            healing_wish_detached,
            future,
        } = s;
        Self {
            wish: wish.as_ref().map(|w| {
                let WishState {
                    hp,
                    starting_turn,
                    source_slot,
                    wisher,
                } = w;
                (
                    if *hp == 0.0 { 0 } else { hp.to_bits() },
                    *starting_turn,
                    *source_slot,
                    wisher.clone(),
                )
            }),
            healing_wish: *healing_wish,
            healing_wish_detached: *healing_wish_detached,
            future: *future,
        }
    }
}

/// Full simulator state, not a player's information-set key.
/// Equal keys share a Dex allocation. Independently loaded dexes stay distinct.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub struct BattleStateKey {
    dex: DexIdentity,
    slot_conds: [[SlotKey; 2]; 2],
    sides: [Side; 2],
    field: Field,
    turn: u32,
    queue: VecDeque<Action>,
    mid_turn: bool,
    request: RequestState,
    rqid: u32,
    force_switch: [[bool; 2]; 2],
    ended: bool,
    winner: Option<usize>,
    last_fainted_side: Option<usize>,
    faint_queue: Vec<MonRef>,
    move_hit: u8,
    smart_target: bool,
    bounce: Option<i8>,
    priority_override: Option<(MonRef, MoveId, i8)>,
    ability_breaker: Option<MonRef>,
    relocated: Option<(MonRef, MonRef)>,
    probe: bool,
    vol_stamp: u32,
    side_extras: [crate::state::SideExtras; 2],
    active_move: crate::state::ActiveMove,
    turn_state: crate::state::TurnState,
}

impl From<&Battle> for BattleStateKey {
    fn from(b: &Battle) -> Self {
        // Every field of `Battle` is named here, with no `..`: a field added
        // to `Battle` fails to compile until it is keyed below or listed as
        // excluded, so outcomes can't silently merge on a forgotten field.
        let Battle {
            dex,
            sides,
            field,
            turn,
            queue,
            mid_turn,
            request,
            rqid,
            force_switch,
            ended,
            winner,
            last_fainted_side,
            faint_queue,
            move_hit,
            smart_target,
            bounce,
            priority_override,
            ability_breaker,
            relocated,
            probe,
            vol_stamp,
            slot_conds,
            side_extras,
            active_move,
            turn_state,
            // Excluded: the generator's position (enumeration doesn't draw),
            // and what the log shows and diagnostics, which don't change
            // what happens next.
            rng: _,
            log: _,
            log_enabled: _,
            last_move_line: _,
            damage_samples: _,
        } = b;
        Self {
            dex: DexIdentity(dex.clone()),
            slot_conds: std::array::from_fn(|s| {
                std::array::from_fn(|i| SlotKey::from(&slot_conds[s][i]))
            }),
            sides: sides.clone(),
            field: *field,
            turn: *turn,
            queue: queue.clone(),
            mid_turn: *mid_turn,
            request: *request,
            rqid: *rqid,
            force_switch: *force_switch,
            ended: *ended,
            winner: *winner,
            last_fainted_side: *last_fainted_side,
            faint_queue: faint_queue.clone(),
            move_hit: *move_hit,
            smart_target: *smart_target,
            bounce: *bounce,
            priority_override: *priority_override,
            ability_breaker: *ability_breaker,
            relocated: *relocated,
            probe: *probe,
            vol_stamp: *vol_stamp,
            side_extras: *side_extras,
            active_move: active_move.clone(),
            turn_state: turn_state.clone(),
        }
    }
}

impl Battle {
    pub fn state_key(&self) -> BattleStateKey {
        self.into()
    }
}
