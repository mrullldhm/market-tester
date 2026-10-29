# T10 — CL second month as a slow signal for one intraday trade (spec, frozen before any run)

Rev 1 written 2026-10-19 before any code or P&L look. **Rev 2** (same day, still no code, no P&L) applies Codex
spec review R-T10-1 (5 high, 3 medium; all accepted): **T101 dropped**, T102 fixed. **Rev 3** (same day, no
code, no P&L) applies final review R-T10-2 (2 high, 3 medium, 1 DSR detail; all accepted). Owner rule: intraday only
(prop-firm end use), CL only. One item, one config, **never tuned**. Dev period only (2010-06-07..2023-12-31)
via `report.evaluate(kind="dev")`, $24.02 round trip. Holdout only per README.

## T101 dropped before any run (R-T10-1)
Carry is paid to a front/second **spread** position as the front converges; T101 held only the outright front
09:00-14:29. A steep curve can coexist with zero expected outright drift in that window, so the 50-tick
threshold had no cost justification. Term structure as an intraday **direction** signal is not tested. No P&L
was computed; T101 is not a trial.

## Why T102, after 18 dead strategies (LESSONS §3, §6.10)
- Every dead idea read only front-month OHLCV. T102 reads the **second month** (`CL.v.1`) as information. The
  trade is one CL front outright, one lot, flat the same session, at most one trade a day.
- Hypothesis: physical / commercial demand for prompt barrels shows first as the front moving beyond what the
  second month explains; speculators who price only the flat outright adjust over the rest of the day.
- **Overlap with dead P101** (first-hour momentum): the residual still contains the front's first-hour move. T102
  is treated as a P101-related trial, and the days where it disagrees with plain first-hour momentum are a
  **pass gate** (below), because only they carry information P101 did not have.

## Data addition (code reviewed before any run)
1. **Opt-in loader.** A strategy declares `requires_second = True` (class attribute, part of the frozen strategy
   source). `report.evaluate` and `report.evaluate_holdout` both call `load_period(..., with_second=
   strategy.requires_second)`; no caller-supplied merge on real data. The holdout lock and `assert_allowed` are
   unchanged (both series are CL and come from the same `cl.parquet` files).
2. **Join.** For each front bar, the `CL.v.1` bar with the same `ts_event_utc`: `c2_close_ticks`,
   `c2_raw_symbol`, `c2_instrument_id`, `c2_expiration`; NaN when that minute has no `CL.v.1` bar. Left join,
   validated one-to-one; assert the front row count and order are unchanged.
   **Second-series roll flag before the join** (rev 3): `c2_roll_day` = True for every trading day on which the
   full `CL.v.1` series (all its bars, not only joined ones) has an `is_roll` bar; carried onto every front row
   of that day, so a `CL.v.1` roll at a minute with no front bar is not lost.
3. **Next-contract check.** From `data/processed/definitions/cl_contracts.parquet` (static listing calendar, known
   in advance). `raw_symbol` repeats across decades (78 duplicates, e.g. CLN0 = 2010 and 2020), so the key is
   **`instrument_id`**: assert it is unique in the file and that each bar's (`instrument_id`, `expiration`)
   matches its definition row. `next_id(front_id)` = the listed CL outright with the smallest expiration
   strictly after the front's (assert exactly one). A spread value exists only when
   `c2_instrument_id == next_id(instrument_id)`. With `with_second=True`
   the definitions file joins `Period.files()` so a holdout prereg freezes it.
4. Schema: the list of added columns is frozen with the prereg; a missing column raises.

## Rule (T102, family `spread_lead`)
All times ET, 1-minute front `CL.v.0` bars.

**Day eligibility** (all must hold; else no signal and the day is not used in any rolling window):
- Exact bars at 08:59 and 09:59 in both series, and an exact front bar at 10:00.
- Front `raw_symbol` identical at 08:59, 09:59 and 10:00; second `c2_raw_symbol` identical at 08:59 and 09:59;
  next-contract check passes at both minutes.
- No roll in either series on that trading day (front `is_roll` false on every bar of the day and
  `c2_roll_day` false).
- **Expiry:** `expiration` converted to ET and taken as a date, minus the trading date, `>= 5` calendar days.
  Missing expiration = ineligible.

**Signal** on an eligible day t:
- d0 = front close(09:59) − front close(08:59); d1 = second close(09:59) − second close(08:59), in ticks.
- Window W = the previous 20 eligible trading days (strictly before t). Full window required, else no signal.
- OLS **with intercept** on W: d0 = α + β·d1 + e. σ = sqrt(Σe² / (20 − 2)).
- Degenerate window = no signal: d1 constant over W (rank-deficient design) or σ not finite or σ <= 0.
- r = d0 − α − β·d1 (today's values, yesterday's coefficients).
- If \|r\| >= σ and r ≠ 0: side = sign(r); signal at the 09:59 close, entry 10:00 open, `max_fill_delay_min=1`.

**Exit:** no stop, no target. Flat at the close of the first bar at or after 14:29 ET; `flat_day_end=True`.
The position holds through the Wednesday 10:30 EIA release (the entry is outside the ±15 min blackout); this is
declared, not filtered. Engine fill and cost rules as README.

## Disagreement subset (gate, not just a report)
D = signal days where sign(r) ≠ sign(d0), counting d0 = 0 as disagreement. A = the other signal days (same side
as plain first-hour momentum). Report trades, net $/trade, block-bootstrap CI for A and D, and the correlation
of r with d0.

## Random-entry baseline
Partners are drawn only among **eligible** days (eligibility mask fixed before the shuffle, same week, same
minute, same side, same exits). Report the match rate; the existing `min_match = 0.9` applies.

