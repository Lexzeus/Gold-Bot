# /ANALOGY — Liquidity Hunts at the New York Open vs. Low-Volume Sessions

Think of price as a **predator at a watering hole**.

During the **Asian session and the lunch lull**, the watering hole is quiet. A few
animals (retail orders) drink at the edges. A predator that shows up finds slim
pickings — not enough prey clustered together to justify a committed strike. Moves
are tentative, ranges are tight, and stop clusters are thin and scattered. An
algo "hunting" here expends energy for little reward, so it mostly waits. This is
why low-volume sessions chop: there isn't enough resting liquidity to fuel a
decisive expansion.

The **8:00 AM ET New York open** is the herd arriving at once. Overnight, orders
piled up: retail stops sit in obvious places — just under the Asian low, just
above yesterday's high, beneath the round number ($3,300, $3,350). To the
institutional algo, that stack of resting stop orders is a **pre-positioned
buffet of counterparty liquidity**. A fund that needs to *buy* a large position
can't just lift the offer — it would move price against itself. So it first
pushes price *down* into the obvious sell-stops below support: a **stop raid**.
Those triggered stops become market sell orders — exactly the liquidity the fund
needs to *absorb* and fill its large buy. Price sweeps the low (a **wick**), grabs
the fuel, then reverses and expands in the real intended direction.

The trap for retail: the initial spike *looks like* a breakdown. It's actually
the predator flushing the prey into the open before the strike.

**Why this maps directly onto our bot's design:**

- The **NY-session toggle (Rule #3)** is us choosing to hunt only when the herd is
  present. Outside the volume pocket, "breaks" lack the liquidity to follow
  through — so we mute execution triggers.
- The **BOS-by-body-close rule (`/STEELMAN`)** is how we tell the *raid* (a wick
  that sweeps stops and snaps back) from the *real move* (a body close that
  commits). The wick is the predator flushing prey; the body close is the kill.
- The **multi-timeframe filter** ensures we side with the predator. If the 1h/4h
  trend is up, a sweep of the lows at the NY open is most likely a buy-side
  liquidity grab — we want to be a buyer into it, not a panic seller.
