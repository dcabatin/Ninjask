//! Choices: parsing and validating them, the legal ones as structured values (no
//! strings, for self-play and search; mirrors `python/ninjask/choices.py`), and the
//! request sent to each player.

use arrayvec::ArrayVec;
use serde_json::{json, Value};

use super::log::secret_health;
use super::{Battle, MonRef};
use crate::dex::types::*;
use crate::state::*;

impl Battle {
    /// Submit a choice for a side, in Showdown's syntax (`Battle.choose`).
    /// When both sides have chosen, the turn (or preview, or switch phase)
    /// runs. A side that has chosen may choose again until then, which
    /// replaces its choice (the format has Cancel Mod); a refused choice
    /// leaves it with none, and one made while a hidden trap or disable could
    /// have been in play can't be replaced (see [`choose_text`]).
    pub fn choose(&mut self, s: usize, input: &str) -> Result<(), String> {
        if self.ended {
            return Err("Can't do anything: The game is over".into());
        }
        let has_request = match self.request {
            RequestState::TeamPreview => true,
            RequestState::Move | RequestState::Switch => {
                self.side_needs_choice(s) || self.sides[s].choice.is_some()
            }
            RequestState::Ended => false,
        };
        if !has_request {
            return Err("Can't do anything: It's not your turn".into());
        }
        if self.sides[s].cant_undo {
            return Err(
                "Can't undo: A trapping/disabling effect would cause undo to leak information"
                    .into(),
            );
        }
        // clearChoice
        self.sides[s].choice = None;
        self.sides[s].team_preview_choice = None;
        let reading = self.read_choice(s, input);
        self.sides[s].cant_undo = reading.cant_undo;
        self.sides[s].choice_moves = reading.moves;
        for (slot, what) in reading.reveals {
            self.record_reveal(s, slot, what);
        }
        match reading.result? {
            super::choose_text::Chosen::Team(order) => {
                self.sides[s].team_preview_choice = Some(order)
            }
            super::choose_text::Chosen::Slots(choice) => self.sides[s].choice = Some(choice),
        }
        if self.all_choices_done() {
            self.commit();
        }
        Ok(())
    }

    /// Showdown's `undoChoice` (a server's `/undo`): take back the side's
    /// choice, unless it can't be undone. Undoing a move that a hidden
    /// disable may have decided reveals the disables, as a refusal would.
    pub fn undo_choice(&mut self, s: usize) -> Result<(), String> {
        let has_request = match self.request {
            RequestState::TeamPreview => true,
            RequestState::Move | RequestState::Switch => {
                self.side_needs_choice(s) || self.sides[s].choice.is_some()
            }
            RequestState::Ended => false,
        };
        if self.ended || !has_request {
            return Ok(());
        }
        if self.sides[s].cant_undo {
            return Err(
                "Can't undo: A trapping/disabling effect would cause undo to leak information"
                    .into(),
            );
        }
        if self.request == RequestState::Move {
            for slot in std::mem::take(&mut self.sides[s].choice_moves) {
                if self.maybe_locked(s, slot) {
                    self.record_reveal(s, slot, Reveal::Disabled);
                }
            }
        }
        self.sides[s].choice = None;
        self.sides[s].team_preview_choice = None;
        self.sides[s].choice_moves.clear();
        Ok(())
    }

    /// Remember what a refused choice ("[Unavailable choice]") revealed for
    /// the current request, which the next request shows.
    fn record_reveal(&mut self, s: usize, slot: usize, what: Reveal) {
        let rqid = self.rqid;
        let side = &mut self.sides[s];
        if side.reveals_rqid != rqid {
            side.reveals.clear();
            side.reveals_rqid = rqid;
        }
        if !side.reveals.contains(&(slot, what)) {
            side.reveals.push((slot, what));
        }
    }

    /// Whether a refused choice revealed this for the current request.
    pub(super) fn revealed(&self, s: usize, slot: usize, what: Reveal) -> bool {
        let side = &self.sides[s];
        side.reveals_rqid == self.rqid && side.reveals.contains(&(slot, what))
    }

