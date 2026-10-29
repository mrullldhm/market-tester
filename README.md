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

Two separate testers, never mixed (no imports across them, `tests/test_boundary.py`):
- **quantum tester** (`market_tester/quantum`): conservative 1-lot engine (stop wins a same-minute tie, target
  needs a trade-through, $24.02 round trip), statistical gates, dev/holdout lock.
- **TradingView tester** (`market_tester/tradingview`): emulates the TradingView Strategy Tester broker
  (intrabar path, limits fill on touch), $50,000 account, 1 CL, $2.01 commission per order ($4.02 round trip,
  TopstepX), TV performance summary and list of trades. Semantics: `market_tester/tradingview/SPEC.md`.

## Layout
| Path | What |
|---|---|
| `market_tester/common/` | shared: `config` (CL guard, costs, paths), `data` (bars, rolls), `sessions` (ET clock, EIA), `periods` (DEV/HOLDOUT dates, `load_range` with holdout guard), `ledger` |
| `market_tester/quantum/` | quantum tester: `engine`, `strategy`, `metrics`, `stats`, `baseline`, `optimize`, `report` (`evaluate`), `prereg`, `periods` (holdout lock), `validate`, `toys` |
| `market_tester/tradingview/` | TradingView tester: `script` (Pine-style orders), `broker` (emulator), `run` (chart + trade list), `report` (TV metrics), `calendar` |
| `strategies/quantum/` | strategies tested with the quantum tester (P10 published, O10 ORB, spread lead) |
| `strategies/tradingview/` | strategies for the TradingView tester (`orb.py`) |
| `experiments/LEDGER.csv` | shared append-only ledger: every run, config counts, holdout looks (both testers) |
| `experiments/quantum/`, `experiments/tradingview/` | specs, runners, reports per tester (`experiments/MOVED.md` maps old paths) |
| `tests/common`, `tests/quantum`, `tests/tradingview`, `tests/pine` | `pytest` (data tests skip if `data/` is missing) |
| `pine/` | wickless S/R TradingView indicator and its parity check |

## Periods (fixed 2026-10-18, before any strategy test)
| Period | Trading days | Use |
|---|---|---|
| dev | 2010-06-07 .. 2023-12-31 | build, tune (guarded grid), walk-forward |
| holdout | 2024-01-01 .. 2026-10-13 | one run per pre-registered frozen config (`prereg.open_run`) |
| forward | after 2026-10-13 | paper trading |

## Testing a strategy (quantum tester)
```python
from market_tester.quantum.report import evaluate
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

Engine proofs: `python -m market_tester.quantum.validate` (coin flip, planted edge PASS, null control FAIL).

## Testing a strategy (TradingView tester)
Write a numba script with `market_tester.tradingview.script` orders (see `strategies/tradingview/orb.py`), then
`Chart.from_bars(bars)`, `simulate_config`, `trade_frame`, `report.summary`. Full history needs a `holdout_look`
ledger row (`common.periods.load_range`). ORB run: `.venv/bin/python experiments/tradingview/ORB/run.py`.

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
