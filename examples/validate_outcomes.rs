//! `Battle::enumerate_outcomes` on real states: for states sampled from random games on a team
//! pool, with random legal joint choices, compare the enumerated outcome distribution with N
//! sampled resolutions of the same turn (ordinary xoshiro play), and time both modes.
//! `cargo run --release --example validate_outcomes -- <dex.json> <teams.txt> [states] [samples] [max_paths]`
//!
//! Per state: Precise outcomes and time (or "over" when the turn has more than `max_paths` chance
//! paths, with the time per path), AlmostPrecise outcomes and time, sampled outcomes outside the
//! enumeration (any is a bug), the total-variation distance from the sample beside its noise
//! 99th percentile, and AlmostPrecise's distance from Precise on who has fainted.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Instant;

use ninjask::battle::Team;
use ninjask::dex::team::parse_packed;
use ninjask::rng::Rng;
use ninjask::state::{RequestState, SlotChoice};
use ninjask::{Battle, BattleStateKey, Dex, EnumerationMode};

/// Which Pokémon have fainted, side by side: the coarse outcome both modes are compared on.
fn faints(b: &Battle) -> Vec<bool> {
    b.sides
        .iter()
        .flat_map(|s| s.mons.iter().map(|m| m.fainted || m.hp == 0))
        .collect()
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
    let states: usize = args.get(3).and_then(|x| x.parse().ok()).unwrap_or(200);
    let samples: u64 = args.get(4).and_then(|x| x.parse().ok()).unwrap_or(4000);
    let max_paths: u64 = args
        .get(5)
        .and_then(|x| x.parse().ok())
        .unwrap_or(ninjask::DEFAULT_MAX_PATHS);
    let mut rng = Rng::new(2026);
    let mut g = 0usize;
    let (mut outside, mut checked, mut flagged, mut over) = (0u64, 0usize, 0usize, 0usize);
    let (mut almost_ms, mut almost_over) = (Vec::new(), 0usize);
    let mut rows: Vec<(f64, f64, usize, usize, f64, f64)> = Vec::new();
    println!("state turn outcomes_precise ms_precise outcomes_almost ms_almost ms_{samples}_samples sampled_outside_support tv tv_noise_p99 faint_tv_almost_vs_precise");
    while checked < states {
        g += 1;
        let (i, j) = ((g * 13) % teams.len(), (g * 29 + 5) % teams.len());
        let mut b = Battle::from_teams(dex.clone(), [&teams[i], &teams[j]], ["a", "b"], g as u64);
        b.log_enabled = false;
        for s in 0..2 {
            let n = b.sides[s].mons.len();
            let mut order: Vec<usize> = (0..n).collect();
            rng.shuffle(&mut order, 0, n);
            b.choose_team(s, order);
        }
        let stop = 1 + rng.random(8);
        while !b.ended && b.turn < 100 {
            if b.request == RequestState::Move
                && b.turn >= stop
                && b.side_needs_choice(0)
                && b.side_needs_choice(1)
            {
                break;
            }
            for s in 0..2 {
                if b.request != RequestState::Ended && b.side_needs_choice(s) {
                    let c = b.sample_joint_choice(s, &mut rng);
                    b.choose_structured(s, c);
                }
            }
        }
        if b.ended
            || b.request != RequestState::Move
            || !(b.side_needs_choice(0) && b.side_needs_choice(1))
        {
            continue;
        }
        let c: [Vec<SlotChoice>; 2] = [
            b.sample_joint_choice(0, &mut rng),
            b.sample_joint_choice(1, &mut rng),
        ];

        let t = Instant::now();
        let almost =
            b.enumerate_outcomes_within(&c[0], &c[1], EnumerationMode::AlmostPrecise, max_paths);
        let ms_a = t.elapsed().as_secs_f64() * 1e3;
        almost_ms.push(ms_a);
        almost_over += almost.is_err() as usize;
        let t = Instant::now();
        let precise =
            b.enumerate_outcomes_within(&c[0], &c[1], EnumerationMode::Precise, max_paths);
        let ms_p = t.elapsed().as_secs_f64() * 1e3;
        let almost_n = almost
            .as_ref()
            .map_or("over".to_string(), |a| a.len().to_string());
        let Ok(precise) = precise else {
            println!(
                "{checked} {} over {ms_p:.0} {almost_n} {ms_a:.2} - - - - - us_per_path {:.1}",
                b.turn,
                1e3 * ms_p / max_paths as f64
            );
            over += 1;
            checked += 1;
            continue;
        };
        let mass: f64 = precise.iter().map(|o| o.probability).sum();
        assert!((mass - 1.0).abs() < 1e-9, "probabilities sum to {mass}");

        // Sampled resolutions of the same turn.
        let t = Instant::now();
        let mut counts: HashMap<BattleStateKey, u64> = HashMap::new();
        for seed in 0..samples {
            let mut x = b.clone();
            x.rng = Rng::new(1_000_000 + seed);
            x.choose_structured(0, c[0].clone());
            x.choose_structured(1, c[1].clone());
            *counts.entry(x.state_key()).or_insert(0) += 1;
        }
        let ms_s = t.elapsed().as_secs_f64() * 1e3;
        let probs: HashMap<BattleStateKey, f64> = precise
            .iter()
            .map(|o| (o.battle.state_key(), o.probability))
            .collect();
        let out_here: u64 = counts
            .iter()
            .filter(|(k, _)| !probs.contains_key(*k))
            .map(|(_, v)| v)
            .sum();
        outside += out_here;
        let tv = 0.5
            * (probs
                .iter()
                .map(|(k, p)| (p - *counts.get(k).unwrap_or(&0) as f64 / samples as f64).abs())
                .sum::<f64>()
                + out_here as f64 / samples as f64);
        // Noise: the TV of `samples` draws from the enumerated distribution itself, 200 times.
        let p: Vec<f64> = precise.iter().map(|o| o.probability).collect();
        let mut cdf = Vec::with_capacity(p.len());
        let mut acc = 0.0;
        for x in &p {
            acc += x;
            cdf.push(acc);
        }
        let mut noise: Vec<f64> = (0..200)
            .map(|_| {
                let mut h = vec![0u64; p.len()];
                for _ in 0..samples {
                    let u = (rng.random(1 << 30) as f64 + 0.5) / (1u64 << 30) as f64;
                    let k = cdf.partition_point(|&c| c < u).min(p.len() - 1);
                    h[k] += 1;
                }
                0.5 * p
                    .iter()
                    .zip(&h)
                    .map(|(q, &n)| (q - n as f64 / samples as f64).abs())
                    .sum::<f64>()
            })
            .collect();
        noise.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let p99 = noise[198];
        if tv > p99 {
            flagged += 1;
        }
        // AlmostPrecise against Precise on who has fainted.
        let mut fp: HashMap<Vec<bool>, f64> = HashMap::new();
        for o in &precise {
            *fp.entry(faints(&o.battle)).or_insert(0.0) += o.probability;
        }
        let mut fa: HashMap<Vec<bool>, f64> = HashMap::new();
        for o in almost.iter().flatten() {
            *fa.entry(faints(&o.battle)).or_insert(0.0) += o.probability;
        }
        let mut keys: Vec<&Vec<bool>> = fp.keys().chain(fa.keys()).collect();
        keys.sort();
        keys.dedup();
        let ftv = 0.5
            * keys
                .iter()
                .map(|k| (fp.get(*k).unwrap_or(&0.0) - fa.get(*k).unwrap_or(&0.0)).abs())
                .sum::<f64>();
        println!(
            "{checked} {} {} {ms_p:.2} {almost_n} {ms_a:.2} {ms_s:.1} {out_here} {tv:.4} {p99:.4} {ftv:.4}",
            b.turn,
            precise.len(),
        );
        rows.push((ms_p, ms_a, precise.len(), 0, ftv, ms_s));
        checked += 1;
    }
    let med = |mut v: Vec<f64>| {
        v.sort_by(|a, b| a.partial_cmp(b).unwrap());
        (v[v.len() / 2], v[(v.len() * 9) / 10], v[v.len() - 1])
    };
    let (pm, p90, pmax) = med(rows.iter().map(|r| r.0).collect());
    let (am, a90, amax) = med(almost_ms.clone());
    let (om, o90, omax) = med(rows.iter().map(|r| r.2 as f64).collect());
    let (sm, _, _) = med(rows.iter().map(|r| r.5).collect());
    let (fm, f90, fmax) = med(rows.iter().map(|r| r.4).collect());
    println!("\n== {states} states, {samples} sampled resolutions each, max_paths {max_paths}");
    println!("Precise over the budget: {over} of {states}");
    println!(
        "sampled outcomes outside the enumerated support: {outside} of {}",
        rows.len() as u64 * samples
    );
    println!("states whose sampled distribution is farther from the enumeration than the noise 99th percentile: {flagged} of {} (expected about {:.1})", rows.len(), rows.len() as f64 * 0.01);
    println!("Precise: ms per enumeration median {pm:.2}, p90 {p90:.2}, max {pmax:.2}; outcomes median {om:.0}, p90 {o90:.0}, max {omax:.0}");
    println!("AlmostPrecise (all {states} states): ms median {am:.2}, p90 {a90:.2}, max {amax:.2}; over the budget {almost_over}");
    println!("{samples} sampled resolutions: median {sm:.1} ms");
    println!(
        "AlmostPrecise vs Precise, TV of who-faints: median {fm:.4}, p90 {f90:.4}, max {fmax:.4}"
    );
}
