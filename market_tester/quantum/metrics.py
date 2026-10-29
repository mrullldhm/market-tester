"""Performance numbers from a trades table (engine.with_costs output).

The trading day is the unit of time: a trade's P&L belongs to its ENTRY trading day, and days without a
trade count as 0 (otherwise Sharpe and drawdown flatter sparse strategies).
"""

import math

import numpy as np
import pandas as pd

TRADING_DAYS_PER_YEAR = 252


def trading_days(bars: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.unique(bars["trading_day"])).sort_values()


def daily(trades: pd.DataFrame, days: pd.DatetimeIndex, col: str = "net_usd") -> pd.DataFrame:
    """Per trading day: `pnl` (sum of `col`, 0 without trades) and `n` (trades), indexed by every day."""
    g = trades.groupby("trading_day")[col]
    out = pd.DataFrame({"pnl": g.sum(), "n": g.size()}).reindex(days, fill_value=0)
    out.index.name = "trading_day"
    return out


def max_drawdown(pnl) -> float:
    """Largest peak-to-trough fall of the cumulative sum (>= 0, in the units of pnl). Start = 0 equity."""
    equity = np.concatenate([[0.0], np.cumsum(np.asarray(pnl, float))])
    return float((np.maximum.accumulate(equity) - equity).max())


def sharpe(day_pnl, annual: bool = True) -> float:
    x = np.asarray(day_pnl, float)
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    if sd == 0:
        return float("nan")
    return float(x.mean() / sd * (math.sqrt(TRADING_DAYS_PER_YEAR) if annual else 1.0))


def summary(trades: pd.DataFrame, days: pd.DatetimeIndex) -> dict:
    n = len(trades)
    d = daily(trades, days)
    if n == 0:
        return {"trades": 0, "days": len(days), "net_usd_per_trade": float("nan"), "total_net_usd": 0.0}
    net = trades["net_usd"]
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    years = len(days) / TRADING_DAYS_PER_YEAR
    return {
        "trades": n,
        "days": len(days),
        "trades_per_year": n / years if years else float("nan"),
        "net_usd_per_trade": float(net.mean()),
        "gross_ticks_per_trade": float(trades["gross_ticks"].mean()),
        "net_ticks_per_trade": float(trades["net_ticks"].mean()),
        "total_net_usd": float(net.sum()),
        "win_rate": float((net > 0).mean()),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
        "sharpe_annual": sharpe(d["pnl"]),
        "max_drawdown_usd": max_drawdown(net.to_numpy()),
        "long_share": float((trades["side"] > 0).mean()),
        "exits": trades["reason"].value_counts().to_dict(),
    }


def by_group(trades: pd.DataFrame, key) -> pd.DataFrame:
    g = trades.groupby(key)
    return pd.DataFrame({"trades": g.size(), "net_usd_per_trade": g["net_usd"].mean(),
                         "gross_ticks_per_trade": g["gross_ticks"].mean(), "total_net_usd": g["net_usd"].sum()})


def by_year(trades: pd.DataFrame) -> pd.DataFrame:
    return by_group(trades, pd.DatetimeIndex(trades["trading_day"]).year.rename("year"))


def by_side(trades: pd.DataFrame) -> pd.DataFrame:
    return by_group(trades, trades["side"].map({1: "long", -1: "short"}).rename("side"))
