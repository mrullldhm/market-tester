"""O10 first-hour NY range breakout, six situations (experiments/O10/SPEC.md rev 3). Own per-day simulator: the
generic engine fills only at the next open, these rules need resting stop / limit orders and a delayed stop.

Prices are integer ticks. Per day: range H/L from 09:00-09:59 ET, orders work 10:00-14:29 ET, one trade, flat
at 14:29 (close of the 14:29 bar, or the next bar's open if that minute has no bar). Long rules below; short is
the mirror (`p` = side). Each day is split into a setup (touch, fill, stop: no exits) and the exit path, so the
blind power check can read entries without any strategy P&L.
"""

from dataclasses import dataclass

import numba as nb
import numpy as np
import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday)

from market_tester.quantum.baseline import week_permutation
from market_tester.common.config import FEE_PER_SIDE, TICK_VALUE, assert_allowed
from market_tester.common.data import RollCrossed
from market_tester.quantum.engine import FLAT, REASONS, STOP, TARGET, validate_bars, with_costs

SITUATIONS = (1, 2, 3, 4, 5, 6)
ITEMS = {1: "O101", 2: "O102", 3: "O103", 4: "O104", 5: "O105", 6: "O106"}
TPS = (30, 31, 32, 33, 34, 35)
SLS = (15, 16, 20, 21, 25, 26, 30, 31)
USES_SL = (2, 4, 5, 6)
RANGE_FIRST, RANGE_LAST, WINDOW_FIRST, WINDOW_LAST = 9 * 60, 9 * 60 + 59, 10 * 60, 14 * 60 + 29
RANGE_BARS = 60
LATE_LAST = 16 * 60 + 59  # a flat fill after a missing 14:29 minute must come by then
WINDOW_LEN = WINDOW_LAST - WINDOW_FIRST + 1
MIN_KEPT = 0.5  # benchmark: a draw is valid if it keeps at least this share of the trades
MIN_VALID_DRAWS = 0.9  # ... and the p-value needs at least this share of valid draws


class _CmeShort(AbstractHolidayCalendar):
    """US holidays on which CL trades a shortened session (no 14:30 ET close). Christmas and New Year's Day are
    full closures (no bars) and need no rule. Juneteenth: CME observed it from 2022."""
    rules = [USMartinLutherKingJr, USPresidentsDay, USMemorialDay,
             Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
             Holiday("Independence", month=7, day=4, observance=nearest_workday), USLaborDay, USThanksgivingDay]


def short_sessions(first: pd.Timestamp, last: pd.Timestamp) -> pd.DatetimeIndex:
    """Scheduled shortened sessions (exchange calendar rules, known in advance): the holidays above, the day
    after Thanksgiving and Dec 24 on weekdays (13:45 ET close)."""
    hol = _CmeShort().holidays(first - pd.Timedelta(days=7), last + pd.Timedelta(days=7))
    thanks = USThanksgivingDay.dates(first - pd.Timedelta(days=366), last + pd.Timedelta(days=366))
    eve = pd.DatetimeIndex([pd.Timestamp(y, 12, 24) for y in range(first.year, last.year + 1)])
    out = hol.union(thanks + pd.Timedelta(days=1)).union(eve[eve.dayofweek < 5])
    return out[(out >= first) & (out <= last)]


def grid() -> list[dict]:
    """The 204 frozen configs: S1/S3 by TP only (sl 0), the others SL x TP."""
    return [{"situation": s, "sl": sl, "tp": tp} for s in SITUATIONS
            for sl in (SLS if s in USES_SL else (0,)) for tp in TPS]


@dataclass
class Days:
    """Bar arrays and per-day layout, built once and reused across the grid."""
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    blk_seen: np.ndarray  # window bars: an EIA-blocked bar occurred from 10:00 up to and including this bar
    bstart: np.ndarray  # window bars: first bar of the bar's wall-clock 5m bucket
    bend: np.ndarray  # window bars: last bar of that bucket
    day: pd.DatetimeIndex  # eligible trading days
    i0: np.ndarray  # first window bar
    i1: np.ndarray  # last window bar
    fj: np.ndarray  # flat bar: i1 if it is the 14:29 bar, else the next bar (flat at its open)
    fpx: np.ndarray  # flat price
    hi: np.ndarray  # range high H
    lo: np.ndarray  # range low L
    ts: np.ndarray
    raw_symbol: np.ndarray
    et_minute: np.ndarray
    bar_at: np.ndarray  # eligible day x window minute -> bar index, -1 if the minute has no bar
    short_days: pd.DatetimeIndex  # scheduled shortened sessions seen in the bars (not traded)
    data_failures: pd.DatetimeIndex  # scheduled full sessions with no flat fill by 16:59 (excluded, reported)
    calendar_mismatch: pd.DatetimeIndex  # scheduled short sessions that still have bars after 14:29


