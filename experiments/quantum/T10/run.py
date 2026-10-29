"""T10 / T102 runs (experiments/T10/SPEC.md rev 3). Order is enforced: global file, power, then dev.

    .venv/bin/python experiments/T10/run.py --build-global   # global_sharpes.csv from P101-P110 dev reports
    .venv/bin/python experiments/T10/run.py --power          # POWER.md: no strategy P&L (unsigned returns)
    .venv/bin/python experiments/T10/run.py --dev            # the one dev run + D gate + SUMMARY.md

Power uses the signal inputs (which days trade, A vs D) and the absolute size of the front 10:00 -> flat move
on those days; every sign is discarded before use, so no strategy P&L is computed or printed.
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from market_tester.common import config, ledger  # noqa: E402
from market_tester.quantum.engine import Market, run  # noqa: E402
from market_tester.quantum.metrics import trading_days  # noqa: E402
from market_tester.quantum.periods import DEV, load_period  # noqa: E402
from market_tester.quantum.report import evaluate  # noqa: E402
from strategies.quantum.spread_lead import SpreadLead  # noqa: E402
from market_tester.quantum.stats import bootstrap_ci, bootstrap_p  # noqa: E402

HERE = config.QUANTUM_EXPERIMENTS_DIR / "T10"
GLOBAL = HERE / "global_sharpes.csv"
POWER = HERE / "POWER.md"
POWER_JSON = HERE / "POWER.json"
SUMMARY = HERE / "SUMMARY.md"
ITEM, FAMILY = "T102", "spread_lead"
GLOBAL_TRIALS = 25
N_BOOT = 49_999
N_RANDOM = 1999
P_PROFIT = 0.002  # Bonferroni 0.05 / 25
D_MIN, D_P = 150, 0.05
H_D_MIN, H_D_P, H_P = 40, 0.10, 0.05  # holdout gates (SPEC rev 3)
COST = 2 * config.FEE_PER_SIDE + 2 * config.SLIPPAGE_TICKS_PER_SIDE * config.TICK_VALUE  # $4.02 baseline (TopstepX)
STRESS = 2 * config.TICK_VALUE  # one more tick per side
HOLDOUT_PROXY = ("2021-03-01", "2023-12-31")  # last ~2.8 dev years stand in for the holdout's length
HYPOTHESIS = ("experiments/T10/SPEC.md rev 3, T102 (spread_lead): front 08:59-09:59 move not explained by the "
              "second month predicts the front 10:00-14:29")


def build_global() -> None:
    rows = []
    for k in range(101, 111):
        item = f"P{k}"
        dev = sorted((config.QUANTUM_EXPERIMENTS_DIR / item).glob("*-dev.json"))
        if len(dev) != 1:
            raise SystemExit(f"{item}: expected one dev report, found {len(dev)}")
        r = json.loads(dev[0].read_text())
        rows.append({"item_id": item, "report_json": str(dev[0].relative_to(ROOT)),
                     "report_sha256": ledger.sha256_file(dev[0]), "day_sharpe": r["dsr"]["sr"]})
    with open(GLOBAL, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {GLOBAL} ({len(rows)} rows), sha256 {ledger.sha256_file(GLOBAL)[:16]}", flush=True)


def load_global() -> dict:
    """Re-check every source report hash; returns evaluate's `global_dsr`."""
    g = pd.read_csv(GLOBAL)
    for row in g.itertuples():
        path = ROOT / row.report_json
        if not path.exists() and row.report_json.startswith("experiments/P"):  # pre-split path (experiments/MOVED.md)
            path = config.QUANTUM_EXPERIMENTS_DIR / Path(row.report_json).relative_to("experiments")
        if ledger.sha256_file(path) != row.report_sha256:
            raise SystemExit(f"{row.report_json} changed since global_sharpes.csv was built")
    return {"n_trials": GLOBAL_TRIALS, "sharpes": g["day_sharpe"].tolist(),
            "source": f"{GLOBAL.relative_to(ROOT)} sha256 {ledger.sha256_file(GLOBAL)}"}


def subset_day_pnl(trades: pd.DataFrame, days, mask=None) -> np.ndarray:
    t = trades if mask is None else trades[mask]
    return t.groupby("trading_day")["net_usd"].sum().reindex(days, fill_value=0.0).to_numpy()


