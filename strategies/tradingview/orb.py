"""ORB-TV: first-hour NY range breakout, 6 situations (experiments/tradingview/ORB/SPEC.md), as a TV script.

Range = 1m bars 09:00..09:59 ET; orders work 10:00..16:07; flat at the first bar close from 16:09 (16:10:00).
"""

import numba as nb
import numpy as np
import pandas as pd

from market_tester.common.data import RollCrossed
from market_tester.tradingview import script as sc
from market_tester.tradingview.calendar import short_sessions

SITUATIONS = (1, 2, 3, 4, 5, 6)
ITEMS = {s: f"TV10{s}" for s in SITUATIONS}
TPS = (30, 31, 32, 33, 34)
SLS = (15, 16, 20, 21, 25, 26, 30, 31)
USES_SL = (2, 4, 5, 6)
RANGE_FIRST, RANGE_LAST = 9 * 60, 9 * 60 + 59
WINDOW_FIRST, LAST_ENTRY = 10 * 60, 16 * 60 + 7
FLAT_FIRST, FLAT_LAST = 16 * 60 + 9, 16 * 60 + 59
RANGE_BARS = 60

# feat_i columns
F_ELIG, F_H, F_L, F_BUCKET, F_NCONF = range(5)
F_C1, F_C2 = 5, 9  # each: bucket id, high, low, start minute
# state
S_DAY, S_PHASE, S_FILL_BUCKET = range(3)
IDLE, ARMED, LIMIT_SENT, IN_TRADE, DONE = range(5)


def grid() -> list[dict]:
    """The 170 frozen configs: S1/S3 by TP only (sl 0), the others SL x TP."""
    return [{"situation": s, "sl": sl, "tp": tp} for s in SITUATIONS
            for sl in (SLS if s in USES_SL else (0,)) for tp in TPS]


def buckets(bars: pd.DataFrame):
    """Per bar: own 5m bucket id; per bucket: high, low, start minute, confirming bar (SPEC 5m rule)."""
    minute = bars["et_minute"].to_numpy(np.int64)
    day = bars["trading_day"].to_numpy("datetime64[D]").astype(np.int64)
    start = minute - minute % 5
    key = day * 2000 + start
    bid = np.cumsum(np.r_[True, key[1:] != key[:-1]]) - 1
    n_b = int(bid[-1]) + 1
    hi = pd.Series(bars["high_ticks"].to_numpy()).groupby(bid).max().to_numpy()
    lo = pd.Series(bars["low_ticks"].to_numpy()).groupby(bid).min().to_numpy()
    first = np.r_[0, np.flatnonzero(np.diff(bid)) + 1]
    last = np.r_[first[1:] - 1, len(bid) - 1]
    b_start = start[first]
    closes_bucket = minute[last] % 5 == 4
    conf = np.where(closes_bucket, last, last + 1)  # missing :x4 -> the first later bar confirms it
    keep = conf < len(bid)
    return bid, hi, lo, b_start, np.where(keep, conf, -1), n_b


class Orb:
    name = "orb_tv"
    flat_on_new_day = True
    state_size = 3

    def prepare(self, bars: pd.DataFrame):
        n = len(bars)
        minute = bars["et_minute"].to_numpy(np.int64)
        day = bars["trading_day"]
        in_range = (minute >= RANGE_FIRST) & (minute <= RANGE_LAST)
        r = bars.loc[in_range]
        g = r.groupby("trading_day")
        rng = pd.DataFrame({"n": g["et_minute"].nunique(), "H": g["high_ticks"].max(), "L": g["low_ticks"].min()})
        short = short_sessions(day.min(), day.max())
        days = pd.DatetimeIndex(pd.unique(day))
        reason = pd.Series("", index=days)
        reason[days.isin(short)] = "short_session"
        n_rng = rng["n"].reindex(days).fillna(0)
        reason[(reason == "") & (n_rng < RANGE_BARS)] = "range_minutes_missing"
        reason[(reason == "") & (n_rng == 0)] = "no_range_bars"
        elig_days = reason.index[reason == ""]
        # roll guard: one raw_symbol from 09:00 to 16:10 on an eligible day
        sess = (minute >= RANGE_FIRST) & (minute <= FLAT_FIRST) & day.isin(elig_days).to_numpy()
        n_raw = bars.loc[sess].groupby("trading_day")["raw_symbol"].nunique()
        if (n_raw > 1).any():
            raise RollCrossed(f"raw_symbol switch inside the NY session on {list(n_raw[n_raw > 1].index[:5])}")
        feat_i = np.zeros((n, 13), np.int64)
        elig = day.isin(elig_days).to_numpy()
        feat_i[:, F_ELIG] = elig
        feat_i[:, F_H] = day.map(rng["H"]).fillna(0).to_numpy(np.int64)
        feat_i[:, F_L] = day.map(rng["L"]).fillna(0).to_numpy(np.int64)
        bid, hi, lo, b_start, conf, _ = buckets(bars)
        feat_i[:, F_BUCKET] = bid
        feat_i[:, F_C1] = feat_i[:, F_C2] = -1
        for b in np.argsort(conf, kind="stable"):  # bucket order inside one confirming bar
            k = conf[b]
            if k < 0:
                continue
            col = F_C1 if feat_i[k, F_NCONF] == 0 else F_C2
            feat_i[k, col:col + 4] = (b, hi[b], lo[b], b_start[b])
            feat_i[k, F_NCONF] += 1
        if (feat_i[:, F_NCONF] > 2).any():
            raise ValueError("more than two 5m buckets confirmed by one bar")
        excluded = reason[reason != ""]
        return feat_i, np.zeros((n, 1)), {"excluded_days": excluded}

    def params(self, config: dict) -> np.ndarray:
        return np.array([config["situation"], config["tp"], config["sl"]], np.int64)

    script = staticmethod(None)  # set below (njit function)


