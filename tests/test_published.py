"""P10 published strategies: causality, session rules, roll handling, hand-checked signals."""

import numpy as np
import pandas as pd
import pytest

from market_tester.engine import Market, run
from market_tester.published import (P10, BollingerReversion, DonchianBreakout, EiaMomentum, GapFade,
                                     IntradayMomentum, MacdCross, NoiseArea, PivotFade, Rsi2, TurtleSoup,
                                     five_minute, prior_day)
from market_tester.report import lookahead_check

from conftest import synthetic_bars


@pytest.fixture(scope="module")
def bars():
    b = synthetic_bars(n_days=120, seed=3, roll_every=40, sd_ticks=4.0)
    b["volume"] = np.random.default_rng(1).integers(1, 50, len(b))
    wed = (b["ts_et"].dt.dayofweek == 2).to_numpy()
    m = b["et_minute"].to_numpy()
    b["eia_block"] = wed & (m >= 10 * 60 + 15) & (m <= 10 * 60 + 45)  # release 10:30 ET
    b["eia_release"] = wed & (m == 10 * 60 + 30)
    # every 17th day closes early at 13:00; 0.3% of other minutes are missing (not the exact-time rule minutes)
    day_no = pd.factorize(b["trading_day"])[0]
    early = (day_no % 17 == 16) & (m >= 13 * 60)
    keep_minutes = np.isin(m, [8 * 60 + 59, 9 * 60, 9 * 60 + 29, 13 * 60 + 59, 14 * 60, 14 * 60 + 29]) | \
        ((m >= 10 * 60 + 30) & (m <= 11 * 60))
    gone = (np.random.default_rng(2).random(len(b)) < 0.003) & ~keep_minutes
    return b[~early & ~gone].reset_index(drop=True)


@pytest.mark.parametrize("item", sorted(P10))
def test_causal_and_inside_session(bars, item):
    _, strategy = P10[item]
    sig = strategy.signals(bars)
    look = lookahead_check(strategy, bars, {}, n_cuts=40, full=sig)
    assert look["ok"], look
    trades = run(Market.from_bars(bars), sig)
    assert len(trades) > 0
    m_in = trades["et_minute"].to_numpy()
    lo = 9 * 60
    assert ((m_in >= lo) & (m_in <= 14 * 60)).all()
    m_out = pd.DatetimeIndex(trades["exit_time"]).tz_convert("America/New_York")
    assert ((m_out.hour * 60 + m_out.minute) <= 14 * 60 + 29).all()
    assert trades["trading_day"].eq(pd.DatetimeIndex(m_out.tz_localize(None).normalize())).all()


def test_prior_day_dropped_across_roll(bars):
    p = prior_day(bars)
    day = bars["trading_day"].to_numpy()
    raw = bars["raw_symbol"].to_numpy()
    roll_days = np.unique(day[np.r_[False, raw[1:] != raw[:-1]]])
    on_roll_day = np.isin(day, roll_days)
    assert p.loc[on_roll_day, "close"].isna().all()
    assert p.loc[~on_roll_day & (bars["et_minute"].to_numpy() >= 9 * 60), "close"].notna().mean() > 0.9
    # value = the previous day's 14:29 close
    d1 = sorted(set(day) - set(roll_days))[5]
    prev = sorted(set(day))[sorted(set(day)).index(d1) - 1]
    want = bars.loc[(day == prev) & (bars["et_minute"] == 14 * 60 + 29), "close_ticks"].item()
    assert p.loc[day == d1, "close"].iloc[0] == want


def test_five_minute_bars(bars):
    f = five_minute(bars)
    assert (bars["et_minute"].to_numpy()[f["pos"]] % 5 == 4).all()
    k = 20  # a bucket fully inside one contract
    p = f["pos"].iloc[k]
    assert f["high"].iloc[k] == bars["high_ticks"].iloc[p - 4: p + 1].max()
    assert f["low"].iloc[k] == bars["low_ticks"].iloc[p - 4: p + 1].min()


def _one_day_hand_check(bars, strategy, minute):
    sig = strategy.signals(bars)
    m = bars["et_minute"].to_numpy()
    return sig, m == minute


def test_intraday_momentum_sign(bars):
    sig, at = _one_day_hand_check(bars, IntradayMomentum(), 13 * 60 + 59)
    day = bars["trading_day"].to_numpy()
    p = prior_day(bars)["close"].to_numpy()
    c929 = bars["close_ticks"].where(bars["et_minute"] == 9 * 60 + 29)
    first = pd.Series(c929.to_numpy()).groupby(day).transform("max").to_numpy()
    want = np.where(np.isnan(p), 0, np.sign(np.nan_to_num(first - p)))
    assert np.array_equal(sig.side[at], want[at].astype(np.int64))
    assert (sig.side[~at] == 0).all()


