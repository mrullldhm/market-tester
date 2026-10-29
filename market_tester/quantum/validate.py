"""Engine proofs on dev data (README roadmap step 3). Run: python -m market_tester.validate [--quick]

1. coin flip     long + short at the same random minutes: each pair nets exactly -2 x $24.02; one random
                 side per pair loses about $24.02 (z within +-2.58)
2. planted edge  add a known momentum drift after 10:00 ET; the morning-momentum toy must find it and PASS
3. null control  the same toy on clean bars must FAIL the gates
"""

import argparse
import math

import numpy as np
import pandas as pd

from ..common import ledger
from ..common.config import FEE_PER_SIDE, SLIPPAGE_TICKS_PER_SIDE, TICK_VALUE
from .engine import Market, run, with_costs
from .periods import DEV, load_period
from .report import evaluate
from .strategy import Signals
from .toys import MorningMomentum, morning_move_sign

PLANT_START = 10 * 60  # ET minute
PLANT_MINUTES = 60
SIGNAL_MINUTE = 9 * 60 + 58
ANCHOR = 9 * 60
TOY_GRID = {"minute": [SIGNAL_MINUTE], "anchor": [ANCHOR - 30, ANCHOR, ANCHOR + 30], "hold": [50, 60, 70]}


def coin_flip(bars: pd.DataFrame, n: int = 20_000, seed: int = 0, slip: float = SLIPPAGE_TICKS_PER_SIDE) -> dict:
    market = Market.from_bars(bars)
    rng = np.random.default_rng(seed)
    m = market.et_minute
    cand = np.flatnonzero((m >= 9 * 60) & (m < 14 * 60))
    picks = np.sort(rng.choice(cand, min(n, len(cand)), replace=False))
    picks = picks[np.r_[True, np.diff(market.ts[picks]) > 61 * 60 * 1_000_000_000]]  # no overlapping holds
    out = {}
    for side in (1, -1):
        s = np.zeros(len(market), np.int64)
        s[picks] = side
        out[side] = with_costs(run(market, Signals(s, hold_minutes=60)), slip).set_index("entry_bar")
    both = out[1].join(out[-1], lsuffix="_l", rsuffix="_s", how="inner")
    pair = both["net_usd_l"] + both["net_usd_s"]
    expected_pair = -2 * (2 * FEE_PER_SIDE + 2 * slip * TICK_VALUE)
    one = np.where(rng.random(len(both)) < 0.5, both["net_usd_l"], both["net_usd_s"])
    expected = expected_pair / 2
    se = one.std(ddof=1) / math.sqrt(len(one))
    z = (one.mean() - expected) / se
    max_err = float((pair - expected_pair).abs().max())
    return {"pairs": len(both), "max_pair_error_usd": max_err, "mean_usd": float(one.mean()),
            "expected_usd": expected, "se_usd": float(se), "z": float(z), "ok": max_err < 1e-9 and abs(z) < 2.58}


def plant_drift(bars: pd.DataFrame, ticks_per_minute: float, start: int = PLANT_START,
                minutes: int = PLANT_MINUTES, signal_minute: int = SIGNAL_MINUTE, anchor: int = ANCHOR) -> pd.DataFrame:
    """Plant a momentum edge: on each day, from `start` ET add sign x round(drift x elapsed) ticks for
    `minutes`, then hold the offset until 17:00 ET; sign = direction of the move from `anchor` to
    `signal_minute` (visible before the drift starts). Days differ in sign, so random entries on other days
    of the week do not get the edge. Same offset on O/H/L/C of a bar: bars stay valid."""
    at, sign = morning_move_sign(bars, signal_minute, anchor)
    day = bars["trading_day"].to_numpy()
    day_sign = pd.Series(sign[at], index=day[at])
    day_sign = day_sign[~day_sign.index.duplicated()]
    s = pd.Series(day).map(day_sign).fillna(0).to_numpy()
    m = bars["et_minute"].to_numpy().astype(np.int64)
    elapsed = np.clip(m - start, 0, minutes)
    live = (m >= start) & (m < 17 * 60)
    off = np.where(live, s * np.round(ticks_per_minute * elapsed), 0).astype(np.int64)
    out = bars.copy()
    for c in ("open", "high", "low", "close"):
        out[f"{c}_ticks"] = bars[f"{c}_ticks"].to_numpy() + off
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="2015-2019 only (walk-forward needs 4+ years)")
    ap.add_argument("--drift", type=float, default=0.25, help="planted ticks per minute (60 min = 15 ticks)")
    args = ap.parse_args(argv)
    years = list(range(2015, 2020)) if args.quick else None
    print("loading dev bars", flush=True)
    bars = load_period(DEV, years=years)

    cf = coin_flip(bars)
    print(f"1. coin flip: {cf}", flush=True)

    kw = dict(n_boot=500, n_random=100, n_mc=1000, n_jobs=6, seed=0)
    planted = evaluate(MorningMomentum(), TOY_GRID, item_id="V001", hypothesis=f"planted {args.drift} tick/min drift",
                       bars=plant_drift(bars, args.drift), kind="synthetic", gates={"min_trades": 100}, **kw)
    null = evaluate(MorningMomentum(), TOY_GRID, item_id="V002", hypothesis="toy on clean bars (null control)",
                    bars=bars, kind="synthetic", gates={"min_trades": 100}, **kw)
    ok = cf["ok"] and planted["verdict"] == "PASS" and null["verdict"] == "FAIL"
    print(f"2. planted edge: {planted['verdict']} net/trade {planted['summary']['net_usd_per_trade']:.2f} "
          f"gross ticks {planted['summary']['gross_ticks_per_trade']:.2f}")
    print(f"3. null control: {null['verdict']} net/trade {null['summary']['net_usd_per_trade']:.2f}")
    ledger.append("V000", "synthetic", DEV.name, statistic="engine_proofs", value=int(ok),
                  verdict="PASS" if ok else "FAIL", code_sha256=ledger.code_sha256(),
                  note=f"coin flip z {cf['z']:.2f} pairs {cf['pairs']}; quick={args.quick}")
    print("ENGINE PROOFS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
