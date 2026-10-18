"""Inference. Unit of inference: the trading day (trades on one day share news and volatility).

What answers what:
- edge real?                 `clustered_mean` (day-clustered t), `bootstrap_ci` (stationary block bootstrap
                             of whole days)
- edge survives the search?  `deflated_sharpe` (trials counted), `romano_wolf` / `spa_test` (grid as a
                             family), `pbo_cscv` (probability the in-sample best is below median out of sample)
- how bad can it get?        `monte_carlo` (trade order reshuffle = drawdown path risk; day-block resample =
                             P&L and drawdown spread).
                             Reshuffling never changes the total or the mean: it says nothing about edge.

Large-sample normal approximations: fine for hundreds of days, not for tiny samples.
"""

import itertools
import math
from statistics import NormalDist

import numpy as np
import pandas as pd

from .metrics import max_drawdown

ND = NormalDist()
EULER_GAMMA = 0.5772156649015329


def clustered_mean(x, day) -> dict:
    """Mean of x with a day-clustered standard error. p_one_sided tests mean > 0."""
    x = np.asarray(x, float)
    day = np.asarray(day)
    ok = ~np.isnan(x)
    x, day = x[ok], day[ok]
    n = len(x)
    if n < 2:
        return {"mean": float("nan"), "se": float("nan"), "t": float("nan"), "p_one_sided": float("nan"),
                "n": n, "days": 0}
    mean = x.mean()
    resid = pd.Series(x - mean).groupby(day).sum().to_numpy()
    k = len(resid)
    var = (resid ** 2).sum() / n ** 2 * k / max(k - 1, 1)
    se = math.sqrt(var) if var > 0 else float("nan")
    t = mean / se if se > 0 else float("nan")
    p = 1 - ND.cdf(t) if not math.isnan(t) else float("nan")
    return {"mean": float(mean), "se": se, "t": float(t), "p_one_sided": float(p), "n": n, "days": k}


def stationary_bootstrap_indices(n: int, mean_block: float, rng) -> np.ndarray:
    """Politis-Romano stationary bootstrap: blocks of geometric length (mean `mean_block`) that wrap around."""
    if n < 1 or mean_block < 1:
        raise ValueError("need n >= 1 and mean_block >= 1")
    new = rng.random(n) < 1.0 / mean_block
    new[0] = True
    starts = rng.integers(0, n, n)
    block_id = np.cumsum(new) - 1
    first = np.flatnonzero(new)
    offset = np.arange(n) - first[block_id]
    return (starts[first][block_id] + offset) % n


def bootstrap_ci(day_pnl, day_trades, n_boot: int = 2000, mean_block: float = 5.0, conf: float = 0.95,
                 seed: int = 0) -> dict:
    """Percentile CIs of mean P&L per day and per trade (sum pnl / sum trades), resampling whole days."""
    pnl = np.asarray(day_pnl, float)
    n_tr = np.asarray(day_trades, float)
    rng = np.random.default_rng(seed)
    per_day, per_trade = np.empty(n_boot), np.empty(n_boot)
    for b in range(n_boot):
        i = stationary_bootstrap_indices(len(pnl), mean_block, rng)
        per_day[b] = pnl[i].mean()
        k = n_tr[i].sum()
        per_trade[b] = pnl[i].sum() / k if k > 0 else np.nan
    a = (1 - conf) / 2
    q = lambda v: [float(np.nanquantile(v, a)), float(np.nanquantile(v, 1 - a))]  # noqa: E731
    return {"per_day": pnl.mean(), "per_day_ci": q(per_day),
            "per_trade": pnl.sum() / n_tr.sum() if n_tr.sum() else float("nan"), "per_trade_ci": q(per_trade),
            "p_per_trade_le_0": float(np.nanmean(per_trade <= 0)), "conf": conf, "n_boot": n_boot,
            "mean_block_days": mean_block}


