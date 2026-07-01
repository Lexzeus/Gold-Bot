"""
FastAPI webhook receiver.

POST /webhook  — TradingView posts the alert JSON here. We:
  1. Authenticate (HMAC over raw body OR `sig` field, + optional token).
  2. Replay-guard via payload timestamp.
  3. Validate through the gate stack (pipeline.validate).
  4. Relay to Discord only if every gate passes.

Auth note: TradingView can't compute an HMAC header itself. Two supported paths:
  A) Put a relaying proxy in front that signs the body into `X-Signature`.
  B) Include `sig` in the JSON = HMAC-SHA256(secret, payload.stable_payload()).
Path B works with raw TradingView alerts and is what the Pine template uses.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import FastAPI, Header, HTTPException, Request

from .config import load_settings
from .pipeline import validate
from .relay import build_embed, build_news_notice, send_discord
from .schemas import AlertPayload
from .security import compute_signature, is_fresh, verify_signature, verify_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("gold-bot")

app = FastAPI(title="XAU/USD Swing Alert Bot", version="1.1.0")
cfg = load_settings(require_secrets=False)  # don't crash dev import; checked at use

if not cfg.webhook_shared_token or "replace_me" in (cfg.webhook_shared_token or ""):
    log.warning(
        "WEBHOOK_SHARED_TOKEN not set: /tv is protected only by TLS + replay window. "
        "Set a token (and match it in the Pine input) before exposing /tv publicly."
    )


@app.on_event("startup")
async def _start_scanner() -> None:
    """Launch the built-in market scanner (TradingView not required)."""
    if cfg.scanner_enabled:
        from .scanner import run_scanner

        asyncio.create_task(run_scanner(cfg, cfg.scanner_poll_seconds))
        log.info("Built-in scanner enabled — TradingView webhook not required.")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "symbol": cfg.symbol,
        "ny_session_only": cfg.ny_session_only,
        "scanner_enabled": cfg.scanner_enabled,
    }


async def _process(raw: bytes, x_signature: str | None) -> dict:
    """Core pipeline shared by /webhook (external sig) and /tv (self-signed)."""
    # --- refuse to run unconfigured (empty secret would make HMACs forgeable) ---
    if not cfg.webhook_signing_secret or "replace_me" in cfg.webhook_signing_secret:
        log.error("WEBHOOK_SIGNING_SECRET missing/placeholder — refusing alert.")
        raise HTTPException(status_code=503, detail="Server not configured.")

    # --- parse ---
    try:
        payload = AlertPayload.model_validate_json(raw)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Bad payload: {exc}") from exc

    # --- authenticate (header HMAC OR in-body sig over stable fields) ---
    header_ok = verify_signature(cfg.webhook_signing_secret, raw, x_signature)
    body_sig_ok = (
        payload.sig is not None
        and verify_signature(
            cfg.webhook_signing_secret,
            payload.stable_payload().encode(),
            payload.sig,
        )
    )
    if not (header_ok or body_sig_ok):
        log.warning("Rejected unsigned/invalid-signature alert.")
        raise HTTPException(status_code=401, detail="Invalid signature.")

    if not verify_token(cfg.webhook_shared_token, payload.token):
        raise HTTPException(status_code=401, detail="Invalid token.")

    # --- replay guard ---
    if not is_fresh(payload.timestamp, cfg.webhook_max_age_seconds):
        raise HTTPException(status_code=401, detail="Stale or replayed alert.")

    # --- validate ---
    result, news, local_time = await validate(payload, cfg)

    if not result.passed:
        log.info("SUPPRESSED %s %s: %s", payload.direction.value,
                 payload.timeframe, result.rejections)
        # If this was a news blackout and a news channel is configured, post a notice there.
        if news.blocked and cfg.discord_news_webhook_url:
            try:
                await send_discord(
                    cfg.discord_news_webhook_url,
                    build_news_notice(payload, news, "suppressed"),
                )
            except Exception as exc:
                log.error("News-channel notice failed: %s", exc)
        return {
            "status": "suppressed",
            "rejections": result.rejections,
            "passed_gates": result.reasons,
            "news": news.note,
        }

    # --- relay ---
    embed = build_embed(payload, result, news, local_time)
    try:
        await send_discord(cfg.discord_webhook_url, embed)
    except Exception as exc:
        log.error("Discord relay failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Relay failed: {exc}") from exc

    # Heads-up: signal passed but high-impact news is near (outside blackout).
    if news.flagged and not news.blocked and cfg.discord_news_webhook_url:
        try:
            await send_discord(
                cfg.discord_news_webhook_url,
                build_news_notice(payload, news, "heads_up"),
            )
        except Exception as exc:
            log.error("News-channel heads-up failed: %s", exc)

    log.info("RELAYED %s %s %s", payload.direction.value, payload.timeframe, payload.pattern)
    return {
        "status": "relayed",
        "passed_gates": result.reasons,
        "rr_targets": result.rr_targets,
        "news": news.note,
    }


@app.get("/scan")
async def manual_scan(token: str | None = None) -> dict:
    """Debug: run one scanner cycle now. Requires the shared token."""
    from .schemas import AlertPayload as _AP  # noqa: F401 (keep import graph warm)
    from .scanner import ScannerState, scan_once

    if cfg.webhook_shared_token and token != cfg.webhook_shared_token:
        raise HTTPException(status_code=401, detail="Invalid token.")
    try:
        # Fresh state: ignores the latch so you can always see current market read.
        out = await scan_once(cfg, state=ScannerState())
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}
    return out or {"status": "no_signal", "note": "No fresh structural break on the last closed 5m bar."}


@app.post("/webhook")
async def webhook(request: Request, x_signature: str | None = Header(default=None)) -> dict:
    """External entry: caller must supply a valid X-Signature header or in-body sig."""
    raw = await request.body()
    return await _process(raw, x_signature)


@app.post("/tv")
async def tradingview(request: Request) -> dict:
    """
    Single-service entry for raw TradingView alerts.

    TradingView can't compute an HMAC, so this trusted endpoint signs the raw
    body itself and runs the same pipeline in-process. This lets you deploy ONE
    web service (point TradingView's webhook here). Protection for /tv comes from
    the shared token + TLS + the replay window, exactly like the standalone proxy.
    """
    raw = await request.body()
    sig = compute_signature(cfg.webhook_signing_secret, raw)
    return await _process(raw, sig)


def make_sig_for(payload: AlertPayload, secret: str) -> str:
    """Helper for tests/tooling: compute the in-body sig for a payload."""
    return compute_signature(secret, payload.stable_payload().encode())
