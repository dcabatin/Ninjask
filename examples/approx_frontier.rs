//! How much speed each research approximation (`ninjask::approx`) buys and
//! how far it moves play from the exact engine:
//! `cargo run --release --features approx --example approx_frontier -- <dex.json> <teams.txt>`.
//!
//! - Speed: random-policy games per second (logging off), the median of
//!   interleaved repetitions. `--bench FLAGS GAMES` plays one configuration
//!   only (for callgrind).
//! - Game level: for fixed matchups, the first player's win rate over many
//!   seeds; the mean absolute difference from the exact engine, beside the
//!   same statistic between two independent exact runs (the noise floor).
//! - Turn level: states sampled from exact games, each with one fixed joint
//!   choice per side, resolved many times under each configuration; the
//!   total-variation distance between the next-state distributions (HP,
//!   status, fainting, boosts, items, field), beside exact against exact.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::sync::Arc;
use std::time::Instant;

use ninjask::approx;
use ninjask::battle::Team;
use ninjask::dex::team::parse_packed;
use ninjask::rng::Rng;
use ninjask::state::RequestState;
use ninjask::{Battle, Dex};

const CONFIGS: [(&str, u32); 5] = [
    ("exact", 0),
    ("static_order", approx::STATIC_ORDER),
    ("no_tie_draws", approx::NO_TIE_DRAWS),
    ("skip_update", approx::SKIP_UPDATE),
    (
        "all_three",
        approx::STATIC_ORDER | approx::NO_TIE_DRAWS | approx::SKIP_UPDATE,
    ),
];

fn new_battle(
    dex: &Arc<Dex>,
    teams: &[Team],
    i: usize,
    j: usize,
    seed: u64,
    rng: &mut Rng,
) -> Battle {
    let mut b = Battle::from_teams(
        dex.clone(),
        [&teams[i], &teams[j]],
        ["Player 1", "Player 2"],
        seed,
    );
    b.log_enabled = false;
    for s in 0..2 {
        let n = b.sides[s].mons.len();
        let mut order: Vec<usize> = (0..n).collect();
        rng.shuffle(&mut order, 0, n);
        b.choose_team(s, order);
    }
    b
}

/// One random-policy game: (winner, turns).
fn play(dex: &Arc<Dex>, teams: &[Team], i: usize, j: usize, seed: u64) -> (Option<usize>, u32) {
    let mut rng = Rng::new(seed ^ 0x9e37_79b9_7f4a_7c15);
    let mut b = new_battle(dex, teams, i, j, seed, &mut rng);
    while !b.ended && b.turn < 150 {
        for s in 0..2 {
            if b.request == RequestState::Ended || !b.side_needs_choice(s) {
                continue;
            }
            let c = b.sample_joint_choice(s, &mut rng);
            b.choose_structured(s, c);
        }
    }
    (b.winner, b.turn)
}

fn games_per_second(dex: &Arc<Dex>, teams: &[Team], flags: u32, games: usize) -> f64 {
    approx::set(flags);
    let start = Instant::now();
    for g in 0..games {
        play(
            dex,
            teams,
            g % teams.len(),
            (g * 7 + 3) % teams.len(),
            g as u64,
        );
    }
    approx::set(0);
    games as f64 / start.elapsed().as_secs_f64()
}

/// What a turn's outcome is compared on.
fn signature(b: &Battle) -> u64 {
    let mut h = std::collections::hash_map::DefaultHasher::new();
    for side in &b.sides {
        for m in &side.mons {
            (
                m.hp,
                m.status as u8,
                m.fainted,
                m.boosts,
                m.item,
                m.is_active,
            )
                .hash(&mut h);
        }
    }
    (
        b.field.weather.map(|w| w as u8),
        b.field.terrain.map(|t| t as u8),
        b.field.trick_room,
    )
        .hash(&mut h);
    (b.ended, b.winner).hash(&mut h);
    h.finish()
}

