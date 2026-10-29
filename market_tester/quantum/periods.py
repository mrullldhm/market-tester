"""Quantum tester's period access: dev freely, the holdout only with a live pre-registration token."""

import pandas as pd

from ..common.data import FRONT
from ..common.periods import DEV, HOLDOUT, HoldoutLocked, Period, read_period, select_period  # noqa: F401
from . import prereg


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
    return read_period(period, years, continuous, with_second, eia_window)
