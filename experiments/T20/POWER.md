# T201 power (SPEC rev 2), before any strategy P&L

1841 dev trades (890 D), unsigned 10:00 -> exit move, random sides (common across k), B = 49999. Holdout proxy 2021-03-01..2023-12-31: 406 trades (195 D). Exits not at 10:59: 1 (latest ET minute 659).
Simulated dev gates: trades, net > 0, 2-tick stress, profitability p <= 0.002, 2019-23 block, global DSR; not simulated: random-entry, walk-forward, look-ahead, so dev power is an upper bound.

| k net ticks | sims | dev all gates | dev + D | D alone | dev + D 95% LB | D alone 95% LB | holdout proxy |
|---|---|---|---|---|---|---|---|
| 0 | 200 | 0.000 | 0.000 | 0.065 | 0.000 | 0.039 |  |
| 2 | 200 | 0.000 | 0.000 | 0.305 | 0.000 | 0.251 |  |
| 4 | 1000 | 0.000 | 0.000 | 0.700 | 0.000 | 0.675 | 0.207 |
| 6 | 200 | 0.000 | 0.000 | 0.935 | 0.000 | 0.899 |  |

Gate (k = 4, both lower bounds >= 0.8): **FAIL**. Holdout proxy < 50%: a candidate needs >= 6 months forward paper trading.