def build_days(bars: pd.DataFrame) -> Days:
    """Eligible days (SPEC 'Data and day eligibility') and their range, window and flat indices."""
    validate_bars(bars)
    bars = bars.reset_index(drop=True)
    m = bars["et_minute"].to_numpy(np.int64)
    td = bars["trading_day"].to_numpy()
    sess = (m >= RANGE_FIRST) & (m <= WINDOW_LAST)
    rng = (m >= RANGE_FIRST) & (m <= RANGE_LAST)
    win = (m >= WINDOW_FIRST) & (m <= WINDOW_LAST)
    flat_ok = (m >= WINDOW_LAST) & (m <= LATE_LAST)
    idx = pd.Series(np.arange(len(bars)))
    raw_n = bars.loc[sess].groupby("trading_day")["raw_symbol"].nunique()
    if (raw_n > 1).any():  # CL never rolls in US daytime, so this is a data fault, not a filter
        raise RollCrossed(f"raw_symbol changes inside 09:00-14:29 ET on {list(raw_n.index[raw_n > 1][:5])}")
    per = pd.DataFrame({
        "range_n": bars.loc[rng].groupby("trading_day")["et_minute"].nunique(),
        "hi": bars.loc[rng].groupby("trading_day")["high_ticks"].max(),
        "lo": bars.loc[rng].groupby("trading_day")["low_ticks"].min(),
        "i0": idx[win].groupby(td[win]).min(),
        "i1": idx[win].groupby(td[win]).max(),
        "fj": idx[flat_ok].groupby(td[flat_ok]).min(),  # first bar at or after 14:29
    })
    per.index = pd.DatetimeIndex(per.index)
    short = short_sessions(per.index.min(), per.index.max()) if len(per) else pd.DatetimeIndex([])
    is_short = per.index.isin(short)
    late = pd.Index(bars.loc[(m > WINDOW_LAST) & (m <= LATE_LAST), "trading_day"].unique())
    mismatch = per.index[is_short & per.index.isin(late)]
    base = (per["range_n"] == RANGE_BARS) & per["i0"].notna() & ~is_short
    failures = per.index[base & per["fj"].isna()]
    per = per[base & per["fj"].notna()]
    i0, i1, fj = (per[k].to_numpy(np.int64) for k in ("i0", "i1", "fj"))
    o, c = bars["open_ticks"].to_numpy(np.int64), bars["close_ticks"].to_numpy(np.int64)
    fpx = np.where(m[fj] == WINDOW_LAST, c[fj], o[fj])
    bar_at = np.full((len(per), WINDOW_LEN), -1, np.int64)
    for q in range(len(per)):
        js = np.arange(i0[q], i1[q] + 1)
        bar_at[q, m[js] - WINDOW_FIRST] = js
    bucket = pd.factorize(td[win])[0].astype(np.int64) * 1000 + m[win] // 5
    wi = idx[win].to_numpy()
    g = pd.Series(wi).groupby(bucket)
    bstart = np.full(len(bars), -1, np.int64)
    bend = np.full(len(bars), -1, np.int64)
    bstart[wi] = g.transform("min").to_numpy()
    bend[wi] = g.transform("max").to_numpy()
    blk = bars["eia_block"].to_numpy(bool) if "eia_block" in bars else np.zeros(len(bars), bool)
    blk_seen = np.zeros(len(bars), bool)
    blk_seen[wi] = pd.Series(blk[wi]).groupby(td[wi]).cummax().to_numpy(bool)
    return Days(o=o, h=bars["high_ticks"].to_numpy(np.int64), l=bars["low_ticks"].to_numpy(np.int64), c=c,
                blk_seen=blk_seen, bstart=bstart, bend=bend, day=pd.DatetimeIndex(per.index), i0=i0, i1=i1,
                fj=fj, fpx=fpx, hi=per["hi"].to_numpy(np.int64), lo=per["lo"].to_numpy(np.int64),
                ts=bars["ts_event_utc"].to_numpy(dtype="datetime64[ns]"), raw_symbol=bars["raw_symbol"].to_numpy(),
                et_minute=m, bar_at=bar_at, short_days=short.intersection(pd.DatetimeIndex(pd.unique(td))),
                data_failures=failures, calendar_mismatch=mismatch)


