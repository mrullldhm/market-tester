"""T20 / T201 runs (experiments/T20/SPEC.md rev 2). Order is enforced: novelty, power, then dev.

    .venv/bin/python experiments/T20/run.py --novelty   # NOVELTY.json: side agreement with P101 / P102 (no P&L)
    .venv/bin/python experiments/T20/run.py --power     # POWER.md / POWER.json: unsigned moves only (no P&L)
    .venv/bin/python experiments/T20/run.py --dev       # the one dev run + D gate + SUMMARY.md

Power discards every return sign before use; random sides are common across k (same seed per sim index).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy.stats import beta

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from market_tester.common import config, ledger  # noqa: E402
from market_tester.quantum.engine import Market, run  # noqa: E402
from market_tester.quantum.metrics import trading_days  # noqa: E402
from market_tester.quantum.periods import DEV, load_period  # noqa: E402
from strategies.quantum.published import IntradayMomentum, NoiseArea  # noqa: E402
from market_tester.quantum.report import evaluate  # noqa: E402
from strategies.quantum.spread_lead import SpreadLeadHour  # noqa: E402
from market_tester.quantum.stats import bootstrap_ci, bootstrap_p, deflated_sharpe  # noqa: E402

HERE = config.QUANTUM_EXPERIMENTS_DIR / "T20"
T10_RUN = config.QUANTUM_EXPERIMENTS_DIR / "T10" / "run.py"
NOVELTY = HERE / "NOVELTY.json"
POWER, POWER_JSON = HERE / "POWER.md", HERE / "POWER.json"
SUMMARY = HERE / "SUMMARY.md"
ITEM, FAMILY = "T201", "spread_lead"
N_BOOT, N_RANDOM = 49_999, 1999
P_PROFIT = 0.002
D_MIN, D_P = 150, 0.05
H_D_MIN, H_D_P, H_P = 40, 0.10, 0.05
AGREE = (0.35, 0.65)
STRESS = 2 * config.TICK_VALUE
HOLDOUT_PROXY = ("2021-03-01", "2023-12-31")
RECENT = (2019, 2023)
DSR_MIN, GLOBAL_TRIALS = 0.95, 25
LATEST_EXIT = 11 * 60 + 15  # EIA margin (SPEC rule 2)
GATE_K, GATE_SIMS, GATE_POWER = 4, 1000, 0.80
EXTRA = {0: 200, 2: 200, 6: 200}
N_JOBS = 10
HYPOTHESIS = ("experiments/T20/SPEC.md rev 2, T201 (spread_lead): sign of the front 08:59-09:59 residual on the "
              "second month, every eligible non-EIA day, traded 10:00-10:59")


def bars_and_table():
    s = SpreadLeadHour()
    bars = load_period(DEV, with_second=True, eia_window=s.eia_window)
    return s, bars, s.day_table(bars)


def first_side(bars: pd.DataFrame, side: np.ndarray) -> pd.Series:
    """First nonzero side per trading day."""
    nz = side != 0
    return pd.Series(side[nz], index=bars["trading_day"].to_numpy()[nz]).groupby(level=0).first()


def novelty() -> None:
    s, bars, t = bars_and_table()
    mine = t.loc[t["side"] != 0, "side"]
    out = {}
    for item, strat in (("P101", IntradayMomentum()), ("P102", NoiseArea())):
        other = first_side(bars, np.asarray(strat.signals(bars).side))
        both = mine.index.intersection(other.index)
        agree = float((mine.loc[both] == other.loc[both]).mean()) if len(both) else float("nan")
        out[item] = {"days_both": len(both), "agreement": agree, "ok": bool(AGREE[0] <= agree <= AGREE[1])}
    out["signal_days"] = len(mine)
    out["pass"] = all(v["ok"] for k, v in out.items() if k.startswith("P"))
    out["code_sha256"], out["bundle_sha256"] = ledger.code_sha256(), bundle_sha256()
    NOVELTY.write_text(json.dumps(out, indent=2))
    ledger.append(ITEM, "decision", "dev", family=FAMILY, code_sha256=ledger.code_sha256(), output=str(NOVELTY),
                  note=f"novelty gate {'PASS' if out['pass'] else 'FAIL'}: side agreement with P101/P102, no P&L")
    print(json.dumps(out, indent=2), flush=True)


def _series(x, p, n):
    out = np.zeros(n)
    np.add.at(out, p, x)
    return out


def _sim(i, k, size, is_d, pos, n_days, in_h, h_pos, n_h, recent, sharpes, holdout):
    """Simulated gates: trades, net > 0, 2-tick stress, profitability p, 2019-23 block, global DSR; D gate;
    holdout gates. Not simulated (they can only lower power, so dev power is an upper bound): random-entry,
    walk-forward, look-ahead."""
    rng = np.random.default_rng(i)  # common random numbers: same sides for sim i at every k
    net = rng.choice([-1.0, 1.0], len(size)) * size + k * config.TICK_VALUE
    day = _series(net, pos, n_days)
    sr = day.mean() / day.std() if day.std() > 0 else 0.0
    dsr = deflated_sharpe(day, GLOBAL_TRIALS, float(np.var(np.r_[sharpes, sr], ddof=1)))["dsr"]
    dev_ok = (len(net) >= 300 and net.mean() > 0 and (net - STRESS).mean() > 0 and net[recent].mean() > 0
              and dsr >= DSR_MIN and bootstrap_p(day, N_BOOT, seed=i) <= P_PROFIT)
    nd = net[is_d]
    d_ok = (len(nd) >= D_MIN and nd.mean() > 0
            and bootstrap_p(_series(nd, pos[is_d], n_days), N_BOOT, seed=100_000 + i) <= D_P)
    h_ok = None
    if holdout:
        nh, dh = net[in_h], is_d[in_h]
        h_ok = bool(len(nh) and nh.mean() > 0 and (nh - STRESS).mean() > 0 and dh.sum() >= H_D_MIN
                    and nh[dh].mean() > 0
                    and bootstrap_p(_series(nh, h_pos, n_h), N_BOOT, seed=200_000 + i) <= H_P
                    and bootstrap_p(_series(nh[dh], h_pos[dh], n_h), N_BOOT, seed=300_000 + i) <= H_D_P)
    return bool(dev_ok), bool(d_ok), h_ok


def lower_bound(hits: int, n: int, conf: float = 0.95) -> float:
    """One-sided Clopper-Pearson lower bound."""
    return 0.0 if hits == 0 else float(beta.ppf(1 - conf, hits, n - hits + 1))


def bundle_sha256() -> str:
    """market_tester code + this runner + the spec: a gate file is valid only for this exact bundle."""
    return ledger.sha256_files([*ledger.code_files(), Path(__file__).resolve(), HERE / "SPEC.md"])


def gate_file(path: Path, what: str) -> dict:
    """A passed gate artifact written by the current market_tester code, runner and spec."""
    g = json.loads(path.read_text()) if path.exists() else None
    if not g or not g["pass"]:
        raise SystemExit(f"{what} gate missing or failed (SPEC rev 2)")
    if g.get("bundle_sha256") != bundle_sha256():
        raise SystemExit(f"{what} gate was written by other code, runner or spec: rerun it")
    return g


def global_sharpes() -> np.ndarray:
    import importlib.util  # T10's checked global_sharpes.csv loader
    spec = importlib.util.spec_from_file_location("t10_run", T10_RUN)
    t10 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(t10)
    return t10.load_global()


def power() -> None:
    gate_file(NOVELTY, "novelty")
    sharpes = np.asarray(global_sharpes()["sharpes"], float)
    t0 = time.time()
    s, bars, t = bars_and_table()
    sig = s.signals(bars)
    sig.side = np.abs(sig.side)  # only |move| is used
    tr = run(Market.from_bars(bars), sig)
    size = np.abs(tr["gross_ticks"].to_numpy(float)) * config.TICK_VALUE
    del sig, tr["gross_ticks"]
    days = pd.DatetimeIndex(trading_days(bars))
    day = pd.DatetimeIndex(tr["trading_day"])
    row = t.loc[day]
    is_d = (np.sign(row["r"]) != np.sign(row["d0"])).to_numpy()
    in_h = (day >= HOLDOUT_PROXY[0]) & (day <= HOLDOUT_PROXY[1])
    h_days = days[(days >= HOLDOUT_PROXY[0]) & (days <= HOLDOUT_PROXY[1])]
    pos, h_pos = np.searchsorted(days, day), np.searchsorted(h_days, day[in_h])
    exit_m = pd.DatetimeIndex(tr["exit_time"]).tz_convert("America/New_York")
    exit_min = exit_m.hour * 60 + exit_m.minute
    late, latest = int((exit_min != 10 * 60 + 59).sum()), int(exit_min.max())
    if latest > LATEST_EXIT:
        raise SystemExit(f"an exit at ET minute {latest} is after the 11:15 EIA margin: power gate FAIL")
    recent = (day.year >= RECENT[0]) & (day.year <= RECENT[1])
    print(f"[power] {len(size)} trades ({is_d.sum()} D), holdout proxy {in_h.sum()} ({(is_d & in_h).sum()} D), "
          f"exits not at 10:59: {late}; B={N_BOOT}, {N_JOBS} jobs", flush=True)
    args = (size, is_d, pos, len(days), in_h, h_pos, len(h_days), recent, sharpes)
    rows = []
    for k, n_sims in [(GATE_K, GATE_SIMS), *EXTRA.items()]:
        res = []
        for start in range(0, n_sims, 100):  # chunks of 100 for live progress
            res += Parallel(n_jobs=N_JOBS)(delayed(_sim)(i, k, *args, k == GATE_K)
                                           for i in range(start, min(start + 100, n_sims)))
            dev_n = sum(a and b for a, b, _ in res)
            print(f"[power] k={k}: {len(res)}/{n_sims} sims, dev+D {dev_n} ({time.time() - t0:.0f}s)", flush=True)
        dev_all = sum(a for a, _, _ in res)
        dev_d = sum(a and b for a, b, _ in res)
        d_only = sum(b for _, b, _ in res)
        h = sum(c for _, _, c in res) if k == GATE_K else None
        rows.append({"k": k, "sims": n_sims, "dev_all": dev_all / n_sims, "dev_and_D": dev_d / n_sims,
                     "D_alone": d_only / n_sims, "dev_and_D_lb95": lower_bound(dev_d, n_sims),
                     "D_alone_lb95": lower_bound(d_only, n_sims),
                     "holdout_proxy": None if h is None else h / n_sims})
    gate = next(r for r in rows if r["k"] == GATE_K)
    passed = gate["dev_and_D_lb95"] >= GATE_POWER and gate["D_alone_lb95"] >= GATE_POWER
    weak_holdout = gate["holdout_proxy"] < 0.5
    POWER_JSON.write_text(json.dumps({"pass": passed, "weak_holdout": weak_holdout, "late_exits": late,
                                      "latest_exit_minute": latest, "code_sha256": ledger.code_sha256(),
                                      "bundle_sha256": bundle_sha256(),
                                      "trades": len(size), "D": int(is_d.sum()), "rows": rows}, indent=2))
    text = (f"# T201 power (SPEC rev 2), before any strategy P&L\n\n"
            f"{len(size)} dev trades ({is_d.sum()} D), unsigned 10:00 -> exit move, random sides (common across k), "
            f"B = {N_BOOT}. Holdout proxy {HOLDOUT_PROXY[0]}..{HOLDOUT_PROXY[1]}: {in_h.sum()} trades "
            f"({(is_d & in_h).sum()} D). Exits not at 10:59: {late} (latest ET minute {latest}).\n"
            "Simulated dev gates: trades, net > 0, 2-tick stress, profitability p <= 0.002, 2019-23 block, global DSR; "
            "not simulated: random-entry, walk-forward, look-ahead, so dev power is an upper bound.\n\n"
            "| k net ticks | sims | dev all gates | dev + D | D alone | dev + D 95% LB | D alone 95% LB | holdout proxy |\n"
            "|---|---|---|---|---|---|---|---|\n"
            + "".join(f"| {r['k']} | {r['sims']} | {r['dev_all']:.3f} | {r['dev_and_D']:.3f} | {r['D_alone']:.3f} | "
                      f"{r['dev_and_D_lb95']:.3f} | {r['D_alone_lb95']:.3f} | "
                      f"{'' if r['holdout_proxy'] is None else format(r['holdout_proxy'], '.3f')} |\n"
                      for r in sorted(rows, key=lambda r: r["k"]))
            + f"\nGate (k = {GATE_K}, both lower bounds >= {GATE_POWER}): **{'PASS' if passed else 'FAIL'}**. "
            + ("Holdout proxy < 50%: a candidate needs >= 6 months forward paper trading.\n" if weak_holdout else "\n"))
    POWER.write_text(text)
    ledger.append(ITEM, "decision", "dev", family=FAMILY, code_sha256=ledger.code_sha256(), output=str(POWER),
                  note=f"power gate {'PASS' if passed else 'FAIL'}: unsigned moves, no strategy P&L")
    print(text, flush=True)


def best_week_out(trades: pd.DataFrame) -> float:
    week = pd.DatetimeIndex(trades["trading_day"]).to_period("W")
    best = trades.groupby(week)["net_usd"].sum().idxmax()
    rest = trades[week != best]
    return float(rest["net_usd"].mean()) if len(rest) else float("nan")


def subset_day_pnl(trades, days, mask=None):
    t = trades if mask is None else trades[mask]
    return t.groupby("trading_day")["net_usd"].sum().reindex(days, fill_value=0.0).to_numpy()


def dev() -> None:
    gate_file(NOVELTY, "novelty")
    pw = gate_file(POWER_JSON, "power")
    if ledger.configs_tried(FAMILY):
        raise SystemExit(f"family {FAMILY} already has a dev run: one run only")
    s = SpreadLeadHour()
    r = evaluate(s, {}, item_id=ITEM, family=FAMILY, kind="dev", hypothesis=HYPOTHESIS, n_boot=N_BOOT,
                 n_random=N_RANDOM, gates={"romano_wolf_p_max": P_PROFIT}, global_dsr=global_sharpes())
    trades = r["trades"]
    days = pd.DatetimeIndex(r["grid_result"].days)
    _, _, table = bars_and_table()
    row = table.loc[pd.DatetimeIndex(trades["trading_day"])]
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

    text = (f"# T201 dev summary (Claude's numbers, not yet replicated by Codex)\n\n"
            f"Spec rev 2, code {r['code_sha256'][:12]}, report `{r['report']}`. Dev verdict **{r['verdict']}**.\n\n"
            "| subset | trades | gross $/trade | net $/trade | 95% block-bootstrap CI net $/trade |\n"
            "|---|---|---|---|---|\n"
            + line("all", trades) + line("A (agrees with first-hour sign)", a, ~is_d) + line("D (disagrees)", d, is_d)
            + f"\nD gate: trades {len(d)} (>= {D_MIN}), net > 0, p {d_p:.4g} (<= {D_P}): **{'PASS' if d_ok else 'FAIL'}**\n"
            f"Concentration: traded days {traded_days} (>= 200), net after best week out {bwo:,.2f} (> 0), "
            f"years net > 0: {(years > 0).sum()}/{len(days.year.unique())}\n\n"
            f"**Candidate: {'YES' if candidate else 'NO'}**"
            + (" (holdout proxy power < 50%: >= 6 months forward paper trading required)" if pw["weak_holdout"] else "")
            + "\n")
    SUMMARY.write_text(text)
    print(text, flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--novelty", action="store_true")
    g.add_argument("--power", action="store_true")
    g.add_argument("--dev", action="store_true")
    args = ap.parse_args()
    {"novelty": novelty, "power": power, "dev": dev}[next(k for k, v in vars(args).items() if v)]()
