"""
Spread / volatility guard (Never #2).

Execution signals must not fire when the spread is too wide — typical during
news spikes and the rollover window — because the 1m entry would be filled far
from the intended price (see docs/PREMORTEM.md).
"""
from __future__ import annotations

from .schemas import AlertPayload, ValidationResult


def check_spread(payload: AlertPayload, result: ValidationResult, max_spread_usd: float) -> None:
    if payload.spread is None:
        # Fail closed only for the fastest TF where slippage hurts most.
        if payload.timeframe == "1m":
            result.add_reject("No spread reading on a 1m trigger; refusing to fire blind.")
        else:
            result.add_pass("No spread reading supplied (non-1m); allowed.")
        return

    if payload.spread > max_spread_usd:
        result.add_reject(
            f"Spread {payload.spread:.2f} USD exceeds max {max_spread_usd:.2f} USD "
            f"— likely news/illiquidity, entry would slip."
        )
        return

    result.add_pass(f"Spread OK: {payload.spread:.2f} USD ≤ {max_spread_usd:.2f} USD.")


def compute_rr(payload: AlertPayload, result: ValidationResult) -> None:
    """Sanity-check SL placement and report R:R for each TP."""
    risk = abs(payload.entry - payload.stop_loss)
    if risk <= 0:
        result.add_reject("Stop loss equals entry — zero/undefined risk.")
        return

    # SL must be on the protective side of the structure.
    if payload.direction.value == "BUY" and payload.stop_loss >= payload.entry:
        result.add_reject("BUY stop loss must sit BELOW entry/structure.")
        return
    if payload.direction.value == "SELL" and payload.stop_loss <= payload.entry:
        result.add_reject("SELL stop loss must sit ABOVE entry/structure.")
        return

    rr = [round(abs(tp - payload.entry) / risk, 2) for tp in payload.take_profits]
    result.rr_targets = rr
    result.add_pass(f"Risk defined ({risk:.2f} USD). R:R targets: {rr}.")
