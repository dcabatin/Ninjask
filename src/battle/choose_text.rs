//! Showdown's `Side.choose` (sim/side.ts) and `Battle.choose`, ported: the
//! text of a choice is read the way Showdown reads it, so the same strings are
//! accepted and refused, with the same messages and the same side effects.
//!
//! The parts, in Showdown's order: the choice is split at commas (a `team `
//! choice is one part); each part's first word names the kind (`move`,
//! `switch`, `shift`, `team`, `pass`/`skip`, `auto`/`default`, `testfight`);
//! a part's slot is the next one that has to choose, fainted Pokémon (or in
//! a switch request those that need no switch) being passed on the way
//! (`getChoiceIndex`). A choice is done when every slot has an action
//! (`isChoiceDone`), and one that isn't is refused ("Incomplete choice").
//!
//! The first refusal stops the parts, except an unrecognised kind, which is
//! reported and read past, as in Showdown. What a refusal leaves behind is
//! kept for the caller: `cant_undo` (Showdown's `choice.cantUndo`, set by a
//! switch of a Pokémon that may be trapped, or a Struggle chosen while
//! Imprison may be disabling moves, even in a choice refused afterwards)
//! and the `reveal` an "[Unavailable choice]" gave away.

use super::{Battle, MonRef, UNAVAILABLE};
use crate::dex::types::{to_id, Category, Target};
use crate::state::*;

/// What a part's trailing word asks for (`event` in `Side.choose`).
#[derive(Clone, Copy, PartialEq, Eq)]
enum Event {
    None,
    Mega,
    MegaX,
    MegaY,
    ZMove,
    Ultra,
    Dynamax,
    Terastallize,
}

/// A parsed choice, not yet applied.
pub(super) enum Chosen {
    Team(Vec<usize>),
    Slots(Vec<SlotChoice>),
}

/// The result of reading a choice, with what it leaves behind either way.
pub(super) struct Reading {
    pub result: Result<Chosen, String>,
    pub cant_undo: bool,
    pub reveals: Vec<(usize, Reveal)>,
}

/// JavaScript's `parseInt(text)` (radix 10, or 16 after `0x`): the leading
/// whitespace skipped, an optional sign, the digits at the start.
fn js_parse_int(text: &str) -> Option<i64> {
    let t = text.trim_start();
    let (neg, t) = match t.strip_prefix('-') {
        Some(r) => (true, r),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    let (radix, t) = match t.strip_prefix("0x").or_else(|| t.strip_prefix("0X")) {
        Some(r) => (16, r),
        None => (10, t),
    };
    let digits: String = t.chars().take_while(|c| c.is_digit(radix)).collect();
    if digits.is_empty() {
        return None;
    }
    let v = i64::from_str_radix(&digits, radix).unwrap_or(i64::MAX);
    Some(if neg { -v } else { v })
}

/// `Utils.splitFirst(text, " ")`: the text before the first space and after it.
fn split_first(text: &str) -> (&str, &str) {
    match text.find(' ') {
        Some(i) => (&text[..i], &text[i + 1..]),
        None => (text, ""),
    }
}

struct Reader<'a> {
    b: &'a Battle,
    s: usize,
    state: RequestState,
    /// Active slots.
    n: usize,
    actions: Vec<SlotChoice>,
    team: Vec<usize>,
    error: Option<String>,
    /// The last error Showdown would have sent: all but `testfight`'s, which
    /// is refused without a message.
    sent: Option<String>,
    cant_undo: bool,
    mega: bool,
    switch_ins: Vec<usize>,
    forced_switches_left: usize,
    forced_passes_left: usize,
    reveals: Vec<(usize, Reveal)>,
}

impl Battle {
    /// Read `input` as side `s`'s choice (`Side.choose`, then the completeness
    /// check of `Battle.choose`).
    pub(super) fn read_choice(&self, s: usize, input: &str) -> Reading {
        let side = &self.sides[s];
        let (mut forced_switches, mut forced_passes) = (0, 0);
        if self.request == RequestState::Switch {
            // clearChoice: how many of the Pokémon that must switch can, and
            // how many have no one to switch in for them.
            let can_out = (0..side.n_active)
                .filter(|&i| self.force_switch[s][i])
                .count();
            let can_in = (side.n_active..side.mons.len())
                .filter(|&i| !side.mons[i].fainted)
                .count();
            forced_switches = can_out.min(can_in);
            forced_passes = can_out - forced_switches;
        }
        let mut r = Reader {
            b: self,
            s,
            state: self.request,
            n: side.n_active,
            actions: Vec::new(),
            team: Vec::new(),
            error: None,
            sent: None,
            cant_undo: false,
            mega: false,
            switch_ins: Vec::new(),
            forced_switches_left: forced_switches,
            forced_passes_left: forced_passes,
            reveals: Vec::new(),
        };
        let result = r.run(input);
        Reading {
            result,
            cant_undo: r.cant_undo,
            reveals: r.reveals,
        }
    }
}

