import pandas as pd
import pytest

from market_tester.config import BARS_DIR
from market_tester.data import DataIntegrityError, RollCrossed, add_roll_flag, check_same_contract, load_bars

needs_data = pytest.mark.skipif(not (BARS_DIR / "year=2026").exists(), reason="price data not present")


@needs_data
def test_load_front_2026():
    bars = load_bars([2026])
    assert set(bars["continuous_symbol"]) == {"CL.v.0"}
    assert bars["raw_symbol"].str.match(r"^CL[FGHJKMNQUVXZ]\d$").all()
    assert bars["ts_event_utc"].is_monotonic_increasing
    assert not bars.duplicated("ts_event_utc").any()


@needs_data
def test_known_roll_2026_05_18():
    bars = load_bars([2026])
    rolls = bars[bars["is_roll"]]
    hit = rolls[(rolls["ts_event_utc"].dt.date == pd.Timestamp("2026-05-18").date()) & (rolls["raw_symbol"] == "CLN6")]
    assert len(hit) == 1
    before = bars.loc[hit.index[0] - 1]
    assert before["raw_symbol"] == "CLM6"
    assert hit.iloc[0]["open_ticks"] - before["close_ticks"] == pytest.approx(-417, abs=30)


@needs_data
def test_both_series_rolls_never_in_us_daytime():
    bars = load_bars([2024, 2025], continuous=None)
    assert set(bars["continuous_symbol"]) == {"CL.v.0", "CL.v.1"}
    hours = bars.loc[bars["is_roll"], "ts_event_utc"].dt.tz_convert("America/New_York").dt.hour
    assert not hours.between(8, 16).any()


def test_roll_flag_per_series():
    bars = pd.DataFrame({
        "continuous_symbol": ["CL.v.0"] * 3 + ["CL.v.1"] * 3,
        "raw_symbol": ["CLZ6", "CLZ6", "CLF7", "CLF7", "CLF7", "CLG7"],
    })
    assert add_roll_flag(bars)["is_roll"].tolist() == [False, False, True, False, False, True]


@pytest.mark.parametrize("entry, exit_", [("CLZ6", "CLF7"), ("MCLZ6", "MCLZ6"), (None, None), ("CL.v.0", "CL.v.0")])
def test_check_same_contract_rejects(entry, exit_):
    with pytest.raises(RollCrossed):
        check_same_contract(entry, exit_)


def test_check_same_contract_accepts():
    check_same_contract("CLZ6", "CLZ6")


def test_null_raw_symbol_rejected():
    bars = pd.DataFrame({"continuous_symbol": ["CL.v.0"] * 3, "raw_symbol": ["CLM6", None, "CLN6"]})
    with pytest.raises(DataIntegrityError):
        add_roll_flag(bars)


@pytest.mark.parametrize("kwargs", [{"years": [2024, 2026]}, {"years": [2026], "continuous": "MCL.v.0"}])
def test_bad_load_arguments(kwargs):
    with pytest.raises(ValueError):
        load_bars(**kwargs)


@needs_data
def test_contiguous_year_boundary_keeps_real_rolls_only():
    bars = load_bars([2025, 2026])
    jan = bars[bars["ts_event_utc"].between("2025-12-31", "2026-01-03", inclusive="left")]
    assert jan["raw_symbol"].nunique() == 1 + jan["is_roll"].sum()
    assert len(bars[bars["is_roll"]]) == len(load_bars([2025]).query("is_roll")) + len(load_bars([2026]).query("is_roll")) + int(
        load_bars([2025])["raw_symbol"].iloc[-1] != load_bars([2026])["raw_symbol"].iloc[0])
