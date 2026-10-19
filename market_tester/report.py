"""One call per experiment: run, validate, gate, write a report, log the ledger row.

    evaluate(strategy, grid, item_id=..., hypothesis=...)    dev period; grid search penalized
    evaluate_holdout(strategy, prereg_path)                   holdout; one frozen config, one run ever

A strategy counts as a candidate only if every dev gate passes; it counts as real only after the holdout
passes AND Codex replicates the headline numbers (AGENTS.md).
"""

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import config, ledger, prereg
from .baseline import random_entry_test
from .engine import Market, run, with_costs
from .strategy import Signals
from .metrics import by_side, by_year, daily, summary, trading_days
from .optimize import day_sharpe, grid_configs, run_grid, select, walk_forward
from .periods import DEV, HOLDOUT, load_period
from .stats import bootstrap_ci, clustered_mean, deflated_sharpe, monte_carlo, pbo_cscv, romano_wolf, spa_test

DEFAULT_GATES = {
    "min_trades": 300,
    "net_usd_per_trade_gt": 0.0,
    "romano_wolf_p_max": 0.05,  # pick's adjusted p inside the grid family (= bootstrap p for one config)
    "dsr_min": 0.95,  # deflated Sharpe, trials = every config ever run in the research family
    "random_entry_p_max": 0.05,
    "walk_forward_net_gt": 0.0,
    "pbo_max": 0.3,
    "recent_years": [2019, 2023],  # this block alone must be net positive (edges decayed after ~2015)
    "stress_slip_ticks": 2,  # still net positive with this slippage per side
}
_DEV_HIGHER_IS_STRICTER = {"min_trades": True, "net_usd_per_trade_gt": True, "romano_wolf_p_max": False,
                           "dsr_min": True, "random_entry_p_max": False, "walk_forward_net_gt": True,
                           "pbo_max": False, "stress_slip_ticks": True}  # recent_years is fixed
strategy_id = prereg.strategy_id


def _signal_arrays(sig: Signals, n: int) -> list[np.ndarray]:
    return [np.broadcast_to(np.asarray(0 if x is None else x), (n,))
            for x in (sig.side, sig.stop_ticks, sig.target_ticks, sig.hold_minutes, sig.exit, sig.flat,
                      sig.exit_long, sig.exit_short, sig.stop_price, sig.target_price)]


