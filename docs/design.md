# Ninjask design

The engine is a Rust port of Pokémon Showdown's battle mechanics for the
Champions VGC doubles format (Reg M-C). It is built to run self-play
training, search and team evaluation. Showdown at a pinned commit is the
reference: the engine reproduces its outcomes, and in reference mode its
exact logs and random draws, while running far faster.

## Requirements

- **Format.** Champions VGC doubles: Reg M-C now. Reg M-A and M-B, and future regulations, are data changes.
  - The format comes from `refsim/export_format.js` (species, moves, items, abilities, learnsets, natures, type chart, rules).
  - Only behaviour that data can't express is code.
- **Correctness.** For any state and joint action, the distribution of next states matches Showdown's `branch` oracle within statistical tolerance. Damage values must match Showdown exactly, including its 4096-based fixed-point rounding.
- **Honesty about coverage.** The engine never silently simulates an effect it doesn't implement.
  - Every move, item and ability is either *generic*, fully described by its data fields, or *implemented*, with code ported from Showdown.
  - Anything else is *unsupported*. A team containing one is reported unsupported (`Dex.team_problems`), so a caller can leave it out until it's implemented.
- **Speed.** Fast enough for search and self-play: thousands of random-policy battles per second per CPU core, against Showdown's roughly 30 (see Performance).
- **Interfaces.**
  - Create a battle from two packed teams and a seed.
  - Get each side's request: team preview, moves, a forced switch, or wait.
  - Get each side's observation: only what that player has seen.
  - Submit choices in Showdown's choice syntax; clone; import a state from the reference simulator.
  - Python bindings, and a Rust API for code that steps many battles at once.


**Champions differs from standard Gen 9** (`data/mods/champions`):
- Full paralysis 1/8, sleep 2–3 turns, freeze thaws within 3 turns.
- Stat formula with stat points; PP capped at 20.
- Megas don't revert on fainting; no Terastallization.
- Its own `modifyDamage`, `spreadMoveHit`, `hitStepMoveHitLoop`, `getActionSpeed` and `canMegaEvo`.
- New Mega abilities.

The engine ports from the Champions versions.

## Architecture

- **Data** is loaded from the export at start-up and interned to small integer ids.
  - Each move, item and ability with special behaviour maps to a handler variant through a registry keyed by its Showdown id.
  - Handlers are dispatched with `match` at fixed hook points: base power, attack and defence modifiers, damage modifiers, on-hit, after-move, switch-in, residual, and so on. This replaces Showdown's dynamic event system, which is where Showdown's own Rust port loses most of its time.
- **State** is plain fixed-size data, so cloning for search is a copy.
  - A battle holds the field, two sides and a request state.
  - A side holds up to six Pokémon, two active slots, side conditions and a Mega-used flag.
  - A Pokémon holds stats, HP, status and its counters, boosts, item, ability, four move slots with PP, compact volatile effects with their counters, last move, and Mega and fainted flags.
- **Turn resolution follows Showdown's action queue.**
  - Switches resolve before moves, and Mega Evolution happens at the start of the turn.
  - Moves sort by priority, then speed, with Trick Room inverting speed and ties broken at random. Speed is recalculated after every action (Gen 8+ behaviour).
  - Mid-turn decisions exist: U-turn, Parting Shot, Eject Button and similar give a side a switch request inside the turn.
  - End of turn follows Showdown's residual order.
  - Each piece cites the Showdown function it ports.
- **Events.** The engine emits a structured event stream that mirrors Showdown's protocol (switch, move, damage, heal, status, boost, weather, field, side, volatile start/end, item, ability, Mega, faint, turn).
  - It can render that stream as Showdown protocol text for debugging and for diffs against Showdown's logs.
  - Each side's observation is built by a tracker that consumes these events. The same tracker consumes parsed Showdown protocol, so a program sees the same observations in simulation and in live play.
- **RNG.** A fast generator (xoshiro256++), deterministic per seed. It doesn't need to match Showdown's sodium generator because correctness is checked on distributions, not exact rolls.
- **Python.** PyO3 bindings built with maturin: `Dex`, `Battle` and `Tracker`, re-exported by the `ninjask` package. A project can compile them into its own extension module next to classes that take them, such as an observation encoder or a batched environment stepping many battles in parallel (README, "Embedding in another extension").


## Verification

1. Play reference-simulator games with baseline agents on teams the engine supports, and sample decision points.
2. At each point, export the state (`state` projection), import it into the engine, and pick a legal joint action.
3. Draw N outcomes from Showdown (`branch`) and N from the engine, and compare their distributions. Outcomes are HP, status, boosts, fainting, field and volatile effects, and the next request. Use an exact-support check plus a χ² test, with false discovery rate controlled across all comparisons.
4. Rank mismatches by the moves, items and abilities involved, so the most common errors get fixed first.

Damage is also tested exactly. For a given attacker, defender, move and field, the set of possible damage values must equal Showdown's set.

Uniformly random test points rarely trigger one-shot effects (a Sitrus Berry needs its holder near half HP; a Focus Sash needs a full-HP holder taking a KO hit). The verifier therefore has a focus mode (`scripts/verify_engine.py --focus ids`). Test teams then carry the named moves, abilities and items whenever a Pokémon can have them. The test point is delayed, up to 12 decisions, until one of them could act: an HP berry between 50% and 85%, a sash at full HP, a focus ability or move on the field. Test points at team preview check lead switch-ins.

States in the middle of a turn with actions still queued (the switch request after U-turn) are not imported, because the projection doesn't carry Showdown's queue. Such switches are still compared when they happen inside a tested turn. Team preview is the one exception: Showdown's queue then holds only its battle-start action, which the engine queues itself.

Besides HP, status and the rest, each Pokémon's last move and its move result (Showdown's `moveThisTurnResult` and `moveLastTurnResult`: did something, failed, or neither) are compared, because Stomping Tantrum depends on the result. The report also counts how many test points had each volatile on the field (a Pokémon recharging, charging, Encored...), to show that those states were tested.


## Reference mode: exact agreement with Showdown

Besides matching Showdown's outcome distributions, the engine has a reference
mode that matches Showdown exactly: equal log lines and state projections, RNG
state included, after every input, over thousands of seeded games. Everything
below was found by that comparison, and each item is a behaviour of Showdown
that a port has to reproduce.

- **Generator.** `Battle.reference(dex, team1, team2, seed)` runs Showdown's
  own generator (`SodiumRNG`: ChaCha20 keyed by the seed, nonce
  "LibsodiumDRG"), seeded with Showdown's seed string. Its draws equal
  Showdown's; a unit test pins them to Node's output.
- **Call order.** Showdown draws not only for damage, accuracy and critical
  hits but for every speed tie it sorts: the action queue, each
  `eachEvent` (`Update` after every action and after every hit), handler
  lists such as the residual phase, and random targets for spread moves. The
  engine has to make the same draws in the same order.
- **Harness.** `scripts/check_reference.py` plays seeded games in both, feeding
  the same choices. After each input it compares every log line and the
  state projections: refsim `project()` against the engine's `projection()`,
  RNG state included. When the logs or the RNG diverge, it prints both
  sequences of draws with their call sites; when a projection differs, the
  fields that differ. Engine traces come from `#[track_caller]`; Showdown's
  are recorded by the reference server.
- **Projection.** It holds the turn, the request state and pending forced
  switches, the queue length, the field and side conditions with their
  state, and for every Pokémon its set, HP, status and status state, boosts,
  stats, types, item, ability and their states, moves with PP and stored
  disabled flags, volatiles with their state, last move and target, and its
  turn and action counters. Effect states leave out Showdown's bookkeeping:
  the effect's id and name, and the source's slot, which only Leech Seed and
  Wish read (those two keep it).

- **Team building.** a set without a gender gets the
  species' fixed one, else a random draw (`sample(['M', 'F'])`) when its
  side is created, before team preview; reference mode draws it from
  Showdown's generator. Shiny sets show ", shiny" in their details. The
  reference harness applies the format's `adjustLevel` to every set, as the
  server's team validation does, so a paste without a level plays at 50. The harness runs games in parallel (`--workers`); 10,000 games take
  about 10 minutes on 4 cores. Fast mode keeps its own generator, and the same
  fixes apply to it.

