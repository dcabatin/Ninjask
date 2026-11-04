# Parity, speed and training

What can Ninjask gain by giving up parity with Showdown, and how much does
a faster simulator speed up training? Measured on 4 November 2026 on one
core of a 4-vCPU cloud VM (Xeon @ 2.8 GHz), on the 766 VGCPastes teams in
`pools/`. Raw outputs are in `results/`; every script here reruns them.

## Summary

1. **The parity/speed trade-off in the engine is small.** The three most
   expensive pieces of fidelity that can be left out cost 16% of the
   engine's instructions together: about 1.2 times faster in Rust. In
   return, play measurably moves away from Showdown, mostly in a few
   situations: one matchup's win rate moved by 29 points.
2. **Engine speed is a small part of a training loop.** In a Python
   training loop the engine is 3–5% of the time, and the rest is the
   interface (JSON requests, choice enumeration, observations) and the
   agent. Even a 1.2 times faster engine makes such a loop at most 1% faster.
3. **The large wins are elsewhere, at full parity.** A native interface (choices
   listed in Rust, observations as floats) makes the same loop 9.5 times
   faster. Showdown to Ninjask is about 3.4 times end to end with the JSON
   interface and about 27 times with a native, batched one.
4. **For the agent trained here, parity didn't matter.** A policy trained on the approximate
   engine and evaluated on the exact one plays exactly as well (63.5%
   against a damage heuristic, both ways). The engine's approximations
   are invisible at this agent's strength. They may not be for a stronger
   agent that exploits the situations where play moved.

The one published comparison point, PokaiEngine (Yu, arXiv:2608.29197),
doesn't trade parity for speed either. Its "~99% parity" is the
out-of-sample bug rate of its first validation pass (6 of 500 turns, four bugs, since
fixed). As a plain simulator it is 26 times Showdown per turn (0.08 ms
against 2.0 ms on its own hardware), while Ninjask is about 200 times
(logging on) to 350 times (logging off) Showdown on this machine. Its
speed lever is algorithmic: Search mode enumerates a joint action's whole
outcome distribution in one pass, and capping it at 32 branches is 10
times faster for 0.5% of the probability mass. That cap is a
parity/speed knob, but for the outcome distribution a search uses, not
for the game's mechanics.

## 1. Where the time goes

**The engine** (`callgrind` on `examples/bench`, logging off): about
720,000 instructions per random game. Turn resolution is 75% of them and
move execution 36%. Damage is 8%, the per-action speed re-sort about 4%
and the `Update` event 5%. No single mechanic dominates, which is why
leaving any one out buys little.

**A Python self-play loop** (`python_loop_anatomy.py`, the README's
pattern with a `Tracker` observation per decision):

| Stage, per decision | Time | Share |
| --- | --- | --- |
| `Tracker.view()` + `json.loads` | 450 µs | 55% |
| Legal choices in Python (`ninjask.choices`) | 160 µs | 20% |
| `request()`: the engine builds request JSON | 99 µs | 12% |
| `json.loads(request)` | 39 µs | 5% |
| `choose()`: parse and simulate | 31 µs | 4% |
| Feeding the tracker | 23 µs | 3% |

That is 45 games/s, against 6,500 games/s for the same games played in
Rust. The engine step is under 5% of the loop.

**The same loop through a native interface** (`python_loop_native.py`:
`Battle.legal_choices()` and `Tracker.encode()`, a 250-float observation):
427 games/s with observations, 9.5 times the JSON loop, and 825 games/s
without. The first prototype listed the engine's own legal choices, which
matched `ninjask.choices` on 7,647 of 7,677 decisions
(`check_legal_choices.txt`): in the other 30 it left out a move the request
marked only as `maybeDisabled`, revealing a foe's unseen Imprison. The
released version lists choices from the player's view instead and matches
on 18,334 of 18,334 decisions, in order, including teams built around
Imprison and traps (`check_player_choices.txt`).

