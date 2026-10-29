"""Python port of pine/SPEC.md (rev 3): the Wickless S/R retest indicator, for CL only.

    .venv/bin/python pine/crosscheck.py run --start 2023-01-01 --end 2023-07-01 --tf 5
        Databento CL.v.0, dev period only (holdout stays locked). Prints trade stats; --trades writes a CSV.
    .venv/bin/python pine/crosscheck.py tv --csv export.csv
        TradingView "Export chart data" of a CL chart with the indicator on it. Runs this port on the same bars
        and compares bar by bar with the script's hidden x_* plots: detection, entry side, cumulative trades,
        net $ and R. Same bars and same first bar, so parity must be exact.

Chart tool check, not a strategy test: no ledger row, no holdout.
"""

import argparse
import math
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_tester import config  # noqa: E402
from market_tester.periods import DEV, load_period  # noqa: E402
from market_tester.sessions import ET, add_time_columns  # noqa: E402

SUPPORTED_TF = (1, 2, 3, 5, 10, 15, 20, 30, 60)
LAST_MINUTE = 16 * 60 + 59  # 16:59 ET
MAX_PIVOTS = 50
WARMUP_DAYS = 90
TICK_DOLLARS = config.TICK_VALUE  # $10 per CL tick per contract (tick 0.01 x point value 1000)
MAX_PIVOT_WIDTH = (100, 50)  # left, right: Pine max_bars_back 500 must cover the pivot loop
PRICE_SCALE = round(1 / config.TICK)  # ticks -> price as TradingView prints it (71.63, not 7163 * 0.01)


@dataclass(frozen=True)
class Config:
    """SPEC section 1. Defaults equal the Pine inputs."""
    tf: int = 5
    tolTicks: int = 0
    minRangeTicks: int = 1
    directional: bool = True
    minGap: int = 1
    maxAge: int = 100
    maxLevels: int = 20
    resetSession: bool = True
    pivLeft: int = 5
    pivRight: int = 2
    stopBufTicks: int = 0
    maxRiskTicks: int = 0
    rr: float = 1.1
    flatSession: bool = True
    useRibbon: bool = False
    ribbonType: str = "EMA"
    ribbonLens: tuple = (8, 13, 21, 34, 55)
    ribbonMode: str = "Stacked"
    useST: bool = False
    stFactor: float = 3.0
    stAtrLen: int = 10
    filterArrows: bool = False
    qty: int = 1
    costRT: float = 24.02

    def __post_init__(self):
        checks = {
            "tf": self.tf in SUPPORTED_TF, "tolTicks": self.tolTicks >= 0, "minRangeTicks": self.minRangeTicks >= 0,
            "minGap": self.minGap >= 1, "maxAge": self.maxAge >= self.minGap, "maxLevels": 1 <= self.maxLevels <= 100,
            "pivLeft": 1 <= self.pivLeft <= MAX_PIVOT_WIDTH[0], "pivRight": 1 <= self.pivRight <= MAX_PIVOT_WIDTH[1], "stopBufTicks": self.stopBufTicks >= 0,
            "maxRiskTicks": self.maxRiskTicks >= 0, "rr": self.rr >= 0.1, "stFactor": self.stFactor >= 0.1,
            "stAtrLen": self.stAtrLen >= 1, "qty": self.qty >= 1, "costRT": self.costRT >= 0,
            "ribbonType": self.ribbonType in ("EMA", "SMA"),
            "ribbonMode": self.ribbonMode in ("Stacked", "Price vs slowest"),
            "ribbonLens": len(self.ribbonLens) == 5 and all(n >= 1 for n in self.ribbonLens),
        }
        bad = [k for k, ok in checks.items() if not ok]
        if bad:
            raise ValueError(f"inputs outside SPEC section 1 domains: {bad}")


# ---------- bars ----------