@nb.njit(cache=True)
def _capped(d, fill, ext, cap):
    """S2/S4 stop: the candle extreme if within `cap` ticks of the fill, else fill -/+ cap."""
    if cap <= 0:
        return ext
    if d * (fill - ext) <= cap:
        return ext
    return fill - d * cap


@nb.njit(cache=True)
def orb_script(k, o, h, l, c, minute, day, fi, ff, p, st, book, pos):
    sit, tp, cap = p[0], p[1], p[2]
    d_k, mi = day[k], minute[k]
    if st[S_DAY] != d_k:
        st[S_DAY] = d_k
        st[S_PHASE] = IDLE
        st[S_FILL_BUCKET] = -1
    phase = st[S_PHASE]
    # fills during this bar
    if pos[sc.P_DIR] != 0 and (phase == ARMED or phase == LIMIT_SENT):
        sc.cancel_entries(book)
        st[S_FILL_BUCKET] = fi[pos[sc.P_BAR], F_BUCKET]
        phase = IN_TRADE if sit <= 4 else DONE
    elif pos[sc.P_DIR] == 0 and pos[sc.P_LAST_EXIT_BAR] == k and phase != IDLE:
        sc.cancel_entries(book)  # entered and exited inside this bar
        phase = DONE
    elif pos[sc.P_DIR] == 0 and phase == IN_TRADE:
        phase = DONE
    # arm at the 09:59 close
    if phase == IDLE and mi == RANGE_LAST and fi[k, F_ELIG] == 1:
        if sit <= 4:
            sc.entry(book, 1, sc.STOP, fi[k, F_H], 1, d_k, LAST_ENTRY, tp, sc.NO_PRICE, 1)
            sc.entry(book, -1, sc.STOP, fi[k, F_L], 1, d_k, LAST_ENTRY, tp, sc.NO_PRICE, 2)
        phase = ARMED
    # S5 / S6: first breakout candle -> limit entry at stop +/- cap
    elif phase == ARMED and sit >= 5 and WINDOW_FIRST <= mi <= LAST_ENTRY:
        hh, ll, found = 0, 0, False
        if sit == 5:
            hh, ll, found = h[k], l[k], True
        else:
            for j in range(fi[k, F_NCONF]):
                col = F_C1 if j == 0 else F_C2
                if fi[k, col + 3] >= WINDOW_FIRST:
                    b_hi, b_lo = fi[k, col + 1], fi[k, col + 2]
                    if b_hi >= fi[k, F_H] or b_lo <= fi[k, F_L]:
                        hh, ll, found = b_hi, b_lo, True
                        break
        if found:
            up, dn = hh >= fi[k, F_H], ll <= fi[k, F_L]
            if up and dn:
                phase = DONE
            elif up:
                sc.entry(book, 1, sc.LIMIT, ll + cap, 0, d_k, LAST_ENTRY, tp, ll, 1)
                phase = LIMIT_SENT
            elif dn:
                sc.entry(book, -1, sc.LIMIT, hh - cap, 0, d_k, LAST_ENTRY, tp, hh, 2)
                phase = LIMIT_SENT
    # S1-S4: stop loss once the breakout candle is complete
    if phase == IN_TRADE and pos[sc.P_DIR] != 0:
        dd, fill = pos[sc.P_DIR], pos[sc.P_PX]
        ext, have = 0, False
        if sit <= 2 and k == pos[sc.P_BAR]:
            ext, have = (l[k] if dd > 0 else h[k]), True
        elif sit >= 3:
            for j in range(fi[k, F_NCONF]):
                col = F_C1 if j == 0 else F_C2
                if fi[k, col] == st[S_FILL_BUCKET]:
                    ext, have = (fi[k, col + 2] if dd > 0 else fi[k, col + 1]), True
        if have:
            sc.exit_stop(book, pos, _capped(dd, fill, ext, cap if (sit == 2 or sit == 4) else 0))
            phase = DONE
    # flat by 16:10 ET
    if pos[sc.P_DIR] != 0 and FLAT_FIRST <= mi <= FLAT_LAST:
        sc.close_all(pos, True)
    st[S_PHASE] = phase


Orb.script = orb_script