def monte_carlo(trade_pnl, day_pnl=None, n_sims: int = 5000, seed: int = 0, capital: float | None = None,
                mean_block: float = 5.0) -> dict:
    """Risk, not edge. `shuffle` = same trades in random order: max drawdown distribution of the path.
    `resample` = stationary block bootstrap of whole trading days (keeps loss clusters and volatility
    regimes): spread of total P&L and drawdown, P(total <= 0). Falls back to trades if no day_pnl.
    `capital`: share of paths whose drawdown reaches it (risk of ruin on that account size)."""
    x = np.asarray(trade_pnl, float)
    days = x if day_pnl is None else np.asarray(day_pnl, float)
    rng = np.random.default_rng(seed)
    dd_shuffle = np.array([max_drawdown(rng.permutation(x)) for _ in range(n_sims)])
    total, dd_res = np.empty(n_sims), np.empty(n_sims)
    for s in range(n_sims):
        r = days[stationary_bootstrap_indices(len(days), mean_block if day_pnl is not None else 1.0, rng)]
        total[s], dd_res[s] = r.sum(), max_drawdown(r)
    qs = [0.5, 0.95, 0.99]
    out = {"trades": len(x), "actual_max_dd": max_drawdown(x),
           "shuffle_max_dd": dict(zip(["p50", "p95", "p99"], np.quantile(dd_shuffle, qs).tolist())),
           "resample_unit": "day blocks" if day_pnl is not None else "trades",
           "resample_max_dd": dict(zip(["p50", "p95", "p99"], np.quantile(dd_res, qs).tolist())),
           "resample_total": dict(zip(["p5", "p50", "p95"], np.quantile(total, [0.05, 0.5, 0.95]).tolist())),
           "p_total_le_0": float((total <= 0).mean())}
    if capital is not None:
        out["capital"] = capital
        out["p_ruin_shuffle"] = float((dd_shuffle >= capital).mean())
        out["p_ruin_resample"] = float((dd_res >= capital).mean())
    return out


def effective_n(x, lags: int = 10) -> float:
    """Sample size of an autocorrelated daily series (Bartlett-weighted autocorrelations)."""
    x = np.asarray(x, float) - np.mean(x)
    v = (x * x).mean()
    if v == 0:
        return float(len(x))
    rho = [(x[k:] * x[:-k]).mean() / v for k in range(1, lags + 1)]
    factor = 1 + 2 * sum((1 - k / (lags + 1)) * r for k, r in enumerate(rho, 1))
    return float(len(x) / max(factor, 1e-9))


def expected_max_sharpe(sharpe_var: float, n_trials: float) -> float:
    """Bailey & Lopez de Prado (2014): expected best Sharpe of n_trials zero-edge strategies."""
    if n_trials < 2:
        return 0.0
    return math.sqrt(sharpe_var) * ((1 - EULER_GAMMA) * ND.inv_cdf(1 - 1 / n_trials)
                                    + EULER_GAMMA * ND.inv_cdf(1 - 1 / (n_trials * math.e)))


def deflated_sharpe(day_pnl, n_trials: float, sharpe_var: float) -> dict:
    """Deflated Sharpe ratio: P(true per-day Sharpe > best-of-n_trials luck), with skew / kurtosis and the
    effective number of days. `sharpe_var` = variance of per-day (not annual) Sharpe across the trials."""
    x = np.asarray(day_pnl, float)
    sd = x.std()
    if sd == 0:
        return {"dsr": float("nan"), "sr": float("nan"), "sr0": float("nan"), "n_trials": n_trials}
    sr = x.mean() / sd
    sr0 = expected_max_sharpe(sharpe_var, n_trials)
    e = x - x.mean()
    skew, kurt = (e ** 3).mean() / sd ** 3, (e ** 4).mean() / sd ** 4
    n_obs = effective_n(x)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr ** 2))
    dsr = ND.cdf((sr - sr0) * math.sqrt(max(n_obs - 1, 1)) / denom)
    return {"dsr": float(dsr), "sr": float(sr), "sr0": float(sr0), "n_trials": n_trials, "n_obs_eff": n_obs}


