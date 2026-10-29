"""ORB-TV run (SPEC.md): 170 configs, full history, TradingView tester, $50,000, 1 CL.

    .venv/bin/python experiments/tradingview/ORB/run.py

Ledger first (holdout_look, tv_start per item), then data, then every config with live progress.
"""

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from market_tester.common import config, ledger  # noqa: E402
from market_tester.common.data import EXT_FILE  # noqa: E402
from market_tester.common.periods import HOLDOUT, load_range  # noqa: E402
from market_tester.tradingview import report  # noqa: E402
from market_tester.tradingview.run import Chart, simulate_config, trade_frame  # noqa: E402
from strategies.tradingview import orb  # noqa: E402

HERE = config.TV_EXPERIMENTS_DIR / "ORB"
SPEC = HERE / "SPEC.md"
TESTER_SPEC = ROOT / "market_tester" / "tradingview" / "SPEC.md"
FIRST_DAY, LAST_DAY = pd.Timestamp("2010-06-07"), pd.Timestamp("2026-12-31")  # last = last bar on disk


def bundle_sha256() -> str:
    return ledger.sha256_files([*ledger.code_files(), Path(__file__).resolve(), SPEC, TESTER_SPEC])


def label(cfg: dict) -> str:
    return f"S{cfg['situation']} TP{cfg['tp']}" + (f" SL{cfg['sl']}" if cfg["sl"] else "")


def money(x) -> str:
    return "n/a" if x is None else f"{x:,.2f}"


def table(df: pd.DataFrame) -> str:
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for r in df.itertuples(index=False):
        out.append("| " + " | ".join(f"{v:,.2f}" if isinstance(v, float) else str(v) for v in r) + " |")
    return "\n".join(out)


