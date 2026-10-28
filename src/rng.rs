//! Deterministic pseudo-random numbers, with the helpers Showdown's battle
//! code uses: `random(n)`, `random_range(a, b)`, `chance(num, den)`, `sample`
//! and partial `shuffle`.
//!
//! Two generators sit behind them:
//! - xoshiro256++, fast, for self-play and search (fast mode);
//! - Showdown's own `SodiumRNG` (ChaCha20, as libsodium's
//!   `randombytes_buf_deterministic`), so that a battle seeded like Showdown's
//!   draws the same numbers (reference mode).
//!
//! Both produce 32-bit draws that the helpers scale exactly as Showdown's
//! `PRNG.random` does, so a helper's result depends only on the draw.

#[derive(Clone, Debug)]
pub struct Rng {
    gen: Gen,
    /// When on, every draw is recorded with its call site (for comparing the
    /// order of draws with Showdown's).
    pub trace: Option<Vec<String>>,
}

#[derive(Clone, Debug)]
enum Gen {
    Xoshiro([u64; 4]),
    /// Showdown's sodium seed: a 32-byte ChaCha20 key, replaced at each draw.
    Sodium([u8; 32]),
}

fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Nonce of libsodium's deterministic random bytes ("LibsodiumDRG").
const SODIUM_NONCE: &[u8; 12] = b"LibsodiumDRG";

fn quarter(s: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize) {
    s[a] = s[a].wrapping_add(s[b]);
    s[d] = (s[d] ^ s[a]).rotate_left(16);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_left(12);
    s[a] = s[a].wrapping_add(s[b]);
    s[d] = (s[d] ^ s[a]).rotate_left(8);
    s[c] = s[c].wrapping_add(s[d]);
    s[b] = (s[b] ^ s[c]).rotate_left(7);
}

/// The first 64-byte ChaCha20 (RFC 8439) keystream block for this key, the
/// sodium nonce and block counter 0.
fn chacha20_block(key: &[u8; 32]) -> [u8; 64] {
    let mut init = [0u32; 16];
    init[..4].copy_from_slice(&[0x6170_7865, 0x3320_646e, 0x7962_2d32, 0x6b20_6574]);
    for i in 0..8 {
        init[4 + i] = u32::from_le_bytes(key[4 * i..4 * i + 4].try_into().unwrap());
    }
    init[12] = 0;
    for i in 0..3 {
        init[13 + i] = u32::from_le_bytes(SODIUM_NONCE[4 * i..4 * i + 4].try_into().unwrap());
    }
    let mut s = init;
    for _ in 0..10 {
        quarter(&mut s, 0, 4, 8, 12);
        quarter(&mut s, 1, 5, 9, 13);
        quarter(&mut s, 2, 6, 10, 14);
        quarter(&mut s, 3, 7, 11, 15);
        quarter(&mut s, 0, 5, 10, 15);
        quarter(&mut s, 1, 6, 11, 12);
        quarter(&mut s, 2, 7, 8, 13);
        quarter(&mut s, 3, 4, 9, 14);
    }
    let mut out = [0u8; 64];
    for i in 0..16 {
        out[4 * i..4 * i + 4].copy_from_slice(&s[i].wrapping_add(init[i]).to_le_bytes());
    }
    out
}

impl Rng {
    /// Fast mode: xoshiro256++ from a 64-bit seed.
    pub fn new(seed: u64) -> Rng {
        let mut st = seed;
        let state = [
            splitmix64(&mut st),
            splitmix64(&mut st),
            splitmix64(&mut st),
            splitmix64(&mut st),
        ];
        Rng {
            gen: Gen::Xoshiro(state),
            trace: None,
        }
    }

