# XAU/USD (Gold) Multi-Timeframe Swing Alert Bot

A TradingView → backend → Discord pipeline that catches a Pine Script signal,
validates it against higher-timeframe trend, structural integrity, the New York
session, spread, and the economic news calendar, then pushes a clean,
contextualized alert to your phone via Discord.

**This is decision support. It does not place or auto-execute trades.**

---

## How it works

```
TradingView (Pine alert, XAUUSD)
        │   POST signed JSON
        ▼
FastAPI  /webhook   ── auth (HMAC + replay guard) ──┐
        │                                            │ reject 401 if bad
        ▼                                            ▼
   Validation gate stack (app/pipeline.py)      (logged, dropped)
   1. BOS body-close   (not a wick sweep)   ── /STEELMAN
   2. MTF agreement    (30m/1h/4h/1D ≥ 3/4) ── core filter
   3. Risk / R:R       (SL on protective side)
   4. NY session pocket (08:00–12:00 ET)    ── toggle, Rule #3
   5. Spread guard     (≤ MAX_SPREAD_USD)   ── Never #2
   6. News blackout    (CPI/NFP/FOMC ±30m)  ── Always #3
        │  all gates pass
        ▼
   Discord embed (app/relay.py)  →  your phone
```

## Project layout

```
app/
  main.py       FastAPI app + /webhook + /health
  config.py     env/secrets loader (.env → secrets.enc → os.environ)
  security.py   HMAC verify, replay freshness, token check
  schemas.py    AlertPayload contract + ValidationResult
  mtf.py        HTF agreement + BOS body-close gate
  sessions.py   NY session pocket (DST-aware)
  spread.py     spread guard + R:R math
  news.py       ForexFactory / AlphaVantage high-impact filter
  relay.py      Discord embed builder + sender
  pipeline.py   orchestrates the gate stack
  signing_proxy.py  public proxy: HMAC-signs raw TV body → backend
Dockerfile  docker-compose.yml   one-command deploy (proxy + backend)
pine/gold_swing_alert.pine   TradingView strategy that emits the JSON
scripts/encrypt_env.py       encrypt secrets → secrets.enc
docs/ANALOGY.md  STEELMAN.md  PREMORTEM.md
tests/test_pipeline.py       10 tests, all passing
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in real values
# generate a signing secret:
python -c "import secrets; print(secrets.token_urlsafe(48))"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Expose port 8000 publicly (e.g. a reverse proxy with TLS, or a tunnel) so
TradingView can reach `https://your-host/webhook`.

### Production secrets (encrypted, no plaintext keys in files)

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
export ENV_MASTER_KEY='<that key>'        # store in host secret manager, NOT repo
# put real values in secrets.json (gitignored), then:
python scripts/encrypt_env.py secrets.json
rm secrets.json                            # secrets.enc is decrypted at startup
```

## Deploy to Render (recommended, single service)

The app serves both `/webhook` and a self-signing `/tv` entry in one process, so
you can deploy a single web service. `render.yaml` is a ready blueprint:

1. Push this repo to GitHub (`.env` is gitignored and stays local).
2. In Render: **New → Blueprint** → connect the repo. Render reads `render.yaml`.
3. Fill in the prompted secrets (`WEBHOOK_SIGNING_SECRET`, `DISCORD_WEBHOOK_URL`,
   optional `DISCORD_NEWS_WEBHOOK_URL`, `WEBHOOK_SHARED_TOKEN`).
4. Render gives you `https://<service>.onrender.com`. Point TradingView at
   `https://<service>.onrender.com/tv`.

## Deploy (one command, with the signing proxy)

```bash
cp .env.example .env        # fill in WEBHOOK_SIGNING_SECRET + DISCORD_WEBHOOK_URL
docker compose up --build
```

This runs two services:

- **proxy** (`app/signing_proxy.py`, published on `:8080`) — the only thing exposed
  to the internet. TradingView posts to `/tv`; the proxy computes the HMAC over
  the raw body and forwards it to the backend with an `X-Signature` header.
- **backend** (`app/main.py`, internal `:8000`) — validates and relays to Discord.

Put TLS in front of `:8080` with your reverse proxy or tunnel, then point
TradingView at `https://your-host/tv`. The backend never touches the public net.

