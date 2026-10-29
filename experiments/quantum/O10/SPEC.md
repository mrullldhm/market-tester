# O10: first-hour NY range breakout, 6 situations x SL/TP grid (spec, frozen before any run)

Rev 1 written 2026-10-29 before any code or data look. **Rev 2** (same day, before any real-data run) applies
Codex spec review R-O10-1 (7 high, 7 medium; all accepted, see the table at the end). **Rev 3** (same day,
still before any real-data P&L) applies Codex code review R-O10-2 (4 high, 3 medium; all accepted, second table).
**Rev 4** (same day, still before any real-data P&L) applies R-O10-3 (2 high, 2 medium; third table). Owner's rules, clarified
by the owner on 2026-10-29. Items O101..O106 (one per situation), family `orb`. **Dev period only** (trading
days 2010-06-07..2023-12-31). Holdout only if the O10 pick is a candidate (below), Codex replicates it, and the
owner approves: then that one frozen config through `prereg.open_run`, one look.

**Prior warning.** ORB is dead idea #2 in `LESSONS.md` §3 (1,056,000 configs, entries no better than random,
edge only 2010-13). Candle stops and limit pullback entries are variants of the same breakout idea. The dev
years were mined before (LESSONS §5): the Bonferroni correction below covers **the 204 O10 configs only**. It
does not undo earlier ORB selection or make dev years clean evidence. A dev pass is at most a lead for the
holdout and forward data, never "significant" globally.

## Owner clarifications
- Anchor = **09:00 ET** (NYMEX open; 21:00 MYT in EDT, 22:00 MYT in EST). DST comes from `America/New_York`.
- Owner's S3 and S7 are the same rule, so S7 is dropped. 6 situations.
- The SL grid **replaces the "16"**. In S2/S4 it caps the stop distance **set after the candle closes**: it does
  not cap the realized loss (no stop exists before then, and a gap can pass the stop). In S5/S6 it is the entry
  distance from the stop. S1/S3 have no SL parameter.
- Flat **14:30 ET**. One trade per day at most.

Grid: TP in {30, 31, 32, 33, 34, 35} ticks; SL in {15, 16, 20, 21, 25, 26, 30, 31} ticks.
S1, S3: 6 configs each. S2, S4, S5, S6: 48 each. **Total 204 configs**, all logged before data is read.

## Data and day eligibility
- Front month `CL.v.0` 1-minute bars, integer ticks ($0.01 = $10), via `periods.load_period(DEV)` (adds the
  ET clock, `trading_day`, `eia_block`).
- Range bars = ET minute 09:00..09:59. A day is eligible only if **all 60** range minutes have a bar (a missing
  minute could hold the true high or low) and the day has at least one window bar.
- **Early close** (rev 3): a frozen rule calendar (`orb_situations.short_sessions`, known in advance) lists
  shortened CL sessions: MLK, Presidents, Memorial, Juneteenth (from 2022), Independence (observed), Labor and
  Thanksgiving days, the day after Thanksgiving and Dec 24 on weekdays. They are not traded. A listed day whose
  bars still run past 14:29 is a calendar mismatch: the run stops.
- **Flat** (rev 3): close of the 14:29 bar; if that minute has no bar, a market exit at the open of the first
  bar after 14:29 (what a 14:29:59 market order would get), slipped. A scheduled full session with no bar from
  14:29 to 16:59 is a **data failure**: excluded and listed by date in RESULTS (dev check: one day, 2020-06-30).
- **Roll guard**: a `raw_symbol` change between 09:00 and 14:29 ET is a data-quality failure, not a filter: the
  run stops with an error (LESSONS §1: rolls never happen in US daytime). No day is dropped silently.
- Order window `W` = bars 10:00..14:29. A missing minute inside `W` has no trade, so nothing fills in it.

## Common order rules (long shown, short is the mirror: swap high/low, H/L, signs)
- **Touch bar** k = first bar in `W` with high >= H (long) or low <= L (short). A bar touching both = skip
  the day (order inside the bar is unknown). The first touch decides the side (OCO), for every situation.
- **EIA** (±15 min of any listed release, `eia_block`; rev 3 wording): if any blocked bar occurs from 10:00 up
  to and including the would-be fill bar (touch bar for S1-S4; S5/S6 fill bar, so the break candle and its 5m
  bucket are covered), all entry orders are cancelled for the day: no trade. No resubmission. Exits of an open
  position keep working through EIA.