@nb.njit(cache=True, nogil=True)
def _manage(o, h, l, j0, i1, fj, fpx, p, stop, tgt):
    """Stop + TP working from bar j0 to the last window bar i1 (precedence table), then flat at (fj, fpx).
    Returns (exit bar, exit price, reason, slip)."""
    for j in range(j0, i1 + 1):
        if p * (o[j] - stop) <= 0:
            return j, o[j], STOP, 1
        if p * (o[j] - tgt) > 0:
            return j, tgt, TARGET, 0
        if (p > 0 and l[j] <= stop) or (p < 0 and h[j] >= stop):
            return j, stop, STOP, 1
        if (p > 0 and h[j] >= tgt + 1) or (p < 0 and l[j] <= tgt - 1):
            return j, tgt, TARGET, 0
    return fj, fpx, FLAT, 1


@nb.njit(cache=True, nogil=True)
def _no_stop(h, l, j0, w, i1, fj, fpx, p, tgt):
    """TP only, bars j0..min(w, i1). Returns (exit bar, price, reason, slip), exit bar -1 if still open."""
    end = min(w, i1)
    for j in range(j0, end + 1):
        if (p > 0 and h[j] >= tgt + 1) or (p < 0 and l[j] <= tgt - 1):
            return j, tgt, TARGET, 0
    if end == i1:
        return fj, fpx, FLAT, 1
    return -1, 0, -1, 0


@nb.njit(cache=True, nogil=True)
def _candle(l, h, a, b, p):
    """Low (long) / high (short) of bars a..b."""
    x = l[a] if p > 0 else h[a]
    for q in range(a, b + 1):
        x = min(x, l[q]) if p > 0 else max(x, h[q])
    return x


@nb.njit(cache=True, nogil=True)
def _setup(o, h, l, blk_seen, bstart, bend, a, b, hi, lo, sit, sl):
    """Entry of one day, no exits. Returns (fill bar or -1, side, entry, entry slip, stop, window end).
    Window end = last bar without a stop (S1-S4); S5/S6 return the fill bar (stop works on it)."""
    k, p = -1, 0
    for j in range(a, b + 1):
        up, dn = h[j] >= hi, l[j] <= lo
        if up and dn:
            return -1, 0, 0, 0, 0, 0
        if up or dn:
            k, p = j, 1 if up else -1
            break
    if k < 0:
        return -1, 0, 0, 0, 0, 0
    level = hi if p > 0 else lo
    if sit <= 4:
        if blk_seen[k]:  # EIA blocked bar at or before the touch: orders cancelled for the day
            return -1, 0, 0, 0, 0, 0
        entry = max(level, o[k]) if p > 0 else min(level, o[k])
        w = k if sit <= 2 else bend[k]
        stop = _candle(l, h, k if sit <= 2 else bstart[k], w, p)
        if sit == 2 or sit == 4:
            stop = max(stop, entry - sl) if p > 0 else min(stop, entry + sl)
        return k, p, entry, 1, stop, w
    e = k if sit == 5 else bend[k]
    if e >= b:
        return -1, 0, 0, 0, 0, 0
    stop = _candle(l, h, k if sit == 5 else bstart[k], e, p) - p
    lim = stop + p * sl
    for j in range(e + 1, b + 1):
        if blk_seen[j]:  # a blocked bar before the fill (from 10:00, incl. the break candle): cancelled
            return -1, 0, 0, 0, 0, 0
        if j == e + 1 and p * (o[j] - lim) <= 0:
            return j, p, o[j], 1, stop, j  # marketable on submission
        if (p > 0 and l[j] <= lim - 1) or (p < 0 and h[j] >= lim + 1):
            return j, p, lim, 0, stop, j  # resting limit: the limit price, never better
    return -1, 0, 0, 0, 0, 0


