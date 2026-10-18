"""P10 dev runs (experiments/P10/SPEC.md rev 3): one frozen config per item, then the batch summary.

    .venv/bin/python experiments/P10/run.py            # every item not run yet, then the summary
    .venv/bin/python experiments/P10/run.py --summary  # summary only, from saved results

Each finished item saves its trades and per-day P&L under data/p10/ (not in git); a rerun skips it. The batch
is one frozen code version: every saved item must carry the current `ledger.code_sha256()`, else the run stops.
An item whose family already has ledger rows but no saved result (crash mid-item) stops the run: resolve it by
hand, never by rerunning silently.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from market_tester import config, ledger  # noqa: E402
from market_tester.published import P10  # noqa: E402
from market_tester.report import evaluate  # noqa: E402
from market_tester.stats import deflated_sharpe  # noqa: E402

OUT = ROOT / "data" / "p10"
SUMMARY = config.EXPERIMENTS_DIR / "P10" / "SUMMARY.md"
N_RANDOM = 1999
RELATED_DEAD_TRIALS = {"P102": 1_056_000 + 17, "P110": 6_144}  # LESSONS §3: ORB + VWAP break; EMA x VWAP
HYPOTHESIS = "experiments/P10/SPEC.md rev 3, item {item} ({family}): frozen published / source-inspired rule"


def write_atomic(path: Path, write) -> None:
    tmp = path.with_name(path.name + ".tmp")
    write(tmp)
    tmp.replace(path)


def check_batch(code: str) -> list[str]:
    """Items still to run. Stops on a saved item from other code or a logged item without a saved result."""
    todo = []
    for item, (family, _) in P10.items():
        f = OUT / f"{item}.json"
        if f.exists():
            saved = json.loads(f.read_text())["code_sha256"]
            if saved != code:
                raise SystemExit(f"{item} was run with code {saved[:12]}, current {code[:12]}: batch not coherent")
        elif ledger.configs_tried(family):
            raise SystemExit(f"{item}: family {family} is in the ledger but has no saved result (crash?)")
        else:
            todo.append(item)
    return todo


def check_code(expected: str, when: str) -> None:
    if ledger.code_sha256() != expected:
        raise SystemExit(f"source changed {when}: the batch must run on one code version")


def run_item(item: str, code: str) -> None:
    family, strategy = P10[item]
    t0 = time.time()
    check_code(code, f"before {item}")
    r = evaluate(strategy, {}, item_id=item, family=family, kind="dev", n_random=N_RANDOM,
                 hypothesis=HYPOTHESIS.format(item=item, family=family))
    check_code(code, f"during {item}")
    if r["code_sha256"] != code:
        raise SystemExit(f"{item} recorded code {r['code_sha256'][:12]}, expected {code[:12]}")
    res = r["grid_result"]
    OUT.mkdir(parents=True, exist_ok=True)
    write_atomic(OUT / f"{item}-trades.parquet", r["trades"].to_parquet)
    daily = pd.DataFrame({"pnl": res.pnl[:, 0], "n": res.trades[:, 0]}, index=res.days)
    write_atomic(OUT / f"{item}-daily.parquet", daily.to_parquet)
    keep = {k: r[k] for k in ("item_id", "family", "verdict", "summary", "romano_wolf_p_pick", "random_entry",
                              "dsr", "clustered", "report", "start_run_id", "code_sha256")}
    keep["gates"] = r["gate_table"].to_dict("records")
    write_atomic(OUT / f"{item}.json", lambda p: p.write_text(json.dumps(keep, indent=2, default=str)))  # last
    print(f"[{item}] saved in {time.time() - t0:.0f}s: {r['verdict']}", flush=True)


def holm(p: list[float]) -> list[float]:
    """Holm step-down adjusted p-values; NaN counts as 1 (a failure)."""
    p = np.nan_to_num(np.asarray(p, float), nan=1.0)
    order = np.argsort(p, kind="stable")
    adj = np.empty(len(p))
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (len(p) - rank) * p[i]))
        adj[i] = running
    return adj.tolist()


def best_week_out(trades: pd.DataFrame) -> float:
    """Net $/trade after removing the calendar week with the largest total net."""
    week = pd.DatetimeIndex(trades["trading_day"]).to_period("W")
    best = trades.groupby(week)["net_usd"].sum().idxmax()
    rest = trades[week != best]
    return float(rest["net_usd"].mean()) if len(rest) else float("nan")


def summary() -> None:
    rows, daily, codes = [], {}, set()
    for item, (family, strategy) in P10.items():
        f = OUT / f"{item}.json"
        if not f.exists():
            print(f"[summary] {item} not run yet", flush=True)
            return
        r = json.loads(f.read_text())
        codes.add(r["code_sha256"])
        t = pd.read_parquet(OUT / f"{item}-trades.parquet")
        daily[item] = pd.read_parquet(OUT / f"{item}-daily.parquet")["pnl"]
        s = r["summary"]
        dev_years = pd.DatetimeIndex(daily[item].index).year.unique()
        years = t.groupby(pd.DatetimeIndex(t["trading_day"]).year)["net_usd"].sum().reindex(dev_years, fill_value=0)
        rows.append({"item": item, "family": family, "strategy": strategy.name, "verdict": r["verdict"],
                     "trades": s["trades"], "traded_days": t["trading_day"].nunique(),
                     "gross_usd_per_trade": float((t["gross_ticks"] * config.TICK_VALUE).mean()),
                     "net_usd_per_trade": s["net_usd_per_trade"], "best_week_out": best_week_out(t),
                     "years_net_pos": f"{int((years > 0).sum())}/{len(years)}",
                     "profit_p": r["romano_wolf_p_pick"], "random_p": r["random_entry"]["p"]})
    if len(codes) != 1:
        raise SystemExit(f"items were run with {len(codes)} code versions: batch not coherent")
    df = pd.DataFrame(rows).set_index("item")
    df["profit_p_holm"] = holm(df["profit_p"].tolist())
    df["random_p_holm"] = holm(df["random_p"].tolist())
    sr = {k: v.mean() / v.std(ddof=0) if v.std(ddof=0) > 0 else np.nan for k, v in daily.items()}
    var = float(np.nanvar(list(sr.values()), ddof=1))
    df["batch_dsr"] = [deflated_sharpe(daily[i].to_numpy(), 10 + RELATED_DEAD_TRIALS.get(i, 0), var)["dsr"]
                       for i in df.index]
    df["candidate"] = ((df["verdict"] == "PASS") & (df["profit_p_holm"] <= 0.05) & (df["random_p_holm"] <= 0.05)
                       & (df["batch_dsr"] >= 0.95) & (df["traded_days"] >= 200) & (df["best_week_out"] > 0))
    fmt = df.copy()
    for c in ("gross_usd_per_trade", "net_usd_per_trade", "best_week_out"):
        fmt[c] = fmt[c].map(lambda x: f"{x:,.2f}")
    for c in ("profit_p", "random_p", "profit_p_holm", "random_p_holm", "batch_dsr"):
        fmt[c] = fmt[c].map(lambda x: f"{x:.4f}")
    head = "| " + " | ".join(["item", *fmt.columns]) + " |\n|" + "---|" * (len(fmt.columns) + 1) + "\n"
    body = "".join("| " + " | ".join([i, *map(str, row)]) + " |\n"
                   for i, row in zip(fmt.index, fmt.itertuples(index=False), strict=True))
    text = (f"# P10 dev summary (Claude's numbers, not yet replicated by Codex)\n\n"
            f"Spec: SPEC.md rev 3, code {codes.pop()[:12]}. Dev 2010-06-07..2023-12-31, $24.02 round trip, "
            f"{N_RANDOM} random-entry draws. Candidate rule: SPEC.md 'Statistics and candidate rule'.\n\n"
            f"{head}{body}\nCandidates: {', '.join(df.index[df['candidate']]) or 'none'}\n")
    write_atomic(SUMMARY, lambda p: p.write_text(text))
    write_atomic(OUT / "summary.json", lambda p: df.to_json(p, indent=2))
    print(SUMMARY.read_text(), flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()
    if not args.summary:
        code = ledger.code_sha256()
        todo = check_batch(code)
        for k, item in enumerate(todo, 1):
            print(f"=== {item} ({k}/{len(todo)}) {P10[item][0]} ===", flush=True)
            run_item(item, code)
    summary()