- **Prices vs costs**: every level (TP, cap, stop distance) is measured from the **fill price before
  slippage**. Slippage is charged afterwards per market fill, so it never moves a level.
- **Stop order entry (S1-S4)**: fills on the touch bar at max(H, open_k) (gap open fills at the open). Market
  fill, slipped.

### Event precedence inside a bar with an open position (stop working)
1. Open at or beyond the stop: stop exit at the open (gap), slipped.
2. Else open beyond the TP: TP fills at the TP price (limit, never better), no slip. The open is the bar's
   first trade, so its order is known.
3. Else low <= stop (long): stop exit at the stop price, slipped. **Stop wins whenever both the stop and the TP
   are touched inside one bar.**
4. Else high >= TP + 1 (1-tick trade-through): TP at the TP price, no slip.
5. Else after the last window bar: flat exit (see Flat), slipped.

- **Costs**: fee **$2.01 per side** ($4.02 per round trip) on every trade; 1 tick ($10) slippage per market
  fill (stop entry, marketable S5/S6 entry, stop exit, flat exit). Two market fills = $24.02, one = $14.02,
  none = $4.02. Stress: 2 ticks per market fill.

## The six situations

| Item | Situation | Entry | Stop |
|---|---|---|---|
| O101 | S1 | buy stop at H | none during the touch bar; after it closes stop = low_k |
| O102 | S2 | buy stop at H | none during the touch bar; after it closes stop = max(low_k, entry - SL) |
| O103 | S3 (= owner S7) | buy stop at H | none until the clock-aligned 5m bucket holding k closes; then stop = min low of the bucket's 1m bars |
| O104 | S4 | buy stop at H | as S3, then stop = max(bucket low, entry - SL) |
| O105 | S5 | after the touch bar closes: stop = low_k - 1 tick, buy limit = stop + SL | stop at its price once filled |
| O106 | S6 | after the 5m bucket holding k closes: stop = bucket low - 1 tick, buy limit = stop + SL | same |

Rules per situation:
- **No-stop window (S1-S4)**: from the fill to the close of the touch bar (S1/S2) or of its 5m bucket (S3/S4)
  there is no stop. TP (rules 2 and 4) is checked on every bar of the window, including the fill bar: inside
  the fill bar price must reach H before TP, so a TP touch there comes after the fill. The stop works from the
  first bar after the window. If the window reaches the flat bar, exit at its close.
- **5m buckets** are wall-clock: [10:00, 10:05), [10:05, 10:10), ... A bucket is complete at its wall-clock end
  even if a minute is missing; its low/high = extremes of the bars present, including bars before the touch.
  The stop starts on the first bar at or after the bucket's end.
- **S2/S4 cap**: stop = max(candle low, entry - SL) (long). Owner's example: entry 100.00, low 98.00, SL 16
  gives stop 99.84 (owner wrote "98.4", a typo). If the next open is already at or below 99.84 the exit is at
  that open (rule 1): the loss can exceed SL. Reported: share of trades losing more than SL + slippage, and the
  worst adverse excursion during the no-stop window.