impl Reader<'_> {
    fn err(&mut self, msg: impl Into<String>) -> bool {
        let msg = msg.into();
        if !msg.starts_with("Hack to avoid") {
            self.sent = Some(msg.clone());
        }
        self.error = Some(msg);
        false
    }

    fn state_word(&self) -> &'static str {
        match self.state {
            RequestState::TeamPreview => "teampreview",
            RequestState::Move => "move",
            RequestState::Switch => "switch",
            RequestState::Ended => "",
        }
    }

    fn mon_ref(&self, slot: usize) -> Option<MonRef> {
        self.b.active(self.s, slot)
    }

    fn fainted(&self, slot: usize) -> bool {
        self.mon_ref(slot)
            .map(|r| self.b.mon(r).fainted)
            .unwrap_or(true)
    }

    fn name(&self, slot: usize) -> String {
        self.mon_ref(slot)
            .map(|r| self.b.mon(r).name.to_string())
            .unwrap_or_default()
    }

    fn game_type(&self) -> &'static str {
        if self.b.dex.format.doubles {
            "doubles"
        } else {
            "singles"
        }
    }

    fn picked_team_size(&self) -> usize {
        let n = self.b.sides[self.s].mons.len();
        self.b.dex.format.picked_team_size.unwrap_or(n).min(n)
    }

    /// `Side.choose` and the check after it in `Battle.choose`.
    fn run(&mut self, input: &str) -> Result<Chosen, String> {
        let parts: Vec<&str> = if input.starts_with("team ") {
            vec![input]
        } else {
            input.split(',').collect()
        };
        if parts.len() > self.n {
            return Err(format!(
                "Can't make choices: You sent choices for {} Pokémon, but this is a {} game!",
                parts.len(),
                self.game_type()
            ));
        }
        for part in parts {
            let (kind, data) = split_first(part.trim());
            let data = data.trim();
            let (kind, data) = if kind == "testfight" {
                ("move", "testfight")
            } else {
                (kind, data)
            };
            let ok = match kind {
                "move" => {
                    let original = data;
                    match parse_move_data(data) {
                        Ok((text, target, event)) => self.choose_move(&text, target, event),
                        Err(()) => {
                            self.err(format!("Conflicting arguments for \"move\": {original}"))
                        }
                    }
                }
                "switch" => self.choose_switch(data),
                "shift" => {
                    if !data.is_empty() {
                        self.err(format!("Unrecognized data after \"shift\": {data}"))
                    } else {
                        self.choose_shift()
                    }
                }
                "team" => self.choose_team(Some(data)),
                "pass" | "skip" => {
                    if !data.is_empty() {
                        self.err(format!("Unrecognized data after \"pass\": {data}"))
                    } else {
                        self.choose_pass()
                    }
                }
                "auto" | "default" => self.auto_choose(),
                _ => {
                    // Reported, and the parts after it still read.
                    self.err(format!("Unrecognized choice: {part}"));
                    true
                }
            };
            if !ok {
                return Err(self.take_error(input));
            }
        }
        if let Some(e) = self.error.take() {
            return Err(self.sent.take().unwrap_or(e));
        }
        if !self.is_choice_done() {
            return Err(format!(
                "Incomplete choice: {input} - missing other pokemon"
            ));
        }
        Ok(if self.state == RequestState::TeamPreview {
            Chosen::Team(std::mem::take(&mut self.team))
        } else {
            Chosen::Slots(std::mem::take(&mut self.actions))
        })
    }

    fn take_error(&mut self, input: &str) -> String {
        let sent = self.sent.take();
        self.error.take().map(|e| sent.unwrap_or(e)).unwrap_or_else(|| {
            format!("Unknown error for choice: {input}. If you're not using a custom client, please report this as a bug.")
        })
    }

    /// How many slots have an action (the next slot to choose for).
    fn done(&self) -> usize {
        if self.state == RequestState::TeamPreview {
            self.team.len()
        } else {
            self.actions.len()
        }
    }

    /// `getChoiceIndex`: the slot the next part is for, passing on the way
    /// the Pokémon that need no choice.
    fn choice_index(&mut self, is_pass: bool) -> usize {
        let mut index = self.done();
        if !is_pass {
            match self.state {
                RequestState::Move => {
                    while index < self.n && self.fainted(index) {
                        self.actions.push(SlotChoice::Pass);
                        index += 1;
                    }
                }
                RequestState::Switch => {
                    while index < self.n && !self.b.force_switch[self.s][index] {
                        self.actions.push(SlotChoice::Pass);
                        index += 1;
                    }
                }
                _ => {}
            }
        }
        index
    }

    fn is_choice_done(&mut self) -> bool {
        if self.forced_switches_left > 0 {
            return false;
        }
        if self.state == RequestState::TeamPreview {
            return self.team.len() >= self.picked_team_size();
        }
        self.choice_index(false);
        self.actions.len() >= self.n
    }

    fn choose_pass(&mut self) -> bool {
        let index = self.choice_index(true);
        if index >= self.n {
            return false;
        }
        match self.state {
            RequestState::Switch => {
                if self.b.force_switch[self.s][index] {
                    if self.forced_passes_left == 0 {
                        let name = self.name(index);
                        return self.err(format!(
                            "Can't pass: You need to switch in a Pokémon to replace {name}"
                        ));
                    }
                    self.forced_passes_left -= 1;
                }
            }
            RequestState::Move => {
                if !self.fainted(index) {
                    let name = self.name(index);
                    return self.err(format!(
                        "Can't pass: Your {name} must make a move (or switch)"
                    ));
                }
            }
            _ => return self.err("Can't pass: Not a move or switch request"),
        }
        self.actions.push(SlotChoice::Pass);
        true
    }

    fn choose_shift(&mut self) -> bool {
        let index = self.choice_index(false);
        if index >= self.n {
            self.err(format!(
                "Can't shift: You do not have a Pokémon in slot {}",
                index + 1
            ))
        } else if self.state != RequestState::Move {
            self.err("Can't shift: You can only shift during a move phase")
        } else {
            // No game type here has triples.
            self.err("Can't shift: You can only shift to the center in triples")
        }
    }

    fn choose_team(&mut self, data: Option<&str>) -> bool {
        if self.state != RequestState::TeamPreview {
            return self.err("Can't choose for Team Preview: You're not in a Team Preview phase");
        }
        let n = self.b.sides[self.s].mons.len();
        let data = data.filter(|d| !d.is_empty());
        let mut bracketed = false;
        let mut team_data = data.unwrap_or("");
        if let Some(d) = data {
            if d.starts_with('[') && d.ends_with(']') {
                bracketed = true;
                team_data = if d.len() >= 2 {
                    d[1..d.len() - 1].trim()
                } else {
                    ""
                };
            }
        }
        let picked = self.picked_team_size();
        // JavaScript numbers: NaN is None.
        let mut positions: Vec<Option<i64>> = if !team_data.is_empty() {
            let pieces: Vec<&str> = if bracketed || team_data.contains(',') || n >= 10 {
                team_data.split(',').collect()
            } else {
                // split(''): one piece per UTF-16 unit.
                team_data
                    .char_indices()
                    .map(|(i, c)| &team_data[i..i + c.len_utf8()])
                    .collect()
            };
            pieces
                .into_iter()
                .map(|p| js_parse_int(p).map(|v| v - 1))
                .collect()
        } else {
            (0..n as i64).map(Some).collect()
        };
        if !bracketed {
            positions.truncate(picked);
        }
        if positions.len() < picked {
            for i in 0..picked as i64 {
                if !positions.contains(&Some(i)) {
                    positions.push(Some(i));
                }
                if positions.len() >= picked {
                    break;
                }
            }
        }
        if positions.len() != picked {
            return self.err(format!(
                "Can't choose for Team Preview: You must choose exactly {picked} Pokémon"
            ));
        }
        for (i, pos) in positions.iter().enumerate() {
            let shown = |p: &Option<i64>| p.map_or("NaN".to_string(), |v| (v + 1).to_string());
            match pos {
                Some(p) if *p >= 0 && (*p as usize) < n => {}
                _ => {
                    return self.err(format!(
                        "Can't choose for Team Preview: You do not have a Pokémon in slot {}",
                        shown(pos)
                    ))
                }
            }
            if positions.iter().position(|q| q == pos) != Some(i) {
                return self.err(format!(
                    "Can't choose for Team Preview: The Pokémon in slot {} can only switch in once",
                    shown(pos)
                ));
            }
        }
        self.team = positions.into_iter().map(|p| p.unwrap() as usize).collect();
        true
    }

    fn choose_switch(&mut self, slot_text: &str) -> bool {
        let state = self.state;
        if state != RequestState::Move && state != RequestState::Switch {
            return self.err(format!(
                "Can't switch: You need a {} response",
                self.state_word()
            ));
        }
        let index = self.choice_index(false);
        if index >= self.n {
            return self.err(if state == RequestState::Switch {
                "Can't switch: You sent more switches than Pokémon that need to switch"
            } else {
                "Can't switch: You sent more choices than unfainted Pokémon"
            });
        }
        let side = &self.b.sides[self.s];
        let revival = side.revival.get(index).copied().unwrap_or(false);
        let team_len = side.mons.len();
        let mut slot: i64;
        if slot_text.is_empty() {
            if state != RequestState::Switch {
                return self.err("Can't switch: You need to select a Pokémon to switch in");
            }
            if revival {
                match side.mons.iter().position(|m| m.fainted) {
                    Some(i) => slot = i as i64,
                    None => return self.err("Can't switch: You have to pass to a fainted Pokémon"),
                }
            } else {
                if self.forced_switches_left == 0 {
                    return self.choose_pass();
                }
                slot = self.n as i64;
                while (slot as usize) < team_len
                    && (self.switch_ins.contains(&(slot as usize))
                        || side.mons[slot as usize].fainted)
                {
                    slot += 1;
                }
            }
        } else {
            slot = js_parse_int(slot_text).map_or(-1, |v| v - 1);
        }
        if slot < 0 {
            // Maybe it's a name or a species.
            let lower = slot_text.to_lowercase();
            let id = to_id(slot_text);
            let found = side.mons.iter().position(|m| {
                lower == m.name.to_lowercase() || id == self.b.dex.species(m.species).id
            });
            match found {
                Some(i) => slot = i as i64,
                None => {
                    return self.err(format!(
                        "Can't switch: You do not have a Pokémon named \"{slot_text}\" to switch to"
                    ))
                }
            }
        }
        if slot as usize >= team_len {
            return self.err(format!(
                "Can't switch: You do not have a Pokémon in slot {} to switch to",
                slot + 1
            ));
        }
        let slot = slot as usize;
        if slot < self.n && !revival {
            return self.err("Can't switch: You can't switch to an active Pokémon");
        }
        if self.switch_ins.contains(&slot) {
            return self.err(format!(
                "Can't switch: The Pokémon in slot {} can only switch in once",
                slot + 1
            ));
        }
        let target_fainted = side.mons[slot].fainted;
        if revival {
            if !target_fainted {
                return self.err("Can't switch: You have to pass to a fainted Pokémon");
            }
            self.forced_switches_left = self.forced_switches_left.saturating_sub(1);
            self.actions.push(SlotChoice::Switch { index: slot });
            return true;
        }
        if target_fainted {
            return self.err("Can't switch: You can't switch to a fainted Pokémon");
        }
        if state == RequestState::Move {
            let r = self.mon_ref(index).expect("a Pokémon that has to choose");
            let m = self.b.mon(r);
            // A Pokémon locked into a move is trapped, and the request says so
            // (getMoveRequestData).
            if self.b.locked_move(r).is_some() {
                return self.err("Can't switch: The active Pokémon is trapped");
            }
            if m.trapped != Trapped::No {
                // "[Unavailable choice]" when the request hid the trap.
                let shown = m.trapped == Trapped::Yes
                    || !self.b.is_last_active(r)
                    || self.b.revealed(self.s, index, Reveal::Trapped);
                if !shown {
                    self.reveals.push((index, Reveal::Trapped));
                }
                let prefix = if shown { "" } else { UNAVAILABLE };
                return self.err(format!(
                    "{prefix}Can't switch: The active Pokémon is trapped"
                ));
            } else if m.maybe_trapped && self.b.is_last_active(r) && self.b.locked_move(r).is_none()
            {
                self.cant_undo = true;
            }
        } else {
            if self.forced_switches_left == 0 {
                return self.err("Can't switch: Player somehow switched too many Pokémon");
            }
            self.forced_switches_left -= 1;
        }
        self.switch_ins.push(slot);
        self.actions.push(SlotChoice::Switch { index: slot });
        true
    }

    /// A Pokémon Imprison may be hiding disabled moves from (`maybeLocked`).
    fn maybe_locked(&self, slot: usize) -> bool {
        let Some(r) = self.mon_ref(slot) else {
            return false;
        };
        self.b.is_last_active(r)
            && !self.b.revealed(self.s, slot, Reveal::Disabled)
            && self.b.locked_move(r).is_none()
            && self
                .b
                .foes_of(r)
                .into_iter()
                .any(|f| self.b.mon(f).vol.imprison)
    }

    fn choose_move(&mut self, move_text: &str, target_loc: Option<i32>, event: Event) -> bool {
        if self.state != RequestState::Move {
            return self.err(format!(
                "Can't move: You need a {} response",
                self.state_word()
            ));
        }
        let index = self.choice_index(false);
        if index >= self.n {
            return self.err("Can't move: You sent more choices than unfainted Pokémon.");
        }
        let b = self.b;
        let r = self.mon_ref(index).expect("a Pokémon that has to choose");
        let m = b.mon(r);
        let name = m.name.to_string();
        let auto = move_text.is_empty();

        // The request's moves (getMoveRequestData): the locked move alone,
        // Struggle alone when none is shown usable, or every move.
        // `None` is the id of a move that is not one of the Pokémon's own.
        let locked = b.locked_move(r);
        let all_shown_disabled = (0..m.moves.len()).all(|i| b.move_shown_disabled(r, i));
        let req: Vec<(String, Option<usize>)> = if let Some(lock) = locked {
            vec![(
                match lock {
                    crate::moves::LockedMove::Recharge => "recharge".to_string(),
                    crate::moves::LockedMove::Move(mv) => b.dex.mv(mv).id.clone(),
                },
                None,
            )]
        } else if all_shown_disabled {
            vec![("struggle".to_string(), None)]
        } else {
            (0..m.moves.len())
                .map(|i| (b.dex.mv(m.moves[i].id).id.clone(), Some(i)))
                .collect()
        };
        let req_disabled = |k: usize| req[k].1.is_some_and(|i| b.move_shown_disabled(r, i));
        let text = if auto { "1" } else { move_text };

        let mut chosen: Option<usize> = None; // index into `req`
        let mut moveid: String;
        if text.bytes().all(|c| c.is_ascii_digit()) {
            let idx: i128 = text.parse::<i128>().unwrap_or(i128::MAX) - 1;
            if idx < 0 || idx >= req.len() as i128 {
                let shown = idx.saturating_add(1);
                return self.err(format!(
                    "Can't move: Your {name} doesn't have a move {shown}"
                ));
            }
            chosen = Some(idx as usize);
            moveid = req[idx as usize].0.clone();
        } else {
            moveid = to_id(text);
            if moveid.starts_with("hiddenpower") {
                moveid = "hiddenpower".to_string();
            }
            if let Some(k) = req.iter().position(|(id, _)| *id == moveid) {
                chosen = Some(k);
            } else if moveid != "testfight" {
                return self.err(format!(
                    "Can't move: Your {name} doesn't have a move matching {moveid}"
                ));
            }
        }

        // The Pokémon's own moves that it can use.
        let usable: Vec<usize> = (0..m.moves.len())
            .filter(|&i| b.move_usable(r, i))
            .collect();
        if auto {
            for (k, (_, mi)) in req.iter().enumerate() {
                if req_disabled(k) {
                    continue;
                }
                if mi.is_some_and(|i| !b.move_usable(r, i)) {
                    continue;
                }
                chosen = Some(k);
                moveid = req[k].0.clone();
                break;
            }
        }
        let move_name: String = match chosen.and_then(|k| req[k].1) {
            Some(i) => b.dex.mv(m.moves[i].id).name.clone(),
            None => match moveid.as_str() {
                "struggle" => "Struggle".into(),
                "recharge" => "recharge".into(),
                "testfight" => "testfight".into(),
                _ => chosen
                    .map(|k| req[k].0.clone())
                    .and_then(|id| {
                        locked.and_then(|l| match l {
                            crate::moves::LockedMove::Move(mv) if b.dex.mv(mv).id == id => {
                                Some(b.dex.mv(mv).name.clone())
                            }
                            _ => None,
                        })
                    })
                    .unwrap_or_else(|| moveid.clone()),
            },
        };

        if event == Event::ZMove {
            return self.err(format!(
                "Can't move: {name} can't use {move_name} as a Z-move"
            ));
        }

        // Targeting, by the request move's target type. A move of the
        // request found by name rather than number defaults to `normal`
        // when the request gives it none (a locked move's), as in Showdown.
        let by_number = text.bytes().all(|c| c.is_ascii_digit());
        let mut target_type = match chosen.and_then(|k| req[k].1) {
            Some(i) => Some(b.move_target_at_choice(r, b.dex.mv(m.moves[i].id))),
            None if moveid == "struggle" => Some(Target::RandomNormal),
            None if chosen.is_some() && !by_number => Some(Target::Normal),
            None => None,
        };
        if event == Event::Dynamax {
            // The Max Move's target (`getMaxMove`): Max Guard's for a status
            // move, a foe's for an attack; Struggle stays itself; there is
            // none for a move the dex doesn't have (Recharge).
            let base = match chosen.and_then(|k| req[k].1) {
                Some(i) => Some(m.moves[i].id),
                None => b.dex.move_id(&moveid),
            };
            match base {
                Some(mv) if moveid == "struggle" => {
                    let _ = mv;
                }
                Some(mv) => {
                    target_type = Some(if b.dex.mv(mv).category == Category::Status {
                        Target::SelfTarget
                    } else {
                        Target::AdjacentFoe
                    });
                }
                None => {
                    return self.err(format!(
                        "Can't move: {name} can't use {move_name} as a Max Move"
                    ));
                }
            }
        }
        let mut target = target_loc.unwrap_or(0);
        if auto || moveid == "testfight" {
            target = 0;
        } else if target_type.is_some_and(|t| t.needs_choice()) {
            if target == 0 && self.n >= 2 {
                return self.err(format!("Can't move: {move_name} needs a target"));
            }
            // validTargetLoc accepts no location at all (a singles game).
            if target != 0 && !b.valid_target_loc(self.s, index, target as i8, target_type.unwrap())
            {
                return self.err(format!("Can't move: Invalid target for {move_name}"));
            }
        } else if target != 0 {
            return self.err(format!(
                "Can't move: You can't choose a target for {move_name}"
            ));
        }

        let maybe_locked = self.maybe_locked(index);
        if locked.is_some() {
            // The request offers only the locked move, without a target.
            self.actions.push(SlotChoice::Move {
                slot: 0,
                target: 0,
                mega: false,
            });
            return true;
        } else if usable.is_empty() {
            // Override: Struggle, when no move can be used.
            if maybe_locked {
                self.cant_undo = true;
            }
            self.actions.push(SlotChoice::Move {
                slot: STRUGGLE,
                target: 0,
                mega: false,
            });
            return true;
        } else if moveid == "testfight" {
            if !maybe_locked {
                return self.err(format!(
                    "Can't move: {name}'s Fight button is known to be safe"
                ));
            }
            self.reveals.push((index, Reveal::Disabled));
            return self.err("Hack to avoid sending error messages to the client :D");
        }
        let idx = chosen.and_then(|k| req[k].1).expect("a move of its own");
        if event != Event::Dynamax && !b.move_usable(r, idx) {
            if auto {
                return self.err("autoChoose chose a disabled move");
            }
            // The first refusal of a move changes its entry in the request
            // (`disabled`, and `disabledSource`, which it lacked), and a Pokémon
            // Imprison may be hiding moves from has its flags cleared: both
            // make the refusal "[Unavailable choice]". A repeat changes
            // nothing and is "[Invalid choice]".
            let first = !b.revealed(self.s, index, Reveal::Move(idx as u8));
            let hidden = first || maybe_locked;
            if maybe_locked {
                self.reveals.push((index, Reveal::Disabled));
            }
            if first {
                self.reveals.push((index, Reveal::Move(idx as u8)));
            }
            let prefix = if hidden { UNAVAILABLE } else { "" };
            return self.err(format!(
                "{prefix}Can't move: {name}'s {move_name} is disabled"
            ));
        }

        // Mega Evolution and the other events.
        let can_mega = b.can_mega_evo(r).is_some() && !b.sides[self.s].mega_used;
        match event {
            Event::Mega if !can_mega => {
                return self.err(format!("Can't move: {name} can't mega evolve"))
            }
            Event::MegaX => return self.err(format!("Can't move: {name} can't mega evolve X")),
            Event::MegaY => return self.err(format!("Can't move: {name} can't mega evolve Y")),
            _ => {}
        }
        if event == Event::Mega && self.mega {
            return self.err("Can't move: You can only mega-evolve once per battle");
        }
        match event {
            Event::Ultra => return self.err(format!("Can't move: {name} can't ultra burst")),
            Event::Dynamax => return self.err("Can't move: Dynamaxing doesn't outside of Gen 8."),
            Event::Terastallize => {
                return self.err(format!("Can't move: {name} can't Terastallize."))
            }
            _ => {}
        }
        if event == Event::Mega {
            self.mega = true;
        }
        self.actions.push(SlotChoice::Move {
            slot: idx,
            target: target as i8,
            mega: event == Event::Mega,
        });
        true
    }

    /// `autoChoose`: finish the choice with default picks.
    fn auto_choose(&mut self) -> bool {
        match self.state {
            RequestState::TeamPreview => {
                if !self.is_choice_done() {
                    return self.choose_team(None);
                }
            }
            RequestState::Switch => {
                for _ in 0..=10 {
                    if self.is_choice_done() {
                        break;
                    }
                    if !self.choose_switch("") {
                        return false;
                    }
                }
            }
            RequestState::Move => {
                for _ in 0..=10 {
                    if self.is_choice_done() {
                        break;
                    }
                    if !self.choose_move("", None, Event::None) {
                        return false;
                    }
                }
            }
            RequestState::Ended => {}
        }
        true
    }
}

