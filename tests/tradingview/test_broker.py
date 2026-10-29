"""TV broker emulator: hand-built bars, one rule per test (market_tester/tradingview/SPEC.md)."""

import numba as nb
import numpy as np
import pytest

from market_tester.tradingview import script as sc
from market_tester.tradingview.broker import (ERR_ROLL, NTRADE, R_CLOSE, R_DAY_END, R_LIMIT, R_STOP, T_DIR, T_EBAR,
                                              T_EPX, T_MAX, T_MIN, T_REASON, T_SLIP, T_XBAR, T_XPX, simulate)

# params: [bar to submit at, direction, type, price, tp ticks, sl price, close_all bar, exit-stop bar, exit-stop px]


@nb.njit
def one_order(k, o, h, l, c, minute, day, feat_i, feat_f, p, state, book, pos):
    if k == p[0]:
        sc.entry(book, p[1], p[2], p[3], 1, -1, sc.NO_LIMIT, p[4], p[5] if p[5] != 0 else sc.NO_PRICE, 0)
    if k == p[7] and pos[sc.P_DIR] != 0:
        sc.exit_stop(book, pos, p[8])
    if k == p[6]:
        sc.close_all(pos, True)


@nb.njit
def bracket_pair(k, o, h, l, c, minute, day, feat_i, feat_f, p, state, book, pos):
    """Buy stop p[1] and sell stop p[2] in one OCA group at bar 0's close, TP p[3] ticks."""
    if k == 0:
        sc.entry(book, 1, sc.STOP, p[1], 7, -1, sc.NO_LIMIT, p[3], sc.NO_PRICE, 0)
        sc.entry(book, -1, sc.STOP, p[2], 7, -1, sc.NO_LIMIT, p[3], sc.NO_PRICE, 0)


def run(bars, fn, params, seg=None, day=None, flat_on_new_day=False, slip=0):
    o, h, l, c = (np.array(x, np.int64) for x in zip(*bars))
    n = len(o)
    seg = np.zeros(n, np.int64) if seg is None else np.array(seg, np.int64)
    day = np.zeros(n, np.int64) if day is None else np.array(day, np.int64)
    trades = np.zeros((16, NTRADE), np.int64)
    m = simulate(fn, o, h, l, c, seg, np.full(n, 600, np.int64), day, np.zeros((n, 1), np.int64),
                 np.zeros((n, 1)), np.array(params, np.int64), np.zeros(4, np.int64), sc.new_book(), sc.new_pos(),
                 trades, flat_on_new_day, slip)
    return m, trades[:max(m, 0)]


def p(bar=0, d=1, t=sc.STOP, px=0, tp=0, sl=0, close_at=-1, xs_bar=-1, xs_px=0):
    return [bar, d, t, px, tp, sl, close_at, xs_bar, xs_px]


def test_stop_fills_at_price_and_gap_fills_at_open():
    m, t = run([(100, 101, 99, 100), (100, 106, 99, 105), (105, 105, 105, 105)], one_order, p(px=103, close_at=1))
    assert m == 1 and t[0, T_EPX] == 103 and t[0, T_EBAR] == 1 and t[0, T_XPX] == 105 and t[0, T_REASON] == R_CLOSE
    m, t = run([(100, 101, 99, 100), (108, 110, 107, 109), (109, 109, 109, 109)], one_order, p(px=103, close_at=1))
    assert t[0, T_EPX] == 108  # gapped through the stop: fills at the open


def test_limit_fills_on_touch_and_better_open():
    m, t = run([(100, 101, 99, 100), (100, 100, 95, 97)], one_order, p(t=sc.LIMIT, px=95, close_at=1))
    assert m == 1 and t[0, T_EPX] == 95  # touch is enough (no trade-through)
    m, t = run([(100, 101, 99, 100), (100, 100, 96, 97)], one_order, p(t=sc.LIMIT, px=95, close_at=1))
    assert m == 0
    m, t = run([(100, 101, 99, 100), (100, 103, 98, 101)], one_order, p(t=sc.LIMIT, px=104, close_at=1))
    assert t[0, T_EPX] == 100  # buy limit above the market: marketable, fills at the next open


def test_intrabar_path_order_decides_tp_vs_stop():
    # long at 100 (market entry fills bar 1 open), TP +3 = 103, stop 96 placed with the entry.
    # bar 2 open 100, high 104, low 95: high nearer the open -> O-H-L-C -> TP first
    m, t = run([(100, 100, 100, 100), (100, 100, 100, 100), (100, 104, 95, 100)], one_order,
               p(t=sc.MARKET, tp=3, sl=96))
    assert t[0, T_REASON] == R_LIMIT and t[0, T_XPX] == 103
    # low nearer the open -> O-L-H-C -> stop first
    m, t = run([(100, 100, 100, 100), (100, 100, 100, 100), (100, 108, 96, 100)], one_order,
               p(t=sc.MARKET, tp=5, sl=97))
    assert t[0, T_REASON] == R_STOP and t[0, T_XPX] == 97
    # tie |H-O| == |O-L| -> low first
    m, t = run([(100, 100, 100, 100), (100, 100, 100, 100), (100, 105, 95, 100)], one_order,
               p(t=sc.MARKET, tp=5, sl=95))
    assert t[0, T_REASON] == R_STOP


