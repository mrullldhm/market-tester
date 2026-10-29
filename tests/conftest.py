import numpy as np
import pandas as pd
import pytest

from market_tester.common import config
from market_tester.common.sessions import add_time_columns


def synthetic_bars(n_days: int = 1300, seed: int = 0, roll_every: int = 40, sd_ticks: float = 3.0) -> pd.DataFrame:
    """Random-walk CL-like 1-minute bars 08:00-14:30 ET on weekdays, a contract roll (with a price jump)
    every `roll_every` days. Zero edge by construction. 1300 days = 5 years (walk-forward needs 4+)."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2015-01-05", periods=n_days)
    per_day = 390
    start = (days.tz_localize("America/New_York") + pd.Timedelta(hours=8)).tz_convert("UTC")
    ts = (start.tz_localize(None).to_numpy(dtype="datetime64[ns]")[:, None]
          + np.arange(per_day)[None, :] * np.timedelta64(1, "m")).ravel()
    n = len(ts)
    contract = np.repeat(np.arange(n_days) // roll_every, per_day)
    months = "FGHJKMNQUVXZ"
    raw = np.array([f"CL{months[c % 12]}{(5 + c // 12) % 10}" for c in range(contract.max() + 1)])[contract]
    steps = np.round(rng.normal(0, sd_ticks, n)).astype(np.int64)
    jump = np.r_[0, np.diff(contract)] * rng.integers(-300, 300, n)
    close = 6000 + np.cumsum(steps + jump)
    open_ = np.r_[close[0], close[:-1]] + jump  # roll jump shows at the new contract's open
    hi = np.maximum(open_, close) + rng.integers(0, 3, n)
    lo = np.minimum(open_, close) - rng.integers(0, 3, n)
    bars = pd.DataFrame({"ts_event_utc": pd.to_datetime(ts, utc=True), "continuous_symbol": "CL.v.0",
                         "raw_symbol": raw, "open_ticks": open_, "high_ticks": hi, "low_ticks": lo,
                         "close_ticks": close})
    bars = add_time_columns(bars)
    bars["eia_block"] = False
    return bars


@pytest.fixture(scope="session")
def synth():
    return synthetic_bars()


@pytest.fixture(autouse=True)
def tmp_experiments(tmp_path, monkeypatch):
    """Every test writes ledger / prereg / reports under tmp, never into the real experiments/."""
    monkeypatch.setattr(config, "EXPERIMENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "QUANTUM_EXPERIMENTS_DIR", tmp_path)
    monkeypatch.setattr(config, "TV_EXPERIMENTS_DIR", tmp_path / "tradingview")
    monkeypatch.setattr(config, "LEDGER_FILE", tmp_path / "LEDGER.csv")
    return tmp_path