- **S5/S6** (orders submitted at the break candle's close, working from the next bar to the flat bar):
  - First bar after submission (j = e + 1): if open_j <= limit, the buy limit is marketable on submission:
    fill at open_j, slipped.
  - Otherwise, or on later bars: a resting limit fills at the limit price when low_j <= limit - 1 (1-tick
    trade-through; an open below the limit also fills at the limit, never better), no slip.
  - On the fill bar: if open_j <= stop, the bar gapped through both levels: stop exit at open_j, slipped
    (the loss is kept, never voided). Else if low_j <= stop: stop exit at the stop, slipped. TP is not checked
    on the fill bar (order inside the bar unknown); it works from the next bar with the precedence table.
  - Not filled by the flat bar: no trade. Break candle closing on the flat bar: no trade.
- Stop distance 0 (entry = candle low, S1-S4) is allowed; the stop-distance distribution is reported.
- Stop distance (rev 4) = side × (entry - stop), signed, of the stop the rules set, also when the TP filled
  before the stop would have been placed (the candle is known then; nothing about the exit is used). It is
  negative only when an S5/S6 marketable fill is already beyond the stop (a breached stop).
- Signal bar (rev 4) = the bar whose close makes the whole signal (side, entry, stop) known: the touch bar
  (S1/S2), the 5m bucket's last bar (S3/S4), the fill bar (S5/S6).

## Stage 1: blind power check (before any P&L)
For each situation at TP 30 / SL 16 (S1/S3: TP 30): the entries only (`orb.entries`: day, side, fill bar, stop
distance; the simulator runs no exit code) and the per-trade sd of net $ from a **variance model**
(`orb.variance_draws`, 20 draws, rev 4): each entry is moved to a week-shuffled day at its fill minute, market
entry at that bar's open, with the situation's own exit timing (S1-S4: no stop until that bar / its 5m bucket
closes, then the stop at the entry's stop distance; S5/S6: stop on the entry bar, TP from the next) and the
entry's stop distance. This keeps the unprotected no-stop exposure. Validated on path-consistent zero-edge bars:
model sd / strategy sd = 0.96-1.10 for all six (unit test bound 0.8-1.25). Entries with a breached stop (< 0)
are left out of the model. The real entries' own outcome is never computed. Minimum detectable net edge
MDE = (z_{1-α} + z_{0.8}) · sd / √n with α = 0.05 / 204. **Proceed** to stage 2 if MDE <= $24.02/trade (one
round trip) for at least 4 of 6 situations; else stop, log FAIL (underpowered). A `decision` START row is logged
before data is read and a result row after. The PROCEED is bound to one SHA-256 over this spec, every
`market_tester` module and `experiments/O10/run.py`; stage 2 refuses a PROCEED from another bundle.

## Stage 2: grid run and statistics (pre-declared, frozen)
- Day P&L series per config over every eligible dev day (0 on no-trade days), time order.
- **O10 pick** = config with the highest per-day Sharpe of day P&L over all 204 (no plateau: the grid is not one
  product). Ties: first in grid order (`orb.grid()`). Runner-ups are never promoted after the pick fails.
  Per-situation bests (same objective inside each situation) are descriptive only.
- **Profitability p**: one-sided stationary block bootstrap of mean day P&L > 0 (`stats.bootstrap_p`,
  49,999 draws, mean block 5 days, seed = config index, p = (k + 1) / (B + 1)). Bonferroni p = min(1, 204 p);
  gate <= 0.05 (raw p <= 0.000245). Romano-Wolf (`stats.romano_wolf`, 1,999 draws, mean block 5, seed 0) is
  reported. DSR is not used (LESSONS §6.12). The dead ORB count (1,056,000) is reported, not used.
- **Entry-quality benchmark** (rev 3; README "same week, minute, side, exits"). It tests the entry signal
  apart from the execution: the profitability gates above use the real S1-S6 fills. Each pick trade gives a
  signal (day, ET minute of its **signal bar**, side, stop distance). Real version: enter at the open of the
  next wall-clock minute after the signal minute on the same day (that minute must have a bar; a 14:29 signal
  has none). Shuffled version: the same on another eligible day of the same Mon-Sun week
  (`baseline.week_permutation`, pandas `W`). Both are market entries (1 tick) with the stop at the trade's stop
  distance and the TP working from the entry bar (precedence table), flat as above, same costs. A version is
  dropped when its signal minute or the next minute has no bar, a blocked EIA bar comes at or before its
  entry bar, or the stop distance is <= 0 (breached or zero: no signal question; count reported). A draw is valid if it keeps >= 50% of the trades; p = (1 + #valid draws with mean >= real) /
  (1 + #valid draws); p is NaN (gate fails) if the real version keeps < 50% or fewer than 90% of the 999 draws
  are valid. Seed 0. Calibration: path-consistent zero-edge bars give p spread over (0, 1) (unit test).
- **Walk-forward**: anchored, `optimize.walk_forward` on the 204 day-P&L matrix with the same objective (no
  plateau), 3 training years minimum (first test year 2013). Gate: OOS total net > 0.
- **PBO**: `stats.pbo_cscv` over all 204 (per-day Sharpe selector = the pick objective), 16 blocks,
  max 20,000 splits, seed 0. Gate: PBO <= 0.3.
- **Look-ahead**: unit truncation test plus a real-data check: trades of the pick before 2017-01-01 unchanged
  when bars from 2017-01-01 on are removed.
- **Candidate** = the O10 pick passes all of: trades >= 300; net $/trade > 0 at 1 and at 2 ticks per market
  fill; Bonferroni p <= 0.05; entry-quality p <= 0.05; walk-forward OOS net > 0; PBO <= 0.3; net > 0 in the
  2019-2023 block with >= 100 trades there; net > 0 after removing its best calendar week; look-ahead unchanged.
  Any statistic that cannot be computed (NaN) fails its gate.
- No candidate: O10 is logged FAIL, `LESSONS.md` §3 gets a row, the holdout stays closed.

## Outputs (`experiments/O10/run.py`)
Per config: trades, win %, gross and net $/trade, profit factor, max drawdown, net per year (all 204,
`data/o10/configs_by_year.csv`), net by side, exit reasons, stop-distance quantiles, share of losses beyond SL,
no-stop-window adverse excursion (p90 and worst). Per situation: its day-Sharpe best (frozen objective), its
max net $/trade (labelled), and its median config (the situation without picking).

## Codex gates
1. Spec review (rev 1 done: R-O10-1). Rev 2 re-check before the real-data run.
2. Code review of `market_tester/orb_situations.py`, `experiments/O10/run.py` + tests, given a hashed test log.
3. Blind replication with Codex's own code: S1-S6 at TP 30 / SL 16 and the O10 pick: trade count, net
   $/trade, net by year.

## Rev 2 changes (Codex R-O10-1)
| # | Sev | Change |
|---|---|---|
| 1.1 | High | S5/S6 gap through limit and stop keeps the loss (fill at limit or open, stop exit at open); no void |
| 1.2 | High | fee is $2.01 per side; levels from the pre-slip fill price |
| 1.3 | High | random baseline = causal same-week, same-minute, same-side benchmark |
| 1.4 | High | early-close sessions not traded (calendar); every traded day runs to 14:29 |
| 1.5 | High | gates restored: walk-forward, PBO <= 0.3, look-ahead |
| 1.6 | High | Bonferroni labelled O10-only; prior ORB selection stated |
| 1.7 | High | EIA: no fill on blocked bars; entry orders cancelled, exits keep working |
| 1.8 | Med | SL caps the later stop distance, not the realized loss; reported |
| 1.9 | Med | one event-precedence table |
| 1.10 | Med | 60/60 range minutes; wall-clock bucket ends |
| 1.11 | Med | daytime roll = hard error, not a filter |
| 1.12 | Med | one frozen pick objective (per-day Sharpe), PBO on the same selector, no runner-ups |
| 1.13 | Med | draws, blocks, seeds, p formula, week convention frozen; NaN fails |
| 1.14 | Med | stage 1 blind power check |

## Rev 3 changes (Codex R-O10-2)
| # | Sev | Change |
|---|---|---|
| 2.1 | High | EIA: any blocked bar from 10:00 to the fill bar cancels the day (S6 bucket included); benchmark versions too |
| 2.2 | High | benchmark = entry-quality test, real and shuffled signals both enter at the next open, same exits and costs |
| 2.3 | High | benchmark validity rules (>= 50% kept per draw, >= 90% valid draws), NaN p otherwise; one result schema |
| 2.4 | High | power path reads entries only (`orb.entries`, exits never run); real outcome never computed |
| 2.5 | Med | frozen rule calendar for short sessions; flat at the next open if 14:29 is missing; outages listed |
| 2.6 | Med | PROCEED bound to a spec + code + runner hash; START row before data |
| 2.7 | Med | per-config yearly export, worst no-stop excursion, Sharpe-best per situation |

## Rev 4 changes (Codex R-O10-3)
| # | Sev | Change |
|---|---|---|
| 3.1 | High | benchmark uses the signal bar (S3/S4: the 5m bucket's last bar), so no stop comes from later bars; intraday causality test |
| 3.2 | High | stage-1 sd from a separate variance model with the delayed-stop exposure, validated against the strategy's own sd on zero-edge paths |
| 3.3 | Med | benchmark entry = the next wall-clock minute, which must exist |
| 3.4 | Med | signed stop distance; breached / zero stops left out of the benchmark, count reported |

## Cost change (2026-10-29, owner)
Baseline cost is now the TopstepX CL commission only: $4.02 round trip per contract ($2.01 per side), slippage
0. The slippage stress gate is 1 tick per side (was: 1 tick baseline, 2-tick stress). Results before this date
used $24.02; they are archived next to the new ones. Ledger decision rows record the re-run.
