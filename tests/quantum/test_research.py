"""Ledger, pre-registration, holdout lock, sessions."""

import json
import shutil

import pandas as pd
import pytest

from market_tester.common import config, ledger
from market_tester.quantum import prereg
from market_tester.quantum.periods import DEV, HOLDOUT, HoldoutLocked, Period, load_period
from market_tester.common.sessions import add_time_columns, eia_blackout
from market_tester.quantum.toys import CoinFlip, TimeOfDay


SPEC = {"item_id": "T001", "hypothesis": "test", "strategy": "market_tester.quantum.toys:TimeOfDay", "params": {"hold": 60},
        "period": "holdout", "gates": {}, "slip_ticks": 1, "seed": 0, "approved_by_user": True, "codex_review": "R001"}


@pytest.fixture
def data_file(tmp_path):
    f = tmp_path / "bars.parquet"
    f.write_bytes(b"frozen bars")
    return f


def test_ledger_counts_by_family_before_results(tmp_experiments):
    assert ledger.append("S001", "dev_start", "dev", family="F", n_configs=12) == "S001-001"
    ledger.append("S001", "dev", "dev", family="F", n_configs=12)  # result row: not counted again
    ledger.append("S002", "dev_start", "dev", family="F", n_configs=3)  # renamed item, same family
    ledger.append("S003", "dev_start", "dev", n_configs=5)  # own family
    assert ledger.configs_tried("F") == 15 and ledger.configs_tried("S003") == 5 and ledger.configs_tried() == 20
    with pytest.raises(ledger.LedgerError):
        ledger.append("S001", "holdout", "holdout")
    with pytest.raises(ledger.LedgerError):
        ledger.append("S001", "dev", "dev", typo=1)


def test_ledger_refuses_foreign_header(tmp_experiments):
    config.LEDGER_FILE.write_text("run_id,utc\n")
    with pytest.raises(ledger.LedgerError):
        ledger.append("S001", "dev", "dev")


def test_prereg_one_shot(tmp_experiments, data_file):
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    with pytest.raises(prereg.PreregError):
        prereg.register(SPEC, TimeOfDay(), [data_file])
    with prereg.open_run(path, TimeOfDay()) as token:
        assert prereg.is_live(token)
    assert not prereg.is_live(token)
    with pytest.raises(prereg.PreregError):
        with prereg.open_run(path, TimeOfDay()):
            pass
    assert ledger.read()["kind"].tolist() == ["prereg", "holdout"]


def test_prereg_copy_cannot_open_twice(tmp_experiments, data_file):
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    copy = tmp_experiments / "elsewhere" / "prereg.json"
    copy.parent.mkdir()
    copy.write_bytes(path.read_bytes())
    with pytest.raises(prereg.PreregError):
        with prereg.open_run(copy, TimeOfDay()):
            pass
    with prereg.open_run(path, TimeOfDay()):
        pass
    shutil.rmtree(path.parent)  # even with the item folder gone, the central marker blocks a re-run
    path.parent.mkdir()
    copy_back = path.parent / "prereg.json"
    copy_back.write_bytes(copy.read_bytes())
    with pytest.raises(prereg.PreregError):
        with prereg.open_run(copy_back, TimeOfDay()):
            pass


def test_prereg_same_config_twice_refused(tmp_experiments, data_file):
    prereg.register(SPEC, TimeOfDay(), [data_file])
    with pytest.raises(prereg.PreregError):
        prereg.register(dict(SPEC, item_id="T002"), TimeOfDay(), [data_file])


def test_prereg_frozen_inputs(tmp_experiments, data_file):
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    with pytest.raises(prereg.PreregError):  # another instance state
        with prereg.open_run(path, TimeOfDay(name="other")):
            pass
    with pytest.raises(prereg.PreregError):  # another strategy class
        with prereg.open_run(path, CoinFlip()):
            pass
    data_file.write_bytes(b"changed")
    with pytest.raises(prereg.PreregError):
        with prereg.open_run(path, TimeOfDay()):
            pass
    assert not (tmp_experiments / "holdout_opened").exists() or not any((tmp_experiments / "holdout_opened").iterdir())