**Batched** (`bench_batch.py`, `ninjask.Batch`: 256 battles, every decision
observed, choices as indices): 713 games/s on one core and 1,380 on four
(`bench_batch.txt`), 29 times the JSON loop, with results identical at any
thread count and to battles played one by one (`check_batch.txt`). What is
left is mostly the engine with logging on and the trackers parsing
protocol lines.

**A neural policy** (`model_cost.py`, numpy, one thread: a 2 × 512 MLP over
the observation scoring 100 choice embeddings): 226 µs per decision
forward at batch 1, and 47 µs batched (256). Forward plus backward is about
three times that. The engine's own cost is about 7 µs per decision in Rust,
or 8–17 µs called from Python.

## 2. The engine's parity/speed frontier

`examples/approx_frontier.rs` (feature `approx`; the switches are in
`src/approx.rs`). Speed is instructions per game under callgrind
(`approx_callgrind.txt`); wall-clock medians of seven interleaved runs
agree. Game level is the first player's win rate in 150 matchups × 400
random-policy games, against an exact-vs-exact run with independent seeds
as the noise floor (mean |Δ| 0.025, max 0.110). Turn level is 400 states
sampled from real games, each with one fixed joint choice resolved 400
times per configuration. It reports the total-variation distance of the
next-state distributions (HP, status, fainting, boosts, items, field)
above the exact-vs-exact floor, and the share of states whose distance is
beyond that floor's 99th percentile.

| Left out | Instructions per game | Speed-up | Win rate: mean, max |Δ| | Excess TV per turn | States beyond noise |
| --- | --- | --- | --- | --- | --- |
| nothing (exact) | 718,585 | ×1.00 | 0.025, 0.110 (noise) | 0 | 1% (by definition) |
| per-action Speed re-sort (Gen 7 order) | 651,180 | ×1.10 | 0.028, 0.120 | 0.017 | 3.0% |
| random tie-break draws | 719,659 | ×1.00 | 0.026, 0.105 | 0.002 | 1.0% |
| `Update` event after actions and hits | 671,329 | ×1.07 | 0.031, **0.258** | 0.033 | 5.8% |
| all three | 601,916 | ×1.19 | 0.033, **0.290** | 0.052 | 7.2% |

Read it as follows:

- **Tie-break draws cost nothing.** Leaving them out saves nothing in fast mode (xoshiro draws
  are cheap), so there is no reason to give them up.
- **Both real savings shift play in particular situations, not on average.** The mean win-rate shift
  is barely above noise, but skipping `Update` moved one matchup by 26 points: berries, items and
  abilities that react to HP mid-turn fire later or not at all. An agent trained on such an engine
  could learn a line that doesn't work in the real game.
- **About 1.2 times is the scale of what the mechanics can give.** That figure comes from removing
  the hottest fidelity that is safe to remove. More would mean simplifying damage or the event
  system itself, where Showdown's behaviour lives, and each step would cost parity in the same
  concentrated way.

## 3. Does speed help training?

`train_reinforce.py` trains a softmax policy (13 features per slot action:
base power × effectiveness against the target, STAB, Protect, priority,
switch, Mega Evolution and so on) by REINFORCE, against a damage heuristic
that plays its best base-power × effectiveness choice 90% of the time.
Every 250 games the policy is evaluated greedily on 300 fixed games.
Three seeds per condition.

| Games trained | 0 | 250 | 500 | 1,000 | 2,000 | 4,000 |
| --- | --- | --- | --- | --- | --- | --- |
| Win rate against the heuristic (exact engine) | 27.7% | 56.3% | 63.0% | 66.7% | 60.8% | 64.2% |
| Trained on all three approximations, evaluated exact | 27.7% | 60.0% | 60.2% | 63.3% | 59.7% | 62.4% |

