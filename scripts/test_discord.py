"""
Fire one sample Gold alert embed to your configured Discord webhook.

Run from the project root after setting DISCORD_WEBHOOK_URL in .env:
    python scripts/test_discord.py

If you see a green "GOLD BUY SIGNAL (TEST)" message land in your Discord
channel, the relay is wired up correctly.
"""
from __future__ import annotations

import asyncio
import sys
import time

from app.config import load_settings
from app.relay import build_embed, send_discord
from app.schemas import AlertPayload, Direction, HTFBias, ValidationResult


def main() -> None:
    cfg = load_settings(require_secrets=False)
    if not cfg.discord_webhook_url or "xxxx" in cfg.discord_webhook_url:
        print("ERROR: DISCORD_WEBHOOK_URL is not set in .env (or still a placeholder).")
        print("       Paste your https://discord.com/api/webhooks/<id>/<token> there.")
        sys.exit(1)

    payload = AlertPayload(
        symbol="XAUUSD", timeframe="5m", direction=Direction.BUY,
        pattern="TEST — ChoCh + Bullish Engulfing",
        entry=3300.0, stop_loss=3295.0, take_profits=[3305.0, 3310.0, 3315.0],
        htf_bias=HTFBias(tf_30m=Direction.BUY, tf_1h=Direction.BUY,
                         tf_4h=Direction.BUY, tf_1d=Direction.BUY),
        spread=0.20, bos_body_close=True, timestamp=time.time(),
    )
    result = ValidationResult(
        passed=True,
        reasons=["This is a test embed — no real signal.",
                 "HTF trend confirmed: 4/4 timeframes agree with BUY."],
        rr_targets=[1.0, 2.0, 3.0],
    )
    from app.news import NewsVerdict
    news = NewsVerdict(blocked=False, flagged=False, note="Test — news filter not evaluated.")

    embed = build_embed(payload, result, news, "TEST")
    embed["embeds"][0]["title"] += " (TEST)"
    asyncio.run(send_discord(cfg.discord_webhook_url, embed))
    print("Sent! Check your Discord channel for the test alert.")


if __name__ == "__main__":
    main()
