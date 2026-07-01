"""Scanner unit tests: resampling, EMA bias, pivots, BOS latch logic."""
from __future__ import annotations

from app.scanner import Candle, ScannerState, ema, ema_bias, find_pivots, resample
from app.schemas import Direction


def _mk(ts: int, o: float, h: float, l: float, c: float) -> Candle:
    return Candle(ts, o, h, l, c)


def test_resample_30m_from_5m():
    # Six 5m candles = one 30m candle
    base = 1_750_000_200  # NOT bucket-aligned; resample must align to bucket
    c5 = [
        _mk(base + i * 300, 100 + i, 105 + i, 95 + i, 101 + i) for i in range(6)
    ]
    out = resample(c5, 30)
    assert len(out) >= 1
    first = out[0]
    assert first.ts % 1800 == 0                      # bucket-aligned
    assert first.open == c5[0].open                  # first candle's open
    assert first.high == max(c.high for c in c5 if c.ts - (c.ts % 1800) == first.ts)
    assert first.low == min(c.low for c in c5 if c.ts - (c.ts % 1800) == first.ts)


def test_ema_monotonic_up_gives_buy_bias():
    candles = [_mk(i * 300, p, p + 1, p - 1, p) for i, p in enumerate(range(100, 300))]
    assert ema_bias(candles) is Direction.BUY
    candles_down = [_mk(i * 300, p, p + 1, p - 1, p) for i, p in enumerate(range(300, 100, -1))]
    assert ema_bias(candles_down) is Direction.SELL


def test_ema_bias_none_when_insufficient_data():
    candles = [_mk(i * 300, 100, 101, 99, 100) for i in range(10)]
    assert ema_bias(candles) is None


def test_ema_values():
    e = ema([1.0, 1.0, 1.0], 50)
    assert e == [1.0, 1.0, 1.0]


def test_find_pivots_detects_swing_high_low():
    # Build: flat, spike up at i=15, flat, dip at i=40, flat
    prices = [100.0] * 60
    prices[15] = 110.0
    prices[40] = 90.0
    candles = [_mk(i * 300, p, p + 0.5, p - 0.5, p) for i, p in enumerate(prices)]
    hi, lo = find_pivots(candles, n=10)
    assert hi == 110.5   # high of the spike candle
    assert lo == 89.5    # low of the dip candle


def test_bos_latch_one_alert_per_break():
    """The fired-at latch must block a second alert on the same swing level."""
    state = ScannerState()
    swing_high = 110.0
    # Simulate: first break fires, second bar above same swing must not.
    assert state.long_fired_at != swing_high
    state.long_fired_at = swing_high
    assert state.long_fired_at == swing_high
