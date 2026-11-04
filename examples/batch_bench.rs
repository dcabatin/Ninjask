//! Throughput of `ninjask::batch::Batch` without Python: N battles, a random
//! policy, finished battles restarted until GAMES games have finished; every
//! decision is observed (observation, ids, legal choices) as in training.
//! `cargo run --release --example batch_bench -- <dex.json> <teams.txt> [N] [GAMES]`;
//! set RAYON_NUM_THREADS to vary the thread count.

use std::sync::Arc;
use std::time::Instant;

use ninjask::batch::Batch;
use ninjask::rng::Rng;
use ninjask::Dex;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let dex = Arc::new(Dex::from_json(&std::fs::read_to_string(&args[1]).unwrap()).unwrap());
    let pool: Vec<String> = std::fs::read_to_string(&args[2])
        .unwrap()
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(String::from)
        .collect();
    let n: usize = args.get(3).and_then(|x| x.parse().ok()).unwrap_or(256);
    let games: usize = args.get(4).and_then(|x| x.parse().ok()).unwrap_or(10_000);
    let mut batch = Batch::new(dex, n);
    let mut started = 0usize;
    let start = |b: &mut Batch, i: usize, started: &mut usize| {
        let (a, c) = (
            &pool[(7 * *started) % pool.len()],
            &pool[(7 * *started + 3) % pool.len()],
        );
        b.reset(i, [a, c], *started as u64).unwrap();
        *started += 1;
    };
    for i in 0..n {
        start(&mut batch, i, &mut started);
    }
    let mut rng = Rng::new(1);
    let (mut finished, mut decisions) = (0usize, 0usize);
    let (mut t_obs, mut t_step, mut t_reset) = (0.0, 0.0, 0.0);
    let t0 = Instant::now();
    while finished < games {
        let t = Instant::now();
        let o = batch.observe();
        t_obs += t.elapsed().as_secs_f64();
        let m = o.offsets.len() - 1;
        decisions += m;
        let picks: Vec<(usize, usize, usize)> = (0..m)
            .map(|k| {
                let count = (o.offsets[k + 1] - o.offsets[k]) as u32;
                (
                    o.who[2 * k] as usize,
                    o.who[2 * k + 1] as usize,
                    rng.random(count) as usize,
                )
            })
            .collect();
        let t = Instant::now();
        batch.step(&picks).unwrap();
        t_step += t.elapsed().as_secs_f64();
        let t = Instant::now();
        for i in 0..n {
            if batch.done(i) {
                finished += 1;
                start(&mut batch, i, &mut started);
            }
        }
        t_reset += t.elapsed().as_secs_f64();
    }
    let secs = t0.elapsed().as_secs_f64();
    println!(
        "threads {}: {finished} games, {decisions} decisions in {secs:.2}s: {:.0} games/s, {:.0} decisions/s (observe {:.0}%, step {:.0}%, reset {:.0}%)",
        rayon::current_num_threads(),
        finished as f64 / secs,
        decisions as f64 / secs,
        100.0 * t_obs / secs,
        100.0 * t_step / secs,
        100.0 * t_reset / secs
    );
}
