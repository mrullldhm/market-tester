"""pine/crosscheck.py: the Python port of pine/SPEC.md rev 3, checked on hand-built bars."""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pine"))

import crosscheck as cc  # noqa: E402
from conftest import synthetic_bars  # noqa: E402
from market_tester.sessions import add_time_columns  # noqa: E402

TF = 5
CFG = cc.Config(tf=TF, pivLeft=1, pivRight=1)


def make_bars(rows, start="2023-03-07 10:00", symbols=None, starts=None) -> pd.DataFrame:
    """rows = [(o, h, l, c), ...] in ticks, TF-minute bars from `start` ET (or explicit `starts`)."""
    if starts is None:
        starts = pd.date_range(start, periods=len(rows), freq=f"{TF}min", tz="America/New_York")
    else:
        starts = pd.DatetimeIndex([pd.Timestamp(s, tz="America/New_York") for s in starts])
    o, h, l, c = (np.array(x, dtype=np.int64) for x in zip(*rows))
    bars = pd.DataFrame({"ts_event_utc": starts.tz_convert("UTC"), "o": o, "h": h, "l": l, "c": c,
                         "raw_symbol": symbols or ["CLJ3"] * len(rows)})
    bars = add_time_columns(bars).rename(columns={"ts_event_utc": "ts"})
    return cc.finish_bars(bars, TF)


# pivot low 1000 at bar 1 (known from bar 3), green no-bottom-wick bar 2 -> support 1004
BASE = [(1010, 1012, 1005, 1008), (1008, 1009, 1000, 1004), (1004, 1010, 1004, 1010)]


def run(rows, cfg=CFG, **kw):
    trades, series = cc.simulate(make_bars(rows, **kw), cfg)
    return trades, series


# ---------- detection ----------

def test_detect_truth_table():
    rows = [(10, 15, 10, 14),   # green, no bottom wick -> support
            (14, 14, 9, 10),    # red, no top wick -> resistance
            (14, 15, 10, 10),   # red, no bottom wick -> directional: none; non-directional: support
            (10, 14, 10, 14),   # green marubozu -> support only
            (10, 12, 10, 10),   # doji at low -> none (both modes: noBot but not bull; non-dir keeps it)
            (10, 10, 10, 10)]   # zero range -> invalid
    o, h, l, c = zip(*rows)
    sup, res = cc.detect(o, h, l, c, cc.Config())
    assert sup.tolist() == [True, False, False, True, False, False]
    assert res.tolist() == [False, True, False, False, False, False]
    sup, res = cc.detect(o, h, l, c, cc.Config(directional=False))
    assert sup.tolist() == [True, False, True, True, True, False]
    assert res.tolist() == [False, True, False, False, False, False]


def test_detect_tolerance_and_min_range():
    o, h, l, c = [11], [15], [10], [14]  # 1-tick bottom wick
    assert not cc.detect(o, h, l, c, cc.Config())[0][0]
    assert cc.detect(o, h, l, c, cc.Config(tolTicks=1))[0][0]
    assert not cc.detect(o, h, l, c, cc.Config(tolTicks=1, minRangeTicks=6))[0][0]


# ---------- trades ----------

def test_long_retest_to_target():
    trades, series = run(BASE + [(1009, 1011, 1003, 1006), (1006, 1010, 1005, 1008)])
    assert series["det"][2] == 1 and series["entry_side"][3] == 1
    t = trades.iloc[0]
    assert (t.entry, t.stop, t.risk, t.target) == (1004, 1000, 4, 1009)  # ceil(1.1 * 4) = 5
    assert (t.exit_bar, t.exit, t.reason) == (4, 1009, "target")
    assert t.r == pytest.approx(1.25)
    assert t.net == pytest.approx(5 * 10 - 24.02)


def test_target_needs_one_tick_trade_through():
    trades, _ = run(BASE + [(1009, 1011, 1003, 1006), (1006, 1009, 1005, 1008)])
    assert trades.empty  # high == target, not through; trade still open (no session end)


def test_same_bar_stop_and_target_is_stop():
    trades, _ = run(BASE + [(1009, 1011, 1003, 1006), (1006, 1012, 999, 1008)])
    assert trades.iloc[0][["exit", "reason"]].tolist() == [1000, "stop"]


def test_stop_gap_fills_at_open():
    trades, _ = run(BASE + [(1009, 1011, 1003, 1006), (998, 999, 990, 995)])
    assert trades.iloc[0][["exit", "reason"]].tolist() == [998, "stop"]


