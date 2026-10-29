# Wickless S/R retest: rules (rev 3)

Indicator `pine/wickless_sr.pine` (TradingView, Pine v6) and its Python port `pine/crosscheck.py` follow
these rules exactly. Replicators work from this file, not from either implementation.

This is a chart tool, not a validated strategy. The dashboard shows in-sample results on whatever history the
chart loads.

Rev 3 (after Codex spec review): resting-limit entries with trade-through and gap fills, target trade-through,
causal session end, roll trades voided (not exited), ambiguous two-sided bars skipped, win = `net$ > 0`,
CL-only guard, input domains, resampling and warm-up defined.

## 0. Notation and scope

- **CL only.** The Pine script stops with an error unless `syminfo.root == "CL"` (CL1!, CL2!, CLZ2026 ...;
  MCL has root `MCL` and is refused). The Python port loads bars only through `market_tester.data.load_bars`
  (which calls `assert_allowed`).
- Bars `t = 0, 1, ...` on the chart timeframe, each with `o h l c v`. Logic runs once per **closed** bar.
- Supported timeframes: 1, 2, 3, 5, 10, 15, 20, 30, 60 minutes (divisors of 60, so bars align to the 18:00 ET
  session open and to the clock alike). Other timeframes: the Pine script stops with an error.
- **All prices are integer ticks** (`round(price / tick)`, `tick` = 0.01 for CL). Every comparison and P&L
  uses ticks; floats only for MA / ATR filters.
- `trading_day`: a bar starting at or after 18:00 ET belongs to the next calendar day (CME Globex day
  18:00-17:00 ET).
- `last_of_session(t)`: bar `t` spans 16:59 ET, i.e. its start minute-of-day `s` (ET) satisfies
  `s <= 16*60+59 < s + tf`. Purely from the clock, so it is known while the bar forms.
- `new_day(t)`: `trading_day[t] != trading_day[t-1]` (first bar of a trading day, `t > 0`).
- "Known at `t-1`" = computed from bars `0..t-1` only.

## 1. Inputs