def test_gap_fade_levels(bars):
    sig = GapFade().signals(bars)
    i = np.flatnonzero(sig.side)
    assert len(i) and (bars["et_minute"].to_numpy()[i] == 8 * 60 + 59).all()
    prev = prior_day(bars)["close"].to_numpy()[i]
    close = bars["close_ticks"].to_numpy()[i]
    gap = close - prev
    assert (np.abs(gap) >= 20).all() and (sig.side[i] == -np.sign(gap)).all()
    assert (sig.target_price[i] == prev).all() and (sig.stop_price[i] == close + gap).all()


def test_eia_momentum_direction_and_timing(bars):
    sig = EiaMomentum().signals(bars)
    i = np.flatnonzero(sig.side)
    m = bars["et_minute"].to_numpy()
    assert len(i) and (m[i] == 13 * 60 + 59).all()
    r = np.flatnonzero(bars["eia_release"].to_numpy())
    close, open_ = bars["close_ticks"].to_numpy(), bars["open_ticks"].to_numpy()
    want = {bars["trading_day"].iloc[k]: np.sign(close[k + 29] - open_[k]) for k in r}
    for k in i:
        assert sig.side[k] == want[bars["trading_day"].iloc[k]]
    has_1359 = set(bars["trading_day"][m == 13 * 60 + 59])
    assert len(i) == sum(v != 0 and d in has_1359 for d, v in want.items())


def test_pivot_first_touch_once_per_side(bars):
    sig = PivotFade().signals(bars)
    day = bars["trading_day"].to_numpy()
    s = pd.DataFrame({"day": day, "side": sig.side})
    per_day = s[s["side"] != 0].groupby(["day", "side"]).size()
    assert len(per_day) and (per_day == 1).all()
    i = np.flatnonzero(sig.side)
    assert (sig.stop_price[i] > 0).all() and (sig.target_price[i] > 0).all()


def five(bars):
    """5m closes / highs / lows per contract as plain lists, for loop-based re-derivations."""
    f = five_minute(bars)
    return f, {g: d for g, d in f.groupby("seg")}


def test_five_minute_needs_complete_buckets(bars):
    f = five_minute(bars)
    ts = bars["ts_event_utc"]
    for p in f["pos"].to_numpy()[::50]:
        window = ts.iloc[p - 4: p + 1]
        assert (window.diff().iloc[1:] == pd.Timedelta(minutes=1)).all()


def test_early_close_is_flat_same_day(bars):
    for item in ("P105", "P109", "P110"):
        trades = run(Market.from_bars(bars), P10[item][1].signals(bars))
        exit_day = pd.Series(bars["trading_day"].to_numpy()[trades["exit_bar"]])
        assert (exit_day.to_numpy() == trades["trading_day"].to_numpy()).all()


def _sides(sig, f):
    return sig.side[f["pos"].to_numpy()]


def in_window(bars, pos):
    m = bars["et_minute"].to_numpy()[pos]
    return (m >= 9 * 60) & (m < 14 * 60)


def test_bollinger_by_hand(bars):
    f, segs = five(bars)
    sig = BollingerReversion().signals(bars)
    for _, d in segs.items():
        c = d["close"].to_numpy()
        for j in range(21, len(d)):
            band = lambda k: (c[k - 19: k + 1].mean(), c[k - 19: k + 1].std())  # noqa: E731
            (mid, sd), (pmid, psd) = band(j), band(j - 1)
            want = 1 if (c[j] < mid - 2 * sd and c[j - 1] >= pmid - 2 * psd) else \
                -1 if (c[j] > mid + 2 * sd and c[j - 1] <= pmid + 2 * psd) else 0
            pos = d["pos"].iloc[j]
            assert sig.side[pos] == (want if in_window(bars, pos) else 0)
            assert sig.exit_long[pos] == (c[j] >= mid) and sig.exit_short[pos] == (c[j] <= mid)


def test_rsi2_by_hand(bars):
    f, segs = five(bars)
    sig = Rsi2().signals(bars)
    for _, d in segs.items():
        c = d["close"].to_numpy()
        up = down = None
        for j in range(1, len(d)):
            ch = c[j] - c[j - 1]
            g, lo = max(ch, 0), max(-ch, 0)
            up, down = (g, lo) if up is None else (up + (g - up) / 2, down + (lo - down) / 2)
            if j < 200:
                continue
            rsi = 100.0 if down == 0 else 100 - 100 / (1 + up / down)
            sma200, sma5 = c[j - 199: j + 1].mean(), c[j - 4: j + 1].mean()
            want = 1 if (rsi < 10 and c[j] > sma200 and c[j] < sma5) else \
                -1 if (rsi > 90 and c[j] < sma200 and c[j] > sma5) else 0
            pos = d["pos"].iloc[j]
            assert sig.side[pos] == (want if in_window(bars, pos) else 0), (j, rsi)