def test_open_below_support_fills_at_open():
    trades, series = run(BASE + [(1002, 1006, 1001, 1005), (1005, 1006, 1004, 1006)])
    t = trades.iloc[0]
    assert series["entry_side"][3] == 1
    assert (t.entry, t.stop, t.target) == (1002, 1000, 1005)  # risk 2, ceil(2.2) = 3
    assert (t.exit_bar, t.reason) == (4, "target")


def test_touch_without_trade_through_consumes_level():
    trades, series = run(BASE + [(1009, 1011, 1004, 1006), (1006, 1007, 1002, 1003)])
    assert trades.empty and not series["entry_side"].any()


def test_entry_bar_checks_stop_only():
    trades, _ = run(BASE + [(1009, 1011, 999, 1006)])
    t = trades.iloc[0]
    assert (t.entry_bar, t.exit_bar, t.exit, t.reason) == (3, 3, 1000, "stop")
    trades, _ = run(BASE + [(1009, 1020, 1003, 1015)])
    assert trades.empty  # target crossed on the entry bar is ignored; trade still open


def test_no_swing_below_entry_no_trade():
    rows = [(1010, 1012, 1005, 1008), (1008, 1009, 1006, 1007), (1004, 1010, 1004, 1010),
            (1009, 1011, 1003, 1006)]  # no pivot low below 1004 known at bar 2
    trades, series = run(rows)
    assert trades.empty and not series["entry_side"].any()


def test_max_risk_blocks_trade():
    trades, series = run(BASE + [(1009, 1011, 1003, 1006)], cfg=cc.Config(tf=TF, pivLeft=1, pivRight=1,
                                                                            maxRiskTicks=3))
    assert not series["entry_side"].any()


def test_pivot_ties_left_allowed_right_not():
    low = np.array([1000, 1000, 1003])
    high = np.array([1009, 1012, 1012])
    assert cc.pivot_at(low, high, 1, 1, 1) == (True, False)  # left tie ok; right tie on the high blocks it
    assert cc.pivot_at(np.array([1001, 1000, 1000]), high, 1, 1, 1)[0] is False


def test_filter_uses_previous_bar(monkeypatch):
    rows = BASE + [(1009, 1011, 1003, 1006)]
    n = len(rows)
    ok_now = np.zeros(n, bool)
    ok_now[3] = True  # passes only at the entry bar itself: must not trade
    monkeypatch.setattr(cc, "filters", lambda bars, cfg: (ok_now, np.ones(n, bool)))
    _, series = run(rows)
    assert not series["entry_side"].any()
    ok_prev = np.zeros(n, bool)
    ok_prev[2] = True
    monkeypatch.setattr(cc, "filters", lambda bars, cfg: (ok_prev, np.ones(n, bool)))
    _, series = run(rows)
    assert series["entry_side"][3] == 1


def test_two_sided_touch_is_ambiguous():
    # resistance 1012 from a red no-top-wick bar, support 1004; bar 4 spans both
    rows = BASE[:2] + [(1012, 1012, 1006, 1007), (1004, 1010, 1004, 1010), (1008, 1014, 1002, 1009)]
    _, series = run(rows)
    assert series["det"][2] == -1 and series["det"][3] == 1
    assert not series["entry_side"].any()


def test_level_expires():
    cfg = cc.Config(tf=TF, pivLeft=1, pivRight=1, maxAge=2)
    rows = BASE + [(1009, 1011, 1006, 1010), (1010, 1012, 1008, 1011), (1010, 1011, 1003, 1006)]
    _, series = run(rows, cfg=cfg)
    assert not series["entry_side"].any()  # touch at bar 5 > d 2 + maxAge 2


def test_session_end_flat_and_no_entry_on_last_bar():
    starts = ["2023-03-07 16:35", "16:40", "16:45", "16:50", "16:55"]
    starts = [starts[0]] + ["2023-03-07 " + s for s in starts[1:]]
    trades, _ = run(BASE + [(1009, 1011, 1003, 1006), (1006, 1008, 1005, 1007)], starts=starts)
    t = trades.iloc[0]
    assert (t.exit_bar, t.exit, t.reason) == (4, 1007, "session")
    starts2 = ["2023-03-07 16:40", "2023-03-07 16:45", "2023-03-07 16:50", "2023-03-07 16:55"]
    _, series = run(BASE + [(1009, 1011, 1003, 1006)], starts=starts2)
    assert not series["entry_side"].any()  # 16:55 bar spans 16:59


