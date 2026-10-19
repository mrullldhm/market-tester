# Lessons from the old `propfirm` repo

Carry-over from the previous research (2026-10-14 .. 2026-10-18). Everything else was deleted; the full
old repo (code, research notes, reports, git history) is in `~/archive/propfirm-2026-10-18.tar.gz` and on
GitHub `mrullldhm/trading_bot`.

**Bottom line:** 8 retail-style strategies (~1.4M configurations) and 5 settlement-window hypotheses on CL
found **no edge that survives costs**. Old goal was passing a Topstep 50K Combine; the new goal is a
profitable CL strategy first.

Grades: **CONFIRMED** = checked independently (second vendor, official data, Codex replication or out of
sample). **EXPLORATORY** = in-sample only, often best of many tries; a lead, not a fact. **REFUTED** =
failed out of sample.

---

## 1. Data traps

| Fact | Grade |
|---|---|
| Bars are Databento 1-minute OHLCV, `CL.v.0` (front) and `CL.v.1` (second), 2010-06-07 .. 2026-10-13 UTC, ~10.4M rows, all on the $0.01 grid | CONFIRMED |
| Prices are **not back-adjusted**. When `raw_symbol` changes, the continuous price jumps (e.g. 2026-05-18 CLM6 to CLN6: −417 ticks = fake −$4,170 on 1 lot). Never count a roll jump as P&L | CONFIRMED |
| Rolls (196 in 16.3 years) happen mostly at 00:00 UTC, else at a session reopen (22:00/23:00 UTC), one old 01:15 UTC case. Never in US daytime | CONFIRMED |
| A trade must not span two `raw_symbol`s. Indicators should reset (or be rebuilt per contract) across a roll. You only see a roll after it happens: not a live signal | rule |
| `CL.v.0` = most volume the **previous day** (Databento decides the roll) | CONFIRMED |
| Daily closes match official WTI spot (EIA/FRED) 2010-21: return corr 0.912, median gap $0.03; biggest gaps on roll days | CONFIRMED |
| Second vendor (Massive, 2024-10 .. 2026-10): OHLC exact 99.93-99.99%, close exact 98.2%. Do not build rules on one exact 1-minute close | CONFIRMED |
| Volume spikes at 09:00 ET, Wed 10:30 ET (EIA) and 14:28-14:30 ET (settlement) line up in summer and winter: time zones and DST are right | CONFIRMED |
| Stage-1 extras (2015-16 only): CL TAS trades, CL mbp-1 slice 14:20-14:35, settlement statistics, RB/HO/BZ bars. Plain CL trades before 2017 lack aggressor side on ~7% of volume; record counts halve 2015 to 2016 (aggregation change): never use record counts as a feature | CONFIRMED |

## 2. Costs and fills

- 1 CL round trip = **$24.02**: $4.02 fee + 1 tick ($10) slippage per side. Random 60-minute trades lose
  about that: −$26.99 vs −$24.02 expected over 19,949 trades, z −1.06 (statistically consistent). CONFIRMED.
- One extra tick per side kills most edges (profitable configs: EMA 40 to 3, ORB 5,365 to 312). EXPLORATORY.
- Fill rules used: when a minute touches both stop and target, assume the stop (against us); a limit target
  needs a 1-tick trade-through; block entries ±15 min around EIA releases (`data/reference/eia_releases.csv`).
- Zero-edge trade noise on 1 CL (2010-16): 14:35-16:05 sd 22 ticks, 14:00-14:30 37, 09:00-10:00 49. Short quiet
  windows need 4-8 net ticks per trade to matter; holding 09:00-16:05 needs 32+. CONFIRMED.
- 1-60 minute returns mean-revert (variance ratio 0.78-0.94): mostly bid-ask bounce on last-trade prices,
  not tradeable. Variance ratios must use a filled minute grid. CONFIRMED.

## 3. Dead ideas (do not re-test without a genuinely new angle)

Dev period 2010-06 .. 2021-12 unless stated. Costs as section 2.

