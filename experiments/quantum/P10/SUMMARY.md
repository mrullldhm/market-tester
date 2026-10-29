# P10 dev summary (Claude's numbers, not yet replicated by Codex)

Spec: SPEC.md rev 3, code 9d70ef73d708. Dev 2010-06-07..2023-12-31, $4.02 round trip (TopstepX commission; stress 1 tick/side), 1999 random-entry draws. Candidate rule: SPEC.md 'Statistics and candidate rule'.

| item | family | strategy | verdict | trades | traded_days | gross_usd_per_trade | net_usd_per_trade | best_week_out | years_net_pos | profit_p | random_p | profit_p_holm | random_p_holm | batch_dsr | candidate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| P101 | im | intraday_momentum | FAIL | 3167 | 3167 | 5.57 | 1.55 | 0.34 | 6/14 | 0.4128 | 0.1095 | 1.0000 | 0.8760 | 0.0179 | False |
| P102 | noise_area | noise_area | PASS | 2837 | 1934 | 29.16 | 25.14 | 23.53 | 11/14 | 0.0075 | 0.0045 | 0.0750 | 0.0405 | 0.0000 | False |
| P103 | eia_momentum | eia_momentum | FAIL | 755 | 755 | 6.73 | 2.71 | -0.15 | 9/14 | 0.4258 | 0.4320 | 1.0000 | 1.0000 | 0.0145 | False |
| P104 | gap_fade | gap_fade | FAIL | 2609 | 2609 | 5.50 | 1.48 | -1.40 | 8/14 | 0.4593 | 0.3380 | 1.0000 | 1.0000 | 0.0078 | False |
| P105 | bollinger | bollinger | FAIL | 7880 | 3452 | -10.69 | -14.71 | -15.62 | 4/14 | 0.9960 | 0.9950 | 1.0000 | 1.0000 | 0.0000 | False |
| P106 | rsi2 | rsi2 | FAIL | 7598 | 3239 | 3.39 | -0.63 | -1.03 | 6/14 | 0.5827 | 0.2990 | 1.0000 | 1.0000 | 0.0050 | False |
| P107 | pivot | pivot | FAIL | 1645 | 1534 | -28.29 | -32.31 | -35.42 | 4/14 | 0.9770 | 0.8620 | 1.0000 | 1.0000 | 0.0000 | False |
| P108 | turtle_soup | turtle_soup | FAIL | 1879 | 1827 | 4.00 | -0.02 | -3.60 | 6/14 | 0.5147 | 0.0025 | 1.0000 | 0.0250 | 0.0122 | False |
| P109 | donchian | donchian | FAIL | 7174 | 3445 | 10.67 | 6.65 | 4.97 | 9/14 | 0.1674 | nan | 1.0000 | 1.0000 | 0.0906 | False |
| P110 | macd | macd | FAIL | 15434 | 3474 | 9.76 | 5.74 | 4.94 | 9/14 | 0.0495 | nan | 0.4453 | 1.0000 | 0.0001 | False |

Candidates: none