def _boot_means(x: np.ndarray, n_boot: int, mean_block: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.stack([x[stationary_bootstrap_indices(len(x), mean_block, rng)].mean(axis=0) for _ in range(n_boot)])


def _check_matrix(x):
    x = np.asarray(x, float)
    if x.ndim != 2 or len(x) < 10 or not np.isfinite(x).all():
        raise ValueError("x must be a finite 2-D array (days x configs) with >= 10 rows")
    return x


def romano_wolf(x, n_boot: int = 1000, mean_block: float = 5.0, seed: int = 0) -> dict:
    """Romano-Wolf step-down max-t for H0_k: mean_k <= 0, k = columns (configs), rows = days in time order
    (0 without a trade). Bootstrapping whole rows keeps the dependence between configs. Adjusted p-values."""
    x = _check_matrix(x)
    n, k = x.shape
    mean, sd = x.mean(axis=0), x.std(axis=0, ddof=1)
    se = np.where(sd > 0, sd / math.sqrt(n), np.inf)
    t = mean / se
    tb = (_boot_means(x, n_boot, mean_block, seed) - mean) / se  # centred: null imposed
    order = np.argsort(-t)
    p_adj = np.empty(k)
    prev = 0.0
    for j, col in enumerate(order):
        mx = tb[:, order[j:]].max(axis=1)
        prev = max(prev, float(((mx >= t[col]).sum() + 1) / (n_boot + 1)))  # step-down monotonicity
        p_adj[col] = prev
    return {"t": t, "p_adj": p_adj}


def spa_test(x, n_boot: int = 1000, mean_block: float = 5.0, seed: int = 0) -> dict:
    """Hansen (2005) SPA (consistent version), H0: no config beats flat. Small p = the best one is real.
    Each column is scaled by its long-run (stationary-bootstrap) standard error of the mean, as Hansen
    requires for autocorrelated P&L; the same scale is used in the statistic and the recentring rule."""
    x = _check_matrix(x)
    n, _ = x.shape
    mean = x.mean(axis=0)
    bm = _boot_means(x, n_boot, mean_block, seed)
    se = bm.std(axis=0, ddof=1)
    se = np.where(se > 0, se, np.inf)
    stat = max(0.0, float((mean / se).max()))
    keep = mean >= -se * math.sqrt(2 * math.log(math.log(n)))  # recentre only plausible nulls
    mu_c = np.where(keep, mean, 0.0)
    tb = np.maximum(0.0, ((bm - mu_c) / se).max(axis=1))
    return {"stat": stat, "p": float(((tb >= stat).sum() + 1) / (n_boot + 1))}


def pbo_cscv(x, n_blocks: int = 16, max_combos: int = 20000, seed: int = 0) -> dict:
    """Bailey et al. (2017) CSCV probability of backtest overfitting. x = days x configs (time order).
    Rows are cut into n_blocks; every half of the blocks is in-sample once, the rest out of sample.
    PBO = share of splits where the in-sample best (per-day Sharpe) ranks at or below the OOS median."""
    x = _check_matrix(x)
    n, k = x.shape
    if k < 2:
        return {"pbo": float("nan"), "splits": 0}
    if n_blocks % 2 or n < 2 * n_blocks:
        raise ValueError("n_blocks must be even and at most half the number of rows")
    edges = np.linspace(0, n, n_blocks + 1).astype(int)
    s1 = np.stack([x[a:b].sum(axis=0) for a, b in zip(edges[:-1], edges[1:])])
    s2 = np.stack([(x[a:b] ** 2).sum(axis=0) for a, b in zip(edges[:-1], edges[1:])])
    cnt = np.diff(edges).astype(float)
    combos = list(itertools.combinations(range(n_blocks), n_blocks // 2))
    if len(combos) > max_combos:
        rng = np.random.default_rng(seed)
        combos = [combos[i] for i in rng.choice(len(combos), max_combos, replace=False)]

    def sr(idx):
        m = cnt[idx].sum()
        mu = s1[idx].sum(axis=0) / m
        var = s2[idx].sum(axis=0) / m - mu ** 2
        return np.where(var > 0, mu / np.sqrt(np.maximum(var, 1e-300)), 0.0)

    logits = np.empty(len(combos))
    everything = set(range(n_blocks))
    for c, is_blocks in enumerate(combos):
        is_idx = list(is_blocks)
        oos_idx = sorted(everything - set(is_blocks))
        best = int(np.argmax(sr(is_idx)))
        oos = sr(oos_idx)
        rank = (oos < oos[best]).sum() + 0.5 * ((oos == oos[best]).sum() - 1) + 1  # 1..k, ties averaged
        w = rank / (k + 1)
        logits[c] = math.log(w / (1 - w))
    return {"pbo": float((logits <= 0).mean()), "splits": len(combos), "logit_median": float(np.median(logits))}
