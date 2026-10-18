"""Generic 1-lot bar simulator for CL (numba). Fill rules are LESSONS §2 and CLAUDE.md:

- signal at bar t close, market entry at bar t+1 open; no entry if bar t+1 is a new contract, an EIA
  blackout bar, the last bar of a contract, or more than `max_fill_delay_min` after bar t (stale signal);
- stop / target are a distance from the entry fill or an absolute price level fixed at the signal; an entry
  whose open has already reached a price level is skipped;
- stop = market order: gap through it fills at the open, else at the stop price; slipped;
- target = limit order: needs a 1-tick trade-through, fills at the target price, never slipped;
- stop and target inside the same bar = stop;
- position is closed at the close of the last bar of each contract (a trade never spans a roll);
- every market fill (entry, stop, time, signal, flat, roll) pays `slip` ticks; fees on both sides.

Prices are integer ticks. The path never depends on slippage, so costs are applied afterwards
(`with_costs`) and a slippage stress test needs no re-run.
"""

from dataclasses import dataclass

import numba as nb
import numpy as np
import pandas as pd

from .config import FEE_PER_SIDE, SLIPPAGE_TICKS_PER_SIDE, TICK_VALUE, assert_allowed
from .data import DataIntegrityError, RollCrossed
from .strategy import Signals, contract_segments

REASONS = np.array(["stop", "target", "time", "signal", "flat", "roll", "end"])
STOP, TARGET, TIME, SIGNAL, FLAT, ROLL, END = range(7)
NS_PER_MIN = 60_000_000_000


@dataclass
class Market:
    """Bar arrays the engine needs, built once and reused across a parameter grid."""
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    contract: np.ndarray
    last_bar: np.ndarray
    entry_ok: np.ndarray
    raw_symbol: np.ndarray
    trading_day: np.ndarray
    et_minute: np.ndarray

    @classmethod
    def from_bars(cls, bars: pd.DataFrame) -> "Market":
        validate_bars(bars)
        contract = contract_segments(bars).astype(np.int64)
        last_bar = np.r_[contract[1:] != contract[:-1], True]
        block = bars["eia_block"].to_numpy(bool) if "eia_block" in bars else np.zeros(len(bars), bool)
        return cls(
            ts=bars["ts_event_utc"].to_numpy(dtype="datetime64[ns]").astype(np.int64),
            open=bars["open_ticks"].to_numpy(np.int64), high=bars["high_ticks"].to_numpy(np.int64),
            low=bars["low_ticks"].to_numpy(np.int64), close=bars["close_ticks"].to_numpy(np.int64),
            contract=contract, last_bar=last_bar, entry_ok=~block & ~last_bar,
            raw_symbol=bars["raw_symbol"].to_numpy(), trading_day=bars["trading_day"].to_numpy(),
            et_minute=bars["et_minute"].to_numpy())

    def __len__(self):
        return len(self.ts)


def validate_bars(bars: pd.DataFrame) -> None:
    """CL only, one series, strictly increasing minutes, integer ticks, low <= open/close <= high."""
    if bars.empty:
        raise DataIntegrityError("no bars")
    if bars["continuous_symbol"].nunique() != 1 or bars[["continuous_symbol", "raw_symbol"]].isna().any().any():
        raise ValueError("run the engine on one continuous series with no null symbols")
    for symbol in pd.unique(pd.concat([bars["continuous_symbol"], bars["raw_symbol"]])):
        assert_allowed(symbol)
    if not bars["ts_event_utc"].is_monotonic_increasing or bars["ts_event_utc"].duplicated().any():
        raise DataIntegrityError("bar times must be strictly increasing")
    o, h, l, c = (bars[f"{k}_ticks"] for k in ("open", "high", "low", "close"))
    if not all(pd.api.types.is_integer_dtype(x) for x in (o, h, l, c)):
        raise DataIntegrityError("prices must be integer ticks")
    if ((l > o) | (l > c) | (h < o) | (h < c)).any():
        raise DataIntegrityError("a bar breaks low <= open/close <= high")


def _arr(x, n, dtype):
    a = np.broadcast_to(np.asarray(0 if x is None else x), (n,)).astype(dtype)
    return np.ascontiguousarray(a)