Run without Docker:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000          # backend
uvicorn app.signing_proxy:proxy --host 0.0.0.0 --port 8080  # proxy
```

## Built-in scanner (no TradingView needed)

Set `SCANNER_ENABLED=true` and the bot detects signals itself: it polls free
gold market data (Yahoo GC=F candles, rebased to spot via Swissquote's public
XAU/USD quote) every 5 minutes, runs the same EMA50 HTF bias + swing-pivot BOS
body-close logic as the Pine script, and pushes qualifying signals through the
identical gate stack to Discord. TradingView (and its paid webhook plan) is
entirely optional. Debug endpoint: `GET /scan?token=<WEBHOOK_SHARED_TOKEN>`
runs one cycle immediately and reports what it saw.

## TradingView side (optional alternative)

1. Add `pine/gold_swing_alert.pine` to a chart on **XAUUSD** (any execution TF: 1/5/15m).
2. Create an alert → Condition: the indicator → "Any alert() function call".
3. Webhook URL: `https://your-host/tv` (the **proxy**). Leave the message empty —
   the script builds the JSON itself.
4. Set the `Webhook shared token` input (and `WEBHOOK_SHARED_TOKEN` in env) to match.

> Auth paths: the backend accepts a valid `X-Signature` HMAC header **or** an
> in-body `sig` field. The signing proxy gives TradingView the header path (true
> body-HMAC) since TradingView can't HMAC natively. If you skip the proxy and post
> straight to `/webhook`, rely on the in-body `sig` + shared token + TLS + replay
> window instead.

## Alert JSON contract (Rule #2)

```json
{
  "symbol": "XAUUSD",
  "token": "…",
  "sig": "…",
  "timestamp": 1751200000,
  "timeframe": "5m",
  "direction": "BUY",
  "pattern": "ChoCh + Bullish Engulfing",
  "entry": 3300.0,
  "stop_loss": 3295.0,
  "take_profits": [3305.0, 3310.0, 3315.0],
  "htf_bias": {"tf_30m":"BUY","tf_1h":"BUY","tf_4h":"BUY","tf_1d":"SELL"},
  "spread": 0.20,
  "bos_body_close": true
}
```

## Configuration knobs (`.env`)

| Key | Default | Meaning |
|-----|---------|---------|
| `HTF_LOOKBACK_BARS` | `500` | Bars scanned for HTF supply/demand zones (Pine side) |
| `NY_SESSION_ONLY` | `true` | Mute triggers outside the NY volume pocket (Rule #3) |
| `NY_SESSION_START/END` | `08:00/12:00` | The pocket, anchored on the 8 AM ET open |
| `MAX_SPREAD_USD` | `0.50` | Suppress if spread wider than this (Never #2) |
| `NEWS_PROVIDER` | `forexfactory` | `forexfactory` (no key) or `alphavantage` |
| `NEWS_BLACKOUT_MINUTES_BEFORE/AFTER` | `30/30` | Blackout window around high-impact USD news |
| `WEBHOOK_MAX_AGE_SECONDS` | `120` | Replay guard |
| `HTF_REQUIRED_AGREEMENT` | `3` | How many of the 4 HTFs must agree (2=looser, 4=strict) |
| `DISCORD_NEWS_WEBHOOK_URL` | _(blank)_ | Optional 2nd channel for news flags |

**News channel:** if `DISCORD_NEWS_WEBHOOK_URL` is set, news-suppressed notices and
"heads-up" flags post to that channel instead of cluttering the main signal
channel; confirmed signals still go to `DISCORD_WEBHOOK_URL`. Leave it blank to
keep everything in one channel.

## Tests

```bash
python -m pytest -q      # 10 passing: auth, replay, BOS, MTF, spread, session, R:R, news
```

## Trading & strategy notes

- `docs/ANALOGY.md` — algorithmic liquidity hunts at the NY open vs. quiet sessions.
- `docs/STEELMAN.md` — why a true BOS needs a body close, not a wick sweep.
- `docs/PREMORTEM.md` — how spread/slippage in news windows can wreck 1m entries,
  and exactly which gate mitigates each failure.

## Safety & limitations

- Decision support only — you place and manage every trade.
- News calendar can be wrong/unavailable; the bot fails *open-with-flag* and labels
  the alert "news state unknown." Flip to fail-closed in `app/news.py` if you prefer.
- Spread quality depends on your feed; prefer a broker spread over an index proxy.
- Nothing here guarantees fills or profits. Markets gap.