def power(n_sims: int = 100, ks=range(0, 21, 2)) -> None:
    t0 = time.time()
    print("[power] loading dev bars with the second month", flush=True)
    bars = load_period(DEV, with_second=True)
    s = SpreadLead()
    table = s.day_table(bars)
    sig = s.signals(bars)
    sig.side = np.abs(sig.side)  # every signal day long: only |move| is used below
    market = Market.from_bars(bars)
    tr = run(market, sig)
    size = np.abs(tr["gross_ticks"].to_numpy(float)) * config.TICK_VALUE  # unsigned move per trade, $
    del sig, tr["gross_ticks"]
    days = pd.DatetimeIndex(trading_days(bars))
    day = pd.DatetimeIndex(tr["trading_day"])
    row = table.loc[day]
    is_d = (np.sign(row["r"]) != np.sign(row["d0"])).to_numpy()
    in_h = (day >= HOLDOUT_PROXY[0]) & (day <= HOLDOUT_PROXY[1])
    h_days = days[(days >= HOLDOUT_PROXY[0]) & (days <= HOLDOUT_PROXY[1])]
    pos = np.searchsorted(days, day)
    h_pos = np.searchsorted(h_days, day[in_h])
    print(f"[power] {len(size)} trades ({is_d.sum()} D), holdout proxy {in_h.sum()} ({(is_d & in_h).sum()} D); "
          f"{n_sims} sims x {len(list(ks))} edges, B={N_BOOT}", flush=True)

    def series(x, mask, p, n):
        out = np.zeros(n)
        np.add.at(out, p[mask], x[mask])
        return out

    rng = np.random.default_rng(0)
    res = []
    for k in ks:
        dev_pass = d_pass = h_pass = 0
        for i in range(n_sims):
            net = rng.choice([-1.0, 1.0], len(size)) * size + k * config.TICK_VALUE
            all_ok = (len(net) >= 300 and net.mean() > 0 and (net - STRESS).mean() > 0
                      and bootstrap_p(series(net, np.ones(len(net), bool), pos, len(days)), N_BOOT, seed=i) <= P_PROFIT)
            nd = net[is_d]
            d_ok = (len(nd) >= D_MIN and nd.mean() > 0
                    and bootstrap_p(series(net, is_d, pos, len(days)), N_BOOT, seed=10_000 + i) <= D_P)
            nh, nhd = net[in_h], net[in_h & is_d]
            h_ok = (len(nh) and nh.mean() > 0 and (nh - STRESS).mean() > 0 and len(nhd) >= H_D_MIN and nhd.mean() > 0
                    and bootstrap_p(series(net[in_h], np.ones(in_h.sum(), bool), h_pos, len(h_days)), N_BOOT,
                                    seed=20_000 + i) <= H_P
                    and bootstrap_p(series(net[in_h], is_d[in_h], h_pos, len(h_days)), N_BOOT,
                                    seed=30_000 + i) <= H_D_P)
            dev_pass += all_ok
            d_pass += all_ok and d_ok
            h_pass += h_ok
        res.append({"net_edge_ticks": k, "dev_all_gates": dev_pass / n_sims, "dev_all_and_D": d_pass / n_sims,
                    "holdout_proxy": h_pass / n_sims})
        print(f"[power] edge {k:>2} ticks: dev {dev_pass}/{n_sims}, dev+D {d_pass}/{n_sims}, "
              f"holdout {h_pass}/{n_sims} ({time.time() - t0:.0f}s)", flush=True)
    df = pd.DataFrame(res)
    k80 = df.loc[df["dev_all_and_D"] >= 0.8, "net_edge_ticks"].min()
    text = (f"# T102 power (SPEC rev 3), before any strategy P&L\n\n"
            f"Inputs: {len(size)} dev signal days ({is_d.sum()} D), unsigned front 10:00->flat move; random sides; "
            f"planted net edge k ticks/trade; B = {N_BOOT}, mean block 5 days; {n_sims} sims per k. Not simulated: "
            f"random-entry, walk-forward, 2019-23 block, DSR, look-ahead (all only make a pass harder).\n"
            f"Holdout proxy = signal days {HOLDOUT_PROXY[0]}..{HOLDOUT_PROXY[1]} ({in_h.sum()} trades, "
            f"{(is_d & in_h).sum()} D).\n\n"
            "| net edge ticks | dev all gates | dev + D gate | holdout proxy |\n|---|---|---|---|\n"
            + "".join(f"| {r.net_edge_ticks} | {r.dev_all_gates:.2f} | {r.dev_all_and_D:.2f} | {r.holdout_proxy:.2f} |\n"
                      for r in df.itertuples())
            + f"\nk at 80% power (dev + D): {k80 if pd.notna(k80) else '> ' + str(max(ks))} net ticks. "
            + ("Above 10: a FAIL is reported as underpowered.\n" if not pd.notna(k80) or k80 > 10 else "\n"))
    POWER.write_text(text)
    POWER_JSON.write_text(json.dumps({"k80_dev_and_D": None if pd.isna(k80) else int(k80), "max_k": max(ks),
                                      "underpowered": bool(pd.isna(k80) or k80 > 10),
                                      "table": df.to_dict("records")}, indent=2))
    ledger.append(ITEM, "decision", "dev", family=FAMILY, code_sha256=ledger.code_sha256(), output=str(POWER),
                  note="power simulation: signal inputs + unsigned moves, no strategy P&L")
    print(text, flush=True)