def lookahead_check(strategy, bars: pd.DataFrame, params: dict, n_cuts: int = 30, seed: int = 0,
                    full: Signals | None = None) -> dict:
    """Cut the future off after bar k: every signal array (side, stop, target, hold, exit, flat, exit_long,
    exit_short, stop / target price) on bars 0..k must be identical to the full run. Two thirds of the cuts
    land on decision bars (an entry, exit or flat signal), where a short leak shows; a prefix compare also
    catches fits over the whole sample.
    A spot check, not a proof: strategies must also be causal by construction."""
    n = len(bars)
    full = strategy.signals(bars, **params) if full is None else full
    ref = _signal_arrays(full, n)
    rng = np.random.default_rng(seed)
    decision = np.flatnonzero(((ref[0] != 0) | ref[4].astype(bool) | ref[5].astype(bool) | ref[6].astype(bool)
                               | ref[7].astype(bool))[: n - 1])
    k_dec = min(len(decision), (2 * n_cuts) // 3)
    cuts = set(int(c) for c in rng.choice(decision, k_dec, replace=False)) if k_dec else set()
    pool = np.setdiff1d(np.arange(n // 10, n - 1), np.fromiter(cuts, np.int64, len(cuts)))
    cuts |= set(int(c) for c in rng.choice(pool, min(len(pool), n_cuts - len(cuts)), replace=False))
    bad = []
    for k in sorted(cuts):
        part = bars.iloc[: k + 1].reset_index(drop=True)
        got = _signal_arrays(strategy.signals(part, **params), k + 1)
        if any(not np.array_equal(g, r[: k + 1]) for g, r in zip(got, ref)):
            bad.append(k)
    return {"ok": not bad, "cuts": len(cuts), "decision_cuts": k_dec, "changed_at_bars": bad[:5]}


def _gate_rows(checks: list[tuple[str, object, str, bool | None]]) -> pd.DataFrame:
    return pd.DataFrame(checks, columns=["gate", "value", "rule", "pass"])


def _fmt(v):
    if isinstance(v, float):
        return "nan" if math.isnan(v) else f"{v:,.4g}"
    return str(v)


def _md_table(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "_none_\n"
    df = df.reset_index() if df.index.name else df
    head = "| " + " | ".join(map(str, df.columns)) + " |\n|" + "---|" * len(df.columns) + "\n"
    return head + "".join("| " + " | ".join(_fmt(v) for v in row) + " |\n" for row in df.itertuples(index=False))


def _write(item_id: str, tag: str, md: str, numbers: dict) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = config.EXPERIMENTS_DIR / item_id / f"{stamp}-{tag}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(md)
    out.with_suffix(".json").write_text(json.dumps(numbers, indent=2, default=str))
    return out


def evaluate(strategy, grid: dict, *, item_id: str, hypothesis: str, bars: pd.DataFrame | None = None,
             years: list[int] | None = None, gates: dict | None = None, slip_ticks: float = 1, n_jobs: int = 1,
             n_boot: int = 2000, n_random: int = 200, n_mc: int = 5000, capital: float | None = None,
             seed: int = 0, write: bool = True, kind: str = "dev", family: str | None = None,
             global_dsr: dict | None = None) -> dict:
    """Dev-period evaluation of a parameter grid. `family` = the research idea this item belongs to
    (default: item_id); renamed variants of one idea must share it so their trials add up.

    `global_dsr` = {"n_trials", "sharpes", "source"}: deflate against research ideas beyond this family. Trials =
    max(family count, n_trials); Sharpe variance over `sharpes` (per-day Sharpes of earlier one-config items) plus
    this pick's own. A strategy with `requires_second = True` gets the CL.v.1 columns (`data.attach_second`); one
    with `eligible(bars, **params)` has random entries drawn only among its eligible days.
    With one config, the profitability p (Romano-Wolf = block bootstrap) uses all `n_boot` draws.

    kind="dev"          the whole dev period; only its PASS makes a candidate
    kind="exploratory"  real dev data, `years` may narrow it; trials count, verdict is EXPLORATORY-*
    kind="synthetic"    `bars` supplied (planted / fake data); not counted"""
    if kind not in ("dev", "exploratory", "synthetic"):
        raise ValueError(f"kind must be 'dev', 'exploratory' or 'synthetic', got {kind!r}")
    real = kind != "synthetic"
    if real and (bars is not None or not write):
        raise ValueError("a real-data run loads the dev period itself and is always logged (use kind='synthetic')")
    if kind == "dev" and years is not None:
        raise ValueError("a dev run uses the whole dev period; narrow it with kind='exploratory'")
    if hasattr(strategy, "fit"):
        raise ValueError("strategies that fit on data are not supported: walk-forward computes signals once")
    family = item_id if family is None else family
    if real:
        # real data: cost baseline and gates may only get stricter
        prereg.check_slip(slip_ticks)
        gates = prereg.stricter_gates(DEFAULT_GATES, _DEV_HIGHER_IS_STRICTER, gates or {})
    else:
        gates = {**DEFAULT_GATES, **(gates or {})}
    t0 = time.time()
    n_configs = len(grid_configs(grid))
    if real:
        prior = ledger.configs_tried(family)
        start_id = ledger.append(item_id, "dev_start" if kind == "dev" else "exploratory", "dev", family=family,
                                 n_configs=n_configs, code_sha256=ledger.code_sha256(),
                                 note=f"{strategy_id(strategy)} {grid} years={years or 'all'}")
        print(f"[{item_id}] logged {start_id}; loading dev bars {years or 'all years'}", flush=True)
        bars = load_period(DEV, years=years, with_second=needs_second(strategy), eia_window=eia_window(strategy))
    else:
        prior, start_id = 0, f"{item_id}-synthetic"
    market = Market.from_bars(bars)
    days = trading_days(bars)
    print(f"[{item_id}] {len(bars):,} bars, {len(days):,} days, {n_configs} configs "
          f"(+{prior} tried before in family {family})", flush=True)

    res = run_grid(strategy, bars, grid, slip_ticks, n_jobs, market)
    pick = select(res)
    params = res.configs[pick]
    print(f"[{item_id}] pick {params}; validating", flush=True)
    sig = strategy.signals(bars, **params)
    trades = with_costs(run(market, sig), slip_ticks)
    d = daily(trades, days)
    s = summary(trades, days)

    sharpes = day_sharpe(res.pnl)
    if real:
        ledger.append_trials(family, start_id, res.configs, sharpes)
        all_sharpes = np.asarray(ledger.trial_sharpes(family))
    else:
        all_sharpes = sharpes
    # re-read now: concurrent runs of the family that started meanwhile count too
    n_trials = max(ledger.configs_tried(family) if real else n_configs, len(all_sharpes))
    # spread of Sharpe across every counted trial (Bailey & Lopez de Prado); 1/T when only one is known
    sharpe_var = float(np.var(all_sharpes, ddof=1)) if len(all_sharpes) > 1 else 1.0 / len(days)
    if global_dsr is not None:
        x = d["pnl"].to_numpy()
        own = float(x.mean() / x.std()) if x.std() > 0 else 0.0  # same per-day Sharpe as deflated_sharpe's `sr`
        pool = np.r_[np.asarray(global_dsr["sharpes"], float), own]
        n_trials = max(n_trials, int(global_dsr["n_trials"]))
        sharpe_var = float(np.var(pool, ddof=1))
    dsr = deflated_sharpe(d["pnl"].to_numpy(), n_trials, sharpe_var)
    rw = romano_wolf(res.pnl, n_boot=min(n_boot, 1000) if n_configs > 1 else n_boot, seed=seed)
    spa = spa_test(res.pnl, n_boot=min(n_boot, 1000), seed=seed) if n_configs > 1 else {"p": float("nan")}
    pbo = pbo_cscv(res.pnl) if n_configs > 1 else {"pbo": float("nan"), "splits": 0}
    wf = walk_forward(res)
    cl = clustered_mean(trades["net_usd"], trades["trading_day"])
    boot = bootstrap_ci(d["pnl"], d["n"], n_boot=n_boot, seed=seed)
    mc = (monte_carlo(trades["net_usd"].to_numpy(), d["pnl"].to_numpy(), n_sims=n_mc, seed=seed, capital=capital)
          if len(trades) else {})
    stressed = with_costs(trades, gates["stress_slip_ticks"])
    lo, hi = gates["recent_years"]
    recent = trades[pd.DatetimeIndex(trades["trading_day"]).year.isin(range(lo, hi + 1))]
    print(f"[{item_id}] random-entry baseline ({n_random} draws)", flush=True)
    rnd = random_entry_test(market, trades, sig, slip_ticks, n_random, seed, progress=True,
                            eligible=eligible_mask(strategy, bars, params))
    print(f"[{item_id}] look-ahead check", flush=True)
    trunc = lookahead_check(strategy, bars, params, seed=seed, full=sig)

    rw_p = float(rw["p_adj"][pick])
    checks = [
        ("trades", s["trades"], f">= {gates['min_trades']}", s["trades"] >= gates["min_trades"]),
        ("net $/trade", s["net_usd_per_trade"], f"> {gates['net_usd_per_trade_gt']}",
         s["net_usd_per_trade"] > gates["net_usd_per_trade_gt"]),
        ("Romano-Wolf p (pick)", rw_p, f"<= {gates['romano_wolf_p_max']}", rw_p <= gates["romano_wolf_p_max"]),
        ("deflated Sharpe", dsr["dsr"], f">= {gates['dsr_min']}", dsr["dsr"] >= gates["dsr_min"]),
        ("random-entry p", rnd["p"], f"<= {gates['random_entry_p_max']}", rnd["p"] <= gates["random_entry_p_max"]),
        ("walk-forward OOS net $/trade", wf["net_usd_per_trade"], f"> {gates['walk_forward_net_gt']}",
         wf["net_usd_per_trade"] > gates["walk_forward_net_gt"]),
        ("PBO", pbo["pbo"], f"<= {gates['pbo_max']}", None if math.isnan(pbo["pbo"]) else pbo["pbo"] <= gates["pbo_max"]),
        (f"net $/trade {lo}-{hi}", float(recent["net_usd"].mean()) if len(recent) else float("nan"), "> 0",
         bool(len(recent)) and recent["net_usd"].mean() > 0),
        (f"net $/trade at {gates['stress_slip_ticks']} tick slip", float(stressed["net_usd"].mean()), "> 0",
         bool(len(stressed)) and stressed["net_usd"].mean() > 0),
        ("look-ahead (signals before cut)", trunc["ok"], "unchanged", trunc["ok"]),
    ]
    gate_df = _gate_rows(checks)
    verdict = "PASS" if gate_df["pass"].dropna().astype(bool).all() else "FAIL"
    if kind == "exploratory":
        verdict = f"EXPLORATORY-{verdict}"  # partial or repeated look: never a candidate

    numbers = {"item_id": item_id, "family": family, "hypothesis": hypothesis, "strategy": strategy_id(strategy),
               "kind": kind, "start_run_id": start_id, "configs_all_families": ledger.configs_tried(),
               "period": f"{days[0].date()}..{days[-1].date()}", "grid": grid, "n_configs": n_configs,
               "n_trials": n_trials, "sharpe_var": sharpe_var, "global_dsr": global_dsr, "pick": params, "slip_ticks": slip_ticks, "summary": s, "dsr": dsr,
               "romano_wolf_p_pick": rw_p, "spa_p": spa["p"], "pbo": pbo, "clustered": cl, "bootstrap": boot,
               "monte_carlo": mc, "random_entry": rnd, "lookahead": trunc,
               "walk_forward": {k: v for k, v in wf.items() if k not in ("day_pnl", "day_trades", "years")},
               "gates": gate_df.to_dict("records"), "verdict": verdict, "code_sha256": ledger.code_sha256(),
               "seconds": round(time.time() - t0, 1)}
    md = (f"# {item_id} — {verdict}\n\n{hypothesis}\n\n"
          f"- strategy `{numbers['strategy']}`, period {numbers['period']} ({kind}), slip {slip_ticks} tick/side\n"
          f"- family `{family}`: configs this run {n_configs}, trials counted {n_trials} "
          f"(all families: {numbers['configs_all_families']}); pick `{params}`\n"
          f"- code sha256 `{numbers['code_sha256'][:16]}`\n\n"
          f"## Gates\n{_md_table(gate_df)}\n## Pick summary\n{_md_table(pd.DataFrame([{k: v for k, v in s.items() if k != 'exits'}]))}\n"
          f"exits: {s.get('exits')}\n\n"
          f"## By year\n{_md_table(by_year(trades))}\n## By side\n{_md_table(by_side(trades))}\n"
          f"## Walk-forward (anchored, plateau pick)\n{_md_table(wf['years'])}\n"
          f"## Statistics\n- day-clustered t: mean {_fmt(cl['mean'])} $/trade, t {_fmt(cl['t'])}, one-sided p {_fmt(cl['p_one_sided'])}\n"
          f"- bootstrap ({boot['n_boot']} draws, mean block {boot['mean_block_days']} days): per trade "
          f"{_fmt(boot['per_trade'])} CI {[_fmt(x) for x in boot['per_trade_ci']]}\n"
          f"- deflated Sharpe {_fmt(dsr['dsr'])} (per-day SR {_fmt(dsr['sr'])}, luck bar {_fmt(dsr['sr0'])})\n"
          f"- Romano-Wolf p {_fmt(rw_p)}, SPA p {_fmt(spa['p'])}, PBO {_fmt(pbo['pbo'])}\n"
          f"- random entries: {rnd}\n- Monte Carlo (risk only): {mc}\n- look-ahead: {trunc}\n")
    if write:
        out = _write(item_id, kind, md, numbers)
        numbers["report"] = str(out)
        result_kind = {"dev": "dev", "exploratory": "exploratory_result", "synthetic": "synthetic"}[kind]
        ledger.append(item_id, result_kind, "synthetic" if kind == "synthetic" else "dev", family=family,
                      n_configs=n_configs,
                      statistic="net_usd_per_trade", value=s["net_usd_per_trade"], p_value=rw_p, verdict=verdict,
                      code_sha256=numbers["code_sha256"], output=str(out),
                      note=strategy_id(strategy) + (f" global_dsr={global_dsr['source']}" if global_dsr else ""))
    print(f"[{item_id}] {verdict} in {numbers['seconds']}s", flush=True)
    numbers.update(trades=trades, grid_result=res, gate_table=gate_df, markdown=md)
    return numbers


def needs_second(strategy) -> bool:
    return bool(getattr(type(strategy), "requires_second", False))


def eia_window(strategy) -> tuple[int, int] | None:
    """A strategy's `eia_window` class attribute (ET minutes): the loader adds `eia_during_hold`."""
    return getattr(type(strategy), "eia_window", None)


def eligible_mask(strategy, bars: pd.DataFrame, params: dict):
    return strategy.eligible(bars, **params) if hasattr(strategy, "eligible") else None


def register_holdout(spec: dict, strategy) -> Path:
    """Freeze a holdout test (owner approval + Codex review named in the spec)."""
    return prereg.register(spec, strategy, HOLDOUT.files(with_second=needs_second(strategy)))


def evaluate_holdout(strategy, prereg_path: Path, n_boot: int = 2000, n_random: int = 200, n_mc: int = 5000,
                     capital: float | None = None) -> dict:
    """The one holdout run of a frozen configuration. Spends the holdout for this prereg even if it crashes."""
    spec, _ = prereg.load(prereg_path)
    gates = prereg.holdout_gates(spec["gates"])
    params, slip, seed = spec["params"], spec["slip_ticks"], spec["seed"]
    with prereg.open_run(prereg_path, strategy) as token:
        bars = load_period(HOLDOUT, token, with_second=needs_second(strategy), eia_window=eia_window(strategy))
    market = Market.from_bars(bars)
    days = trading_days(bars)
    sig = strategy.signals(bars, **params)
    trades = with_costs(run(market, sig), slip)
    d = daily(trades, days)
    s = summary(trades, days)
    cl = clustered_mean(trades["net_usd"], trades["trading_day"])
    boot = bootstrap_ci(d["pnl"], d["n"], n_boot=n_boot, seed=seed)
    rnd = random_entry_test(market, trades, sig, slip, n_random, seed, progress=True,
                            eligible=eligible_mask(strategy, bars, params))
    mc = (monte_carlo(trades["net_usd"].to_numpy(), d["pnl"].to_numpy(), n_sims=n_mc, seed=seed, capital=capital)
          if len(trades) else {})
    stressed = with_costs(trades, gates["stress_slip_ticks"])
    checks = [
        ("trades", s["trades"], f">= {gates['min_trades']}", s["trades"] >= gates["min_trades"]),
        ("net $/trade", s["net_usd_per_trade"], f"> {gates['net_usd_per_trade_gt']}",
         s["net_usd_per_trade"] > gates["net_usd_per_trade_gt"]),
        ("day-clustered p (one-sided)", cl["p_one_sided"], f"<= {gates['p_one_sided_max']}",
         cl["p_one_sided"] <= gates["p_one_sided_max"]),
        (f"net $/trade at {gates['stress_slip_ticks']} tick slip", float(stressed["net_usd"].mean()), "> 0",
         bool(len(stressed)) and stressed["net_usd"].mean() > 0),
    ]
    gate_df = _gate_rows(checks)
    verdict = "PASS" if gate_df["pass"].astype(bool).all() else "FAIL"
    numbers = {"item_id": spec["item_id"], "strategy": spec["strategy"], "params": params, "summary": s,
               "clustered": cl, "bootstrap": boot, "random_entry": rnd, "monte_carlo": mc,
               "gates": gate_df.to_dict("records"), "verdict": verdict, "prereg": str(prereg_path)}
    md = (f"# {spec['item_id']} HOLDOUT — {verdict}\n\n{spec['hypothesis']}\n\nparams `{params}`, slip {slip}\n\n"
          f"## Gates\n{_md_table(gate_df)}\n## By year\n{_md_table(by_year(trades))}\n"
          f"## Statistics\n- clustered: {cl}\n- bootstrap: {boot}\n- random entries: {rnd}\n- Monte Carlo: {mc}\n")
    out = _write(spec["item_id"], "holdout", md, numbers)
    ledger.append(spec["item_id"], "holdout", "holdout", n_configs=1, statistic="net_usd_per_trade",
                  value=s["net_usd_per_trade"], p_value=cl["p_one_sided"], verdict=verdict,
                  prereg_sha256=token.prereg_sha256, code_sha256=ledger.code_sha256(), output=str(out))
    numbers.update(trades=trades, report=str(out), markdown=md)
    return numbers