def resample(bars1m: pd.DataFrame, tf: int) -> pd.DataFrame:
    """SPEC section 7: 1-minute bars (with time columns) to `tf`-minute bars from the 18:00 ET open."""
    if not bars1m["ts_event_utc"].is_unique:
        raise ValueError("duplicate 1-minute stamps")
    b = bars1m.sort_values("ts_event_utc")
    open_et = (b["trading_day"] - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).dt.tz_localize(ET)
    minutes = ((b["ts_et"] - open_et).dt.total_seconds() // 60).astype(int)
    b = b.assign(bucket=minutes // tf)
    g = b.groupby(["trading_day", "bucket", "raw_symbol"], sort=False)
    out = g.agg(o=("open_ticks", "first"), h=("high_ticks", "max"), l=("low_ticks", "min"),
                c=("close_ticks", "last"), v=("volume", "sum"), first_ts=("ts_event_utc", "first")).reset_index()
    start_et = (out["trading_day"] - pd.Timedelta(days=1) + pd.Timedelta(hours=18)).dt.tz_localize(ET) \
        + pd.to_timedelta(out["bucket"] * tf, unit="min")
    out["ts"] = start_et.dt.tz_convert("UTC")
    out = out.sort_values(["ts", "first_ts"], ignore_index=True).drop(columns=["bucket", "first_ts"])
    return finish_bars(out, tf)


def finish_bars(bars: pd.DataFrame, tf: int) -> pd.DataFrame:
    """Adds `close` (float), `last_of_session`, `new_day`, `roll` (SPEC section 0 / 7). Needs ts, trading_day."""
    et = bars["ts"].dt.tz_convert(ET)
    s = et.dt.hour * 60 + et.dt.minute
    day = bars["trading_day"]
    sym = bars["raw_symbol"] if "raw_symbol" in bars else pd.Series("", index=bars.index)
    first = np.arange(len(bars)) == 0
    return bars.assign(
        close=bars["c"] / PRICE_SCALE, high_f=bars["h"] / PRICE_SCALE, low_f=bars["l"] / PRICE_SCALE,
        last_of_session=((s <= LAST_MINUTE) & (LAST_MINUTE < s + tf)).to_numpy(),
        new_day=(~first & (day != day.shift())).to_numpy(),
        roll=(~first & (sym != sym.shift())).to_numpy(),
    )


# ---------- filters (SPEC section 5) ----------

def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def ema_seeded(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    a = 2.0 / (n + 1)
    e = float(np.mean(x[:n]))
    out[n - 1] = e
    for i in range(n, len(x)):
        e = a * x[i] + (1 - a) * e
        out[i] = e
    return out


def rma_seeded(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    r = float(np.mean(x[:n]))
    out[n - 1] = r
    for i in range(n, len(x)):
        r = (r * (n - 1) + x[i]) / n
        out[i] = r
    return out


def supertrend(h: np.ndarray, l: np.ndarray, c: np.ndarray, factor: float, n: int) -> np.ndarray:
    """Trend per bar: 1 bull, -1 bear, 0 not ready."""
    prev_c = np.roll(c, 1)
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    if len(tr):
        tr[0] = h[0] - l[0]
    atr = rma_seeded(tr, n)
    trend = np.zeros(len(c), dtype=int)
    up = dn = math.nan
    for i in range(len(c)):
        if math.isnan(atr[i]):
            continue
        hl2 = (h[i] + l[i]) / 2
        up_b, dn_b = hl2 - factor * atr[i], hl2 + factor * atr[i]
        if math.isnan(up):
            up, dn, trend[i] = up_b, dn_b, 1
            continue
        up = up_b if (up_b > up or c[i - 1] < up) else up
        dn = dn_b if (dn_b < dn or c[i - 1] > dn) else dn
        if trend[i - 1] == 1:
            trend[i] = -1 if c[i] < up else 1
        else:
            trend[i] = 1 if c[i] > dn else -1
    return trend


def filters(bars: pd.DataFrame, cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    """long_ok / short_ok per bar, values at that bar."""
    c = bars["close"].to_numpy(float)
    n = len(c)
    long_ok = np.ones(n, bool)
    short_ok = np.ones(n, bool)
    if cfg.useRibbon:
        ma = ema_seeded if cfg.ribbonType == "EMA" else sma
        m = [ma(c, k) for k in cfg.ribbonLens]
        ready = ~np.any([np.isnan(x) for x in m], axis=0)
        if cfg.ribbonMode == "Stacked":
            up = np.all([m[i] > m[i + 1] for i in range(4)], axis=0)
            down = np.all([m[i] < m[i + 1] for i in range(4)], axis=0)
        else:
            up, down = c > m[4], c < m[4]
        long_ok &= ready & up
        short_ok &= ready & down
    if cfg.useST:
        trend = supertrend(bars["high_f"].to_numpy(float), bars["low_f"].to_numpy(float), c, cfg.stFactor, cfg.stAtrLen)
        long_ok &= trend == 1
        short_ok &= trend == -1
    return long_ok, short_ok


# ---------- detection (SPEC section 2) ----------

def detect(o, h, l, c, cfg: Config, long_ok=None, short_ok=None) -> tuple[np.ndarray, np.ndarray]:
    o, h, l, c = (np.asarray(x, dtype=np.int64) for x in (o, h, l, c))
    no_bot = np.minimum(o, c) - l <= cfg.tolTicks
    no_top = h - np.maximum(o, c) <= cfg.tolTicks
    valid = h - l >= cfg.minRangeTicks
    bull, bear = c > o, c < o
    both = no_bot & no_top
    keep_side = cfg.directional | both
    sup = valid & no_bot & np.where(keep_side, bull, True)
    res = valid & no_top & np.where(keep_side, bear, True)
    if cfg.filterArrows:
        sup &= long_ok
        res &= short_ok
    return sup, res


# ---------- simulation (SPEC sections 3-4) ----------

def pivot_at(l: np.ndarray, h: np.ndarray, j: int, left: int, right: int) -> tuple[bool, bool]:
    """Pivot low / high at bar j (SPEC section 4): ties allowed on the left, not on the right."""
    lo = l[j] <= l[j - left:j].min() and l[j] < l[j + 1:j + right + 1].min()
    hi = h[j] >= h[j - left:j].max() and h[j] > h[j + 1:j + right + 1].max()
    return bool(lo), bool(hi)


def simulate(bars: pd.DataFrame, cfg: Config, progress: bool = False) -> tuple[pd.DataFrame, dict]:
    """Trades (in exit order) and per-bar series: det, entry_side, voided count."""
    o, h, l, c = (bars[k].to_numpy(np.int64) for k in ("o", "h", "l", "c"))
    los = bars["last_of_session"].to_numpy(bool)
    new_day = bars["new_day"].to_numpy(bool)
    roll = bars["roll"].to_numpy(bool)
    seg = np.cumsum(roll)
    ts = bars["ts"].to_numpy()
    long_ok, short_ok = filters(bars, cfg)
    sup, res = detect(o, h, l, c, cfg, long_ok, short_ok)
    n = len(bars)
    det = np.where(sup, 1, np.where(res, -1, 0))
    entry_side = np.zeros(n, dtype=int)

    levels: list[list[int]] = []  # [price, side, d], creation order
    piv_low: list[int] = []
    piv_high: list[int] = []
    trade: dict | None = None
    trades: list[dict] = []
    voided: list[int] = []  # entry bars of trades voided by a roll
    L, R = cfg.pivLeft, cfg.pivRight
    step = max(n // 10, 1)
    t0 = time.time()

    def close(t: int, exit_t: int, reason: str) -> None:
        pnl = (exit_t - trade["entry"]) * trade["side"]
        trades.append({**trade, "exit_bar": t, "exit_ts": ts[t], "exit": exit_t, "reason": reason, "pnl_t": pnl,
                       "r": pnl / trade["risk"],
                       "net": pnl * TICK_DOLLARS * cfg.qty - cfg.costRT * cfg.qty})

    for t in range(n):
        if progress and t % step == 0:
            print(f"  simulate {t:,}/{n:,} bars ({time.time() - t0:.0f}s, {len(trades)} trades)", flush=True)
        exited = False
        # 0. roll (Python only), then new day
        if roll[t]:
            if trade is not None:
                voided.append(trade["entry_bar"])
                trade = None
            levels.clear()
            piv_low.clear()
            piv_high.clear()
        if new_day[t]:
            if trade is not None:
                close(t, o[t], "gap")
                trade = None
                exited = True
            if cfg.resetSession:
                levels.clear()
        # (a) manage
        if trade is not None:
            side, stop, tgt = trade["side"], trade["stop"], trade["target"]
            stop_hit = l[t] <= stop if side == 1 else h[t] >= stop
            tgt_hit = h[t] >= tgt + 1 if side == 1 else l[t] <= tgt - 1
            if stop_hit:
                close(t, min(o[t], stop) if side == 1 else max(o[t], stop), "stop")
                trade, exited = None, True
            elif tgt_hit:
                close(t, tgt, "target")
                trade, exited = None, True
        # (b) expire
        levels = [lv for lv in levels if t <= lv[2] + cfg.maxAge]
        # (c) touches and entry
        live = [lv for lv in levels if t >= lv[2] + cfg.minGap]
        touched = [lv for lv in live if (l[t] <= lv[0] if lv[1] == 1 else h[t] >= lv[0])]
        best = None
        for lv in touched:  # latest d, then closest to the open, then earlier created
            if best is None or lv[2] > best[2] or (lv[2] == best[2] and abs(lv[0] - o[t]) < abs(best[0] - o[t])):
                best = lv
        if touched:
            gone = {id(lv) for lv in touched}
            levels = [lv for lv in levels if id(lv) not in gone]
        sides = {lv[1] for lv in touched}
        fill = None
        if best is not None and len(sides) == 1:
            p, side = best[0], best[1]
            if side == 1:
                fill = o[t] if o[t] <= p else (p if l[t] <= p - 1 else None)
            else:
                fill = o[t] if o[t] >= p else (p if h[t] >= p + 1 else None)
        if fill is not None and trade is None and not exited and not los[t]:
            side = best[1]
            ok = t >= 1 and (long_ok[t - 1] if side == 1 else short_ok[t - 1])
            stop = None
            if ok:
                for pv in reversed(piv_low if side == 1 else piv_high):
                    if (pv < fill) if side == 1 else (pv > fill):
                        stop = pv - side * cfg.stopBufTicks
                        break
            if stop is not None:
                risk = abs(fill - stop)
                if risk >= 1 and (cfg.maxRiskTicks == 0 or risk <= cfg.maxRiskTicks):
                    target = fill + side * math.ceil(cfg.rr * risk - 1e-9)
                    trade = {"entry_bar": t, "entry_ts": ts[t], "side": side, "entry": int(fill), "stop": int(stop),
                             "target": int(target), "risk": int(risk), "level": best[0], "level_bar": best[2]}
                    entry_side[t] = side
                    if (l[t] <= stop) if side == 1 else (h[t] >= stop):
                        close(t, stop, "stop")
                        trade = None
        # (d) detection
        if det[t] != 0:
            same = [lv for lv in levels if lv[1] == det[t]]
            if len(same) >= cfg.maxLevels:
                levels.remove(same[0])
            levels.append([int(l[t] if det[t] == 1 else h[t]), int(det[t]), t])
        # (e) session end
        if los[t]:
            if cfg.flatSession and trade is not None:
                close(t, c[t], "session")
                trade = None
            if cfg.resetSession:
                levels.clear()
        # pivots found at t
        j = t - R
        if j - L >= 0 and seg[j - L] == seg[t]:
            is_low, is_high = pivot_at(l, h, j, L, R)
            if is_low:
                piv_low.append(int(l[j]))
                del piv_low[:-MAX_PIVOTS]
            if is_high:
                piv_high.append(int(h[j]))
                del piv_high[:-MAX_PIVOTS]

    cols = ["entry_bar", "entry_ts", "side", "entry", "stop", "target", "risk", "level", "level_bar", "exit_bar",
            "exit_ts", "exit", "reason", "pnl_t", "r", "net"]
    return pd.DataFrame(trades, columns=cols), {"det": det, "entry_side": entry_side, "voided": voided,
                                               "open_trade": trade}


# ---------- statistics (SPEC section 6) ----------

def max_drawdown(x: np.ndarray) -> float:
    if len(x) == 0:
        return 0.0
    cum = np.cumsum(x)
    peak = np.maximum.accumulate(np.maximum(cum, 0.0))
    return float(np.max(peak - cum))


def stats(trades: pd.DataFrame, cfg: Config) -> dict:
    n = len(trades)
    net = trades["net"].to_numpy(float)
    r = trades["r"].to_numpy(float)
    gp, gl = net[net > 0].sum(), -net[net < 0].sum()
    longs = trades["side"] == 1
    return {
        "trades": n,
        "win_rate": float((net > 0).mean()) if n else math.nan,
        "target_hit_rate": float((trades["reason"] == "target").mean()) if n else math.nan,
        "avg_r": float(r.mean()) if n else math.nan,
        "total_r": float(r.sum()),
        "net": float(net.sum()),
        "net_per_trade": float(net.mean()) if n else math.nan,
        "profit_factor": float(gp / gl) if n and gl > 0 else math.nan,
        "max_dd_net": max_drawdown(net),
        "max_dd_r": max_drawdown(r),
        "long_n": int(longs.sum()), "long_net": float(net[longs.to_numpy()].sum()),
        "short_n": int((~longs).sum()), "short_net": float(net[~longs.to_numpy()].sum()),
        "breakeven_win_rate_pre_cost": 1 / (1 + cfg.rr),
        "exit_reasons": trades["reason"].value_counts().to_dict(),
    }


# ---------- data paths ----------

def databento_bars(start: pd.Timestamp, end: pd.Timestamp, tf: int) -> pd.DataFrame:
    """Dev-period CL.v.0 bars from `start - WARMUP_DAYS` to `end` (trading days, end exclusive)."""
    if end - pd.Timedelta(days=1) > DEV.last_day or start < DEV.first_day:
        raise ValueError(f"run is dev-only: {DEV.first_day.date()} .. {DEV.last_day.date()} (holdout stays locked)")
    load_from = max(start - pd.Timedelta(days=WARMUP_DAYS), DEV.first_day)
    years = list(range(load_from.year - 1, (end - pd.Timedelta(days=1)).year + 1))
    b = load_period(DEV, years=years)
    b = b[(b["trading_day"] >= load_from) & (b["trading_day"] < end)]
    return resample(b, tf)


def tv_bars(path: Path, tf: int) -> pd.DataFrame:
    """TradingView chart export: time, open, high, low, close, plus the script's x_* plots."""
    raw = pd.read_csv(path)
    if raw.empty:
        raise ValueError(f"{path} has no bars")
    raw.columns = [col.strip() for col in raw.columns]
    t = raw["time"]
    ts = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    px = {k: (raw[k] / config.TICK).round().astype(np.int64) for k in ("open", "high", "low", "close")}
    bars = pd.DataFrame({"ts": ts, "o": px["open"], "h": px["high"], "l": px["low"], "c": px["close"]})
    bars = add_time_columns(bars.rename(columns={"ts": "ts_event_utc"})).rename(columns={"ts_event_utc": "ts"})
    bars = finish_bars(bars, tf)
    for col in ("x_det", "x_entry", "x_n", "x_net", "x_r"):
        if col not in raw:
            raise ValueError(f"export lacks plot {col!r}: add the indicator to the chart before exporting")
        bars[col] = raw[col].to_numpy()
    return bars


def cumulative(trades: pd.DataFrame, n: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Closed-trade count, net $ and R per bar (booked at the exit bar), as the Pine x_n / x_net / x_r plots."""
    k, net, r = np.zeros(n), np.zeros(n), np.zeros(n)
    for _, tr in trades.iterrows():
        k[tr["exit_bar"]] += 1
        net[tr["exit_bar"]] += tr["net"]
        r[tr["exit_bar"]] += tr["r"]
    return np.cumsum(k), np.cumsum(net), np.cumsum(r)


def compare_tv(bars: pd.DataFrame, cfg: Config) -> dict:
    """Per-bar parity on every confirmed bar (all but the export's last, which may still be forming)."""
    if len(bars) < 2:
        raise ValueError("export needs at least two bars")
    last = len(bars) - 1
    confirmed = bars.iloc[:last].reset_index(drop=True)
    trades, series = simulate(confirmed, cfg)
    k, net, r = cumulative(trades, last)
    ours = {"x_det": series["det"], "x_entry": series["entry_side"], "x_n": k, "x_net": net, "x_r": r}
    tolerance = {"x_det": 0, "x_entry": 0, "x_n": 0, "x_net": 0.01, "x_r": 1e-6}
    out: dict = {"bars_compared": last, "trades_py": len(trades), "trades_tv": int(confirmed["x_n"].iloc[-1])}
    for col, mine in ours.items():
        theirs = confirmed[col].fillna(0).to_numpy(float)
        bad = np.flatnonzero(np.abs(theirs - np.asarray(mine, float)) > tolerance[col])
        out[f"{col}_mismatch"] = len(bad)
        out[f"{col}_first_mismatch"] = str(confirmed["ts"].iloc[bad[0]]) if len(bad) else None
    out["parity_ok"] = all(out[f"{col}_mismatch"] == 0 for col in ours)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    run_p = sub.add_parser("run")
    run_p.add_argument("--start", required=True)
    run_p.add_argument("--end", required=True, help="exclusive trading day")
    run_p.add_argument("--trades", type=Path)
    tv_p = sub.add_parser("tv")
    tv_p.add_argument("--csv", type=Path, required=True)
    for p in (run_p, tv_p):
        p.add_argument("--tf", type=int, default=5)
        p.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                       help="override a Config input, e.g. --set useST=true --set rr=1.5")
    args = ap.parse_args(argv)
    cfg = Config(tf=args.tf, **parse_overrides(args.set))

    if args.cmd == "run":
        start, end = pd.Timestamp(args.start), pd.Timestamp(args.end)
        print(f"loading CL.v.0 {start.date()} .. {end.date()} (+{WARMUP_DAYS}d warm-up), tf {cfg.tf}m", flush=True)
        bars = databento_bars(start, end, cfg.tf)
        print(f"  {len(bars):,} bars", flush=True)
        trades, series = simulate(bars, cfg, progress=True)
        day = bars["trading_day"].to_numpy()  # score entries on trading days >= start
        scored = trades[day[trades["entry_bar"].to_numpy(int)] >= start.to_datetime64()] if len(trades) else trades
        result = stats(scored, cfg)
        result["voided_by_roll"] = sum(day[b] >= start.to_datetime64() for b in series["voided"])
        for key, val in result.items():
            print(f"{key:>28}: {val}")
        if args.trades:
            scored.to_csv(args.trades, index=False)
            print(f"trades -> {args.trades}")
    else:
        result = compare_tv(tv_bars(args.csv, cfg.tf), cfg)
        for key, val in result.items():
            print(f"{key:>20}: {val}")
        print("PARITY OK" if result["parity_ok"] else "PARITY FAIL")
        return 0 if result["parity_ok"] else 1
    return 0


def parse_overrides(items: list[str]) -> dict:
    types = {f.name: f.type for f in fields(Config)}
    out = {}
    for item in items:
        name, _, raw = item.partition("=")
        if name not in types or name == "tf":
            raise SystemExit(f"unknown input {name!r}")
        kind = types[name]
        if kind in (bool, "bool"):
            if raw.lower() not in ("true", "false"):
                raise SystemExit(f"{name} must be true or false, got {raw!r}")
            out[name] = raw.lower() == "true"
        elif kind in (int, "int"):
            out[name] = int(raw)
        elif kind in (float, "float"):
            out[name] = float(raw)
        elif kind in (tuple, "tuple"):
            out[name] = tuple(int(x) for x in raw.split(","))
        else:
            out[name] = raw
    return out


if __name__ == "__main__":
    raise SystemExit(main())
