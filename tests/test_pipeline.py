"""Grid, walk-forward, random-entry baseline, look-ahead check and the full evaluate() on synthetic bars."""

import numpy as np
import pandas as pd
import pytest

from market_tester import ledger, prereg, report
from market_tester.baseline import random_entry_test
from market_tester.engine import Market, run, with_costs
from market_tester.optimize import GridResult, grid_configs, plateau, run_grid, walk_forward
from market_tester.report import evaluate, lookahead_check
from market_tester.strategy import Signals, first_bar_at
from market_tester.toys import CoinFlip, MorningMomentum, TimeOfDay
from market_tester.validate import coin_flip, plant_drift

GRID = {"minute": [9 * 60 + 58], "anchor": [8 * 60 + 30, 9 * 60, 9 * 60 + 30], "hold": [50, 60, 70]}


class Cheater:
    """Uses the NEXT bar's close: look-ahead the check must catch."""
    name = "cheater"

    def signals(self, bars, hold=5):
        nxt = bars["close_ticks"].shift(-1)
        side = np.sign(nxt - bars["close_ticks"]).fillna(0).astype(np.int64).to_numpy().copy()
        side[np.arange(len(side)) % 97 != 0] = 0
        return Signals(side, hold_minutes=hold)


def test_plateau_median_of_neighbours():
    s = np.array([0, 0, 9, 0, 0, 0, 5, 5, 5], float)  # 3x3: lone spike at (0, 2), ridge on row 2
    p = plateau(s, (3, 3))
    assert p[2] == 0 and p.argmax() in (6, 7, 8)


def test_walk_forward_uses_only_past():
    days = pd.bdate_range("2010-01-01", "2015-12-31")
    rng = np.random.default_rng(0)
    pnl = rng.normal(0, 1, (len(days), 2))
    pnl[days.year < 2013, 0] += 0.5  # config 0 good before 2013

    def picks(p):
        res = GridResult({"k": [0, 1]}, [{"k": 0}, {"k": 1}], days, p, np.ones_like(p, dtype=np.int64), 1)
        return walk_forward(res, min_train_years=3, use_plateau=False)["years"]

    base = picks(pnl)
    assert base["year"].tolist() == [2013, 2014, 2015] and base["param_k"].iloc[0] == 0
    future = pnl.copy()
    future[days.year == 2015, 1] += 100  # change only the last test year
    changed = picks(future)
    assert changed["param_k"].tolist() == base["param_k"].tolist()  # picks never see their test year
    assert changed["total_net_usd"].iloc[:2].tolist() == base["total_net_usd"].iloc[:2].tolist()


def test_grid_parallel_equals_serial(synth):
    grid = {"minute": [600, 660], "hold": [30, 60]}
    a = run_grid(TimeOfDay(), synth, grid, n_jobs=1, progress=False)
    b = run_grid(TimeOfDay(), synth, grid, n_jobs=2, progress=False)
    assert np.array_equal(a.pnl, b.pnl) and np.array_equal(a.trades, b.trades)


def test_coin_flip_on_synthetic(synth):
    cf = coin_flip(synth, n=5000)
    assert cf["max_pair_error_usd"] < 1e-9 and cf["pairs"] > 500
    assert abs(cf["z"]) < 3


def test_random_entry_detects_entry_edge_not_drift(synth):
    planted = plant_drift(synth, 0.25)
    m = Market.from_bars(planted)
    sig = MorningMomentum().signals(planted)
    trades = with_costs(run(m, sig))
    assert trades["gross_ticks"].mean() > 10
    assert random_entry_test(m, trades, sig, n_draws=40)["p"] < 0.05
    m0 = Market.from_bars(synth)
    sig0 = MorningMomentum().signals(synth)
    assert random_entry_test(m0, with_costs(run(m0, sig0)), sig0, n_draws=40)["p"] > 0.05


def test_lookahead_check_catches_cheater(synth):
    assert lookahead_check(CoinFlip(), synth, {})["ok"]
    assert lookahead_check(MorningMomentum(), synth, {})["ok"]
    assert run(Market.from_bars(synth), Cheater().signals(synth))["gross_ticks"].mean() > 0
    assert not lookahead_check(Cheater(), synth, {})["ok"]


def test_no_trade_crosses_roll(synth):
    t = run(Market.from_bars(synth), Signals(np.ones(len(synth), np.int64), hold_minutes=600))
    assert (synth["raw_symbol"].to_numpy()[t["entry_bar"]] == synth["raw_symbol"].to_numpy()[t["exit_bar"]]).all()
    assert (t["reason"] == "roll").sum() >= 9


KW = dict(n_boot=200, n_random=40, n_mc=200, gates={"min_trades": 100, "recent_years": [2018, 2019]})
KW_DEV = dict(n_boot=200, n_random=40, n_mc=200)  # dev runs take default (or stricter) gates only