| # | Idea | Configs | Result | Why it failed |
|---|---|---|---|---|
| 1 | EMA x VWAP cross | 6,144 | +$24/trade in-sample, **−$33/trade on 2022+** | REFUTED out of sample |
| 2 | Opening-range breakout (ORB) | 1,056,000 | +$20/trade, PF 1.08 | entries no better than random; earned only 2010-13 |
| 3 | ZAB first-candle + relative volume | 3,456 | +$26/trade | entries no better than random |
| 4 | Crabel stretch, NR4/NR7/inside day | 7,200 | +$85/trade, PBO 52.9% | entry edge only 2010-15; overfit risk |
| 5 | Fisher ACD | 2,880 | +$45/trade in-sample, **−$27 vs random on 2022+** (p 0.85) | REFUTED: best-of-2,880 selection luck |
| 6 | "CL VWAP Break" bot (published) | 1 + 16 | −$40/trade 2010-26, every year negative | loses even at zero cost |
| 7 | Momentum Pullback candle | 233,280 | −$2.6/trade | loses before costs |
| 8 | TradingView "1-Minute Momentum" Pine | 81,000 | −$6.2/trade | loses before costs |
| H025 | Settlement continuation (14:25-14:34 move to 16:05), big-move filter | 1 family | 5.85 gross ticks, decays 9.5 / 5.9 / 1.6 by block | below cost in 2015-16 |
| H026 | Post-settlement short 14:30 to 15:00 | 1 | 2.25 gross ticks, positive every block | −0.16 net |
| H027 | TAS / signed flow at settlement (2015-16) | 4 | wrong-signed or ~0 | no edge |
| H028 | RB/HO/BZ vs CL at settlement (2015-16) | 3 | −0.04 to −0.65 gross ticks | no edge |
| H033 | Top-of-book queue imbalance at 14:29 | spec only | never run | reset before run |
| S003 | ML ceiling: ridge / elastic net / boosted trees on ~25 OHLCV features, 8 times x 6 horizons | 432 | 0 significant after costs (Romano-Wolf min p 0.96) | rules out ~8+ tick edges at those times only |
| P101 | Intraday momentum (Gao 2018), first half-hour -> 14:00-14:29 | 1 | -$18.45/trade, gross +$5.57 | gross edge < cost |
| P102 | Noise-area momentum (Zarattini 2024) | 1 | +$5.14/trade, p 0.34, 2019-23 -$17.20 | entries beat random (Holm p 0.04) but edge < 1 round trip; decayed |
| P103 | EIA half-hour momentum (Wen 2023) -> last half-hour | 1 | -$17.29/trade | gross edge < cost |
| P104 | Gap fade 08:59 vs prior 14:29, >= 20 ticks | 1 | -$14.59/trade | gross edge < cost |
| P105 | Bollinger(20,2) 5m reversion | 1 | -$34.71/trade, gross -$10.69 | loses before costs |
| P106 | RSI(2) 5m (Connors) | 1 | -$20.63/trade | gross edge < cost |
| P107 | Floor pivot R1/S1 fade | 1 | -$49.16/trade, gross -$28.29 | loses before costs |
| P108 | Turtle Soup intraday (prior-day extreme) | 1 | -$20.02/trade | entries beat random (Holm p 0.025) but edge < cost |
| P109 | Donchian 20/10 5m + 2N stop | 1 | -$13.35/trade | gross edge < cost |
| P110 | MACD(12,26,9) 5m cross | 1 | -$14.26/trade | gross edge < cost |
| T101 | Term structure (front-second spread) as intraday direction, 09:00-14:29 | 0 (dropped at spec) | not run | carry is paid to a spread position, not the outright; no cost basis |
| T102 | Spread lead: front 08:59-09:59 residual on second month, \|r\|>=σ, hold to 14:29 | 0 (not run) | power: 80% only at 16 net ticks | underpowered (847 trades, ~150-tick noise) |
| T201 | Spread lead, every eligible non-EIA day, hold 10:00-10:59 | 0 (not run) | power gate FAIL: D alone 70% at 4 net ticks | underpowered; family `spread_lead` closed (experiments/T10, T20) |

P101-P110: dev 2010-06..2023-12 only, one frozen config each, replicated by Codex (`experiments/P10/`).

Walk-forward (pick each year from earlier years only) never found a qualifying config for strategies 1-8.

## 4. Leads (in-sample, weak, treat as hypotheses)