### Turn structure and logging

Fixes found by the harness:
- Showdown names an un-nicknamed forme after its base species
  ("Indeedee").
- It sorts the team-preview picks (equal speeds draw).
- It sorts the active Pokémon for `BeforeTurn`.
- It sorts only the chosen actions at commit, and inserts `beforeTurn` and
  `residual` after.
- Moves without a chosen target draw a random foe when queued, at each
  speed computation and when run, and the move line names it.
- `[still]` blanks the move line's target.
- Status moves run the hit loop's two `Update` events.
- Each duration-only volatile (Protect, stall, flinch...) is a separate
  residual handler, so equal ones tie.
- A Pokémon that fainted this turn still takes part in the residual sort.
- Mega Evolution and fainting reset the stored speed to the Speed stat.
- `-mega` names the base species.
- A move whose user fainted or left does nothing more: `runAction` returns
  at once.
- A switch caused by a move names it (`[from] U-turn`). A fainted Pokémon is
  named without its slot.
- Failures mark the move line `[still]`. This covers a failed TryHit
  (Helping Hand, Yawn), a status move on a Pokémon with another status,
  effects that all returned false (Imprison twice, Encore, Taunt, Trick),
  and a heal at full HP. Ability or terrain blocks return null and print
  nothing more.
- Weather changes sort the active Pokémon (`WeatherChange`).
- Every damage modifier present joins the `ModifyDamage` sort, whether or
  not it changes the hit. That includes screens on either side and a resist
  berry of any type. Equal ones tie and draw.
- A Pokémon leaving on its own (U-turn, Eject Button) runs `BeforeSwitchOut`
  when the switch request is made. It does not run again when it switches.
- `recharge` is not in the dex. With no target type, it draws a random foe
  at each speed computation and when it runs. So does Struggle, which
  targets `randomNormal`.
- Dire Claw's status comes from its secondary, not the move, so its sleep
  is announced without `[from]`.
- Flash Fire's boost ends with a silent message on switching out. It does
  not end on fainting, because a Pokémon at 0 HP keeps its volatiles.
- Boosts apply in the order the move's data lists them (Shell Smash lowers
  Defense first). The dex keeps JSON key order.
- The residual handlers are collected in Showdown's order: the field
  (pseudo-weather, weather, terrain), then per side its conditions and each
  Pokémon's status, volatiles, ability, item and Grassy Terrain heal. Since
  the sort swaps elements, this order decides how ties shuffle. Side
  conditions keep their insertion order.
- A fainted Pokémon's Speed has no modifiers (Choice Scarf, Tailwind),
  since it is no longer active. This sets the order of double replacements.
- A charge move runs `TryMove` (Solar Beam in sun) before failing for
  want of a target.
- The harness chooses again when Showdown refuses a choice only hidden
  information rules out (Imprison's `hidden` disables, Shadow Tag's hidden
  trap). Self-play masks, made from the engine's state, leave such choices
  out, so self-play knows a little more than a player would: which moves the
  imprisoning foe knows, and that it is trapped. The engine's requests and
  choices follow Showdown (below).

### More fixes from the larger runs

- `BasePower` collects every handler present, as `ModifyDamage` does: the
  move's own, Helping Hand, the user's ability and item, Fairy and Dark Aura
  on any Pokémon, and the terrain. Two Mega Floette at equal Speed tie on
  every move.
- `TryMove` sorts Armor Tail, Queenly Majesty and Damp. `Accuracy` sorts
  No Guard and Glaive Rush, and runs for moves that can't miss too.
- At the end of the turn, `DisableMove` sorts each Pokémon's Choice lock,
  Disable, Encore, Taunt and Throat Chop, and foes' Imprison.
- TryHit runs by priority across all targets: every Wide Guard (4) before
  any Protect (3).
- A fainted Pokémon's queued move loses its ability priority (Prankster).
  Fainting also cancels a pending switch, so a U-turn user knocked out by
  Rough Skin asks for no replacement mid-turn.
- Flash Fire's absorb sets the shared move's accuracy to true, so a spread
  move's other targets skip the accuracy roll.
- A two-turn move is redirected (Lightning Rod) on its charging turn too, in
  this Showdown version.
- Roar with nothing to bring in fails like any effect returning false.
  `[spread]` lists every target when the move did something to one, and
  none otherwise.
- Emergency Exit checks the user after contact damage (Rough Skin) and after
  recoil. The Champions version leaves other pending switches alone.
- Struggle is modelled. It is typeless (Stellar stands in for `???`), has a
  quarter-HP recoil, and is exempt from the Choice lock.
- A Pokémon's stored speed starts at its Speed stat and returns to it when
  it switches out (`clearVolatile` -> `setSpecies`). Switch-in sorting uses
  that value until the next refresh, not the current modified Speed.
- A second Wide Guard from the ally succeeds silently. Its `onHitSide`
  returns true and still gives the user the stall counter.
- White Herb received from Trick is used at once on a lowered stat, before
  the `-item` message.
- Emergency Exit also checks the user after Life Orb.
- Imprison's disables are hidden only in the request for the side's last
  active Pokémon (`isLastActive`): it shows the moves as usable, and Struggle
  only when no move is shown usable. When every move is in fact disabled,
  Showdown turns any move chosen with a valid target into Struggle at a
  random target. Otherwise it refuses an imprisoned move.
- A Struggle choice ignores "mega": the Pokémon does not Mega Evolve, and
  Showdown does not check that it could. `default` (autoChoose) leaves the
  target to be drawn when the action is queued.

### State the log does not show

- Showdown stores each move's disabled flag in the end-of-turn
  `DisableMove` pass and keeps it until the next one, benched or not. A
  fainted Pokémon still in its slot gets the pass, but only Fake Out's own
  handler applies to it. The pass also ends a Choice lock once the item or
  the move is gone.
- A fainted Pokémon gains no `activeTurns`.
- `lastMoveTargetLoc` survives switching out and fainting.
- Sleep and freeze keep `startTime`. A fainted Pokémon keeps its old
  status state; only a cure clears it.
- `abilityState` is reset on switching in and on an ability change, and
  kept on the bench: Trace's `seek`, Unnerve's `unnerved` (false once it
  ends).
- The 'start' action waits in the queue during team preview. A win leaves
  the remaining actions in the queue.
- Helping Hand stores its multiplier: 1.5, times 1.5 for each repeat.

### Perish Song, Shadow Tag, Psychic Noise, one-hit KOs, Skill Swap, Soak, Steel Roller, Haze

- A residual handler whose effect expires skips the faint check that
  follows other handlers. A Perish Song faint therefore waits for the next
  handler that runs, or for the end of the residual action, after `upkeep`.
- A Pokémon's volatiles are kept in the order they were added, and that
  order decides how equal residual handlers shuffle.