From 1,000 games on, both conditions average 63.5% (±0.4 standard
error); the approximations made no difference. A game took 24.7 ms. The
engine took **3.3%** of that, the interface 71% and the agent (Python
feature code) 24%.

This agent is deliberately small. It learns what it can in about 500
games, so it needs about 12 seconds and engine speed couldn't matter to it.
A strong agent needs orders of magnitude more data, and for it the
question is what share of each training step the engine is (Amdahl's law). Per
decision, from the measurements above:

| Pipeline | Engine | Interface | Policy (forward) | Total | Engine share | Gain from a 1.19× engine |
| --- | --- | --- | --- | --- | --- | --- |
| Showdown, JSON interface, MLP at batch 1 | 2,400 µs | 770 µs | 226 µs | 3,396 µs | 71% | — |
| Ninjask, JSON interface, MLP at batch 1 | 17 µs | 770 µs | 226 µs | 1,013 µs | 1.7% | 0.3% |
| Ninjask, native interface, MLP at batch 1 | 17 µs | 64 µs | 226 µs | 307 µs | 5.5% | 0.9% |
| Ninjask, native interface, MLP batched | 17 µs | 64 µs | 47 µs | 128 µs | 13% | 2.2% |
| Rust-side search or rollouts, no network | 7 µs | 0 | 0 | 7 µs | ~100% | 19% |

(Showdown's figure is its measured 174 turns/s at Ninjask's 2.39 decisions
per turn; the JSON interface is the same per-decision cost for either
simulator. Backward passes, which add about twice the forward cost while
training, make the engine's share smaller still.)

So, for training:

- **Moving from Showdown to Ninjask is the big speed-up:** about 3.4 times end to end with today's
  JSON interface, and about 27 times with a native, batched interface.
- **A native interface is worth 3.3–8 times** over today's JSON, at full parity.
- **Trading parity for engine speed is worth 0.3–2.2%** of a training run with a neural policy,
  and up to 19% only where the engine is the whole cost: search and rollouts in Rust. That is
  where engine speed matters. It is also where PokaiEngine invests, by enumerating outcomes
  instead of sampling them.

## Directions this suggests

- **A native observation and action interface:** done in this release
  (`Battle.legal_choices`, `Tracker.encode`, `ninjask.Batch`); it is the
  10–30× lever, at full parity. Next on it: feeding trackers from the
  engine's events instead of protocol text.
- **Exact outcome enumeration for search,** as PokaiEngine does, at
  Ninjask's parity: the speed lever that matters once the engine is the
  bottleneck.
- **Parallelism:** a `Battle` is plain data and games are independent, so
  throughput scales with cores. It is not measured here.
- **Approximations only as a measured option:** `approx_frontier.rs` is a
  harness for testing any proposed shortcut against the exact engine
  before anyone trains on it.

## Limits of these measurements

- The policy is linear and the opponent is a heuristic, so its learning
  curve shows the method, not how many games a strong agent needs.
- The neural-policy costs are numpy on one CPU thread; on a GPU the policy
  is cheaper per decision when batched, which raises the engine's share,
  though not past the native-interface rows' order of magnitude.
- The turn-level comparison uses the full next state, so its distance has
  a high noise floor (0.17) when a turn has many outcomes; the excess over
  that floor is what to compare.
- One machine, one pool of teams, random-policy games for the
  frontier.

## Rerunning

```sh
cargo run --release --features approx --example approx_frontier -- data/derived/format/gen9championsvgc2026regmc.json pools/vgcpastes-regmc.txt
python research/python_loop_anatomy.py pools/vgcpastes-regmc.txt 400
python research/python_loop_native.py pools/vgcpastes-regmc.txt 2000
python research/train_reinforce.py --pool pools/vgcpastes-regmc.txt --games 4000 --eval-every 250 --eval-games 300 --opponent heuristic --seed 0
```

Training on the approximate engine needs a build with the feature
(`maturin build --release --features python-module,approx`) and
`--train-approx 7`.
