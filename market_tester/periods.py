"""Dev and holdout periods, fixed 2026-10-18 before any strategy test (LESSONS §5).

Dev     = trading days 2010-06-07 .. 2023-12-31: build, tune, walk-forward.
Holdout = trading days 2024-01-01 .. 2026-10-13: opened once per pre-registered test (prereg.open_run).
After 2026-10-13 = forward / paper only.
"""

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import prereg
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


def load_period(period: Period, token=None, years: list[int] | None = None, continuous: str = FRONT,
                with_second: bool = False, eia_window: tuple[int, int] | None = None) -> pd.DataFrame:
    """Front-month bars of a period with time columns, `eia_block` and `eia_release` (bar starting at a confirmed
    release). The holdout needs a live RunToken.

    `years` narrows a dev load (e.g. a quick check on [2018, 2019]); it can never reach outside the period.
    Only the canonical DEV and HOLDOUT objects are accepted, so a home-made Period cannot unlock dates.
    The lock guards the tester's own code path; strategy code that reads files itself is not contained.
    """
    if period is not DEV and period is not HOLDOUT:
        raise ValueError("load_period accepts only periods.DEV or periods.HOLDOUT")
    if period.protected and not (prereg.is_live(token) and token.period == period.name):
        raise HoldoutLocked(f"{period.name} needs a live token from prereg.open_run")
    wanted = period.years() if years is None else sorted(set(years) & set(period.years()))
    if not wanted:
        raise ValueError(f"years {years} are outside {period.name}")
    bars = add_time_columns(load_bars(wanted, continuous=continuous))
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
