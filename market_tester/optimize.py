"""Guarded parameter grid: every configuration is run, counted and kept, so the search can be penalized.

- `run_grid`       all configs on the same bars -> per-day P&L and trade-count matrices (days x configs)
- `plateau`        each config's score = median over itself and its grid neighbours: pick a stable
                   region, not a lucky peak
- `walk_forward`   anchored: each test year is picked from earlier years only, then concatenated

Signals are computed once on all dev bars, so walk-forward is honest only for causal, stateless rules
(no fitting inside `signals`); report.evaluate refuses strategies that define `fit`.
"""

import itertools
import math
import multiprocessing as mp
import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import Market, run, with_costs
from .metrics import daily, trading_days


@dataclass
class GridResult:
    grid: dict
    configs: list[dict]
    days: pd.DatetimeIndex
    pnl: np.ndarray  # days x configs, net $ (0 on days without a trade)
    trades: np.ndarray  # days x configs, trade count
    slip_ticks: float

    @property
    def shape(self) -> tuple[int, ...]:
        return tuple(len(v) for v in self.grid.values())

    def table(self) -> pd.DataFrame:
        n = self.trades.sum(axis=0)
        tot = self.pnl.sum(axis=0)
        sharpe_day = day_sharpe(self.pnl)
        return pd.DataFrame(self.configs).assign(
            trades=n, total_net_usd=tot, net_usd_per_trade=np.where(n > 0, tot / np.maximum(n, 1), np.nan),
            sharpe_day=sharpe_day, plateau_sharpe_day=plateau(sharpe_day, self.shape) if self.grid else sharpe_day)


def _same(a, b) -> bool:
    try:
        return bool(a == b)
    except (TypeError, ValueError):
        return repr(a) == repr(b)


def validate_grid(grid: dict) -> None:
    """Each axis: a non-empty list of distinct values; numeric axes strictly increasing, so that grid
    neighbours (plateau) are neighbouring values. Non-numeric axes are neighbours in the order given."""
    for k, values in grid.items():
        if not isinstance(values, (list, tuple)) or not values:
            raise ValueError(f"grid axis {k!r} must be a non-empty list")
        numeric = all(isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, (bool, np.bool_))
                      for v in values)
        if numeric:
            if not all(math.isfinite(v) for v in values):
                raise ValueError(f"grid axis {k!r} has a non-finite value")
            if not all(b > a for a, b in zip(values, values[1:])):
                raise ValueError(f"numeric grid axis {k!r} must be strictly increasing")
        elif any(_same(a, b) for i, a in enumerate(values) for b in values[i + 1:]):
            raise ValueError(f"grid axis {k!r} repeats a value")


def grid_configs(grid: dict) -> list[dict]:
    validate_grid(grid)
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*(grid[k] for k in keys))]


_STATE: dict = {}  # per process: the parent for serial runs, each worker via _init_worker


def _init_worker(strategy, bars, configs, slip):
    _STATE.update(strategy=strategy, bars=bars, market=Market.from_bars(bars), configs=configs,
                  days=trading_days(bars), slip=slip)


def _one(j: int):
    s = _STATE
    trades = with_costs(run(s["market"], s["strategy"].signals(s["bars"], **s["configs"][j])), s["slip"])
    d = daily(trades, s["days"])
    return j, d["pnl"].to_numpy(float), d["n"].to_numpy(np.int64)


def run_grid(strategy, bars: pd.DataFrame, grid: dict, slip_ticks: float = 1, n_jobs: int = 1,
             market: Market | None = None, progress: bool = True) -> GridResult:
    configs = grid_configs(grid)
    days = trading_days(bars)
    _STATE.update(strategy=strategy, bars=bars, market=market or Market.from_bars(bars), configs=configs,
                  days=days, slip=slip_ticks)
    pnl = np.zeros((len(days), len(configs)))
    cnt = np.zeros((len(days), len(configs)), np.int64)
    t0, done, step = time.time(), 0, max(1, len(configs) // 20)

    def collect(item):
        nonlocal done
        j, p, c = item
        pnl[:, j], cnt[:, j] = p, c
        done += 1
        if progress and (done % step == 0 or done == len(configs)):
            el = time.time() - t0
            print(f"  grid {done}/{len(configs)}  {el:.0f}s elapsed  ~{el / done * (len(configs) - done):.0f}s left",
                  flush=True)

    try:
        if n_jobs == 1 or len(configs) == 1:
            for j in range(len(configs)):
                collect(_one(j))
        else:
            # forkserver, not fork: forking a process that runs Arrow / Numba threads can inherit held locks
            ctx = mp.get_context("forkserver")
            with ctx.Pool(n_jobs, initializer=_init_worker, initargs=(strategy, bars, configs, slip_ticks)) as pool:
                for item in pool.imap_unordered(_one, range(len(configs))):
                    collect(item)
    finally:
        _STATE.clear()
    return GridResult(grid, configs, days, pnl, cnt, slip_ticks)


def plateau(scores: np.ndarray, shape: tuple[int, ...]) -> np.ndarray:
    """Median of each config's score and its +-1 neighbours along every grid axis (configs in product order)."""
    s = np.asarray(scores, float).reshape(shape)
    out = np.empty_like(s)
    for idx in np.ndindex(*shape):
        vals = [s[idx]]
        for ax in range(len(shape)):
            for step in (-1, 1):
                j = list(idx)
                j[ax] += step
                if 0 <= j[ax] < shape[ax]:
                    vals.append(s[tuple(j)])
        out[idx] = np.median(vals)
    return out.ravel()


def day_sharpe(pnl: np.ndarray) -> np.ndarray:
    sd = pnl.std(axis=0, ddof=1)
    return np.where(sd > 0, pnl.mean(axis=0) / np.where(sd > 0, sd, 1), 0.0)


def select(result: GridResult, rows=None, use_plateau: bool = True) -> int:
    """Config index with the best (plateau-smoothed) per-day Sharpe on `rows` (default: all days).
    Ties go to the first config in grid order (np.argmax)."""
    p = result.pnl if rows is None else result.pnl[rows]
    score = day_sharpe(p)
    if use_plateau and len(result.configs) > 1:
        score = plateau(score, result.shape)
    return int(np.argmax(score))


def walk_forward(result: GridResult, min_train_years: int = 3, use_plateau: bool = True) -> dict:
    """Anchored walk-forward: for each year Y after the first `min_train_years`, pick on days before Y,
    record the pick's P&L in Y. Only the concatenated out-of-sample years are evidence."""
    years = result.days.year
    uniq = sorted(set(years))
    rows, pnl, cnt = [], [], []
    for y in uniq[min_train_years:]:
        j = select(result, years < y, use_plateau)
        test = years == y
        p, n = result.pnl[test, j], result.trades[test, j]
        rows.append({"year": y, **{f"param_{k}": v for k, v in result.configs[j].items()}, "trades": int(n.sum()),
                     "net_usd_per_trade": float(p.sum() / n.sum()) if n.sum() else float("nan"),
                     "total_net_usd": float(p.sum())})
        pnl.append(p)
        cnt.append(n)
    if not rows:
        return {"years": pd.DataFrame(), "trades": 0, "net_usd_per_trade": float("nan"), "total_net_usd": 0.0}
    pnl, cnt = np.concatenate(pnl), np.concatenate(cnt)
    return {"years": pd.DataFrame(rows), "trades": int(cnt.sum()), "total_net_usd": float(pnl.sum()),
            "net_usd_per_trade": float(pnl.sum() / cnt.sum()) if cnt.sum() else float("nan"),
            "day_pnl": pnl, "day_trades": cnt}