    /// Reference mode: Showdown's PRNG for a seed like "sodium,0123abcd...".
    pub fn from_showdown_seed(seed: &str) -> Result<Rng, String> {
        let hex = seed
            .strip_prefix("sodium,")
            .ok_or_else(|| format!("not a sodium seed: {seed}"))?;
        if hex.len() > 64 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
            return Err(format!("bad sodium seed: {seed}"));
        }
        // Showdown pads the hex to 32 bytes with zeros.
        let padded = format!("{hex:0<64}");
        let mut key = [0u8; 32];
        for (i, b) in key.iter_mut().enumerate() {
            *b = u8::from_str_radix(&padded[2 * i..2 * i + 2], 16).unwrap();
        }
        Ok(Rng {
            gen: Gen::Sodium(key),
            trace: None,
        })
    }

    /// The seed as Showdown prints it (`battle.prng.getSeed()`), in reference mode.
    pub fn showdown_seed(&self) -> Option<String> {
        match &self.gen {
            Gen::Sodium(key) => Some(format!(
                "sodium,{}",
                key.iter().map(|b| format!("{b:02x}")).collect::<String>()
            )),
            Gen::Xoshiro(_) => None,
        }
    }

    fn next_xoshiro(s: &mut [u64; 4]) -> u64 {
        let result = (s[0].wrapping_add(s[3])).rotate_left(23).wrapping_add(s[0]);
        let t = s[1] << 17;
        s[2] ^= s[0];
        s[3] ^= s[1];
        s[1] ^= s[2];
        s[0] ^= s[3];
        s[2] ^= t;
        s[3] = s[3].rotate_left(45);
        result
    }

    /// A 32-bit draw (Showdown's `rng.next()`).
    pub fn next_u32(&mut self) -> u32 {
        match &mut self.gen {
            Gen::Xoshiro(s) => (Rng::next_xoshiro(s) >> 32) as u32,
            Gen::Sodium(key) => {
                let block = chacha20_block(key);
                key.copy_from_slice(&block[..32]);
                u32::from_be_bytes(block[32..36].try_into().unwrap())
            }
        }
    }

    /// 64 random bits, for seeding (not used by battle code).
    pub fn next_u64(&mut self) -> u64 {
        match &mut self.gen {
            Gen::Xoshiro(s) => Rng::next_xoshiro(s),
            Gen::Sodium(_) => ((self.next_u32() as u64) << 32) | self.next_u32() as u64,
        }
    }

    /// Uniform integer in `[0, n)`: Showdown's `random(n)`, floor(draw * n / 2^32).
    #[track_caller]
    pub fn random(&mut self, n: u32) -> u32 {
        debug_assert!(n > 0);
        let r = ((self.next_u32() as u64 * n as u64) >> 32) as u32;
        if let Some(t) = &mut self.trace {
            let at = std::panic::Location::caller();
            t.push(format!("random({n}) = {r} @ {}:{}", at.file(), at.line()));
        }
        r
    }

    /// Uniform integer in `[lo, hi)`, like Showdown's `random(lo, hi)`.
    #[track_caller]
    pub fn random_range(&mut self, lo: u32, hi: u32) -> u32 {
        lo + self.random(hi - lo)
    }

    /// True with probability `num / den`, like `randomChance`.
    #[track_caller]
    pub fn chance(&mut self, num: u32, den: u32) -> bool {
        self.random(den) < num
    }

    #[track_caller]
    pub fn sample<T: Copy>(&mut self, items: &[T]) -> T {
        items[self.random(items.len() as u32) as usize]
    }

    /// Fisher–Yates shuffle of `items[start..end]`, like Showdown's `shuffle`.
    #[track_caller]
    pub fn shuffle<T>(&mut self, items: &mut [T], start: usize, end: usize) {
        let mut start = start;
        while start < end - 1 {
            let next = self.random_range(start as u32, end as u32) as usize;
            if start != next {
                items.swap(start, next);
            }
            start += 1;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chacha20_matches_rfc8439_keystream() {
        // RFC 8439 2.4.2 uses another nonce; check the block function on the
        // all-zero key and nonce (test vector 1 of A.1) through a local copy.
        let key = [0u8; 32];
        let mut init = [0u32; 16];
        init[..4].copy_from_slice(&[0x6170_7865, 0x3320_646e, 0x7962_2d32, 0x6b20_6574]);
        let mut s = init;
        for _ in 0..10 {
            quarter(&mut s, 0, 4, 8, 12);
            quarter(&mut s, 1, 5, 9, 13);
            quarter(&mut s, 2, 6, 10, 14);
            quarter(&mut s, 3, 7, 11, 15);
            quarter(&mut s, 0, 5, 10, 15);
            quarter(&mut s, 1, 6, 11, 12);
            quarter(&mut s, 2, 7, 8, 13);
            quarter(&mut s, 3, 4, 9, 14);
        }
        let first = s[0].wrapping_add(init[0]).to_le_bytes();
        assert_eq!(first, [0x76, 0xb8, 0xe0, 0xad]);
        let _ = key;
    }

    #[test]
    fn sodium_matches_showdown() {
        // Showdown: new PRNG('sodium,0123456789abcdef0123456789abcdef').rng.next() x 5.
        let mut r = Rng::from_showdown_seed("sodium,0123456789abcdef0123456789abcdef").unwrap();
        let draws: Vec<u32> = (0..5).map(|_| r.next_u32()).collect();
        assert_eq!(
            draws,
            [1564598223, 2628916691, 3021397871, 1916167142, 4157841405]
        );
        assert_eq!(
            r.showdown_seed().unwrap(),
            "sodium,51b3d614ec21851a6419bc898430b29563817b1ec4fa00ea8c2cf63b160efc26"
        );
        // random(16), random(100), random(2, 5), randomChance(1, 3), random(6).
        let mut q = Rng::from_showdown_seed("sodium,0123456789abcdef0123456789abcdef").unwrap();
        assert_eq!(
            (
                q.random(16),
                q.random(100),
                q.random_range(2, 5),
                q.chance(1, 3),
                q.random(6)
            ),
            (5, 61, 4, false, 5)
        );
    }

    #[test]
    fn showdown_seed_round_trips() {
        let r = Rng::from_showdown_seed("sodium,0123456789abcdef0123456789abcdef").unwrap();
        assert_eq!(
            r.showdown_seed().unwrap(),
            "sodium,0123456789abcdef0123456789abcdef00000000000000000000000000000000"
        );
    }
}
