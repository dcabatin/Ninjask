"""ninjask.Batch against playing each battle alone with Battle + Tracker: at every decision the same
observation, ids and legal actions, and in the end the same winner and turn count. Choices come from a
deterministic rule of (battle, side, decision number, number of choices), so both runs pick alike."""
import hashlib, sys
import numpy as np
import ninjask

FORMAT = "gen9championsvgc2026regmc"
d = ninjask.dex(FORMAT)
pool = [l for l in open(sys.argv[1]).read().split("\n") if l]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 64
teams = [(pool[(3 * i) % len(pool)], pool[(3 * i + 1) % len(pool)], 1000 + i) for i in range(N)]
rule = lambda i, s, k, n: (i * 7919 + s * 104729 + k * 15485863) % n

def digest(*parts):
    h = hashlib.sha256()
    for p in parts: h.update(p)
    return h.hexdigest()[:16]

# Batched.
batch = ninjask.Batch(d, N)
for i, (t1, t2, seed) in enumerate(teams): batch.reset(i, t1, t2, seed)
count = [[0, 0] for _ in range(N)]; trace_b = [[] for _ in range(N)]; refused_b = 0
while True:
    o = batch.observe()
    if o["n"] == 0: break
    who = np.frombuffer(o["who"], np.uint32).reshape(-1, 2); off = np.frombuffer(o["offsets"], np.uint32)
    obs = np.frombuffer(o["obs"], np.float32).reshape(-1, ninjask.OBS_LEN); ids = np.frombuffer(o["ids"], np.int16).reshape(-1, ninjask.IDS_LEN)
    acts = np.frombuffer(o["actions"], np.int8).reshape(-1, ninjask.ACTION_WIDTH)
    picks = []
    for k, (i, s) in enumerate(who):
        n = int(off[k + 1] - off[k]); picks.append(rule(int(i), int(s), count[i][s], n)); count[i][s] += 1
        trace_b[i].append(digest(obs[k].tobytes(), ids[k].tobytes(), acts[off[k]:off[k + 1]].tobytes()))
    flags = batch.step(picks); refused_b += sum(f & 1 for f in flags)
result_b = [(batch.winner(i), batch.turn(i)) for i in range(N)]

# One battle at a time.
trace_s = [[] for _ in range(N)]; result_s = []; refused_s = 0
for i, (t1, t2, seed) in enumerate(teams):
    b = ninjask.Battle(d, t1, t2, seed=seed); b.set_logging(True)
    trs = [ninjask.Tracker(d, "p1"), ninjask.Tracker(d, "p2")]; seen = [0, 0]; k = [0, 0]
    while not b.ended and b.turn < 150:
        sides = [s for s in (0, 1) if b.needs_choice(("p1", "p2")[s])]
        picks = []
        for s in sides:
            side = ("p1", "p2")[s]; lines = b.log(side, seen[s]); seen[s] += len(lines); trs[s].feed(lines); trs[s].feed_battle(b)
            preview = b.action_width(side) and len(b.legal_choices(side)[0].split()) == 2 and b.legal_choices(side)[0].startswith("team")
            raw = np.frombuffer(b.legal_actions(side), np.int8).reshape(-1, b.action_width(side))
            padded = np.zeros((raw.shape[0], ninjask.ACTION_WIDTH), np.int8); padded[:, :raw.shape[1]] = raw
            trace_s[i].append(digest(trs[s].encode(), trs[s].encode_ids(), padded.tobytes()))
            picks.append((side, rule(i, s, k[s], raw.shape[0]))); k[s] += 1
        for side, c in picks:
            try: b.choose_index(side, c)
            except ValueError: refused_s += 1
    result_s.append((b.winner_side, b.turn))

same_trace = sum(a == b for a, b in zip(trace_b, trace_s)); decisions = sum(map(len, trace_b))
print(f"battles {N}: decisions {decisions}; identical decision traces {same_trace}/{N}; identical (winner, turns) {sum(a == b for a, b in zip(result_b, result_s))}/{N}; refusals batch {refused_b}, alone {refused_s}")