def test_same_bar_entry_and_tp_from_actual_fill():
    # buy stop 103, TP +3 from the fill; bar 1 runs O 100 -> H 107: entry at 103 then TP 106 on the same bar
    m, t = run([(100, 101, 99, 100), (100, 107, 99, 104)], one_order, p(px=103, tp=3))
    assert m == 1 and t[0, T_EBAR] == 1 and t[0, T_XBAR] == 1 and t[0, T_XPX] == 106
    # gap entry at 108: TP = 111 from the fill, not 106 from the order price
    m, t = run([(100, 101, 99, 100), (108, 110, 107, 109), (109, 112, 108, 111)], one_order, p(px=103, tp=3))
    assert t[0, T_EPX] == 108 and t[0, T_XPX] == 111 and t[0, T_XBAR] == 2


def test_oca_first_fill_cancels_the_other():
    # buy stop 103 / sell stop 97; bar 1 O 100, L 96 (nearer) then H 104: sell fills first, buy never fills
    m, t = run([(100, 101, 99, 100), (100, 104, 96, 100), (100, 110, 90, 100)], bracket_pair, [0, 103, 97, 0])
    assert m == 0 or (t[:, T_DIR] == -1).all()
    m, t = run([(100, 101, 99, 100), (100, 107, 94, 100), (100, 100, 100, 100)], bracket_pair, [0, 103, 97, 2])
    assert m == 1 and t[0, T_DIR] == -1 and t[0, T_EPX] == 97 and t[0, T_XPX] == 95  # TP 2 below on the way down


def test_exit_stop_already_beyond_market_fills_next_open():
    # long at 100 (bar 1 open); at bar 1 close (98) a sell stop at 99 is above the market -> bar 2 open 97
    m, t = run([(100, 100, 100, 100), (100, 101, 98, 98), (97, 99, 96, 98)], one_order,
               p(t=sc.MARKET, xs_bar=1, xs_px=99))
    assert t[0, T_REASON] == R_STOP and t[0, T_XPX] == 97 and t[0, T_XBAR] == 2


def test_excursions_and_slip_count():
    m, t = run([(100, 100, 100, 100), (100, 103, 97, 101), (101, 104, 99, 102)], one_order,
               p(t=sc.MARKET, close_at=2))
    assert t[0, T_MAX] == 104 and t[0, T_MIN] == 97
    assert t[0, T_SLIP] == 2  # market entry + market close
    m, t = run([(100, 101, 99, 100), (100, 100, 95, 97)], one_order, p(t=sc.LIMIT, px=95, tp=3))
    m, t = run([(100, 101, 99, 100), (100, 100, 95, 97), (97, 99, 96, 98)], one_order, p(t=sc.LIMIT, px=95, tp=3))
    assert t[0, T_SLIP] == 0  # limit in, limit out


def test_roll_guard_and_day_end():
    m, t = run([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100)], one_order, p(t=sc.MARKET),
               seg=[0, 0, 0])
    assert m == 1 and t[0, T_XBAR] == 2  # closed at the end of the data
    m, _ = run([(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 100, 100), (90, 90, 90, 90)], one_order,
               p(t=sc.MARKET), seg=[0, 0, 0, 1])
    assert m == ERR_ROLL - 3  # a position on the new contract is an error, never a pre-emptive exit
    m, t = run([(100, 100, 100, 100), (100, 100, 100, 100), (95, 96, 94, 95)], one_order, p(t=sc.MARKET),
               day=[0, 0, 1], flat_on_new_day=True)
    assert t[0, T_REASON] == R_DAY_END and t[0, T_XPX] == 95 and t[0, T_XBAR] == 2


def test_slippage_moves_fills_and_tp_from_fill():
    # buy stop 103 slipped 1 -> fill 104, TP +3 from the fill = 107 (not 106); exit limit not slipped
    m, t = run([(100, 101, 99, 100), (100, 110, 99, 104)], one_order, p(px=103, tp=3), slip=1)
    assert t[0, T_EPX] == 104 and t[0, T_XPX] == 107


def test_negative_prices_work():
    # CL below zero: buy stop -90 crossed from -100 to -80, TP +5 -> -85
    m, t = run([(-100, -100, -101, -100), (-100, -80, -100, -82)], one_order, p(px=-90, tp=5))
    assert m == 1 and t[0, T_EPX] == -90 and t[0, T_XPX] == -85
    m, t = run([(-100, -100, -101, -100), (-100, -99, -101, -100), (-100, -99, -106, -105)], one_order,
               p(t=sc.MARKET, sl=-105))
    assert t[0, T_REASON] == R_STOP and t[0, T_XPX] == -105


@pytest.mark.parametrize("d", [1, -1])
def test_short_mirror(d):
    m, t = run([(100, 101, 99, 100), (100, 100, 100, 100), (100, 104, 96, 100)], one_order,
               p(t=sc.MARKET, d=d, tp=3, close_at=2))
    assert t[0, T_DIR] == d and t[0, T_XPX] == 100 + 3 * d


def test_activated_stop_fills_next_open_even_after_gap_back():
    # long at 100; at bar 1 close (98) a sell stop at 99 is through the market: triggered now, so bar 2 fills at
    # its open 101 although the open is back above the stop
    m, t = run([(100, 100, 100, 100), (100, 101, 98, 98), (101, 103, 100, 102)], one_order,
               p(t=sc.MARKET, xs_bar=1, xs_px=99))
    assert t[0, T_REASON] == R_STOP and t[0, T_XPX] == 101 and t[0, T_XBAR] == 2 and t[0, T_SLIP] == 2


def test_entry_stop_at_close_is_activated():
    # buy stop 100 submitted with close 100: triggered, fills at the next open 99
    m, t = run([(100, 100, 99, 100), (99, 99, 98, 98), (98, 98, 98, 98)], one_order, p(px=100, close_at=1))
    assert t[0, T_EPX] == 99 and t[0, T_EBAR] == 1
