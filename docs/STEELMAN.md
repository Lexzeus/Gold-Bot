# /STEELMAN — Why a true BOS requires a candle BODY close, not a wick sweep

**Claim being steelmanned:** *A valid Break of Structure (BOS) or Change of
Character (ChoCh) must be confirmed by a candle **body close** beyond the swing
level. A wick that pierces the level and retraces is NOT a structural shift.*

**The strongest case for this rule:**

1. **A wick is unfinished business; a close is a settled verdict.** Intrabar, price
   visits many levels. Only the close represents where buyers and sellers agreed
   value should rest for that bar. A swing high taken by a wick means price
   *tried* to break and was rejected — that's the opposite of a break. Treating
   the wick as a BOS confuses an attempt with an outcome.

2. **Wicks beyond structure are the literal signature of a liquidity grab.** The
   stops resting above a swing high are the fuel for a reversal *down*. The clean
   pattern of a sweep is: spike through the level (collect stops) → immediate
   rejection → close back inside. If you call that a bullish BOS, you are buying
   exactly where smart money is selling into your stops. The body-close filter is
   precisely what separates "structure broke" from "structure was defended."

3. **It is the conservative error to make.** Of the two possible mistakes —
   (a) requiring a close and occasionally entering one bar late, vs. (b) accepting
   wicks and repeatedly getting trapped at the extremes — (b) is far more
   expensive on Gold, which is famous for long stop-hunt wicks at session opens.
   Demanding a close trades a little entry latency for a large reduction in false
   breaks. For a *swing* bot prioritizing high-confidence over frequency, that's
   the correct trade.

4. **It is unambiguous and machine-checkable.** "Did the body close past the
   level?" is a boolean with no discretion. "Was that wick meaningful?" is a
   judgment call that can't be coded reliably. A rule the bot can enforce
   identically every time beats a heuristic that drifts.

**The honest counterpoint (so the steelman isn't a strawman of the alternative):**
On very low timeframes a body close can still be faked, and waiting for the close
sacrifices the best fill. Some intrabar/aggressive styles legitimately enter on
the sweep-and-reclaim *before* the close. Our answer: that's a different strategy
with different risk controls. For this swing system the body-close requirement is
the right default, which is why `bos_body_close` is a **hard gate** in
`app/mtf.py` — a `False` or missing value is rejected, never relayed.

**Implementation:** enforced in two places.
- Pine (`pine/gold_swing_alert.pine`): `bosUp = bodyTop > lastSwingHigh and close > lastSwingHigh`.
- Backend (`app/mtf.py:check_bos_integrity`): rejects unless `bos_body_close is True`.
