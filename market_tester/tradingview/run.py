"""Run a TradingView-style strategy over bars: build arrays once, simulate per config, return the trade list.

A strategy object has
    name: str
    script: njit function (see script.py)
    flat_on_new_day: bool
    prepare(bars) -> (feat_i int64 [n, a], feat_f float64 [n, b], info dict)   once per bar set
    params(config: dict) -> int64 array; state_size: int
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..common.config import FEE_PER_SIDE, TICK_VALUE, assert_allowed
from ..common.data import DataIntegrityError, RollCrossed
from . import script as sc
from .broker import (ERR_ROLL, NTRADE, REASONS, T_DIR, T_EBAR, T_EPX, T_ETYPE, T_MAX, T_MIN, T_REASON, T_SLIP,
                     T_SLPX, T_SUBMIT, T_TAG, T_TPPX, T_XBAR, T_XPX, simulate)

INITIAL_CAPITAL = 50_000.0
COMMISSION_PER_ORDER = FEE_PER_SIDE  # $2.01 per contract per filled order (TopstepX CL $4.02 round trip)
ENTRY_TYPES = np.array(["", "stop", "limit", "market"])


@dataclass
class Chart:
    """Bar arrays the broker needs, built once and reused across a grid."""
    bars: pd.DataFrame
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray
    c: np.ndarray
    seg: np.ndarray
    minute: np.ndarray
    day: np.ndarray  # trading-day ordinal (int)
    ts: np.ndarray  # datetime64[ns] UTC
    raw: np.ndarray
    trading_day: np.ndarray  # datetime64[ns]

    @classmethod
    def from_bars(cls, bars: pd.DataFrame) -> "Chart":
        if bars.empty:
            raise DataIntegrityError("no bars")
        for symbol in pd.unique(pd.concat([bars["continuous_symbol"], bars["raw_symbol"]])):
            assert_allowed(symbol)
        if bars["continuous_symbol"].nunique() != 1:
            raise ValueError("one continuous series per chart")
        ts = bars["ts_event_utc"]
        if not ts.is_monotonic_increasing or ts.duplicated().any():
            raise DataIntegrityError("bar times must be strictly increasing")
        o, h, l, c = (bars[f"{k}_ticks"].to_numpy(np.int64) for k in ("open", "high", "low", "close"))
        if ((l > o) | (l > c) | (h < o) | (h < c)).any():
            raise DataIntegrityError("a bar breaks low <= open/close <= high")
        raw = bars["raw_symbol"].to_numpy()
        seg = np.cumsum(np.r_[False, raw[1:] != raw[:-1]]).astype(np.int64)
        day = bars["trading_day"].to_numpy("datetime64[D]").astype(np.int64)
        return cls(bars, o, h, l, c, seg, bars["et_minute"].to_numpy(np.int64),
                   day, ts.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy("datetime64[ns]"), raw,
                   bars["trading_day"].to_numpy("datetime64[ns]"))


def simulate_config(chart: Chart, strategy, feats, config: dict, slip_ticks: int = 0) -> np.ndarray:
    """Raw trade records of one config; `slip_ticks` worsens every market / stop fill inside the broker."""
    feat_i, feat_f = feats[0], feats[1]
    cap = int(len(np.unique(chart.day))) * 2 + 16
    trades = np.zeros((cap, NTRADE), np.int64)
    m = simulate(strategy.script, chart.o, chart.h, chart.l, chart.c, chart.seg, chart.minute, chart.day, feat_i,
                 feat_f, strategy.params(config), np.zeros(strategy.state_size, np.int64), sc.new_book(),
                 sc.new_pos(), trades, strategy.flat_on_new_day, int(slip_ticks))
    if m < 0:
        raise RollCrossed(f"a position reached another contract at bar {ERR_ROLL - m}")
    return trades[:m]


def trade_frame(chart: Chart, t: np.ndarray) -> pd.DataFrame:
    """One row per closed trade with TV-style money columns (fill prices already include any slippage)."""
    d = t[:, T_DIR]
    gross_ticks = d * (t[:, T_XPX] - t[:, T_EPX])
    commission = 2 * COMMISSION_PER_ORDER
    net = gross_ticks * TICK_VALUE - commission
    ei, xi = t[:, T_EBAR], t[:, T_XBAR]
    best = np.where(d > 0, t[:, T_MAX], t[:, T_MIN])
    worst = np.where(d > 0, t[:, T_MIN], t[:, T_MAX])
    raw_in, raw_out = chart.raw[ei], chart.raw[xi]
    if (raw_in != raw_out).any():
        raise RollCrossed("a trade spans two raw_symbols")
    return pd.DataFrame({
        "side": np.where(d > 0, "long", "short"), "dir": d,
        "entry_bar": ei, "exit_bar": xi,
        "entry_time": chart.ts[ei], "exit_time": chart.ts[xi],
        "trading_day": chart.trading_day[ei], "raw_symbol": raw_in,
        "entry_type": ENTRY_TYPES[t[:, T_ETYPE]], "entry_ticks": t[:, T_EPX], "exit_ticks": t[:, T_XPX],
        "exit_reason": REASONS[t[:, T_REASON]],
        "stop_ticks": np.where(t[:, T_SLPX] == sc.NO_PRICE, np.nan, t[:, T_SLPX]),
        "target_ticks": np.where(t[:, T_TPPX] == sc.NO_PRICE, np.nan, t[:, T_TPPX]),
        "submit_time": np.where(t[:, T_SUBMIT] >= 0, chart.ts[np.maximum(t[:, T_SUBMIT], 0)], np.datetime64("NaT", "ns")),
        "exit_minute": chart.minute[xi],
        "gross_ticks": gross_ticks, "slip_fills": t[:, T_SLIP], "commission": commission,
        "net_usd": net, "bars": xi - ei + 1,
        "runup_usd": np.maximum(0, d * (best - t[:, T_EPX])) * TICK_VALUE,
        "drawdown_usd": np.maximum(0, d * (t[:, T_EPX] - worst)) * TICK_VALUE,
        "tag": t[:, T_TAG]})