    pub fn side_needs_choice(&self, s: usize) -> bool {
        match self.request {
            RequestState::TeamPreview => self.sides[s].team_preview_choice.is_none(),
            RequestState::Move => self.sides[s].choice.is_none() && self.side_has_actives(s),
            RequestState::Switch => {
                self.sides[s].choice.is_none() && self.force_switch[s].iter().any(|&f| f)
            }
            RequestState::Ended => false,
        }
    }

    fn side_has_actives(&self, s: usize) -> bool {
        (0..self.sides[s].n_active).any(|slot| {
            self.active(s, slot)
                .map(|r| !self.mon(r).fainted)
                .unwrap_or(false)
        })
    }

    pub(super) fn all_choices_done(&self) -> bool {
        (0..2).all(|s| !self.side_needs_choice(s))
    }

    pub(super) fn move_usable(&self, r: MonRef, idx: usize) -> bool {
        !self.move_disabled(r, idx)
    }

    /// Showdown `validTargetLoc` for doubles.
    #[inline(always)]
    pub(super) fn valid_target_loc(&self, s: usize, slot: usize, loc: i8, target: Target) -> bool {
        let n = self.sides[s].n_active as i8;
        if loc == 0 || loc.abs() > n {
            return false;
        }
        let source_loc = -(slot as i8 + 1);
        let is_self = loc == source_loc;
        let is_foe = loc > 0;
        let across = -(n + 1 - loc);
        let adjacent = if loc > 0 {
            (across - source_loc).abs() <= 1
        } else {
            (loc - source_loc).abs() == 1
        };
        match target {
            Target::RandomNormal | Target::Scripted | Target::Normal => adjacent,
            Target::AdjacentAlly => adjacent && !is_foe,
            Target::AdjacentAllyOrSelf => (adjacent && !is_foe) || is_self,
            Target::AdjacentFoe => adjacent && is_foe,
            Target::Any => !is_self,
            _ => false,
        }
    }

    /// Showdown `canMegaEvo` (Champions): the held item's Mega Stone for this species.
    pub fn can_mega_evo(&self, r: MonRef) -> Option<SpeciesId> {
        let m = self.mon(r);
        if m.mega_evolved {
            return None;
        }
        self.dex.mega_forme(m.item?, m.base_species)
    }

    /// Whether `s` has something to decide in the current request, whether
    /// or not it has chosen already (Showdown's `requests[i]` before the
    /// waiting ones are filled in).
    fn has_request(&self, s: usize) -> bool {
        match self.request {
            RequestState::Ended => false,
            RequestState::TeamPreview => true,
            RequestState::Switch => self.force_switch[s].iter().any(|&f| f),
            RequestState::Move => self.side_has_actives(s),
        }
    }

    /// The request as Showdown sends it (`Battle.makeRequest`), as JSON.
    pub fn request_json(&self, s: usize) -> Option<Value> {
        let mut request = self.request_json_without_cancel(s)?;
        // With Cancel Mod a request can be answered again until the other
        // side has answered theirs, so `noCancel` is for a request that is
        // alone (the other side waits).
        if request.get("wait").is_none() && !(self.has_request(0) && self.has_request(1)) {
            request["noCancel"] = json!(true);
        }
        // A refused choice that gave something away has the request sent
        // again with `update` set, and it stays set.
        let side = &self.sides[s];
        if request.get("wait").is_none()
            && side.reveals_rqid == self.rqid
            && !side.reveals.is_empty()
        {
            request["update"] = json!(true);
        }
        Some(request)
    }

