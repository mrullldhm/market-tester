# T20 — spread lead, every eligible day, one 10:00-11:00 trade (spec, frozen before any run)

Rev 1 written 2026-10-19, before any code for it and before any 10:00-11:00 data look. **Rev 2** (same day,
still no code, no look) applies Codex spec review R-T20-2 (3 high, 3 medium; all accepted, fix 3 adapted to stay
causal). Owner rules: CL only,
intraday only, 1 lot. One item **T201**, one config, never tuned. Family `spread_lead` (same idea as T102, so
trials add up). Dev period only via `report.evaluate(kind="dev")`; holdout only per README.

## Why (T10 decision, Codex R-T20-1)
T102 (same signal, |r| >= σ filter, hold to 14:29) needed a 16-net-tick edge for 80% power (`T10/POWER.md`)
and was not run. Power grows with trade count and falls with hold noise (sd ~ √hold). T201 trades every eligible
day (~2,600 dev trades instead of 847) and holds one hour. Rough 80%-power edge at α 0.002 (LESSONS §2 noise,
09:00-10:00 sd 49 ticks as a proxy): 3.72 × 49 / √2,000 ≈ 4 net ticks. Measured by the blind power check
below before any P&L.

What was seen before this spec: T102 dev signal inputs (eligibility, r, d0, A/D split) and the **unsigned**
10:00 -> 14:29 move size on 847 days. Nothing about 10:00-11:00, no signs, no strategy P&L. Choosing the
10:00-11:00 exit now is therefore not outcome-driven; no other exit window may ever be compared for this family.

Overlap: a 1-hour hold after a first-hour signal is close to dead short-horizon ideas (P101, P102). Corr(r, d0)
was 0.028 on T102's filtered days only; that is **not** evidence for T201. Novelty is checked by the gate below.
LESSONS §6.10 discouraged short horizons; T201 is an exception because the signal is the second month, not a
front-month minute-bar indicator, and because power, not cost, killed T102.

## Rule (T201)
Data, eligibility, contract checks, rolling OLS, causal/final split: exactly `T10/SPEC.md` rev 3 and its
implementation notes (`SpreadLead.day_table`, window = previous 20 `final` days, OLS with intercept,
degenerate window = no signal). Changes:
1. **No magnitude filter:** side = sign(r) on every eligible day with a non-degenerate window and r ≠ 0.
2. **EIA days excluded from trading:** per trading day, `eia_during_hold` = True if **any** row of
   `data/reference/eia_releases.csv` (candidate or confirmed; a date can have several rows, e.g. an unconfirmed
   10:30 and a confirmed 11:00) has an ET time in **10:00..11:15**. Conservative: holiday-week 11:00 releases are
   excluded too (the margin also covers a late exit, rule 3). The loader adds it per bar (constant within the
   day, from the schedule, not from later bars); a test asserts the column is identical under truncation.
   Excluded days still enter later rolling windows if `final` (their 08:59-09:59 inputs precede any release).
3. **Exit:** flat at the close of the first bar at or after 10:59 ET; `flat_day_end=True`. Entry 10:00 open,
   `max_fill_delay_min=1`. No stop, no target. Engine fill and cost rules as README. Requiring an exact 10:59 bar
   before entry (R-T20-2) would read a future bar at 09:59, so instead: rule 2's 11:15 margin keeps a late exit
   off a release, and the report counts exits not at 10:59 (expected near zero in this liquid hour).
4. **Day sets:** `model_final` = T10's `final` (rolling OLS window). `trade_eligible` = `eligible` and not
   `eia_during_hold` (signal, causal). `trade_final` = `final` and not `eia_during_hold` (random-entry partners and
   power days).

## Gates (pre-declared)
- **Novelty gate first** (signal inputs only, no P&L): on T201 signal days, share of days whose side equals the
  exact P101 entry side of that day, and (on days where both trade) the first P102 entry side. **Stop** if either
  agreement is outside 35%..65% (T201 would mostly repeat a dead signal). Reported with counts.
- **Power gate** (blind, no strategy P&L): T201 signal days, unsigned 10:00 -> exit move, random sides, B = 49,999
  per test, common random numbers across k. Gate at **k = 4 net ticks: 1,000 sims**; pass only if the one-sided 95%
  Clopper-Pearson lower bound of power is >= 80% for (a) dev all gates + D gate and (b) D gate alone. Also
  reported (200 sims each): k = 0 (false-positive rate) and k = 2, 6. **Holdout-proxy power** (signal days
  2021-03-01..2023-12-31, the T10 rev 3 holdout gates) reported at k = 4 with its count; below 50% means a dev
  candidate also needs >= 6 months of forward paper trading before any bot, whatever the holdout says.
  Fail = stop: T201 logged as not run, no trial spent.
  Simulated dev gates (code review R-T20-3): trades, net > 0, 2-tick stress, profitability p <= 0.002, 2019-23
  block, global DSR, plus the D gate. Random-entry, walk-forward and look-ahead are not simulated (they can only
  lower power), so the reported dev power is an **upper bound**. Any exit after 11:15 ET fails the power gate.
  Each gate file carries the code hash; `--power` and `--dev` refuse stale or failed gate files.
- **Dev:** `evaluate` default gates, random-entry 1,999 draws on `trade_final` days (eligible mask), profitability
  p <= 0.002 (49,999 draws), global DSR N = 25 ideas (T201 replaces the never-run T102), Sharpe pool P101-P110 +
  own (`T10/global_sharpes.csv`, hashes re-checked).
- **D gate (dev):** trades in D >= 150, net $/trade > 0, one-sided block-bootstrap p <= 0.05. A and D CIs reported.
- **Concentration:** traded days >= 200; net > 0 after the best week out; years with net > 0 reported.
- **Candidate** = dev PASS + D gate + concentration. Then Codex replication, owner approval, holdout prereg with
  the T10 rev 3 holdout gates (all trades p <= 0.05, 2-tick stress > 0, D trades >= 40, D net > 0, D p <= 0.10),
  one holdout run, then forward paper trading. A FAIL ends the `spread_lead` family: no other exit, window,
  threshold or day filter.

## Code needed (reviewed by Codex before the power run)
- `periods.load_period`: per-bar `eia_during_hold` (rule 2), from the same reference file the blackout uses;
  truncation-invariance test.
- `spread_lead.py`: `SpreadLeadHour` (T201) reusing `day_table` with the magnitude filter off; T102's class and
  behaviour unchanged.
- `experiments/T20/run.py`: `--novelty`, `--power` (gates above, write `NOVELTY.json`, `POWER.md`/`POWER.json`;
  power runs in parallel with live progress) and `--dev` (refuses unless both gates passed and family
  `spread_lead` has no dev run).

## Decision 2026-10-19: T201 stopped at the power gate (pre-declared outcome)
`POWER.md`: at k = 4 net ticks, dev all gates 0% (blocked by the global DSR), D alone 70% (95% LB 67.5%) < 80%.
Novelty gate had passed (agreement P101 49.9%, P102 51.7%). No dev run, no trial spent, no strategy P&L seen.
Per this spec a fail ends family `spread_lead`; no exit / window / threshold variant may follow.
Codex R-T20-5 (agreed): the global DSR here is mis-specified (own Sharpe in its own variance pool; P101-P110 are
not an exchangeable null; double-counts the Bonferroni p). D alone fails regardless, so the stop stands.

## Cost change (2026-10-29, ledger D005)
Costs above ($24.02, 2-tick stress) are superseded for any new run: TopstepX commission $4.02 round trip,
slippage 0, 1-tick stress gate. The runners read the new values from `common.config`.
