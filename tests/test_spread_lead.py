"""T102 spread lead: second-month join, contract identity, causality, hand-checked signal, eligible baseline."""

import numpy as np
import pandas as pd
import pytest

from market_tester import periods
from market_tester.baseline import _day_index, random_entry_signals, random_entry_test
from market_tester.data import DataIntegrityError, add_roll_flag, attach_second, next_contract_ids
from market_tester.engine import Market, run, with_costs
from market_tester.report import lookahead_check, needs_second
from market_tester.spread_lead import SpreadLead
from market_tester.stats import bootstrap_p, romano_wolf

from conftest import synthetic_bars

ROLL_EVERY = 40


def definitions(n_contracts: int, first_day: pd.Timestamp) -> pd.DataFrame:
    months = "FGHJKMNQUVXZ"
    exp = [pd.Timestamp(first_day, tz="UTC") + pd.Timedelta(days=58 * (c + 1)) for c in range(n_contracts)]
    return pd.DataFrame({"instrument_id": 1000 + np.arange(n_contracts),
                         "raw_symbol": [f"CL{months[c % 12]}{(5 + c // 12) % 10}" for c in range(n_contracts)],
                         "expiration": pd.DatetimeIndex(exp).as_unit("ns")})


def front_and_second(n_days=200, seed=5):
    front = synthetic_bars(n_days=n_days, seed=seed, roll_every=ROLL_EVERY, sd_ticks=4.0)
    contract = np.repeat(np.arange(n_days) // ROLL_EVERY, 390)
    defs = definitions(contract.max() + 2, front["trading_day"].iloc[0])
    front["instrument_id"] = 1000 + contract
    front["expiration"] = defs.set_index("instrument_id").loc[front["instrument_id"], "expiration"].to_numpy()
    front = add_roll_flag(front.assign(continuous_symbol="CL.v.0"))
    rng = np.random.default_rng(seed + 1)
    second = front.copy()
    second["continuous_symbol"] = "CL.v.1"
    second["instrument_id"] = front["instrument_id"] + 1
    second["raw_symbol"] = defs.set_index("instrument_id").loc[second["instrument_id"], "raw_symbol"].to_numpy()
    second["expiration"] = defs.set_index("instrument_id").loc[second["instrument_id"], "expiration"].to_numpy()
    second["close_ticks"] = front["close_ticks"] - 30 + np.round(rng.normal(0, 2, len(front))).astype(np.int64)
    second = add_roll_flag(second)
    return front.reset_index(drop=True), second.reset_index(drop=True), defs


@pytest.fixture(scope="module")
def data():
    front, second, defs = front_and_second()
    return attach_second(front, second, defs), front, second, defs


def test_join_keeps_front_rows_and_flags_next_contract(data):
    bars, front, _, _ = data
    assert len(bars) == len(front) and bars["ts_event_utc"].equals(front["ts_event_utc"])
    assert bars["c2_is_next"].all()
    roll_days = bars.loc[bars["is_roll"], "trading_day"].unique()
    on_roll_day = bars["trading_day"].isin(roll_days).to_numpy()
    assert bars.loc[on_roll_day, "c2_roll_so_far"].all()
    assert not bars.loc[~on_roll_day, "c2_roll_so_far"].any()


def test_second_roll_at_minute_without_front_bar_still_counts():
    front, second, defs = front_and_second()
    roll_ts = second.loc[second["is_roll"], "ts_event_utc"]
    front = front[~front["ts_event_utc"].isin(roll_ts)].reset_index(drop=True)
    bars = attach_second(front, second, defs)
    days = second.loc[second["is_roll"], "trading_day"].unique()
    assert bars.loc[bars["trading_day"].isin(days), "c2_roll_so_far"].all()


def test_contract_identity_is_by_instrument_id():
    front, second, defs = front_and_second()
    nxt = next_contract_ids(defs)
    assert all(nxt[i] == i + 1 for i in defs["instrument_id"].iloc[:-1])
    # a same-name contract from another decade (other id) must not pass as the next contract
    defs2 = pd.concat([defs, pd.DataFrame({"instrument_id": [9999], "raw_symbol": [defs["raw_symbol"].iloc[1]],
                                           "expiration": [defs["expiration"].iloc[-1] + pd.Timedelta(days=3650)]})],
                      ignore_index=True)
    s2 = second.copy()
    first_c = s2["instrument_id"] == 1001
    s2.loc[first_c, "instrument_id"] = 9999
    s2.loc[first_c, "expiration"] = defs2["expiration"].iloc[-1]
    bars = attach_second(front, s2, defs2.sort_values("expiration", ignore_index=True))
    assert not bars.loc[bars["c2_instrument_id"] == 9999, "c2_is_next"].any()
    bad = second.copy()
    bad.loc[0, "expiration"] = bad.loc[0, "expiration"] + pd.Timedelta(days=1)
    with pytest.raises(DataIntegrityError):
        attach_second(front, bad, defs)


def test_causal_and_session_rules(data):
    bars = data[0]
    s = SpreadLead()
    sig = s.signals(bars)
    look = lookahead_check(s, bars, {}, n_cuts=40, full=sig)
    assert look["ok"], look
    trades = run(Market.from_bars(bars), sig)
    assert len(trades) > 20
    assert (trades["et_minute"] == 10 * 60).all()
    out = pd.DatetimeIndex(trades["exit_time"]).tz_convert("America/New_York")
    assert ((out.hour * 60 + out.minute) <= 14 * 60 + 29).all()
    t = s.day_table(bars)
    assert t.loc[trades["trading_day"], "eligible"].all()
    roll_days = bars.loc[bars["is_roll"], "trading_day"].unique()
    assert not t.loc[t.index.isin(roll_days), "eligible"].any()
    assert needs_second(s)


def test_signal_matches_hand_ols(data):
    bars = data[0]
    t = SpreadLead().day_table(bars)
    final = t.index[t["final"]]
    for day in final[[20, 37, 61]]:
        k = final.get_loc(day)
        w = t.loc[final[k - 20:k]]
        beta, alpha = np.polyfit(w["d1"], w["d0"], 1)
        resid = w["d0"] - alpha - beta * w["d1"]
        sigma = np.sqrt((resid ** 2).sum() / 18)
        r = t.loc[day, "d0"] - alpha - beta * t.loc[day, "d1"]
        assert t.loc[day, "r"] == pytest.approx(r) and t.loc[day, "sigma"] == pytest.approx(sigma)
        assert t.loc[day, "side"] == (int(np.sign(r)) if abs(r) >= sigma else 0)
    assert t.loc[final[:20], "side"].eq(0).all()  # no full window yet


def test_day_without_1000_bar_signals_but_never_enters_window(data):
    bars = data[0]
    t0 = SpreadLead().day_table(bars)
    final = t0.index[t0["final"]]
    gap = final[30]
    cut = bars[~((bars["trading_day"] == gap) & (bars["et_minute"] == 10 * 60))].reset_index(drop=True)
    t1 = SpreadLead().day_table(cut)
    assert t1.loc[gap, "eligible"] and not t1.loc[gap, "final"]
    assert t1.loc[gap, "r"] == pytest.approx(t0.loc[gap, "r"])  # today's signal does not peek at 10:00
    nxt = final[31]  # its window now skips `gap` and reaches one final day further back
    w = t1.loc[t1.index[t1["final"] & (t1.index < nxt)][-20:]]
    assert gap not in w.index
    beta, alpha = np.polyfit(w["d1"], w["d0"], 1)
    assert t1.loc[nxt, "r"] == pytest.approx(t1.loc[nxt, "d0"] - alpha - beta * t1.loc[nxt, "d1"])
    assert not SpreadLead().eligible(cut)[(cut["trading_day"] == gap).to_numpy()].any()


def test_null_contract_fields_raise():
    front, second, defs = front_and_second()
    second.loc[5, "instrument_id"] = np.nan
    with pytest.raises(DataIntegrityError):
        attach_second(front, second, defs)


def test_degenerate_window_and_expiry_give_no_signal(data):
    bars = data[0].copy()
    m = bars["et_minute"].to_numpy()
    bars.loc[np.isin(m, [8 * 60 + 59, 9 * 60 + 59]), "c2_close_ticks"] = 5000.0  # d1 = 0 every day
    t = SpreadLead().day_table(bars)
    assert t["eligible"].any() and t["side"].eq(0).all()
    near = data[0].copy()
    near["expiration"] = pd.to_datetime(near["trading_day"]).dt.tz_localize("America/New_York") \
        + pd.Timedelta(days=4, hours=14)  # 4 calendar days to expiry, in ET
    assert not SpreadLead().day_table(near)["eligible"].any()
    near["expiration"] = near["expiration"] + pd.Timedelta(days=1)  # exactly 5: eligible
    assert SpreadLead().day_table(near)["eligible"].any()


def test_random_entries_stay_on_eligible_days(data):
    bars = data[0]
    s = SpreadLead()
    sig = s.signals(bars)
    market = Market.from_bars(bars)
    trades = with_costs(run(market, sig))
    elig = s.eligible(bars)
    index = _day_index(market, elig)
    rng = np.random.default_rng(0)
    for _ in range(20):
        r = random_entry_signals(market, trades, sig, rng, index)
        assert elig[np.flatnonzero(r.side != 0)].all()
    res = random_entry_test(market, trades, sig, n_draws=20, eligible=elig)
    assert res["eligible_mask"] and res["match_rate_median"] > 0.9


def test_bootstrap_p_agrees_with_romano_wolf():
    rng = np.random.default_rng(3)
    x = rng.normal(0.15, 1, 1500)
    p = bootstrap_p(x, n_boot=4999, seed=1)
    rw = romano_wolf(x[:, None], n_boot=4999, seed=1)["p_adj"][0]
    assert abs(p - rw) < 0.02
    assert bootstrap_p(rng.normal(0, 1, 1500), n_boot=999) > 0.05
    assert bootstrap_p(np.r_[np.full(10, 1.0), np.zeros(5)], n_boot=99) >= 1 / 100


def test_holdout_freezes_definitions_with_second():
    assert periods.DEFINITIONS_FILE in periods.HOLDOUT.files(with_second=True)
    assert periods.DEFINITIONS_FILE not in periods.HOLDOUT.files()


# --- T201 (experiments/T20/SPEC.md rev 2) ---

from market_tester.sessions import eia_day_window  # noqa: E402
from market_tester.spread_lead import SpreadLeadHour  # noqa: E402


@pytest.fixture(scope="module")
def hour_bars(data):
    bars = data[0].copy()
    bars["eia_during_hold"] = (bars["ts_et"].dt.dayofweek == 2).to_numpy()  # Wednesdays: release in the hold
    return bars


def test_hour_causal_entries_exits_and_eia_days(hour_bars):
    s = SpreadLeadHour()
    sig = s.signals(hour_bars)
    look = lookahead_check(s, hour_bars, {}, n_cuts=40, full=sig)
    assert look["ok"], look
    trades = run(Market.from_bars(hour_bars), sig)
    assert (trades["et_minute"] == 10 * 60).all()
    out = pd.DatetimeIndex(trades["exit_time"]).tz_convert("America/New_York")
    assert ((out.hour * 60 + out.minute) == 10 * 60 + 59).all()
    assert (pd.DatetimeIndex(trades["trading_day"]).dayofweek != 2).all()
    t = s.day_table(hour_bars)
    t102 = SpreadLead().day_table(hour_bars)
    ok = t["trade_eligible"] & t["r"].notna() & (t["r"] != 0) & (t["sigma"] > 0)
    assert (t.loc[ok, "side"] != 0).all() and (t.loc[~ok, "side"] == 0).all()
    assert (t["side"] != 0).sum() > (t102["side"] != 0).sum()  # no |r| >= sigma filter
    # EIA days still feed the window: same r as T102 on every day
    both = t["r"].notna()
    np.testing.assert_allclose(t.loc[both, "r"], t102.loc[both, "r"])
    assert not s.eligible(hour_bars)[(hour_bars["ts_et"].dt.dayofweek == 2).to_numpy()].any()


def test_eia_day_window_uses_every_listed_row():
    rel = pd.DatetimeIndex(pd.to_datetime(["2020-01-08 10:30", "2020-01-16 10:30", "2020-01-16 11:00",
                                           "2020-01-23 11:30"]).tz_localize("America/New_York").tz_convert("UTC"))
    days = pd.Series(pd.to_datetime(["2020-01-08", "2020-01-09", "2020-01-16", "2020-01-23"]))
    np.testing.assert_array_equal(eia_day_window(days, 600, 675, rel), [True, False, True, False])
    rel11 = rel[[2]]  # 11:00 alone (holiday week) is inside the 10:00..11:15 margin
    assert eia_day_window(days, 600, 675, rel11).tolist() == [False, False, True, False]