    fn request_json_without_cancel(&self, s: usize) -> Option<Value> {
        let side_data = self.side_request_data(s);
        match self.request {
            RequestState::Ended => None,
            RequestState::TeamPreview => {
                let size = self
                    .dex
                    .format
                    .picked_team_size
                    .unwrap_or(self.sides[s].mons.len());
                // `maxChosenTeamSize` is left out when the format picks no subset.
                let mut request =
                    json!({"teamPreview": true, "side": side_data, "rqid": self.rqid});
                if self.dex.format.picked_team_size.is_some() {
                    request["maxChosenTeamSize"] = json!(size);
                }
                Some(request)
            }
            RequestState::Switch => {
                if !self.force_switch[s].iter().any(|&f| f) {
                    return Some(json!({"wait": true, "side": side_data, "rqid": self.rqid}));
                }
                let n = self.sides[s].n_active;
                let flags: Vec<bool> = (0..n).map(|i| self.force_switch[s][i]).collect();
                Some(json!({"forceSwitch": flags, "side": side_data, "rqid": self.rqid}))
            }
            RequestState::Move => {
                if !self.side_has_actives(s) {
                    return Some(json!({"wait": true, "side": side_data, "rqid": self.rqid}));
                }
                let mut active = Vec::new();
                for slot in 0..self.sides[s].n_active {
                    let Some(r) = self.active(s, slot) else {
                        continue;
                    };
                    let m = self.mon(r);
                    if let Some(lock) = self.locked_move(r) {
                        // getMoveRequestData for a locked Pokémon: one move, trapped.
                        let (name, id) = match lock {
                            crate::moves::LockedMove::Recharge => {
                                ("Recharge".to_string(), "recharge".to_string())
                            }
                            crate::moves::LockedMove::Move(mv) => {
                                (self.dex.mv(mv).name.clone(), self.dex.mv(mv).id.clone())
                            }
                        };
                        active.push(json!({"moves": [{"move": name, "id": id}], "trapped": true}));
                        continue;
                    }
                    let mut moves: Vec<Value> = m
                        .moves
                        .iter()
                        .enumerate()
                        .map(|(i, ms)| {
                            let mv = self.dex.mv(ms.id);
                            json!({
                                "move": mv.name, "id": mv.id, "pp": ms.pp, "maxpp": ms.max_pp,
                                "target": self.move_target_at_choice(r, mv).id(), "disabled": self.request_move_disabled(r, i),
                            })
                        })
                        .collect();
                    // A refused choice of a move changed its entry: disabled,
                    // with an empty `disabledSource` (`getMoves` gives its
                    // entries none, so the update sets it to "").
                    for (i, mv) in moves.iter_mut().enumerate() {
                        if self.revealed(s, slot, Reveal::Move(i as u8)) {
                            mv["disabled"] = json!(true);
                            mv["disabledSource"] = json!("");
                        }
                    }
                    // A refused choice revealed the stored disabled flags
                    // (updateDisabledRequest).
                    if self.sides[s].n_active > 1 && self.revealed(s, slot, Reveal::Disabled) {
                        for (mv, ms) in moves.iter_mut().zip(&m.moves) {
                            if ms.disabled {
                                mv["disabled"] = json!(true);
                            }
                        }
                    }
                    // getMoveRequestData: with no move shown usable, Struggle is the only one.
                    let struggle_only =
                        (0..m.moves.len()).all(|i| self.request_move_disabled(r, i));
                    if struggle_only {
                        moves = vec![
                            json!({"move": "Struggle", "id": "struggle", "target": "randomNormal", "disabled": false}),
                        ];
                    }
                    let mut entry = json!({"moves": moves});
                    // getMoveRequestData: a hidden trap shows only to the
                    // side's other Pokémon; the last active is told it may be trapped.
                    // The last active is told a foe's Imprison may disable
                    // moves it hasn't revealed, until a refused choice does.
                    let imprisoned_by_foe = self
                        .foes_of(r)
                        .into_iter()
                        .any(|f| self.mon(f).vol.imprison);
                    // In singles a refusal clears `maybeLocked` only: `maybeDisabled`
                    // (`updateDisabledRequest`) stays.
                    if imprisoned_by_foe && self.is_last_active(r) {
                        let revealed = self.revealed(s, slot, Reveal::Disabled);
                        if !revealed || self.sides[s].n_active == 1 {
                            entry["maybeDisabled"] = json!(true);
                        }
                        if !revealed {
                            entry["maybeLocked"] = json!(true);
                        }
                    }
                    let can_switch = (self.sides[s].n_active..self.sides[s].mons.len())
                        .any(|i| !self.sides[s].mons[i].fainted);
                    if can_switch {
                        let revealed = self.revealed(s, slot, Reveal::Trapped);
                        if m.trapped == Trapped::Yes
                            || (m.trapped == Trapped::Hidden
                                && (!self.is_last_active(r) || revealed))
                        {
                            entry["trapped"] = json!(true);
                        } else if m.maybe_trapped && self.is_last_active(r) {
                            entry["maybeTrapped"] = json!(true);
                        }
                    }
                    // `updateDisabledRequest` takes the Mega Evolution away once every
                    // move of the updated request is disabled.
                    let updated = self.revealed(s, slot, Reveal::Disabled)
                        || (0..m.moves.len())
                            .any(|i| self.revealed(s, slot, Reveal::Move(i as u8)));
                    let all_disabled = entry["moves"].as_array().is_some_and(|ms| {
                        ms.iter()
                            .all(|mv| mv["disabled"] == json!(true) || mv["id"] == "struggle")
                    });
                    // Struggle is a locked move: no Mega Evolution is offered with it.
                    if !self.sides[s].mega_used
                        && self.can_mega_evo(r).is_some()
                        && !struggle_only
                        && !(updated && all_disabled)
                    {
                        entry["canMegaEvo"] = json!(true);
                    }
                    active.push(entry);
                }
                Some(json!({"active": active, "side": side_data, "rqid": self.rqid}))
            }
        }
    }

