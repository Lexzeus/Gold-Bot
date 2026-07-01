"""
Configuration loader.

Secrets are loaded in this priority order:
  1. Encrypted blob `secrets.enc` decrypted with ENV_MASTER_KEY (production).
  2. Plain `.env` file (development).
  3. Process environment.

No secret is ever hardcoded in source. If a required secret is missing the
app refuses to start rather than running insecurely.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
ENCRYPTED_SECRETS = ROOT / "secrets.enc"


def _load_encrypted_secrets() -> None:
    """If secrets.enc + ENV_MASTER_KEY exist, decrypt and inject into os.environ."""
    master_key = os.getenv("ENV_MASTER_KEY")
    if not (ENCRYPTED_SECRETS.exists() and master_key):
        return
    try:
        from cryptography.fernet import Fernet

        token = ENCRYPTED_SECRETS.read_bytes()
        data = json.loads(Fernet(master_key.encode()).decrypt(token).decode())
        for k, v in data.items():
            # Encrypted values win over a checked-in .env, but never override
            # something explicitly exported in the real process environment.
            os.environ.setdefault(k, str(v))
    except Exception as exc:  # pragma: no cover - defensive
        raise RuntimeError(f"Failed to decrypt secrets.enc: {exc}") from exc


# Load .env first (dev), then layer encrypted secrets (prod) on top.
load_dotenv(ROOT / ".env")
_load_encrypted_secrets()


def _get(key: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.getenv(key, default)
    if required and (val is None or val == "" or "replace_me" in str(val)):
        raise RuntimeError(
            f"Required secret '{key}' is missing. Set it in .env or secrets.enc."
        )
    return val


def _bool(key: str, default: bool) -> bool:
    return str(os.getenv(key, str(default))).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # auth
    webhook_signing_secret: str
    webhook_shared_token: str | None
    webhook_max_age_seconds: int
    # relay
    discord_webhook_url: str
    discord_news_webhook_url: str | None
    # strategy strictness
    htf_required_agreement: int
    # news
    news_provider: str
    alphavantage_api_key: str | None
    news_blackout_before: int
    news_blackout_after: int
    news_fail_closed: bool
    # strategy
    symbol: str
    htf_lookback_bars: int
    max_spread_usd: float
    ny_session_only: bool
    ny_session_start: str
    ny_session_end: str
    timezone: str
    # built-in scanner (replaces TradingView)
    scanner_enabled: bool
    scanner_poll_seconds: int
    twelvedata_api_key: str | None


def load_settings(require_secrets: bool = True) -> Settings:
    return Settings(
        webhook_signing_secret=_get("WEBHOOK_SIGNING_SECRET", required=require_secrets) or "",
        webhook_shared_token=_get("WEBHOOK_SHARED_TOKEN"),
        webhook_max_age_seconds=int(_get("WEBHOOK_MAX_AGE_SECONDS", "120")),
        discord_webhook_url=_get("DISCORD_WEBHOOK_URL", required=require_secrets) or "",
        discord_news_webhook_url=_get("DISCORD_NEWS_WEBHOOK_URL") or None,
        htf_required_agreement=int(_get("HTF_REQUIRED_AGREEMENT", "3")),
        news_provider=(_get("NEWS_PROVIDER", "forexfactory") or "forexfactory").lower(),
        alphavantage_api_key=_get("ALPHAVANTAGE_API_KEY"),
        news_blackout_before=int(_get("NEWS_BLACKOUT_MINUTES_BEFORE", "30")),
        news_blackout_after=int(_get("NEWS_BLACKOUT_MINUTES_AFTER", "30")),
        news_fail_closed=_bool("NEWS_FAIL_CLOSED", False),
        symbol=(_get("SYMBOL", "XAUUSD") or "XAUUSD").upper(),
        htf_lookback_bars=int(_get("HTF_LOOKBACK_BARS", "500")),
        max_spread_usd=float(_get("MAX_SPREAD_USD", "0.50")),
        ny_session_only=_bool("NY_SESSION_ONLY", True),
        ny_session_start=_get("NY_SESSION_START", "08:00") or "08:00",
        ny_session_end=_get("NY_SESSION_END", "12:00") or "12:00",
        timezone=_get("TIMEZONE", "America/New_York") or "America/New_York",
        scanner_enabled=_bool("SCANNER_ENABLED", False),
        scanner_poll_seconds=int(_get("SCANNER_POLL_SECONDS", "300")),
        twelvedata_api_key=_get("TWELVEDATA_API_KEY") or None,
    )