@nb.njit(cache=True, nogil=True)
def _simulate(ts, o, h, l, c, contract, last_bar, entry_ok, side, stop, target, stop_price, target_price, hold_ns,
              exit_sig, exit_long, exit_short, flat, max_delay_ns, out_i, out_j, out_side, out_in, out_out, out_slip,
              out_reason):
    n = len(ts)
    m = 0
    pos = 0
    pos_contract = -1
    ref_in = 0
    stop_px = 0
    tgt_px = 0
    has_stop = False
    has_tgt = False
    deadline = 0
    pending_exit = False
    for k in range(n):
        reason = -1
        exit_px = 0
        slipped = 0
        # 1. at the open: orders working since the previous close
        if pos != 0:
            if contract[k] != pos_contract:
                return -1 - k  # a held position reached another contract: engine bug
            if pending_exit:
                exit_px, reason, slipped = o[k], SIGNAL, 1
            elif deadline > 0 and ts[k] >= deadline:
                exit_px, reason, slipped = o[k], TIME, 1
            elif has_stop and ((pos > 0 and o[k] <= stop_px) or (pos < 0 and o[k] >= stop_px)):
                exit_px, reason, slipped = o[k], STOP, 1
            elif has_tgt and ((pos > 0 and o[k] > tgt_px) or (pos < 0 and o[k] < tgt_px)):
                exit_px, reason, slipped = tgt_px, TARGET, 0
            if reason >= 0:
                out_j[m], out_out[m], out_reason[m] = k, exit_px, reason
                out_slip[m] += slipped
                m += 1
                pos = 0
                pending_exit = False
        # 2. entry at the open
        if pos == 0 and k > 0 and side[k - 1] != 0 and contract[k] == contract[k - 1] and entry_ok[k] \
                and ts[k] - ts[k - 1] <= max_delay_ns:
            p = side[k - 1]
            s_px = stop_price[k - 1] if stop_price[k - 1] > 0 else o[k] - p * stop[k - 1]
            t_px = target_price[k - 1] if target_price[k - 1] > 0 else o[k] + p * target[k - 1]
            s_on = stop[k - 1] > 0 or stop_price[k - 1] > 0
            t_on = target[k - 1] > 0 or target_price[k - 1] > 0
            # a price level the open has already reached voids the entry
            if (not s_on or p * (o[k] - s_px) > 0) and (not t_on or p * (t_px - o[k]) > 0):
                pos = p
                pos_contract = contract[k]
                ref_in = o[k]
                has_stop, has_tgt = s_on, t_on
                stop_px, tgt_px = s_px, t_px
                deadline = ts[k] + hold_ns[k - 1] if hold_ns[k - 1] > 0 else 0
                out_i[m], out_side[m], out_in[m], out_slip[m] = k, pos, ref_in, 1
        # 3. inside the bar: stop first when both are touched
        reason = -1
        if pos != 0:
            hit_stop = has_stop and ((pos > 0 and l[k] <= stop_px) or (pos < 0 and h[k] >= stop_px))
            hit_tgt = has_tgt and ((pos > 0 and h[k] >= tgt_px + 1) or (pos < 0 and l[k] <= tgt_px - 1))
            if hit_stop:
                exit_px, reason, slipped = stop_px, STOP, 1
            elif hit_tgt:
                exit_px, reason, slipped = tgt_px, TARGET, 0
            # 4. at the close
            elif last_bar[k]:
                exit_px, reason, slipped = c[k], ROLL if k < n - 1 else END, 1
            elif flat[k]:
                exit_px, reason, slipped = c[k], FLAT, 1
            if reason >= 0:
                out_j[m], out_out[m], out_reason[m] = k, exit_px, reason
                out_slip[m] += slipped
                m += 1
                pos = 0
                pending_exit = False
            elif exit_sig[k] or (pos > 0 and exit_long[k]) or (pos < 0 and exit_short[k]):
                pending_exit = True
    return m


