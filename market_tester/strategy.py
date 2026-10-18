"""Strategy interface: a strategy turns bars into per-bar signals; the engine owns every fill.

A strategy is any object with `name` and `signals(bars, **params) -> Signals`. Signals are read at the
CLOSE of bar t and act at the OPEN of bar t+1, so a signal may use bar t's close but nothing later.
Indicators must not run across a roll: build them with `per_contract`.
Signals must be causal and stateless: no fitting, scaling or thresholds computed over the whole sample
(walk-forward and the look-ahead check assume it; report.evaluate refuses objects with a `fit` method).
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd


@dataclass
class Signals:
    """Per-bar arrays (or scalars, broadcast). Distances are in ticks from the entry fill's open; 0 = none."""
    side: np.ndarray  # +1 long, -1 short, 0 nothing; entry at next bar's open when flat
    stop_ticks: np.ndarray | int = 0
    target_ticks: np.ndarray | int = 0
    hold_minutes: np.ndarray | int = 0  # exit at the first bar open at or after entry + hold
    exit: np.ndarray | None = None  # True at close of bar t: exit at bar t+1 open (with `side` = reversal)
    flat: np.ndarray | None = None  # True: exit at this bar's close (session flat time)
    exit_long: np.ndarray | None = None  # as `exit`, applied only to a long position
    exit_short: np.ndarray | None = None  # as `exit`, applied only to a short position
    stop_price: np.ndarray | int = 0  # absolute stop level in ticks (instead of stop_ticks); 0 = none
    target_price: np.ndarray | int = 0  # absolute target level in ticks (instead of target_ticks); 0 = none
    flat_day_end: bool = False  # also exit at the close of each trading day's last bar (early closes)
    max_fill_delay_min: int | None = None  # stricter stale-signal limit than the engine's, e.g. 1 = exact minute


class Strategy(Protocol):
    name: str

    def signals(self, bars: pd.DataFrame, **params) -> Signals: ...


def contract_segments(bars: pd.DataFrame) -> np.ndarray:
    """0, 1, 2, ... one id per run of the same raw_symbol."""
    raw = bars["raw_symbol"].to_numpy()
    change = np.r_[False, raw[1:] != raw[:-1]]
    return np.cumsum(change)


def per_contract(bars: pd.DataFrame, column: str, fn) -> pd.Series:
    """Apply `fn(series) -> series` to `column` separately inside each contract (resets at every roll)."""
    seg = contract_segments(bars)
    return bars[column].groupby(seg, sort=False, group_keys=False).transform(fn)


def flat_before(bars: pd.DataFrame, et_minute: int) -> np.ndarray:
    """True at the last bar of each trading day that starts before `et_minute` ET (e.g. 16*60+55)."""
    session_minute = (bars["et_minute"].to_numpy().astype(np.int32) - 18 * 60) % 1440  # 0 = 18:00 ET open
    day = bars["trading_day"].to_numpy()
    before = session_minute < (et_minute - 18 * 60) % 1440
    next_not_before = np.r_[~before[1:] | (day[1:] != day[:-1]), True]
    return before & next_not_before


def first_bar_at(bars: pd.DataFrame, et_minute: int) -> np.ndarray:
    """True at the first bar of each trading day at or after `et_minute` ET (daytime minutes, < 17:00)."""
    m = bars["et_minute"].to_numpy()
    day = bars["trading_day"].to_numpy()
    at = (m >= et_minute) & (m < 17 * 60)
    prev_at = np.r_[False, at[:-1] & (day[1:] == day[:-1])]
    return at & ~prev_at
