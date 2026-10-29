"""market_tester/orb_situations.py: experiments/O10/SPEC.md rules on hand-built bars."""

import numpy as np
import pandas as pd
import pytest

from conftest import synthetic_bars
from market_tester import orb_situations as orb
from market_tester.data import RollCrossed
from market_tester.sessions import add_time_columns

H, L, MID = 1010, 950, 1000


N_BARS = 360  # 09:00-14:59 ET: bars after 14:29 mark a full (not early-close) session


def make_day(bars=None, date="2023-03-07", raw="CLJ3", eia=(), utc_start=None, rolls_at=None, n=N_BARS):
    """One day from 09:00 ET. Range bars sit at MID with H/L set by 09:30; window bars are flat at MID.
    `bars` = {"HH:MM": (o, h, l, c)} overrides."""
    if utc_start is None:
        start = pd.Timestamp(f"{date} 09:00", tz="America/New_York").tz_convert("UTC")
    else:
        start = pd.Timestamp(utc_start, tz="UTC")
    ts = pd.date_range(start, periods=n, freq="1min")
    o = np.full(n, MID, np.int64)
    h, l, c = o.copy(), o.copy(), o.copy()
    h[30], l[30] = H, L
    et = ts.tz_convert("America/New_York").strftime("%H:%M")
    pos = {t: q for q, t in enumerate(et)}
    for t, (bo, bh, bl, bc) in (bars or {}).items():
        q = pos[t]
        o[q], h[q], l[q], c[q] = bo, bh, bl, bc
    sym = np.array([raw] * n, dtype=object)
    if rolls_at is not None:
        sym[pos[rolls_at]:] = "CLK3"
    df = pd.DataFrame({"ts_event_utc": ts, "continuous_symbol": "CL.v.0", "raw_symbol": sym, "open_ticks": o,
                       "high_ticks": h, "low_ticks": l, "close_ticks": c})
    df = add_time_columns(df)
    df["eia_block"] = df["ts_et"].dt.strftime("%H:%M").isin(eia)
    return df


def one(bars, situation, sl=0, tp=30):
    t = orb.run(orb.build_days(bars), situation, sl, tp)
    assert len(t) <= 1
    return None if t.empty else t.iloc[0]


def test_grid_is_204_configs():
    g = orb.grid()
    assert len(g) == 204 and len({tuple(x.values()) for x in g}) == 204


def test_range_and_window_follow_dst():
    for date, utc, first in (("2023-07-11", "2023-07-11 13:00", "14:00"), ("2023-01-10", "2023-01-10 14:00", "15:00")):
        d = orb.build_days(make_day(utc_start=utc))
        assert d.hi[0] == H and d.lo[0] == L
        assert pd.Timestamp(d.ts[d.i0[0]]).strftime("%H:%M") == first  # 10:00 ET
        assert d.et_minute[d.i1[0]] == orb.WINDOW_LAST


def test_s1_long_stop_at_touch_bar_low():
    t = one(make_day({"10:05": (1005, 1012, 1003, 1011), "10:06": (1011, 1011, 1003, 1004)}), 1)
    assert (t.side, t.entry_ticks, t.exit_ticks, t.reason, t.slip_sides, t.stop_dist) == (1, 1010, 1003, "stop", 2, 7)


def test_s1_short_mirror():
    t = one(make_day({"10:05": (955, 957, 948, 949), "10:06": (949, 957, 949, 956)}), 1)
    assert (t.side, t.entry_ticks, t.exit_ticks, t.reason) == (-1, 950, 957, "stop")


def test_s1_target_inside_fill_bar_and_trade_through():
    t = one(make_day({"10:05": (1005, 1041, 1003, 1035)}), 1)
    assert (t.reason, t.gross_ticks, t.slip_sides) == ("target", 30, 1)
    t = one(make_day({"10:05": (1005, 1040, 1003, 1035)}), 1)  # touch without trade-through: no fill
    assert t.reason == "stop"


def test_gap_open_fills_at_open():
    t = one(make_day({"10:05": (1015, 1016, 1014, 1015), "10:06": (1015, 1015, 1013, 1013)}), 1)
    assert (t.entry_ticks, t.exit_ticks, t.reason) == (1015, 1014, "stop")


