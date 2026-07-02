"""
Built-in market scanner — replaces the TradingView/Pine side entirely.

Every SCANNER_POLL_SECONDS (default 300 = one 5m bar) it:
  1. Pulls 5m gold candles (Yahoo Finance GC=F, keyless) and 1D candles.
  2. Resamples 5m -> 30m / 1h / 4h locally.
  3. Computes EMA50-slope bias per higher timeframe (same rule as the Pine script).
  4. Detects swing pivots (length 10) and a Break of Structure confirmed by a
     candle BODY close past the swing level — latched one alert per break,
     exactly like the Pine version.
  5. Rebases futures prices to spot using Swissquote's public XAU/USD quote
     (also yields a real bid/ask spread for the spread gate). Best-effort.
  6. Feeds the signal through the SAME gate stack (pipeline.validate) and
     relays to Discord on pass.

Data notes:
  - GC=F is the gold futures front month; its *shape* (structure, EMAs) tracks
    spot closely. Absolute prices differ by the basis, hence the spot rebase.
  - If Swissquote is unreachable we send futures-referenced prices and no
    spread; the 5m spread gate passes on missing spread by design.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
import pytz

from .config import Settings
from .pipeline import validate
from .relay import build_embed, build_news_notice, send_discord
from .schemas import AlertPayload, Direction, HTFBias

log = logging.getLogger("gold-scanner")

SWING_LEN = 10
SL_BUFFER_USD = 0.50
TP_R = (1.0, 2.0, 3.0)

YAHOO_HOSTS = ("query1.finance.yahoo.com", "query2.finance.yahoo.com")
STOOQ_URL = "https://stooq.com/q/d/l/"
SWISSQUOTE_URL = (
    "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
)
UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
}
STOOQ_TZ = pytz.timezone("Europe/Warsaw")  # stooq intraday timestamps are Warsaw time


@dataclass
class Candle:
    ts: int  # bar open, epoch seconds UTC
    open: float
    high: float
    low: float
    close: float


# ---------------------------------------------------------------- data fetch
# Provider chain: Yahoo (GC=F futures) -> Stooq (spot XAUUSD CSV) -> stale cache.
# Yahoo rate-limits cloud IPs (429); Stooq is keyless CSV. Cache smooths gaps.
_CANDLE_CACHE: dict[str, tuple[float, list["Candle"]]] = {}
_CACHE_MAX_AGE = 1800  # 30 min of staleness tolerated before we give up


async def _fetch_yahoo(interval: str, range_: str) -> list[Candle]:
    params = {"interval": interval, "range": range_, "includePrePost": "false"}
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=15.0, headers=UA) as client:
        for host in YAHOO_HOSTS:
            try:
                r = await client.get(f"https://{host}/v8/finance/chart/GC=F", params=params)
                r.raise_for_status()
                data = r.json()
                break
            except Exception as exc:
                last_exc = exc
        else:
            raise last_exc or RuntimeError("yahoo unreachable")
    result = data["chart"]["result"][0]
    ts = result["timestamp"]
    q = result["indicators"]["quote"][0]
    candles = []
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            continue
        candles.append(Candle(int(t), float(o), float(h), float(l), float(c)))
    return candles


async def _fetch_stooq(interval: str) -> list[Candle]:
    """Stooq spot XAUUSD CSV. interval '5m' -> i=5, '1d' -> i=d."""
    params = {"s": "xauusd", "i": "5" if interval == "5m" else "d"}
    async with httpx.AsyncClient(timeout=20.0, headers=UA, follow_redirects=True) as client:
        r = await client.get(STOOQ_URL, params=params)
        r.raise_for_status()
        text = r.text
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 3 or not lines[0].lower().startswith("date"):
        raise RuntimeError(f"stooq returned no data ({lines[:1]})")
    header = [h.strip().lower() for h in lines[0].split(",")]
    idx = {name: header.index(name) for name in ("date", "open", "high", "low", "close")}
    t_idx = header.index("time") if "time" in header else None
    candles: list[Candle] = []
    for ln in lines[1:]:
        cols = ln.split(",")
        try:
            if t_idx is not None:
                naive = datetime.strptime(f"{cols[idx['date']]} {cols[t_idx]}", "%Y-%m-%d %H:%M:%S")
                ts = int(STOOQ_TZ.localize(naive).timestamp())
            else:
                naive = datetime.strptime(cols[idx["date"]], "%Y-%m-%d")
                ts = int(pytz.utc.localize(naive).timestamp())
            candles.append(
                Candle(ts, float(cols[idx["open"]]), float(cols[idx["high"]]),
                       float(cols[idx["low"]]), float(cols[idx["close"]]))
            )
        except Exception:
            continue
    if not candles:
        raise RuntimeError("stooq CSV parsed to zero candles")
    return candles


async def _fetch_twelvedata(interval: str, api_key: str) -> list[Candle]:
    """Twelve Data spot XAU/USD. Free tier: 800 credits/day (we use ~300)."""
    td_interval = "5min" if interval == "5m" else "1day"
    outputsize = 2500 if interval == "5m" else 80
    params = {
        "symbol": "XAU/USD",
        "interval": td_interval,
        "outputsize": str(outputsize),
        "timezone": "UTC",
        "apikey": api_key,
    }
    async with httpx.AsyncClient(timeout=20.0, headers=UA) as client:
        r = await client.get("https://api.twelvedata.com/time_series", params=params)
        r.raise_for_status()
        data = r.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(f"twelvedata: {data.get('message', 'no values')}")
    candles: list[Candle] = []
    for row in reversed(data["values"]):  # API is newest-first; we want oldest-first
        try:
            dt_str = row["datetime"]
            # Intraday: "2026-07-01 15:30:00" — Daily: just "2026-07-01"
            if len(dt_str) <= 10:
                naive = datetime.strptime(dt_str, "%Y-%m-%d")
            else:
                naive = datetime.strptime(dt_str[:16], "%Y-%m-%d %H:%M")
            ts = int(pytz.utc.localize(naive).timestamp())
            candles.append(
                Candle(ts, float(row["open"]), float(row["high"]),
                       float(row["low"]), float(row["close"]))
            )
        except Exception:
            continue
    if not candles:
        raise RuntimeError("twelvedata parsed to zero candles")
    return candles


async def fetch_candles(interval: str, range_: str, td_api_key: str | None = None) -> list[Candle]:
    key = f"{interval}:{range_}"
    errors = []
    fetchers: list[tuple] = []
    if td_api_key:
        fetchers.append(("twelvedata", lambda: _fetch_twelvedata(interval, td_api_key)))
    fetchers.append(("yahoo", lambda: _fetch_yahoo(interval, range_)))
    fetchers.append(("stooq", lambda: _fetch_stooq(interval)))
    for name, fetcher in fetchers:
        try:
            candles = await fetcher()
            if candles:
                _CANDLE_CACHE[key] = (time.time(), candles)
                return candles
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    cached = _CANDLE_CACHE.get(key)
    if cached and time.time() - cached[0] < _CACHE_MAX_AGE:
        log.warning("All feeds failed (%s); using cached candles.", "; ".join(errors))
        return cached[1]
    raise RuntimeError("All candle feeds failed: " + "; ".join(errors))


async def fetch_spot() -> tuple[float | None, float | None]:
    """Return (spot_mid, spread_usd) from Swissquote, or (None, None)."""
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=UA) as client:
            r = await client.get(SWISSQUOTE_URL)
            r.raise_for_status()
            rows = r.json()
        best_bid, best_ask = None, None
        for row in rows:
            for prof in row.get("spreadProfilePrices", []):
                bid, ask = prof.get("bid"), prof.get("ask")
                if bid and ask and (best_bid is None or ask - bid < best_ask - best_bid):
                    best_bid, best_ask = float(bid), float(ask)
        if best_bid is None:
            return None, None
        return (best_bid + best_ask) / 2, round(best_ask - best_bid, 2)
    except Exception as exc:
        log.warning("Spot fetch failed (%s); using futures prices, no spread.", exc)
        return None, None


# ---------------------------------------------------------------- indicators
def resample_closed(c5: list[Candle], minutes: int) -> list[Candle]:
    """Resample and drop the final bucket if it isn't fully covered yet.

    A 15m 'bar' assembled from only one or two 5m candles hasn't closed —
    triggering on it would be acting on an unfinished candle.
    """
    out = resample(c5, minutes)
    if out and c5:
        last_5m_end = c5[-1].ts + 300
        if out[-1].ts + minutes * 60 > last_5m_end:
            out = out[:-1]
    return out


def resample(c5: list[Candle], minutes: int) -> list[Candle]:
    """Aggregate 5m candles into fixed `minutes` buckets (UTC-aligned)."""
    out: list[Candle] = []
    bucket = minutes * 60
    cur: Candle | None = None
    cur_key = None
    for c in c5:
        key = c.ts - (c.ts % bucket)
        if key != cur_key:
            if cur is not None:
                out.append(cur)
            cur = Candle(key, c.open, c.high, c.low, c.close)
            cur_key = key
        else:
            cur.high = max(cur.high, c.high)
            cur.low = min(cur.low, c.low)
            cur.close = c.close
    if cur is not None:
        out.append(cur)
    return out


def ema(values: list[float], length: int) -> list[float]:
    if not values:
        return []
    k = 2 / (length + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ema_bias(candles: list[Candle], length: int = 50) -> Direction | None:
    """BUY if EMA rising (now > prev), SELL if falling — same as Pine f_bias."""
    closes = [c.close for c in candles]
    if len(closes) < length + 2:
        return None
    e = ema(closes, length)
    return Direction.BUY if e[-1] > e[-2] else Direction.SELL


def find_pivots(candles: list[Candle], n: int = SWING_LEN) -> tuple[float | None, float | None]:
    """Most recent confirmed swing high/low (n bars either side), Pine-style.

    Pine's ta.pivothigh/low requires the pivot to be STRICTLY beyond its
    neighbors — a flat plateau is not a pivot.
    """
    last_high = last_low = None
    for i in range(n, len(candles) - n):
        neighbors = candles[i - n : i] + candles[i + 1 : i + n + 1]
        if all(candles[i].high > c.high for c in neighbors):
            last_high = candles[i].high
        if all(candles[i].low < c.low for c in neighbors):
            last_low = candles[i].low
    return last_high, last_low


# ---------------------------------------------------------------- state
TRIGGER_TFS = ("5m", "15m")


class ScannerState:
    """Latches per trigger TF: one alert per structural break per timeframe."""

    def __init__(self) -> None:
        self.fired: dict[tuple[str, str], float] = {}   # (tf, side) -> swing level
        self.last_bar_ts: dict[str, int] = {}           # tf -> last evaluated bar


STATE = ScannerState()


# ---------------------------------------------------------------- one cycle
async def scan_once(cfg: Settings, state: ScannerState = STATE) -> dict | None:
    c5 = await fetch_candles("5m", "7d", cfg.twelvedata_api_key)
    if len(c5) < SWING_LEN * 2 + 2:
        log.info("Not enough 5m candles yet (%d).", len(c5))
        return None

    # Work on CLOSED bars only: drop the still-forming last 5m candle.
    c5 = c5[:-1]

    # HTF bias computed once per cycle (30m/1h/4h resampled; 1D fetched)
    bias = HTFBias(
        tf_30m=ema_bias(resample(c5, 30)),
        tf_1h=ema_bias(resample(c5, 60)),
        tf_4h=ema_bias(resample(c5, 240)),
        tf_1d=ema_bias(await fetch_candles("1d", "1y", cfg.twelvedata_api_key)),
    )

    spot_mid, spread = await fetch_spot()
    outcomes: list[dict] = []

    for tf in TRIGGER_TFS:
        candles = c5 if tf == "5m" else resample_closed(c5, int(tf.rstrip("m")))
        if len(candles) < SWING_LEN * 2 + 2:
            continue
        bar = candles[-1]
        if state.last_bar_ts.get(tf) == bar.ts:
            continue  # this TF has no newly closed bar since last cycle
        state.last_bar_ts[tf] = bar.ts

        # Swing structure from bars BEFORE the trigger bar
        swing_high, swing_low = find_pivots(candles[:-1])
        body_top = max(bar.open, bar.close)
        body_bottom = min(bar.open, bar.close)
        bos_up = swing_high is not None and body_top > swing_high and bar.close > swing_high
        bos_down = swing_low is not None and body_bottom < swing_low and bar.close < swing_low

        direction: Direction | None = None
        if bos_up and state.fired.get((tf, "long")) != swing_high:
            direction = Direction.BUY
            state.fired[(tf, "long")] = swing_high
        elif bos_down and state.fired.get((tf, "short")) != swing_low:
            direction = Direction.SELL
            state.fired[(tf, "short")] = swing_low
        if direction is None:
            continue

        # Price geometry (rebase futures -> spot when possible)
        offset = (spot_mid - bar.close) if spot_mid is not None else 0.0
        entry = round(bar.close + offset, 2)
        if direction is Direction.BUY:
            sl = round((swing_low if swing_low is not None else bar.low) + offset - SL_BUFFER_USD, 2)
            risk = entry - sl
            tps = [round(entry + risk * r, 2) for r in TP_R]
        else:
            sl = round((swing_high if swing_high is not None else bar.high) + offset + SL_BUFFER_USD, 2)
            risk = sl - entry
            tps = [round(entry - risk * r, 2) for r in TP_R]
        if risk <= 0:
            log.info("[%s] Degenerate risk geometry; skipping.", tf)
            continue

        payload = AlertPayload(
            symbol="XAUUSD",
            token=cfg.webhook_shared_token,
            timestamp=time.time(),
            timeframe=tf,
            direction=direction,
            pattern=f"BOS body-close on {tf} (built-in scanner)",
            entry=entry,
            stop_loss=sl,
            take_profits=tps,
            htf_bias=bias,
            spread=spread,
            bos_body_close=True,
        )

        result, news, local_time = await validate(payload, cfg)
        if not result.passed:
            log.info("[%s] Scanner signal SUPPRESSED %s: %s", tf, direction.value, result.rejections)
            if news.blocked and cfg.discord_news_webhook_url:
                try:
                    await send_discord(
                        cfg.discord_news_webhook_url,
                        build_news_notice(payload, news, "suppressed"),
                    )
                except Exception as exc:
                    log.error("News notice failed: %s", exc)
            outcomes.append({"tf": tf, "status": "suppressed", "rejections": result.rejections})
            continue

        await send_discord(cfg.discord_webhook_url, build_embed(payload, result, news, local_time))
        if news.flagged and not news.blocked and cfg.discord_news_webhook_url:
            try:
                await send_discord(
                    cfg.discord_news_webhook_url, build_news_notice(payload, news, "heads_up")
                )
            except Exception as exc:
                log.error("News heads-up failed: %s", exc)
        log.info("[%s] Scanner RELAYED %s entry=%.2f sl=%.2f", tf, direction.value, entry, sl)
        outcomes.append({"tf": tf, "status": "relayed", "direction": direction.value, "entry": entry})

    if not outcomes:
        return None
    return {"status": outcomes[0]["status"] if len(outcomes) == 1 else "multiple", "signals": outcomes}


# ---------------------------------------------------------------- loop
async def run_scanner(cfg: Settings, poll_seconds: int) -> None:
    log.info("Built-in scanner started (poll every %ds).", poll_seconds)
    while True:
        try:
            # Skip weekends entirely (gold closed); saves API calls.
            now_et = datetime.now(pytz.timezone("America/New_York"))
            if now_et.weekday() < 5 or (now_et.weekday() == 6 and now_et.hour >= 18):
                await scan_once(cfg)
        except Exception as exc:
            log.error("Scanner cycle failed: %s", exc)
        await asyncio.sleep(poll_seconds)
