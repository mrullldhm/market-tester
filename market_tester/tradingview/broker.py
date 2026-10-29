"""TradingView broker emulator (numba), rules in SPEC.md.

Per bar: roll / day-end guards, then orders worked along the intrabar path (open, nearer extreme, other
extreme, close; tie = low first), then the script at the close, then `close_all(immediately)`.
Prices are integer ticks and may be negative (CL 2020-04-20); NO_PRICE marks "none". Slippage moves market and
stop fills against the position inside the broker, so a TP measured from the fill moves with it.
"""

import numba as nb
import numpy as np

from .script import (ACTIVATED, ACTIVE, DAY, DIR, ENTRY, EXIT, LASTMIN, LIMIT, MARKET, NO_LIMIT, NO_PRICE, OCA,
                     P_BAR, P_CLOSE_NOW, P_DAY, P_DIR, P_ETYPE, P_LAST_EXIT_BAR, P_MAX, P_MIN, P_PX, P_SEG,
                     P_SUBMIT, P_TAG, PRICE, ROLE, SL, STOP, SUBMIT, TAG, TP, TYPE)

# trade record fields
(T_DIR, T_EBAR, T_XBAR, T_EPX, T_XPX, T_ETYPE, T_REASON, T_MAX, T_MIN, T_SLIP, T_TAG, T_SLPX, T_TPPX,
 T_SUBMIT) = range(14)
NTRADE = 14
# exit reasons
R_STOP, R_LIMIT, R_CLOSE, R_MARKET, R_DAY_END, R_END = range(6)
REASONS = np.array(["stop", "limit", "close", "market", "day_end", "end"])
ERR_ROLL = -1


@nb.njit(cache=True)
def _workable(book, s, day_k, minute_k):
    if book[s, ACTIVE] == 0:
        return False
    if book[s, ROLE] == ENTRY:
        if book[s, DAY] != -1 and book[s, DAY] != day_k:
            return False
        if book[s, LASTMIN] != NO_LIMIT and minute_k > book[s, LASTMIN]:
            return False
    return True


@nb.njit(cache=True)
def _marketable_at(book, s, px):
    """True if order s fills at price px as the first trade of a bar (gap or already marketable)."""
    t, d, p = book[s, TYPE], book[s, DIR], book[s, PRICE]
    if t == MARKET:
        return True
    if t == STOP:
        return (d > 0 and px >= p) or (d < 0 and px <= p)
    return (d > 0 and px <= p) or (d < 0 and px >= p)  # LIMIT


@nb.njit(cache=True)
def _trigger(book, s, a, b):
    """(hit, price) of order s on the path segment a -> b (a != b), excluding a itself."""
    t, d, p = book[s, TYPE], book[s, DIR], book[s, PRICE]
    if t == MARKET:
        return False, 0
    if b > a and a < p <= b and ((t == STOP and d > 0) or (t == LIMIT and d < 0)):
        return True, p
    if b < a and b <= p < a and ((t == STOP and d < 0) or (t == LIMIT and d > 0)):
        return True, p
    return False, 0


@nb.njit(cache=True)
def _add_exit(book, typ, d, price):
    for r in range(book.shape[0]):
        if book[r, ACTIVE] == 0:
            book[r, ACTIVE] = 1
            book[r, ROLE] = EXIT
            book[r, TYPE] = typ
            book[r, DIR] = -d
            book[r, PRICE] = price
            book[r, DAY] = -1
            book[r, LASTMIN] = NO_LIMIT
            book[r, OCA] = 0
            book[r, ACTIVATED] = 0
            book[r, SUBMIT] = -1
            return
    raise ValueError("order book full")


