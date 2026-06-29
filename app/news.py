"""
News filter (Always #3).

Pauses/flags signals around high-impact USD releases (CPI, NFP, FOMC) that
cluster around the New York open. Two providers:

  - forexfactory : public weekly JSON calendar, no key required.
  - alphavantage : ECONOMIC_CALENDAR / news sentiment (key required).

Results are cached for 15 minutes so we don't hammer the provider on every alert.
Fails OPEN-with-flag: if the calendar can't be fetched we don't block trading,
but we annotate the alert so you know the news state is unknown.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
import pytz

HIGH_IMPACT_KEYWORDS = (
    "Non-Farm", "Nonfarm", "NFP", "CPI", "Consumer Price",
    "FOMC", "Federal Funds", "Interest Rate", "PPI", "PCE",
    "Unemployment", "GDP", "Powell",
)

_CACHE: dict[str, tuple[float, list["NewsEvent"]]] = {}
_CACHE_TTL = 900  # 15 min


@dataclass
class NewsEvent:
    title: str
    when_utc: datetime
    impact: str
    currency: str


@dataclass
class NewsVerdict:
    blocked: bool
    flagged: bool
    note: str
    nearest: NewsEvent | None = None


async def _fetch_forexfactory() -> list[NewsEvent]:
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        rows = r.json()
    events: list[NewsEvent] = []
    for row in rows:
        if str(row.get("country", "")).upper() != "USD":
            continue
        if str(row.get("impact", "")).lower() != "high":
            continue
        try:
            when = datetime.fromisoformat(row["date"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = pytz.utc.localize(when)
        except Exception:
            continue
        events.append(
            NewsEvent(title=row.get("title", "?"), when_utc=when.astimezone(pytz.utc),
                      impact="high", currency="USD")
        )
    return events


async def _fetch_alphavantage(api_key: str) -> list[NewsEvent]:
    # AlphaVantage's economic calendar is CSV; we keep it best-effort.
    url = (
        "https://www.alphavantage.co/query?function=ECONOMIC_CALENDAR"
        f"&horizon=3month&apikey={api_key}"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        r.raise_for_status()
        text = r.text
    events: list[NewsEvent] = []
    lines = text.splitlines()
    if not lines or "," not in lines[0]:
        return events
    header = [h.strip().lower() for h in lines[0].split(",")]
    for line in lines[1:]:
        cols = line.split(",")
        if len(cols) < len(header):
            continue
        row = dict(zip(header, cols))
        if row.get("currency", "").upper() != "USD":
            continue
        title = row.get("event", "")
        if not any(k.lower() in title.lower() for k in HIGH_IMPACT_KEYWORDS):
            continue
        try:
            when = pytz.utc.localize(datetime.fromisoformat(row["releasedate"]))
        except Exception:
            continue
        events.append(NewsEvent(title=title, when_utc=when, impact="high", currency="USD"))
    return events


async def _get_events(provider: str, api_key: str | None) -> list[NewsEvent]:
    now = time.time()
    cached = _CACHE.get(provider)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]
    if provider == "alphavantage" and api_key:
        events = await _fetch_alphavantage(api_key)
    else:
        events = await _fetch_forexfactory()
    _CACHE[provider] = (now, events)
    return events


async def check_news(
    provider: str,
    api_key: str | None,
    minutes_before: int,
    minutes_after: int,
    now_utc: datetime | None = None,
    fail_closed: bool = False,
) -> NewsVerdict:
    now_utc = now_utc or datetime.now(pytz.utc)
    try:
        events = await _get_events(provider, api_key)
    except Exception as exc:
        if fail_closed:
            return NewsVerdict(
                blocked=True, flagged=True,
                note=f"BLOCKED: news calendar unavailable ({exc}) and NEWS_FAIL_CLOSED=true.",
            )
        return NewsVerdict(blocked=False, flagged=True,
                           note=f"News calendar unavailable ({exc}); proceeding uninformed.")

    window_before = timedelta(minutes=minutes_before)
    window_after = timedelta(minutes=minutes_after)
    nearest: NewsEvent | None = None
    nearest_delta: timedelta | None = None

    for ev in events:
        delta = ev.when_utc - now_utc
        if nearest_delta is None or abs(delta) < abs(nearest_delta):
            nearest, nearest_delta = ev, delta
        if -window_after <= delta <= window_before:
            mins = int(delta.total_seconds() // 60)
            rel = f"in {mins} min" if mins >= 0 else f"{abs(mins)} min ago"
            return NewsVerdict(
                blocked=True, flagged=True,
                note=f"BLOCKED: high-impact '{ev.title}' {rel}.",
                nearest=ev,
            )

    if nearest and nearest_delta is not None and abs(nearest_delta) <= timedelta(hours=2):
        mins = int(nearest_delta.total_seconds() // 60)
        return NewsVerdict(
            blocked=False, flagged=True,
            note=f"Heads-up: '{nearest.title}' in {mins} min (outside blackout).",
            nearest=nearest,
        )

    return NewsVerdict(blocked=False, flagged=False, note="No high-impact USD news nearby.")
