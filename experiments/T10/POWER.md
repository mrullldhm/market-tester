# T102 power (SPEC rev 3), before any strategy P&L

Inputs: 847 dev signal days (390 D), unsigned front 10:00->flat move; random sides; planted net edge k ticks/trade; B = 49999, mean block 5 days; 100 sims per k. Not simulated: random-entry, walk-forward, 2019-23 block, DSR, look-ahead (all only make a pass harder).
Holdout proxy = signal days 2021-03-01..2023-12-31 (193 trades, 85 D).

| net edge ticks | dev all gates | dev + D gate | holdout proxy |
|---|---|---|---|
| 0 | 0.01 | 0.01 | 0.06 |
| 2 | 0.00 | 0.00 | 0.03 |
| 4 | 0.04 | 0.02 | 0.05 |
| 6 | 0.11 | 0.08 | 0.13 |
| 8 | 0.20 | 0.19 | 0.06 |
| 10 | 0.38 | 0.28 | 0.12 |
| 12 | 0.52 | 0.45 | 0.11 |
| 14 | 0.74 | 0.68 | 0.26 |
| 16 | 0.93 | 0.89 | 0.35 |
| 18 | 1.00 | 0.95 | 0.33 |
| 20 | 1.00 | 1.00 | 0.38 |

k at 80% power (dev + D): 16 net ticks. Above 10: a FAIL is reported as underpowered.
