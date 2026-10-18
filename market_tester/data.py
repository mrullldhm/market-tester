"""Read CL 1-minute bars from data/processed/ohlcv-1m/year=YYYY/cl.parquet.

Prices are NOT back-adjusted: the continuous price jumps when raw_symbol changes (a roll).
A roll jump is never P&L; see LESSONS.md section 1.
"""

from collections.abc import Iterable

import pandas as pd

from .config import BARS_DIR, CONTINUOUS_SYMBOLS, assert_allowed, is_cl_outright

FRONT = "CL.v.0"
SECOND = "CL.v.1"


class RollCrossed(RuntimeError):
    pass


class DataIntegrityError(RuntimeError):
    pass


def load_bars(years: Iterable[int] | None = None, continuous: str | None = FRONT) -> pd.DataFrame:
    """Bars sorted by (continuous_symbol, ts_event_utc), with an `is_roll` flag.

    `years` must be contiguous (a gap would fake a roll at the later year's first bar).
    `continuous=None` returns both CL.v.0 and CL.v.1. Raises if any non-CL symbol is present.
    """
    if continuous is not None and continuous not in CONTINUOUS_SYMBOLS:
        raise ValueError(f"continuous must be one of {sorted(CONTINUOUS_SYMBOLS)} or None, got {continuous!r}")
    if years is None:
        paths = sorted(BARS_DIR.glob("year=*/cl.parquet"))
    else:
        wanted = sorted(set(years))
        if wanted and wanted != list(range(wanted[0], wanted[-1] + 1)):
            raise ValueError(f"years must be contiguous, got {wanted}; load each span separately")
        paths = [BARS_DIR / f"year={y}" / "cl.parquet" for y in wanted]
    missing = [p for p in paths if not p.exists()]
    if missing or not paths:
        raise FileNotFoundError(f"no bar files for {missing or BARS_DIR}")

    filters = [("continuous_symbol", "==", continuous)] if continuous else None
    bars = pd.concat((pd.read_parquet(p, filters=filters) for p in paths), ignore_index=True)
    if bars.empty:
        raise DataIntegrityError(f"no rows for {continuous or 'any series'} in {[p.parent.name for p in paths]}")

    nulls = bars[["continuous_symbol", "raw_symbol"]].isna().sum()
    if nulls.any():
        raise DataIntegrityError(f"null symbols in bars: {nulls.to_dict()}")
    for symbol in pd.unique(pd.concat([bars["continuous_symbol"], bars["raw_symbol"]])):
        assert_allowed(symbol)

    bars = bars.sort_values(["continuous_symbol", "ts_event_utc"], ignore_index=True)
    return add_roll_flag(bars)


def add_roll_flag(bars: pd.DataFrame) -> pd.DataFrame:
    """is_roll = first bar of a new contract within each continuous series (first bar ever is not a roll).

    Assumes bars are sorted by (continuous_symbol, ts_event_utc), with no null raw_symbol.
    """
    if bars["raw_symbol"].isna().any():
        raise DataIntegrityError("null raw_symbol; roll flag would be wrong")
    prev = bars.groupby("continuous_symbol", sort=False)["raw_symbol"].shift()
    return bars.assign(is_roll=prev.notna() & (bars["raw_symbol"] != prev))


def check_same_contract(entry_symbol: str, exit_symbol: str) -> None:
    """Crash a backtest whose trade entry and exit are on different contracts.

    Endpoints only: the execution loop must also assert the held raw_symbol on every bar it consumes.
    """
    for symbol in (entry_symbol, exit_symbol):
        if not isinstance(symbol, str) or not is_cl_outright(symbol):
            raise RollCrossed(f"{symbol!r} is not a CL outright contract")
    if entry_symbol != exit_symbol:
        raise RollCrossed(f"trade spans a roll: entry {entry_symbol}, exit {exit_symbol}")
