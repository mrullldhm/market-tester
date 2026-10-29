"""Pre-registration: freeze a holdout test once, then open the holdout exactly once for it.

    path = register(spec, strategy, data_files)   experiments/<item_id>/prereg.json (never overwritten) + ledger row
    with open_run(path, strategy) as token:       re-checks every frozen hash, then creates
        bars = load_period(HOLDOUT, token)        experiments/holdout_opened/<prereg sha>.json atomically BEFORE
                                                  data is read. The token dies with the block. One run, ever.

Frozen: market_tester code, the strategy's source file and instance state, the configuration, the holdout
bar files and the EIA reference file. The same strategy + params + state can be registered only once.
"""

import dataclasses
import hashlib
import inspect
import json
import math
import os
import secrets
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..common import config, ledger

REQUIRED = ["item_id", "hypothesis", "strategy", "params", "period", "gates", "slip_ticks", "seed",
            "approved_by_user", "codex_review"]
STOPPING_RULE = "one run; never re-run, extended or re-registered"
HOLDOUT_GATES = {"min_trades": 100, "net_usd_per_trade_gt": 0.0, "p_one_sided_max": 0.05, "stress_slip_ticks": 2}
_HIGHER_IS_STRICTER = {"min_trades": True, "net_usd_per_trade_gt": True, "p_one_sided_max": False,
                       "stress_slip_ticks": True}


class PreregError(RuntimeError):
    pass


def strategy_id(strategy) -> str:
    return f"{type(strategy).__module__}:{type(strategy).__name__}"


def strategy_state(strategy) -> str:
    """Canonical text of the instance's own attributes (constructor state), so another instance cannot pass."""
    state = dataclasses.asdict(strategy) if dataclasses.is_dataclass(strategy) else dict(vars(strategy))
    return json.dumps(state, sort_keys=True, default=repr)


def strategy_source_sha256(strategy) -> str:
    return ledger.sha256_file(Path(inspect.getsourcefile(type(strategy))))


def config_sha256(spec: dict, strategy) -> str:
    key = {"strategy": spec["strategy"], "params": spec["params"], "state": strategy_state(strategy)}
    return hashlib.sha256(json.dumps(key, sort_keys=True, default=repr).encode()).hexdigest()


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def stricter_gates(defaults: dict, higher_is_stricter: dict, overrides: dict) -> dict:
    """Defaults with overrides that may only tighten them (finite, non-boolean numbers; no unknown keys).
    Keys missing from `higher_is_stricter` are fixed: an override must equal the default."""
    if not isinstance(overrides, dict):
        raise PreregError("gates must be a dict")
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise PreregError(f"unknown gates {sorted(unknown)}")
    gates = dict(defaults)
    for k, v in overrides.items():
        if k not in higher_is_stricter:
            if v != defaults[k]:
                raise PreregError(f"gate {k} is fixed at {defaults[k]}")
            continue
        if not _is_number(v):
            raise PreregError(f"gate {k} must be a finite number")
        looser = v < defaults[k] if higher_is_stricter[k] else v > defaults[k]
        if looser:
            raise PreregError(f"gate {k}={v} is looser than the default {defaults[k]}")
        gates[k] = v
    return gates


def holdout_gates(overrides: dict) -> dict:
    """Default holdout gates; a prereg may only make them stricter."""
    return stricter_gates(HOLDOUT_GATES, _HIGHER_IS_STRICTER, overrides)


def check_slip(slip_ticks) -> None:
    if not _is_number(slip_ticks) or slip_ticks < 1:
        raise PreregError("slip_ticks must be a finite number >= 1 (the cost baseline)")


def validate(spec: dict) -> None:
    missing = [k for k in REQUIRED if k not in spec]
    if missing:
        raise PreregError(f"prereg missing fields: {missing}")
    try:
        ledger.check_name(spec["item_id"])
    except ledger.LedgerError as e:
        raise PreregError(str(e)) from None
    if not isinstance(spec["gates"], dict):
        raise PreregError("gates must be a dict")
    if spec["period"] != "holdout":
        raise PreregError("only the holdout is pre-registered; dev runs go through report.evaluate")
    if spec["approved_by_user"] is not True:
        raise PreregError("a holdout prereg needs approved_by_user: true (owner approval)")
    if not str(spec["codex_review"]).strip():
        raise PreregError("codex_review must name the Codex review that cleared this prereg")
    if not isinstance(spec["params"], dict):
        raise PreregError("params must be one frozen configuration (a dict), not a grid")
    check_slip(spec["slip_ticks"])
    holdout_gates(spec["gates"])


def item_dir(item_id: str) -> Path:
    return config.QUANTUM_EXPERIMENTS_DIR / item_id


def canonical_path(item_id: str) -> Path:
    return (item_dir(item_id) / "prereg.json").resolve()


def _registered_rows():
    rows = ledger.read()
    return rows[rows["kind"] == "prereg"] if len(rows) else rows


