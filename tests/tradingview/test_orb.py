"""ORB-TV situations on hand-built days (experiments/tradingview/ORB/SPEC.md): one rule per test."""

import numpy as np
import pandas as pd
import pytest

from market_tester.common.data import RollCrossed
from market_tester.common.sessions import add_time_columns
from market_tester.tradingview import report
from market_tester.tradingview.run import Chart, simulate_config, trade_frame
from strategies.tradingview.orb import Orb, grid

BASE = 1000  # ticks


def day_bars(date="2019-03-05", changes=None, drop=(), raw="CLJ9", after=None):
    """One trading day of 1m bars 09:00..16:59 ET at BASE, range 990..1010 (09:30 bar), then `changes`
    {"HH:MM": (o, h, l, c)}; `drop` = minutes with no bar; `after` = ("HH:MM", level): flat bars at `level` from then."""
    idx = pd.date_range(f"{date} 09:00", f"{date} 16:59", freq="1min", tz="America/New_York")
    px = {t: (BASE, BASE, BASE, BASE) for t in idx}
    if after:
        start = pd.Timestamp(f"{date} {after[0]}", tz="America/New_York")
        px.update({t: (after[1],) * 4 for t in idx if t >= start})
    px[pd.Timestamp(f"{date} 09:30", tz="America/New_York")] = (BASE, 1010, 990, BASE)
    for hm, v in (changes or {}).items():
        px[pd.Timestamp(f"{date} {hm}", tz="America/New_York")] = v
    keep = [t for t in idx if t.strftime("%H:%M") not in drop]
    o, h, l, c = (np.array([px[t][i] for t in keep], np.int64) for i in range(4))
    bars = pd.DataFrame({"ts_event_utc": pd.DatetimeIndex(keep).tz_convert("UTC"), "continuous_symbol": "CL.v.0",
                         "raw_symbol": raw, "open_ticks": o, "high_ticks": h, "low_ticks": l, "close_ticks": c})
    return add_time_columns(bars)


def trades(bars, situation, tp=30, sl=0):
    s = Orb()
    ch = Chart.from_bars(bars)
    f = s.prepare(bars)
    return trade_frame(ch, simulate_config(ch, s, f, {"situation": situation, "tp": tp, "sl": sl}))


BREAK = {"10:05": (1005, 1015, 1004, 1012), "10:06": (1012, 1013, 1003, 1005)}


def test_grid_has_170_configs():
    g = grid()
    assert len(g) == 170 and sum(c["situation"] == 1 for c in g) == 5 and sum(c["situation"] == 2 for c in g) == 40


def test_s1_stop_entry_then_candle_low_stop():
    t = trades(day_bars(changes=BREAK), 1)
    assert len(t) == 1
    r = t.iloc[0]
    assert (r.dir, r.entry_ticks, r.exit_ticks, r.exit_reason) == (1, 1010, 1004, "stop")
    assert r.net_usd == pytest.approx(-6 * 10 - 4.02)


def test_s2_caps_the_stop_distance():
    bars = day_bars(changes={"10:05": (1005, 1015, 990, 1012), "10:06": (1012, 1013, 990, 1000)})
    r = trades(bars, 2, sl=15).iloc[0]
    assert r.exit_reason == "stop" and r.exit_ticks == 995  # 1010 - 15, not the candle low 990
    r = trades(day_bars(changes=BREAK), 2, sl=15).iloc[0]
    assert r.exit_ticks == 1004  # candle low within 15 ticks: used as is


def test_tp_from_fill_same_bar():
    bars = day_bars(changes={"10:05": (1005, 1045, 1004, 1030)})  # O-L-H: fill 1010 then TP 1040 on one bar
    r = trades(bars, 1).iloc[0]
    assert (r.entry_ticks, r.exit_ticks, r.exit_reason, r.entry_bar == r.exit_bar) == (1010, 1040, "limit", True)


def test_s3_stop_waits_for_the_5m_candle():
    ch = {"10:01": (1000, 1001, 995, 1000), "10:02": (1005, 1015, 1004, 1012),
          "10:03": (1012, 1013, 1001, 1005),  # below the 1m candle low 1004, but no stop yet
          "10:05": (1005, 1006, 990, 995)}
    r = trades(day_bars(changes=ch), 3).iloc[0]
    assert r.entry_ticks == 1010 and r.exit_reason == "stop" and r.exit_ticks == 995  # 5m low 10:00-10:04
    assert r.exit_time == pd.Timestamp("2019-03-05 10:05", tz="America/New_York").tz_convert("UTC").tz_localize(None)


def test_s3_missing_x4_minute_confirms_on_next_bar():
    ch = {"10:02": (1005, 1015, 1004, 1012), "10:05": (1012, 1013, 900, 1000), "10:06": (1000, 1001, 990, 995)}
    r = trades(day_bars(changes=ch, drop=("10:04",)), 3).iloc[0]
    # 10:00-10:03 low is 1000 (10:05's own low 900 is not in the bucket); stop placed at 10:05 close (1000 > it?)
    # sell stop 1000 at close 1000 is activated -> fills at the 10:06 open 1000
    assert r.exit_ticks == 1000 and r.exit_reason == "stop"


