# O10 stage 1: blind power check

SPEC rev 4 stage 1. Bundle 1aaa46a80562. alpha = 0.05/204, power 0.8, z sum 4.328. Entries from `orb.entries` (no exits); sd = per-trade net $ of the variance model (`orb.variance_draws`: week-shuffled days, the situation's own delayed-stop exits; 20 draws), not the strategy's P&L.

| situation | sl | tp | trades | trade_sd_usd | mde_usd | ok |
|---|---|---|---|---|---|---|
| 1 | 0 | 30 | 2824 | 194.388 | 15.830 | False |
| 2 | 16 | 30 | 2824 | 185.607 | 15.115 | False |
| 3 | 0 | 30 | 2824 | 250.090 | 20.367 | False |
| 4 | 16 | 30 | 2824 | 218.869 | 17.824 | False |
| 5 | 16 | 30 | 2736 | 167.950 | 13.896 | False |
| 6 | 16 | 30 | 2472 | 182.253 | 15.864 | False |


Rule: proceed if MDE <= $4.02 for >= 4 of 6 situations. **STOP**