    /// A move's `disabled` in the request (`getMoves`). A Pokémon that fainted
    /// this turn is not active when the end-of-turn pass runs, so its moves
    /// show the flags that pass left on them last time (`store_disabled_moves`).
    pub(crate) fn request_move_disabled(&self, r: MonRef, idx: usize) -> bool {
        let m = self.mon(r);
        if m.fainted {
            return m.moves[idx].disabled || m.moves[idx].pp == 0;
        }
        self.move_shown_disabled(r, idx)
    }

    fn side_request_data(&self, s: usize) -> Value {
        let side = &self.sides[s];
        let pokemon: Vec<Value> = side
            .mons
            .iter()
            .enumerate()
            .map(|(i, m)| {
                // baseStoredStats: its own, also while transformed, and
                // without Speed Swap and the splits.
                let st = self.base_stored_stats((s, i));
                let stats = json!({
                    "atk": st[1], "def": st[2], "spa": st[3], "spd": st[4], "spe": st[5],
                });
                json!({
                    "ident": format!("{}: {}", side.id(), m.name),
                    "details": self.details(s, i),
                    "condition": secret_health(m.hp, m.maxhp, m.status.id()),
                    // At team preview the first Pokémon already read as active.
                    "active": m.is_active || (self.request == RequestState::TeamPreview && i < side.n_active),
                    "stats": stats,
                    "moves": m.moves.iter().map(|ms| self.dex.mv(ms.id).id.clone()).collect::<Vec<_>>(),
                    "baseAbility": self.dex.ability(m.base_ability).id,
                    "item": m.item.map(|it| self.dex.item(it).id.clone()).unwrap_or_default(),
                    "pokeball": "pokeball",
                    "ability": self.dex.ability(m.ability).id,
                    "commanding": false,
                    "reviving": m.is_active && m.position < 2 && side.revival[m.position],
                })
            })
            .collect();
        json!({"name": side.name, "id": side.id(), "pokemon": pokemon})
    }

    /// Legal options for one active slot of a side, ignoring the other slot.
    /// They are what Showdown accepts, so they can reflect what the player
    /// does not know (a foe's unseen Imprison, a hidden trap); see
    /// [`Battle::player_slot_options`] for the player's view.
    pub fn slot_options(&self, s: usize, slot: usize) -> Vec<SlotChoice> {
        let mut out = Vec::with_capacity(self.slot_options_bound(s, slot));
        self.slot_options_with::<false>(s, slot, &mut |c| out.push(c));
        out
    }

    /// The options one slot's request offers its player: what
    /// `python/ninjask/choices.py` lists from the request, without building
    /// it. A move is left out only when the request shows it disabled, and
    /// switches only when the request shows the Pokémon trapped, so nothing
    /// the player can't see shapes the list; a listed option can then be
    /// refused, as in Showdown, and the refusal reveals why.
    pub fn player_slot_options(&self, s: usize, slot: usize) -> Vec<SlotChoice> {
        let mut out = Vec::with_capacity(self.slot_options_bound(s, slot));
        self.slot_options_with::<true>(s, slot, &mut |c| out.push(c));
        out
    }

