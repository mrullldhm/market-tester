"""Dev and holdout periods, fixed 2026-10-18 before any strategy test, and the shared bar loader.

Dev     = trading days 2010-06-07 .. 2023-12-31: build, tune, walk-forward.
Holdout = trading days 2024-01-01 .. 2026-10-13: the quantum tester opens it once per pre-registered test
          (`quantum.periods.load_period`); the TradingView tester reads it only after a `holdout_look` ledger row.
After 2026-10-13 = forward / paper only.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import ledger
from .config import BARS_DIR, DEFINITIONS_FILE, EIA_RELEASES_FILE
from .data import FRONT, SECOND, attach_second, contract_definitions, load_bars
from .sessions import add_time_columns, eia_blackout, eia_day_window, eia_release_bar


class HoldoutLocked(PermissionError):
    pass


@dataclass(frozen=True)
class Period:
    name: str
    first_day: pd.Timestamp  # trading days, inclusive
    last_day: pd.Timestamp
    protected: bool

    def files(self, with_second: bool = False) -> list[Path]:
        """Bar files of the period plus the EIA reference file (and the contract definitions when the strategy
        reads the second month): what a holdout prereg freezes."""
        extra = [DEFINITIONS_FILE] if with_second else []
        return [BARS_DIR / f"year={y}" / "cl.parquet" for y in self.years()] + [EIA_RELEASES_FILE] + extra

    def years(self) -> list[int]:
        """Bar files to read: a trading day starts 18:00 ET the evening before, so the year before too."""
        have = sorted(int(p.parent.name.split("=")[1]) for p in BARS_DIR.glob("year=*/cl.parquet"))
        lo = max(self.first_day.year - 1, have[0]) if have else self.first_day.year - 1
        return list(range(lo, self.last_day.year + 1))


DEV = Period("dev", pd.Timestamp("2010-06-07"), pd.Timestamp("2023-12-31"), protected=False)
HOLDOUT = Period("holdout", pd.Timestamp("2024-01-01"), pd.Timestamp("2026-10-13"), protected=True)


def select_period(bars: pd.DataFrame, period: Period) -> pd.DataFrame:
    """Rows of `bars` (with time columns) inside the period, by trading day."""
    keep = bars["trading_day"].between(period.first_day, period.last_day)
    return bars.loc[keep].reset_index(drop=True)


def read_period(period: Period, years: list[int] | None = None, continuous: str = FRONT, with_second: bool = False,
                eia_window: tuple[int, int] | None = None, extend: bool = False) -> pd.DataFrame:
    """Unguarded read: front-month bars of `period` with time columns, `eia_block` and `eia_release` (bar starting
    at a confirmed release). Callers own the holdout check (`quantum.periods.load_period`, `load_range`)."""
    wanted = period.years() if years is None else sorted(set(years) & set(period.years()))
    if not wanted:
        raise ValueError(f"years {years} are outside {period.name}")
    bars = add_time_columns(load_bars(wanted, continuous=continuous, extend=extend))
    if with_second:  # `data.attach_second`: same-minute CL.v.1 columns, joined before the period cut
        if continuous != FRONT:
            raise ValueError("with_second attaches CL.v.1 to the front series only")
        bars = attach_second(bars, add_time_columns(load_bars(wanted, continuous=SECOND)), contract_definitions())
    bars = select_period(bars, period)
    ts = bars["ts_event_utc"]
    bars = bars.assign(eia_block=eia_blackout(ts), eia_release=eia_release_bar(ts))
    if eia_window is not None:  # `eia_during_hold`: a listed release that day at ET minutes [lo, hi]
        bars["eia_during_hold"] = eia_day_window(bars["trading_day"], *eia_window)
    return bars


def load_range(first_day, last_day, holdout_look: str | None = None, extend: bool = False) -> pd.DataFrame:
    """Front-month bars for trading days [first_day, last_day] (any range, e.g. full history). A range that
    reaches the holdout needs `holdout_look` = the run_id of an existing `holdout_look` ledger row."""
    first_day, last_day = pd.Timestamp(first_day), pd.Timestamp(last_day)
    if last_day < first_day:
        raise ValueError("last_day is before first_day")
    if last_day >= HOLDOUT.first_day and first_day <= HOLDOUT.last_day:
        rows = ledger.read()
        ok = holdout_look and len(rows) and ((rows["run_id"] == holdout_look) & (rows["kind"] == "holdout_look")).any()
        if not ok:
            raise HoldoutLocked("range reaches the holdout: log a holdout_look ledger row and pass its run_id")
    return read_period(Period("range", first_day, last_day, protected=False), extend=extend)
