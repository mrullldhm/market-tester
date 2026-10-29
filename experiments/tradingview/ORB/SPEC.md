# ORB-TV: first-hour NY range breakout, 6 situations x SL/TP grid, TradingView tester (rev 2)

Rev 2 (same day, before code ran on real data) applies Codex review R-TV-1 (see tester SPEC table).

Written 2026-10-29 before any TV-tester code or run. Owner's rules (this conversation). Tester semantics:
`market_tester/tradingview/SPEC.md`. Family `orb`, items `TV101..TV106` (one per situation).

**Data**: `CL.v.0` 1-minute bars, **full history** trading days 2010-06-07 .. last bar on disk (2026-10),
owner-approved holdout look (ledger `holdout_look` row before any read). Results are exploratory: this
family was already tested on dev in the quantum tester (O10, 204 configs), and the 2024+ holdout is now seen.

## Clock
- NY session open **09:00 ET**, DST from `America/New_York`: 21:00 MYT in EDT, 22:00 MYT in EST.
- Range = 1m bars 09:00..09:59 ET. Flat = Topstep rule 3:10 PM CT = **16:10 ET**; no new entry fills after
  3:08 PM CT.

## Grid (170 configs, all logged before data is read)
TP in {30, 31, 32, 33, 34} ticks; SL in {15, 16, 20, 21, 25, 26, 30, 31} ticks.
S1, S3 (no SL value): 5 configs each. S2, S4, S5, S6: 40 each.

## Day rules (all situations)
- **Eligible day**: all 60 range minutes have a bar; one `raw_symbol` from 09:00 to 16:10 ET (a switch inside
  it stops the run: CL does not roll in US daytime); not a scheduled shortened session. Shortened sessions
  (frozen rule calendar, `market_tester/tradingview/calendar.py`): MLK, Presidents, Memorial, Juneteenth
  (from 2022), Independence (observed), Labor, Thanksgiving days, the day after Thanksgiving, Dec 24 on
  weekdays. Not traded, listed in RESULTS. Excluded days are listed with the reason.
- H = max high, L = min low of the 60 range bars.
- **Orders** are submitted at the close of the 09:59 bar and work from 10:00. Entry orders are not worked on
  bars from 16:08 ET; pending ones are cancelled. One trade per day at most.
- **Flat**: `close_all(immediately)` at the close of the first bar from 16:09 ET up to 16:59 ET (= 16:10:00 when
  the 16:09 bar exists; a later bar = flagged "late flat"). If no such bar exists that session, the position
  is closed at the next bar's open (reason `day_end`) and counted as an **overnight failure** in RESULTS; at the
  end of the data, at the last close (reason `end`). Expected count: 0 or a few; each is listed.
- **Entry expiry** (custom restriction, tester SPEC 6): entry orders carry the trading day and last minute 16:07,
  so no entry fills on a bar from 16:08 even if the 16:07 bar is missing.
- **Break**: a candle breaks up if high >= H, down if low <= L.
- **TP** = actual entry fill + / - TP ticks (limit), pre-submitted with the entry, so it works from the fill.
  SL legs amend the same bracket (tester SPEC 5).
- Before the SL is placed only the TP and the flat can close the trade (owner: no SL until the candle closes).
- No EIA filter (TradingView has none). RESULTS counts trades with an entry within 15 min of a listed release.
- 5m candles = ET wall-clock buckets [hh:00, hh:05), [hh:05, hh:10), ... of one trading day; high/low of the
  1m bars inside the bucket only (including minutes before a fill). A bucket with no bar does not exist.
  Confirmed at the close of its :x4 / :x9 bar; if that minute has no bar, at the close of the first later bar
  (whose own high/low never enter the confirmed bucket). Several overdue buckets confirmed by one bar are
  handled in time order.
- Entry stops are submitted at the 09:59 close; H >= that close >= L, so a stop equal to the close is
  activated (tester SPEC 2b) and fills at the 10:00 open.

## Situations (long shown; short mirrors: swap high/low, H/L, signs)
- **S1** buy stop at H and sell stop at L (one OCA-cancel group: the path decides on a bar that reaches both).
  At the close of the fill bar: SL = that 1m candle's low.
- **S2** as S1, SL = the candle's low if fill - low <= SL ticks, else fill - SL ticks. (A stop already at or
  above the market fills at the next open.)
- **S3** as S1, SL = low of the 5m candle that contains the fill bar, placed when that candle is confirmed.
- **S4** as S3 with the S2 cap (5m low if within SL ticks of the fill, else fill - SL ticks).
- **S5** no stop entries. The first 1m candle from 10:00 that breaks (both sides on one candle -> no trade that
  day; at most one signal per day): at its close SL = its low, buy limit at SL + SL ticks, with bracket
  stop = SL and TP = fill + TP. A limit at or above the market fills at the next open.
- **S6** as S5 with the first confirmed 5m candle (buckets from 10:00) that breaks.

## Outputs (`experiments/tradingview/ORB/`)
`RESULTS.md` (best config per situation, TV summary, optimizer table of 170 rows ranked by net profit, by
year, 2010-2023 vs 2024+ split, 1-tick stress), `optimizer.csv`, per-config JSON summaries, trades CSV for
the best config per situation and for TP30/SL16 per situation. Ledger: `tv_start` (170 configs, data and
code hashes) before data, `tv_run` per item after. Codex replication in `REPLICATION.md`.

**Not proof of edge**: the pick is the best of 170 on the same data it is judged on.