def test_stop_gap_exits_at_open():
    t = one(make_day({"10:05": (1005, 1012, 1003, 1011), "10:06": (1001, 1002, 1000, 1000)}), 1)
    assert (t.exit_ticks, t.reason) == (1001, "stop")


def test_both_levels_in_one_bar_skips_day():
    assert one(make_day({"10:05": (1000, 1011, 949, 1000)}), 1) is None


def test_eia_touch_bar_skips_day():
    assert one(make_day({"10:30": (1005, 1012, 1003, 1011)}, eia=("10:30",)), 1) is None


def test_roll_inside_session_is_an_error():
    with pytest.raises(RollCrossed):
        orb.build_days(make_day(rolls_at="12:00"))
    assert len(orb.build_days(make_day(rolls_at="14:45")).day) == 1  # after 14:29: not a session bar


def test_missing_range_minute_skips_day():
    assert len(orb.build_days(make_day().drop(index=20)).day) == 0  # 09:20 missing


def test_calendar_rules():
    short = orb.short_sessions(pd.Timestamp("2010-06-01"), pd.Timestamp("2023-12-31"))
    for d in ("2010-07-05", "2015-07-03", "2022-06-20", "2023-06-19", "2012-12-24", "2013-11-29", "2019-01-21"):
        assert pd.Timestamp(d) in short, d
    for d in ("2021-06-18", "2011-12-23", "2023-10-09", "2023-03-07"):
        assert pd.Timestamp(d) not in short, d


def test_short_session_outage_and_mismatch():
    d = orb.build_days(make_day(date="2023-11-24", n=285))  # Black Friday, last bar 13:44
    assert len(d.day) == 0 and list(d.short_days) == [pd.Timestamp("2023-11-24")] and len(d.calendar_mismatch) == 0
    d = orb.build_days(make_day(date="2023-11-24"))  # calendar says short, bars run to 14:59
    assert list(d.calendar_mismatch) == [pd.Timestamp("2023-11-24")]
    d = orb.build_days(make_day(n=285))  # normal Tuesday whose bars stop at 13:44: outage
    assert len(d.day) == 0 and list(d.data_failures) == [pd.Timestamp("2023-03-07")]


def test_missing_1429_flattens_at_next_open():
    bars = make_day({"10:05": (1005, 1012, 999, 1011), "14:30": (1013, 1014, 1012, 1012)})
    bars = bars[bars["ts_et"].dt.strftime("%H:%M") != "14:29"]
    t = one(bars, 1)
    assert (t.reason, t.exit_ticks) == ("flat", 1013)
    assert pd.Timestamp(t.exit_time).tz_convert("America/New_York").strftime("%H:%M") == "14:30"


def test_eia_before_touch_cancels_day():
    bars = make_day({"10:40": (1005, 1012, 1003, 1011)}, eia=("10:15", "10:16"))
    for s, sl in ((1, 0), (3, 0), (5, 16), (6, 16)):
        assert one(bars, s, sl) is None
    assert one(make_day({"10:40": (1005, 1012, 1003, 1011)}), 1) is not None


def test_s6_blocked_bar_inside_bucket_cancels():
    bars = make_day({"10:06": (1005, 1020, 1004, 1018), "10:10": (1018, 1018, 1011, 1013)}, eia=("10:08",))
    assert one(bars, 6, sl=16) is None


def test_flat_at_1429_close():
    t = one(make_day({"10:05": (1005, 1012, 999, 1011), "14:29": (1011, 1012, 1010, 1012)}), 1)
    assert (t.reason, t.exit_ticks, t.slip_sides) == ("flat", 1012, 2)
    assert pd.Timestamp(t.exit_time).tz_convert("America/New_York").strftime("%H:%M") == "14:29"


def test_same_bar_stop_and_target_is_stop():
    t = one(make_day({"10:05": (1005, 1012, 1003, 1011), "10:06": (1011, 1045, 1002, 1030)}), 1)
    assert t.reason == "stop"


def test_s2_cap():
    bars = make_day({"10:05": (1000, 1012, 985, 1008), "10:06": (1008, 1008, 990, 995)})
    assert one(bars, 1).reason == "flat"  # S1 stop 985 never hit
    t = one(bars, 2, sl=16)
    assert (t.stop_dist, t.exit_ticks, t.reason) == (16, 994, "stop")
    t = one(bars, 2, sl=30)  # candle low inside the cap: candle low is the stop
    assert t.stop_dist == 25


