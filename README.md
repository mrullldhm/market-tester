# market-tester

Backtesting research to find a **profitable strategy on CL** (NYMEX Light Crude Oil futures, TradingView `CL1!`).

## Scope
- **CL only.** MCL (Micro WTI) is banned: non-syariah for the owner. `market_tester.config.assert_allowed`
  rejects every symbol that is not CL, and the bar loader calls it on every symbol it reads.
- **Order of work:** 1) find a strategy that is profitable after costs on data it was not tuned on;
  2) only then build a trading bot. **Intraday only** (end use is a prop firm): every trade is flat the same
  session. Prop-firm rule details come after a strategy passes.
- **Team:** Claude builds (code, tests, reports); Codex reviews adversarially and replicates headline
  numbers with its own code (see `AGENTS.md`). No result counts until Codex has checked it.

Read `LESSONS.md` before proposing any strategy: it lists dead ideas, data traps and which years were
already looked at.

## Layout
| Path | What |
|---|---|
| `market_tester/config.py` | market (CL), tick $0.01 = $10, cost baseline, data paths, CL-only guard |
| `market_tester/data.py` | `load_bars(years, continuous)`, `is_roll` flag, `check_same_contract` |
| `market_tester/sessions.py` | ET clock, CME trading day (18:00 ET start), EIA ±15 min blackout |
| `market_tester/periods.py` | `DEV` / `HOLDOUT` (fixed 2026-10-18), `load_period`; holdout needs a live prereg token |
| `market_tester/ledger.py` | append-only `experiments/LEDGER.csv` (every run, config count per family, holdout look); `experiments/trials/` per-config Sharpe |
| `market_tester/prereg.py` | freeze a holdout test; `open_run` opens the holdout once, ever |
| `market_tester/strategy.py` | `Signals`, `Strategy` protocol, `per_contract`, session helpers |
| `market_tester/engine.py` | generic numba 1-lot simulator: fill, stop/target, roll, EIA, cost rules |
| `market_tester/metrics.py` | per-trade / per-day numbers, by year / side, drawdown |
| `market_tester/stats.py` | day-clustered t, block bootstrap CI, Monte Carlo (risk), deflated Sharpe, Romano-Wolf, SPA, PBO |
| `market_tester/baseline.py` | random-entry baseline (same week, minute, side, exits) |
| `market_tester/optimize.py` | guarded grid (parallel), plateau pick, anchored walk-forward |
| `market_tester/report.py` | `evaluate` (dev, all gates, report + ledger) and `evaluate_holdout` |
| `market_tester/validate.py` | engine proofs: coin flip, planted edge, null control |
| `market_tester/toys.py` | toy strategies for the proofs (not research ideas) |
| `experiments/` | ledger, pre-registrations, reports (in git) |
| `tests/` | `pytest` (data tests skip if `data/` is missing) |
| `LESSONS.md` | everything worth keeping from the old `propfirm` repo |

## Periods (fixed 2026-10-18, before any strategy test)
| Period | Trading days | Use |
|---|---|---|
| dev | 2010-06-07 .. 2023-12-31 | build, tune (guarded grid), walk-forward |
| holdout | 2024-01-01 .. 2026-10-13 | one run per pre-registered frozen config (`prereg.open_run`) |
| forward | after 2026-10-13 | paper trading |

## Testing a strategy
```python
from market_tester.report import evaluate
r = evaluate(MyStrategy(), {"param": [1, 2, 3]}, item_id="S001", family="orb", hypothesis="...", n_jobs=6)
```
`family` names the research idea: every variant of one idea shares it, so trials add up across renamed
items. A dev run always covers the whole dev period with the $24.02 baseline (or worse) and default (or
stricter) gates; only its PASS makes a candidate. `kind="exploratory"` allows `years=[...]` for a quick
partial look: trials still count, verdict is `EXPLORATORY-*`, never a candidate. `evaluate` logs a `dev_start` row (config count) before it loads data, loads dev bars, runs the
grid, picks by plateau-smoothed Sharpe, and gates the pick:

| Gate | Default | Answers |
|---|---|---|
| trades | >= 300 | enough sample |
| net $/trade | > 0 | profitable after $24.02 round trip |
| Romano-Wolf p | <= 0.05 | real inside the grid family |
| deflated Sharpe | >= 0.95 | beats best-of-N luck, N = every config ever run for the item |
| random-entry p | <= 0.05 | entries beat random entries (same week, minute, side, exits) |
| walk-forward OOS | > 0 | picks made only from earlier years still earn |
| PBO | <= 0.3 | in-sample best is not usually an OOS loser |
| 2019-23 block | > 0 | edge has not decayed |
| 2 tick slippage | > 0 | survives worse fills |
| look-ahead | unchanged | signal at a cut bar does not change when the future is removed |

Also reported: day-clustered t, block-bootstrap CI per trade, Monte Carlo drawdown (trade reshuffle and
resample: risk only, never evidence of edge), by year / side, exits. PASS on dev = candidate. Then:
Codex review + replication, `report.register_holdout(spec, strategy)` (owner approval, Codex review id;
freezes code, strategy source + state, config, holdout files; gates may only get stricter), then
`report.evaluate_holdout(strategy, path)` once.

Strategy rules: signals causal and stateless (no fitting over the sample; objects with `fit` are refused),
indicators per contract (`per_contract`). The holdout lock guards the tester's code path only: strategy
code must not read data files itself.

Engine proofs: `python -m market_tester.validate` (coin flip, planted edge PASS, null control FAIL).

## Data (`data/`, not in git, ~1.5 GB)
| Path | What |
|---|---|
| `processed/ohlcv-1m/year=YYYY/cl.parquet` | CL 1-minute bars 2010-06 .. 2026-10, `CL.v.0` + `CL.v.1`, with `raw_symbol`, `expiration`, prices in $ and integer ticks |
| `processed/definitions/cl_contracts.parquet` | CL contract definitions (symbol, expiry) |
| `processed/stage1/` | 2015-16 only: CL TAS trades, CL mbp-1 book 14:20-14:35, CL settlement statistics, RB/HO/BZ 1-minute bars (non-CL, reference only) |
| `raw/` | original Databento DBN files (source of truth for the above) |
| `crosscheck/massive/` | second-vendor CL bars 2024-10 .. 2026-10 used to verify Databento |
| `reference/eia_releases.csv` | EIA weekly inventory release times (news filter) |
| `manifests/` | download / purchase manifests with file hashes |

Columns of the bar files: `ts_event_utc, continuous_symbol, instrument_id, raw_symbol, open_ticks, high_ticks,
low_ticks, close_ticks, open, high, low, close, volume, expiration`.

**Roll warning:** prices are not back-adjusted. Never count the jump at an `is_roll` bar as P&L, and never
let a trade span two `raw_symbol`s.

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```
`.env` holds `DATABENTO_API_KEY` and `MASSIVE_API_KEY` (only needed to buy or re-download data).

## Roadmap
1. ~~Holdout written down, ledger started~~ (2026-10-18, `experiments/LEDGER.csv` D001).
2. ~~Backtest engine with the fill and cost rules~~; Codex review.
3. ~~Engine proofs: random entries lose the cost, planted edge found, null rejected~~; Codex replication.
4. Test strategy ideas one at a time with `evaluate`, Codex replicates, then one holdout run.
5. Forward / paper test the survivor.
6. Trading bot.