def best_week_out(trades: pd.DataFrame) -> float:
    week = pd.DatetimeIndex(trades["trading_day"]).to_period("W")
    best = trades.groupby(week)["net_usd"].sum().idxmax()
    rest = trades[week != best]
    return float(rest["net_usd"].mean()) if len(rest) else float("nan")


def dev() -> None:
    if not POWER_JSON.exists() or not GLOBAL.exists():
        raise SystemExit("run --build-global and --power first (SPEC rev 3 order)")
    if ledger.configs_tried(FAMILY):
        raise SystemExit(f"family {FAMILY} already has a dev run: one run only")
    g = load_global()
    s = SpreadLead()
    r = evaluate(s, {}, item_id=ITEM, family=FAMILY, kind="dev", hypothesis=HYPOTHESIS, n_boot=N_BOOT,
                 n_random=N_RANDOM, gates={"romano_wolf_p_max": P_PROFIT}, global_dsr=g)
    trades = r["trades"]
    days = pd.DatetimeIndex(r["grid_result"].days)
    bars_table = s.day_table(load_period(DEV, with_second=True))
    row = bars_table.loc[pd.DatetimeIndex(trades["trading_day"])]
    is_d = (np.sign(row["r"]) != np.sign(row["d0"])).to_numpy()
    d, a = trades[is_d], trades[~is_d]
    d_p = bootstrap_p(subset_day_pnl(trades, days, is_d), N_BOOT, seed=1) if len(d) > 1 else float("nan")
    d_ok = len(d) >= D_MIN and d["net_usd"].mean() > 0 and d_p <= D_P
    traded_days = trades["trading_day"].nunique()
    bwo = best_week_out(trades)
    years = trades.groupby(pd.DatetimeIndex(trades["trading_day"]).year)["net_usd"].sum()
    candidate = r["verdict"] == "PASS" and d_ok and traded_days >= 200 and bwo > 0

    def line(name, t, mask=None):
        if not len(t):
            return f"| {name} | 0 | | | |\n"
        sub = trades if mask is None else trades[mask]
        n = sub.groupby("trading_day").size().reindex(days, fill_value=0).to_numpy()
        ci = bootstrap_ci(subset_day_pnl(trades, days, mask), n, n_boot=N_BOOT, seed=2)["per_trade_ci"]
        return (f"| {name} | {len(t)} | {(t['gross_ticks'] * config.TICK_VALUE).mean():,.2f} | "
                f"{t['net_usd'].mean():,.2f} | {ci[0]:,.2f} .. {ci[1]:,.2f} |\n")

    pw = json.loads(POWER_JSON.read_text())
    conclusion = ("candidate" if candidate else
                  "FAIL (underpowered: k80 " + str(pw["k80_dev_and_D"] or f"> {pw['max_k']}") + " net ticks)"
                  if pw["underpowered"] else "FAIL (no edge at the power of this test)")

    text = (f"# T102 dev summary (Claude's numbers, not yet replicated by Codex)\n\n"
            f"Spec rev 3, code {r['code_sha256'][:12]}, report `{r['report']}`. Dev verdict **{r['verdict']}**.\n\n"
            "| subset | trades | gross $/trade | net $/trade | 95% block-bootstrap CI net $/trade |\n|---|---|---|---|---|\n"
            + line("all", trades) + line("A (agrees with first-hour sign)", a, ~is_d) + line("D (disagrees)", d, is_d)
            + f"\nD gate: trades {len(d)} (>= {D_MIN}), net > 0, p {d_p:.4g} (<= {D_P}): **{'PASS' if d_ok else 'FAIL'}**\n"
            f"Concentration: traded days {traded_days} (>= 200), net after best week out {bwo:,.2f} (> 0), "
            f"years net > 0: {(years > 0).sum()}/{len(days.year.unique())}\n"
            f"Corr(r, d0) on signal days: {np.corrcoef(row['r'], row['d0'])[0, 1]:.3f}\n\n"
            f"**Candidate: {'YES' if candidate else 'NO'}** — {conclusion}\n")
    SUMMARY.write_text(text)
    print(text, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build-global", action="store_true")
    g.add_argument("--power", action="store_true")
    g.add_argument("--dev", action="store_true")
    args = ap.parse_args()
    if args.build_global:
        build_global()
    elif args.power:
        power()
    else:
        dev()
