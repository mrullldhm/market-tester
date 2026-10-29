"""O10 dev run (experiments/O10/SPEC.md rev 4), dev period only.

    .venv/bin/python experiments/O10/run.py --power   # stage 1: blind power check, no strategy P&L
    .venv/bin/python experiments/O10/run.py --grid    # stage 2: 204 configs, gates, RESULTS.md

Stage 2 refuses to run without a stage-1 PROCEED in data/o10/power.json made by the same spec, simulator and
runner (`bundle_sha256`). Each stage logs a start row before reading data. Stage 2 logs one `dev_start` row per item
(config count) before reading data, then one `dev` row per item. Outputs go to data/o10/ (not in git) and
experiments/O10/RESULTS.md.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from market_tester.common import config, ledger  # noqa: E402
from market_tester.quantum import metrics  # noqa: E402
from strategies.quantum import orb_situations as orb  # noqa: E402
from market_tester.quantum.optimize import GridResult, day_sharpe, walk_forward  # noqa: E402
from market_tester.quantum.periods import DEV, load_period  # noqa: E402
from market_tester.quantum.stats import bootstrap_p, pbo_cscv, romano_wolf  # noqa: E402

OUT = ROOT / "data" / "o10"
RESULTS = config.QUANTUM_EXPERIMENTS_DIR / "O10" / "RESULTS.md"
POWER_MD = config.QUANTUM_EXPERIMENTS_DIR / "O10" / "POWER.md"
SPEC = config.QUANTUM_EXPERIMENTS_DIR / "O10" / "SPEC.md"
FAMILY = "orb"
N_CONFIGS = 204
ALPHA = 0.05 / N_CONFIGS
N_BOOT, N_RW, N_RANDOM, N_POWER_DRAWS = 49_999, 1_999, 999, 20
DEAD_ORB = 1_056_000
BLOCK = (2019, 2023)
CUT = pd.Timestamp("2017-01-01")
ROUND_TRIP = 24.02


def md(df: pd.DataFrame, index: bool = True) -> str:
    """Markdown table without the optional `tabulate` dependency."""
    d = df.reset_index() if index else df
    cell = lambda v: f"{v:,.3f}" if isinstance(v, (float, np.floating)) else str(v)  # noqa: E731
    head = "| " + " | ".join(map(str, d.columns)) + " |\n|" + "---|" * len(d.columns) + "\n"
    return head + "".join("| " + " | ".join(cell(v) for v in row) + " |\n" for row in d.itertuples(index=False))


def reference_config(s: int) -> dict:
    return {"situation": s, "sl": 16 if s in orb.USES_SL else 0, "tp": 30}


def bundle_sha256() -> str:
    """Spec + every market_tester module + this runner: what a stage-1 PROCEED is bound to."""
    return ledger.sha256_files([SPEC, Path(__file__).resolve(), *ledger.code_files()])


def load_days() -> tuple[pd.DataFrame, orb.Days]:
    t0 = time.time()
    print("[O10] loading dev bars ...", flush=True)
    bars = load_period(DEV)
    days = orb.build_days(bars)
    if len(days.calendar_mismatch):
        raise SystemExit(f"calendar says short session but bars run past 14:29: {list(days.calendar_mismatch)}")
    print(f"[O10] {len(bars):,} bars, {len(days.day):,} eligible days, {len(days.short_days)} scheduled short "
          f"sessions, data failures {[str(d.date()) for d in days.data_failures]} ({time.time() - t0:.0f}s)",
          flush=True)
    return bars, days


def power() -> None:
    """SPEC stage 1. Uses trade counts and the benchmark's per-trade sd only; never the strategy's own P&L."""
    code, bundle = ledger.code_sha256(), bundle_sha256()
    start = ledger.append("O10", "decision", "dev", family=FAMILY, verdict="START", code_sha256=code,
                          note=f"power check start, bundle {bundle[:12]}")
    _, days = load_days()
    z = norm.ppf(1 - ALPHA) + norm.ppf(0.8)
    rows = []
    for s in orb.SITUATIONS:
        cfg = reference_config(s)
        ent = orb.entries(days, s, cfg["sl"])  # day, side, fill bar, stop distance: no outcome
        pooled = orb.variance_draws(days, ent, s, cfg["sl"], cfg["tp"], N_POWER_DRAWS, seed=s)
        kept = len(pooled) / (N_POWER_DRAWS * len(ent)) if len(ent) else 0.0
        sd = float(pooled.std(ddof=1)) if kept >= orb.MIN_KEPT and len(pooled) > 1 else float("nan")
        mde = z * sd / np.sqrt(len(ent)) if len(ent) else float("nan")
        rows.append({**cfg, "trades": len(ent), "trade_sd_usd": sd, "mde_usd": mde, "ok": bool(mde <= ROUND_TRIP)})
        print(f"[O10 power] S{s}: {len(ent)} entries, benchmark sd ${sd:.0f}, MDE ${mde:.2f}", flush=True)
    df = pd.DataFrame(rows)
    proceed = int(df["ok"].sum()) >= 4
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "power.json").write_text(json.dumps({"proceed": proceed, "code_sha256": code, "bundle_sha256": bundle,
                                                "start_run_id": start, "rows": rows}, indent=2))
    POWER_MD.write_text(
        f"# O10 stage 1: blind power check\n\nSPEC rev 4 stage 1. Bundle {bundle[:12]}. alpha = 0.05/{N_CONFIGS}, "
        f"power 0.8, z sum {z:.3f}. Entries from `orb.entries` (no exits); sd = per-trade net $ of the "
        f"variance model (`orb.variance_draws`: week-shuffled days, the situation's own delayed-stop exits; "
        f"{N_POWER_DRAWS} draws), not the strategy's P&L.\n\n{md(df, index=False)}\n\n"
        f"Rule: proceed if MDE <= ${ROUND_TRIP} for >= 4 of 6 situations. **{'PROCEED' if proceed else 'STOP'}**\n")
    ledger.append("O10", "decision", "dev", family=FAMILY, statistic="situations_with_mde_le_round_trip",
                  value=int(df["ok"].sum()), verdict="PROCEED" if proceed else "FAIL", code_sha256=code,
                  output=str(POWER_MD), note=f"power check done (start {start}): entry counts + benchmark sd, no strategy P&L")
    print(POWER_MD.read_text(), flush=True)


def best_week_out(t: pd.DataFrame) -> float:
    if t.empty:
        return float("nan")
    week = pd.DatetimeIndex(t["trading_day"]).to_period("W")
    best = t.groupby(week)["net_usd"].sum().idxmax()
    rest = t[week != best]
    return float(rest["net_usd"].mean()) if len(rest) else float("nan")


def describe(t: pd.DataFrame, t2: pd.DataFrame, days: orb.Days, cfg: dict) -> dict:
    s = metrics.summary(t, days.day)
    yr = pd.DatetimeIndex(t["trading_day"]).year
    blk = t[(yr >= BLOCK[0]) & (yr <= BLOCK[1])]
    years = t.groupby(yr)["net_usd"].sum().reindex(sorted(set(days.day.year)), fill_value=0)
    dist = t.loc[t["stop_dist"] >= 0, "stop_dist"]
    beyond = (-t["gross_ticks"] > cfg["sl"]).mean() if cfg["sl"] else float("nan")
    return {**cfg, "trades": s["trades"], "win_rate": s.get("win_rate", np.nan),
            "gross_usd_per_trade": float(t["gross_ticks"].mean() * config.TICK_VALUE) if len(t) else np.nan,
            "net_usd_per_trade": s["net_usd_per_trade"], "net_2tick": float(t2["net_usd"].mean()) if len(t2) else np.nan,
            "profit_factor": s.get("profit_factor", np.nan), "max_dd": s.get("max_drawdown_usd", np.nan),
            "total_net": s["total_net_usd"], "block_trades": len(blk), "block_net": float(blk["net_usd"].sum()),
            "best_week_out": best_week_out(t), "years_pos": f"{int((years > 0).sum())}/{len(years)}",
            "long_net": float(t.loc[t["side"] > 0, "net_usd"].mean()),
            "short_net": float(t.loc[t["side"] < 0, "net_usd"].mean()),
            "loss_beyond_sl": beyond, "window_mae_p90": float(t["window_mae"].quantile(0.9)) if len(t) else np.nan,
            "window_mae_max": float(t["window_mae"].max()) if len(t) else np.nan,
            "stop_q10_50_90": "/".join(str(int(x)) for x in dist.quantile([0.1, 0.5, 0.9])) if len(dist) else "",
            "exits": json.dumps(s.get("exits", {}))}


def lookahead_ok(bars: pd.DataFrame, cfg: dict, full: pd.DataFrame) -> bool:
    """Pick trades before CUT unchanged when every bar from CUT on is removed."""
    part = orb.run(orb.build_days(bars[bars["trading_day"] < CUT]), **cfg)
    cols = ["trading_day", "side", "entry_ticks", "exit_ticks", "slip_sides", "reason", "entry_time", "exit_time"]
    a = full.loc[full["trading_day"] < CUT, cols].reset_index(drop=True)
    return bool(a.equals(part[cols].reset_index(drop=True)))


def grid() -> None:
    p = OUT / "power.json"
    pw = json.loads(p.read_text()) if p.exists() else {}
    if not pw.get("proceed"):
        raise SystemExit("stage 1 power check has not returned PROCEED")
    if pw.get("bundle_sha256") != bundle_sha256():
        raise SystemExit("spec, simulator or runner changed since the power check: rerun stage 1")
    code = ledger.code_sha256()
    spec_sha = ledger.sha256_file(SPEC)
    configs = orb.grid()
    assert len(configs) == N_CONFIGS
    starts = {}
    for s in orb.SITUATIONS:
        n = sum(c["situation"] == s for c in configs)
        starts[s] = ledger.append(orb.ITEMS[s], "dev_start", "dev", family=FAMILY, n_configs=n, code_sha256=code,
                                  note=f"O10 S{s}, SPEC rev 4 sha {spec_sha[:12]}, bundle {pw['bundle_sha256'][:12]}")
    print(f"[O10] {len(configs)} configs logged", flush=True)
    t0 = time.time()
    bars, days = load_days()
    rows, trades_of, yearly = [], {}, {}
    pnl, cnt = np.zeros((len(days.day), len(configs))), np.zeros((len(days.day), len(configs)))
    for q, cfg in enumerate(configs):
        t = orb.backtest(days, **cfg)
        t2 = orb.backtest(days, **cfg, slip_ticks=2)
        pnl[:, q] = orb.day_pnl(t, days)
        cnt[:, q] = t.groupby("trading_day").size().reindex(days.day, fill_value=0).to_numpy()
        trades_of[q] = t
        yearly[q] = t.groupby(pd.DatetimeIndex(t["trading_day"]).year)["net_usd"].sum()
        rows.append({**describe(t, t2, days, cfg), "boot_p": bootstrap_p(pnl[:, q], n_boot=N_BOOT, seed=q)})
        print(f"[O10] {q + 1}/{len(configs)} S{cfg['situation']} sl {cfg['sl']} tp {cfg['tp']}: "
              f"{len(t)} trades, net ${rows[-1]['net_usd_per_trade']:.2f}/trade ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(rows)
    df["day_sharpe"] = day_sharpe(pnl)
    df["bonferroni_p"] = np.minimum(1.0, df["boot_p"] * N_CONFIGS)
    print("[O10] Romano-Wolf, PBO, walk-forward ...", flush=True)
    df["romano_wolf_p"] = romano_wolf(pnl, n_boot=N_RW, seed=0)["p_adj"]
    pbo = pbo_cscv(pnl, n_blocks=16, max_combos=20_000, seed=0)["pbo"]
    res = GridResult({"config": list(range(len(configs)))}, configs, days.day, pnl, cnt, 1)
    wf = walk_forward(res, min_train_years=3, use_plateau=False)

    pick = int(np.argmax(df["day_sharpe"].to_numpy()))
    best = {s: int(df[df["situation"] == s]["day_sharpe"].idxmax()) for s in orb.SITUATIONS}
    df[["random_p", "random_real", "random_null_mean", "random_valid"]] = np.nan
    for q in dict.fromkeys([*best.values(), pick]):
        r = orb.entry_quality(days, trades_of[q], int(df.at[q, "tp"]), n_draws=N_RANDOM, seed=0)
        df.loc[q, ["random_p", "random_real", "random_null_mean", "random_valid"]] = (
            r["p"], r["real"], r["null_mean"], r["valid_draws"])
        df.loc[q, "random_breached"] = r["breached"]
        print(f"[O10] benchmark S{df.at[q, 'situation']} sl {df.at[q, 'sl']} tp {df.at[q, 'tp']}: p {r['p']:.3f}",
              flush=True)
    print("[O10] look-ahead check on the pick ...", flush=True)
    look = lookahead_ok(bars, configs[pick], trades_of[pick])
    pk = df.loc[pick]
    gates = {"trades >= 300": pk["trades"] >= 300, "net > 0": pk["net_usd_per_trade"] > 0,
             "net > 0 at 2 ticks": pk["net_2tick"] > 0, "Bonferroni p <= 0.05": pk["bonferroni_p"] <= 0.05,
             "entry-quality p <= 0.05": pk["random_p"] <= 0.05, "walk-forward OOS > 0": wf["total_net_usd"] > 0,
             "PBO <= 0.3": pbo <= 0.3, "2019-23 block > 0, >= 100 trades": pk["block_net"] > 0 and pk["block_trades"] >= 100,
             "best week out > 0": pk["best_week_out"] > 0, "look-ahead unchanged": look}
    gates = {k: bool(v) if pd.notna(v) else False for k, v in gates.items()}  # NaN fails
    candidate = all(gates.values())

    OUT.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT / "configs.csv", index=False)
    pd.DataFrame(pnl, index=days.day).to_parquet(OUT / "day_pnl.parquet")
    for s in orb.SITUATIONS:
        q = configs.index(reference_config(s))
        trades_of[q].to_parquet(OUT / f"S{s}-tp30-trades.parquet")
        trades_of[best[s]].to_parquet(OUT / f"S{s}-best-trades.parquet")
    trades_of[pick].to_parquet(OUT / "pick-trades.parquet")
    wf["years"].to_csv(OUT / "walk_forward.csv", index=False)
    pd.DataFrame({q: yearly[q] for q in yearly}).T.fillna(0).join(df[["situation", "sl", "tp"]]).to_csv(
        OUT / "configs_by_year.csv")

    per_sit = df.groupby("situation").agg(max_net=("net_usd_per_trade", "max"),
                                          median_net=("net_usd_per_trade", "median"),
                                          median_gross=("gross_usd_per_trade", "median"),
                                          median_trades=("trades", "median"), min_bonf_p=("bonferroni_p", "min"))
    per_sit.insert(0, "sharpe_best_net", [df.at[best[s], "net_usd_per_trade"] for s in per_sit.index])
    cols = ["situation", "sl", "tp", "trades", "win_rate", "gross_usd_per_trade", "net_usd_per_trade", "net_2tick",
            "profit_factor", "max_dd", "block_net", "best_week_out", "years_pos", "long_net", "short_net",
            "loss_beyond_sl", "window_mae_p90", "window_mae_max", "stop_q10_50_90", "day_sharpe", "boot_p",
            "bonferroni_p", "romano_wolf_p", "random_p", "random_real", "random_null_mean"]
    ref = [configs.index(reference_config(s)) for s in orb.SITUATIONS]
    by_year = metrics.by_year(trades_of[pick])
    gate_md = "\n".join(f"| {k} | {'PASS' if v else 'FAIL'} |" for k, v in gates.items())
    text = (f"# O10 dev results (Claude's numbers, not yet replicated by Codex)\n\n"
            f"SPEC rev 4 sha {spec_sha[:12]}, code {code[:12]}. Dev {DEV.first_day.date()}..{DEV.last_day.date()}, "
            f"{len(days.day):,} eligible days ({len(days.short_days)} scheduled short sessions not traded, data "
            f"failures excluded: {[str(d.date()) for d in days.data_failures]}), $24.02 round trip. {N_CONFIGS} configs (Bonferroni is O10-only; dead "
            f"ORB trials {DEAD_ORB:,} not corrected for). PBO over the 204: {pbo:.3f}. Walk-forward OOS "
            f"{wf['trades']} trades, net ${wf['net_usd_per_trade']:.2f}/trade, total ${wf['total_net_usd']:,.0f}.\n\n"
            f"## O10 pick: S{pk['situation']} sl {pk['sl']} tp {pk['tp']} (highest per-day Sharpe)\n\n"
            f"| Gate | Result |\n|---|---|\n{gate_md}\n\n**{'CANDIDATE' if candidate else 'FAIL'}**\n\n"
            f"Pick by year:\n\n{md(by_year)}\n\n"
            f"## Per situation (median = situation without picking)\n\n{md(per_sit)}\n\n"
            f"## Best config per situation (descriptive)\n\n"
            f"{md(df.loc[list(best.values()), cols], index=False)}\n\n"
            f"## Reference configs TP 30 / SL 16 (Codex replication set)\n\n"
            f"{md(df.loc[ref, cols], index=False)}\n\n"
            f"## Walk-forward picks\n\n{md(wf['years'], index=False)}\n\n"
            f"## All 204 configs\n\n{md(df[cols], index=False)}\n")
    RESULTS.write_text(text)
    for s in orb.SITUATIONS:
        q = best[s]
        ledger.append(orb.ITEMS[s], "dev", "dev", family=FAMILY, n_configs=int((df["situation"] == s).sum()),
                      statistic="net_usd_per_trade_best", value=df.at[q, "net_usd_per_trade"],
                      p_value=df.at[q, "bonferroni_p"], verdict="PASS" if (candidate and q == pick) else "FAIL",
                      code_sha256=code, output=str(RESULTS),
                      note=f"start {starts[s]}; situation best sl {df.at[q, 'sl']} tp {df.at[q, 'tp']}"
                           + ("; O10 pick" if q == pick else ""))
    print(per_sit.to_string(), flush=True)
    print(f"[O10] pick S{pk['situation']} sl {pk['sl']} tp {pk['tp']}: {'CANDIDATE' if candidate else 'FAIL'} "
          f"({time.time() - t0:.0f}s)", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--power", action="store_true")
    g.add_argument("--grid", action="store_true")
    args = ap.parse_args()
    power() if args.power else grid()