@nb.njit(cache=True)
def _open_position(book, pos, s, px, k, seg_k, day_k, slip):
    """Fill entry s at path price px (market / stop fills slipped against the position)."""
    d = book[s, DIR]
    fill = px if book[s, TYPE] == LIMIT else px + d * slip
    pos[P_DIR] = d
    pos[P_PX] = fill
    pos[P_BAR] = k
    pos[P_SEG] = seg_k
    pos[P_DAY] = day_k
    pos[P_TAG] = book[s, TAG]
    pos[P_MIN] = min(px, fill)
    pos[P_MAX] = max(px, fill)
    pos[P_ETYPE] = book[s, TYPE]
    pos[P_SUBMIT] = book[s, SUBMIT]
    tp, sl, oca = book[s, TP], book[s, SL], book[s, OCA]
    book[s, ACTIVE] = 0
    for r in range(book.shape[0]):  # oca.cancel; stale exits of an earlier position
        if book[r, ACTIVE] == 1 and ((oca > 0 and book[r, ROLE] == ENTRY and book[r, OCA] == oca)
                                     or book[r, ROLE] == EXIT):
            book[r, ACTIVE] = 0
    if tp > 0:
        _add_exit(book, LIMIT, d, fill + d * tp)
    if sl != NO_PRICE:
        _add_exit(book, STOP, d, sl)


@nb.njit(cache=True)
def _close_position(book, pos, trades, m, k, px, reason, slip):
    """Close at path price px; every reason but a limit is a market / stop fill and is slipped."""
    d = pos[P_DIR]
    fill = px if reason == R_LIMIT else px - d * slip
    sl_px, tp_px = NO_PRICE, NO_PRICE
    for r in range(book.shape[0]):
        if book[r, ACTIVE] == 1 and book[r, ROLE] == EXIT:
            if book[r, TYPE] == LIMIT:
                tp_px = book[r, PRICE]
            else:
                sl_px = book[r, PRICE]
            book[r, ACTIVE] = 0
    if reason == R_STOP and sl_px == NO_PRICE:
        sl_px = px
    if reason == R_LIMIT:
        tp_px = px
    t = trades[m]
    t[T_DIR] = d
    t[T_EBAR] = pos[P_BAR]
    t[T_XBAR] = k
    t[T_EPX] = pos[P_PX]
    t[T_XPX] = fill
    t[T_ETYPE] = pos[P_ETYPE]
    t[T_REASON] = reason
    t[T_MAX] = max(pos[P_MAX], px, fill)
    t[T_MIN] = min(pos[P_MIN], px, fill)
    t[T_SLIP] = (0 if pos[P_ETYPE] == LIMIT else 1) + (0 if reason == R_LIMIT else 1)
    t[T_TAG] = pos[P_TAG]
    t[T_SLPX] = sl_px
    t[T_TPPX] = tp_px
    t[T_SUBMIT] = pos[P_SUBMIT]
    pos[P_DIR] = 0
    pos[P_CLOSE_NOW] = 0
    pos[P_LAST_EXIT_BAR] = k
    return m + 1


@nb.njit(cache=True)
def _seen(pos, px):
    if px < pos[P_MIN]:
        pos[P_MIN] = px
    if px > pos[P_MAX]:
        pos[P_MAX] = px


@nb.njit(cache=True)
def _exit_reason(book, s):
    t = book[s, TYPE]
    if t == LIMIT:
        return R_LIMIT
    return R_STOP if t == STOP or book[s, ACTIVATED] == 1 else R_MARKET


@nb.njit(cache=True)
def _at_open(book, pos, trades, m, k, px, seg_k, day_k, minute_k, slip):
    """Orders that fill at the bar's first trade: entries when flat, then exits of the (new) position."""
    if pos[P_DIR] == 0:
        for s in range(book.shape[0]):
            if book[s, ROLE] == ENTRY and _workable(book, s, day_k, minute_k) and _marketable_at(book, s, px):
                _open_position(book, pos, s, px, k, seg_k, day_k, slip)
                break
    if pos[P_DIR] != 0:
        _seen(pos, px)
        for s in range(book.shape[0]):
            if book[s, ACTIVE] == 1 and book[s, ROLE] == EXIT and _marketable_at(book, s, px):
                return _close_position(book, pos, trades, m, k, px, _exit_reason(book, s), slip)
    return m


