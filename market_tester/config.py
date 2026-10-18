"""Market settings. This repo trades CL (NYMEX Light Crude Oil) only.

MCL (Micro WTI) and every other root are banned: MCL is non-syariah for the owner.
"""

import re
from pathlib import Path

MARKET = "CL"
ALLOWED_ROOTS = frozenset({"CL"})

TICK = 0.01  # $ per barrel
TICK_VALUE = 10.0  # $ per tick on 1 contract (1,000 barrels)
FEE_PER_SIDE = 2.01  # $ per contract per side; $4.02 round trip (old repo baseline)
SLIPPAGE_TICKS_PER_SIDE = 1

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
BARS_DIR = DATA_DIR / "processed" / "ohlcv-1m"
DEFINITIONS_FILE = DATA_DIR / "processed" / "definitions" / "cl_contracts.parquet"
EIA_RELEASES_FILE = DATA_DIR / "reference" / "eia_releases.csv"

# Root = letters before the month code + year digit(s): "CLZ6" -> "CL", "MCLZ6" -> "MCL".
# Continuous symbols ("CL.v.0") have the root before the first dot.
_OUTRIGHT = re.compile(r"^([A-Z]+?)[FGHJKMNQUVXZ]\d{1,2}$")
CONTINUOUS_SYMBOLS = frozenset({"CL.v.0", "CL.v.1"})


class MarketNotAllowed(ValueError):
    pass


def root_of(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol.split(".", 1)[0]
    match = _OUTRIGHT.match(symbol)
    return match.group(1) if match else symbol


def is_cl_outright(symbol: str) -> bool:
    match = _OUTRIGHT.match(symbol.strip().upper())
    return bool(match) and match.group(1) in ALLOWED_ROOTS


def assert_allowed(symbol: str) -> str:
    """Return "CL" for bare CL, a CL outright or a supported continuous symbol; raise otherwise (MCL included)."""
    if not isinstance(symbol, str):
        raise MarketNotAllowed(f"{symbol!r} is not a symbol")
    root = root_of(symbol)
    if root not in ALLOWED_ROOTS:
        raise MarketNotAllowed(f"{symbol!r} (root {root!r}) is not allowed; this repo trades CL only")
    clean = symbol.strip()
    if clean.upper() != "CL" and clean not in CONTINUOUS_SYMBOLS and not is_cl_outright(clean):
        raise MarketNotAllowed(f"{symbol!r} is not a supported CL symbol")
    return root
