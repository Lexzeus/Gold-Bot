"""
Discord relay — pushes the validated, contextualized alert as a rich embed.
"""
from __future__ import annotations

import httpx

from .news import NewsVerdict
from .schemas import AlertPayload, ValidationResult

GREEN = 0x2ECC71
RED = 0xE74C3C


def build_embed(
    payload: AlertPayload, result: ValidationResult, news: NewsVerdict, local_time: str
) -> dict:
    color = GREEN if payload.direction.value == "BUY" else RED
    tps = "\n".join(
        f"• TP{i+1}: `{tp:.2f}`  ({rr}R)"
        for i, (tp, rr) in enumerate(
            zip(payload.take_profits, result.rr_targets or [0] * len(payload.take_profits))
        )
    )
    fields = [
        {"name": "Direction", "value": f"**{payload.direction.value}**", "inline": True},
        {"name": "Exec TF", "value": payload.timeframe, "inline": True},
        {"name": "Pattern", "value": payload.pattern, "inline": True},
        {"name": "Entry", "value": f"`{payload.entry:.2f}`", "inline": True},
        {"name": "Stop Loss", "value": f"`{payload.stop_loss:.2f}`", "inline": True},
        {"name": "Spread", "value": f"{payload.spread:.2f}" if payload.spread is not None else "n/a", "inline": True},
        {"name": "Take Profits", "value": tps or "—", "inline": False},
        {"name": "HTF Confirmation", "value": "\n".join(f"✓ {r}" for r in result.reasons) or "—", "inline": False},
        {"name": "News", "value": news.note, "inline": False},
    ]
    return {
        "username": "XAU/USD Swing Bot",
        "embeds": [
            {
                "title": f"🟢 GOLD {payload.direction.value} SIGNAL" if color == GREEN
                else f"🔴 GOLD {payload.direction.value} SIGNAL",
                "description": f"High-confidence multi-timeframe setup · {local_time}",
                "color": color,
                "fields": fields,
                "footer": {"text": "Decision support — not auto-executed. Manage your own risk."},
            }
        ],
    }


YELLOW = 0xF1C40F
GREY = 0x95A5A6


def build_news_notice(payload: "AlertPayload", news: "NewsVerdict", kind: str) -> dict:
    """A lighter embed for the news channel.

    kind="heads_up"  -> a signal passed but high-impact news is near.
    kind="suppressed"-> a signal was blocked by the news blackout.
    """
    if kind == "suppressed":
        title = "🚫 Signal suppressed — news blackout"
        color = GREY
    else:
        title = "⚠️ News heads-up near an active setup"
        color = YELLOW
    return {
        "username": "XAU/USD News Filter",
        "embeds": [
            {
                "title": title,
                "description": news.note,
                "color": color,
                "fields": [
                    {"name": "Would-be direction", "value": payload.direction.value, "inline": True},
                    {"name": "Exec TF", "value": payload.timeframe, "inline": True},
                    {"name": "Pattern", "value": payload.pattern, "inline": False},
                ],
                "footer": {"text": "Context only — no trade action taken by the bot."},
            }
        ],
    }


async def send_discord(webhook_url: str, embed_payload: dict) -> bool:
    if not webhook_url or "xxxx" in webhook_url:
        raise RuntimeError("DISCORD_WEBHOOK_URL not configured.")
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(webhook_url, json=embed_payload)
        r.raise_for_status()
    return True
