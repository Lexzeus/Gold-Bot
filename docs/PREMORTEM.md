# /PREMORTEM — How spread widening & slippage on XAU/USD during major news wrecks 1m entries

**Premise:** It is six months from now. The bot has been firing 1-minute execution
entries straight through news windows, and the account is bleeding despite a "good"
win rate on paper. Here is the autopsy of *how* that happened — so we design it out.

### Failure 1 — The spread eats the edge before price even moves
On a calm 1m XAU/USD entry, spread might be $0.10–$0.20. In the seconds around an
8:30 AM CPI print, market makers pull quotes and spread blows out to $2–$8. Our
1m setup risks, say, $3.00 to target $6.00. If we get filled across a $4 spread,
we start the trade already underwater by more than our intended risk. The edge was
real; the *execution cost* deleted it.
- **Mitigation built in:** `app/spread.py` rejects any signal where `spread >
  MAX_SPREAD_USD` (default $0.50), and **fails closed on 1m if no spread reading
  is supplied**. No spread data on the fastest, most slippage-sensitive TF = no fire.

### Failure 2 — Slippage past the stop ("the stop that wasn't")
A stop-loss is a *request* to exit at a price, not a guarantee. During an NFP
spike, price can gap $5–$15 with no liquidity in between. The stop fills far
beyond its level — a $3 planned loss becomes a $12 realized loss. Do that a few
times and a month of gains is gone.
- **Mitigation built in:** the **news blackout** (`app/news.py`) blocks signals
  in a ±30 min window around high-impact USD releases, so we're not *entering*
  into the exact conditions that produce gap-through stops.

### Failure 3 — The whipsaw fakeout
News spikes one way, triggers a 1m "BOS," then violently reverses. The 1m entry
is caught on the wrong side at the worst price.
- **Mitigation built in:** **body-close BOS** (`/STEELMAN`) rejects the initial
  wick spike, and **multi-timeframe agreement** keeps us aligned with the 1h/4h
  trend rather than the news candle's first lurch.

### Failure 4 — Clock/timezone drift around DST
The "8:00 AM ET" pocket and the news calendar are timezone-sensitive. A naive UTC
offset breaks twice a year at DST, silently shifting the blackout and session
window by an hour — right across the most dangerous releases.
- **Mitigation built in:** `app/sessions.py` uses `pytz`/`America/New_York` (DST-
  aware), not a fixed offset.

### Failure 5 — Stale/duplicate alerts replayed
A retried or replayed webhook fires an entry minutes late, into a moved market.
- **Mitigation built in:** `app/security.py:is_fresh` rejects alerts older than
  `WEBHOOK_MAX_AGE_SECONDS` (default 120s).

### Residual risk we accept (and you should know)
- The news calendar can be **wrong or down**. We fail *open-with-flag* (trade
  proceeds but the Discord embed says "news state unknown"). If you want
  fail-closed during US sessions, flip that in `app/news.py`.
- Spread in the payload is only as honest as your data feed. Garbage in, garbage
  gate. Prefer a broker feed spread over an index proxy.
- **None of this guarantees fills or profits.** This is decision support; you still
  place and manage the trade.