fn distribution(
    state: &Battle,
    choices: &[Vec<ninjask::state::SlotChoice>; 2],
    flags: u32,
    seeds: std::ops::Range<u64>,
) -> HashMap<u64, u32> {
    approx::set(flags);
    let mut d = HashMap::new();
    for seed in seeds {
        let mut b = state.clone();
        b.rng = Rng::new(seed);
        for (s, choice) in choices.iter().enumerate() {
            if b.side_needs_choice(s) {
                b.choose_structured(s, choice.clone());
            }
        }
        *d.entry(signature(&b)).or_insert(0) += 1;
    }
    approx::set(0);
    d
}

fn tv(a: &HashMap<u64, u32>, b: &HashMap<u64, u32>) -> f64 {
    let (na, nb) = (
        a.values().sum::<u32>() as f64,
        b.values().sum::<u32>() as f64,
    );
    let mut keys: Vec<&u64> = a.keys().chain(b.keys()).collect();
    keys.sort();
    keys.dedup();
    0.5 * keys
        .into_iter()
        .map(|k| (*a.get(k).unwrap_or(&0) as f64 / na - *b.get(k).unwrap_or(&0) as f64 / nb).abs())
        .sum::<f64>()
}

fn median(mut v: Vec<f64>) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[v.len() / 2]
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dex = Arc::new(Dex::from_json(&std::fs::read_to_string(&args[1]).unwrap()).unwrap());
    let teams: Vec<Team> = std::fs::read_to_string(&args[2])
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|l| Team::new(&dex, &parse_packed(&dex, l).unwrap()))
        .collect();
    if let Some(p) = args.iter().position(|a| a == "--bench") {
        let flags: u32 = args[p + 1].parse().unwrap();
        let games: usize = args[p + 2].parse().unwrap();
        println!(
            "flags {flags}: {:.0} games/s",
            games_per_second(&dex, &teams, flags, games)
        );
        return;
    }

    // Speed: interleaved repetitions, so drift on a shared host hits every configuration alike.
    let reps = 7;
    let mut speed: Vec<Vec<f64>> = vec![Vec::new(); CONFIGS.len()];
    for _ in 0..reps {
        for (k, &(_, flags)) in CONFIGS.iter().enumerate() {
            speed[k].push(games_per_second(&dex, &teams, flags, 6000));
        }
    }
    let exact_speed = median(speed[0].clone());
    println!("== speed: random-policy games/s, logging off, median of {reps} interleaved runs of 6000 games");
    for (k, (name, _)) in CONFIGS.iter().enumerate() {
        let m = median(speed[k].clone());
        let (lo, hi) = (
            speed[k].iter().cloned().fold(f64::MAX, f64::min),
            speed[k].iter().cloned().fold(0.0, f64::max),
        );
        println!(
            "  {name:13} {m:7.0}  [{lo:.0}-{hi:.0}]  x{:.3}",
            m / exact_speed
        );
    }

    // Game level: matchup win rates.
    let (matchups, per) = (150usize, 400u64);
    let pairs: Vec<(usize, usize)> = (0..matchups)
        .map(|m| ((m * 37) % teams.len(), (m * 101 + 17) % teams.len()))
        .collect();
    let win_rates = |flags: u32, base: u64| -> (Vec<f64>, f64) {
        approx::set(flags);
        let mut turns = 0u64;
        let rates = pairs
            .iter()
            .enumerate()
            .map(|(m, &(i, j))| {
                let mut wins = 0.0;
                for g in 0..per {
                    let (w, t) = play(&dex, &teams, i, j, base + (m as u64) * 100_000 + g);
                    turns += t as u64;
                    wins += match w {
                        Some(0) => 1.0,
                        None => 0.5,
                        _ => 0.0,
                    };
                }
                wins / per as f64
            })
            .collect();
        approx::set(0);
        (rates, turns as f64 / (matchups as u64 * per) as f64)
    };
    let mad = |a: &[f64], b: &[f64]| {
        a.iter().zip(b).map(|(x, y)| (x - y).abs()).sum::<f64>() / a.len() as f64
    };
    let max_ad = |a: &[f64], b: &[f64]| {
        a.iter()
            .zip(b)
            .map(|(x, y)| (x - y).abs())
            .fold(0.0, f64::max)
    };
    let (exact_rates, exact_turns) = win_rates(0, 0);
    let (noise_rates, noise_turns) = win_rates(0, 1 << 40);
    println!("\n== game level: p1 win rate over {matchups} matchups x {per} random-policy games (independent seeds per run)");
    println!(
        "  {:13} mean |dWR| {:.4}  max {:.4}  mean turns {:.2}  (noise floor: exact vs exact)",
        "exact again",
        mad(&exact_rates, &noise_rates),
        max_ad(&exact_rates, &noise_rates),
        noise_turns
    );
    for &(name, flags) in &CONFIGS[1..] {
        let (r, t) = win_rates(flags, 2 << 40);
        println!(
            "  {name:13} mean |dWR| {:.4}  max {:.4}  mean turns {t:.2}  (exact {exact_turns:.2})",
            mad(&exact_rates, &r),
            max_ad(&exact_rates, &r)
        );
    }

    // Turn level: next-state distributions from sampled states.
    let (states, trials) = (400usize, 400u64);
    let mut picked: Vec<(Battle, [Vec<ninjask::state::SlotChoice>; 2])> = Vec::new();
    let mut g = 0u64;
    while picked.len() < states {
        let mut rng = Rng::new(77 + g);
        let mut b = new_battle(
            &dex,
            &teams,
            (g as usize * 13) % teams.len(),
            (g as usize * 29 + 5) % teams.len(),
            5_000_000 + g,
            &mut rng,
        );
        let stop = 1 + rng.random(8);
        while !b.ended && b.turn < 150 {
            if b.request == RequestState::Move
                && b.turn >= stop
                && b.side_needs_choice(0)
                && b.side_needs_choice(1)
            {
                let c = [
                    b.sample_joint_choice(0, &mut rng),
                    b.sample_joint_choice(1, &mut rng),
                ];
                picked.push((b.clone(), c));
                break;
            }
            for s in 0..2 {
                if b.request == RequestState::Ended || !b.side_needs_choice(s) {
                    continue;
                }
                let c = b.sample_joint_choice(s, &mut rng);
                b.choose_structured(s, c);
            }
        }
        g += 1;
    }
    let base: Vec<(HashMap<u64, u32>, HashMap<u64, u32>)> = picked
        .iter()
        .map(|(b, c)| {
            (
                distribution(b, c, 0, 0..trials),
                distribution(b, c, 0, trials..2 * trials),
            )
        })
        .collect();
    let noise: Vec<f64> = base.iter().map(|(a, b)| tv(a, b)).collect();
    let mut sorted = noise.clone();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let q99 = sorted[(sorted.len() * 99) / 100];
    let distinct = base.iter().map(|(a, _)| a.len() as f64).sum::<f64>() / states as f64;
    println!("\n== turn level: {states} sampled states x {trials} resolutions per configuration; {distinct:.1} distinct outcomes per state on average");
    println!(
        "  {:13} mean TV {:.4}  (noise floor: exact vs exact; its 99th percentile {q99:.4})",
        "exact again",
        noise.iter().sum::<f64>() / states as f64
    );
    for &(name, flags) in &CONFIGS[1..] {
        let d: Vec<f64> = picked
            .iter()
            .zip(&base)
            .map(|((b, c), (e, _))| tv(e, &distribution(b, c, flags, trials..2 * trials)))
            .collect();
        let mean = d.iter().sum::<f64>() / states as f64;
        let over = d.iter().filter(|&&x| x > q99).count();
        println!(
            "  {name:13} mean TV {mean:.4}  excess {:.4}  states above the noise 99th percentile: {over} of {states} ({:.1}%)",
            mean - noise.iter().sum::<f64>() / states as f64,
            100.0 * over as f64 / states as f64
        );
    }
}