def test_new_day_without_close_bar_exits_at_open():
    starts = ["2023-03-07 13:00", "2023-03-07 13:05", "2023-03-07 13:10", "2023-03-07 13:15",
              "2023-03-07 18:00"]  # early close: no 16:55 bar
    trades, _ = run(BASE + [(1009, 1011, 1003, 1006), (1007, 1008, 1005, 1006)], starts=starts)
    t = trades.iloc[0]
    assert (t.exit_bar, t.exit, t.reason) == (4, 1007, "gap")


def test_roll_voids_open_trade_and_clears_state():
    syms = ["CLJ3"] * 4 + ["CLK3"] * 2
    rows = BASE + [(1009, 1011, 1003, 1006), (1100, 1102, 1099, 1101), (1101, 1103, 1100, 1102)]
    trades, series = run(rows, symbols=syms)
    assert trades.empty and series["voided"] == [3]


def test_win_is_net_positive():
    # risk 1 tick: target +2 ticks = $20 < $24.02 cost -> a target hit that loses money
    rows = [(1010, 1012, 1005, 1008), (1008, 1009, 1003, 1005), (1004, 1010, 1004, 1010),
            (1004, 1006, 1004, 1005), (1005, 1007, 1004, 1006)]  # bar 3 opens at the level: fill at open
    trades, _ = run(rows)
    t = trades.iloc[0]
    assert (t.risk, t.target, t.reason) == (1, 1006, "target")
    s = cc.stats(trades, CFG)
    assert s["win_rate"] == 0.0 and s["target_hit_rate"] == 1.0
    assert s["profit_factor"] == 0.0  # no profit, one loss
    assert math.isnan(cc.stats(trades.iloc[:0], CFG)["profit_factor"])


def test_stats_drawdown_from_zero():
    assert cc.max_drawdown(np.array([-5.0, 10.0, -3.0, -4.0, 2.0])) == 7.0
    assert cc.max_drawdown(np.array([])) == 0.0


def test_seeded_ema_and_rma():
    x = np.array([1.0, 2.0, 3.0, 4.0])
    e = cc.ema_seeded(x, 2)
    assert np.isnan(e[0]) and e[1:].tolist() == pytest.approx([1.5, 2.5, 3.5])
    r = cc.rma_seeded(x, 2)
    assert r[1:].tolist() == pytest.approx([1.5, 2.25, 3.125])


def test_config_domains():
    with pytest.raises(ValueError, match="maxAge"):
        cc.Config(minGap=5, maxAge=3)
    with pytest.raises(ValueError, match="tf"):
        cc.Config(tf=7)


