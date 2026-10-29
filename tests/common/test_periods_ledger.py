"""Shared loader guard and the TradingView ledger kinds."""

import pytest

from market_tester.common import ledger
from market_tester.common.periods import HoldoutLocked, load_range


def test_load_range_into_holdout_needs_a_holdout_look_row():
    with pytest.raises(HoldoutLocked):
        load_range("2023-12-01", "2024-02-01")
    other = ledger.append("X1", "decision", "all")
    with pytest.raises(HoldoutLocked):
        load_range("2023-12-01", "2024-02-01", holdout_look=other)


def test_tv_start_counts_as_trials():
    ledger.append("TV901", "tv_start", "full", family="fam_x", n_configs=40)
    ledger.append("TV902", "tv_start", "full", family="fam_x", n_configs=5)
    assert ledger.configs_tried("fam_x") == 45
    ledger.append("TV90", "holdout_look", "holdout", family="fam_x")
    assert ledger.configs_tried("fam_x") == 45


def test_code_hash_covers_both_testers_and_strategies():
    names = {p.relative_to(ledger.config.REPO_ROOT).as_posix() for p in ledger.code_files()}
    assert "market_tester/tradingview/broker.py" in names and "strategies/quantum/published.py" in names
    assert "strategies/tradingview/orb.py" in names
