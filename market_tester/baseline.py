"""Random-entry baseline (LESSONS §6.2): are the strategy's ENTRIES better than random ones?

Each draw permutes the trading days inside every calendar week and moves all of a day's real entries to
its partner day: same ET minute (exact), same side, same stop / target / hold (price levels as their
distance from the real fill), and the strategy's own `exit` / `exit_long` / `exit_short` / `flat` rules. A
bijection, so entries never collide and the count is kept unless the partner day has no bar at that exact
minute. Draws that keep under `min_match` of the real trades are rejected.
With an `eligible` mask (per bar, constant within a day), days are permuted only among eligible days of the
same week, so a strategy's eligibility filter (e.g. expiry, contract checks) also holds for random entries.
If the real net per trade is not above most draws, the "edge" is drift or the exit rule, not the entry.
"""

import numpy as np
import pandas as pd

from .engine import Market, run, with_costs
from .strategy import Signals


def _day_index(market: Market, eligible: np.ndarray | None = None):
    days, first = np.unique(market.trading_day, return_index=True)
    order = np.argsort(first)
    days, first = days[order], first[order]
    end = np.r_[first[1:], len(market)]
    session_minute = (market.et_minute.astype(np.int64) - 18 * 60) % 1440
    week = pd.DatetimeIndex(days).to_period("W").asi8
    if eligible is not None:
        eligible = np.asarray(eligible, bool)
        if len(eligible) != len(market):
            raise ValueError("eligible must have one value per bar")
        day_ok = eligible[first]
        if any(not (eligible[a:b] == ok).all() for a, b, ok in zip(first, end, day_ok)):
            raise ValueError("eligible must be constant within a trading day")
        week = week * 2 + day_ok  # permutation groups: (week, eligible)
    return days, first, end, session_minute, week


def week_permutation(week: np.ndarray, rng) -> np.ndarray:
    """partner[i] = a day of the same week as day i; a random bijection inside each week."""
    by_week = np.lexsort((np.arange(len(week)), week))
    shuffled = np.lexsort((rng.random(len(week)), week))
    partner = np.empty(len(week), np.int64)
    partner[by_week] = shuffled
    return partner


def random_entry_signals(market: Market, trades: pd.DataFrame, sig: Signals, rng, index=None) -> Signals:
    days, first, end, sess_min, week = index or _day_index(market)
    n = len(market)
    stop = np.broadcast_to(np.asarray(sig.stop_ticks), (n,))
    target = np.broadcast_to(np.asarray(sig.target_ticks), (n,))
    # absolute levels mean nothing on another day: move them as their distance from the real entry fill
    stop_px = np.broadcast_to(np.asarray(sig.stop_price), (n,))
    target_px = np.broadcast_to(np.asarray(sig.target_price), (n,))
    hold = np.broadcast_to(np.asarray(sig.hold_minutes), (n,))
    entry = trades["entry_bar"].to_numpy()
    partner = week_permutation(week, rng)[np.searchsorted(days, market.trading_day[entry])]
    side = np.zeros(n, np.int64)
    st, tg, ho = np.zeros(n, np.int64), np.zeros(n, np.int64), np.zeros(n, np.int64)
    for p, e, s in zip(partner, entry, trades["side"].to_numpy()):
        a, b = first[p], end[p]
        fill = a + np.searchsorted(sess_min[a:b], sess_min[e])
        if fill <= a or fill >= b or sess_min[fill] != sess_min[e]:
            continue  # no bar at that exact minute on the partner day
        st_e = abs(stop_px[e - 1] - market.open[e]) if stop_px[e - 1] > 0 else stop[e - 1]
        tg_e = abs(target_px[e - 1] - market.open[e]) if target_px[e - 1] > 0 else target[e - 1]
        side[fill - 1], st[fill - 1], tg[fill - 1], ho[fill - 1] = s, st_e, tg_e, hold[e - 1]
    return Signals(side, st, tg, ho, exit=sig.exit, exit_long=sig.exit_long, exit_short=sig.exit_short,
                   flat=sig.flat, flat_day_end=sig.flat_day_end, max_fill_delay_min=sig.max_fill_delay_min)


def random_entry_test(market: Market, trades: pd.DataFrame, sig: Signals, slip_ticks: float = 1,
                      n_draws: int = 200, seed: int = 0, min_match: float = 0.9, progress: bool = False,
                      eligible: np.ndarray | None = None) -> dict:
    """p = share of accepted draws whose net per trade is >= the real one (+1 smoothing). nan if fewer than
    half the draws keep `min_match` of the real trade count (the null would be a different experiment)."""
    if trades.empty:
        return {"draws": 0, "p": float("nan")}
    rng = np.random.default_rng(seed)
    index = _day_index(market, eligible)
    if eligible is not None and not np.asarray(eligible, bool)[trades["entry_bar"].to_numpy()].all():
        raise ValueError("a real trade entered on an ineligible day")
    real = float(trades["net_usd"].mean())
    means, counts = np.full(n_draws, np.nan), np.zeros(n_draws, np.int64)
    for d in range(n_draws):
        t = with_costs(run(market, random_entry_signals(market, trades, sig, rng, index)), slip_ticks)
        counts[d] = len(t)
        if len(t) >= min_match * len(trades):
            means[d] = t["net_usd"].mean()
        if progress and (d + 1) % max(1, n_draws // 10) == 0:
            print(f"  random entries {d + 1}/{n_draws}", flush=True)
    ok = ~np.isnan(means)
    enough = ok.sum() >= n_draws / 2
    return {"draws": n_draws, "accepted": int(ok.sum()), "real_net_usd_per_trade": real,
            "random_net_usd_per_trade_mean": float(np.nanmean(means)) if ok.any() else float("nan"),
            "random_p5_p95": [float(np.nanquantile(means, q)) for q in (0.05, 0.95)] if ok.any() else [],
            "random_trades_median": int(np.median(counts)), "real_trades": len(trades),
            "match_rate_median": float(np.median(counts) / len(trades)), "eligible_mask": eligible is not None,
            "p": float(((means[ok] >= real).sum() + 1) / (ok.sum() + 1)) if enough else float("nan")}