    /// An upper bound on `slot_options(s, slot).len()`: every move at every
    /// target, with and without Mega Evolution, then Struggle and the
    /// switches (or Pass).
    fn slot_options_bound(&self, s: usize, slot: usize) -> usize {
        let side = &self.sides[s];
        let moves = self.active(s, slot).map_or(0, |r| self.mon(r).moves.len());
        moves * 2 * (2 * side.n_active) + side.mons.len() + 1
    }

    /// `slot_options` (or with `PLAYER`, `player_slot_options`), handing each
    /// option to `push` in order.
    fn slot_options_with<const PLAYER: bool>(
        &self,
        s: usize,
        slot: usize,
        push: &mut impl FnMut(SlotChoice),
    ) {
        let side = &self.sides[s];
        let bench = || (side.n_active..side.mons.len()).filter(|&i| !side.mons[i].fainted);
        if self.request == RequestState::Switch {
            if !self.force_switch_flag(s, slot) {
                return push(SlotChoice::Pass);
            }
            if side.revival[slot] {
                // Revival Blessing: a fainted teammate, wherever it is.
                let mut any = false;
                for i in (0..side.mons.len()).filter(|&i| side.mons[i].fainted) {
                    any = true;
                    push(SlotChoice::Switch { index: i });
                }
                if !any {
                    push(SlotChoice::Pass);
                }
                return;
            }
            for i in bench() {
                push(SlotChoice::Switch { index: i });
            }
            return push(SlotChoice::Pass);
        }
        let Some(r) = self.active(s, slot) else {
            return push(SlotChoice::Pass);
        };
        let m = self.mon(r);
        if m.fainted {
            return push(SlotChoice::Pass);
        }
        // A locked Pokémon (recharging, or striking with a charged move) has
        // one choice, "move 1": no target, no Mega Evolution, no switch.
        if self.locked_move(r).is_some() {
            return push(SlotChoice::Move {
                slot: 0,
                target: 0,
                mega: false,
            });
        }
        // The player's view (request_json): moves shown disabled, or
        // revealed disabled by a refused choice, are out; with every move
        // shown disabled the request offers Struggle alone; switches are
        // out only when the request shows a trap.
        let shown_trapped = || {
            m.trapped == Trapped::Yes
                || (m.trapped == Trapped::Hidden
                    && (!self.is_last_active(r) || self.revealed(s, slot, Reveal::Trapped)))
        };
        let revealed_disabled =
            PLAYER && side.n_active > 1 && self.revealed(s, slot, Reveal::Disabled);
        let player_struggle = PLAYER && (0..m.moves.len()).all(|i| self.move_shown_disabled(r, i));
        // Every move at every target, with and without Mega Evolution, then
        // Struggle or the switches.
        let can_mega = !side.mega_used && self.can_mega_evo(r).is_some();
        // Moves the Pokémon can use. Imprison's hidden disables leave others
        // in the last active Pokémon's request, but Showdown refuses them.
        // `move_disabled`, with Imprison's test of the foes made once.
        let imprison = !PLAYER && self.foe_side_imprisons(s);
        let usable = (0..m.moves.len()).filter(|&i| {
            if PLAYER {
                !player_struggle
                    && !(self.move_shown_disabled(r, i)
                        || (revealed_disabled && m.moves[i].disabled)
                        || self.revealed(s, slot, Reveal::Move(i as u8)))
            } else {
                !(self.move_disabled_openly(r, i) || (imprison && self.move_imprisoned(r, i)))
            }
        });
        // The target locations of each target type met so far: they depend
        // only on the type here.
        type Locs = ArrayVec<i8, { 2 * MAX_ACTIVE_PER_SIDE }>;
        let mut by_type: ArrayVec<(Target, Locs), 4> = ArrayVec::new();
        let mut any_usable = false;
        for i in usable {
            any_usable = true;
            let mv = self.dex.mv(m.moves[i].id);
            let tt = self.move_target_at_choice(r, mv);
            // Target locations: 1..=n on the foe side, -1..=-n on the user's.
            let mut targets = Locs::new();
            if side.n_active > 1 && tt.needs_choice() {
                if let Some((_, known)) = by_type.iter().find(|(t, _)| *t == tt) {
                    targets = known.clone();
                } else {
                    let n = side.n_active as i8;
                    for t in (1..=n).chain((1..=n).map(|x| -x)) {
                        if self.target_ok(s, slot, t, tt) {
                            targets.push(t);
                        }
                    }
                    if !by_type.is_full() {
                        by_type.push((tt, targets.clone()));
                    }
                }
            } else {
                targets.push(0);
            }
            // The request's order (choices.py): the user's own location
            // before its allies'.
            if PLAYER && tt == Target::AdjacentAllyOrSelf {
                let own = -(slot as i8 + 1);
                if let Some(k) = targets.iter().position(|&t| t == own) {
                    targets[..=k].rotate_right(1);
                }
            }
            for &t in &targets {
                push(SlotChoice::Move {
                    slot: i,
                    target: t,
                    mega: false,
                });
                if can_mega {
                    push(SlotChoice::Move {
                        slot: i,
                        target: t,
                        mega: true,
                    });
                }
            }
        }
        if PLAYER {
            if player_struggle {
                // The request's one move, Struggle, which ignores "mega".
                push(SlotChoice::Move {
                    slot: STRUGGLE,
                    target: 0,
                    mega: false,
                });
            }
            let mut any_switch = false;
            if !shown_trapped() {
                for i in bench() {
                    any_switch = true;
                    push(SlotChoice::Switch { index: i });
                }
            }
            // Nothing offered (every move revealed disabled, and trapped):
            // Showdown accepts "move 1".
            if !any_usable && !player_struggle && !any_switch {
                push(SlotChoice::Move {
                    slot: 0,
                    target: 0,
                    mega: false,
                });
            }
            return;
        }
        // A trapped Pokémon can't switch (Showdown refuses it even when the
        // request hid the trap).
        if !any_usable {
            // No usable move: Struggle, which ignores "mega".
            push(SlotChoice::Move {
                slot: STRUGGLE,
                target: 0,
                mega: false,
            });
        }
        if m.trapped == crate::state::Trapped::No {
            for i in bench() {
                push(SlotChoice::Switch { index: i });
            }
        }
    }

