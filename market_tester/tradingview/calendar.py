"""CL session calendar for the TradingView tester: scheduled shortened sessions, frozen rules known in advance.

Shortened (no normal afternoon): MLK, Presidents, Memorial, Juneteenth (CME from 2022), Independence (observed),
Labor, Thanksgiving days, the day after Thanksgiving and Dec 24 on weekdays. Christmas and New Year's Day are
full closures (no bars) and need no rule.
"""

import pandas as pd
from pandas.tseries.holiday import (AbstractHolidayCalendar, Holiday, USLaborDay, USMartinLutherKingJr,
                                    USMemorialDay, USPresidentsDay, USThanksgivingDay, nearest_workday)


class _ShortHolidays(AbstractHolidayCalendar):
    rules = [USMartinLutherKingJr, USPresidentsDay, USMemorialDay,
             Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
             Holiday("Independence", month=7, day=4, observance=nearest_workday), USLaborDay, USThanksgivingDay]


def short_sessions(first, last) -> pd.DatetimeIndex:
    """Trading days in [first, last] with a scheduled shortened CL session."""
    first, last = pd.Timestamp(first), pd.Timestamp(last)
    hol = _ShortHolidays().holidays(first - pd.Timedelta(days=7), last + pd.Timedelta(days=7))
    thanks = USThanksgivingDay.dates(first - pd.Timedelta(days=366), last + pd.Timedelta(days=366))
    eve = pd.DatetimeIndex([pd.Timestamp(y, 12, 24) for y in range(first.year, last.year + 1)])
    out = hol.union(thanks + pd.Timedelta(days=1)).union(eve[eve.dayofweek < 5])
    return out[(out >= first) & (out <= last)]
