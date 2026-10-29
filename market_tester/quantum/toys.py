"""Toy strategies used to prove the tester (not research ideas)."""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .strategy import Signals, first_bar_at


@dataclass
class TimeOfDay:
    """Signal at the first bar at or after `minute` ET each day; fixed side, hold, optional stop / target."""
    name: str = "time_of_day"

    def signals(self, bars: pd.DataFrame, minute: int = 600, side: int = 1, hold: int = 60, stop: int = 0,
                target: int = 0) -> Signals:
        return Signals(side * first_bar_at(bars, minute).astype(np.int64), stop_ticks=stop, target_ticks=target,
                       hold_minutes=hold)


@dataclass
class CoinFlip:
    """Random daytime (09:00-14:00 ET) signals with random side; zero edge by construction."""
    name: str = "coin_flip"

    def signals(self, bars: pd.DataFrame, rate: float = 0.002, hold: int = 60, seed: int = 0) -> Signals:
        u = np.random.default_rng(seed).random((len(bars), 2))  # row k depends on k only: truncation-safe
        m = bars["et_minute"].to_numpy()
        day = (m >= 9 * 60) & (m < 14 * 60)
        fire = day & (u[:, 0] < rate)
        side = np.where(u[:, 1] < 0.5, 1, -1) * fire
        return Signals(side.astype(np.int64), hold_minutes=hold)


def morning_move_sign(bars: pd.DataFrame, minute: int, anchor: int) -> tuple[np.ndarray, np.ndarray]:
    """At the first bar at or after `minute` ET each day: sign(close - open of the first bar at `anchor`).
    Returns (signal-bar mask, sign per bar; 0 elsewhere). Uses only bars up to the signal bar's close."""
    at = first_bar_at(bars, minute)
    start = first_bar_at(bars, anchor)
    day = bars["trading_day"].to_numpy()
    anchor_open = pd.Series(bars["open_ticks"].to_numpy()[start], index=day[start])
    anchor_open = anchor_open[~anchor_open.index.duplicated()]
    ref = pd.Series(day).map(anchor_open).to_numpy(dtype=float)
    move = bars["close_ticks"].to_numpy() - ref
    sign = np.where(at & ~np.isnan(ref), np.sign(np.nan_to_num(move)), 0).astype(np.int64)
    return at, sign


@dataclass
class MorningMomentum:
    """At `minute` ET, trade in the direction of the move since `anchor` ET; hold `hold` minutes."""
    name: str = "morning_momentum"

    def signals(self, bars: pd.DataFrame, minute: int = 9 * 60 + 58, anchor: int = 9 * 60, hold: int = 60,
                stop: int = 0, target: int = 0) -> Signals:
        _, sign = morning_move_sign(bars, minute, anchor)
        return Signals(sign, stop_ticks=stop, target_ticks=target, hold_minutes=hold)