def test_run_refuses_holdout():
    with pytest.raises(ValueError, match="dev-only"):
        cc.databento_bars(pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-01"), 5)


def test_resample_buckets_from_session_open():
    b = synthetic_bars(n_days=3, roll_every=2).assign(volume=1)
    bars = cc.resample(b, 5)
    first = b[b["trading_day"] == b["trading_day"].iloc[0]].iloc[:5]
    assert bars["ts"].iloc[0] == first["ts_event_utc"].iloc[0]
    assert (bars.iloc[0][["o", "h", "l", "c", "v"]].tolist()
            == [first["open_ticks"].iloc[0], first["high_ticks"].max(), first["low_ticks"].min(),
                first["close_ticks"].iloc[-1], 5])
    assert bars["roll"].sum() == 1 and bars["ts"].is_monotonic_increasing


def test_truncation_does_not_change_past_trades():
    b = synthetic_bars(n_days=40, roll_every=15).assign(volume=1)
    bars = cc.resample(b, 5)
    cfg = cc.Config(tf=5, useRibbon=True, useST=True, ribbonMode="Price vs slowest")
    full, _ = cc.simulate(bars, cfg)
    cut = len(bars) * 2 // 3
    part, _ = cc.simulate(bars.iloc[:cut].reset_index(drop=True), cfg)
    assert len(full) > 20
    pd.testing.assert_frame_equal(full[full["exit_bar"] < cut - 1].reset_index(drop=True),
                                  part[part["exit_bar"] < cut - 1].reset_index(drop=True))


def mirror(rows, axis=2000):
    """Price-mirror bars: a long setup becomes the same short setup."""
    return [(axis - o, axis - l, axis - h, axis - c) for o, h, l, c in rows]


def test_short_retest_mirrors_long():
    rows = BASE + [(1009, 1011, 1003, 1006), (1006, 1010, 1005, 1008)]
    trades, series = run(mirror(rows))
    t = trades.iloc[0]
    assert series["det"][2] == -1 and series["entry_side"][3] == -1
    assert (t.side, t.entry, t.stop, t.target, t.exit, t.reason) == (-1, 996, 1000, 991, 991, "target")
    assert t.net == pytest.approx(5 * 10 - 24.02)


def test_short_stop_gap_fills_at_open():
    trades, _ = run(mirror(BASE + [(1009, 1011, 1003, 1006), (998, 999, 990, 995)]))
    assert trades.iloc[0][["exit", "reason"]].tolist() == [1002, "stop"]


NESTED = BASE + [(1006, 1009, 1006, 1009),  # newer support at 1006 (does not touch 1004)
                 (1008, 1009, 1003, 1005)]  # touches both; newest (1006) wins


def test_newest_level_wins_and_no_fallback():
    trades, series = run(NESTED + [(1005, 1006, 999, 1000)])
    assert series["entry_side"][4] == 1
    assert trades.iloc[0][["entry", "stop", "reason"]].tolist() == [1006, 1000, "stop"]
    _, series = run(NESTED, cfg=cc.Config(tf=TF, pivLeft=1, pivRight=1, maxRiskTicks=5))
    assert not series["entry_side"].any()  # 1006 fails max risk (6); 1004 (risk 4) is not tried


def test_max_levels_evicts_oldest():
    rows = BASE + [(1006, 1009, 1006, 1009),  # newer support 1006
                   (1008, 1009, 1006, 1007),  # exact touch of 1006: consumed, no fill
                   (1006, 1007, 1003, 1005)]  # touches only 1004, if it still exists
    _, series = run(rows)
    assert series["entry_side"][5] == 1
    _, series = run(rows, cfg=cc.Config(tf=TF, pivLeft=1, pivRight=1, maxLevels=1))
    assert not series["entry_side"].any()  # 1004 was evicted when 1006 was added


def test_reset_session_toggle():
    starts = ["2023-03-07 16:45", "2023-03-07 16:50", "2023-03-07 16:55", "2023-03-07 18:00"]
    rows = BASE + [(1009, 1011, 1003, 1006)]
    _, series = run(rows, starts=starts)
    assert not series["entry_side"].any()
    _, series = run(rows, cfg=cc.Config(tf=TF, pivLeft=1, pivRight=1, resetSession=False), starts=starts)
    assert series["entry_side"][3] == 1


def test_supertrend_direction():
    up = np.arange(100.0, 130.0)
    down = np.arange(129.0, 90.0, -1.5)
    c = np.r_[up, down]
    trend = cc.supertrend(c + 0.2, c - 0.2, c, 3.0, 10)
    assert (trend[:9] == 0).all() and trend[9] == 1 and trend[len(up) - 1] == 1 and trend[-1] == -1


def write_export(path, bars, series_by_name):
    df = pd.DataFrame({"time": bars["ts"].astype("int64") // 10**9, "open": bars["o"] / 100, "high": bars["h"] / 100,
                       "low": bars["l"] / 100, "close": bars["c"] / 100, **series_by_name})
    df.to_csv(path, index=False)


def test_tv_parity_catches_every_series(tmp_path):
    b = synthetic_bars(n_days=10, roll_every=100).assign(volume=1)
    bars = cc.resample(b, 5).drop(columns="raw_symbol")
    cfg = cc.Config(tf=5)
    trades, series = cc.simulate(bars.iloc[:-1].reset_index(drop=True), cfg)
    k, net, r = cc.cumulative(trades, len(bars) - 1)
    good = {"x_det": np.r_[series["det"], 0], "x_entry": np.r_[series["entry_side"], 0], "x_n": np.r_[k, k[-1]],
            "x_net": np.r_[net, net[-1]], "x_r": np.r_[r, r[-1]]}
    assert len(trades) > 5
    write_export(tmp_path / "ok.csv", bars, good)
    assert cc.compare_tv(cc.tv_bars(tmp_path / "ok.csv", 5), cfg)["parity_ok"]
    unclosed = {key: val.astype(float).copy() for key, val in good.items()}
    for val in unclosed.values():
        val[-1] += 7  # the last export bar may still be forming: never compared
    write_export(tmp_path / "unclosed.csv", bars, unclosed)
    assert cc.compare_tv(cc.tv_bars(tmp_path / "unclosed.csv", 5), cfg)["parity_ok"]
    for col in good:
        bad = {key: val.astype(float).copy() for key, val in good.items()}
        bad[col][len(bars) // 2] += 1
        write_export(tmp_path / f"{col}.csv", bars, bad)
        out = cc.compare_tv(cc.tv_bars(tmp_path / f"{col}.csv", 5), cfg)
        assert not out["parity_ok"] and out[f"{col}_mismatch"] >= 1, col