def test_prereg_rejects_bad_specs(tmp_experiments, data_file):
    bad = [dict(SPEC, approved_by_user=False), dict(SPEC, params=[{"a": 1}]), dict(SPEC, slip_ticks=-1),
           dict(SPEC, gates={"min_trades": 0}), dict(SPEC, gates={"p_one_sided_max": 1}),
           dict(SPEC, gates={"stress_slip_ticks": -1}), dict(SPEC, gates={"net_usd_per_trade_gt": -100}),
           dict(SPEC, gates={"made_up": 1}), dict(SPEC, strategy="market_tester.quantum.toys:CoinFlip")]
    for spec in bad:
        with pytest.raises(prereg.PreregError):
            prereg.register(spec, TimeOfDay(), [data_file])
    assert prereg.holdout_gates({"min_trades": 500, "p_one_sided_max": 0.01})["min_trades"] == 500


def test_prereg_rejects_edit(tmp_experiments, data_file):
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    spec = json.loads(path.read_text())
    path.write_text(json.dumps(dict(spec, params={"hold": 30})))
    with pytest.raises(prereg.PreregError):
        with prereg.open_run(path, TimeOfDay()):
            pass


def test_holdout_locked_without_token():
    with pytest.raises(HoldoutLocked):
        load_period(HOLDOUT)
    with pytest.raises(HoldoutLocked):
        load_period(HOLDOUT, token=prereg.RunToken("x", "holdout", "sha"))  # forged token
    fake = Period("dev", HOLDOUT.first_day, HOLDOUT.last_day, protected=False)
    with pytest.raises(ValueError):
        load_period(fake)  # home-made period cannot unlock holdout dates


def test_periods_do_not_overlap():
    assert DEV.last_day < HOLDOUT.first_day
    assert HOLDOUT.last_day == pd.Timestamp("2026-10-13")


def test_trading_day_boundary_and_dst():
    ts = pd.Series(pd.to_datetime(["2024-03-08 21:59", "2024-03-10 22:00", "2024-07-01 21:59", "2024-07-01 22:00"],
                                  utc=True))
    t = add_time_columns(pd.DataFrame({"ts_event_utc": ts}))
    # 2024-03-10 22:00 UTC = 18:00 EDT Sunday -> Monday 03-11; 2024-07-01 22:00 UTC = 18:00 EDT -> 07-02
    assert t["trading_day"].dt.strftime("%Y-%m-%d").tolist() == ["2024-03-08", "2024-03-11", "2024-07-01", "2024-07-02"]
    assert t["et_minute"].tolist() == [16 * 60 + 59, 18 * 60, 17 * 60 + 59, 18 * 60]


def test_eia_blackout_window():
    rel = pd.DatetimeIndex(pd.to_datetime(["2024-07-03 14:30"], utc=True))  # 10:30 ET
    ts = pd.Series(pd.to_datetime(["2024-07-03 14:14", "2024-07-03 14:15", "2024-07-03 14:45", "2024-07-03 14:46"],
                                  utc=True))
    assert eia_blackout(ts, 15, rel).tolist() == [False, True, True, False]


def test_eia_file_times_are_et():
    from market_tester.common.sessions import eia_release_times
    if not config.EIA_RELEASES_FILE.exists():
        pytest.skip("reference data not present")
    t = eia_release_times()
    assert len(t) > 1000
    assert set(t.tz_convert("America/New_York").strftime("%H:%M")) <= {"10:30", "11:00", "12:00"}


def test_unsafe_names_refused(tmp_experiments):
    for bad in ("../LEDGER", "/etc/x", "a b", ""):
        with pytest.raises(ledger.LedgerError):
            ledger.append("S001", "dev_start", "dev", family=bad, n_configs=1)
        with pytest.raises(ledger.LedgerError):
            ledger.trials_file(bad)


def test_prereg_rejects_nan_and_bool_numbers(tmp_experiments, data_file):
    for spec in (dict(SPEC, gates={"p_one_sided_max": float("nan")}), dict(SPEC, slip_ticks=True),
                 dict(SPEC, gates={"min_trades": True}), dict(SPEC, item_id="../x")):
        with pytest.raises(prereg.PreregError):
            prereg.register(spec, TimeOfDay(), [data_file])


def test_prereg_failed_ledger_write_leaves_nothing(tmp_experiments, data_file, monkeypatch):
    def fail(*a, **k):
        raise OSError("disk full")

    real_append = ledger.append
    monkeypatch.setattr(ledger, "append", fail)
    with pytest.raises(OSError):
        prereg.register(SPEC, TimeOfDay(), [data_file])
    monkeypatch.setattr(ledger, "append", real_append)  # not undo(): that would also drop tmp_experiments
    assert not prereg.canonical_path("T001").exists()
    assert prereg.register(SPEC, TimeOfDay(), [data_file]).exists()  # the config can still be registered


