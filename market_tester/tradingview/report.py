"""TradingView Strategy Tester numbers from a trade list (formulas frozen in SPEC.md, Report)."""

import numpy as np
import pandas as pd

from ..common.config import TICK
from .run import COMMISSION_PER_ORDER, INITIAL_CAPITAL

RF_MONTHLY = 0.02 / 12


def _ratio(a, b):
    return None if b == 0 else float(a / b)


def drawdown_runup(t: pd.DataFrame) -> tuple[float, float, float, float]:
    """TV intrabar max drawdown / run-up ($, % of the anchor): anchored to closed equity before each entry."""
    eq_after = INITIAL_CAPITAL + t["net_usd"].cumsum().to_numpy()
    eq_before = np.r_[INITIAL_CAPITAL, eq_after[:-1]]
    peak_before = np.maximum.accumulate(eq_before)
    low_before = np.minimum.accumulate(eq_before)
    trough = eq_before - t["drawdown_usd"].to_numpy() - COMMISSION_PER_ORDER
    top = eq_before + t["runup_usd"].to_numpy() - COMMISSION_PER_ORDER
    dd = peak_before - np.minimum(trough, eq_after)
    ru = np.maximum(top, eq_after) - low_before
    if not len(t):
        return 0.0, 0.0, 0.0, 0.0
    i, j = int(np.argmax(dd)), int(np.argmax(ru))
    return float(dd[i]), float(dd[i] / peak_before[i] * 100), float(ru[j]), float(ru[j] / low_before[j] * 100)


def monthly_returns(t: pd.DataFrame, first, last) -> pd.Series:
    """Closed-equity return of every calendar month (UTC exit time) from `first` to `last`; no trade = 0."""
    months = pd.period_range(pd.Timestamp(first).to_period("M"), pd.Timestamp(last).to_period("M"), freq="M")
    pnl = t.groupby(pd.to_datetime(t["exit_time"]).dt.to_period("M"))["net_usd"].sum().reindex(months, fill_value=0)
    eq_end = INITIAL_CAPITAL + pnl.cumsum()
    eq_start = eq_end.shift(1, fill_value=INITIAL_CAPITAL)
    return pnl / eq_start


def sharpe_sortino(r: pd.Series) -> tuple[float | None, float | None]:
    if not len(r):
        return None, None
    ex = r.mean() - RF_MONTHLY
    sd = r.std(ddof=0)
    down = np.sqrt(np.mean(np.minimum(0.0, r - RF_MONTHLY) ** 2))
    return _ratio(ex, sd), _ratio(ex, down)


def summary(t: pd.DataFrame, first_bar_time, last_bar_time, last_close_ticks: int) -> dict:
    """Performance Summary + Trades Analysis of one config."""
    net = t["net_usd"]
    win, loss = net[net > 0], net[net < 0]
    gp, gl = float(win.sum()), float(loss.sum())
    dd, dd_pct, ru, ru_pct = drawdown_runup(t)
    sharpe, sortino = sharpe_sortino(monthly_returns(t, first_bar_time, last_bar_time))
    bh = None
    if len(t):
        first_px = t["entry_ticks"].iloc[0]
        bh = float((last_close_ticks - first_px) / first_px * 100)
    out = {
        "net_profit": float(net.sum()), "net_profit_pct": float(net.sum() / INITIAL_CAPITAL * 100),
        "gross_profit": gp, "gross_loss": gl, "profit_factor": _ratio(gp, -gl),
        "max_drawdown": dd, "max_drawdown_pct": dd_pct, "max_runup": ru, "max_runup_pct": ru_pct,
        "sharpe": sharpe, "sortino": sortino,
        "buy_hold_pct": bh, "buy_hold_usd": None if bh is None else bh / 100 * INITIAL_CAPITAL,
        "commission_paid": float(t["commission"].sum()),
        "trades": int(len(t)), "winners": int(len(win)), "losers": int(len(loss)),
        "even": int((net == 0).sum()), "pct_profitable": _ratio(len(win) * 100, len(t)),
        "avg_trade": _ratio(net.sum(), len(t)), "avg_win": _ratio(gp, len(win)), "avg_loss": _ratio(gl, len(loss)),
        "largest_win": float(win.max()) if len(win) else None,
        "largest_loss": float(loss.min()) if len(loss) else None,
        "avg_bars": _ratio(t["bars"].sum(), len(t)),
        "long_trades": int((t["dir"] > 0).sum()), "long_net": float(net[t["dir"] > 0].sum()),
        "short_trades": int((t["dir"] < 0).sum()), "short_net": float(net[t["dir"] < 0].sum()),
    }
    out["win_loss_ratio"] = None if out["avg_win"] is None or not out["avg_loss"] else out["avg_win"] / -out["avg_loss"]
    return out


def by_year(t: pd.DataFrame) -> pd.DataFrame:
    y = pd.to_datetime(t["trading_day"]).dt.year
    g = t.groupby(y)["net_usd"]
    return pd.DataFrame({"trades": g.size(), "net": g.sum(), "avg": g.mean(),
                         "win_pct": t.assign(w=t["net_usd"] > 0).groupby(y)["w"].mean() * 100})


def list_of_trades(t: pd.DataFrame) -> pd.DataFrame:
    """TV 'List of trades' layout: an entry row and an exit row per trade, plus the tester's extra columns."""
    rows = []
    cum = t["net_usd"].cumsum().to_numpy()
    for n, r in enumerate(t.itertuples(index=False), 1):
        side = "Long" if r.dir > 0 else "Short"
        common = {"Trade #": n, "Contracts": 1, "Profit": round(r.net_usd, 2), "Cum. Profit": round(cum[n - 1], 2),
                  "Run-up": r.runup_usd, "Drawdown": r.drawdown_usd, "raw_symbol": r.raw_symbol,
                  "stop": round(r.stop_ticks * TICK, 2), "target": round(r.target_ticks * TICK, 2),
                  "submitted": r.submit_time,
                  "commission": r.commission, "exit_reason": r.exit_reason}
        rows.append({"Type": f"Exit {side}", "Signal": r.exit_reason, "Date/Time": r.exit_time,
                     "Price": round(r.exit_ticks * TICK, 2), **common})
        rows.append({"Type": f"Entry {side}", "Signal": f"{side} {r.entry_type}", "Date/Time": r.entry_time,
                     "Price": round(r.entry_ticks * TICK, 2), **common})
    return pd.DataFrame(rows)
