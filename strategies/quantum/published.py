"""Ten published day-trading strategies, frozen as in experiments/P10/SPEC.md (P101..P110).

Day session = 09:00..14:29 ET; entry signals on bars 09:00..13:59 ET; every position is flat at the close of
the first bar at or after 14:29.
Prior-day values come from the previous trading day's day session and are dropped across a roll.
5-minute bars exist only at 1-minute bars whose ET minute ends in 4 or 9 (bucket close), so they are causal.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from market_tester.quantum.strategy import Signals, contract_segments, first_bar_at

OPEN = 9 * 60
LAST_ENTRY = 14 * 60  # signal bars before 14:00 ET
SETTLE = 14 * 60 + 30  # day session ends; flat at the close of the first bar at or after 14:29
NS_PER_MIN = 60_000_000_000


def entry_window(bars: pd.DataFrame) -> np.ndarray:
    m = bars["et_minute"].to_numpy()
    return (m >= OPEN) & (m < LAST_ENTRY)


def day_session(bars: pd.DataFrame) -> np.ndarray:
    m = bars["et_minute"].to_numpy()
    return (m >= OPEN) & (m < SETTLE)


def day_value(bars: pd.DataFrame, mask: np.ndarray, column: str) -> np.ndarray:
    """Per bar: `column` at the first `mask` bar of the bar's trading day (nan if none so far or ever)."""
    day = bars["trading_day"].to_numpy()
    first = pd.Series(bars[column].to_numpy()[mask], index=day[mask])
    first = first[~first.index.duplicated()]
    return pd.Series(day).map(first).to_numpy()


def prior_day(bars: pd.DataFrame) -> pd.DataFrame:
    """Per bar: high, low and 14:29 close of the previous trading day's day session. All nan when that session
    has no 14:29 bar (early close) or was on another contract than the current bar."""
    sess = day_session(bars)
    s = bars.loc[sess, ["trading_day", "high_ticks", "low_ticks", "raw_symbol"]]
    days = s.groupby("trading_day", sort=True).agg(high=("high_ticks", "max"), low=("low_ticks", "min"),
                                                   raw=("raw_symbol", "last"))
    last = bars["et_minute"].to_numpy() == SETTLE - 1
    days["close"] = pd.Series(bars["close_ticks"].to_numpy()[last], index=bars["trading_day"].to_numpy()[last])
    days.loc[days["close"].isna(), ["high", "low"]] = np.nan
    day = bars["trading_day"].to_numpy()
    k = np.searchsorted(days.index.to_numpy(), day, side="left") - 1  # last day-session day strictly before
    have = k >= 0
    out = pd.DataFrame({c: np.where(have, days[c].to_numpy()[np.maximum(k, 0)], np.nan if c != "raw" else None)
                        for c in ("high", "low", "close", "raw")})
    same = (out["raw"].to_numpy() == bars["raw_symbol"].to_numpy()) & ~np.isnan(out["close"].to_numpy(float))
    for k in ("high", "low", "close"):
        out[k] = np.where(same, out[k].astype(float), np.nan)
    return out[["high", "low", "close"]]