- Anchors 09:00 ET (NYMEX open) and London 08:00 beat 08:30 ET; 18:00 ET Globex open was worst.
- Longer timeframes / ranges, close-confirmed entries, wide stops, holding to session end looked better.
- Short side looked better 2010-21, but that period holds the 2014-15 and 2020 crashes (likely drift).
- Settlement continuation is real but decays and sits below cost; post-settlement drift is real but below cost.
- Edges decayed after ~2015: always check the latest years separately.
- Literature not yet tested here: intraday momentum (first half-hour predicts last half-hour; Baltussen 2021,
  Wen et al. 2023 on crude), EIA surprise reaction lasting ~25 min (Li & Geman 2018).

## 5. Data already seen (holdout contamination)

Deleting code does not un-see data. Honest status:

| Period | Status |
|---|---|
| 2010-06 .. 2016-12 | heavily mined (all discovery, S001-S003, H025-H028) |
| 2017-01 .. 2021-12 | mined by strategies 1-8 dev runs (grids up to 1M configs). Not clean |
| 2022-01 .. 2026-10 | touched by EMA x VWAP holdout, ACD signal test and VWAP Break full run (P&L by year). **Quasi-clean**: fine for a new, unrelated idea, not for EMA/ACD/VWAP-break variants |
| after 2026-10-13 | never seen. Only truly clean data = future data (forward / paper trading) |

T10/T20 (2026-10-19) touched dev only: second-month signal inputs and unsigned 10:00-14:29 / 10:00-10:59 move
sizes. No signed strategy P&L, no holdout.

**Rule for market-tester:** holdout fixed 2026-10-18 before any test: dev = trading days 2010-06-07 .. 2023-12-31,
holdout = 2024-01-01 .. 2026-10-13 (`periods.py`, ledger D001). Every look is a ledger row; one run per prereg.

## 6. Method lessons

1. **Selection luck is real.** Best of thousands in-sample (ACD) was worse than random on new data. Prefer
   few pre-registered tests over big grids; count every configuration tried.
2. **Compare entries with random entries** (same minute, same side, same weeks). Most "edges" were not.
3. **Plant a fake edge** to prove the pipeline can find one (old pipeline found ~$150/trade drift).
4. **Look-ahead checks:** truncation test (cut future bars, result must not change) and scramble test.
5. **PBO / walk-forward** to measure overfitting; report every year separately.
6. **Power:** 5 out-of-sample years find an 8-net-tick edge 90% of the time, 4 ticks 15%, 1-2 ticks never.
   A null result only rules out big edges.
7. **Codex blind replication** caught real bugs: replicate headline numbers with independent code.
8. A tuned 2.5-month backtest says nothing (VWAP Break claimed +$40,878, lost every year over 16 years).
9. Micro contracts (MCL) were already out of scope and are now **banned** (non-syariah for owner).
10. **Stop intraday indicator searches** (decided 2026-10-19, Claude + Codex). 18 strategies: gross edges
    $3-11/trade vs $24.02 cost; real-but-small entry edges (P102, P108) cannot pay it. Codex odds for another
    1-60 min OHLCV indicator passing: low single-digit %. Owner needs **intraday only** (prop-firm target), so
    next tests must stay intraday but be low-turnover: few trades, long holds within the session (expected move
    far above $24), signals from slower information (e.g. CL.v.0/CL.v.1 term structure, prior days) rather than
    minute-bar indicators. Order flow / POC is weakly testable (2015-16 mbp-1 slice only); "watch the tape" is not testable.
11. **Power wall (2026-10-19, Claude + Codex, T10/T20).** On dev 2010-2023 a 1-hour CL hold has ~53-tick noise
    per trade; with ~1,800 trades and α 0.002 the smallest certifiable edge is ~4.6 net ticks nominal, 5-6 in
    practice ($50-60 net, $74-84 gross per trade); the 2.8-year holdout needs ~8-10, one year of new data ~12.
    Every edge seen so far was 0.3-1.1 gross ticks. Run a blind power check (signal inputs + unsigned moves only)
    before any P&L; it saved two trials here.
12. **Global DSR as built is mis-specified** (T20 R-T20-5): the tested strategy's own Sharpe sat in its own variance
    pool, and P101-P110 (cost-driven negative Sharpes) are not a null distribution. Next time: one primary global
    correction (Bonferroni p), DSR descriptive only, or a null pool simulated with matched frequency and hold.