def test_s5_limit_at_stop_plus_cap_marketable_fills_next_open():
    r = trades(day_bars(changes={"10:05": (1005, 1015, 1004, 1012), "10:06": (1012, 1050, 1011, 1045)}), 5,
               sl=16).iloc[0]
    # SL 1004, buy limit 1020 above the 1012 close -> fills at 10:06 open 1012; TP 1042 from the fill
    assert (r.entry_ticks, r.entry_type, r.exit_ticks, r.exit_reason) == (1012, "limit", 1042, "limit")


def test_s5_waits_for_pullback_to_limit():
    ch = {"10:05": (1005, 1030, 1004, 1028), "10:06": (1028, 1029, 1019, 1022), "10:07": (1022, 1023, 1003, 1010)}
    r = trades(day_bars(changes=ch), 5, sl=16).iloc[0]
    assert (r.entry_ticks, r.exit_ticks, r.exit_reason) == (1020, 1004, "stop")


def test_s5_both_sides_candle_skips_the_day():
    assert trades(day_bars(changes={"10:05": (1000, 1015, 985, 1000)}), 5, sl=16).empty


def test_s6_uses_confirmed_5m_candle():
    ch = {"10:06": (1000, 1015, 998, 1012), "10:09": (1012, 1013, 1011, 1012), "10:10": (1012, 1013, 1000, 1005)}
    r = trades(day_bars(changes=ch), 6, sl=16).iloc[0]
    # 5m 10:05-10:09: high 1015, low 998 -> SL 998, buy limit 1014 above close 1012 -> fills 10:10 open 1012
    assert r.entry_ticks == 1012 and r.entry_bar > 0 and r.stop_ticks == 998


def test_flat_at_1610_and_one_trade_per_day():
    # fill 1010 at 10:05, stop at the 1m low 1009, price then sits at 1015 all day: flat at the 16:09 close
    t = trades(day_bars(changes={"10:05": (1009, 1012, 1009, 1011)}, after=("10:06", 1015)), 1)
    assert len(t) == 1 and t.iloc[0].exit_reason == "close" and t.iloc[0].exit_ticks == 1015
    assert t.iloc[0].exit_time == pd.Timestamp("2019-03-05 16:09", tz="America/New_York").tz_convert("UTC").tz_localize(None)


def test_no_entry_after_1607():
    t = trades(day_bars(changes={"16:08": (1005, 1015, 1004, 1012)}), 1)
    assert t.empty


def test_missing_range_minute_excludes_day():
    bars = day_bars(changes=BREAK, drop=("09:17",))
    assert trades(bars, 1).empty
    assert Orb().prepare(bars)[2]["excluded_days"].iloc[0] == "range_minutes_missing"


def test_short_session_not_traded():
    assert trades(day_bars(date="2019-11-29", changes=BREAK), 1).empty  # day after Thanksgiving


def test_daytime_roll_is_an_error():
    bars = day_bars(changes=BREAK)
    bars.loc[bars["et_minute"] >= 12 * 60, "raw_symbol"] = "CLK9"
    with pytest.raises(RollCrossed):
        trades(bars, 1)


def test_short_mirror():
    ch = {"10:05": (995, 996, 985, 988), "10:06": (988, 997, 987, 990)}  # O-H-L: sell stop 990, SL = high 996
    r = trades(day_bars(changes=ch), 1).iloc[0]
    assert (r.dir, r.entry_ticks, r.exit_ticks, r.exit_reason) == (-1, 990, 996, "stop")


def test_report_numbers_on_known_trades():
    t = pd.DataFrame({"net_usd": [100.0, -50.0, 0.0], "drawdown_usd": [30.0, 80.0, 0.0],
                      "runup_usd": [120.0, 10.0, 5.0], "commission": 4.02, "dir": [1, -1, 1], "bars": [1, 2, 3],
                      "entry_ticks": [1000, 1000, 1000],
                      "exit_time": pd.to_datetime(["2020-01-10", "2020-02-10", "2020-02-11"]),
                      "trading_day": pd.to_datetime(["2020-01-10", "2020-02-10", "2020-02-11"])})
    s = report.summary(t, "2020-01-01", "2020-03-31", 1100)
    assert s["net_profit"] == 50 and s["profit_factor"] == 2 and s["winners"] == 1 and s["even"] == 1
    # trade 2: peak before 50,100, trough 50,100 - 80 - 2.01
    assert s["max_drawdown"] == pytest.approx(82.01)
    assert s["buy_hold_pct"] == pytest.approx(10.0)
    r = report.monthly_returns(t, "2020-01-01", "2020-03-31")
    assert list(r.round(8)) == [round(100 / 50000, 8), round(-50 / 50100, 8), 0.0]
    sh, so = report.sharpe_sortino(r)
    ex = r.mean() - 0.02 / 12
    assert sh == pytest.approx(ex / r.std(ddof=0))
