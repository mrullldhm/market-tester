# Wickless S/R Retest (TradingView indicator)

Marks candles with no bottom wick (support) or no top wick (resistance), draws a level at that price, and
simulates a limit-order retest trade with a swing stop and a 1.1R target. A dashboard shows the result.
Exact rules: [`SPEC.md`](SPEC.md). Python port and parity check: [`crosscheck.py`](crosscheck.py).

**Read this first.** The dashboard is an in-sample backtest of whatever history the chart has loaded. It is
not evidence of an edge (`LESSONS.md` section 10: minute-bar indicator ideas on CL have not beaten the $24.02
round trip). On dev data (H1 2023, 5m, defaults) the Python port gives 1,345 trades at -$42/trade after
costs (about -$18/trade before costs). TradingView charts also show 2024+ bars, which is this repo's holdout
period: numbers read there are not a clean test.

## Install

1. TradingView: open a **CL** chart (`CL1!` or a CL contract). The script refuses any other root (MCL included).
2. Pine Editor: paste `wickless_sr.pine`, save, "Add to chart".
3. Timeframes: 1, 2, 3, 5, 10, 15, 20, 30 or 60 minutes. Any other timeframe stops the script with an error.

## What you see

| Mark | Meaning |
|---|---|
| Green ▲ below bar + volume | no bottom wick (green candle when directional) -> support at the low |
| Red ▼ above bar + volume | no top wick (red candle when directional) -> resistance at the high |
| Green / red line | live level (resting limit). Ends where it was touched, expired, gapped or reset |
| Green / red boxes | a trade: target zone and stop zone |
| TP / SL / EOS / GAP | exit: target, stop, session end (16:59 ET bar), next-day open (no 16:59 bar) |
| Ribbon / Supertrend lines | the filters (plotted even when the filter is off; toggle in inputs) |

## Main inputs

| Group | Input | Default |
|---|---|---|
| Detection | wick tolerance, min range, **directional**, volume label | 0 ticks, 1 tick, on, on |
| Levels | bars before retest, max age, max levels per side, reset each session | 1, 100, 20, on |
| Trade | swing left / right bars, stop buffer, max risk, **reward:risk**, flat at session end, contracts, cost | 5 / 2, 0, off, 1.1, on, 1, $24.02 |
| Filters | MA ribbon (EMA/SMA 8-13-21-34-55, Stacked or Price vs slowest), Supertrend (3, 10) | off, off |

Fill model (conservative, `SPEC.md` sections 3-4): entry needs a 1-tick trade-through of the level or fills at
the open on a gap; target needs a 1-tick trade-through; stop and target in the same bar = stop; on the entry bar
only the stop is checked. One trade at a time. Filters read the previous bar.

Alerts: support / resistance detected, retest entry, stop hit, target hit.

## Known limits

- `CL1!` is not back-adjusted and TradingView does not tell the script when the contract rolls. A trade or a
  level can straddle a roll jump on the chart, and the dashboard says `rolls not voided`. The Python port voids
  such trades; the indicator cannot. For clean numbers use a single contract chart (e.g. `CLZ2026`).
- Pine keeps only the newest 500 lines, labels and boxes. Old drawings vanish; the dashboard is unaffected.
- Holiday early closes have no 16:59 bar: an open trade exits at the next session's first open (GAP).

## Parity check (Pine == spec)

1. On the chart with the indicator, use the chart menu "Export chart data", keep the default columns
   (the hidden `x_det`, `x_entry`, `x_n`, `x_net`, `x_r` plots are included).
2. `.venv/bin/python pine/crosscheck.py tv --csv <export.csv> --tf 5` (add `--set name=value` for every input
   you changed, e.g. `--set useST=true`).
3. Expect `PARITY OK`: same detection, entry side, trade count, net $ and R on every closed bar (the export's
   last bar may still be forming and is skipped).

Databento check (dev period only): `.venv/bin/python pine/crosscheck.py run --start 2023-01-01 --end 2023-07-01`.
