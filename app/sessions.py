"""
New York session volume-pocket toggle (Rule #3).

When NY_SESSION_ONLY is true, execution triggers fired outside the configured
window (default 08:00–12:00 ET, anchored on the 8:00 AM NY open) are muted.
"""
from __future__ import annotations

from datetime import datetime, time as dtime

import pytz


def _parse_hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


def in_ny_session(
    now_utc: datetime | None,
    tz_name: str,
    start_hhmm: str,
    end_hhmm: str,
) -> tuple[bool, str]:
    """Return (is_in_window, human_readable_local_time)."""
    tz = pytz.timezone(tz_name)
    now_utc = now_utc or datetime.now(pytz.utc)
    if now_utc.tzinfo is None:
        now_utc = pytz.utc.localize(now_utc)
    local = now_utc.astimezone(tz)

    # Weekends: gold spot is effectively closed.
    if local.weekday() >= 5:
        return False, local.strftime("%a %H:%M %Z")

    start, end = _parse_hhmm(start_hhmm), _parse_hhmm(end_hhmm)
    inside = start <= local.time() <= end
    return inside, local.strftime("%a %H:%M %Z")