@nb.njit(cache=True)
def _segment(book, pos, trades, m, k, a, b, seg_k, day_k, minute_k, slip):
    """Work orders along a -> b in path order; an entry's exits continue from its path price."""
    while True:
        best, best_px = -1, 0
        want = EXIT if pos[P_DIR] != 0 else ENTRY
        for s in range(book.shape[0]):
            if book[s, ROLE] != want or not _workable(book, s, day_k, minute_k):
                continue
            hit, p = _trigger(book, s, a, b)
            if hit and (best < 0 or abs(p - a) < abs(best_px - a)):
                best, best_px = s, p
        if best < 0:
            if pos[P_DIR] != 0:
                _seen(pos, b)
            return m
        if want == ENTRY:
            _open_position(book, pos, best, best_px, k, seg_k, day_k, slip)
        else:
            _seen(pos, best_px)
            m = _close_position(book, pos, trades, m, k, best_px, _exit_reason(book, best), slip)
        a = best_px
        if a == b:
            return m


@nb.njit(cache=True)
def simulate(script, o, h, l, c, seg, minute, day, feat_i, feat_f, params, state, book, pos, trades,
             flat_on_new_day, slip):
    """Run `script` over all bars. Returns the number of trades written, or ERR_ROLL - k when a position is open
    on a bar of another contract (never liquidated in advance: that would need the roll date)."""
    n = len(o)
    m = 0
    for k in range(n):
        if pos[P_DIR] != 0 and seg[k] != pos[P_SEG]:
            return ERR_ROLL - k
        for s in range(book.shape[0]):  # entries of a past trading day
            if book[s, ACTIVE] == 1 and book[s, ROLE] == ENTRY and book[s, DAY] != -1 and book[s, DAY] < day[k]:
                book[s, ACTIVE] = 0
        if pos[P_DIR] != 0 and flat_on_new_day and day[k] != pos[P_DAY]:
            m = _close_position(book, pos, trades, m, k, o[k], R_DAY_END, slip)
        elif pos[P_DIR] != 0 and pos[P_CLOSE_NOW] == 2:
            m = _close_position(book, pos, trades, m, k, o[k], R_MARKET, slip)
        m = _at_open(book, pos, trades, m, k, o[k], seg[k], day[k], minute[k], slip)
        if h[k] - o[k] < o[k] - l[k]:
            x1, x2 = h[k], l[k]
        else:
            x1, x2 = l[k], h[k]
        a = o[k]
        for b in (x1, x2, c[k]):
            if b != a:
                m = _segment(book, pos, trades, m, k, a, b, seg[k], day[k], minute[k], slip)
            a = b
        script(k, o, h, l, c, minute, day, feat_i, feat_f, params, state, book, pos)
        for s in range(book.shape[0]):
            if book[s, ACTIVE] == 1 and book[s, SUBMIT] == -1:
                book[s, SUBMIT] = k
            # stops at or through the close are triggered now (SPEC 2b)
            if book[s, TYPE] == STOP and _workable(book, s, day[k], minute[k]) and _marketable_at(book, s, c[k]):
                if book[s, ROLE] == EXIT and pos[P_DIR] == 0:
                    continue
                book[s, TYPE] = MARKET
                book[s, ACTIVATED] = 1
        if pos[P_CLOSE_NOW] == 1:
            if pos[P_DIR] != 0:
                m = _close_position(book, pos, trades, m, k, c[k], R_CLOSE, slip)
            pos[P_CLOSE_NOW] = 0
        elif pos[P_CLOSE_NOW] == 2 and pos[P_DIR] == 0:
            pos[P_CLOSE_NOW] = 0
    if pos[P_DIR] != 0:
        m = _close_position(book, pos, trades, m, n - 1, c[n - 1], R_END, slip)
    return m
