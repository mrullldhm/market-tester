# TradingView tester: semantics (rev 2, 2026-10-29)

Rev 2 applies Codex spec review (5 high, 6 medium; table at the end).

Emulates the TradingView Strategy Tester (Pine v6 broker emulator) on Databento `CL.v.0` 1-minute bars.
Results are **"TV execution semantics on Databento CL.v.0"**, not `CL1!` data parity: TradingView's roll
dates and bars can differ. Separate from the quantum tester: imports only `market_tester.common`.

## Frozen settings (as `strategy()` arguments)
| Setting | Value |
|---|---|
| `initial_capital` | 50,000 USD |
| `default_qty_type` / `default_qty_value` | fixed, 1 contract (CL, $10 per $0.01 tick, 1,000 bbl) |
| `commission_type` / `commission_value` | cash per contract, $2.01 per filled order ($4.02 round trip, TopstepX CL) |
| `slippage` | 0 ticks (report adds a 1-tick stress: 1 tick worse on every market and stop fill) |
| `pyramiding` | 0 (one position) |
| `calc_on_every_tick`, `calc_on_order_fills`, `process_orders_on_close` | false, false, false |
| `margin_long`, `margin_short` | 0 (no margin check, no margin calls) |
| `backtest_fill_limits_assumption` | 0 ticks (limit fills on touch) |
| Bar Magnifier | off |

## Script cycle
The script runs once at the **close** of every bar. It sees that bar's OHLC, the position and fills up to
that close. Orders it submits work from the **next** bar. `close_all(immediately=true)` fills at this bar's
close. Every other order waits for the next bar.

## Broker emulator (per bar, orders submitted at earlier closes)
1. **Intrabar path**: open -> high -> low -> close if `|high - open| < |open - low|`, else open -> low -> high
   -> close. The tie (`|high - open| == |open - low|`) goes low first: **unverified convention** (TV docs define
   only the strict cases) until a TradingView fixture settles it. Bar Magnifier off.
2. **At the open** (first path point): a buy stop at or below the open, a sell stop at or above it, a buy limit
   at or above it, a sell limit at or below it, and any market order fill at the **open**. This covers gaps
   and orders that were already marketable when submitted (a buy limit above the close fills at the next open).
3. **Along each segment** (open->x1, x1->x2, x2->close): a stop fills at its price when the segment reaches
   it; a limit fills at its price when the segment touches it. Orders fill in path order.
2b. **Activated stops**: a stop that is at or through the close of the bar on which it is submitted (buy stop
   <= close, sell stop >= close) is triggered at submission and becomes a market order: it fills at the next
   open whatever that open is (a gap back does not cancel it). Limits never activate this way: a marketable
   limit fills at the next open only if the open is still at or better than its price.
4. **Entry then exits on the same bar**: after an entry fills, its exit orders (pre-submitted with
   `from_entry`) work on the rest of the path from the fill point. A `profit` / `loss` distance is measured
   from the actual fill price.
5. **OCA**: entries in one `oca_name` group with type cancel: the first fill cancels the others. Each entry has
   **one bracket** (one `strategy.exit` id, `from_entry` = that entry) covering the whole contract: a TP leg
   (`profit`) and a stop leg. Adding or moving the stop amends that same bracket and keeps the TP. The first leg
   to fill cancels the other; all exit legs are removed when the position closes.
6. **Expiry (custom restriction, not a Pine default)**: an entry order may carry a trading day and a last ET
   minute; it is not worked on bars of another day or after that minute, even when the bar that would have run
   the cancelling script is missing. Equivalent to Pine cancelling on every bar's close by time.
7. **Roll guard**: a position never spans two `raw_symbol`s (run stops with an error if it would).
8. **Costs**: commission per filled order; cancelled orders cost nothing.