def test_evaluate_planted_passes_and_null_fails(synth, tmp_experiments):
    good = evaluate(MorningMomentum(), GRID, item_id="X001", hypothesis="planted", bars=plant_drift(synth, 0.25),
                    kind="synthetic", **KW)
    assert good["verdict"] == "PASS", good["gate_table"]
    null = evaluate(MorningMomentum(), GRID, item_id="X002", hypothesis="null", bars=synth, kind="synthetic", **KW)
    assert null["verdict"] == "FAIL"
    rows = ledger.read()
    assert rows["item_id"].tolist() == ["X001", "X002"] and set(rows["kind"]) == {"synthetic"}
    assert (tmp_experiments / "X001").is_dir()


def test_evaluate_refuses_custom_bars_as_dev(synth, tmp_experiments):
    with pytest.raises(ValueError):
        evaluate(TimeOfDay(), {}, item_id="X003", hypothesis="x", bars=synth, **KW)


def test_tiny_edge_below_cost_fails(synth, tmp_experiments):
    tiny = evaluate(MorningMomentum(), GRID, item_id="X004", hypothesis="1 tick edge, cost 2.4 ticks",
                    bars=plant_drift(synth, 1 / 60), kind="synthetic", **KW)
    assert tiny["verdict"] == "FAIL"


def test_evaluate_holdout_runs_once_and_logs(synth, tmp_experiments, monkeypatch, tmp_path):
    seen = []

    def fake_load(period, token=None, **kw):
        assert period.name == "holdout" and prereg.is_live(token)  # the real lock is used
        seen.append(token)
        return plant_drift(synth, 0.25)

    monkeypatch.setattr(report, "load_period", fake_load)
    frozen = tmp_path / "bars.parquet"
    frozen.write_bytes(b"bars")
    spec = {"item_id": "H001", "hypothesis": "planted", "strategy": report.strategy_id(MorningMomentum()),
            "params": {"anchor": 540, "hold": 60}, "period": "holdout", "gates": {}, "slip_ticks": 1, "seed": 0,
            "approved_by_user": True, "codex_review": "R000"}
    path = prereg.register(spec, MorningMomentum(), [frozen])
    with pytest.raises(prereg.PreregError):
        report.evaluate_holdout(TimeOfDay(), path)  # wrong strategy refused before the holdout opens
    assert not seen
    r = report.evaluate_holdout(MorningMomentum(), path, n_boot=100, n_random=20, n_mc=100)
    assert r["verdict"] == "PASS" and len(seen) == 1 and not prereg.is_live(seen[0])
    with pytest.raises(prereg.PreregError):
        report.evaluate_holdout(MorningMomentum(), path)  # one run, ever
    assert ledger.read()["kind"].tolist() == ["prereg", "holdout", "holdout"]


def test_evaluate_rejects_unknown_kind_and_unlogged_dev(synth, tmp_experiments):
    with pytest.raises(ValueError):
        evaluate(TimeOfDay(), {}, item_id="X005", hypothesis="x", bars=synth, kind="other", **KW)
    with pytest.raises(ValueError):
        evaluate(TimeOfDay(), {}, item_id="X006", hypothesis="x", write=False, **KW)


