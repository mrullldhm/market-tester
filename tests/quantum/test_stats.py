"""Statistics against cases with a known answer."""

import numpy as np
import pytest

from market_tester.quantum import stats
from market_tester.quantum.metrics import max_drawdown


def test_stationary_bootstrap_indices_are_wrapped_blocks():
    rng = np.random.default_rng(0)
    idx = stats.stationary_bootstrap_indices(1000, 10.0, rng)
    assert idx.min() >= 0 and idx.max() < 1000
    cont = np.mean(np.diff(idx) % 1000 == 1)  # share of steps that continue a block
    assert 0.85 < cont < 0.95  # 1 - 1/mean_block


def test_bootstrap_ci_covers_true_mean():
    rng = np.random.default_rng(1)
    hits = 0
    for s in range(40):
        x = rng.normal(5, 20, 500)
        ci = stats.bootstrap_ci(x, np.ones(500), n_boot=400, mean_block=1, seed=s)["per_day_ci"]
        hits += ci[0] <= 5 <= ci[1]
    assert hits >= 34  # ~95% coverage, loose for 40 trials


def test_bootstrap_per_trade_is_ratio_of_sums():
    pnl, n = np.array([10.0, 0, 30, -10] * 10), np.array([1, 0, 2, 1] * 10)
    assert stats.bootstrap_ci(pnl, n, n_boot=50)["per_trade"] == pytest.approx(30 / 4)


def test_clustered_mean_wider_than_iid_when_days_cluster():
    rng = np.random.default_rng(2)
    day = np.repeat(np.arange(200), 10)
    x = np.repeat(rng.normal(0, 10, 200), 10) + rng.normal(0, 1, 2000)  # day shock shared by 10 trades
    cl = stats.clustered_mean(x, day)
    iid_se = x.std(ddof=1) / np.sqrt(len(x))
    assert cl["se"] > 2.5 * iid_se and cl["days"] == 200


def test_monte_carlo_shuffle_keeps_total_and_orders_quantiles():
    rng = np.random.default_rng(3)
    x = rng.normal(2, 50, 400)
    mc = stats.monte_carlo(x, n_sims=500, seed=0, capital=1000)
    q = mc["shuffle_max_dd"]
    assert q["p50"] <= q["p95"] <= q["p99"]
    assert mc["actual_max_dd"] == pytest.approx(max_drawdown(x))
    assert 0 <= mc["p_total_le_0"] <= 1 and 0 <= mc["p_ruin_shuffle"] <= 1


def test_deflated_sharpe_falls_with_more_trials():
    rng = np.random.default_rng(4)
    x = rng.normal(0.1, 1, 2000)  # per-day Sharpe 0.1
    few = stats.deflated_sharpe(x, 1, 1 / 2000)["dsr"]
    many = stats.deflated_sharpe(x, 1000, 1 / 2000)["dsr"]
    assert few > 0.99 and many < few


def test_expected_max_sharpe_matches_simulation():
    rng = np.random.default_rng(5)
    t, k = 1000, 200
    sims = [(rng.normal(0, 1, (t, k)).mean(0) / 1).max() * np.sqrt(1) for _ in range(30)]
    # per-day Sharpe of k zero-edge strategies has variance 1/t
    assert stats.expected_max_sharpe(1 / t, k) == pytest.approx(np.mean(sims), rel=0.1)


def test_pbo_noise_near_half_and_real_edge_low():
    rng = np.random.default_rng(6)
    noise = rng.normal(0, 1, (800, 50))
    assert 0.3 < stats.pbo_cscv(noise, n_blocks=10)["pbo"] < 0.7
    real = noise.copy()
    real[:, 7] += 0.3
    assert stats.pbo_cscv(real, n_blocks=10)["pbo"] < 0.1


def test_romano_wolf_and_spa():
    rng = np.random.default_rng(7)
    x = rng.normal(0, 1, (1000, 20))
    rw = stats.romano_wolf(x, n_boot=300, mean_block=1)
    assert rw["p_adj"].min() > 0.05
    x[:, 3] += 0.25
    rw = stats.romano_wolf(x, n_boot=300, mean_block=1)
    assert rw["p_adj"][3] < 0.01 and np.delete(rw["p_adj"], 3).min() > 0.05
    assert stats.spa_test(x, n_boot=300, mean_block=1)["p"] < 0.01
    with pytest.raises(ValueError):
        stats.romano_wolf(np.zeros((5, 2)))
