# market-tester

Backtesting research to find a **profitable strategy on CL** (NYMEX Light Crude Oil futures, TradingView `CL1!`).

## Scope
- **CL only.** MCL (Micro WTI) is banned: non-syariah for the owner. `market_tester.config.assert_allowed`
  rejects every symbol that is not CL, and the bar loader calls it on every symbol it reads.
- **Order of work:** 1) find a strategy that is profitable after costs on data it was not tuned on;
  2) only then build a trading bot. Prop-firm rules are not part of the goal for now.
- **Team:** Claude builds (code, tests, reports); Codex reviews adversarially and replicates headline
  numbers with its own code (see `AGENTS.md`). No result counts until Codex has checked it.

Read `LESSONS.md` before proposing any strategy: it lists dead ideas, data traps and which years were
already looked at.

## Layout
| Path | What |
|---|---|
| `market_tester/config.py` | market (CL), tick $0.01 = $10, cost baseline, data paths, CL-only guard |
| `market_tester/data.py` | `load_bars(years, continuous)`, `is_roll` flag, `check_same_contract` |
| `tests/` | `pytest` (data tests skip if `data/` is missing) |
| `LESSONS.md` | everything worth keeping from the old `propfirm` repo |

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
1. Decide and write down the holdout period (see `LESSONS.md` section 5) and start an append-only
   experiment ledger: hypothesis, configurations tried, periods accessed, result.
2. Build a small backtest engine with the fill and cost rules from `LESSONS.md`; Codex reviews it.
3. Prove the engine: random-entry baseline loses exactly the cost; a planted edge is found.
4. Test strategy ideas one at a time, pre-registered, Codex replicates.
5. Forward / paper test the survivor.
6. Trading bot.
