//! Research switches that trade parity with Showdown for speed (feature
//! `approx`, off by default). Each one leaves out a piece of fidelity that
//! costs time on every turn; `examples/approx_frontier.rs` measures how much
//! time each saves and how far play then moves from the exact engine.
//!
//! Without the feature, [`on`] is a constant `false` and every switch
//! compiles away, so the default build, its fingerprint and its parity are
//! untouched. The switches are process-wide (an atomic), which keeps
//! `Battle` unchanged; set them before playing.

/// Sort the turn's actions once when it starts (Gen 7): no re-sort of the
/// remaining moves by current Speed after each action.
pub const STATIC_ORDER: u32 = 1;
/// Break Speed ties in every sort by list order instead of a random draw.
pub const NO_TIE_DRAWS: u32 = 2;
/// Skip the `Update` event after actions and hits (berries, abilities and
/// items that react to HP or status then wait for the next one that runs).
pub const SKIP_UPDATE: u32 = 4;

#[cfg(feature = "approx")]
static FLAGS: std::sync::atomic::AtomicU32 = std::sync::atomic::AtomicU32::new(0);

/// Whether switch `flag` is on.
#[inline(always)]
pub fn on(flag: u32) -> bool {
    #[cfg(feature = "approx")]
    {
        FLAGS.load(std::sync::atomic::Ordering::Relaxed) & flag != 0
    }
    #[cfg(not(feature = "approx"))]
    {
        let _ = flag;
        false
    }
}

/// Set the switches (a bitwise or of the constants above; 0 is exact play).
/// Without the feature this does nothing.
pub fn set(flags: u32) {
    #[cfg(feature = "approx")]
    FLAGS.store(flags, std::sync::atomic::Ordering::Relaxed);
    #[cfg(not(feature = "approx"))]
    let _ = flags;
}