def test_donchian_by_hand(bars):
    f, segs = five(bars)
    sig = DonchianBreakout().signals(bars)
    for _, d in segs.items():
        c, h, lo = (d[k].to_numpy() for k in ("close", "high", "low"))
        atr = h[0] - lo[0]  # first bar of a contract: true range = high - low
        for j in range(1, len(d)):
            tr = max(h[j], c[j - 1]) - min(lo[j], c[j - 1])
            atr = atr + (tr - atr) / 20
            if j < 20:
                continue
            stop = int(np.floor(2 * atr + 0.5))
            want = 1 if c[j] > h[j - 20: j].max() else -1 if c[j] < lo[j - 20: j].min() else 0
            pos = d["pos"].iloc[j]
            assert sig.side[pos] == (want if in_window(bars, pos) and stop > 0 else 0)
            if sig.side[pos]:
                assert sig.stop_ticks[pos] == stop
            assert sig.exit_long[pos] == (c[j] < lo[j - 10: j].min())
            assert sig.exit_short[pos] == (c[j] > h[j - 10: j].max())


def test_macd_by_hand(bars):
    f, segs = five(bars)
    sig = MacdCross().signals(bars)
    for _, d in segs.items():
        c = d["close"].to_numpy()
        e12 = e26 = s9 = None
        prev = None
        for j in range(len(d)):
            e12 = c[j] if e12 is None else e12 + (c[j] - e12) * 2 / 13
            e26 = c[j] if e26 is None else e26 + (c[j] - e26) * 2 / 27
            macd = e12 - e26
            s9 = macd if s9 is None else s9 + (macd - s9) * 2 / 10
            diff = macd - s9
            pos = d["pos"].iloc[j]
            up = prev is not None and diff > 0 and prev <= 0
            down = prev is not None and diff < 0 and prev >= 0
            if abs(diff) > 1e-9 and (prev is None or abs(prev) > 1e-9):  # skip float ties at zero
                assert sig.side[pos] == ((1 if up else -1 if down else 0) if in_window(bars, pos) else 0)
                assert sig.exit_long[pos] == down and sig.exit_short[pos] == up
            prev = diff


def test_turtle_soup_by_hand(bars):
    sig = TurtleSoup().signals(bars)
    p = prior_day(bars)
    f = five_minute(bars)
    day = bars["trading_day"].to_numpy()
    m = bars["et_minute"].to_numpy()
    seen = set()
    for pos, c in zip(f["pos"].to_numpy(), f["close"].to_numpy(), strict=True):
        if not in_window(bars, pos):
            assert sig.side[pos] == 0
            continue
        today = (day == day[pos]) & (m >= 9 * 60) & (np.arange(len(bars)) <= pos)
        low, high = bars["low_ticks"].to_numpy()[today].min(), bars["high_ticks"].to_numpy()[today].max()
        pdl, pdh = p["low"].iloc[pos], p["high"].iloc[pos]
        want = 0
        if low < pdl and c > pdl and (day[pos], 1) not in seen:
            want = 1
            seen.add((day[pos], 1))
            assert sig.stop_price[pos] == low - 1
        elif high > pdh and c < pdh and (day[pos], -1) not in seen:
            want = -1
            seen.add((day[pos], -1))
            assert sig.stop_price[pos] == high + 1
        assert sig.side[pos] == want


def test_noise_area_sigma_by_hand(bars):
    sig = NoiseArea().signals(bars)
    m = bars["et_minute"].to_numpy()
    day = bars["trading_day"].to_numpy()
    close = bars["close_ticks"].to_numpy().astype(float)
    raw = bars["raw_symbol"].to_numpy()
    opens = {d: (o, r) for d, o, r in zip(day[m == 540], bars["open_ticks"].to_numpy()[m == 540], raw[m == 540],
                                          strict=True)}
    i = np.flatnonzero(sig.side)
    assert len(i)
    prev = prior_day(bars)["close"].to_numpy()
    for k in i[:40]:
        hist = [abs(close[j] / opens[day[j]][0] - 1) for j in np.flatnonzero((m == m[k]) & (day < day[k]))
                if day[j] in opens and opens[day[j]][1] == raw[j]][-14:]
        assert len(hist) == 14
        sigma = np.mean(hist)
        o = opens[day[k]][0]
        upper, lower = max(o, prev[k]) * (1 + sigma), min(o, prev[k]) * (1 - sigma)
        assert sig.side[k] == (1 if close[k] > upper else -1 if close[k] < lower else 0)