/// The part of `Side.choose` that reads a move part's trailing words: a target
/// location (`1` to `3`, signed) and one modifier, in either order, each at
/// most once. Returns the move text left, the target and the modifier, or
/// `Err` for "Conflicting arguments".
fn parse_move_data(data: &str) -> Result<(String, Option<i32>, Event), ()> {
    const SUFFIXES: [(&str, Event); 10] = [
        (" mega", Event::Mega),
        (" megax", Event::MegaX),
        (" megay", Event::MegaY),
        (" zmove", Event::ZMove),
        (" ultra", Event::Ultra),
        (" dynamax", Event::Dynamax),
        (" gigantamax", Event::Dynamax),
        (" max", Event::Dynamax),
        (" terastal", Event::Terastallize),
        (" terastallize", Event::Terastallize),
    ];
    let mut data = data.to_string();
    let mut target: Option<i32> = None;
    let mut event = Event::None;
    loop {
        let chars: Vec<char> = data.chars().collect();
        let len = chars.len();
        // /\s(?:-|\+)?[1-3]$/
        let target_at_end = {
            let digit = len >= 2 && ('1'..='3').contains(&chars[len - 1]);
            if digit && chars[len - 2].is_whitespace() {
                Some((chars[len - 1].to_digit(10).unwrap() as i32, 2))
            } else if digit
                && len >= 3
                && (chars[len - 2] == '-' || chars[len - 2] == '+')
                && chars[len - 3].is_whitespace()
            {
                let v = chars[len - 1].to_digit(10).unwrap() as i32;
                Some((if chars[len - 2] == '-' { -v } else { v }, 2))
            } else {
                None
            }
        };
        if let (Some((value, cut)), true) = (target_at_end, to_id(&data) != "conversion2") {
            if target.is_some() {
                return Err(());
            }
            target = Some(value);
            data = chars[..len - cut]
                .iter()
                .collect::<String>()
                .trim()
                .to_string();
            continue;
        }
        let mut matched = false;
        for (suffix, ev) in SUFFIXES.iter() {
            if data.ends_with(suffix) {
                if event != Event::None {
                    return Err(());
                }
                event = *ev;
                data.truncate(data.len() - suffix.len());
                matched = true;
                break;
            }
        }
        if !matched {
            break;
        }
    }
    Ok((data, target, event))
}
