"""15m trigger + confluence grading tests."""
from __future__ import annotations

from app.relay import _confluence
from app.scanner import Candle, resample_closed
from app.schemas import AlertPayload, Direction, HTFBias


def _mk(ts: int, o=100.0, h=101.0, l=99.0, c=100.0) -> Candle:
    return Candle(ts, o, h, l, c)


def test_resample_closed_drops_unfinished_bucket():
    base = 1_750_003_200  # 15m-aligned
    # Four 5m candles: one full 15m bucket + 1 candle of the next bucket
    c5 = [_mk(base + i * 300) for i in range(4)]
    out = resample_closed(c5, 15)
    assert len(out) == 1                     # unfinished 2nd bucket dropped
    assert out[0].ts == base

    # With six candles (two full buckets) both survive
    c5 = [_mk(base + i * 300) for i in range(6)]
    assert len(resample_closed(c5, 15)) == 2


def _payload(bias: HTFBias) -> AlertPayload:
    return AlertPayload(
        symbol="XAUUSD", timeframe="5m", direction=Direction.BUY, pattern="t",
        entry=3300.0, stop_loss=3295.0, take_profits=[3305.0],
        htf_bias=bias, bos_body_close=True, timestamp=1.0,
    )


def test_confluence_full_alignment_is_a_grade():
    full = HTFBias(tf_30m=Direction.BUY, tf_1h=Direction.BUY,
                   tf_4h=Direction.BUY, tf_1d=Direction.BUY)
    assert "4/4" in _confluence(_payload(full))
    assert "A-grade" in _confluence(_payload(full))


def test_confluence_partial():
    part = HTFBias(tf_30m=Direction.BUY, tf_1h=Direction.BUY,
                   tf_4h=Direction.BUY, tf_1d=Direction.SELL)
    s = _confluence(_payload(part))
    assert "3/4" in s and "A-grade" not in s


def test_confluence_no_votes():
    assert _confluence(_payload(HTFBias())) == "n/a"


def test_volatility_shock_detection():
    from app.scanner import volatility_shock
    base_ts = 1_750_003_200
    # 60 calm bars (range ~1.0 USD) then a violent 10 USD bar
    calm = [_mk(base_ts + i * 300, 100.0, 100.6, 99.6, 100.1) for i in range(60)]
    shocked, note = volatility_shock(calm, mult=3.0)
    assert not shocked

    violent = calm + [_mk(base_ts + 61 * 300, 100.0, 110.0, 99.0, 108.0)]
    shocked, note = volatility_shock(violent, mult=3.0)
    assert shocked
    assert "VOLATILITY SHOCK" in note