def write_exclusive(path: Path, text: str) -> None:
    """Create `path` with complete content or not at all: write a temp file, fsync, then hard-link it into
    place (link fails if `path` exists). A crash never leaves a half-written file at `path`."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.link(tmp, path)
    finally:
        os.unlink(tmp)


def _config_taken(cfg: str) -> bool:
    """A config is taken by a valid registration (its prereg file exists with the ledger's sha) or by a
    holdout that was ever opened for it. Call under the registry lock."""
    rows = _registered_rows()
    mine = rows[rows["value"] == cfg] if len(rows) else rows
    opened_dir = config.QUANTUM_EXPERIMENTS_DIR / "holdout_opened"
    for out, sha in zip(mine["output"], mine["prereg_sha256"]):
        if (opened_dir / f"{sha}.json").exists():
            return True
        if Path(out).exists() and ledger.sha256_file(Path(out)) == sha:
            return True
    return False


def _clear_stale_claim(cfg: str, claim: Path) -> None:
    """A process killed mid-registration leaves a claim (and maybe a prereg file) with no ledger row.
    Neither is valid, so remove them; a retry must not be blocked forever. Call under the registry lock."""
    if not claim.exists() or _config_taken(cfg):
        return
    target = Path(json.loads(claim.read_text())["prereg"])
    if target.exists() and not ledger.prereg_registered(ledger.sha256_file(target)):
        target.unlink()
    claim.unlink()


def register(spec: dict, strategy, data_files) -> Path:
    validate(spec)
    if spec["strategy"] != strategy_id(strategy):
        raise PreregError(f"spec names {spec['strategy']}, strategy is {strategy_id(strategy)}")
    data_files = [Path(f) for f in data_files]
    if not data_files or not all(f.exists() for f in data_files):
        raise PreregError("data_files must list the existing holdout bar files and the EIA reference file")
    path = canonical_path(spec["item_id"])
    cfg = config_sha256(spec, strategy)
    full = dict(spec, stopping_rule=STOPPING_RULE, code_sha256=ledger.code_sha256(),
                strategy_source_sha256=strategy_source_sha256(strategy), strategy_state=strategy_state(strategy),
                config_sha256=cfg, data_files_sha256={str(f): ledger.sha256_file(f) for f in data_files},
                registered_utc=datetime.now(timezone.utc).isoformat())
    claim = config.QUANTUM_EXPERIMENTS_DIR / "registered" / f"{cfg}.json"
    # uniqueness check, file creation and ledger row are one transaction; a failure undoes the files
    with ledger.locked(config.QUANTUM_EXPERIMENTS_DIR / "prereg_registry"):
        _clear_stale_claim(cfg, claim)
        if path.exists():
            raise PreregError(f"{path} already exists: a pre-registration is never rewritten")
        if claim.exists() or _config_taken(cfg):
            raise PreregError("this strategy + params + state is already registered")
        claim.parent.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        created = []
        try:
            write_exclusive(claim, json.dumps({"prereg": str(path)}))
            created.append(claim)
            write_exclusive(path, json.dumps(full, indent=2, default=str))
            created.append(path)
            sha = ledger.sha256_file(path)
            ledger.append(spec["item_id"], "prereg", "holdout", prereg_sha256=sha, code_sha256=full["code_sha256"],
                          statistic="config_sha256", value=cfg, output=str(path), note="frozen")
        except BaseException:
            for f in reversed(created):
                f.unlink(missing_ok=True)
            raise
    return path


def load(path: Path) -> tuple[dict, str]:
    """Spec and its sha256. Refuses a file that was edited, never frozen, or is a copy at another path."""
    path = Path(path).resolve()
    sha = ledger.sha256_file(path)
    rows = _registered_rows()
    mine = rows[rows["prereg_sha256"] == sha] if len(rows) else rows
    if not len(mine):
        raise PreregError(f"{path} is not a registered pre-registration (edited or never frozen)")
    spec = json.loads(path.read_text())
    if str(path) not in set(mine["output"]) or path != canonical_path(spec["item_id"]):
        raise PreregError(f"{path} is not where this pre-registration was frozen")
    return spec, sha


def verify_frozen(spec: dict, strategy) -> None:
    """Every frozen hash still matches: code, strategy source + state, config, data files."""
    checks = {
        "market_tester code": (spec["code_sha256"], ledger.code_sha256()),
        "strategy": (spec["strategy"], strategy_id(strategy)),
        "strategy source": (spec["strategy_source_sha256"], strategy_source_sha256(strategy)),
        "strategy state": (spec["strategy_state"], strategy_state(strategy)),
        "configuration": (spec["config_sha256"], config_sha256(spec, strategy)),
    }
    for f, h in spec["data_files_sha256"].items():
        checks[f"data {Path(f).name}"] = (h, ledger.sha256_file(Path(f)) if Path(f).exists() else "missing")
    changed = [k for k, (a, b) in checks.items() if a != b]
    if changed:
        raise PreregError(f"changed since the prereg was frozen: {changed}")


@dataclass(eq=False)
class RunToken:
    """Key to the holdout for one pre-registered run. Only `open_run` creates live tokens."""
    item_id: str
    period: str
    prereg_sha256: str
    _secret: str = field(repr=False, default="")
    _live: bool = field(repr=False, default=False)


_LIVE: dict[int, str] = {}  # id(token) -> secret; tokens not in here (or revoked) are refused


def is_live(token) -> bool:
    return isinstance(token, RunToken) and token._live and _LIVE.get(id(token)) == token._secret != ""


@contextmanager
def open_run(path: Path, strategy):
    spec, sha = load(path)
    validate(spec)
    verify_frozen(spec, strategy)  # before the one-shot marker: a mismatch must not spend the run
    marker = config.QUANTUM_EXPERIMENTS_DIR / "holdout_opened" / f"{sha}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise PreregError(f"{marker} exists: this test already ran (one run, never re-run)") from None
    with os.fdopen(fd, "w") as f:
        json.dump({"opened_utc": datetime.now(timezone.utc).isoformat(), "prereg": str(path)}, f)
    token = RunToken(spec["item_id"], spec["period"], sha, secrets.token_hex(16), True)
    _LIVE[id(token)] = token._secret
    ledger.append(spec["item_id"], "holdout", spec["period"], prereg_sha256=sha, note="holdout opened")
    try:
        yield token
    finally:
        token._live = False
        _LIVE.pop(id(token), None)