def run(market: Market, sig: Signals, max_fill_delay_min: int = 5) -> pd.DataFrame:
    """Trades of one configuration (gross, in ticks). Apply costs with `with_costs`.
    The day-end flat uses the session calendar (like the roll close): known before the day, not a peek."""
    n = len(market)
    if sig.max_fill_delay_min is not None:
        if sig.max_fill_delay_min < 1:
            raise ValueError("max_fill_delay_min must be >= 1")
        max_fill_delay_min = min(max_fill_delay_min, sig.max_fill_delay_min)
    side = _arr(sig.side, n, np.int64)
    if not np.isin(side, (-1, 0, 1)).all():
        raise ValueError("side must be -1, 0 or +1")
    stop = _arr(sig.stop_ticks, n, np.int64)
    target = _arr(sig.target_ticks, n, np.int64)
    stop_price = _arr(sig.stop_price, n, np.int64)
    target_price = _arr(sig.target_price, n, np.int64)
    hold = _arr(sig.hold_minutes, n, np.int64) * NS_PER_MIN
    if (stop < 0).any() or (target < 0).any() or (hold < 0).any() or (stop_price < 0).any() or (target_price < 0).any():
        raise ValueError("stop, target, their prices and hold must be >= 0 (0 = none)")
    if ((stop > 0) & (stop_price > 0)).any() or ((target > 0) & (target_price > 0)).any():
        raise ValueError("give a stop / target as a distance or as a price, not both")
    exit_sig = _arr(sig.exit, n, np.bool_)
    exit_long = _arr(sig.exit_long, n, np.bool_)
    exit_short = _arr(sig.exit_short, n, np.bool_)
    flat = _arr(sig.flat, n, np.bool_)
    if sig.flat_day_end:
        day = market.trading_day
        flat = flat | np.r_[day[1:] != day[:-1], True]
    cap = int(np.count_nonzero(side)) + 1
    out = [np.zeros(cap, np.int64) for _ in range(7)]
    m = _simulate(market.ts, market.open, market.high, market.low, market.close, market.contract,
                  market.last_bar, market.entry_ok, side, stop, target, stop_price, target_price, hold, exit_sig,
                  exit_long, exit_short, flat,
                  np.int64(max_fill_delay_min) * NS_PER_MIN, *out)
    if m < 0:
        raise RollCrossed(f"engine held a position into another contract at bar {-m - 1}")
    i, j, s, p_in, p_out, slip, reason = (a[:m] for a in out)
    raw_in, raw_out = market.raw_symbol[i], market.raw_symbol[j]
    if (raw_in != raw_out).any() or (market.contract[i] != market.contract[j]).any():
        raise RollCrossed("a trade spans two raw_symbols")
    return pd.DataFrame({
        "entry_bar": i, "exit_bar": j,
        "entry_time": pd.to_datetime(market.ts[i], utc=True), "exit_time": pd.to_datetime(market.ts[j], utc=True),
        "trading_day": market.trading_day[i], "et_minute": market.et_minute[i], "raw_symbol": raw_in,
        "side": s, "entry_ticks": p_in, "exit_ticks": p_out, "gross_ticks": s * (p_out - p_in),
        "slip_sides": slip, "reason": REASONS[reason]})


def with_costs(trades: pd.DataFrame, slip_ticks: float = SLIPPAGE_TICKS_PER_SIDE,
               fee_per_side: float = FEE_PER_SIDE) -> pd.DataFrame:
    """Adds `net_ticks` and `net_usd`: gross - slippage on every market fill - fees on both sides."""
    net_ticks = trades["gross_ticks"] - trades["slip_sides"] * slip_ticks
    return trades.assign(net_ticks=net_ticks, net_usd=net_ticks * TICK_VALUE - 2 * fee_per_side)


def backtest(bars_or_market, strategy, params: dict, slip_ticks: float = SLIPPAGE_TICKS_PER_SIDE,
             bars: pd.DataFrame | None = None) -> pd.DataFrame:
    """Signals + simulation + costs for one configuration."""
    if isinstance(bars_or_market, Market):
        market, frame = bars_or_market, bars
        if frame is None:
            raise ValueError("pass bars= with a prebuilt Market (the strategy needs the DataFrame)")
    else:
        frame, market = bars_or_market, Market.from_bars(bars_or_market)
    return with_costs(run(market, strategy.signals(frame, **params)), slip_ticks)