def main():
    grid = orb.grid()
    code = bundle_sha256()
    data_files = sorted(config.BARS_DIR.glob("year=*/cl.parquet")) + [EXT_FILE]
    data_sha = ledger.sha256_files(data_files)
    look = ledger.append("TV10", "holdout_look", "holdout", family="orb", code_sha256=code,
                         note=f"owner approved 2026-10-29: TradingView ORB run over full history reads holdout "
                              f"{HOLDOUT.first_day.date()}..{HOLDOUT.last_day.date()} and forward bars to the latest "
                              f"(Databento + Massive extension); data sha {data_sha[:12]}")
    starts = {}
    for s in orb.SITUATIONS:
        n = sum(c["situation"] == s for c in grid)
        starts[s] = ledger.append(orb.ITEMS[s], "tv_start", "full", family="orb", n_configs=n, code_sha256=code,
                                  note=f"ORB-TV S{s}, bundle {code[:12]}, data {data_sha[:12]}, holdout look {look}")
    print(f"[ORB-TV] ledger {look}, starts {list(starts.values())}; loading bars...", flush=True)
    t0 = time.time()
    bars = load_range(FIRST_DAY, LAST_DAY, holdout_look=look, extend=True)
    chart = Chart.from_bars(bars)
    strat = orb.Orb()
    feats = strat.prepare(bars)
    excluded = feats[2]["excluded_days"]
    first_t, last_t = chart.ts[0], chart.ts[-1]
    print(f"[ORB-TV] {len(bars):,} bars, days {bars.trading_day.min().date()}..{bars.trading_day.max().date()}, "
          f"excluded {excluded.value_counts().to_dict()} ({time.time() - t0:.0f}s)", flush=True)
    eia = bars["eia_block"].to_numpy()
    rows, frames = [], {}
    for i, cfg in enumerate(grid, 1):
        t = trade_frame(chart, simulate_config(chart, strat, feats, cfg))
        t1 = trade_frame(chart, simulate_config(chart, strat, feats, cfg, slip_ticks=1))
        s = report.summary(t, first_t, last_t, int(chart.c[-1]))
        hold = t["trading_day"] >= HOLDOUT.first_day
        rows.append({"situation": cfg["situation"], "tp": cfg["tp"], "sl": cfg["sl"], "config": label(cfg), **s,
                     "net_2010_2023": float(t.loc[~hold, "net_usd"].sum()),
                     "net_2024_plus": float(t.loc[hold, "net_usd"].sum()),
                     "net_slip1": float(t1["net_usd"].sum()),
                     "eia_trades": int(eia[t["entry_bar"].to_numpy()].sum()),
                     "late_flat": int(((t["exit_reason"] == "close") & (t["exit_minute"] > orb.FLAT_FIRST)).sum()),
                     "overnight_failure": int((t["exit_reason"] == "day_end").sum()),
                     "end_of_data": int((t["exit_reason"] == "end").sum())})
        frames[label(cfg)] = t
        if i % 10 == 0 or i == len(grid):
            print(f"[ORB-TV] {i}/{len(grid)} configs, last {label(cfg)} net {s['net_profit']:,.0f} "
                  f"({time.time() - t0:.0f}s)", flush=True)
    opt = pd.DataFrame(rows).sort_values("net_profit", ascending=False).reset_index(drop=True)
    HERE.mkdir(parents=True, exist_ok=True)
    opt.to_csv(HERE / "optimizer.csv", index=False)
    excluded.rename("reason").rename_axis("trading_day").to_csv(HERE / "excluded_days.csv")
    best = opt.groupby("situation", sort=True).head(1).sort_values("situation")
    base = [c for c in grid if c["tp"] == 30 and c["sl"] in (0, 16)]
    keep = {*best["config"], *map(label, base)}
    (HERE / "trades").mkdir(exist_ok=True)
    for name in keep:
        t = frames[name]
        stem = name.replace(" ", "_")
        report.list_of_trades(t).to_csv(HERE / "trades" / f"{stem}.csv", index=False)
        eq = t[["exit_time", "net_usd"]].assign(equity=report.INITIAL_CAPITAL + t["net_usd"].cumsum())
        eq.to_csv(HERE / "trades" / f"{stem}_equity.csv", index=False)
    (HERE / "summaries.json").write_text(json.dumps(opt.to_dict("records"), indent=2, default=str))
    (HERE / "best.json").write_text(json.dumps(best.to_dict("records"), indent=2, default=str))
    odd = pd.concat([frames[label(c)].assign(config=label(c)) for c in grid])
    odd = odd[(odd["exit_reason"].isin(["day_end", "end"]))
              | ((odd["exit_reason"] == "close") & (odd["exit_minute"] > orb.FLAT_FIRST))]
    odd.to_csv(HERE / "late_or_overnight_trades.csv", index=False)
    for r in best.itertuples():
        ledger.append(orb.ITEMS[r.situation], "tv_run", "full", family="orb", n_configs=len(
            [c for c in grid if c["situation"] == r.situation]), statistic="net_profit_best", value=r.net_profit,
            verdict="EXPLORATORY", code_sha256=code, output=str((HERE / "RESULTS.md").relative_to(ROOT)),
            note=f"start {starts[r.situation]}; best {r.config}; trades {r.trades}; PF {r.profit_factor}")
    write_results(opt, best, frames, excluded, code, data_sha, look, bars)
    print(f"[ORB-TV] done in {time.time() - t0:.0f}s -> {HERE / 'RESULTS.md'}", flush=True)


