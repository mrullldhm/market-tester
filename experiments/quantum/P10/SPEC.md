# P10 — ten published or source-inspired day-trading rules on CL (spec, frozen before any run)

Rev 1 written 2026-10-19 before any code or data look. **Rev 2** (same day, still before any P&L was computed)
applies Codex spec review R-P10-1 (3 critical, 6 high, 4 medium, 1 low; all accepted). Items P101..P110, one
config each, **never tuned**. Dev period only (2010-06-07..2023-12-31) via `report.evaluate(kind="dev")` with
default gates and the $24.02 round trip. Holdout only per README (candidate below, clean Codex replication,
owner approval, `register_holdout`, one run).

A failure refutes **this CL intraday construction**, not the cited author's whole idea: most rows are
adaptations (column "project choices"). Only P101 and P103 transfer a published, tested intraday rule nearly
as written.

Why not ORB, VWAP trend/break, EMA cross, Crabel NR7/stretch, Fisher ACD, Larry Williams volatility breakout:
dead in LESSONS §3 (Williams breakout = Crabel stretch). Overlap that remains (Codex R-P10-1): P102 is an
open-anchored breakout with a VWAP exit (kin of ORB / VWAP break; new angle = time-of-day 14-day noise
bands); P110 is built from EMAs (kin of EMA x VWAP; new angle = MACD signal-line cross). Their deflated
Sharpe counts the related dead trials (below).

## Common rules (all ten)
- Bars: front month `CL.v.0`, 1-minute, ET clock. Signal read at the close of bar t, market entry at bar t+1
  open (engine). 1 lot. Engine fill rules: stop = market (gap fills at open), target = limit with 1-tick
  trade-through, same-bar stop+target = stop, EIA ±15 min entry blackout (all candidate times), no trade across
  a roll.
- Day session = bars 09:00..14:29 ET. Entry signals only on bars 09:00..13:59 ET (exception P104: 08:59).
  Every position is closed at the close of the first bar at or after 14:29 ET (causal; `flat_before` peeks
  at the next bar and is not used).
- "Prior day" = the last trading day before today that has day-session bars. Its H/L = day-session extremes,
  its close = the **exact 14:29 bar** close. If that bar is missing (early close) or the prior session's
  `raw_symbol` differs from the current bar's (roll), there is no prior day: no signal that needs it.
- 5-minute bars: only **complete** buckets (all five 1-minute bars of the contract present), stamped at the
  bucket-closing 1-minute bar (ET minute ending in 4 or 9). High/low = bucket extremes, close = closing bar
  close. 5-minute indicators run over the full Globex series (like a TradingView chart) and reset at every
  roll. Rolling means / extremes need a full window (no partial warm-up).
- Stops and targets: either a distance from the entry fill (`stop_ticks` / `target_ticks`) or an **absolute
  price level fixed at the signal bar** (`stop_price` / `target_price`, engine rev 2). If the entry open has
  already reached a level, the entry is skipped. Random-entry baseline moves a level as its distance from the
  real fill.
- Engine rev 2 also adds `exit_long` / `exit_short`: close-confirmed exit applied only to the position held at
  that close; with an opposite `side` on the same bar it exits, then reverses at the next open. Covered by the
  look-ahead check and the random-entry baseline.
- Confirmed EIA releases: `eia_release` column (bar starting at a `confirmed` row of
  `data/reference/eia_releases.csv`), added by `load_period`. The blackout still uses every candidate row.