    /// All legal joint choices for a side's current request: what Showdown
    /// accepts, which can depend on what the player can't see (see
    /// [`Battle::player_choices`]).
    pub fn joint_choices(&self, s: usize) -> Vec<Vec<SlotChoice>> {
        let n = self.sides[s].n_active;
        let per_slot: Vec<Vec<SlotChoice>> =
            (0..n).map(|slot| self.slot_options(s, slot)).collect();
        self.combine_slot_options(per_slot)
    }

    /// The joint choices a side's request offers its player, in
    /// `python/ninjask/choices.py`'s order (ally targets included): from
    /// [`Battle::player_slot_options`], so they depend only on what the
    /// player can see. Empty without a move or switch request.
    pub fn player_choices(&self, s: usize) -> Vec<Vec<SlotChoice>> {
        if !matches!(self.request, RequestState::Move | RequestState::Switch)
            || !self.side_needs_choice(s)
        {
            return Vec::new();
        }
        let n = self.sides[s].n_active;
        let per_slot: Vec<Vec<SlotChoice>> = (0..n)
            .map(|slot| self.player_slot_options(s, slot))
            .collect();
        self.combine_slot_options(per_slot)
    }

    /// Every team-preview pick a side can make, as team positions (0-based)
    /// in the order they are sent: each choice of the brought Pokémon, and
    /// within it each pair of leads, the rest in team order
    /// (`team_preview_choices` in `python/ninjask/choices.py`). Empty outside
    /// team preview or once the side has picked.
    pub fn team_preview_orders(&self, s: usize) -> Vec<Vec<usize>> {
        let mut out = Vec::new();
        self.for_each_team_preview_order(s, &mut |o| out.push(o.to_vec()));
        out
    }