@nb.njit(cache=True, nogil=True)
def _simulate(o, h, l, blk_seen, bstart, bend, i0, i1, fj, fpx, hi, lo, sit, sl, tp, exits,
              out_day, out_side, out_i, out_j, out_in, out_out, out_slip, out_reason, out_dist, out_mae, out_sig):
    m = 0
    for d in range(len(i0)):
        b = i1[d]
        ji, p, entry, slip_in, stop, w = _setup(o, h, l, blk_seen, bstart, bend, i0[d], b, hi[d], lo[d], sit, sl)
        if ji < 0:
            continue
        out_day[m], out_side[m], out_i[m], out_in[m], out_dist[m] = d, p, ji, entry, p * (entry - stop)
        out_sig[m] = w if sit == 3 or sit == 4 else ji  # bar whose close makes the whole signal known
        if not exits:  # power check: entries only
            m += 1
            continue
        tgt = entry + p * tp
        mae = 0
        if sit <= 4:
            jx, px, why, sx = _no_stop(h, l, ji, w, b, fj[d], fpx[d], p, tgt)
            for q in range(ji, min(w if jx < 0 else jx, b) + 1):  # worst adverse move without a stop
                mae = max(mae, p * (entry - (l[q] if p > 0 else h[q])))
            if jx < 0:
                jx, px, why, sx = _manage(o, h, l, w + 1, b, fj[d], fpx[d], p, stop, tgt)
        elif p * (o[ji] - stop) <= 0:
            jx, px, why, sx = ji, o[ji], STOP, 1  # gapped through limit and stop: the loss is kept
        elif (p > 0 and l[ji] <= stop) or (p < 0 and h[ji] >= stop):
            jx, px, why, sx = ji, stop, STOP, 1
        else:  # TP from the bar after the fill
            jx, px, why, sx = _manage(o, h, l, ji + 1, b, fj[d], fpx[d], p, stop, tgt)
        out_j[m], out_out[m], out_slip[m], out_reason[m], out_mae[m] = jx, px, slip_in + sx, why, mae
        m += 1
    return m


def _check(situation: int, sl: int, tp: int) -> None:
    if situation not in SITUATIONS or tp <= 0 or (situation in USES_SL) != (sl > 0):
        raise ValueError(f"bad config situation={situation} sl={sl} tp={tp}")


def _call(days: Days, situation: int, sl: int, tp: int, exits: bool):
    n = len(days.i0)
    out = [np.zeros(n, np.int64) for _ in range(11)]
    m = _simulate(days.o, days.h, days.l, days.blk_seen, days.bstart, days.bend, days.i0, days.i1, days.fj,
                  days.fpx, days.hi, days.lo, situation, sl, tp, exits, *out)
    return [x[:m] for x in out]


def entries(days: Days, situation: int, sl: int) -> pd.DataFrame:
    """Entries only (day, side, fill bar, stop distance): no exit, no price outcome. For the blind power check."""
    _check(situation, sl, 1)
    d, s, i, *_, dist, _, sig = _call(days, situation, sl, 1, exits=False)
    return pd.DataFrame({"day_pos": d, "side": s, "entry_bar": i, "signal_bar": sig, "stop_dist": dist})


def run(days: Days, situation: int, sl: int, tp: int) -> pd.DataFrame:
    """Gross trades of one config (ticks); `engine.with_costs` adds net columns."""
    _check(situation, sl, tp)
    d, s, i, j, p_in, p_out, slip, reason, dist, mae, sig = _call(days, situation, sl, tp, exits=True)
    raw_in, raw_out = days.raw_symbol[i], days.raw_symbol[j]
    if (raw_in != raw_out).any():
        raise RollCrossed("a trade spans two raw_symbols")
    for sym in pd.unique(raw_in):
        assert_allowed(sym)
    return pd.DataFrame({
        "trading_day": days.day[d], "day_pos": d, "entry_bar": i, "exit_bar": j,
        "entry_time": pd.to_datetime(days.ts[i], utc=True), "exit_time": pd.to_datetime(days.ts[j], utc=True),
        "et_minute": days.et_minute[i], "raw_symbol": raw_in, "side": s, "entry_ticks": p_in,
        "exit_ticks": p_out, "gross_ticks": s * (p_out - p_in), "slip_sides": slip, "reason": REASONS[reason],
        "signal_bar": sig, "stop_dist": dist, "window_mae": mae})


