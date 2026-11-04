"""Throughput of ninjask.Batch: N battles, a uniformly random policy in numpy, finished battles reset with
new teams until GAMES games have finished. Every decision gets its observation, ids and legal actions
(the full training interface). Set RAYON_NUM_THREADS to vary the thread count. Prints a digest of all
results, which must not depend on the thread count."""
import hashlib, os, sys, time
import numpy as np
import ninjask
d = ninjask.dex("gen9championsvgc2026regmc")
pool = [l for l in open(sys.argv[1]).read().split("\n") if l]
N, GAMES = int(sys.argv[2]), int(sys.argv[3])
rng = np.random.default_rng(0)
b = ninjask.Batch(d, N); started = 0
def start(i):
    global started
    b.reset(i, pool[(7 * started) % len(pool)], pool[(7 * started + 3) % len(pool)], started); started += 1
for i in range(N): start(i)
finished = decisions = 0; h = hashlib.sha256(); t0 = time.perf_counter()
while finished < GAMES:
    o = b.observe()
    off = np.frombuffer(o["offsets"], np.uint32).astype(np.int64)
    counts = off[1:] - off[:-1]
    picks = (rng.random(len(counts)) * counts).astype(np.int64)
    decisions += o["n"]
    b.step(picks.tolist())
    done = np.flatnonzero(np.frombuffer(b.dones(), np.uint8))
    for i in done:
        h.update(f"{i}:{b.winner(i)}:{b.turn(i)};".encode()); finished += 1
    if len(done):
        k = range(started, started + len(done)); started += len(done)
        b.reset_many(done.tolist(), [pool[(7 * j) % len(pool)] for j in k], [pool[(7 * j + 3) % len(pool)] for j in k], list(k))
wall = time.perf_counter() - t0
print(f"threads {os.environ.get('RAYON_NUM_THREADS', 'all')}, {N} battles: {finished} games, {decisions} decisions in {wall:.2f} s = {finished / wall:.0f} games/s, {decisions / wall:.0f} decisions/s; digest {h.hexdigest()[:16]}")