def test_s3_waits_for_5m_bucket():
    bars = make_day({"10:06": (1005, 1012, 1004, 1011), "10:07": (1011, 1011, 1002, 1003),
                     "10:05": (1000, 1001, 999, 1000), "10:10": (1003, 1003, 998, 999)})
    assert one(bars, 1).reason == "stop"  # S1: stop 1004, hit at 10:07
    t = one(bars, 3)
    assert (t.stop_dist, t.exit_ticks, t.exit_bar - t.entry_bar) == (11, 999, 4)  # bucket low 999 hit at 10:10
    t = one(bars, 4, sl=5)
    assert (t.stop_dist, t.exit_ticks) == (5, 1003)  # stop 1005 above the 10:10 open: gap exit at the open


def test_s3_target_inside_bucket_without_stop():
    t = one(make_day({"10:06": (1005, 1012, 1004, 1011), "10:07": (1011, 1011, 990, 1000),
                      "10:08": (1000, 1041, 1000, 1040)}), 3)
    assert t.reason == "target"


def test_s5_pullback_limit():
    bars = make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (1015, 1016, 1011, 1012),
                     "10:07": (1012, 1013, 1009, 1012), "10:08": (1012, 1041, 1012, 1040)})
    t = one(bars, 5, sl=16)  # stop 994, limit 1010
    assert (t.entry_ticks, t.slip_sides, t.exit_ticks, t.reason, t.stop_dist) == (1010, 0, 1040, "target", 16)


def test_s5_marketable_limit_fills_at_open():
    t = one(make_day({"10:05": (1005, 1012, 1004, 1011), "10:06": (1011, 1011, 1008, 1009),
                      "10:07": (1009, 1009, 1003, 1003)}), 5, sl=16)  # stop 1003, limit 1019 > open
    assert (t.entry_ticks, t.exit_ticks, t.reason, t.slip_sides) == (1011, 1003, "stop", 2)


def test_s5_gap_through_both_levels_keeps_the_loss():
    t = one(make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (990, 996, 985, 992)}), 5, sl=16)
    assert (t.entry_ticks, t.exit_ticks, t.reason, t.slip_sides) == (990, 990, "stop", 2)  # marketable at 990
    t = one(make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (1015, 1016, 1012, 1013),
                      "10:07": (990, 991, 985, 990)}), 5, sl=16)
    assert (t.entry_ticks, t.exit_ticks, t.reason, t.slip_sides) == (1010, 990, "stop", 1)  # resting: limit price


def test_s5_fill_bar_stop():
    t = one(make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (1015, 1050, 990, 1000)}), 5, sl=16)
    assert (t.entry_ticks, t.exit_ticks, t.reason) == (1010, 994, "stop")  # TP not counted on the fill bar


def test_s5_limit_inactive_on_eia_bar():
    bars = make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (1015, 1015, 1005, 1012),
                     "10:07": (1009, 1013, 1009, 1012)}, eia=("10:06",))
    assert one(bars, 5, sl=16) is None  # blocked 10:06 cancels the unfilled limit for the day


def test_s6_uses_bucket_low():
    bars = make_day({"10:05": (1000, 1001, 997, 1000), "10:06": (1005, 1020, 1004, 1018),
                     "10:10": (1018, 1018, 1011, 1013)})
    t = one(bars, 6, sl=16)  # bucket low 997: stop 996, limit 1012
    assert (t.entry_ticks, t.stop_dist, pd.Timestamp(t.entry_time).tz_convert("America/New_York").minute) == (1012, 16, 10)


def full_sessions(bars):
    """conftest bars end 14:29 ET; add one 14:30 bar per day so no day counts as an early close."""
    last = bars.groupby("trading_day").tail(1).copy()
    last["ts_event_utc"] += pd.Timedelta(minutes=1)
    for k in ("open_ticks", "high_ticks", "low_ticks"):
        last[k] = last["close_ticks"]
    out = pd.concat([bars, last]).sort_values("ts_event_utc", ignore_index=True)
    return add_time_columns(out.drop(columns=["ts_et", "et_minute", "trading_day"]))