## Multiplicity (implemented, not assumed)
`evaluate`'s deflated Sharpe counts only trials inside one family and cannot see the ~1.4M old configs.
Rev 2 declares instead:
- **Global DSR:** `evaluate(..., global_trials=25, global_sharpes="experiments/T10/global_sharpes.csv")`.
  N = 25 = research ideas already tried on CL (8 old strategies, H025-H028 + H033 spec, S003, P101-P110, T102),
  counting a grid as one idea after its own in-family correction.
  `global_sharpes.csv` (built and committed before the run, Codex checks it): columns `item_id, report_json,
  report_sha256, day_sharpe`, one row per P101-P110, `day_sharpe` read from each item's saved dev report JSON
  (the same per-day Sharpe `evaluate` computes), file hash logged in the ledger row. At run time T102's own
  per-day Sharpe is appended in memory (not to the file), so the variance is over 11 values. This understates
  the old grids' search; the stricter p below covers it.
- **Profitability p <= 0.002** (Bonferroni 0.05 / 25), one-sided stationary block bootstrap on day P&L with
  **B = 49,999** draws and p = (k + 1) / (B + 1), k = draws with mean <= 0 after centring. Same B and formula
  for the D-gate p and in the power simulation (fewer power runs if needed, never fewer draws per test).

## Power (before any signal P&L)
Simulation, not the IID formula. Inputs that use no strategy P&L: the eligible days with \|r\| >= σ (signal input
only) and their front 10:00→14:29 returns. Each run: random side per day, add a planted edge of k net ticks per
trade, apply the 2-tick-slippage stress, run the real tests (block-bootstrap p <= 0.002 on all trades, D-subset
gate, trade count gates) with a stationary block bootstrap (mean block 5 days) over days. Report power vs k and
the k at 80% power, for all trades and for D. If k(80%) > 10 net ticks, a FAIL is reported as "underpowered",
not "no edge".

## Candidate rule (pre-declared)
All of:
1. `report.evaluate` default gates (trades >= 300, net > 0, random-entry p <= 0.05 with 1,999 draws, walk-forward,
   2019-23 block, 2-tick slippage, look-ahead), with the global DSR >= 0.95 and profitability p <= 0.002.
2. **D gate:** trades in D >= 150, net $/trade in D > 0, one-sided block-bootstrap p in D <= 0.05.
3. Concentration: traded days >= 200; net > 0 after removing the best calendar week; years with net > 0 reported.

Then Codex replication, owner approval, `register_holdout`, one holdout run, then forward paper trading.

**Holdout PASS** (pre-declared, rev 3) = all of: net $/trade > 0 on all trades, one-sided block-bootstrap
p <= 0.05 on all trades (B = 49,999), 2-tick slippage net > 0, **and D on holdout: trades >= 40, net $/trade > 0,
one-sided p <= 0.10**. A holdout that passes only through A (P101-like) days is a FAIL. The holdout has ~2.8
years, so D power there is low; the power simulation reports it, and an underpowered D holdout goes to forward
paper trading as "unconfirmed", never to a bot. A FAIL ends the family: no retuning of 20 days, σ, the 08:59-09:59 window or the expiry
filter.

## Implementation notes (rev 3 code, before any run; for Codex code review)
Code: `market_tester/data.py` (`contract_definitions`, `next_contract_ids`, `attach_second`), `periods.py`
(`load_period(with_second)`, `Period.files(with_second)`), `spread_lead.py` (`SpreadLead`), `baseline.py`
(`eligible` mask), `report.py` (`global_dsr`, `requires_second`, full-B profitability p for one config),
`stats.py` (`bootstrap_p`), `experiments/T10/run.py`, tests `tests/test_spread_lead.py`.
- **Causal roll rule:** "no roll that day" is checked as "no roll in either series so far, at the 09:59 close"
  (`is_roll` cumulative in the trading day; `c2_roll_so_far`). Rolls are never in US daytime (LESSONS §1), and
  the engine never lets a trade span a roll, so this equals the spec's rule for every trade.
- **10:00 bar:** not part of the signal (it is after the signal bar; the look-ahead check would rightly fail).
  `max_fill_delay_min=1` and the engine's new-contract refusal enforce "exact front bar at 10:00, same contract".
  Code review R-T10-3 (1 high, 2 medium, 1 low; all fixed): a day enters later rolling windows and the
  random-entry pool only if it also had the exact 10:00 front bar on the 09:59 contract (`final`; in the past
  when used). A/D block-bootstrap CIs reported; null contract fields raise; the dev summary carries the power
  verdict ("underpowered") from `POWER.json`.
- Pre-run coverage on dev (signal inputs only, no P&L): 3,491 days, 2,601 eligible (64-80% per year), 847 signal
  days, 390 of them D. Ineligible causes overlap: 353 near expiry, 326 second month not the next contract,
  380 second-month roll so far, 163 front roll days, 66 missing second-month 09:59 bar.
- Holdout D gate (rev 3) is pre-declared here but not yet in `evaluate_holdout`; it is added before any
  `register_holdout`, and only if T102 becomes a candidate.

## Decision 2026-10-19: T102 dev run NOT executed (owner, option 2)
Power (`POWER.md`, signal inputs + unsigned moves only, no strategy P&L): 80% power needs a 16-net-tick edge on
dev (spec limit 10) and the holdout proxy reaches only 38% at 20 ticks. Owner chose to redesign instead of
running an underpowered test. No dev trial was spent: family `spread_lead` has a power `decision` row only, no
`dev_start`. Seen so far: dev signal inputs (eligibility, r, d0, A/D split) and the unsigned size of the
10:00 -> 14:29 move on 847 signal days. Next design: work with Codex (see T20).
