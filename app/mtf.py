"""
Multi-timeframe bias engine + BOS confirmation (the core filter, Always #2).

Two responsibilities:

1. Higher-timeframe agreement: a lower-TF execution trigger (1m/5m/15m) is only
   allowed if the higher timeframes (30m/1h/4h/1D) agree with its direction.
   The Pine Script reports each HTF's current trend in `htf_bias`; this module
   decides whether the alignment is strong enough.

2. Structural-shift integrity (/STEELMAN): a genuine Break of Structure (BOS)
   requires a candle BODY close beyond the swing level, not merely a wick that
   sweeps liquidity and snaps back. We hard-require `bos_body_close == True`.

Note on the 500-bar HTF Supply/Demand lookback: zone *detection* happens on
TradingView in Pine (where the bar history lives), using HTF_LOOKBACK_BARS=500.
This backend consumes the resulting per-TF bias the script ships in the payload,
then enforces agreement. `required_htf_agreement` controls how strict we are.
"""
from __future__ import annotations

from .schemas import AlertPayload, Direction, ValidationResult

# How many of the four HTFs must agree with the trigger direction.
# 3/4 keeps us out of chop while tolerating one lagging/neutral timeframe.
REQUIRED_HTF_AGREEMENT = 3


def _htf_votes(payload: AlertPayload) -> list[Direction]:
    b = payload.htf_bias
    return [d for d in (b.tf_30m, b.tf_1h, b.tf_4h, b.tf_1d) if d is not None]


def _swing_votes(payload: AlertPayload) -> list[Direction]:
    """For swing triggers, only TFs STRICTLY ABOVE the trigger count as 'higher'."""
    b = payload.htf_bias
    if payload.timeframe == "1h":
        pool = (b.tf_4h, b.tf_1d)
    else:  # 4h
        pool = (b.tf_1d,)
    return [d for d in pool if d is not None]


def check_mtf_alignment(
    payload: AlertPayload,
    result: ValidationResult,
    required: int = REQUIRED_HTF_AGREEMENT,
) -> None:
    tf = payload.timeframe

    # --- swing triggers (1h/4h): every strictly-higher TF must agree ---
    if tf in {"1h", "4h"}:
        votes = _swing_votes(payload)
        if not votes:
            result.add_reject(f"No bias above the {tf} swing trigger; cannot confirm trend.")
            return
        agree = sum(1 for v in votes if v == payload.direction)
        if agree < len(votes):
            result.add_reject(
                f"Swing trigger on {tf} requires FULL agreement above it; "
                f"only {agree}/{len(votes)} aligned with {payload.direction.value}."
            )
            return
        result.add_pass(
            f"Swing trend confirmed: all {agree}/{len(votes)} higher timeframes "
            f"agree with {payload.direction.value}."
        )
        return

    # --- intraday triggers (1m/5m/15m): classic 4-HTF vote ---
    if tf not in {"1m", "5m", "15m"}:
        result.add_reject(
            f"Trigger timeframe {tf} is not a supported trigger TF (1m/5m/15m/1h/4h)."
        )
        return

    votes = _htf_votes(payload)
    if not votes:
        result.add_reject("No higher-timeframe bias supplied; cannot confirm trend.")
        return

    agree = sum(1 for v in votes if v == payload.direction)
    against = sum(1 for v in votes if v != payload.direction)

    if agree < required:
        result.add_reject(
            f"HTF agreement {agree}/{len(votes)} < required {required} "
            f"for {payload.direction.value} (against={against})."
        )
        return

    result.add_pass(
        f"HTF trend confirmed: {agree}/{len(votes)} timeframes agree with "
        f"{payload.direction.value}."
    )


def check_bos_integrity(payload: AlertPayload, result: ValidationResult) -> None:
    """/STEELMAN: a true BOS needs a body close past the swing, not a wick sweep."""
    if payload.bos_body_close is None:
        result.add_reject(
            "BOS confirmation missing: cannot tell body close from wick sweep."
        )
        return
    if payload.bos_body_close is False:
        result.add_reject(
            "Structure break was a wick sweep (liquidity grab), not a body close — "
            "no valid BOS/ChoCh."
        )
        return
    result.add_pass("BOS confirmed by candle body close past swing level.")