def backtest(days: Days, situation: int, sl: int, tp: int, slip_ticks: float = 1) -> pd.DataFrame:
    return with_costs(run(days, situation, sl, tp), slip_ticks)


def day_pnl(trades: pd.DataFrame, days: Days, col: str = "net_usd") -> np.ndarray:
    """Net $ per eligible day, 0 on days without a trade (one trade a day at most)."""
    return trades.groupby("trading_day")[col].sum().reindex(days.day, fill_value=0.0).to_numpy(float)


@nb.njit(cache=True, nogil=True)
def _next_open(o, h, l, i1_of, fj_of, fpx_of, j0, dist, tp, side, out_gross, out_slip):
    """Entry-quality trades: market entry at bar j0's open, stop `dist` and TP `tp` working from that bar."""
    for t in range(len(j0)):
        p, e = side[t], o[j0[t]]
        _, px, _, sx = _manage(o, h, l, j0[t], i1_of[t], fj_of[t], fpx_of[t], p, e - p * dist[t], e + p * tp)
        out_gross[t], out_slip[t] = p * (px - e), 1 + sx


def _usable(ent: pd.DataFrame) -> np.ndarray:
    """Benchmark inputs: a stop already breached at the fill (distance <= 0) carries no signal question."""
    return ent["stop_dist"].to_numpy(np.int64) > 0


def _next_open_net(days: Days, ent: pd.DataFrame, day_pos: np.ndarray, tp: int, slip_ticks: float):
    """Net $ of each signal moved to `day_pos` at the same ET signal minute, entering at the open of the next
    wall-clock minute. NaN if the signal minute or the next minute has no bar, the signal is at 14:29, an EIA
    blocked bar came at or before the entry, or the stop distance is <= 0."""
    minute = days.et_minute[ent["signal_bar"].to_numpy(np.int64)] - WINDOW_FIRST
    nxt = np.minimum(minute + 1, WINDOW_LEN - 1)
    sig, j0 = days.bar_at[day_pos, minute], days.bar_at[day_pos, nxt]
    ok = (sig >= 0) & (minute + 1 < WINDOW_LEN) & (j0 >= 0) & _usable(ent)
    ok[ok] = ~days.blk_seen[j0[ok]]
    net = np.full(len(ent), np.nan)
    if ok.any():
        q = day_pos[ok]
        gross, slip = np.zeros(ok.sum(), np.int64), np.zeros(ok.sum(), np.int64)
        _next_open(days.o, days.h, days.l, days.i1[q], days.fj[q], days.fpx[q], j0[ok],
                   ent["stop_dist"].to_numpy(np.int64)[ok], tp, ent["side"].to_numpy(np.int64)[ok], gross, slip)
        net[ok] = (gross - slip * slip_ticks) * TICK_VALUE - 2 * FEE_PER_SIDE
    return net


def _shuffled(days: Days, ent: pd.DataFrame, n_draws: int, seed: int):
    """Partner day of every entry for each draw: a random other day of its Mon-Sun week (`week_permutation`)."""
    week = days.day.to_period("W").asi8
    rng = np.random.default_rng(seed)
    dq = ent["day_pos"].to_numpy(np.int64)
    for _ in range(n_draws):
        yield week_permutation(week, rng)[dq]


def null_draws(days: Days, ent: pd.DataFrame, tp: int, n_draws: int, seed: int, slip_ticks: float = 1):
    """Entry-quality benchmark draws. Returns (per-draw means, valid flags)."""
    means, valid = np.full(n_draws, np.nan), np.zeros(n_draws, bool)
    for r, partner in enumerate(_shuffled(days, ent, n_draws, seed)):
        net = _next_open_net(days, ent, partner, tp, slip_ticks)
        keep = np.isfinite(net)
        valid[r] = len(net) > 0 and keep.mean() >= MIN_KEPT
        if valid[r]:
            means[r] = net[keep].mean()
    return means, valid


