No execution or accounting discrepancies found in the six requested configurations. The following are research limitations, not newly discovered implementation defects:

- **High | experiments/tradingview/ORB/RESULTS.md:5 | Selected profit is not convincing evidence of edge | S3 has t = 0.47, two-sided unadjusted p = 0.638, before accounting for selection across 170 configurations | Keep the result exploratory; assess a frozen rule on genuinely unseen data.**
- **High | experiments/tradingview/ORB/optimizer.csv:2 | Profit is execution-cost fragile | Independent S3 replay with one tick adverse slippage on market/stop fills produces −$61,136.18 | Require profitability under executable costs before treating it as a candidate.**
- **Medium | experiments/tradingview/ORB/RESULTS.md:62 | Aggregate profit depends on one year | Removing 2022 changes +$7,573.82 to −$9,725.30 | Report year-removal sensitivity alongside the headline.**

Independent replication, trading days **2010-06-07..2026-10-27**, commission **$4.02 round trip**, zero slippage:

| Config | My trades | Claude trades | My net $ | Claude net $ | Net difference $ | Differing trades |
|---|---:|---:|---:|---:|---:|---:|
| S1 TP30 | 4,009 | 4,009 | −27,586.18 | −27,586.18 | 0.00 | 0 |
| S2 TP30 SL16 | 4,009 | 4,009 | −29,536.18 | −29,536.18 | 0.00 | 0 |
| S3 TP30 | 4,009 | 4,009 | 7,573.82 | 7,573.82 | 0.00 | 0 |
| S4 TP30 SL16 | 4,009 | 4,009 | −2,886.18 | −2,886.18 | 0.00 | 0 |
| S5 TP30 SL16 | 3,921 | 3,921 | −41,352.42 | −41,352.42 | 0.00 | 0 |
| S6 TP30 SL16 | 3,712 | 3,712 | −15,002.24 | −15,002.24 | 0.00 | 0 |

All **23,669 trades** match entry time, side, entry price, exit time, exit price and exit reason after mapping my descriptive `flat` label to Claude's `close` (`close_all(immediately)`). Per-trade net P&L and raw symbols also agree. There are no substantive first differences to diagnose. Before this label mapping, reason-only differences numbered 4/2/18/10/10/30 respectively; neither reading changes execution. For example, S1 on 2013-08-28 exits short at 109.42 at 20:09 UTC in both implementations, labelled `flat` versus `close`.

PF and intrabar maximum drawdown independently match Claude in every configuration; differences are zero at the displayed precision:

| Config | PF, mine = Claude | Max drawdown $, mine = Claude |
|---|---:|---:|
| S1 TP30 | 0.918609 | 40,618.31 |
| S2 TP30 SL16 | 0.906570 | 40,250.67 |
| S3 TP30 | 1.016431 | 25,532.99 |
| S4 TP30 SL16 | 0.992808 | 24,471.23 |
| S5 TP30 SL16 | 0.854316 | 44,032.13 |
| S6 TP30 SL16 | 0.951823 | 23,106.35 |

S3 red-team computations:

- **Noise:** mean net is $1.8892/trade. The unadjusted trade-level t-test gives t = 0.470, p = 0.638 and a 95% mean interval of **−$5.99 to +$9.77/trade**. A circular moving-block bootstrap over all 4,062 eligible days, including no-trade days (20,000 samples, seed 20261029), gives a 95% percentile interval for total net of **−$24,288 to +$39,456** with 5-day blocks, and **−$23,629 to +$40,378** with 20-day blocks. Centered one-sided bootstrap p-values are 0.319 and 0.320. These are unadjusted diagnostics under the stated sampling assumptions, not proof of a stationary process; even before selection adjustment, they do not distinguish the result from noise.
- **Years:** 2022 contributes **+$17,299.12**, exceeding the whole-history profit. Excluding it leaves **−$9,725.30**; excluding 2022 and 2016 leaves **−$19,908.34**. Only **6 of 17 calendar-year portions** are positive. The 2024+ subset nets **+$160.02** and was included in selection, so it is not an independent validation sample.
- **A few days:** the largest winner is **$335.98**. Removing the best 5, 10 or 20 trading days removes $1,519.90, $2,999.80 or $5,959.60 respectively, leaving profit positive. Removing the best **26 days** leaves **−$161.66**. Thus one or two huge wins do not explain it, but a small fraction of days can erase the entire margin. This deletion exercise is sensitivity analysis, not a significance test.
- **Costs:** the independent one-tick replay matches Claude exactly: **4,009 trades, −$61,136.18**, a **$68,710** deterioration. Entry fills, TP distances and exits were recomputed. Counting the supplied optimizer rows confirms **16/170** positive at zero slippage and **0/170** positive in its stress column; I did **not** independently replay all 170 stress configurations.
- **Rolls and overnight exposure:** no position in these replays spans raw symbols. S3's one specified `day_end` fallback is the 2020-06-30 long, 39.26 to 39.60, exiting 2020-07-01 00:00 UTC, **+$335.98**, still CLQ0. Removing it leaves **+$7,237.84**; it does not explain the profit, though it is an intraday-flat failure. S4 and S6 also reproduce that day's overnight fallback.
- **Selection:** the 170-row optimizer identifies S3 TP30 as the maximum. Ledger lines 48–53 record another **204** earlier ORB configurations; line 65 also records substantial earlier development exposure. This replication verifies arithmetic and execution under the frozen specs; it creates no new out-of-sample evidence.

Method: independent Python implementation at `/tmp/codex-orbtv/replicate.py`, analysis at `/tmp/codex-orbtv/analyze.py`, results at `/tmp/codex-orbtv/analysis.log`. No `market_tester` or strategy imports. Read 5,667,487 CL.v.0 bars, using Databento through **2026-10-13 23:59 UTC**, then only strictly later Massive bars through **2026-10-27 20:59 UTC**. Independently derived exclusions match all **158** dates and reasons: 119 shortened sessions and 39 incomplete ranges. Applied rev-2 path, gap, activation, bracket, confirmation, expiry and flat rules. No pytest, commits, data changes or edits to existing files.