| Input | Default | Domain | Meaning |
|---|---|---|---|
| `tolTicks` | 0 | int >= 0 | wick counts as absent when its length <= `tolTicks` |
| `minRangeTicks` | 1 | int >= 0 | skip bars with `h - l < minRangeTicks` |
| `directional` | true | bool | body colour must match (section 2) |
| `minGap` | 1 | int >= 1 | level can fill from bar `d + minGap` on (`d` = detection bar) |
| `maxAge` | 100 | int >= `minGap` | level expires after bar `d + maxAge` |
| `maxLevels` | 20 | int 1..100 | live levels per side; adding one more deletes the oldest of that side |
| `resetSession` | true | bool | delete all levels at session end (section 3) |
| `pivLeft`, `pivRight` | 5, 2 | int 1..100, 1..50 | swing pivot widths (fit Pine's 500-bar history buffer) |
| `stopBufTicks` | 0 | int >= 0 | stop placed this many ticks beyond the swing |
| `maxRiskTicks` | 0 | int >= 0 | skip trade if `riskT >` this (0 = no limit) |
| `rr` | 1.1 | float >= 0.1 | target distance = `ceil(rr * riskT)` ticks |
| `flatSession` | true | bool | close open trades at session end (section 4) |
| `useRibbon` | false | bool | MA ribbon filter on entries |
| `ribbonType` | EMA | EMA / SMA | |
| `ribbonLens` | 8, 13, 21, 34, 55 | int >= 1 each | fastest first |
| `ribbonMode` | Stacked | Stacked / Price vs slowest | |
| `useST` | false | bool | Supertrend filter on entries |
| `stFactor`, `stAtrLen` | 3.0, 10 | float >= 0.1, int >= 1 | |
| `filterArrows` | false | bool | also drop detections (arrow and level) that fail the filters |
| `qty` | 1 | int >= 1 | contracts |
| `costRT` | 4.02 | float >= 0 | $ per contract per round trip (TopstepX CL commission) |

`pointValue` = $ per 1.00 price move per contract (`syminfo.pointvalue`; CL = 1000). One tick = $10 on CL.

## 2. Detection (bar `d`, at its close)

```
body_lo = min(o, c);  body_hi = max(o, c)          (ticks)
noBot   = body_lo - l <= tolTicks
noTop   = h - body_hi <= tolTicks
valid   = h - l >= minRangeTicks
bull    = c > o;  bear = c < o
```

- `directional = true`: support if `valid and noBot and bull`; resistance if `valid and noTop and bear`.
- `directional = false`: support if `valid and noBot`; resistance if `valid and noTop`; but a bar with both
  `noBot and noTop` keeps only its body-colour side (bull = support, bear = resistance, doji = neither).
- A bar gives at most one level. Support price = `l`, resistance price = `h`.
- `filterArrows = true`: the detection is dropped (no arrow, no level) if its side's filter fails with values
  at bar `d` itself.
- Display only (no effect on stats): normal-size arrow below the bar (support) / above (resistance); a text
  label with `v` in TradingView's abbreviated volume format (`format.volume`, e.g. `1.23K`). TradingView keeps
  only the newest 500 labels / lines / boxes; older drawings disappear, statistics do not change.

## 3. Levels (resting limit orders)

A level = `(side, price, d)`, live from bar `d + minGap`. Bars `d < t < d + minGap` ignore it.
A support level is a **resting buy limit** at `price`; resistance is a resting sell limit.

At bar `t` (step (c), section 4), for each live level:
- **Expiry** (step (b), first): delete when `t > d + maxAge`.
- **Touched** if the bar reaches the price: support `l <= price`, resistance `h >= price`. (A whole bar below a
  support has `l <= price`: that is a gap fill, below.)
- **Filled** (support; resistance mirror):
  - `o <= price`: filled at `o` (the market opened through the resting buy limit: price improvement);
  - else `l <= price - 1`: filled at `price` (one-tick trade-through, repo fill rule);
  - else (`l == price` exactly, no trade-through): touched, not filled.
- Every touched level is **consumed** (deleted), filled or not, traded or not.
- `resetSession = true`: all levels are deleted at step (e) of `last_of_session`, and at step (0) of
  `new_day` (covers days without a 16:59 bar, e.g. early closes or missing data).
- Display: a line from bar `d` at `price`, extended right while live; ended at bar `t` when deleted.

## 4. Trades (one at a time)

### Swing pivots
Hand-coded (not `ta.pivotlow`): at bar `t`, candidate `j = t - pivRight`, needs `j - pivLeft >= 0`.
- `pivLow` at `j` if `l[j] <= l[k]` for every `k` in `j-pivLeft .. j-1` and `l[j] < l[k]` for every `k` in
  `j+1 .. t`. Price = `l[j]`.
- `pivHigh` mirror: `h[j] >= h[k]` left, `h[j] > h[k]` right. Price = `h[j]`.
- Found at bar `t` = **known from bar `t+1` on** (pivot update is the last step of bar `t`).
- Pivots persist across sessions (Python: cleared at a roll, section 7). Keep the latest 50 per side.

### Order of steps at bar `t`
0. `new_day(t)`: if a trade is open (no 16:59 bar ended it): exit at `o`, reason `gap`. If `resetSession`:
   delete all levels. (Python: roll handling also here, section 7.)
1. (a) Manage the open trade (entered on an earlier bar).
2. (b) Expire levels.
3. (c) Touches, fills, entry.
4. (d) Detection on bar `t` (new level, live from `t + minGap`).
5. (e) Session end (`last_of_session`): `flatSession` and trade open: exit at `c`, reason `session`.
   `resetSession`: delete all levels.
6. Pivot update.

### (c) Entry
- Collect touched live levels; all are consumed.
- If the touched set contains **both** supports and resistances: ambiguous intrabar path, **no trade**.
- Else take the touched level with the latest `d` (tie: price closest to `o`; further tie: lower index =
  earlier created). If that level is not filled, or fails any check below: **no trade** (no fallback to another
  level).
- Checks: no trade open; no trade exited at this bar (steps 0, (a)); `t` is not `last_of_session`; side filter
  passes with values at `t-1` (section 5); swing stop exists; risk inside limits.
- `entry` = fill price from section 3.
- Stop: long = latest pivot known at `t` (found at bar `<= t-1`) with price `< entry`, `stop = pivot -
  stopBufTicks`; short = latest `pivHigh` with price `> entry`, `stop = pivot + stopBufTicks`. Search the 50
  stored pivots of that side, newest first; none = no trade.
- `riskT = |entry - stop|`, needs `riskT >= 1` and (`maxRiskTicks == 0` or `riskT <= maxRiskTicks`).
- `tgtDist = ceil(rr * riskT - 1e-9)`, `target = entry + tgtDist` (long) / `entry - tgtDist` (short).

### Management
- **Entry bar:** only the stop: long stopped if `l <= stop`, exit at `stop` (the fill was at or below `o`, so
  the stop is reached after entry). Short mirror (`h >= stop`). Target is never checked on the entry bar.
- **Later bars** (step (a)), long: `stopHit = l <= stop`; `tgtHit = h >= target + 1` (one-tick trade-through).
  Both = stop. Stop exit price = `min(o, stop)` (gap through the stop fills at the open); target exit = `target`.
  Short: `stopHit = h >= stop`, exit `max(o, stop)`; `tgtHit = l <= target - 1`.
- Session end / new day as in the step order.

## 5. Filters

Values at bar `t` use bars `0..t`. Entry at `t` uses values at `t-1`; `filterArrows` uses values at `d`.
All formulas hand-coded in Pine (not `ta.ema` / `ta.rma` / `ta.supertrend`) so seeds are fixed here.
Filter off = pass. Both on = both must pass. Any `na` = fail.

- **MA ribbon** on close (float price). EMA: `na` for bars `0..len-2`; bar `len-1` = mean of closes `0..len-1`;
  then `m = alpha*c + (1-alpha)*m[1]`, `alpha = 2/(len+1)`. SMA: mean of the last `len` closes.
  - `Stacked`: long if `m_1 > m_2 > ... > m_5`; short if `m_1 < ... < m_5`.
  - `Price vs slowest`: long if `c > m_5`; short if `c < m_5`.
- **Supertrend.** `tr[0] = h - l`; `tr[t] = max(h - l, |h - c[t-1]|, |l - c[t-1]|)`. `atr` = RMA, `n = stAtrLen`:
  `na` for bars `0..n-2`, bar `n-1` = mean of `tr[0..n-1]`, then `atr = (atr[1]*(n-1) + tr)/n`.
  `hl2 = (h+l)/2`, `upB = hl2 - f*atr`, `dnB = hl2 + f*atr`.
  First bar with `atr`: `up = upB`, `dn = dnB`, trend = bull.
  Later: `up = (upB > up[1] or c[t-1] < up[1]) ? upB : up[1]`; `dn = (dnB < dn[1] or c[t-1] > dn[1]) ? dnB : dn[1]`;
  trend[t-1] bull: bear if `c < up` else bull; trend[t-1] bear: bull if `c > dn` else bear.
  Long allowed if bull, short if bear.

## 6. Statistics (closed trades, in exit order)

Per trade: `pnlT = (exit - entry) * side` (ticks; side +1 long, -1 short), `R = pnlT / riskT`,
`net$ = pnlT * tick * pointValue * qty - costRT * qty`.
- **Win** = `net$ > 0`, else loss (any exit reason). Also report target-hit rate = share with reason `target`.
- Win rate = wins / trades. Avg R = mean R. Total R = sum R. Net $ = sum `net$`.
- Profit factor = `sum(net$ > 0) / |sum(net$ < 0)|`; `na` if there are no losing trades or no trades.
- Max drawdown: `cum_k = sum of the first k values`, `peak_k = max(0, cum_1..cum_k)`, `dd = max_k(peak_k - cum_k)`
  (0 if no trades); for `net$` and for `R`.
- Long / short: count and net $.
- Break-even win rate before costs at `rr` (reference only) = `1 / (1 + rr)`.
- No trades: counts 0, every ratio `na`.

## 7. Python port only (`crosscheck.py`)

- **Bars.** `CL.v.0` 1-minute bars via `market_tester.data.load_bars`. For each 1-minute bar: `m` = minutes
  since the 18:00 ET open of its trading day; `bucket = floor(m / tf)`. Group by
  `(trading_day, bucket, raw_symbol)` in time order: `o` first, `h` max, `l` min, `c` last, `v` sum. Bar start =
  18:00 ET of the previous calendar day + `bucket * tf` minutes. Incomplete buckets are kept (no fill of
  missing minutes); empty buckets give no bar. Duplicate 1-minute stamps raise an error.
- **Rolls.** At bar `t` with `raw_symbol[t] != raw_symbol[t-1]`, at step 0: an open trade is **voided**
  (removed, not in statistics, counted as `voided`), and all levels and pivots are cleared. A pivot needs every
  bar `j-pivLeft .. t` on one `raw_symbol`. Nothing uses knowledge of a roll before it is seen.
- **Pine on continuous symbols (`CL1!`) cannot void roll trades.** TradingView does not expose the roll bar to the
  script, and inferring it from price jumps is not allowed. A trade open at a roll, or a level from the old contract,
  can turn the roll jump into fake P&L. The dashboard says `rolls not voided` on `!` symbols. For clean numbers use
  a single CL contract chart (e.g. `CLZ2026`) or the Python port.
- MA / ATR / Supertrend state runs continuously across rolls and sessions (as TradingView does on CL1!).
- **Warm-up.** Scoring window `[start, end)`. Python loads bars from `start - 90 days` and runs all state from
  there; only trades whose entry bar starts inside the window are scored.
- **TradingView parity check.** `crosscheck.py tv` runs this port on the bars of a TradingView chart export
  (same bars, same first bar, no rolls visible) and compares per bar: detection side (`x_det`), entry side
  (`x_entry`), and the cumulative closed-trade count / net $ / R (`x_n`, `x_net`, `x_r`) on every bar except the
  export's last (possibly unclosed) bar, which is dropped before simulating. Tolerance: 0 for counts and sides,
  $0.01 for net, 1e-6 for R.
- **Databento vs TradingView** (different vendor, rolls, start bar) is not expected to match exactly and is not
  a gate.
- Python step 0 order at a bar with both a roll and a new day: roll first (the trade is voided, not gap-exited).
