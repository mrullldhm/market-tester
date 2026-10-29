# Moved paths (2026-10-29, ledger D003)

Ledger rows written before 2026-10-29 name the old locations. Map:

| Old | New |
|---|---|
| `experiments/<ID>/` (P10, P101..P110, O10, T10, T102, T20, V001, V002) | `experiments/quantum/<ID>/` |
| `experiments/trials/` | `experiments/quantum/trials/` |
| `market_tester/{config,data,sessions,ledger,periods}.py` | `market_tester/common/` (periods split: holdout lock in `market_tester/quantum/periods.py`) |
| `market_tester/{engine,strategy,metrics,stats,baseline,optimize,report,prereg,validate,toys}.py` | `market_tester/quantum/` |
| `market_tester/{published,orb_situations,spread_lead}.py` | `strategies/quantum/` |
| `tests/test_*.py` | `tests/{common,quantum,pine}/` |
| `LESSONS.md` | removed (owner); exposure facts in ledger D004 |
