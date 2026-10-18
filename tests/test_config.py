import pytest

from market_tester.config import MarketNotAllowed, assert_allowed, root_of


@pytest.mark.parametrize("symbol", ["CL", "CLZ6", "CLF26", "CL.v.0", "CL.v.1", "clq6"])
def test_cl_allowed(symbol):
    assert assert_allowed(symbol) == "CL"


@pytest.mark.parametrize("symbol", ["MCL", "MCLZ6", "MCL.v.0", "QM", "QMZ6", "BZZ6", "RB.v.0", "ES"])
def test_everything_else_rejected(symbol):
    with pytest.raises(MarketNotAllowed):
        assert_allowed(symbol)


def test_root_of():
    assert root_of("MCLZ6") == "MCL"
    assert root_of("CLH5") == "CL"


@pytest.mark.parametrize("symbol", ["MCLF26", "CL.anything", "CL.", "CL.v.2", "CLZ", None])
def test_malformed_or_banned_rejected(symbol):
    with pytest.raises(MarketNotAllowed):
        assert_allowed(symbol)
