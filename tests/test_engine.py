"""Hand-made bars, one per fill rule. Prices are ticks; every bar starts on a whole minute."""

import numpy as np
import pandas as pd
import pytest

from market_tester.engine import Market, run, with_costs
from market_tester.sessions import add_time_columns
from market_tester.strategy import Signals, first_bar_at, flat_before

T0 = pd.Timestamp("2024-03-05 14:00", tz="UTC")  # 09:00 ET


def make_bars(ohlc, raw=None, minutes=None, block=None):
    n = len(ohlc)
    minutes = range(n) if minutes is None else minutes
    o, h, l, c = np.array(ohlc, dtype=np.int64).T
    bars = pd.DataFrame({
        "ts_event_utc": [T0 + pd.Timedelta(minutes=int(m)) for m in minutes],
        "continuous_symbol": "CL.v.0", "raw_symbol": raw or ["CLJ4"] * n,
        "open_ticks": o, "high_ticks": h, "low_ticks": l, "close_ticks": c})
    bars = add_time_columns(bars)
    if block is not None:
        bars["eia_block"] = block
    return bars


def sides(n, at, side=1):
    s = np.zeros(n, np.int64)
    s[at] = side
    return s


def one_trade(bars, sig, **kw):
    t = run(Market.from_bars(bars), sig, **kw)
    assert len(t) == 1, t
    return t.iloc[0]


FLAT_BAR = (1000, 1000, 1000, 1000)


def test_entry_next_open_and_time_exit():
    bars = make_bars([FLAT_BAR, (1002, 1003, 1001, 1002), (1004, 1005, 1003, 1004), (1006, 1006, 1006, 1006)])
    t = one_trade(bars, Signals(sides(4, 0), hold_minutes=2))
    assert (t.entry_bar, t.entry_ticks) == (1, 1002)  # signal at bar 0 close, fill at bar 1 open
    assert (t.exit_bar, t.exit_ticks, t.reason) == (3, 1006, "time")  # first open at or after entry + 2 min
    assert t.gross_ticks == 4 and t.slip_sides == 2


def test_stop_and_target_same_bar_is_stop():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1000, 1020, 980, 1000)])
    t = one_trade(bars, Signals(sides(3, 0), stop_ticks=10, target_ticks=10))
    assert (t.reason, t.exit_ticks, t.gross_ticks, t.slip_sides) == ("stop", 990, -10, 2)


def test_short_stop_and_target_mirror():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1000, 1005, 989, 990)])
    t = one_trade(bars, Signals(sides(3, 0, -1), stop_ticks=10, target_ticks=10))
    assert (t.reason, t.exit_ticks, t.gross_ticks, t.slip_sides) == ("target", 990, 10, 1)


def test_target_needs_one_tick_trade_through():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1000, 1010, 1000, 1005), (1005, 1011, 1004, 1006)])
    t = one_trade(bars, Signals(sides(4, 0), target_ticks=10))
    assert (t.exit_bar, t.reason, t.exit_ticks, t.slip_sides) == (3, "target", 1010, 1)  # touch at bar 2 not filled


def test_gap_through_stop_fills_at_open():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (980, 985, 975, 982)], minutes=[0, 1, 30])
    t = one_trade(bars, Signals(sides(3, 0), stop_ticks=10))
    assert (t.reason, t.exit_ticks, t.gross_ticks) == ("stop", 980, -20)


def test_gap_through_target_fills_at_target_not_open():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1030, 1035, 1025, 1030)])
    t = one_trade(bars, Signals(sides(3, 0), target_ticks=10))
    assert (t.reason, t.exit_ticks, t.slip_sides) == ("target", 1010, 1)