def five_minute(bars: pd.DataFrame) -> pd.DataFrame:
    """Complete 5-minute bars (all five 1-minute bars present) at their closing 1-minute bar: position `pos`,
    contract `seg`, bucket high / low, close of the closing bar."""
    seg = contract_segments(bars)
    ts = bars["ts_event_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
    bucket = ts // (5 * NS_PER_MIN)
    hi = bars["high_ticks"].groupby([seg, bucket], sort=False).cummax().to_numpy()
    lo = bars["low_ticks"].groupby([seg, bucket], sort=False).cummin().to_numpy()
    count = bars["high_ticks"].groupby([seg, bucket], sort=False).cumcount().to_numpy() + 1
    pos = np.flatnonzero((bars["et_minute"].to_numpy() % 5 == 4) & (count == 5))
    return pd.DataFrame({"pos": pos, "seg": seg[pos], "high": hi[pos].astype(float), "low": lo[pos].astype(float),
                         "close": bars["close_ticks"].to_numpy()[pos].astype(float)})


def by_seg(f: pd.DataFrame, column: str, fn) -> pd.Series:
    return f[column].groupby(f["seg"], sort=False, group_keys=False).transform(fn)


def scatter(n: int, pos: np.ndarray, values, dtype) -> np.ndarray:
    out = np.zeros(n, dtype)
    out[pos] = values
    return out


def flat_at_settle(bars: pd.DataFrame) -> np.ndarray:
    """Close of the first bar at or after 14:29 ET. Causal, unlike `flat_before`, which needs the next bar.
    Early-close days have no such bar: `flat_day_end` closes those at the day's last bar."""
    return first_bar_at(bars, SETTLE - 1)


def _signals(bars, side, **kw) -> Signals:
    side = np.where(entry_window(bars), side, 0).astype(np.int64)
    return Signals(side, flat=flat_at_settle(bars), flat_day_end=True, **kw)


@dataclass
class IntradayMomentum:
    """P101: sign of prior 14:29 close -> 09:29 close, traded 14:00 -> 14:29 (Gao, Han, Li & Zhou 2018)."""
    name: str = "intraday_momentum"

    def signals(self, bars: pd.DataFrame, first_end: int = 9 * 60 + 29, enter: int = 13 * 60 + 59) -> Signals:
        m = bars["et_minute"].to_numpy()
        prev_close = prior_day(bars)["close"].to_numpy()
        c_first = day_value(bars, m == first_end, "close_ticks").astype(float)
        raw_first = day_value(bars, m == first_end, "raw_symbol")
        ok = (m == enter) & (raw_first == bars["raw_symbol"].to_numpy())
        r1 = np.nan_to_num(c_first - prev_close)
        return _signals(bars, np.where(ok, np.sign(r1), 0), max_fill_delay_min=1)


@dataclass
class NoiseArea:
    """P102: Zarattini, Barbon & Aziz (2024) noise-area breakout, checked every 30 min, VWAP/band trailing exit."""
    name: str = "noise_area"

    def signals(self, bars: pd.DataFrame, lookback: int = 14, every: int = 30) -> Signals:
        n = len(bars)
        m = bars["et_minute"].to_numpy()
        day = bars["trading_day"].to_numpy()
        close = bars["close_ticks"].to_numpy().astype(float)
        day_open = day_value(bars, m == OPEN, "open_ticks").astype(float)
        raw_open = day_value(bars, m == OPEN, "raw_symbol")
        prev_close = prior_day(bars)["close"].to_numpy()
        check_bar = (m > OPEN) & (m < LAST_ENTRY) & ((m - OPEN + 1) % every == 0)  # 09:29, 09:59, .. 13:59 closes
        has_move = check_bar & ~np.isnan(day_open) & (raw_open == bars["raw_symbol"].to_numpy())

        # sigma per check minute: mean abs move from the open over the previous `lookback` days with that bar
        move = pd.DataFrame({"m": m[has_move], "move": np.abs(close[has_move] / day_open[has_move] - 1)})
        move["sigma"] = move.groupby("m", sort=False)["move"].transform(
            lambda x: x.shift(1).rolling(lookback, min_periods=lookback).mean())
        sigma = scatter(n, np.flatnonzero(has_move), move["sigma"].to_numpy(), float)
        check = has_move & ~np.isnan(prev_close) & ~np.isnan(sigma) & (sigma > 0)

        upper = np.maximum(day_open, prev_close) * (1 + sigma)
        lower = np.minimum(day_open, prev_close) * (1 - sigma)
        sess = day_session(bars)
        typical = (bars["high_ticks"] + bars["low_ticks"] + bars["close_ticks"]).to_numpy() / 3.0
        vol = np.where(sess, bars["volume"].to_numpy().astype(float), 0.0)
        cum_pv = pd.Series(typical * vol).groupby(day).cumsum().to_numpy()
        cum_v = pd.Series(vol).groupby(day).cumsum().to_numpy()
        vwap = np.divide(cum_pv, cum_v, out=np.full(n, np.nan), where=cum_v > 0)
        long_stop = np.fmax(upper, vwap)  # fmax / fmin ignore a missing VWAP
        short_stop = np.fmin(lower, vwap)
        side = np.where(check & (close > upper), 1, np.where(check & (close < lower), -1, 0))
        return _signals(bars, side, exit_long=check & (close <= long_stop), exit_short=check & (close >= short_stop))


@dataclass
class EiaMomentum:
    """P103: on confirmed EIA release days, the first 30 minutes after the release predict the last half hour
    (Wen, Indriawan, Lien & Xu 2023, USO; here CL): sign(open of release bar -> close 29 bars later), traded
    14:00 -> 14:29."""
    name: str = "eia_momentum"

    def signals(self, bars: pd.DataFrame, window: int = 30, enter: int = 13 * 60 + 59) -> Signals:
        n = len(bars)
        ts = bars["ts_event_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64)
        raw = bars["raw_symbol"].to_numpy()
        day = bars["trading_day"].to_numpy()
        r = np.flatnonzero(bars["eia_release"].to_numpy(bool))  # bar starting at the release minute
        e = np.searchsorted(ts, ts[r] + (window - 1) * NS_PER_MIN)  # its close = release + 30 min
        ok = e < n
        r, e = r[ok], e[ok]
        ok = ((ts[e] == ts[r] + (window - 1) * NS_PER_MIN) & (raw[r] == raw[e])
              & (bars["et_minute"].to_numpy()[e] < enter))
        r, e = r[ok], e[ok]
        move = pd.Series(np.sign(bars["close_ticks"].to_numpy()[e] - bars["open_ticks"].to_numpy()[r]), index=day[r])
        move_raw = pd.Series(raw[r], index=day[r])
        move, move_raw = move[~move.index.duplicated()], move_raw[~move_raw.index.duplicated()]
        at = (bars["et_minute"].to_numpy() == enter) & (pd.Series(day).map(move_raw).to_numpy() == raw)
        side = np.where(at, pd.Series(day).map(move).fillna(0).to_numpy(), 0)
        return _signals(bars, side, max_fill_delay_min=1)


@dataclass
class GapFade:
    """P104: generic gap fade: a >= 20-tick gap between the prior 14:29 close and the 08:59 close is faded at
    09:00; target = the prior close, stop = the same distance beyond the 08:59 close (price levels)."""
    name: str = "gap_fade"

    def signals(self, bars: pd.DataFrame, min_gap: int = 20, at: int = OPEN - 1) -> Signals:
        m = bars["et_minute"].to_numpy()
        close = bars["close_ticks"].to_numpy()
        prev = prior_day(bars)["close"].to_numpy()
        gap = np.nan_to_num(close - prev)
        ok = (m == at) & (np.abs(gap) >= min_gap)
        side = np.where(ok, -np.sign(gap), 0).astype(np.int64)  # 08:59 is outside entry_window
        target = np.where(ok, np.nan_to_num(prev), 0).astype(np.int64)
        stop = np.where(ok, close + gap, 0).astype(np.int64)
        return Signals(side, stop_price=stop, target_price=target, flat=flat_at_settle(bars), flat_day_end=True,
                       max_fill_delay_min=1)


@dataclass
class BollingerReversion:
    """P105: 5m close crosses outside BB(20, 2): fade; exit at the middle band (Bollinger 2001)."""
    name: str = "bollinger"

    def signals(self, bars: pd.DataFrame, length: int = 20, width: float = 2.0) -> Signals:
        f = five_minute(bars)
        mid = by_seg(f, "close", lambda x: x.rolling(length).mean())
        sd = by_seg(f, "close", lambda x: x.rolling(length).std(ddof=0))
        c = f["close"]
        up, lo = mid + width * sd, mid - width * sd
        prev = lambda x: x.groupby(f["seg"], sort=False).shift(1)  # noqa: E731
        long_ = (c < lo) & (prev(c) >= prev(lo))
        short = (c > up) & (prev(c) <= prev(up))
        n, pos = len(bars), f["pos"].to_numpy()
        side = scatter(n, pos, np.where(long_, 1, np.where(short, -1, 0)), np.int64)
        return _signals(bars, side, exit_long=scatter(n, pos, (c >= mid).to_numpy(), bool),
                        exit_short=scatter(n, pos, (c <= mid).to_numpy(), bool))


@dataclass
class Rsi2:
    """P106: Connors & Alvarez (2008) RSI(2) pullback with SMA(200) trend filter, exit on SMA(5) cross (5m)."""
    name: str = "rsi2"

    def signals(self, bars: pd.DataFrame, low: float = 10, high: float = 90, trend: int = 200,
                exit_len: int = 5) -> Signals:
        f = five_minute(bars)
        c = f["close"]
        change = by_seg(f, "close", lambda x: x.diff())
        g = pd.DataFrame({"seg": f["seg"], "up": change.clip(lower=0), "down": (-change).clip(lower=0)})
        wilder = lambda x: x.ewm(alpha=1 / 2, adjust=False, min_periods=2).mean()  # noqa: E731
        avg_up, avg_down = by_seg(g, "up", wilder), by_seg(g, "down", wilder)
        rsi = 100 - 100 / (1 + avg_up / avg_down)
        rsi = rsi.where(avg_down > 0, 100.0).where(change.notna())
        sma_t = by_seg(f, "close", lambda x: x.rolling(trend).mean())
        sma_x = by_seg(f, "close", lambda x: x.rolling(exit_len).mean())
        long_ = (rsi < low) & (c > sma_t) & (c < sma_x)
        short = (rsi > high) & (c < sma_t) & (c > sma_x)
        n, pos = len(bars), f["pos"].to_numpy()
        side = scatter(n, pos, np.where(long_, 1, np.where(short, -1, 0)), np.int64)
        return _signals(bars, side, exit_long=scatter(n, pos, (c > sma_x).to_numpy(), bool),
                        exit_short=scatter(n, pos, (c < sma_x).to_numpy(), bool))


@dataclass
class PivotFade:
    """P107: fade the first touch of floor-trader R1 / S1; target the pivot, stop at R2 / S2 (price levels).
    P rounds half up to a tick; a bar touching both R1 and S1 trades neither."""
    name: str = "pivot"

    def signals(self, bars: pd.DataFrame) -> Signals:
        p = prior_day(bars)
        hi, lo, cl = (p[k].to_numpy() for k in ("high", "low", "close"))
        piv = np.floor((hi + lo + cl) / 3 + 0.5)
        r1, s1 = 2 * piv - lo, 2 * piv - hi
        r2, s2 = piv + (hi - lo), piv - (hi - lo)
        day = bars["trading_day"].to_numpy()
        sess = day_session(bars)
        h = np.where(sess, bars["high_ticks"].to_numpy(), -np.inf)
        l_ = np.where(sess, bars["low_ticks"].to_numpy(), np.inf)
        prev_max = pd.Series(h).groupby(day).cummax().groupby(day).shift(1).fillna(-np.inf).to_numpy()
        prev_min = pd.Series(l_).groupby(day).cummin().groupby(day).shift(1).fillna(np.inf).to_numpy()
        o = bars["open_ticks"].to_numpy()
        short = sess & (prev_max < r1) & (o < r1) & (h >= r1)
        long_ = sess & (prev_min > s1) & (o > s1) & (l_ <= s1)
        side = np.where(short & ~long_, -1, np.where(long_ & ~short, 1, 0))
        target = np.where(side == -1, piv, np.where(side == 1, piv, 0))
        stop = np.where(side == -1, r2, np.where(side == 1, s2, 0))
        valid = (side != 0) & (r1 > piv) & (piv > s1)
        side = np.where(valid, side, 0)
        return _signals(bars, side, stop_price=np.where(valid, stop, 0).astype(np.int64),
                        target_price=np.where(valid, target, 0).astype(np.int64))


@dataclass
class TurtleSoup:
    """P108: intraday adaptation of Raschke & Connors (1995) Turtle Soup on the prior day-session extreme: after
    a trade through it, fade the first 5m close back inside; stop one tick beyond today's extreme (price level)."""
    name: str = "turtle_soup"

    def signals(self, bars: pd.DataFrame) -> Signals:
        n = len(bars)
        p = prior_day(bars)
        day = bars["trading_day"].to_numpy()
        sess = day_session(bars)
        run_low = pd.Series(np.where(sess, bars["low_ticks"].to_numpy(), np.inf)).groupby(day).cummin().to_numpy()
        run_high = pd.Series(np.where(sess, bars["high_ticks"].to_numpy(), -np.inf)).groupby(day).cummax().to_numpy()
        f = five_minute(bars)
        pos = f["pos"].to_numpy()
        c = f["close"].to_numpy()
        win = entry_window(bars)[pos]
        pdl, pdh = p["low"].to_numpy()[pos], p["high"].to_numpy()[pos]
        long_c = win & (run_low[pos] < pdl) & (c > pdl)
        short_c = win & (run_high[pos] > pdh) & (c < pdh)
        d = day[pos]
        first_long = long_c & (pd.Series(long_c).groupby(d).cumsum().to_numpy() == 1)
        first_short = short_c & (pd.Series(short_c).groupby(d).cumsum().to_numpy() == 1)
        side5 = np.where(first_long, 1, np.where(first_short, -1, 0))
        stop5 = np.where(first_long, run_low[pos] - 1, np.where(first_short, run_high[pos] + 1, 0))
        return _signals(bars, scatter(n, pos, side5, np.int64), stop_price=scatter(n, pos, stop5, np.int64))


@dataclass
class DonchianBreakout:
    """P109: intraday adaptation of Turtle System 1 (Faith 2007): 20-bar 5m channel breakout, 10-bar opposite
    channel exit, 2N stop from the fill (N = Wilder 20-bar average true range of 5m bars; the first bar of a
    contract has true range = high - low; 2N rounds half up to ticks)."""
    name: str = "donchian"

    def signals(self, bars: pd.DataFrame, entry_len: int = 20, exit_len: int = 10, n_len: int = 20,
                n_stop: float = 2.0) -> Signals:
        f = five_minute(bars)
        prev_close = f["close"].groupby(f["seg"], sort=False).shift(1)
        f["tr"] = np.fmax(f["high"], prev_close) - np.fmin(f["low"], prev_close)
        atr = by_seg(f, "tr", lambda x: x.ewm(alpha=1 / n_len, adjust=False, min_periods=n_len).mean())
        stop = np.floor(n_stop * atr + 0.5).fillna(0).astype(np.int64).to_numpy()  # half up
        prior = lambda col, k, fn: by_seg(f, col, lambda x: getattr(x.shift(1).rolling(k), fn)())  # noqa: E731
        hh, ll = prior("high", entry_len, "max"), prior("low", entry_len, "min")
        xh, xl = prior("high", exit_len, "max"), prior("low", exit_len, "min")
        c = f["close"]
        n, pos = len(bars), f["pos"].to_numpy()
        side5 = np.where((c > hh) & (stop > 0), 1, np.where((c < ll) & (stop > 0), -1, 0))
        return _signals(bars, scatter(n, pos, side5, np.int64), stop_ticks=scatter(n, pos, stop, np.int64),
                        exit_long=scatter(n, pos, (c < xl).to_numpy(), bool),
                        exit_short=scatter(n, pos, (c > xh).to_numpy(), bool))


@dataclass
class MacdCross:
    """P110: MACD(12, 26, 9) signal-line cross on 5m bars (Appel); always with the latest cross."""
    name: str = "macd"

    def signals(self, bars: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> Signals:
        f = five_minute(bars)
        ema = lambda k: lambda x: x.ewm(span=k, adjust=False).mean()  # noqa: E731
        f["macd"] = by_seg(f, "close", ema(fast)) - by_seg(f, "close", ema(slow))
        sig = by_seg(f, "macd", ema(signal))
        diff = f["macd"] - sig
        prev = diff.groupby(f["seg"], sort=False).shift(1)
        up, down = (diff > 0) & (prev <= 0), (diff < 0) & (prev >= 0)
        n, pos = len(bars), f["pos"].to_numpy()
        side = scatter(n, pos, np.where(up, 1, np.where(down, -1, 0)), np.int64)
        return _signals(bars, side, exit_long=scatter(n, pos, down.to_numpy(), bool),
                        exit_short=scatter(n, pos, up.to_numpy(), bool))


# item id -> (family, strategy); the frozen order of SPEC.md
P10 = {
    "P101": ("im", IntradayMomentum()),
    "P102": ("noise_area", NoiseArea()),
    "P103": ("eia_momentum", EiaMomentum()),
    "P104": ("gap_fade", GapFade()),
    "P105": ("bollinger", BollingerReversion()),
    "P106": ("rsi2", Rsi2()),
    "P107": ("pivot", PivotFade()),
    "P108": ("turtle_soup", TurtleSoup()),
    "P109": ("donchian", DonchianBreakout()),
    "P110": ("macd", MacdCross()),
}
