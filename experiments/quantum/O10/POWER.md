# O10 stage 1: blind power check

SPEC rev 4 stage 1. Bundle 1ef81de2b67e. alpha = 0.05/204, power 0.8, z sum 4.328. Entries from `orb.entries` (no exits); sd = per-trade net $ of the variance model (`orb.variance_draws`: week-shuffled days, the situation's own delayed-stop exits; 20 draws), not the strategy's P&L.

| situation | sl | tp | trades | trade_sd_usd | mde_usd | ok |
|---|---|---|---|---|---|---|
| 1 | 0 | 30 | 2824 | 198.740 | 16.185 | True |
| 2 | 16 | 30 | 2824 | 190.001 | 15.473 | True |
| 3 | 0 | 30 | 2824 | 254.712 | 20.743 | True |
| 4 | 16 | 30 | 2824 | 223.515 | 18.202 | True |
| 5 | 16 | 30 | 2736 | 171.893 | 14.222 | True |
| 6 | 16 | 30 | 2472 | 186.369 | 16.222 | True |


Rule: proceed if MDE <= $24.02 for >= 4 of 6 situations. **PROCEED**
