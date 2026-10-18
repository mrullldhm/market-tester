# market-tester — rules for Claude

- **CL only. MCL is banned** (non-syariah for the owner). Never propose, download or test MCL or any other
  market. Keep `assert_allowed` on every data path.
- **Goal order:** profitable CL strategy first (after costs, on data not used to build it); trading bot last.
  Prop-firm rules are not a goal for now.
- **Read `LESSONS.md`** before proposing a strategy. Do not re-test a dead idea without a new angle; respect
  the "data already seen" table.
- **Codex gate:** Claude builds; Codex reviews and independently replicates (`AGENTS.md`) before any result is
  called real. Give Codex a hashed pytest log, never ask it to run pytest.
- Before the first strategy test: write down the holdout period and log every look at it.
- Costs baseline: $4.02 fee + 1 tick slippage per side = $24.02 round trip on 1 CL. Stop and target in the same
  minute = stop. Roll jumps are never P&L; a trade must not span two `raw_symbol`s.
- Long jobs print live progress.