    /// Each order `team_preview_orders(s)` lists, in its order, handed to
    /// `f` without building the list.
    pub fn for_each_team_preview_order(&self, s: usize, f: &mut impl FnMut(&[usize])) {
        if self.request != RequestState::TeamPreview || !self.side_needs_choice(s) {
            return;
        }
        let n = self.sides[s].mons.len();
        let bring = self.dex.format.picked_team_size.unwrap_or(n).min(n);
        let lead = bring.min(2);
        // Teams have at most six Pokémon (TEAM_MAX).
        let mut picked = [0usize; TEAM_MAX];
        let mut order = [0usize; TEAM_MAX];
        let mut leads = [0usize; 2];
        for_each_subset(n, bring, &mut picked, 0, 0, &mut |picked| {
            for_each_subset(picked.len(), lead, &mut leads, 0, 0, &mut |leads| {
                let mut k = 0;
                for &l in leads {
                    order[k] = picked[l];
                    k += 1;
                }
                for (j, &p) in picked.iter().enumerate() {
                    if !leads.contains(&j) {
                        order[k] = p;
                        k += 1;
                    }
                }
                f(&order[..k]);
            });
        });
    }

    /// Joint choices from each slot's options: Showdown refuses two
    /// switches to one Pokémon and two Mega Evolutions, and a forced switch
    /// must fill as many slots as it can.
    fn combine_slot_options(&self, per_slot: Vec<Vec<SlotChoice>>) -> Vec<Vec<SlotChoice>> {
        let mut out = Vec::new();
        self.for_each_combination(&per_slot, &mut |c| out.push(c.to_vec()));
        out
    }

    /// Each joint choice `player_choices(s)` lists, in its order, handed to
    /// `f` without building the list (no allocation per choice).
    pub fn for_each_player_choice(&self, s: usize, f: &mut impl FnMut(&[SlotChoice])) {
        if !matches!(self.request, RequestState::Move | RequestState::Switch)
            || !self.side_needs_choice(s)
        {
            return;
        }
        let n = self.sides[s].n_active;
        let per_slot: ArrayVec<Vec<SlotChoice>, MAX_ACTIVE_PER_SIDE> = (0..n)
            .map(|slot| self.player_slot_options(s, slot))
            .collect();
        self.for_each_combination(&per_slot, f);
    }

    /// The joint choices of each slot's options in product order, keeping
    /// those Showdown accepts (no two switches to one Pokémon, at most one
    /// Mega Evolution; a forced switch fills as many slots as it can).
    fn for_each_combination(
        &self,
        per_slot: &[Vec<SlotChoice>],
        f: &mut impl FnMut(&[SlotChoice]),
    ) {
        type Joint = ArrayVec<SlotChoice, MAX_ACTIVE_PER_SIDE>;
        /// The number of switches of an acceptable joint choice.
        fn switches(c: &[SlotChoice]) -> Option<usize> {
            let mut to: ArrayVec<usize, MAX_ACTIVE_PER_SIDE> = ArrayVec::new();
            let mut megas = 0;
            for x in c {
                match *x {
                    SlotChoice::Switch { index } => {
                        if to.contains(&index) {
                            return None;
                        }
                        to.push(index);
                    }
                    SlotChoice::Move { mega: true, .. } => megas += 1,
                    _ => {}
                }
            }
            (megas <= 1).then_some(to.len())
        }
        fn rec(per_slot: &[Vec<SlotChoice>], cur: &mut Joint, f: &mut dyn FnMut(&[SlotChoice])) {
            let Some((first, rest)) = per_slot.split_first() else {
                return f(cur);
            };
            for c in first {
                cur.push(*c);
                rec(rest, cur, f);
                cur.pop();
            }
        }
        let mut cur = Joint::new();
        let most = if self.request == RequestState::Switch {
            let mut most = None;
            rec(per_slot, &mut cur, &mut |c| {
                if let Some(k) = switches(c) {
                    most = Some(most.map_or(k, |m: usize| m.max(k)));
                }
            });
            most
        } else {
            None
        };
        rec(per_slot, &mut cur, &mut |c| {
            if let Some(k) = switches(c) {
                if most.is_none_or(|m| k == m) {
                    f(c);
                }
            }
        });
    }

