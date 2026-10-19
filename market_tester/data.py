"""Read CL 1-minute bars from data/processed/ohlcv-1m/year=YYYY/cl.parquet.

Prices are NOT back-adjusted: the continuous price jumps when raw_symbol changes (a roll).
A roll jump is never P&L; see LESSONS.md section 1.
"""

from collections.abc import Iterable

import pandas as pd

from .config import BARS_DIR, CONTINUOUS_SYMBOLS, DEFINITIONS_FILE, assert_allowed, is_cl_outright

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


SECOND_COLUMNS = ["c2_close_ticks", "c2_raw_symbol", "c2_instrument_id", "c2_expiration", "c2_is_next",
                  "c2_roll_so_far"]


def contract_definitions(path=DEFINITIONS_FILE) -> pd.DataFrame:
    """CL contract definitions keyed by `instrument_id` (raw_symbol repeats across decades: CLN0 = 2010 and 2020)."""
    defs = pd.read_parquet(path, columns=["instrument_id", "raw_symbol", "expiration"])
    for symbol in defs["raw_symbol"]:
        assert_allowed(symbol)
    if not defs["instrument_id"].is_unique or not defs["expiration"].is_unique or defs.isna().any().any():
        raise DataIntegrityError("contract definitions need unique instrument_id and expiration, no nulls")
    return defs.sort_values("expiration", ignore_index=True)


def next_contract_ids(defs: pd.DataFrame) -> dict[int, int]:
    """instrument_id -> id of the listed contract with the smallest expiration strictly after it."""
    ids = defs["instrument_id"].to_numpy()  # sorted by unique expiration, so the next row is the next contract
    return dict(zip(ids[:-1].tolist(), ids[1:].tolist()))


def check_definitions(bars: pd.DataFrame, defs: pd.DataFrame, prefix: str = "") -> None:
    """Every bar's (instrument_id, raw_symbol, expiration) must be its definition row."""
    cols = [f"{prefix}instrument_id", f"{prefix}raw_symbol", f"{prefix}expiration"]
    if bars[cols].isna().any().any():
        raise DataIntegrityError(f"null contract fields in bars: {bars[cols].isna().sum().to_dict()}")
    seen = bars[cols].drop_duplicates()
    seen.columns = ["instrument_id", "raw_symbol", "expiration"]
    m = seen.merge(defs, on="instrument_id", how="left", suffixes=("", "_def"))
    bad = m["raw_symbol_def"].isna() | (m["raw_symbol"] != m["raw_symbol_def"]) | (m["expiration"] != m["expiration_def"])
    if bad.any():
        raise DataIntegrityError(f"bars disagree with contract definitions: {m[bad].head(3).to_dict('records')}")


def attach_second(front: pd.DataFrame, second: pd.DataFrame, defs: pd.DataFrame) -> pd.DataFrame:
    """Front bars plus the CL.v.1 bar of the same minute (`c2_*`, NaN if none) and:
    `c2_is_next`     the second-month bar is the contract listed right after the front bar's contract;
    `c2_roll_so_far` CL.v.1 rolled earlier in this trading day (computed on every CL.v.1 bar before the join,
                     so a roll at a minute with no front bar still counts).
    Both inputs need time columns (`trading_day`) and `is_roll`. Front row count and order are unchanged."""
    for b in (front, second):
        if not b["ts_event_utc"].is_unique:
            raise DataIntegrityError("duplicate minutes in one continuous series")
    check_definitions(front, defs)
    check_definitions(second, defs)
    sec = second.sort_values("ts_event_utc", ignore_index=True)
    sec = sec.assign(roll_so_far=sec["is_roll"].astype(bool).groupby(sec["trading_day"]).cummax())
    cols = {"close_ticks": "c2_close_ticks", "raw_symbol": "c2_raw_symbol", "instrument_id": "c2_instrument_id",
            "expiration": "c2_expiration"}
    exact = sec[["ts_event_utc", *cols]].rename(columns=cols)
    out = front.merge(exact, on="ts_event_utc", how="left", validate="one_to_one", sort=False)
    # latest CL.v.1 bar at or before each front minute, same trading day: its roll-so-far flag
    left = out[["ts_event_utc", "trading_day"]].reset_index().sort_values("ts_event_utc")
    asof = pd.merge_asof(left, sec[["ts_event_utc", "trading_day", "roll_so_far"]].rename(
        columns={"trading_day": "sec_day"}), on="ts_event_utc", direction="backward")
    rolled = (asof["sec_day"] == asof["trading_day"]) & asof["roll_so_far"].fillna(False).astype(bool)
    out["c2_roll_so_far"] = pd.Series(rolled.to_numpy(), index=asof["index"].to_numpy()).sort_index().to_numpy()
    nxt = out["instrument_id"].map(next_contract_ids(defs))
    out["c2_is_next"] = out["c2_instrument_id"].notna() & (out["c2_instrument_id"] == nxt)
    if len(out) != len(front) or not out["ts_event_utc"].equals(front["ts_event_utc"].reset_index(drop=True)):
        raise DataIntegrityError("second-month join changed the front rows")
    return out