- **Rev 3** (Codex code review R-P10-2, before any P&L): early-close sessions (no 14:29 bar) are flattened at
  the day's last bar (`Signals.flat_day_end`, session calendar like the roll close); P101/P103/P104 entries
  must fill at the exact next minute (`max_fill_delay_min=1`, else no trade); P102 sigma history uses every
  day with the exact 09:00 open and check bar on one contract (prior close / roll only gate today's signal);
  the batch runs on one code hash (`ledger.code_sha256`), refuses a logged item without a saved result and
  writes atomically; positive-year counts use every dev year as denominator.

## The ten

| Item | Family | Rule (source) | Source-fixed | Project choices | Counterparty hypothesis (falsifiable) |
|---|---|---|---|---|---|
| P101 | `im` | Intraday momentum (Gao, Han, Li & Zhou 2018, JFE). r1 = close(09:29) − prior close(14:29). Signal at 13:59 close, side sign(r1) (0 if r1 = 0), entry 14:00 open, flat 14:29 close. No stop/target. | first half-hour return predicts last half-hour, traded last half-hour | CL day session 09:00-14:30 instead of 09:30-16:00; prior close = 14:29 bar; all days incl. EIA days | Late-day traders who unwind against the morning move or trade on information late (Gao: contrarian day-trader unwinds and late informed trading) pay the morning-direction flow. |
| P102 | `noise_area` | Noise-area momentum (Zarattini, Barbon & Aziz 2024). open = 09:00 bar open (exact). Check bars = closes of 09:29, 09:59 .. 13:59. sigma_m = mean over the previous 14 trading days having that check bar of abs(close_m / open − 1). UB = max(open, prior close) × (1 + sigma_m), LB = min(open, prior close) × (1 − sigma_m). At a check bar: long if close > UB, short if close < LB. Exit long at a check bar if close <= max(UB, VWAP); exit short if close >= min(LB, VWAP). VWAP = day-session (from 09:00) volume-weighted typical price up to that close. Flat 14:29. | bands, 14-day lookback, 30-min checks, VWAP/band trailing exit, gap adjustment | CL session; 14:29 prior close; volatility sizing of the paper dropped (1 lot) | Faders and liquidity providers who sell moves beyond the usual intraday range are adversely selected when demand imbalance persists. |
| P103 | `eia_momentum` | EIA half-hour momentum (Wen, Indriawan, Lien & Xu 2023; USO). On a confirmed release day: s = sign(close of the bar 29 minutes after the release bar − open of the release bar), i.e. the first 30 minutes after release; signal at 13:59 close, entry 14:00, flat 14:29. Nothing on other days. | post-release half-hour return predicts the last half-hour on EIA days | CL instead of USO; CL last half-hour = 14:00-14:30; holiday releases (11:00/12:00) included | Traders who absorb inventory news slowly (Wen: gradual information incorporation on EIA days) keep pushing in the release direction into the settlement. |
| P104 | `gap_fade` | Generic gap fade at the NYMEX open. gap = close(08:59) − prior close(14:29). If abs(gap) >= 20 ticks: signal at 08:59 close, side −sign(gap), entry 09:00 open, target price = prior close, stop price = close(08:59) + gap (same distance beyond). Flat 14:29. | fade the overnight gap toward the prior close | 20-tick minimum, symmetric stop, 08:59 reference | A temporary overnight liquidity imbalance moves price; day-session liquidity providers are paid to reverse it. |
| P105 | `bollinger` | Bollinger Band reversion (bands from Bollinger 2001). 5m BB(20, 2.0), population SD. Long when the 5m close crosses below the lower band (previous 5m close >= its lower band); short mirror. Exit long when a 5m close >= middle band; exit short when <= middle. No stop. Flat 14:29. | BB(20, 2) | the whole trading rule (Bollinger gives the indicator, not this system), 5m bars | Symmetric overreaction: traders who chase moves outside the band pay a liquidity premium. |
| P106 | `rsi2` | RSI(2) (Connors & Alvarez 2008). 5m Wilder RSI(2) = EWM alpha 1/2 (adjust = False, from the first close change, at least 2 changes), RSI = 100 when the average loss is 0. Long when RSI2 < 10 and close > SMA200 and close < SMA5; short when RSI2 > 90 and close < SMA200 and close > SMA5. Exit long when a 5m close > SMA5; exit short when < SMA5. No stop. Flat 14:29. | RSI2 < 10 / SMA200 / SMA5 exit (long side) | 5m bars (source uses daily), symmetric short side, intraday flat | Symmetric overreaction: short-horizon panic sellers (and panic buyers on the short side) pay for liquidity. |
| P107 | `pivot` | Floor-trader pivot fade. From prior day H, L, C: P = floor((H+L+C)/3 + 0.5) ticks, R1 = 2P − L, S1 = 2P − H, R2 = P + (H − L), S2 = P − (H − L). Short on the first 1m bar of today's session with open < R1 <= high (no earlier session bar reached R1); target price P, stop price R2. Long mirror at S1 (target P, stop S2). A bar touching both R1 and S1 trades neither. Each side at most once a day. Flat 14:29. | pivot formulas | fading the first touch, P/R2 exits, day-session H/L/C | Breakout traders who buy widely watched levels are adversely selected when the level holds. |
| P108 | `turtle_soup` | Intraday adaptation of Turtle Soup (Raschke & Connors 1995, *Street Smarts*). Long: today's session low has traded below the prior day low, then the first 5m close back above it; stop price = today's session low so far − 1 tick. Short mirror on the prior high. Each side at most once a day. No target. Flat 14:29. | fade a failed breakout of a prior extreme, stop beyond the new extreme | prior **day** extreme instead of a 20-day extreme at least 4 sessions old; 5m confirmation; intraday flat | Breakout traders whose stops beyond yesterday's extreme fill into a move that fails are adversely selected. |
| P109 | `donchian` | Intraday adaptation of Turtle System 1 (Faith 2007, *Way of the Turtle*). 5m close > highest 5m high of the previous 20 bars: long; < lowest low of the previous 20: short. Exit long when a 5m close < lowest low of the previous 10; exit short when > highest high of the previous 10. Stop = 2N ticks from the fill, rounded half up, N = Wilder 20-bar ATR of 5m bars (EWM alpha 1/20 seeded with the first bar, full window; the first bar of a contract has TR = high − low). Opposite breakout reverses. Flat 14:29. | 20/10 channels, 2N stop | 5m bars instead of daily, one market, 1 lot (no N-based sizing or pyramiding), intraday flat | Discretionary faders lose on trend days; short-gamma hedgers amplify the move (they are not the counterparty). |
| P110 | `macd` | MACD(12, 26, 9) signal-line cross (Appel). 5m EMA12 − EMA26 (adjust = False), signal = EMA9 of it. Cross above: long (exit any short); cross below: short (exit any long). Flat 14:29. | MACD(12, 26, 9) | the whole always-in trading rule, 5m bars, intraday flat | None identified: a lagging public signal, so the prior is zero edge (control-like test). |

## Statistics and candidate rule (pre-declared)
Per item, `report.evaluate` gates as README (trades >= 300, net > 0, Romano-Wolf p, deflated Sharpe, random
entry p, walk-forward, 2019-23 block, 2-tick slippage, look-ahead), with **1,999 random-entry draws**.

Across the ten (`experiments/P10/run.py` summary):
- **Profitability family:** Holm over the ten one-sided block-bootstrap p-values (`romano_wolf_p_pick`, which
  is the bootstrap p for one config). NaN = fail.
- **Entry-quality family:** Holm over the ten random-entry p-values. NaN = fail.
- **Deflated Sharpe across the batch:** N = 10 trials with the variance of the ten per-day Sharpes; P102 adds
  the dead ORB (1,056,000) and VWAP-break (17) configs, P110 adds EMA x VWAP (6,144).
- **Concentration:** traded days >= 200; net $/trade still > 0 after removing the best calendar week; years
  with net > 0 reported.

**Candidate** = dev PASS and Holm profitability p <= 0.05 and Holm random-entry p <= 0.05 and batch DSR >= 0.95
and both concentration checks. Ties in Holm keep input order; p-values are not re-ranked after a run.

## Codex
R-P10-1 spec review (done, all findings applied above). Next: code review with a hashed test log, then
independent replication of trades, gross and net $/trade for all ten before any result is called real.
