"""Extend the CL 1-minute bars past the last Databento bar with Massive (second vendor) REST data.

    .venv/bin/python -m market_tester.common.massive [--end YYYY-MM-DD]

Downloads 1m bars of the CL outrights around the front (`GET /futures/v1/aggs/{ticker}?resolution=1min`),
checks Massive against Databento where both exist, rebuilds CL.v.0 / CL.v.1 for later bars by Databento's rule
(rank by the previous UTC day's volume, switch at 00:00 UTC) and writes
data/processed/ohlcv-1m-ext/cl_massive.parquet plus a manifest. Databento files are never touched; the loader
appends the extension only when asked (`data.load_bars(..., extend=True)`).
"""

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

from . import ledger
from .config import BARS_DIR, DATA_DIR, DEFINITIONS_FILE, REPO_ROOT, assert_allowed
from .data import FRONT, SECOND, load_bars

API = "https://api.massive.com/futures/v1/aggs/{ticker}"
EXT_DIR = DATA_DIR / "processed" / "ohlcv-1m-ext"
EXT_FILE = EXT_DIR / "cl_massive.parquet"
MANIFEST = DATA_DIR / "manifests" / "massive_ext.json"
MONTHS = "FGHJKMNQUVXZ"


def api_key() -> str:
    key = os.environ.get("MASSIVE_API_KEY")
    if not key:
        env = REPO_ROOT / ".env"
        for line in env.read_text().splitlines() if env.exists() else []:
            if line.startswith("MASSIVE_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("MASSIVE_API_KEY missing (.env)")
    return key


def next_contracts(raw: str, n: int) -> list[str]:
    """CLX6 -> [CLX6, CLZ6, CLF7, ...] (one-digit year, this decade)."""
    m, y = MONTHS.index(raw[2]), int(raw[3:])
    out = []
    for i in range(n):
        k = m + i
        out.append(f"CL{MONTHS[k % 12]}{(y + k // 12) % 10}")
    return out


def fetch(ticker: str, start: pd.Timestamp, end: pd.Timestamp, key: str) -> tuple[pd.DataFrame, list[str]]:
    assert_allowed(ticker)
    url = API.format(ticker=ticker)
    params = {"resolution": "1min", "window_start.gte": str(start.value), "window_start.lt": str(end.value),
              "limit": 50000, "sort": "window_start.asc"}
    rows, ids = [], []
    while url:
        for attempt in range(5):
            r = requests.get(url, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=60)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        else:
            r.raise_for_status()
        js = r.json()
        ids.append(js.get("request_id", ""))
        rows.extend(js.get("results") or [])
        url, params = js.get("next_url"), None
        print(f"  {ticker}: {len(rows):,} bars", flush=True)
    df = pd.DataFrame(rows)
    if df.empty:
        return df, ids
    if (df["ticker"] != ticker).any():
        raise ValueError(f"{ticker}: response holds other tickers")
    out = pd.DataFrame({"raw_symbol": ticker, "ts_event_utc": pd.to_datetime(df["window_start"], unit="ns", utc=True),
                        "volume": df["volume"].astype("int64")})
    for k in ("open", "high", "low", "close"):
        ticks = df[k].astype(float) * 100
        if (ticks - ticks.round()).abs().max() > 1e-6:
            raise ValueError(f"{ticker}: {k} not on the $0.01 tick")
        out[f"{k}_ticks"] = ticks.round().astype("int64")
    return out.drop_duplicates("ts_event_utc").sort_values("ts_event_utc", ignore_index=True), ids


def rank_by_volume(m: pd.DataFrame, seed_front: str, seed_second: str, first_day: pd.Timestamp) -> pd.DataFrame:
    """Per UTC day from `first_day`: front / second = top two contracts by the previous UTC day's volume.
    The first day keeps Databento's last assignment (its own previous day is in Databento)."""
    day = m["ts_event_utc"].dt.floor("D")
    vol = m.assign(d=day).groupby(["d", "raw_symbol"])["volume"].sum().unstack(fill_value=0).sort_index()
    out = {}
    days = vol.index[vol.index >= first_day]
    for i, d in enumerate(days):
        if i == 0 or vol.index.get_loc(d) == 0:
            out[d] = (seed_front, seed_second)
            continue
        prev = vol.iloc[vol.index.get_loc(d) - 1].sort_values(ascending=False)
        out[d] = (prev.index[0], prev.index[1])
    return pd.DataFrame(out, index=["front", "second"]).T


def compare(db: pd.DataFrame, m: pd.DataFrame) -> dict:
    """Same raw_symbol + minute in both vendors: share of exact OHLC matches and of volume matches."""
    j = db.merge(m, on=["raw_symbol", "ts_event_utc"], suffixes=("_db", "_m"))
    if j.empty:
        return {"overlap_bars": 0}
    ohlc = np.all([j[f"{k}_ticks_db"] == j[f"{k}_ticks_m"] for k in ("open", "high", "low", "close")], axis=0)
    close_diff = (j["close_ticks_db"] - j["close_ticks_m"]).abs()
    return {"overlap_bars": int(len(j)), "ohlc_exact_pct": float(ohlc.mean() * 100),
            "close_within_1tick_pct": float((close_diff <= 1).mean() * 100),
            "volume_exact_pct": float((j["volume_db"] == j["volume_m"]).mean() * 100),
            "db_bars_missing_in_massive": int(len(db) - len(j))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="UTC date, exclusive (default: now)")
    ap.add_argument("--check-days", type=int, default=14, help="days of Databento overlap to compare")
    args = ap.parse_args()
    key = api_key()
    last_year = max(int(p.parent.name.split("=")[1]) for p in BARS_DIR.glob("year=*/cl.parquet"))
    db = load_bars([last_year], continuous=None)
    last_ts = db["ts_event_utc"].max()
    end = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC").floor("min")
    start = (last_ts - pd.Timedelta(days=args.check_days)).floor("D")
    tail = db[db["ts_event_utc"] == last_ts]
    seed_front = tail.loc[tail["continuous_symbol"] == FRONT, "raw_symbol"].iloc[0]
    seed_second = tail.loc[tail["continuous_symbol"] == SECOND, "raw_symbol"].iloc[0]
    tickers = next_contracts(seed_front, 4)
    print(f"[massive] Databento ends {last_ts}; front {seed_front}, second {seed_second}; fetching {tickers} "
          f"{start.date()}..{end}", flush=True)
    parts, request_ids = [], {}
    for t in tickers:
        df, ids = fetch(t, start, end, key)
        request_ids[t] = ids
        if len(df):
            parts.append(df)
    m = pd.concat(parts, ignore_index=True)
    check = compare(db[db["ts_event_utc"] >= start].drop_duplicates(["raw_symbol", "ts_event_utc"]),
                    m[m["ts_event_utc"] <= last_ts])
    print(f"[massive] overlap check vs Databento: {check}", flush=True)
    new = m[m["ts_event_utc"] > last_ts]
    first_day = (last_ts + pd.Timedelta(minutes=1)).floor("D")
    ranks = rank_by_volume(m, seed_front, seed_second, first_day)
    defs = pd.read_parquet(DEFINITIONS_FILE).sort_values("activation").drop_duplicates("raw_symbol", keep="last")
    defs = defs.set_index("raw_symbol")
    series = []
    for cont, col in ((FRONT, "front"), (SECOND, "second")):
        day = new["ts_event_utc"].dt.floor("D")
        pick = new[new["raw_symbol"].to_numpy() == day.map(ranks[col]).to_numpy()].copy()
        pick["continuous_symbol"] = cont
        series.append(pick)
    ext = pd.concat(series, ignore_index=True)
    ext["instrument_id"] = ext["raw_symbol"].map(defs["instrument_id"]).fillna(-1).astype("int64")
    ext["expiration"] = pd.to_datetime(ext["raw_symbol"].map(defs["expiration"]), utc=True)
    for k in ("open", "high", "low", "close"):
        ext[k] = ext[f"{k}_ticks"] / 100
    ext["vendor"] = "massive"
    cols = ["ts_event_utc", "continuous_symbol", "instrument_id", "raw_symbol", "open_ticks", "high_ticks",
            "low_ticks", "close_ticks", "open", "high", "low", "close", "volume", "expiration", "vendor"]
    ext = ext[cols].sort_values(["continuous_symbol", "ts_event_utc"], ignore_index=True)
    bad = (ext["low_ticks"] > ext[["open_ticks", "close_ticks"]].min(axis=1)) | \
          (ext["high_ticks"] < ext[["open_ticks", "close_ticks"]].max(axis=1))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} Massive bars break low <= open/close <= high")
    EXT_DIR.mkdir(parents=True, exist_ok=True)
    ext.to_parquet(EXT_FILE, index=False)
    manifest = {"written_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "Massive REST "
                "/futures/v1/aggs resolution=1min", "after_databento_ts": str(last_ts), "end_exclusive": str(end),
                "first_bar": str(ext["ts_event_utc"].min()), "last_bar": str(ext["ts_event_utc"].max()),
                "rows": {c: int((ext["continuous_symbol"] == c).sum()) for c in (FRONT, SECOND)},
                "roll_rule": "rank by previous UTC day volume, switch 00:00 UTC (matches Databento CL.v.0 on "
                             "848/869 UTC days 2024-2026; misses are +-1 day at rolls)",
                "ranks": {str(d.date()): list(r) for d, r in ranks.iterrows()},
                "overlap_check": check, "request_ids": request_ids, "sha256": ledger.sha256_file(EXT_FILE)}
    MANIFEST.write_text(json.dumps(manifest, indent=2))
    print(f"[massive] wrote {EXT_FILE} {manifest['rows']} bars {manifest['first_bar']}..{manifest['last_bar']}; "
          f"ranks {manifest['ranks']}", flush=True)


if __name__ == "__main__":
    main()
