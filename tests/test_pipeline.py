"""
End-to-end validation tests. Run: pytest -q  (from project root)

Covers: signature auth, replay guard, BOS gate, MTF gate, spread gate,
NY-session gate, R:R math, and the happy path that would relay.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytz

from app.config import Settings
from app.schemas import AlertPayload, Direction, HTFBias
from app.security import compute_signature, is_fresh, verify_signature
from app.pipeline import validate
from app import news as news_mod


def _cfg(**over) -> Settings:
    base = dict(
        webhook_signing_secret="testsecret",
        webhook_shared_token=None,
        webhook_max_age_seconds=120,
        discord_webhook_url="https://discord.com/api/webhooks/1/abc",
        discord_news_webhook_url=None,
        htf_required_agreement=3,
        news_provider="forexfactory",
        alphavantage_api_key=None,
        news_blackout_before=30,
        news_blackout_after=30,
        news_fail_closed=False,
        symbol="XAUUSD",
        htf_lookback_bars=500,
        max_spread_usd=0.50,
        ny_session_only=True,
        ny_session_start="08:00",
        ny_session_end="12:00",
        timezone="America/New_York",
    )
    base.update(over)
    return Settings(**base)


# A weekday inside the NY pocket: 2026-06-29 (Mon) 09:00 ET == 13:00 UTC.
NOW = pytz.utc.localize(datetime(2026, 6, 29, 13, 0))


def _payload(**over) -> AlertPayload:
    base = dict(
        symbol="XAUUSD",
        timeframe="5m",
        direction=Direction.BUY,
        pattern="ChoCh + Bullish Engulfing",
        entry=3300.0,
        stop_loss=3295.0,
        take_profits=[3305.0, 3310.0, 3315.0],
        htf_bias=HTFBias(tf_30m=Direction.BUY, tf_1h=Direction.BUY,
                         tf_4h=Direction.BUY, tf_1d=Direction.SELL),
        spread=0.20,
        bos_body_close=True,
        timestamp=NOW.timestamp(),
    )
    base.update(over)
    return AlertPayload(**base)


def _no_news(monkeypatch):
    async def fake(*a, **k):
        return news_mod.NewsVerdict(blocked=False, flagged=False, note="No news.")
    monkeypatch.setattr(news_mod, "check_news", fake)
    # pipeline imported check_news by name
    import app.pipeline as p
    monkeypatch.setattr(p, "check_news", fake)


# ---------------- security ----------------
def test_signature_roundtrip():
    body = b'{"hello":"world"}'
    sig = compute_signature("testsecret", body)
    assert verify_signature("testsecret", body, sig)
    assert not verify_signature("testsecret", body, "deadbeef")
    assert not verify_signature("testsecret", body, None)


def test_replay_guard():
    assert is_fresh(NOW.timestamp(), 120) is False  # NOW is in the past vs real clock
    import time
    assert is_fresh(time.time(), 120) is True
    assert is_fresh(time.time() - 9999, 120) is False
    assert is_fresh(None, 120) is False


# ---------------- gates ----------------
def test_happy_path_passes(monkeypatch):
    _no_news(monkeypatch)
    res, _, _ = asyncio.run(validate(_payload(), _cfg(), now_utc=NOW))
    assert res.passed, res.rejections
    assert res.rr_targets == [1.0, 2.0, 3.0]


def test_wick_sweep_rejected(monkeypatch):
    _no_news(monkeypatch)
    res, _, _ = asyncio.run(validate(_payload(bos_body_close=False), _cfg(), now_utc=NOW))
    assert not res.passed
    assert any("wick sweep" in r for r in res.rejections)


def test_mtf_disagreement_rejected(monkeypatch):
    _no_news(monkeypatch)
    bias = HTFBias(tf_30m=Direction.SELL, tf_1h=Direction.SELL,
                   tf_4h=Direction.BUY, tf_1d=Direction.SELL)
    res, _, _ = asyncio.run(validate(_payload(htf_bias=bias), _cfg(), now_utc=NOW))
    assert not res.passed
    assert any("HTF agreement" in r for r in res.rejections)


def test_htf_strictness_tunable(monkeypatch):
    _no_news(monkeypatch)
    # default payload has 3/4 agreeing (1d disagrees). required=4 must reject...
    res4, _, _ = asyncio.run(validate(_payload(), _cfg(htf_required_agreement=4), now_utc=NOW))
    assert not res4.passed
    # ...while required=2 still passes.
    res2, _, _ = asyncio.run(validate(_payload(), _cfg(htf_required_agreement=2), now_utc=NOW))
    assert res2.passed, res2.rejections


def test_wide_spread_rejected(monkeypatch):
    _no_news(monkeypatch)
    res, _, _ = asyncio.run(validate(_payload(spread=2.0), _cfg(), now_utc=NOW))
    assert not res.passed
    assert any("Spread" in r for r in res.rejections)


def test_outside_ny_session_rejected(monkeypatch):
    _no_news(monkeypatch)
    # 02:00 UTC == 22:00 ET previous day -> outside pocket
    night = pytz.utc.localize(datetime(2026, 6, 29, 2, 0))
    res, _, _ = asyncio.run(validate(_payload(timestamp=night.timestamp()), _cfg(), now_utc=night))
    assert not res.passed
    assert any("NY session" in r for r in res.rejections)


def test_bad_sl_side_rejected(monkeypatch):
    _no_news(monkeypatch)
    # BUY with SL above entry is invalid
    res, _, _ = asyncio.run(validate(_payload(stop_loss=3305.0), _cfg(), now_utc=NOW))
    assert not res.passed
    assert any("BUY stop loss" in r for r in res.rejections)


def test_non_gold_rejected():
    import pytest
    with pytest.raises(Exception):
        _payload(symbol="EURUSD")


def test_news_fail_closed_blocks_when_calendar_down(monkeypatch):
    # Force the calendar fetch to raise; fail_closed=True must block.
    async def boom(*a, **k):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(news_mod, "_get_events", boom)
    res, news, _ = asyncio.run(validate(_payload(), _cfg(news_fail_closed=True), now_utc=NOW))
    assert not res.passed
    assert news.blocked
    assert any("NEWS_FAIL_CLOSED" in r for r in res.rejections)


def test_news_fail_open_when_calendar_down(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("calendar down")
    monkeypatch.setattr(news_mod, "_get_events", boom)
    res, news, _ = asyncio.run(validate(_payload(), _cfg(news_fail_closed=False), now_utc=NOW))
    assert res.passed, res.rejections
    assert news.flagged and not news.blocked


def test_news_blackout_blocks(monkeypatch):
    async def fake(*a, **k):
        return news_mod.NewsVerdict(blocked=True, flagged=True, note="BLOCKED: CPI in 5 min.")
    import app.pipeline as p
    monkeypatch.setattr(p, "check_news", fake)
    res, _, _ = asyncio.run(validate(_payload(), _cfg(), now_utc=NOW))
    assert not res.passed
    assert any("CPI" in r for r in res.rejections)