    /// A random legal joint choice, drawing each slot's option uniformly and
    /// redrawing combinations Showdown rejects (two switches to one
    /// Pokémon, two Mega Evolutions, a forced switch left unfilled). Cheaper
    /// than enumerating `joint_choices`; not uniform over joint choices.
    pub fn sample_joint_choice(&self, s: usize, rng: &mut crate::rng::Rng) -> Vec<SlotChoice> {
        let n = self.sides[s].n_active;
        // Each slot's options, on the stack when they fit (always, for
        // teams of at most six with four moves each), filled in place.
        let mut inline: [ArrayVec<SlotChoice, SLOT_OPTIONS_INLINE>; MAX_ACTIVE_PER_SIDE] =
            Default::default();
        let mut heap: [Vec<SlotChoice>; MAX_ACTIVE_PER_SIDE] = Default::default();
        for slot in 0..n {
            if self.slot_options_bound(s, slot) <= SLOT_OPTIONS_INLINE {
                let o = &mut inline[slot];
                self.slot_options_with::<false>(s, slot, &mut |c| o.push(c));
            } else {
                let o = &mut heap[slot];
                self.slot_options_with::<false>(s, slot, &mut |c| o.push(c));
            }
        }
        let options = |slot: usize| -> &[SlotChoice] {
            if heap[slot].is_empty() {
                &inline[slot]
            } else {
                &heap[slot]
            }
        };
        let bench = (n..self.sides[s].mons.len())
            .filter(|&i| !self.sides[s].mons[i].fainted)
            .count();
        let forced = (0..n)
            .filter(|&slot| self.force_switch_flag(s, slot))
            .count();
        let want_switches = if self.request == RequestState::Switch {
            forced.min(bench)
        } else {
            0
        };
        loop {
            let pick: ArrayVec<SlotChoice, MAX_ACTIVE_PER_SIDE> = (0..n)
                .map(|slot| {
                    let o = options(slot);
                    o[rng.random(o.len() as u32) as usize]
                })
                .collect();
            let mut switches: ArrayVec<usize, MAX_ACTIVE_PER_SIDE> = ArrayVec::new();
            let mut megas = 0;
            for c in &pick {
                match *c {
                    SlotChoice::Switch { index } => switches.push(index),
                    SlotChoice::Move { mega: true, .. } => megas += 1,
                    _ => {}
                }
            }
            let distinct = switches
                .iter()
                .enumerate()
                .all(|(i, x)| !switches[..i].contains(x));
            if distinct && megas <= 1 && switches.len() >= want_switches {
                return pick.to_vec();
            }
        }
    }

    /// Showdown choice string for a structured joint choice.
    pub fn choice_string(&self, choice: &[SlotChoice]) -> String {
        choice
            .iter()
            .map(|c| match *c {
                SlotChoice::Pass => "pass".to_string(),
                SlotChoice::Switch { index } => format!("switch {}", index + 1),
                SlotChoice::Move { slot, target, mega } => {
                    let mut s = format!("move {}", if slot >= RECHARGE { 1 } else { slot + 1 });
                    if target != 0 {
                        s.push_str(&format!(" {target}"));
                    }
                    if mega {
                        s.push_str(" mega");
                    }
                    s
                }
            })
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Choose a random legal option (team preview: a random order).
    pub fn random_choice(&mut self, s: usize, rng: &mut crate::rng::Rng) -> String {
        if self.request == RequestState::TeamPreview {
            let n = self.sides[s].mons.len();
            let mut order: Vec<usize> = (1..=n).collect();
            rng.shuffle(&mut order, 0, n);
            return format!(
                "team {}",
                order.iter().map(|x| x.to_string()).collect::<String>()
            );
        }
        let choices = self.joint_choices(s);
        let c = &choices[rng.random(choices.len() as u32) as usize];
        self.choice_string(c)
    }
}

/// Most Pokémon on a team.
const TEAM_MAX: usize = 6;

/// Every increasing `k`-subset of `0..n`, in lexicographic order, written
/// into `buf[..k]` and handed to `f` (no allocation).
fn for_each_subset(
    n: usize,
    k: usize,
    buf: &mut [usize],
    depth: usize,
    start: usize,
    f: &mut dyn FnMut(&[usize]),
) {
    if depth == k {
        return f(&buf[..k]);
    }
    for i in start..n {
        buf[depth] = i;
        for_each_subset(n, k, buf, depth + 1, i + 1, f);
    }
}

/// How many options of one slot `sample_joint_choice` keeps on the stack:
/// four moves at four targets with and without Mega Evolution, then six
/// Pokémon and Struggle, fit.
const SLOT_OPTIONS_INLINE: usize = 48;
