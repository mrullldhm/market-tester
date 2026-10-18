"""Append-only experiment ledger (experiments/LEDGER.csv): every run and every holdout look is one row.

The ledger is how configurations tried are counted (LESSONS §6.1). Rows are never edited or deleted.
A dev run writes `dev_start` (with its config count) BEFORE it touches data, so crashed runs still count.
Trials are counted per research `family` (one idea and all its renamed variants), not per item id.
Holdout rows must carry the sha256 of a frozen pre-registration.
"""

import csv
import fcntl
import hashlib
import io
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config

COLUMNS = ["run_id", "utc", "item_id", "family", "kind", "period", "n_configs", "statistic", "value", "p_value",
           "verdict", "prereg_sha256", "code_sha256", "output", "note"]
KINDS = {"decision", "synthetic", "exploratory", "exploratory_result", "dev_start", "dev", "prereg", "holdout",
         "review"}
COUNTED = {"dev_start", "exploratory"}  # rows whose n_configs are trials
NEEDS_PREREG = {"holdout"}


FAMILY_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # also a file name under experiments/trials/


class LedgerError(ValueError):
    pass


def check_name(name: str) -> str:
    if not isinstance(name, str) or not FAMILY_NAME.fullmatch(name):
        raise LedgerError(f"{name!r} must be 1-64 letters, digits, '_' or '-'")
    return name


@contextmanager
def locked(path: Path):
    """Exclusive lock on `path`.lock for one read-check-append transaction."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def ledger_file() -> Path:
    return config.LEDGER_FILE  # read at call time so tests can point it elsewhere


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_files(files) -> str:
    """One hash over several files (sorted by path; each file's name and content)."""
    h = hashlib.sha256()
    for p in sorted(Path(f) for f in files):
        h.update(p.name.encode())
        h.update(sha256_file(p).encode())
    return h.hexdigest()


def code_sha256() -> str:
    return sha256_files(Path(__file__).parent.glob("*.py"))


def read() -> pd.DataFrame:
    path = ledger_file()
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def configs_tried(family: str | None = None) -> int:
    """Configurations already run in a family (all families if None): feeds the multiple-testing count."""
    rows = read()
    if not len(rows):
        return 0
    mine = rows[rows["kind"].isin(COUNTED)]
    if family is not None:
        mine = mine[mine["family"] == family]
    return int(pd.to_numeric(mine["n_configs"], errors="coerce").fillna(0).sum())


TRIAL_COLUMNS = ["run_id", "config", "sharpe_day"]


def trials_file(family: str) -> Path:
    return config.EXPERIMENTS_DIR / "trials" / f"{check_name(family)}.csv"


def append_trials(family: str, run_id: str, configs: list[dict], sharpe_day) -> None:
    """Per-day Sharpe of every config run in a family: the deflated Sharpe needs their spread."""
    path = trials_file(family)
    with locked(path):
        new = not path.exists()
        if not new:
            with open(path, newline="") as f:
                if next(csv.reader(f), []) != TRIAL_COLUMNS:
                    raise LedgerError(f"{path} has a foreign header")
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(TRIAL_COLUMNS)
            for c, s in zip(configs, sharpe_day):
                w.writerow([run_id, json.dumps(c, sort_keys=True, default=str), repr(float(s))])


def trial_sharpes(family: str) -> list[float]:
    path = trials_file(family)
    with locked(path):
        if not path.exists():
            return []
        return pd.read_csv(path)["sharpe_day"].astype(float).tolist()


def append(item_id: str, kind: str, period: str, family: str | None = None, **fields) -> str:
    """Write one row; returns its run_id. Unknown fields raise (typos must not vanish silently)."""
    if kind not in KINDS:
        raise LedgerError(f"unknown kind {kind!r}; one of {sorted(KINDS)}")
    if kind in NEEDS_PREREG and not fields.get("prereg_sha256"):
        raise LedgerError(f"a {kind} row needs prereg_sha256")
    extra = set(fields) - set(COLUMNS)
    if extra:
        raise LedgerError(f"unknown ledger fields {sorted(extra)}")
    family = check_name(item_id if family is None else family)
    check_name(item_id)
    path = ledger_file()
    with locked(path):  # id allocation + append as one transaction
        if path.exists():
            with open(path, newline="") as f:
                header = next(csv.reader(f), [])
            if header != COLUMNS:
                raise LedgerError(f"{path} header {header} differs from {COLUMNS}")
        rows = read()
        n = int((rows["item_id"] == item_id).sum()) if len(rows) else 0
        run_id = f"{item_id}-{n + 1:03d}"
        row = {c: "" for c in COLUMNS}
        row.update({k: "" if v is None else str(v) for k, v in fields.items()})
        row.update(run_id=run_id, utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                   item_id=item_id, family=family, kind=kind, period=period)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=COLUMNS)
        if not path.exists():
            w.writeheader()
        w.writerow(row)
        _append_whole(path, buf.getvalue().encode())
    return run_id


def _append_whole(path: Path, data: bytes) -> None:
    """Append all of `data` or nothing: loop over short writes; on any failure cut the file back."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        size = os.fstat(fd).st_size
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("ledger write made no progress")
                view = view[n:]
            os.fsync(fd)
        except BaseException:
            os.ftruncate(fd, size)
            os.fsync(fd)
            raise
    finally:
        os.close(fd)


def prereg_registered(prereg_sha: str) -> bool:
    rows = read()
    return bool(len(rows)) and bool(((rows["kind"] == "prereg") & (rows["prereg_sha256"] == prereg_sha)).any())