def write_results(opt, best, frames, excluded, code, data_sha, look, bars):
    keys = [("net_profit", "Net profit $"), ("net_profit_pct", "Net profit %"), ("gross_profit", "Gross profit"),
            ("gross_loss", "Gross loss"), ("profit_factor", "Profit factor"), ("max_drawdown", "Max drawdown $"),
            ("max_drawdown_pct", "Max drawdown %"), ("max_runup", "Max run-up $"), ("sharpe", "Sharpe (monthly)"),
            ("sortino", "Sortino (monthly)"), ("trades", "Total closed trades"), ("pct_profitable", "% profitable"),
            ("avg_trade", "Avg trade $"), ("avg_win", "Avg win $"), ("avg_loss", "Avg loss $"),
            ("win_loss_ratio", "Ratio avg win / avg loss"), ("largest_win", "Largest win $"),
            ("largest_loss", "Largest loss $"), ("avg_bars", "Avg bars in trade"), ("commission_paid", "Commission $"),
            ("long_trades", "Long trades"), ("long_net", "Long net $"), ("short_trades", "Short trades"),
            ("short_net", "Short net $"), ("net_2010_2023", "Net 2010-2023 $"), ("net_2024_plus", "Net 2024+ $"),
            ("net_slip1", "Net, 1-tick slippage $"), ("buy_hold_pct", "Buy & hold % (TV-style)"),
            ("eia_trades", "Trades entered in EIA ±15 min"), ("late_flat", "Late flats (after 16:10)"),
            ("overnight_failure", "Overnight failures"), ("end_of_data", "End-of-data exits")]
    head = "| Metric | " + " | ".join(best["config"]) + " |\n|---|" + "---|" * len(best) + "\n"
    body = ""
    for k, name in keys:
        vals = [best[k].iloc[j] for j in range(len(best))]
        body += f"| {name} | " + " | ".join(
            "n/a" if v is None or (isinstance(v, float) and np.isnan(v)) else
            (f"{v:,.2f}" if isinstance(v, (float, np.floating)) else str(v)) for v in vals) + " |\n"
    years = []
    for name in best["config"]:
        y = report.by_year(frames[name])["net"].rename(name)
        years.append(y)
    yt = pd.concat(years, axis=1).fillna(0.0).round(0)
    yt.index.name = "year"
    cols = ["config", "net_profit", "profit_factor", "trades", "pct_profitable", "max_drawdown", "sharpe",
            "net_2010_2023", "net_2024_plus", "net_slip1"]
    opt_t = opt[cols].copy()
    profitable = opt[opt["net_profit"] > 0]
    lines = [
        "# ORB-TV results (TradingView tester, exploratory)", "",
        f"Spec `SPEC.md` rev 2; tester `market_tester/tradingview/SPEC.md` rev 2. Bundle {code[:12]}, data "
        f"{data_sha[:12]}, holdout look `{look}`. Bars {bars.trading_day.min().date()}..{bars.trading_day.max().date()}"
        f" (CL.v.0 1m: Databento to 2026-10-13, Massive extension after it, see data/manifests/massive_ext.json; TV "
        f"execution semantics, not CL1! data parity). $50,000, 1 CL, commission $2.01 per "
        f"order ($4.02 round trip), slippage 0.", "",
        "**Read first.** Best of 170 configs on the same data it is judged on: an in-sample pick, not proof of edge. "
        "The ORB family was tested before on dev (quantum tester O10, 204 configs) and the 2024+ holdout is now seen. "
        "Codex replication pending (`REPLICATION.md`).", "",
        f"Profitable configs: **{len(profitable)} / {len(opt)}** after $4.02 round trip; "
        f"{int((opt['net_slip1'] > 0).sum())} / {len(opt)} with 1-tick slippage.", "",
        "## Best config per situation (Strategy Tester summary)", "", head + body,
        "## Net $ by year (best config per situation)", "", table(yt.reset_index()), "",
        "## Optimizer (all 170, ranked by net profit)", "", table(opt_t), "",
        f"## Excluded days ({len(excluded)})", "",
        ", ".join(f"{k}: {v}" for k, v in excluded.value_counts().items()) + " (dates in `excluded_days.csv`).", "",
        "Late flats / overnight failures, every config: `late_or_overnight_trades.csv`.", "",
        "Files: `optimizer.csv`, `summaries.json` (all 170), `best.json`, `trades/<config>.csv` (TV list of trades) and `_equity.csv` for the best "
        "config per situation and the TP30 / SL16 baseline per situation.",
    ]
    (HERE / "RESULTS.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
