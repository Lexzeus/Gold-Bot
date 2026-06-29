"""
Signing proxy.

TradingView cannot compute an HMAC, so point your TradingView alert webhook at
THIS service instead of the backend directly. The proxy:

  1. Receives the raw TradingView JSON.
  2. Computes X-Signature = HMAC-SHA256(WEBHOOK_SIGNING_SECRET, raw_body).
  3. Forwards the body verbatim to the backend /webhook with that header.

Both processes share WEBHOOK_SIGNING_SECRET via the same .env / secrets.enc, so
the backend's header-HMAC path validates cleanly. The proxy is the only thing
exposed to the public internet; the backend can stay on a private network.

Run:
  uvicorn app.signing_proxy:proxy --host 0.0.0.0 --port 8080
Env:
  WEBHOOK_SIGNING_SECRET   shared secret (same as backend)
  BACKEND_WEBHOOK_URL      where to forward (default http://127.0.0.1:8000/webhook)
"""
from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, Request, Response

from .config import load_settings
from .security import compute_signature

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("signing-proxy")

cfg = load_settings(require_secrets=False)
BACKEND_URL = os.getenv("BACKEND_WEBHOOK_URL", "http://127.0.0.1:8000/webhook")

proxy = FastAPI(title="TradingView Signing Proxy", version="1.0.0")


@proxy.get("/health")
async def health() -> dict:
    return {"status": "ok", "forwards_to": BACKEND_URL}


@proxy.post("/tv")
async def sign_and_forward(request: Request) -> Response:
    raw = await request.body()
    if not cfg.webhook_signing_secret or "replace_me" in cfg.webhook_signing_secret:
        log.error("WEBHOOK_SIGNING_SECRET not configured on proxy.")
        return Response(content='{"error":"proxy misconfigured"}',
                        status_code=500, media_type="application/json")

    sig = compute_signature(cfg.webhook_signing_secret, raw)
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.post(
                BACKEND_URL,
                content=raw,
                headers={"Content-Type": "application/json", "X-Signature": sig},
            )
    except Exception as exc:
        log.error("Forward to backend failed: %s", exc)
        return Response(content='{"error":"backend unreachable"}',
                        status_code=502, media_type="application/json")

    log.info("Forwarded TV alert -> backend %s", r.status_code)
    return Response(content=r.content, status_code=r.status_code,
                    media_type="application/json")
