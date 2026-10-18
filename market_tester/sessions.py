"""Exchange time: ET clock, CME trading day, EIA release blackout.

Trading day: a bar at or after 18:00 ET belongs to the next calendar day (Globex reopens 18:00 ET), so
Sunday 18:00 ET opens Monday. Bars carry their start time (`ts_event_utc` = minute start).
"""

import numpy as np
import pandas as pd

from .config import EIA_RELEASES_FILE

ET = "America/New_York"
SESSION_START_HOUR = 18
EIA_BLACKOUT_MINUTES = 15


def add_time_columns(bars: pd.DataFrame) -> pd.DataFrame:
    """Adds `ts_et`, `et_minute` (minutes after ET midnight) and `trading_day` (tz-naive midnight)."""
    ts_et = bars["ts_event_utc"].dt.tz_convert(ET)
    midnight = ts_et.dt.tz_localize(None).dt.normalize()
    et_minute = (ts_et.dt.hour * 60 + ts_et.dt.minute).astype(np.int16)
    trading_day = midnight + pd.to_timedelta((ts_et.dt.hour >= SESSION_START_HOUR).astype(int), unit="D")
    return bars.assign(ts_et=ts_et, et_minute=et_minute, trading_day=trading_day)


def eia_release_times(path=EIA_RELEASES_FILE) -> pd.DatetimeIndex:
    """Every listed release time in UTC. Unconfirmed rows are kept: blocking a candidate time costs little."""
    rel = pd.read_csv(path, dtype=str)
    et = pd.to_datetime(rel["date"] + " " + rel["time_et"]).dt.tz_localize(ET)
    return pd.DatetimeIndex(et.dt.tz_convert("UTC")).sort_values()


def eia_blackout(ts_utc: pd.Series, minutes: int = EIA_BLACKOUT_MINUTES, releases=None) -> np.ndarray:
    """True for bars whose start lies within ±`minutes` of a release (window [t - m, t + m])."""
    releases = eia_release_times() if releases is None else releases
    t = ts_utc.to_numpy(dtype="datetime64[ns]").astype(np.int64)
    r = np.asarray(releases.as_unit("ns").asi8, dtype=np.int64)
    if len(r) == 0:
        return np.zeros(len(t), bool)
    w = np.int64(minutes) * 60 * 1_000_000_000
    i = np.searchsorted(r, t - w, side="left")  # first release >= t - w
    i = np.minimum(i, len(r) - 1)
    return np.abs(r[i] - t) <= w
