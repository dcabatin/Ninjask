"""Cost of a small neural policy per decision on this CPU (numpy, one thread): an MLP over the
250-float Tracker.encode() observation (2 x 512 ReLU) and a 64-dim embedding per legal joint choice
(100 choices, a typical move request), scored by a dot product. Forward alone, and forward plus a
backward pass (approximated as 2x the forward FLOPs, the usual rule of thumb), at batch 1 and batch 256."""
import os, time
os.environ.setdefault("OMP_NUM_THREADS", "1")
import numpy as np
rng = np.random.default_rng(0)
D, H, E, C = 250, 512, 64, 100
W1, W2, W3 = rng.standard_normal((D, H)) * .05, rng.standard_normal((H, H)) * .05, rng.standard_normal((H, E)) * .05
Wc = rng.standard_normal((32, E)) * .1
def forward(x, ch):
    h = np.maximum(x @ W1, 0); h = np.maximum(h @ W2, 0); q = h @ W3           # (B, E)
    emb = ch @ Wc                                                              # (B, C, E)
    return np.einsum("be,bce->bc", q, emb)
for B in (1, 256):
    x = rng.standard_normal((B, D)).astype(np.float64); ch = rng.standard_normal((B, C, 32))
    forward(x, ch); n = 2000 if B == 1 else 40; t = time.perf_counter()
    for _ in range(n): forward(x, ch)
    per = (time.perf_counter() - t) / n / B
    print(f"batch {B:3d}: forward {1e6 * per:7.1f} us per decision; forward+backward ~{3e6 * per:7.1f} us per decision")