## Report (per config)
Money is $ and % of 50,000. Closed equity = 50,000 + sum of closed net P&L.
- Net profit, gross profit, gross loss, profit factor (gross profit / |gross loss|), commission paid.
- Total closed trades, winners (net > 0), losers (net < 0), even (net = 0), % profitable (winners / all),
  avg trade, avg win, avg loss, ratio avg win / |avg loss|, largest win, largest loss, avg bars in trade
  (exit bar - entry bar + 1), long/short split. A zero denominator gives null, never infinity.
- **Per-trade excursions** (ticks): best / worst price while exposed = path points from the fill to the exit
  (entry bar: only after the fill; exit bar: only up to the exit; bars between: high and low).
  Run-up $ = max(0, dir x (best - entry)) x $10; drawdown $ = max(0, dir x (entry - worst)) x $10 (commission not
  included).
- **Max drawdown** (TV intrabar): for trade i, peak_i = max closed equity up to **before** its entry,
  trough_i = closed equity before entry - trade drawdown $ - entry commission; also closed equity after
  the trade. Max drawdown = max over trades of peak_i - min(trough_i, equity after i). % = of peak_i.
  **Max run-up** mirrors it with the minimum closed equity before entry and the trade run-up $.
- **Sharpe** = (mean(r) - rf) / sd(r), **Sortino** = (mean(r) - rf) / sqrt(mean(min(0, r - rf)^2)), with r =
  monthly returns of closed equity (calendar months in UTC of the exit time; month-end equity / previous
  month-end equity - 1, starting from 50,000; every month from the first to the last bar month, a month with no
  trade = 0), rf = 2%/12, sd population (ddof 0, as TV's example). Not annualized. Zero sd / downside = null.
- **Buy & hold** (labelled TV-style, not executable): return % = (last close - entry price of the first trade)
  / that entry price, $ = that % of 50,000. Unadjusted continuous series, so roll jumps are inside it.
- By year, 1-tick slippage stress row.
- Trades CSV: TV "List of trades" columns (Trade #, Type, Signal, Date/Time, Price, Contracts, Profit,
  Cum. Profit, Run-up, Drawdown) plus raw_symbol, submit/fill bar times, stop/target levels, commission, exit
  reason.

## Codex review R-TV-1 (spec, 2026-10-29)
| Sev | Finding | Change |
|---|---|---|
| High | Stops already through the market at submission must stay triggered | 2b activated stops |
| High | Missing-bar flat fallback undefined / overnight exposure | ORB SPEC Flat rule rewritten |
| High | Timestamp expiry stronger than Pine cancellation | labelled custom restriction (6) |
| High | Exit IDs and amendment semantics missing | one bracket per entry (5) |
| High | Drawdown used a future peak | peak/trough anchored before entry |
| Medium | Sharpe sd ddof | ddof 0 |
| Medium | Buy & hold anchor and sizing | first trade entry, % of 50,000 |
| Medium | 5m delayed confirmation identity | ORB SPEC 5m rule |
| Medium | Path tie unverified | labelled unverified convention |
| Medium | Average bars off by one | + 1 |
| Medium | Zero/even trades, zero denominators, month timezone | defined above |

## Codex review R-TV-2 (code, 2026-10-29, before the real run)
| Sev | Finding | Change |
|---|---|---|
| High | Roll exit at a contract's last bar used the roll date in advance | no pre-emptive exit; a position on another contract stops the run; open position at data end closes at the last close (`end`) |
| High | Slippage stress only subtracted money | slippage applied to market / stop fills inside the broker; TP measured from the slipped fill |
| High | 0 used as "no price" (CL traded below zero 2020-04-20) | `NO_PRICE` sentinel; negative-price tests |
| Medium | Late flats invisible | late flat / overnight / end counts and `late_or_overnight_trades.csv` |
| Medium | T10 manifest names pre-split paths | loader maps old paths, hash still checked |
| Low | No submission times; summaries for 6 configs only | submit time per trade; `summaries.json` for all 170 |
