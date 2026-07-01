"""
Alert payload schema (Rule #2).

The clean JSON contract every TradingView alert must satisfy:
  Timeframe, Direction (BUY/SELL), Pattern Type, Entry Price,
  Stop Loss (below the zone/structure), and Take Profit targets.
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


# Lower-timeframe execution triggers vs higher-timeframe bias confirmation.
EXECUTION_TFS = {"1m", "5m", "15m"}
HTF_TFS = {"30m", "1h", "4h", "1D"}


class HTFBias(BaseModel):
    """Trend direction reported by the Pine Script per higher timeframe."""
    tf_30m: Optional[Direction] = None
    tf_1h: Optional[Direction] = None
    tf_4h: Optional[Direction] = None
    tf_1d: Optional[Direction] = None


class AlertPayload(BaseModel):
    # --- identity / auth ---
    symbol: str = Field(..., examples=["XAUUSD"])
    token: Optional[str] = Field(default=None, description="Optional shared token")
    sig: Optional[str] = Field(default=None, description="HMAC of stable fields")
    # REQUIRED: no default. A defaulted timestamp would silently defeat the
    # replay guard (any payload missing it would always look "fresh").
    timestamp: float = Field(..., description="Unix seconds when the alert fired")

    # --- the trade ---
    timeframe: str = Field(..., examples=["5m"], description="Execution timeframe")
    direction: Direction
    pattern: str = Field(..., examples=["ChoCh + Bullish Engulfing"])
    entry: float = Field(..., gt=0)
    stop_loss: float = Field(..., gt=0)
    take_profits: List[float] = Field(..., min_length=1)

    # --- context for validation ---
    htf_bias: HTFBias = Field(default_factory=HTFBias)
    spread: Optional[float] = Field(default=None, ge=0, description="Current spread in USD")
    bos_body_close: Optional[bool] = Field(
        default=None,
        description="True if structure break was confirmed by a candle BODY close "
        "past the swing level (not just a wick sweep).",
    )

    @field_validator("symbol")
    @classmethod
    def _symbol_must_be_gold(cls, v: str) -> str:
        v = v.upper().replace("/", "").replace("OANDA:", "").replace("$", "")
        if v not in {"XAUUSD", "GOLD"}:
            raise ValueError("This bot trades XAU/USD (Gold) exclusively.")
        return "XAUUSD"

    @field_validator("timeframe")
    @classmethod
    def _tf_known(cls, v: str) -> str:
        v = v.strip()
        if v not in EXECUTION_TFS | HTF_TFS:
            raise ValueError(f"Unknown timeframe '{v}'.")
        return v

    def stable_payload(self) -> str:
        """Deterministic string the `sig` field is computed over (excludes sig)."""
        return "|".join(
            str(x)
            for x in (
                self.symbol,
                self.timeframe,
                self.direction.value,
                self.entry,
                self.stop_loss,
                ",".join(str(t) for t in self.take_profits),
                int(self.timestamp),
            )
        )


class ValidationResult(BaseModel):
    passed: bool
    reasons: List[str] = []           # why it passed each gate
    rejections: List[str] = []        # why it was suppressed
    rr_targets: List[float] = []      # risk:reward per TP

    def add_pass(self, msg: str) -> None:
        self.reasons.append(msg)

    def add_reject(self, msg: str) -> None:
        self.rejections.append(msg)
        self.passed = False