def test_roll_forces_exit_on_last_bar_and_blocks_entry_into_new_contract():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1000, 1001, 999, 1001), (1500, 1500, 1500, 1500),
                      (1500, 1500, 1500, 1500)], raw=["CLJ4"] * 3 + ["CLK4"] * 2)
    trades = run(Market.from_bars(bars), Signals(np.array([1, 1, 1, 0, 0])))
    assert len(trades) == 1  # bar 2 (last of CLJ4) and bar 3 (first of CLK4) take no entry
    t = trades.iloc[0]
    assert (t.entry_bar, t.exit_bar, t.reason, t.exit_ticks, t.raw_symbol) == (1, 2, "roll", 1001, "CLJ4")


def test_end_of_data_exit():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1003, 1003, 1003, 1003)])
    t = one_trade(bars, Signals(sides(3, 0)))
    assert (t.reason, t.exit_ticks) == ("end", 1003)


def test_eia_blackout_blocks_entry():
    bars = make_bars([FLAT_BAR] * 4, block=[False, True, False, False])
    assert run(Market.from_bars(bars), Signals(sides(4, 0))).empty


def test_stale_signal_after_gap_not_filled():
    bars = make_bars([FLAT_BAR, FLAT_BAR, FLAT_BAR], minutes=[0, 10, 11])
    assert run(Market.from_bars(bars), Signals(sides(3, 0), hold_minutes=1)).empty
    assert len(run(Market.from_bars(bars), Signals(sides(3, 0), hold_minutes=1), max_fill_delay_min=10)) == 1


def test_exit_signal_and_reversal():
    bars = make_bars([FLAT_BAR, (1000, 1000, 1000, 1000), (1004, 1004, 1004, 1004), (1001, 1001, 1001, 1001),
                      (1001, 1001, 1001, 1001)])
    side = np.array([1, 0, -1, 0, 0])
    ex = np.array([False, False, True, False, False])
    trades = run(Market.from_bars(bars), Signals(side, exit=ex))
    assert trades["side"].tolist() == [1, -1]
    assert trades["entry_bar"].tolist() == [1, 3] and trades["exit_bar"].tolist() == [3, 4]
    assert trades["reason"].tolist() == ["signal", "end"]
    assert trades["gross_ticks"].tolist() == [1, 0]


def test_one_position_at_a_time():
    bars = make_bars([FLAT_BAR] * 6)
    trades = run(Market.from_bars(bars), Signals(np.ones(6, np.int64), hold_minutes=2))
    assert trades["entry_bar"].tolist() == [1, 3]  # bar 3 exits at the open then re-enters on bar 2's signal
    assert trades["exit_bar"].tolist() == [3, 5]


def test_flat_exit_at_close():
    bars = make_bars([FLAT_BAR, FLAT_BAR, (1002, 1003, 1002, 1003), FLAT_BAR])
    flat = np.array([False, False, True, False])
    t = one_trade(bars, Signals(sides(4, 0), flat=flat))
    assert (t.exit_bar, t.reason, t.exit_ticks) == (2, "flat", 1003)


def test_costs_baseline_round_trip():
    bars = make_bars([FLAT_BAR] * 3)
    t = with_costs(run(Market.from_bars(bars), Signals(sides(3, 0), hold_minutes=1)))
    assert t["net_usd"].iloc[0] == pytest.approx(-24.02)
    stressed = with_costs(t, slip_ticks=2)
    assert stressed["net_usd"].iloc[0] == pytest.approx(-44.02)


def test_bad_signals_rejected():
    bars = make_bars([FLAT_BAR] * 3)
    with pytest.raises(ValueError):
        run(Market.from_bars(bars), Signals(np.array([2, 0, 0])))
    with pytest.raises(ValueError):
        run(Market.from_bars(bars), Signals(sides(3, 0), stop_ticks=-1))


def test_session_helpers():
    # 09:00 ET .. 09:05 ET bars; flat before 09:03 marks 09:02, first bar at 09:02 marks 09:02
    bars = make_bars([FLAT_BAR] * 6)
    assert flat_before(bars, 9 * 60 + 3).tolist() == [False, False, True, False, False, False]
    assert first_bar_at(bars, 9 * 60 + 2).tolist() == [False, False, True, False, False, False]
