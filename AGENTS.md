# Instructions for Codex

This repo is a CL (NYMEX Light Crude Oil) strategy research project run by Claude (builder) with you,
Codex, as **reviewer and independent replicator**. Read `README.md` first.

## Hard rules
- **CL only.** Never use, suggest or download MCL (Micro WTI) or any other market. MCL is non-syariah for
  the owner. Non-CL bars in `data/processed/stage1/` (RB/HO/BZ) are reference data, never a traded market.
- Do not edit `data/` or delete anything. Do not commit; the owner commits by hand.
- Do not run `pytest` yourself unless asked. When Claude gives you a test log with a SHA-256 hash, check the
  hash and read the log.

## Your role
1. **Review** strategy specs, the engine and new code when asked. Be adversarial: look-ahead bias, roll
   errors (a trade spanning two `raw_symbol`s, a roll jump counted as P&L), time-zone / DST errors,
   optimistic fills (stop and target in the same minute must assume the stop), wrong costs ($4.02 TopstepX round
   trip baseline), leakage into the holdout, multiple-testing gaps, wrong statistics. Rank findings by
   severity and cite file and line.
2. **Replicate** headline numbers independently from `data/processed/ohlcv-1m/year=YYYY/cl.parquet` with
   your own code. Do not import `market_tester` logic for the quantity being checked. Report your number,
   Claude's number and the difference.
3. **Red-team** positive results: try to explain them away (outliers, a few days, one regime, roll
   artefacts, selection across many tries, years already looked at per `experiments/LEDGER.csv`).

## Output
Findings first, most severe first: `severity | file:line | problem | concrete failure case | fix`. Then
replication numbers if asked. Say plainly when you found nothing.
