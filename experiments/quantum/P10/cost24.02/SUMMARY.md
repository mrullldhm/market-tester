# P10 dev summary (Claude's numbers, not yet replicated by Codex)

Spec: SPEC.md rev 3, code 84ca3523fefc. Dev 2010-06-07..2023-12-31, $24.02 round trip, 1999 random-entry draws. Candidate rule: SPEC.md 'Statistics and candidate rule'.

| item | family | strategy | verdict | trades | traded_days | gross_usd_per_trade | net_usd_per_trade | best_week_out | years_net_pos | profit_p | random_p | profit_p_holm | random_p_holm | batch_dsr | candidate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P101 | im | intraday_momentum | FAIL | 3167 | 3167 | 5.57 | -18.45 | -19.66 | 5/14 | 0.9970 | 0.1095 | 1.0000 | 0.8760 | 0.0000 | False |
| P102 | noise_area | noise_area | FAIL | 2837 | 1934 | 29.16 | 5.14 | 3.53 | 9/14 | 0.3407 | 0.0045 | 1.0000 | 0.0405 | 0.0000 | False |
| P103 | eia_momentum | eia_momentum | FAIL | 755 | 755 | 6.73 | -17.29 | -20.15 | 5/14 | 0.8981 | 0.4320 | 1.0000 | 1.0000 | 0.0000 | False |
| P104 | gap_fade | gap_fade | FAIL | 2609 | 2609 | 5.50 | -14.59 | -17.48 | 4/14 | 0.8292 | 0.3325 | 1.0000 | 1.0000 | 0.0000 | False |
| P105 | bollinger | bollinger | FAIL | 7880 | 3452 | -10.69 | -34.71 | -35.62 | 0/14 | 1.0000 | 0.9950 | 1.0000 | 1.0000 | 0.0000 | False |
| P106 | rsi2 | rsi2 | FAIL | 7598 | 3239 | 3.39 | -20.63 | -21.03 | 1/14 | 1.0000 | 0.2990 | 1.0000 | 1.0000 | 0.0000 | False |
| P107 | pivot | pivot | FAIL | 1645 | 1534 | -28.29 | -49.16 | -52.28 | 3/14 | 0.9990 | 0.8645 | 1.0000 | 1.0000 | 0.0000 | False |
| P108 | turtle_soup | turtle_soup | FAIL | 1879 | 1827 | 4.00 | -20.02 | -23.60 | 4/14 | 0.9041 | 0.0025 | 1.0000 | 0.0250 | 0.0000 | False |
| P109 | donchian | donchian | FAIL | 7174 | 3445 | 10.67 | -13.35 | -15.03 | 2/14 | 0.9730 | nan | 1.0000 | 1.0000 | 0.0000 | False |
| P110 | macd | macd | FAIL | 15434 | 3474 | 9.76 | -14.26 | -15.06 | 3/14 | 1.0000 | nan | 1.0000 | 1.0000 | 0.0000 | False |

Candidates: none

## Codex replication (R-P10-4 blind, R-P10-5 reconciliation)

| Item | Claude trades / net $/trade | Codex | Status |
|---|---|---|---|
| P101 | 3,167 / -18.45 | 3,167 / -18.45 | exact |
| P102 | 2,837 / +5.14 | 2,718 / +5.05 | reading: a bar with a long exit and a long entry re-enters (Claude) or stays flat (Codex). Verdict unchanged (profit p 0.34, 2019-23 -$17.20, 2-tick -$14.86) |
| P103 | 755 / -17.29 | 755 / -17.29 | exact |
| P104 | 2,609 / -14.59 | 2,609 / -14.59 | exact |
| P105 | 7,880 / -34.71 | 7,880 / -34.71 | exact |
| P106 | 7,598 / -20.63 | 7,666 / -20.41, then 7,598 entries exact | Codex bug: indicators grouped by symbol text (CLN0 repeats each decade) |
| P107 | 1,645 / -49.16 | 1,733 / -50.95, then 1,645 entries exact | Codex bug: signals after 13:59 |
| P108 | 1,879 / -20.02 | 1,878 / -19.92 | 1 trade |
| P109 | 7,174 / -13.35 | 7,174 / -13.35 | total differs by $10 |
| P110 | 15,434 / -14.26 | 15,434 / -14.26 | exact |

## Reading
- All ten FAIL on dev; no candidate, so **the holdout stays closed** (not opened, no prereg).
- Only P102 (noise area) is net positive (+$5/trade), and it is not significant (p 0.34), lost money in
  2019-23 and does not survive 2-tick slippage.
- Entries better than random (Holm-adjusted): P102 (0.041) and P108 Turtle Soup (0.025). Both still lose after
  costs: there is timing information, about one round trip too small.
- Mean reversion on 5m bars (P105 Bollinger, P107 pivots) loses before costs: gross -$10.69 and -$28.29.
- P109 / P110 random-entry p is NaN (always-in reversal systems cannot keep 90% of trades on moved days):
  counted as fail by the spec.