- Every damage but `directDamage` (Struggle recoil, Clangorous Soul's cost)
  runs the `Damage` event, whose sort includes Damp on any active Pokémon.
  Two Damp holders at equal Speed draw on every hit.
- Throat Chop and Heal Block tie in `BeforeMove` and in `ModifyMove`.
- A move's own heal goes through `TryHeal`. Heal Block refuses it, and the
  move fails with a second `[still]`.
- Mega Evolution replaces the ability after the `-mega` message, so Flash
  Fire's end follows it.
- A move without a target prints "null" where the target goes.
- A side's entry hazards run at a switch-in in the order they were
  created (`effectOrder`), with no draw.
- Electric and Misty Terrain announce blocking Yawn's sleep, as they do
  for a status move.
- Triple Axel's later hits roll accuracy again, scaled by a table of stage
  multipliers, and compare the draw with the unrounded chance.
- A choice refused for hidden information gets "[Unavailable choice]" and
  an updated request: Imprison reveals every disabled move, Shadow Tag the
  trap. The side's last active Pokémon is told `maybeTrapped` and
  `maybeDisabled` until then.

### Hard Press, Power Trip, Stone Axe, Rage Fist, Double Shock

- Rage Fist's counter (`timesAttacked`) counts every hit of a multi-hit
  move, after the faint check, and resets when the Pokémon switches out.
- Double Shock leaves its user typeless: the type "???" has no chart
  entry, so it is neutral to every type and immune to none.
- Sucker Punch fails against a target that must recharge. Encore can
  replace the queued Recharge with a damaging move, and the check still
  holds.

### Substitute

- A substitute takes a hit in `TryPrimaryHit`, before damage is rolled for
  the move's other targets. Recoil and drain come from the damage it took
  (drain rounds up there), and the move's `AfterSubDamage` runs: Ice
  Spinner and Steel Roller end the terrain, Stone Axe sets Stealth Rock,
  Air Balloon pops.
- The target behind it is then out of the damage, the effects on it and
  `DamagingHit`, but it still counts as hit: the user's own effects apply
  (self boosts, U-turn's switch, recharge), each secondary still rolls and
  only its effect on the user applies, and a resist berry's handler still
  joins the damage sort without being eaten.
- A status move it blocks fails with "-fail", yet counts as a success
  (`moveThisTurnResult`): the target's result is null, not false.
- Intimidate makes a Pokémon behind a substitute "-immune".
- Cursed Body ignores Struggle.
- Emergency Exit also checks the first Pokémon of a `runSwitch` action
  against its HP before entry hazards.

### Phantom Force

- Its own volatile lasts two turns, next to `twoturnmove`, so both join
  the residual sort and draw against each other.
- While charging, the user is semi-invulnerable: every move aimed at it
  misses in the `Invulnerability` step, before Protect and TryHit, unless
  the user or the target has No Guard; Helping Hand is exempt. Perish Song
  misses it too. The terrains leave it alone: no Grassy Terrain heal, and
  no Electric or Misty Terrain protection.
- `breaksProtect` (Feint, Phantom Force) removes Protect and the side's
  guards; all but Feint announce it with `[broken]`.
- Struggle is not one of the Pokémon's moves: the tracker no longer
  records it.

### Sheer Force, Dragon Darts

- Sheer Force (Mega Camerupt) strikes with a copy of the move without its
  secondaries, and without its `self` effects when it had secondaries; a
  `hasSheerForceBoost` move keeps them. Either way the move has 1.3x
  power, and `AfterMoveSecondarySelf` (Life Orb, Shell Bell) is skipped.
  King's Rock still adds its flinch chance afterwards. Champions doesn't
  skip `AfterMoveSecondary`.
- Dragon Darts (`smartTarget`) aims one dart at the target and one at its
  ally, without the spread modifier. The first target to drop out (a miss,
  an immunity, Protect) turns that off without a message, and the other
  takes both darts. Follow Me and Rage Powder turn it off too. Each dart
  after the first gets an `-anim` line; there is no `-hitcount`. Each
  target counts one attack, and only the second is checked for Emergency
  Exit (Showdown's damage list loses the first).
- A move's own TryHit runs before a substitute's TryPrimaryHit, so Yawn on
  a Pokémon with a status and a substitute fails outright.

### Magic Bounce

- Magic Bounce acts in TryHit at priority 1, after Protect and before the
  other abilities, for each target in order: the holder uses the
  reflectable move back on its user (`useMove`, "[from] ability: Magic
  Bounce"). The bounced move spends no PP, ignores Pressure, sets no
  Choice lock, can't be bounced again, keeps the original's priority but
  not its Prankster boost, and its result becomes the holder's
  `moveThisTurnResult`. Later attributes of the original move go to the
  line written last.
- Moves on the foe's side (Stealth Rock) bounce in TryHitSide off the
  fastest holder on that side (ties drawn), unless the side's first
  Pokémon is semi-invulnerable.
- Tracker: a move used through an ability reveals the ability, not a
  move; Feint and "[broken]" protection end the side's guards.
  `check_tracker.py --require` puts a team using the given entities on
  one side, as `check_reference.py` does.
- Unnerve blocks berries only once its start has run (`unnerved`): a
  Pokémon switching in at low HP next to it eats its Sitrus Berry in the
  Update after its switch, before `runSwitch`.
- Redirection (Follow Me, Lightning Rod) breaks a speed tie by the older
  ability state (`abilityState.effectOrder`), not at random. The engine
  stamps each new ability state (switch-in, ability change, Skill Swap).
- Tracker: an item Trick hands over can be used up (White Herb) before
  the line that names it.
- Revival Blessing marks the user's slot (a slot condition) and sets its
  switch flag even with nobody left to switch in (`reviveSwitch`). The
  switch request offers every fainted teammate, also one that fainted in
  its slot earlier in the turn, and its `revivalblessing` action (order 6)
  brings it back at half HP. A teammate revived in its slot isn't active
  (`isActive` false since it fainted): its ability and item are ignored,
  damage, heals and boosts skip it, and it switches back in at the end of
  the queue, after the residual phase (`queue.addChoice` appends). Its max
  PP is its base PP (`noPPBoosts`).
- Switch choices name team positions 1-6, so Revival Blessing's targets,
  fainted or not, can be chosen.
- Bugs the synthetic sweep found, fixed:
  - Knock Off's `TakeItem` runs Unburden before a Mega Stone refuses.
  - Magic Bounce's `onAllyTryHitSide` holders sort by the plain Speed
    stat, and a semi-invulnerable holder passes it on.
  - `SetStatus` sorts its handlers (Insomnia next to Flower Veil draws).
  - Eject Button doesn't activate when the holder is already being
    forced out.
  - At the end of the hit loop, a single-target move counts as dealing 0
    on top of `hurtThisTurn` for Emergency Exit, so Substitute's cost can
    trigger it.
  - Throat Chop and Heal Block stop a called sound or healing move in
    `onModifyMove` (Magic Bounce), after a random-foe draw.

### Partial trapping

- Bind, Wrap, Fire Spin, Infestation and the rest last 5 or 6 turns,
  drawn after `TryAddVolatile`. At each residual the duration runs down
  first; then the volatile ends silently if its user has left, fainted, or
  has no `activeTurns` yet. A lead that binds on turn 1 frees its target at
  the first residual, without damage.
- Substitute's start frees its user from partial trapping, silently.
- A `cantusetwice` move (Gigaton Hammer, Blood Moon) is disabled right
  after its use, in the end-of-turn `DisableMove` pass.

### Steel Beam, Moxie, Assurance...

- Steel Beam's cost and High Jump Kick's crash name a condition that
  doesn't exist, so the line reads "[from] steelbeam" ("highjumpkick").
  The crash (`MoveFail`) comes only when the move reached the hit steps and
  hit nothing: not when TryMove or a missing target stopped it. Steel Beam
  pays half its max HP (rounded) through `applyRecoilDamage`, once per
  move, also when only a substitute took the hit.
- Moxie acts in `AfterFaint`, which runs once per `faintMessages`, for the
  last Pokémon in the faint queue: it raises Attack by the queue's length
  when that last faint came from a move, and not at all after a win.
- `hurtThisTurn` (Assurance) and `statsRaisedThisTurn` (Alluring Voice,
  Burning Jealousy) are in the projection. Neither resets at the start of
  turn 1, so a boost from a lead's switch-in counts on turn 1. Only damage
  through `spreadDamage` sets `hurtThisTurn`, never `directDamage`; fainting
  clears it but keeps `statsRaisedThisTurn` until the slot is refilled.
- Poltergeist's `-activate` names the target's item: the tracker learns it.
- Gigaton Hammer (`cantusetwice`) is disabled by the end-of-turn pass
  after it was the last move used.
- Eject Button stays put on a target that Dragon Tail or Roar will drag
  out (`forceSwitchFlag`).

### Disguise, Mold Breaker, Stance Change, Magic Guard

- Mold Breaker's ignoreAbility lasts from the user's ModifyMove to the end
  of its action (`clearActiveMove`, after phazing). Other Pokémon's
  `breakable` abilities still join every event sort, so ties still draw;
  only their effects are skipped. Levitate is ignored too (`isGrounded`),
  but core `hasAbility` checks are not (Parting Shot's Mirror Armor).
- Stance Change's forme change runs in a runEvent handler, so the
  `-formechange` line names no ability. Any forme change resets the
  stored speed to the new Speed stat, and a temporary forme reverts
  silently on switching out or fainting; the request's details keep the
  base species.
- Disguise also takes a confusion self-hit (its effect is a move). The
  damage line still shows, the Damage event stops there (no Focus Band
  roll), and the hit still counts as damaging (Rough Skin acts). The bust
  is `detailschange` and an eighth of max HP "[from] pokemon:
  Mimikyu-Busted"; `abilityState.busted` stays until it switches out.
- Magic Guard stops recoil, Life Orb, hazards, weather and status damage;
  damage from an ability is announced on its holder ("-activate|HOLDER|ability:
  Rough Skin").
- Gigaton Hammer (`cantusetwice`) is disabled at the end of a turn it was
  used in. Knock Off on an item that can't be taken (a Mega Stone) still
  runs TakeItem, so Unburden takes note.

### Weather and terrain abilities

- Cloud Nine announces itself only on switching in (`onSwitchIn`); its
  start and its end (switching out, fainting, being replaced) each run
  `eachEvent('WeatherChange')`, whose sort draws. Its `abilityState.ending`
  is in the projection. While it is on the field the weather still counts
  down and announces its upkeep, and rain and sun still sort the actives
  for `eachEvent('Weather')`, but no `Weather` handler runs (Rain Dish, Dry
  Skin, Ice Body).
- Dry Skin's 1.25x from Fire is the target's `onSourceBasePower`
  (priority 17), which joins the `BasePower` sort after the foes' handlers
  and before the terrain.

### Hex, Facade, Lash Out...

- Facade skips the burn halving (Champions `modifyDamage`), and its own
  `onBasePower` doubles it for any status but sleep. Lash Out reads
  `statsLoweredThisTurn` (projected), set like `statsRaisedThisTurn`.
- Raging Bull breaks the target side's screens in its own TryHit, before a
  substitute, like Brick Break; its type follows Paldean Tauros's forme.
- Tri Attack's secondary picks burn, paralysis or freeze after its chance
  roll and sets it without an effect, like Dire Claw.

### Healing

- Rest's sleep replaces any other status. Its start still draws the usual
  2-3 turns before Rest sets 3; a Lum Berry that cures it at once leaves
  the empty status state holding those 3 turns. Electric Terrain announces
  blocking it; Misty Terrain and Leaf Guard block it silently.
- Moonlight, Morning Sun and Synthesis at full HP print `-fail|X|heal`
  but count as having done something (`moveThisTurnResult` true).
- Wish is a slot condition: it heals whatever stands in the slot at the
  residual (order 4) of the next turn, half its user's max HP (kept as a
  float in the state). Healing Wish heals and cures, silently, the first
  hurt or statused Pokémon to come into its slot; only Wish and Leech Seed
  keep `sourceSlot` in the projection.
- A status move whose user faints (Healing Wish) shows the faint inside
  the hit loop, between its two `Update` events, so its result stays
  true.
- `boost` runs `ChangeBoost` and `TryBoost`, then `AfterEachBoost` per
  stat changed and `AfterBoost`, each a sorted event: Own Tempo and an
  ally's Flower Veil at equal speed draw on every stat drop.

### Residual volatiles, Moody

- Leech Seed drains into whoever stands in its user's slot now
  (`sourceSlot`), unless fainted: `[from] Leech Seed|[of]` that Pokémon,
  then a silent heal of the damage dealt, which Big Root raises. Grass
  types are immune at `TryImmunity` (`-immune`, before accuracy).
- Champions halves Salt Cure's residual: 1/8 of max HP to Water and Steel
  types, 1/16 to the rest. Its volatile is `noCopy`, so Baton Pass leaves
  it behind; Leech Seed, Aqua Ring and Ingrain pass.
- Ingrain grounds its Pokémon ahead of every other `isGrounded` check
  (Flying, Levitate, Air Balloon), traps it, and stops a drag with
  `-activate|X|move: Ingrain` after Guard Dog and Suction Cups (priority 1).
- Salt Cure and partial trapping share residual order 13, so on one
  Pokémon they tie and draw like any equal residual handlers.
- Moody builds its boost as +2 then -1 (each a `sample` over Atk to Spe
  that can still move), so the `-boost` line comes before the `-unboost`
  whatever the stats' order.

### Electro Ball, Axe Kick...

- A `basePowerCallback` runs before the critical-hit roll. Electro Ball and
  Gyro Ball read `getStat('spe')` of the user, then the target, each running
  the ModifyBoost and ModifySpe sorts.
- Axe Kick's confusion lasts 3 to 5 turns (`random(3, 6)`), not 2 to 5.
  Its crash, like Supercell Slam's, names its own id ("[from] axekick").
- Payback doubles unless the target is still to move or came in since the
  last end of turn (`newlySwitched`, i.e. no active turn yet).
- Fell Stinger's own `AfterMoveSecondarySelf` runs before the items' (Life
  Orb), on the move's last chosen target.
- Snipe Shot (`tracksTarget`) skips RedirectTarget: Follow Me and Lightning
  Rod don't draw it. Its `getTarget` also returns the action's
  `originalTarget` while that Pokémon is active, as for Stalwart, so after
  an Ally Switch it follows the Pokémon, not the slot.

### Steely Spirit, Fur Coat, Fluffy...

- Defensive abilities that Mold Breaker can ignore (Fluffy, Punk Rock,
  Heatproof) still join their event sorts under it; only their effect goes.
- Heatproof halves burn damage in the Damage event (floored, at least 1),
  where its handler joins the sort with Damp and the rest.
- Fluffy's ModifyDamage handler runs even when the two halves cancel (a
  Fire contact move), as `chainModify(1)`.
- Steely Spirit is an `onAllyBasePower` handler, so it boosts its holder's
  own Steel moves too.

### Status, volatile and stat moves

- Toxic from a Poison-type user skips the Invulnerability step as well as
  the accuracy roll, so it hits a Pokémon charging Phantom Force.
- Belly Drum announces its boost as `-setboost|X|atk|6|[from] move: Belly
  Drum`, not `-boost`.
- Heal Pulse on a Pokémon at full HP returns NOT_FAIL: `-fail|target|heal`
  without `[still]`, and the move counts as a success.
- Endure joins the `Damage` sort at priority -10 (subOrder 2), before
  Sturdy and Focus Sash, and acts on confusion damage too (its effect type
  is Move).
- Minimize's volatile joins the target's `Accuracy` and `ModifyDamage`
  sorts (subOrder 2) for every move, `minimize` flag or not.
- Memento's user faints in `runMoveEffects`, whether or not the stat drops
  worked, but not when a substitute or a miss stopped the move; the faint
  message comes between the hit loop's two `Update`s.
- Ally Switch keeps its own stall counter (`allyswitch`, rolled in
  PrepareHit). The swap takes the Pokémon's queued actions with it; with no
  ally it fails with NOT_FAIL.
- Baton Pass copies stat stages and every volatile without `noCopy` (not
  Choice lock, Encore, Disable, Imprison, Yawn, Glaive Rush, Minimize),
  their durations and order included.
- Symbiosis and Unburden share the `AfterUseItem` sort, ties drawn. Eject
  Button sets its holder's switch flag before using the item, so Symbiosis
  gives nothing to an ejecting ally.

### Encore, Disable, Taunt and related volatiles

- After You moves the target's queued move to the front of the queue with
  order 3; Quash gives it order 201, which takes effect at the re-sort
  after the current action.
- Speed Swap, Power Split and Guard Split change `storedStats`; the
  request's stats stay `baseStoredStats`, and switching out or fainting
  restores them (`setSpecies`). Baton Pass doesn't pass them.
- Endure leaving 0 damage (a Pokémon at 1 HP) ends the `Damage` event: no
  Focus Band roll follows.

### Destiny Bond, Outrage, Curse

- `lockedmove`'s `onStart` stores `effect.id`, the move being used; when
  Copycat, Instruct or Sleep Talk calls Outrage, that is Outrage, not the
  caller (the engine reads the active move, not `last_move`).
- `lockedmove` draws its true length (2 or 3) when it starts; its
  `duration` restarts at 2 only while that length is at least 2. Its
  `onAfterMove` joins White Herb's `onAnyAfterMove` sort (subOrder 2 vs 8)
  and ends it at duration 1. A turn the user can't move leaves it to the
  residual phase, whose end confuses too (`[fatigue]`); sleep at the
  residual drops it without confusion. Raging Fury has the same data.
- A faint Destiny Bond causes (`source.faint()`, from a foe's move only) is
  handled in the same `faintMessages` loop, and forces the win check. The
  volatile ends at the holder's next `BeforeMove` (priority -1) unless that
  move is Destiny Bond, and on `MoveAborted`; a second Destiny Bond in a
  row removes it in PrepareHit and fails.
- Champions makes a non-Ghost's Curse a `self` move in the request and in
  `resolveAction`: no target is chosen and none is drawn. A Ghost's Curse
  aimed at an ally becomes `randomNormal` (two draws, like any ModifyMove
  target change); its curse does `baseMaxhp / 4` at residual order 12.
- Worry Seed on Truant or Insomnia fails in `onTryImmunity` (`-immune`,
  before the accuracy roll); its other checks, and Entrainment's and Role
  Play's, are `onTryHit` (`-fail`). Worry Seed's `setAbility('insomnia')`
  has no source, so its `-ability` line has no `[of]`; Entrainment's names
  the user, Role Play's the target it copied. Simple Beam's Simple is no
  species' ability, so the exported format data leaves it out.

### Pickpocket, Red Card, Anticipation

- Pickpocket (ability, subOrder 7) and Red Card (item, 8) join freeze and
  Eject Button in the one `AfterMoveSecondary` speed sort over all hit
  targets. Pickpocket's `takeItem` runs TakeItem first (Unburden notes it
  even when a Mega Stone stays), then `setItem` and its Start (Air
  Balloon's `-item`) before its own `-enditem [silent]` and `-item` lines.
  It still steals from a U-turn user (`switchFlag` is the move id), not
  from one Eject Button is sending out (`switchFlag === true`).
- Red Card is spent (`-enditem ... [of] attacker`) even when DragOut
  holds the attacker (Guard Dog, Suction Cups, Ingrain). Its
  `forceSwitchFlag` makes the attacker's Life Orb and Shell Bell skip
  their `AfterMoveSecondarySelf`. The dragged-out Pokémon's
  `clearVolatile` drops its own pending U-turn switch, so a later
  ordinary switch from that Pokémon has no `[from]`.
- Anticipation checks each damaging move of each foe against the holder's
  current types with the dex chart alone (no abilities, no Freeze-Dry),
  plus OHKO moves, and shudders at most once.

### Attract, Cute Charm, Block, Mean Look, Spirit Shackle, Jaw Lock

- Attract's volatile checks the genders in its start, after TryAddVolatile
  has sorted (Cute Charm on a same-gender attacker still draws for ties);
  the move's own TryImmunity (`-immune`) catches them first. Oblivious is
  `Immunity` to it, silently for Cute Charm, and its onUpdate ends a Mold
  Breaker's Attract (`-activate`, the silent `-end`, then `-end ... move:
  Attract|[from] ability: Oblivious`) before a Taunt.
- Attract ends (`-end|Attract|[silent]`) in the first Update after its
  source left the field (`source.isActive`). BeforeSwitchOut's Update runs
  while the source is still in, so the line follows the newcomer's switch.
  It rolls in BeforeMove at priority 2, between confusion and paralysis.
- Block, Mean Look, Spirit Shackle and Jaw Lock add `trapped` linked to the
  source's `trapper` (`addVolatile(..., 'trapper')`). The source gets its
  own TryAddVolatile sort when its `trapper` is new. A Ghost is immune
  silently, and Block fails. Either side's `clearVolatile` (switching out,
  fainting, Baton Pass, as both are `noCopy`) ends the link without a
  message. `trapped` joins the Pokémon's own `TrapPokemon` handlers, which
  tie with partial trapping and Ingrain (`effectOrder` breaks ties only
  for SwitchIn and RedirectTarget). The projection shows
  `trapped.linkedStatus: "trapper"`; `trapper`'s is the condition object,
  which it leaves out.
- Jaw Lock traps the user (by the target) first, then the target. Spirit
  Shackle's secondary traps only while the user is active.

### Illusion

- Illusion picks its disguise in BeforeSwitchIn, after the positions swap:
  the last unfainted Pokémon after it in the side's order, an active ally
  to its right included. Every ident shows the disguise's name, the switch
  line its species, gender and shininess at the user's own level. Any
  damaging hit ends it, even one that knocks it out (`replace`, then
  `-end|Illusion`); a faint under the disguise is announced under the
  disguise's name. Switching out keeps the disguise (`beingCalledBack`), so
  the projection's `illusion` stays set on the bench.
- `setAbility` (Entrainment, Worry Seed, Role Play) runs the old ability's
  End, so it ends an Illusion too: `replace` and `-end|Illusion` come
  before the `-ability` line, which then names the real Pokémon.
- Tracker: one's own Illusion user is told apart by its max HP or by its
  disguise being out already, and the request's actives settle the rest. A
  foe showing a name already on the field is unmasked by elimination;
  `replace` and a Mega Evolution correct a wrong guess.
  `check_tracker.py` leaves out a foe's disguise, and the user until its
  `replace`, since no player can tell them apart.

### Air Balloon and Symbiosis

- An Air Balloon popped by a hit runs the whole `AfterUseItem` event, so
  an ally's Symbiosis passes its item (`-activate ... ability: Symbiosis`
  right after the `-enditem`), not only the holder's Unburden.
- Helping Hand adds its volatile through `addVolatile`, whose
  `TryAddVolatile` sorts its handlers (Flower Veil, Sweet Veil on the
  target's side) and draws on a tie; a second Helping Hand on the same
  target only restarts it and draws nothing.

### Unaware, Skill Link, Iron Ball

- `calculateStat` runs a ModifyBoost sort for each stat it reads: the
  attacker's, then the defender's in `getDamage`, and twice on the Pokémon
  for a confusion self-hit. With two Unaware on the field they draw.
- Unaware reads `activeTarget`: the move's current target in damage and
  accuracy, but confusion makes the confused Pokémon its own target first,
  so Unaware never changes a self-hit.
- A later `multiaccuracy` hit runs ModifyBoost on the user and the target,
  then ModifyAccuracy. Skill Link removes `multiaccuracy` and fixes a 2-5
  hit move at 5 hits, so the hit count isn't drawn.
- Iron Ball's `onEffectiveness` returns 0 for every type of a Flying-type
  holder hit by a Ground move: neutral, whatever its other type.

### Explosion, Final Gambit, Normal Gem

- `selfdestruct: 'always'` (Explosion, Self-Destruct, Misty Explosion)
  faints the user right after TryMove (Damp), before the no-target check
  and the move's Try: the user is at 0 HP for the whole move, its faint
  comes first in the queue, and its faint's source is itself (AfterFaint).
- Final Gambit's `damageCallback` takes the user's HP and calls `faint()`
  with no source, so a miss, Protect or a Ghost-type target spares it.
- A gem is the only item `useItem` uses on a Pokémon at 0 HP, and its
  volatile `affectsFainted`: Explosion still uses up a Normal Gem
  (`-enditem ... [from] gem|[move] Explosion`) and gets the x1.3. The gem
  fires in TryPrimaryHit, before Substitute's handler; the `gem` volatile
  lasts until the residual phase.
### Transform and Imposter

- `transformInto` fails silently on a fainted target, a substitute, or
  when either Pokémon is already transformed or under an Illusion. `setSpecies` stores the
  Speed of the target's species with the user's own set; the copied
  stats replace the stored stats but not that stored speed. Types (from
  before Roost), boosts, `timesAttacked` and the move slots (5 PP each at
  most) are copied, then the critical-hit volatiles as Psych Up copies
  them (Focus Energy, Dragon Cheer: removed, then added `[silent]`, each
  running TryAddVolatile); `-transform` comes before the ability, which starts
  only when it differs (Intimidate copied by Imposter acts at once). A
  `notransform` ability (Disguise) does nothing, and Stance Change
  changes no forme.
- Imposter acts in `onSwitchIn`, not `onStart`: Skill Swap or Trace
  giving it does nothing. It targets the foe directly across.
- Switching out or fainting brings back the Pokémon's own move slots
  (`baseMoveSlots`, with the PP and stored disabled flags they had) and
  species. The request keeps the base species in its details and its own
  stats (`baseStoredStats`), and lists the copied moves and ability.
- A queued move keeps the move chosen (`action.move`), not a slot: a
  transformed Pokémon that faints before moving still sorts with the
  copied move's priority and target type.
- Tracker: `-transform` gives the user the target's known species,
  boosts, moves and ability until it leaves the field. The observation
  shows a transformed Pokémon as the species it copied, with a
  `transformed` flag.

### Protean, Libero, Analytic, Zero to Hero, Anger Point, Berserk, Purifying Salt, Stalwart

- Protean and Libero also act on a two-turn move's charging turn:
  `twoturnmove`'s onStart runs PrepareHit, after `-prepare`. Their
  `abilityState.protean` / `libero` holds them to once per switch-in.
- Champions' `hitStepMoveHitLoop` runs AfterMoveSecondary even when Sheer
  Force dropped the secondaries, and for status moves too: Berserk's
  handler (which readies healing berries again) acts on a Rest user. A
  fainted holder is no longer active, so its handler doesn't run.
- Berserk (Champions): any single-hit move's damage, a confusion self-hit
  included, sets `checkedBerserk` false, and healing berries wait for its
  AfterMoveSecondary. The boost uses the target's last-hit damage (the
  move's total for a non-smart multi-hit move). `eatItem` runs a
  TryEatItem sort, so equal Berserk and Unnerve handlers draw.
- Stalwart keeps the move's `originalTarget` from `resolveAction` (not
  getActionSpeed's re-sorts) while it is active, without the random draw
  getTarget would make, and its ModifyMove sets `tracksTarget`, so nothing
  redirects the move. It applies to `recharge` and Struggle too.
- Zero to Hero's switch-out change is permanent (`detailschange` while
  the Pokémon is still out, then `setAbility`); the `-activate` comes at
  the next switch-in only (`heroMessageDisplayed`, now projected).

### Electromorphosis, Aftermath, Aroma Veil, Big Pecks, Keen Eye, Illuminate, Corrosion

- Aroma Veil acts in `TryAddVolatile`, after the sort: the first Aroma
  Veil holder in sorted order (target or ally) names itself in `[of]`,
  and only a move's volatile is announced (`-block`); Cursed Body's
  Disable and Cute Charm's Attract are blocked silently. Taunt, Encore
  and Cursed Body's Disable also sort there (when the volatile is new),
  before their start's own checks (Encore's last move, Disable's).
- Electromorphosis restarts a present `charge` (no sort) and its start
  line names the active move: `-start|X|Charge|<move>|[from] ability:
  Electromorphosis`. Aftermath's handler runs with the holder at 0 HP,
  before faint messages.
- An ability's End on `setAbility` is a `singleEvent`, which Mold Breaker
  doesn't suppress: Entrainment from a Mold Breaker user still ends Flash
  Fire's volatile (`-end|X|ability: Flash Fire|[silent]`).

### Mental Herb, Gravity, Healer, Leppa Berry, Stockpile, Spit Up, Swallow

- A status move's effect that returns `NOT_FAIL` (a heal at full HP:
  Moonlight, Heal Pulse, Swallow) counts as done, but spreadMoveHit drops
  that target before `afterMoveSecondaryEvent`, so Berserk doesn't set
  `checkedBerserk` on it.
- Gravity is a field handler: its `BeforeMove` (priority 6) and
  `ModifyAccuracy` (priority 0, so first in the chain: x6840/4096) sort
  after the Pokémon's own handlers of equal priority (no speed), and never
  draw. It grounds everything ahead of Ingrain, disables `gravity` moves,
  and hides Air Balloon's switch-in `-item`.
- Mental Herb (`onUpdate`) is used up first (`-enditem`, then
  `AfterUseItem`), then ends Taunt, Encore, Disable and Heal Block in that
  fixed order. Champions' Encore leaves a holder's chosen move alone.
- Stockpile remembers per stat whether each layer moved it
  (`effectState.def`/`spd`, counted down); its end (Spit Up's own
  `AfterMove`, hit or miss, or Swallow's `onHit`, after the heal) undoes
  only those. The volatile is `noCopy`.
- Leppa Berry's `onEat` also runs for Bug Bite and Pluck, on the eater's
  moves: `-activate|X|item: Leppa Berry|MOVE|[consumed]`.
- Champions' Healer cures each statused adjacent ally with chance 1/2.

### Safeguard, Spikes, Toxic Spikes, Sticky Web, Ceaseless Edge, Defog, Court Change

- Safeguard's `onSetStatus` and `onTryAddVolatile` (confusion, Yawn)
  return `null`, not `false`: a blocked Thunder Wave or Confuse Ray shows
  `-activate|X|move: Safeguard` and no `-fail`. It blocks any status from
  another Pokémon (Toxic Spikes and contact abilities silently), but not
  Yawn's own sleep, and an Infiltrator's moves pass it against foes. As a
  side condition (subOrder 4, no speed) it runs after the abilities and
  before the terrain, and never draws.
- Defog's `onHit` counts only the evasion drop (skipped behind a
  substitute) and removed hazards as success: screens and Safeguard leave
  with their own `-sideend` and no `[from]`; hazards on both sides get
  `[from] move: Defog|[of] USER`. The terrain ends either way, before the
  `-fail` of a Defog that did nothing else.
- Court Change moves each side condition's state object (duration,
  layers, `effectOrder`); on the receiving side the swapped conditions
  come after the ones it keeps (Wide Guard, Quick Guard), in the giving
  side's insertion order. `-swapsideconditions` then
  `-activate|USER|move: Court Change`; nothing to swap fails the move.
- Spikes take `floor([0, 3, 4, 6][layers] * maxhp / 24)`; the line is
  `-sidestart|SIDE|Spikes`, without `move:`. Ceaseless Edge's extra layer
  past three is silent.

### Charge, Thief, Covet, Electrify, Fairy Lock

- Charge ends (`-end|X|Charge|[silent]`) in the user's `AfterMove` after an
  Electric move other than Charge, judged by the active move's type after
  ModifyType (Electrify counts), and in `MoveAborted` by the base type. Its
  handler ties with `lockedmove`'s in AfterMove and with `twoturnmove`'s
  and `destinybond`'s in MoveAborted, so those sorts draw. A user knocked
  out by its own recoil keeps it: `removeVolatile` does nothing at 0 HP.
  Restarting Charge announces it again and succeeds.
- Thief and Covet steal in the move's `onAfterHit`, right after the
  `DamagingHit` reactions, only while the user stands and holds nothing.
  Unburden on the target takes note even when a Mega Stone then refuses;
  Covet's `-item` line is the only sign the target lost its item.
- Electrify's `ModifyType` (priority -2) runs after the -ate abilities, so
  the -ate power boost stays. Its own TryHit fails on a target that has
  already moved this turn.
- Fairy Lock is a pseudo-weather whose state has no target, so its
  `TrapPokemon` and duration-only residual handlers have subOrder 2 and no
  speed. It traps every active Pokémon (Ghosts aside) for the next turn's
  choice and ends silently.

### Forewarn, Gastro Acid, Klutz, Lock-On, Magician, Magnetic Flux

- A suppressed ability (Gastro Acid) or item (Klutz) keeps its handlers in
  every sort (residual, switch-in), so they still draw on ties, and is
  skipped when run (`Battle.c.sorting` turns the suppression off while the
  engine collects handlers). `End` is never suppressed: Gastro Acid's start
  writes `-endability`, then runs the ability's End (Illusion's `replace`,
  Unnerve's state, Air Lock's), and fainting runs End too. Under Klutz a
  Choice lock stays (`getItem()`) but isn't enforced; Friend Guard and
  Magic Bounce check `ignoringAbility` at run time.
- Magician steals in `AfterMoveSecondarySelf` (before Life Orb and Shell
  Bell, whose handlers were collected while it held nothing) from the hit
  targets sorted by speed, fainted ones included; a fainted target is no
  longer active, so its Unburden is ignored, but a Mega Stone still
  refuses. Only `-item ... [from] ability: Magician|[of] X` shows the loss.
- Lock-On's volatile sits on the user and names a Pokémon, not a slot: it
  follows its target through switches. Its `onSourceAccuracy` joins the
  Accuracy sort last (after `onAny` No Guard) and its
  `onSourceInvulnerability` beats Phantom Force. Forewarn samples among the
  foes' moves of equal top power (one draw even for a single move).
- Aftermath respects Damp's `onAnyDamage`, found in a
  synthetic game with a Damp Swampert on the field.

### Move volatiles and TryAddVolatile

- A move's `self.volatileStatus` (Hyper Beam's `mustrecharge`, Roost,
  Glaive Rush) goes through `addVolatile`, so TryAddVolatile sorts first:
  two Sweet Veil holders at equal speed draw.


### Further mechanics

- Chilly Reception's `priorityChargeCallback` is a queued `priorityChargeMove`
  action (order 107, before every move) that adds a one-turn `chillyreception`
  volatile; its `-prepare|X|Chilly Reception|[premajor]` comes at BeforeMove
  priority 100, before sleep or flinch checks. A resist berry's `eatItem` takes
  the attacker as its source, so Cheek Pouch's heal after it reads `[of] attacker`.
  Cud Chew counts 2 from a berry eaten with actions still queued, 1 from one eaten
  in the residual or Update after it.
- Fly, Bounce, Dig and Dive are Phantom Force's charge with a volatile whose
  `onInvulnerability` lets a few moves through (Gust, Twister, Thunder,
  Hurricane...; Earthquake and Magnitude; Surf and Whirlpool). Their doubling
  sits in different events: Bounce in `onSourceBasePower`, Fly, Dig and Dive in
  `onSourceModifyDamage` (a volatile handler at subOrder 2, in the ModifyDamage
  sort). Dig and Dive's `onImmunity` also spares the user sandstorm damage.
  Gravity starting removes Fly and Bounce, drops the user's queued move and
  ends `twoturnmove`, logging `-activate|X|move: Gravity`.
- Effect Spore draws `random(100)` once a contact hit gets past the powder
  immunity (Grass, Overcoat); the status is tried only below 30. Eelevate is
  Levitate (`-immune|X|[from] ability: Eelevate`) and, after a faint from its
  move, raises the best stored stat by the faint count (`getBestStat(true,
  true)`). Battle Bond acts only for Greninja-Bond and Greninja-Ash, which no
  team here has, so it does nothing.
- A Mold Breaker user dragged out mid-move (Red Card) is no longer active, so
  its move stops suppressing abilities: the Intimidate of the Pokémon dragged in
  is still blocked by Inner Focus. The engine's reference to the user now
  follows the position swap of a switch.
- Hunger Switch (residual order 29, subOrder 2) swaps Morpeko and
  Morpeko-Hangry with a plain `-formechange|...|Morpeko-Hangry|` line: the
  residual event's effect is not the ability, so there is no `[from] ability`.
  Plus and Minus count another living active ally only, never the holder.
- Hunger Switch's handler is sorted even under Gastro Acid but its run is
  skipped (no `-formechange`): the residual checks the ability when it runs.
- Smack Down's `onStart` reads `hasAbility(levitate)`, which ignores Mold
  Breaker: a Mold Breaker ally's Smack Down still grounds a Levitate target
  (`-start|..|Smack Down`). Only Gastro Acid and Transform-style ignoring count.
- Fairy Aura and Dark Aura `onStart` return early under `suppressingAbility`
  (not only `breakable`): a Roar or Whirlwind from a Mold Breaker user drags
  the holder in without the `-ability` line.
- Liquid Ooze runs inside `heal`'s `TryHeal` event: a drain or Leech Seed heal
  becomes `-damage|DRAINER|..|[from] ability: Liquid Ooze|[of] HOLDER` even at
  full HP, after Big Root (priority 1) has scaled the amount, and Heal Block on
  the drainer sorts with it. Innards Out deals the fainting hit's damage plus
  the move's earlier hits (`move.totalDamage`). Opportunist's table exists,
  empty, after any foe boost event, so its later release still runs
  `ChangeBoost` and `TryBoost` sorts; Gooey announces itself with `-ability`
  and lowers Speed as a secondary (no second announcement).
- Strength Sap computes the heal from `getStat('atk', false, true)` (the stage counts, no modifiers) before the drop, then `heal(atk, source, target)`: Liquid Ooze turns it into damage (`canOoze` lists `strengthsap`), and the move fails only if neither the heal nor the drop happened. Spite's `deductPP` sets the slot's `used` even when the slot has no PP left (the move then fails). Stuff Cheeks (Champions: no `onDisableMove`) fails in `onTry` without a berry; a Defense that can't rise returns `null` (no failure line); `eatItem(true)` skips `TryEatItem`, so Unnerve doesn't stop it. Sticky Hold's `onTakeItem` blocks `takeItem(source)` by another Pokémon (Thief, Covet, Bug Bite, Pluck, Corrosive Gas, Pickpocket, Magician) and Knock Off, but not a bare `takeItem()` (Trick's own side, Symbiosis, Sticky Barb); Trick and Switcheroo have `onTryImmunity` on `hasAbility('stickyhold')`, which Mold Breaker doesn't bypass. Stall's constant `onFractionalPriority` (-0.1) runs first, so Quick Claw still rolls (`priority <= 0`). Trace's `noability` check reads the raw ability (a Gastro Acid'd foe still counts).
- Uproar's `onAnySetStatus` joins every `SetStatus` sort (volatile,
  subOrder 2, the holder's Speed) for each status, so two Uproar users tie and
  shuffle in `findEventHandlers` order (the target's side first). The first
  handler to act decides the message: `-fail|X|slp|[from] Uproar`, with `[msg]`
  only when X is the holder itself. It returns null, so Rest then fails quietly
  (no `-fail` line). Its residual ticks the duration first (`-end` at 0, none at
  0 HP, because `removeVolatile` does nothing then), then Throat Chop ends it, then
  `-start|X|Uproar|[upkeep]`; `onResidualSubOrder` is 1. `onLockMove` skips PP
  (`[from] lockedmove`) and traps. Wonder Room swaps only the stored Defense and
  Special Defense in `calculateStat` (damage and confusion damage); boosts and
  `ModifyDef`/`ModifySpD` stay with the stat the move names, so Psyshock uses the
  stored Sp. Def with Def stages. Body Press's `onModifyMove` flips its
  `overrideOffensiveStat` and writes a `-hint` (hints are not compared).
  `getStat(unmodified)` (Shell Side Arm) keeps the stored value but takes the
  other defense's stages. Teatime ('all' target) has the first living Pokémon of
  the user's side as the event target, so a forced Sitrus or Oran heal reads
  `[from] item: Sitrus Berry|[of] FIRST_ALLY` unless it is that Pokémon; with no
  berry it logs `-fieldactivate` then `-fail|X|move: Teatime` and the move
  result is null (`NOT_FAIL`). Tidy Up's `-sideend` lines carry no `[from]`.
- Shield Dust (`ModifySecondaries`) drops a hit's secondaries without a `self` part before any chance roll, so a Shield Dust target costs no draws, King's Rock's flinch included. A substitute turns the target `null` first, so then the secondaries roll as usual. Poison Touch checks `hasAbility('shielddust')` itself. Stench adds a 10% flinch secondary in `onModifyMove` (priority -1) unless the move has one, so King's Rock (priority -1, item order after ability) then adds nothing.
- Synchronize passes a status back with a stand-in effect that has no `name` or `effectType`, so Flower Veil blocks it without the `-block` line a status move would get. A charge move runs `addVolatile` twice (`twoturnmove`, then its own id, a condition or not), each with the TryAddVolatile sort: two handlers at equal speed (an ally's Flower Veil and Inner Focus) draw.
- Upper Hand's `onTry` reads `queue.willMove(target)`'s `action.move.priority`, which `getActionSpeed` overwrites with the modified priority (Grassy Glide in Grassy Terrain, Gale Wings) without the fractional part; a Struggle, a recharge and a Status move fail it. Syrup Bomb's `syrupbomb` (duration 4, `noCopy`, order-14 residual) lowers Speed on the first three ticks with the thrower as source and ends silently on the fourth; its `onUpdate` ends it once the thrower is no longer active (a fainted thrower included). Attract, Fling and Syrup Bomb are `Update` handlers of the same sort (volatile, subOrder 2) and draw when two are present. Torment's `onDisableMove` joins the end-of-turn `DisableMove` sort (a tie with Choice lock, Taunt... draws) and disables `lastMove` unless it is Struggle; its start line has no `move:` prefix, Aroma Veil blocks it, Mental Herb ends it between Encore and Disable. Wandering Spirit calls `skillSwap(attacker, holder)` after a contact hit, with no `-fail`: the `-activate|ATTACKER|Skill Swap|HOLDER_ABILITY|ATTACKER_ABILITY|[of] HOLDER` line, then each new ability starts (the holder's first). `Update` runs for a Pokémon at 0 HP that has not fainted yet (a swap into Thermal Exchange logs its `-activate` there); and a Poison Touch handler collected before the swap still names Poison Touch.
- `addType` sets `addedType`, which `getTypes()` appends after the `Type` event (Roost) and `getTypes(true)` leaves out; `hasType` sees it, so a Forest's Curse target is immune to powder and Leech Seed, gets Grass STAB and Grass's effectiveness, and a Trick-or-Treat target is immune to trapping and Normal and Fighting moves and uses a Ghost's Curse. `setType` (and `setSpecies`: switching out, Mega Evolution) clears it; Transform and Reflect Type copy it; the turn start's apparent-type fix (`-start ... typechange ... [silent]`) re-adds it with a silent `typeadd`. Trick-or-Treat on the right-hand Pokémon of a doubles side points its queued Curse at `targetLoc` -1. Simple Beam's onTryHit reads the raw `target.ability` (Simple, Truant) and the ability's `cantsuppress` flag; Simple's `onChangeBoost` doubles before the cap. `singleEvent('Eat')` is suppressed when the eater ignores its items (Magic Room), as Bug Bite shows; `runEvent('EatItem')` still runs.
- `battle.lastMove` is whatever `activeMove` is when an action ends (`clearActiveMove()`): the called move after Copycat or Sleep Talk, nothing new after a move that failed in `runMove` (BeforeMove, Focus Punch, no PP) or after the residual action. `useMove` from an onHit takes `this.effect` (the caller) as `sourceEffect`: `[from] move: CALLER`, Pressure's PP off the caller, and the caller's priority and `pranksterBoosted` carry over; the outer `runMove` then runs AfterMove with the called move and keeps its `moveThisTurnResult` (a faint in the caller's hit loop resets it, so the caller's result wins). Instruct's `resolveAction(...)[0]` is a move's priority-charge or before-turn action when it has one, so only that is queued (order 3, at the front); Quick Claw's FractionalPriority draws again. The Metronome item skips `callsMove` moves; a volatile without onRestart (Follow Me) fails the move; `removeVolatile` does nothing at 0 HP.
- Mega Sol lives in `Pokemon.effectiveWeather()`: when `battle.activePokemon` has Mega Sol and the effect asking is a move or a weather (or Mega Sol), any Pokémon's weather is sun. So during a Mega Sol Pokémon's move its Weather Ball is Fire, Solar Beam needs no charge (with `-activate|X|ability: Mega Sol` after `-prepare` when the field isn't sunny), Thunder and Hurricane drop to 50% in rain, and a Rock or Ice defender loses its Sandstorm or Snow defense boost; abilities' own checks (Chlorophyll, Leaf Guard) see the real weather. Its `onWeatherModifyDamage` runs sun's handler itself and returns the damage, so `priorityEvent` exits before the real weather's handler: Fire x1.5 and Water x0.5 once, under real sun too (never x2.25), and rain does nothing.

## Fixes found by the distribution checks

Found by the fast-mode distribution comparison (general runs, focused runs and real pool teams). Each was a porting bug.

- A Mega's new ability (Intimidate, Drought) now starts on Mega Evolution, and an ability's end handler runs when Mega Evolution replaces it (Flash Fire's boost ends).
- When one spread move hits two Eject Button holders, only the faster holder's button activates; on a speed tie, a random one.
- Knock Off could remove Floettite from Floette-Eternal: the Mega Stone check compared the plain species name ("Floette") instead of the forme the stone names.
- The win was declared as soon as the last foe fainted. Showdown waits until the attacker's recoil and Life Orb have been applied, so the attacker can faint too and the game state differs.
- Quick Claw's roll happens when the action is made (`resolveAction`'s `FractionalPriority`), before the random target is drawn, and again when Encore replaces a queued move. The queue's re-sort reuses the stored fractional priority instead of rolling again.
- Round's `onTry` moves the first other queued Round to the front with `sourceEffect` Round: its move line carries `[from] move: Round` and its power doubles. Last Resort reads `moveSlot.used`, which `deductPP` sets even at 0 PP and switching in clears.

Only the real-team runs caught the Floettite bug; the deferred win showed up in both kinds of run.

## Performance

Random legal play, one CPU core, real teams: Showdown runs about 29 games/s (250 turns/s). The engine runs about 6,000 games/s, 60,000 turns/s (the `bench` example on a pool of real tournament teams, logging off), about 200 times Showdown. That gap is why the engine exists: search and large-scale self-play need it. A shared host's speed varies between runs by 10% or more, so the steady measure is instructions per game under callgrind: about 935,000 (1,500 games, setup subtracted).

Profile with callgrind. With logging off, log lines are not formatted at all (`log_line!`) and Pokémon identities are not built. The benchmark's random player samples each slot's option instead of enumerating every joint choice. Run it with `./target/release/examples/bench <format.json> <teams.txt> [games]`.

What keeps it fast, none of which changes a draw:

- Records that name a Pokémon (`originalTarget`, `attackedBy`) share its name (`Arc<str>`), and the queue's re-sort does not record original targets again (`Resolution::Resort`).
- Teams are prepared once (`battle::Team`: Pokémon and open sheet), so a battle copies them.
- Move flags are a bit set (`MoveFlags`); each effect's event-handler table carries a mask of the events it handles; the dex's lookups by id use FxHash.
- The `Update` and Speed handlers test the raw ability and item first: a suppressed ability or ignored item has no kind, so when the raw one has no handler none can act.
- `update_speed` looks up the Unaware holders (whose `ModifyBoost` sort draws on ties) once, and the actions resolved right after it take the speeds it stored (`Resolution::Chosen`, `Resort`; a debug assertion checks them).
- Short lists (actives, targets, handlers) live in `ArrayVec`s, and lines that only edit the log are built only with logging on.

Where the time goes now (callgrind, share of instructions): the move pipeline (`run_move`, about 35%, a third of it damage), the per-action re-sort (`update_speed` and `resolve_action_frac`, about 12%), the `Update` event after each action (about 8%), legal-option lists for the random player (about 8%), event sorts (about 5%) and allocation (about 10%). No single function is above 5%: what remains is the cost of looking state up through `MonRef`s and the dex, spread over every handler check.