def test_truncation_does_not_change_past_trades():
    bars = full_sessions(synthetic_bars(n_days=120, seed=3))
    cut = bars["trading_day"].unique()[80]
    for s, sl in ((1, 0), (2, 16), (3, 0), (4, 16), (5, 16), (6, 16)):
        full = orb.run(orb.build_days(bars), s, sl, 30)
        part = orb.run(orb.build_days(bars[bars["trading_day"] < cut]), s, sl, 30)
        keep = full[full["trading_day"] < cut].drop(columns=["entry_bar", "exit_bar"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(keep, part.drop(columns=["entry_bar", "exit_bar"]).reset_index(drop=True))
        assert len(full) > 20


def test_random_entry_baseline_on_zero_edge_data():
    days = orb.build_days(full_sessions(synthetic_bars(n_days=200, seed=5)))
    for s, sl in ((1, 0), (4, 16), (5, 16)):
        t = orb.backtest(days, s, sl, 30)
        r = orb.entry_quality(days, t, 30, n_draws=99)
        assert 0 < r["p"] <= 1 and np.isfinite(r["null_mean"]) and r["real_kept"] > 0.9 and r["valid_draws"] == 99


def test_entries_have_no_outcomes():
    e = orb.entries(orb.build_days(make_day({"10:05": (1005, 1012, 1003, 1011)})), 2, 16)
    assert list(e.columns) == ["day_pos", "side", "entry_bar", "signal_bar", "stop_dist"]
    assert e.stop_dist.tolist() == [7]


def test_benchmark_without_valid_draws_is_nan():
    days = orb.build_days(make_day({"10:05": (1005, 1012, 1003, 1011)}))  # one day: partner = itself
    t = orb.backtest(days, 1, 0, 30)
    t = t.assign(signal_bar=days.i1[0])  # signal on the last window bar: no next minute on any day
    r = orb.entry_quality(days, t, 30, n_draws=20)
    assert np.isnan(r["p"]) and r["valid_draws"] == 0
    r = orb.entry_quality(days, t.iloc[:0], 30, n_draws=20)
    assert set(r) == {"p", "real", "null_mean", "null_sd", "valid_draws", "real_kept", "breached"}


def path_bars(n_days=300, seed=0, steps=20):
    """Zero-edge bars with wicks on the price path: each minute = `steps` moves of +-1 tick, 09:00-14:59 ET."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2017-01-09", periods=n_days)
    dates = dates[~dates.isin(orb.short_sessions(dates[0], dates[-1]))]
    n = 360
    path = 6000 + np.cumsum(rng.choice([-1, 1], (len(dates) * n, steps)).ravel()).reshape(-1, steps)
    prev = np.r_[6000, path[:-1, -1]]
    ts = np.concatenate([pd.date_range(pd.Timestamp(f"{d.date()} 09:00", tz="America/New_York"), periods=n,
                                       freq="1min").tz_convert("UTC") for d in dates])
    df = pd.DataFrame({"ts_event_utc": pd.DatetimeIndex(ts), "continuous_symbol": "CL.v.0", "raw_symbol": "CLZ7",
                       "open_ticks": prev, "high_ticks": np.maximum(path.max(1), prev),
                       "low_ticks": np.minimum(path.min(1), prev), "close_ticks": path[:, -1]})
    df = add_time_columns(df)
    df["eia_block"] = False
    return df


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_zero_edge_calibration(seed):
    """Path-consistent random walk: gross P&L about 0 and the benchmark p not extreme."""
    days = orb.build_days(path_bars(seed=seed))
    for s, sl in ((1, 0), (4, 16), (5, 16)):
        t = orb.backtest(days, s, sl, 30)
        se = t["gross_ticks"].std() / np.sqrt(len(t))
        assert abs(t["gross_ticks"].mean()) < 4 * se + 1, (s, t["gross_ticks"].mean(), se)
        r = orb.entry_quality(days, t, 30, n_draws=199, seed=seed)
        assert 0.005 < r["p"] < 0.995 and abs(r["real"] - r["null_mean"]) < 4 * r["null_sd"], (s, r)


def test_signal_is_known_at_its_bar_close():
    """Intraday causality: changing any bar after the signal bar leaves the entry, side, signal bar and stop
    distance unchanged (S3/S4: the signal bar is the 5m bucket's last bar)."""
    base = {"10:06": (1005, 1012, 1004, 1011), "10:07": (1011, 1011, 1002, 1003), "10:08": (1003, 1004, 1001, 1002)}
    for s, sl in ((1, 0), (2, 16), (3, 0), (4, 16), (5, 16), (6, 16)):
        e = orb.entries(orb.build_days(make_day(base)), s, sl).iloc[0]
        assert e.signal_bar >= e.entry_bar
        changed = make_day(base)
        after = changed.index > e.signal_bar
        changed.loc[after, "low_ticks"] -= 3
        e2 = orb.entries(orb.build_days(changed), s, sl).iloc[0]
        assert (e2.side, e2.entry_bar, e2.signal_bar, e2.stop_dist) == (e.side, e.entry_bar, e.signal_bar,
                                                                          e.stop_dist), s
    t = orb.run(orb.build_days(make_day(base)), 3, 0, 30).iloc[0]
    assert pd.Timestamp(t.entry_time).tz_convert("America/New_York").minute == 6
    assert orb.build_days(make_day(base)).et_minute[t.signal_bar] == 10 * 60 + 9


def test_benchmark_needs_the_next_wall_clock_minute():
    bars = make_day({"10:05": (1005, 1012, 1003, 1011)})
    bars = bars[~bars["ts_et"].dt.strftime("%H:%M").between("10:06", "14:28")]
    days = orb.build_days(bars)
    t = orb.backtest(days, 1, 0, 30)
    r = orb.entry_quality(days, t, 30, n_draws=20)
    assert np.isnan(r["p"]) and r["real_kept"] == 0


def test_breached_stop_left_out_of_benchmark():
    days = orb.build_days(make_day({"10:05": (1000, 1020, 995, 1018), "10:06": (990, 996, 985, 992)}))
    t = orb.backtest(days, 5, 16, 30)
    assert t.stop_dist.tolist() == [-4]  # marketable at 990, stop 994 already above it
    r = orb.entry_quality(days, t, 30, n_draws=20)
    assert r["breached"] == 1 and np.isnan(r["p"])


@pytest.mark.parametrize("seed", [0, 1])
def test_variance_model_matches_strategy_spread(seed):
    """On zero-edge path bars the stage-1 variance model's per-trade sd is close to the strategy's own."""
    days = orb.build_days(path_bars(seed=seed + 20))
    for s, sl in ((1, 0), (2, 16), (3, 0), (4, 16), (5, 16), (6, 16)):
        real_sd = orb.backtest(days, s, sl, 30)["net_usd"].std()
        model = orb.variance_draws(days, orb.entries(days, s, sl), s, sl, 30, n_draws=20, seed=seed)
        assert 0.8 < model.std() / real_sd < 1.25, (s, model.std(), real_sd)


def test_bad_config_raises():
    days = orb.build_days(make_day())
    with pytest.raises(ValueError):
        orb.run(days, 1, 16, 30)
    with pytest.raises(ValueError):
        orb.run(days, 2, 0, 30)


def test_benchmark_detects_a_planted_breakout_edge():
    """Breakouts that always run 40 ticks: the strategy wins; the same minute and side on another day of the
    week (half the days break the other way) does not, so the benchmark p is small."""
    up = {"10:05": (1005, 1012, 1003, 1011), "10:06": (1011, 1030, 1011, 1030), "10:07": (1030, 1051, 1030, 1050)}
    dn = {"10:05": (955, 957, 948, 949), "10:06": (949, 949, 930, 930), "10:07": (930, 930, 909, 910)}
    for k in ("10:08", "10:09"):
        up[k], dn[k] = (1050, 1050, 1049, 1050), (910, 911, 910, 910)
    dates = pd.bdate_range("2023-03-06", periods=40)
    bars = pd.concat([make_day(up if q % 2 else dn, date=str(d.date())) for q, d in enumerate(dates)], ignore_index=True)
    days = orb.build_days(bars)
    t = orb.backtest(days, 1, 0, 30)
    assert len(t) == 40 and (t["reason"] == "target").all()
    r = orb.entry_quality(days, t, 30, n_draws=199)
    assert r["p"] <= 0.01 and r["null_mean"] < r["real"]