def test_dev_run_counts_trials_before_running(synth, tmp_experiments, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("crash while loading")

    monkeypatch.setattr(report, "load_period", boom)
    with pytest.raises(RuntimeError, match="crash while loading"):
        evaluate(MorningMomentum(), GRID, item_id="S010", hypothesis="x", family="momo", **KW_DEV)
    assert ledger.configs_tried("momo") == 9  # the crashed run still counts
    monkeypatch.setattr(report, "load_period", lambda *a, **k: synth)
    r = evaluate(MorningMomentum(), GRID, item_id="S011", hypothesis="renamed", family="momo", **KW_DEV)
    assert r["n_trials"] == 18 and ledger.configs_tried("momo") == 18
    assert len(ledger.trial_sharpes("momo")) == 9
    assert ledger.read()["kind"].tolist() == ["dev_start", "dev_start", "dev"]


class Fitter:
    name = "fitter"

    def fit(self, bars):
        pass

    def signals(self, bars):
        return Signals(np.zeros(len(bars), np.int64))


def test_fitting_strategies_refused(synth, tmp_experiments):
    with pytest.raises(ValueError):
        evaluate(Fitter(), {}, item_id="X007", hypothesis="x", bars=synth, kind="synthetic", **KW)


class ExitPeeker:
    """Causal entries, but the exit uses the next bar: the check must still catch it."""
    name = "exit_peeker"

    def signals(self, bars):
        base = MorningMomentum().signals(bars)
        nxt = bars["close_ticks"].shift(-1).to_numpy()
        exit_ = (nxt < bars["close_ticks"].to_numpy()) & (bars["et_minute"].to_numpy() > 600)
        return Signals(base.side, hold_minutes=60, exit=exit_)


class GlobalScaler:
    """Threshold from the whole sample's mean: changes every early signal when the future is cut."""
    name = "global_scaler"

    def signals(self, bars):
        c = bars["close_ticks"].to_numpy()
        side = np.where(c > c.mean(), 1, -1) * first_bar_at(bars, 600)
        return Signals(side.astype(np.int64), hold_minutes=30)


def test_lookahead_catches_exit_and_global_leaks(synth):
    assert not lookahead_check(ExitPeeker(), synth, {})["ok"]
    assert not lookahead_check(GlobalScaler(), synth, {})["ok"]


def test_random_entry_keeps_trade_count(synth):
    m = Market.from_bars(synth)
    sig = MorningMomentum().signals(synth)
    trades = with_costs(run(m, sig))
    r = random_entry_test(m, trades, sig, n_draws=20)
    assert r["accepted"] == 20 and r["random_trades_median"] >= 0.95 * len(trades)


def test_week_permutation_is_bijection_within_week():
    from market_tester.baseline import week_permutation
    week = np.repeat(np.arange(50), 5)
    partner = week_permutation(week, np.random.default_rng(0))
    assert sorted(partner) == list(range(250)) and (week[partner] == week).all()
    assert (partner != np.arange(250)).mean() > 0.5


@pytest.mark.parametrize("grid", [{"a": []}, {"a": [1, 1]}, {"a": [3, 2, 1]}, {"a": 5}, {"a": [1, float("nan")]},
                                  {"a": [1, np.int64(1)]}, {"a": ["x", "x"]}])
def test_bad_grids_rejected(grid):
    with pytest.raises(ValueError):
        grid_configs(grid)


def test_engine_rejects_bad_bars(synth):
    from market_tester.config import MarketNotAllowed
    from market_tester.data import DataIntegrityError
    head = synth.iloc[:50].copy()
    with pytest.raises(MarketNotAllowed):
        Market.from_bars(head.assign(raw_symbol="MCLJ5"))
    broken = head.copy()
    broken.loc[3, "high_ticks"] = broken.loc[3, "low_ticks"] - 1
    with pytest.raises(DataIntegrityError):
        Market.from_bars(broken)
    with pytest.raises(DataIntegrityError):
        Market.from_bars(pd.concat([head.iloc[:5], head.iloc[4:6]], ignore_index=True))


@pytest.mark.parametrize("kw", [{"slip_ticks": 0}, {"slip_ticks": False}, {"slip_ticks": float("nan")},
                                {"gates": {"min_trades": 1}}, {"gates": {"dsr_min": 0.5}},
                                {"gates": {"romano_wolf_p_max": 1.0}}, {"gates": {"recent_years": [2010, 2011]}},
                                {"gates": {"made_up": 1}}, {"gates": {"pbo_max": float("nan")}}])
def test_dev_run_refuses_cheaper_costs_or_looser_gates(kw, tmp_experiments, monkeypatch, synth):
    monkeypatch.setattr(report, "load_period", lambda *a, **k: synth)
    with pytest.raises(prereg.PreregError):
        evaluate(TimeOfDay(), {}, item_id="X008", hypothesis="x", **kw)
    assert ledger.read().empty  # refused before anything is logged or loaded


def test_dev_run_accepts_stricter_gates(tmp_experiments, monkeypatch, synth):
    monkeypatch.setattr(report, "load_period", lambda *a, **k: synth)
    r = evaluate(TimeOfDay(), {"hold": [30, 60]}, item_id="X009", hypothesis="x", slip_ticks=2,
                 gates={"min_trades": 400, "dsr_min": 0.99}, n_boot=50, n_random=10, n_mc=50)
    assert r["verdict"] == "FAIL" and r["slip_ticks"] == 2


def test_mixed_axis_equal_values_rejected():
    with pytest.raises(ValueError):
        grid_configs({"a": [1, "middle", 1.0]})


def test_dev_run_cannot_narrow_years_but_exploratory_can(tmp_experiments, monkeypatch, synth):
    monkeypatch.setattr(report, "load_period", lambda *a, **k: synth)
    with pytest.raises(ValueError):
        evaluate(TimeOfDay(), {}, item_id="X010", hypothesis="x", years=[2019, 2020, 2021, 2022, 2023], **KW_DEV)
    r = evaluate(TimeOfDay(), {"hold": [30, 60]}, item_id="X011", family="tod", hypothesis="x",
                 years=[2018, 2019], kind="exploratory", **KW_DEV)
    assert r["verdict"].startswith("EXPLORATORY-")
    assert ledger.configs_tried("tod") == 2
    assert ledger.read()["kind"].tolist() == ["exploratory", "exploratory_result"]
