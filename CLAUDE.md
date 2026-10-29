# market-tester — rules for Claude

- **CL only. MCL is banned** (non-syariah for the owner). Never propose, download or test MCL or any other
  market. Keep `assert_allowed` on every data path.
- **Goal order:** profitable CL strategy first (after costs, on data not used to build it); trading bot last.
- **Intraday only:** the bot's end use is a prop firm, so every trade is flat the same session. No multi-day
  holds. Prop-firm rule details (drawdown, payout) come later, after a strategy passes.
- **Two testers, never mixed:** `market_tester/quantum` (conservative engine + statistical gates, dev/holdout
  lock) and `market_tester/tradingview` (TradingView Strategy Tester emulation). Shared code only in
  `market_tester/common`. Strategies live in `strategies/quantum` or `strategies/tradingview`; reports in
  `experiments/quantum` or `experiments/tradingview`. No imports across the two sides (`tests/test_boundary.py`).
- Check `experiments/LEDGER.csv` for what was already run and which periods were looked at.
- **Codex gate:** Claude builds; Codex reviews and independently replicates (`AGENTS.md`) before any result is
  called real. Give Codex a hashed pytest log, never ask it to run pytest.
- Before the first strategy test: write down the holdout period and log every look at it.
- **Costs (both testers): TopstepX CL commission, 1 contract = $4.02 round trip ($2.01 per side).** That is the
  only cost in the baseline; slippage is 0 by default. A 1-tick-per-side slippage stress ($20 more per round
  trip with two market fills) is always reported separately (quantum: a gate). Never call $24.02 "commission".
- Quantum: stop and target in the same minute = stop; target needs a 1-tick trade-through.
- TradingView tester: $50,000 account, 1 CL, TV broker emulator fills (intrabar path). Flat by 16:10 ET
  (Topstep 3:10 PM CT).
- Both: roll jumps are never P&L; a trade must not span two `raw_symbol`s.
- Long jobs print live progress.
