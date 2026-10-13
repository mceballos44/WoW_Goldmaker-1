"""Copper <-> gold conversion and formatting.

All prices are stored internally as COPPER (10,000 copper = 1 gold).
Gold is only used for display and user input. A missing price is always
``None`` (or NaN inside DataFrames) - it is never silently turned into 0.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import pandas as pd

from .config import COPPER_PER_GOLD


def is_missing(value) -> bool:
    """True for None / NaN / pandas NA."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def copper_to_gold(copper) -> Optional[float]:
    """Convert copper to gold. Returns None when the price is missing."""
    if is_missing(copper):
        return None
    return float(copper) / COPPER_PER_GOLD


def gold_to_copper(gold) -> Optional[int]:
    """Convert gold to whole copper, rounding half up.

    Uses Decimal so values like 3.29 gold become exactly 32,900 copper.
    """
    if is_missing(gold):
        return None
    copper = Decimal(str(gold)) * COPPER_PER_GOLD
    return int(copper.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def series_copper_to_gold(series: pd.Series) -> pd.Series:
    """Vectorised copper -> gold. NaN stays NaN."""
    return pd.to_numeric(series, errors="coerce") / COPPER_PER_GOLD


def format_gold(copper, decimals: int = 2, missing: str = "n/a") -> str:
    """Format a copper amount as e.g. ``'1,234.50g'``."""
    gold = copper_to_gold(copper)
    if gold is None:
        return missing
    return f"{gold:,.{decimals}f}g"
