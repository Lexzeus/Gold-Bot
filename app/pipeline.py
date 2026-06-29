"""
Validation orchestration — the gate stack a signal must clear before relay.

Order is cheapest-and-most-decisive first:
  1. BOS integrity (body close, not wick sweep)   [/STEELMAN]
  2. Multi-timeframe HTF agreement                 [Always #2, core filter]
  3. Risk / SL placement + R:R
  4. NY-session volume pocket toggle               [Rule #3]
  5. Spread / volatility guard                     [Never #2]
  6. News blackout                                 [Always #3, Never #2]
"""
from __future__ import annotations

from datetime import datetime

from .config import Settings
from .mtf import check_bos_integrity, check_mtf_alignment
from .news import NewsVerdict, check_news
from .schemas import AlertPayload, ValidationResult
from .sessions import in_ny_session
from .spread import check_spread, compute_rr


async def validate(
    payload: AlertPayload, cfg: Settings, now_utc: datetime | None = None
) -> tuple[ValidationResult, NewsVerdict, str]:
    result = ValidationResult(passed=True)

    # 1 + 2: structure + trend (the high-confidence core)
    check_bos_integrity(payload, result)
    check_mtf_alignment(payload, result, required=cfg.htf_required_agreement)

    # 3: risk geometry
    compute_rr(payload, result)

    # 4: NY session pocket
    inside, local_time = in_ny_session(
        now_utc, cfg.timezone, cfg.ny_session_start, cfg.ny_session_end
    )
    if cfg.ny_session_only and not inside:
        result.add_reject(
            f"Outside NY session pocket ({cfg.ny_session_start}-{cfg.ny_session_end} "
            f"{cfg.timezone}); now {local_time}."
        )
    elif inside:
        result.add_pass(f"Within NY session pocket ({local_time}).")

    # 5: spread guard
    check_spread(payload, result, cfg.max_spread_usd)

    # 6: news blackout
    news = await check_news(
        cfg.news_provider, cfg.alphavantage_api_key,
        cfg.news_blackout_before, cfg.news_blackout_after, now_utc,
        fail_closed=cfg.news_fail_closed,
    )
    if news.blocked:
        result.add_reject(news.note)

    return result, news, local_time