def test_concurrent_registrations_one_wins(tmp_experiments, data_file):
    from concurrent.futures import ThreadPoolExecutor

    def reg(i):
        try:
            prereg.register(dict(SPEC, item_id=f"C{i:03d}"), TimeOfDay(), [data_file])
            return True
        except prereg.PreregError:
            return False

    with ThreadPoolExecutor(8) as pool:
        assert sum(pool.map(reg, range(8))) == 1


def test_prereg_crash_while_writing_leaves_no_file(tmp_experiments, data_file, monkeypatch):
    import os
    real_link, calls = os.link, []

    def link_then_crash(src, dst):
        calls.append(dst)
        if len(calls) == 2:  # the prereg.json write, after the claim landed
            raise OSError("disk full")
        real_link(src, dst)

    monkeypatch.setattr(prereg.os, "link", link_then_crash)
    with pytest.raises(OSError):
        prereg.register(SPEC, TimeOfDay(), [data_file])
    monkeypatch.setattr(prereg.os, "link", real_link)
    assert not prereg.canonical_path("T001").exists()
    assert not any((tmp_experiments / "registered").iterdir())
    assert not [p for p in (tmp_experiments / "T001").iterdir()]  # no half-written or temp files
    assert prereg.register(SPEC, TimeOfDay(), [data_file]).exists()


def test_prereg_ledger_row_written_then_crash_allows_retry(tmp_experiments, data_file, monkeypatch):
    real_append = ledger.append

    def append_then_crash(*a, **k):
        real_append(*a, **k)
        raise OSError("crash after the row landed")

    monkeypatch.setattr(ledger, "append", append_then_crash)
    with pytest.raises(OSError):
        prereg.register(SPEC, TimeOfDay(), [data_file])
    monkeypatch.setattr(ledger, "append", real_append)
    assert len(ledger.read()) == 1 and not prereg.canonical_path("T001").exists()  # orphan row, no files
    path = prereg.register(SPEC, TimeOfDay(), [data_file])  # retry is not blocked by the orphan row
    with prereg.open_run(path, TimeOfDay()):
        pass


def test_newline_names_refused():
    with pytest.raises(ledger.LedgerError):
        ledger.check_name("family\n")


@pytest.mark.parametrize("killed_after", ["claim", "prereg_file"])
def test_prereg_process_killed_mid_registration_can_retry(killed_after, tmp_experiments, data_file):
    # simulate a SIGKILL: leave the files exactly as a dead process would, no ledger row
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    claim = next((tmp_experiments / "registered").iterdir())
    config.LEDGER_FILE.unlink()  # no row was ever written
    if killed_after == "claim":
        path.unlink()
    assert claim.exists()
    again = prereg.register(SPEC, TimeOfDay(), [data_file])
    with prereg.open_run(again, TimeOfDay()):
        pass


def test_opened_holdout_config_never_reregistered(tmp_experiments, data_file):
    path = prereg.register(SPEC, TimeOfDay(), [data_file])
    with prereg.open_run(path, TimeOfDay()):
        pass
    path.unlink()  # tampering: delete the prereg after the holdout ran
    for f in (tmp_experiments / "registered").iterdir():
        f.unlink()
    with pytest.raises(prereg.PreregError):
        prereg.register(dict(SPEC, item_id="T009"), TimeOfDay(), [data_file])


def test_ledger_short_write_rolls_back(tmp_experiments, monkeypatch):
    import os
    ledger.append("S001", "dev_start", "dev", n_configs=1)
    before = config.LEDGER_FILE.read_bytes()
    real_write = os.write

    def half_then_fail(fd, data):
        if len(data) > 10:
            return real_write(fd, bytes(data[: len(data) // 2]))
        raise OSError("disk full")

    monkeypatch.setattr(ledger.os, "write", half_then_fail)
    with pytest.raises(OSError):
        ledger.append("S001", "dev_start", "dev", n_configs=1)
    monkeypatch.setattr(ledger.os, "write", real_write)
    assert config.LEDGER_FILE.read_bytes() == before
    assert ledger.append("S001", "dev_start", "dev", n_configs=1) == "S001-002"