def entry_quality(days: Days, trades: pd.DataFrame, tp: int, n_draws: int = 999, seed: int = 0,
                  slip_ticks: float = 1) -> dict:
    """SPEC 'Entry-quality benchmark': the real signals and the week-shuffled ones both enter at the open of
    the minute after the signal minute, with the same stop distance, TP and market costs. p = (1 + #valid draws
    with mean >= real) / (1 + #valid draws); NaN if too few valid draws or real signals kept."""
    nan = float("nan")
    out = {"p": nan, "real": nan, "null_mean": nan, "null_sd": nan, "valid_draws": 0, "real_kept": nan,
           "breached": int((~_usable(trades)).sum())}
    if trades.empty:
        return out
    real_net = _next_open_net(days, trades, trades["day_pos"].to_numpy(np.int64), tp, slip_ticks)
    keep = np.isfinite(real_net)
    means, valid = null_draws(days, trades, tp, n_draws, seed, slip_ticks)
    out.update(real_kept=float(keep.mean()), valid_draws=int(valid.sum()))
    if keep.mean() < MIN_KEPT or valid.sum() < MIN_VALID_DRAWS * n_draws:
        return out
    real = float(real_net[keep].mean())
    mv = means[valid]
    out.update(p=float((1 + (mv >= real).sum()) / (1 + len(mv))), real=real, null_mean=float(mv.mean()),
               null_sd=float(mv.std(ddof=1)) if len(mv) > 1 else nan)
    return out


@nb.njit(cache=True, nogil=True)
def _variance_trades(o, h, l, blk_seen, bstart, bend, i1_of, fj_of, fpx_of, j0, side, dist, sit, sl, tp,
                     out_gross, out_slip, out_ok):
    """Power variance model: market entry at bar j0's open with the situation's own exit timing and the entry's
    own stop distance `dist` (from `entries`, known at the signal): S1-S4 no stop until the entry bar / its 5m
    bucket closes, then the stop at `dist`; S5/S6 the stop on the entry bar, TP from the next bar."""
    for t in range(len(j0)):
        j, p, b = j0[t], side[t], i1_of[t]
        out_ok[t] = False
        if blk_seen[j]:
            continue
        e = o[j]
        tgt = e + p * tp
        if dist[t] < 0:
            continue
        stop = e - p * dist[t]
        if sit <= 4:
            w = j if sit <= 2 else bend[j]
            jx, px, why, sx = _no_stop(h, l, j, w, b, fj_of[t], fpx_of[t], p, tgt)
            if jx < 0:
                jx, px, why, sx = _manage(o, h, l, w + 1, b, fj_of[t], fpx_of[t], p, stop, tgt)
        else:
            if dist[t] == 0:
                continue
            if (p > 0 and l[j] <= stop) or (p < 0 and h[j] >= stop):
                px, sx = stop, 1
            elif j == b:
                px, sx = fpx_of[t], 1
            else:
                _, px, _, sx = _manage(o, h, l, j + 1, b, fj_of[t], fpx_of[t], p, stop, tgt)
        out_gross[t], out_slip[t], out_ok[t] = p * (px - e), 1 + sx, True


def variance_draws(days: Days, ent: pd.DataFrame, situation: int, sl: int, tp: int, n_draws: int, seed: int,
                   slip_ticks: float = 1) -> np.ndarray:
    """SPEC stage 1: pooled per-trade net $ of the variance model on week-shuffled days at each entry's fill
    minute. Uses entries only (`entries`), never the real trades' outcomes."""
    minute = days.et_minute[ent["entry_bar"].to_numpy(np.int64)] - WINDOW_FIRST
    side, dist = ent["side"].to_numpy(np.int64), ent["stop_dist"].to_numpy(np.int64)
    pooled = []
    for partner in _shuffled(days, ent, n_draws, seed):
        j0 = days.bar_at[partner, minute]
        has = j0 >= 0
        q = partner[has]
        g, sx, ok = np.zeros(has.sum(), np.int64), np.zeros(has.sum(), np.int64), np.zeros(has.sum(), bool)
        _variance_trades(days.o, days.h, days.l, days.blk_seen, days.bstart, days.bend, days.i1[q], days.fj[q],
                         days.fpx[q], j0[has], side[has], dist[has], situation, sl, tp, g, sx, ok)
        pooled.append(((g - sx * slip_ticks) * TICK_VALUE - 2 * FEE_PER_SIDE)[ok])
    return np.concatenate(pooled) if pooled else np.array([])
