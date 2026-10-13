"""Milling calculator and empirical yield calibration (herb-named layer over ``conversion``).

Nothing here knows any "real" drop rate. Yields come only from
  (a) the user's own recorded experiments, or
  (b) numbers the user types in by hand.

All money is in COPPER (floats); convert with ``goldmaker.currency`` at the edges.
A missing price is ``None`` and is never treated as 0.

Assumptions kept explicit
-------------------------
* Revenue is linear in quantity: selling more units does NOT move the price.
  (The UI shows a liquidity check for that reason.)
* Auction House commission is a single percentage of the sale price. The
  per-auction rounding done by the game is ignored. Deposits are ignored
  (they are refunded when an auction sells).
* Unsold items are worth 0 (conservative); see ``sell_through``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import conversion
from .conversion import days_of_volume, t_critical_95  # noqa: F401  (re-exported)

MIN_EXPERIMENTS_MODERATE = conversion.MIN_EXPERIMENTS_MODERATE
MIN_HERBS_MODERATE = conversion.MIN_UNITS_MODERATE
WORDING = conversion.Wording("herb", "herbs", "milled")


# --- Experiments and yield estimates ---------------------------------------------------

@dataclass(frozen=True)
class Experiment:
    herb_item_id: int
    herbs_used: int
    outputs: dict                       # item_id -> quantity received
    output_names: dict = field(default_factory=dict)
    herb_name: Optional[str] = None
    id: Optional[int] = None
    performed_at: Optional[str] = None
    notes: str = ""

    @staticmethod
    def from_row(row: dict) -> "Experiment":
        """Build from the dict shape returned by ``database.list_milling_experiments``."""
        b = conversion.Batch.from_row(row)
        return Experiment(b.input_item_id, b.units_used, b.outputs, b.output_names, b.input_name,
                          b.id, b.performed_at, b.notes)

    def to_batch(self) -> conversion.Batch:
        return conversion.Batch(self.herb_item_id, self.herbs_used, self.outputs, self.output_names,
                                self.herb_name, self.id, self.performed_at, self.notes)


class YieldEstimate(conversion.YieldEstimate):
    @property
    def mean_per_herb(self) -> float:
        return self.mean_per_unit


class YieldSummary(conversion.YieldSummary):
    @property
    def total_herbs(self) -> int:
        return self.total_units


def estimate_yields(experiments: list) -> YieldSummary:
    """Pool experiments for ONE herb into per-herb yields (see ``conversion.estimate_yields``)."""
    s = conversion.estimate_yields([e.to_batch() for e in experiments], WORDING)
    return YieldSummary(s.n_experiments, s.total_units,
                        tuple(YieldEstimate(**vars(e)) for e in s.estimates),
                        s.reliability, s.warnings)


# --- Profit calculation -----------------------------------------------------------------

class MillingResult(conversion.ConversionResult):
    """``ConversionResult`` with herb-named aliases."""

    herb_price_copper = property(lambda self: self.input_price_copper)
    gross_per_herb = property(lambda self: self.gross_per_unit)
    net_per_herb = property(lambda self: self.net_per_unit)
    profit_per_herb = property(lambda self: self.profit_per_unit)
    profit_per_herb_low = property(lambda self: self.profit_per_unit_low)
    profit_per_herb_high = property(lambda self: self.profit_per_unit_high)


def calculate_milling(herb_price_copper: Optional[float], quantity: int, yields: dict,
                      output_prices: dict, names: Optional[dict] = None,
                      ah_cut: float = 0.05, sell_through: float = 1.0,
                      target_roi: float = 0.0, yield_bounds: Optional[dict] = None) -> MillingResult:
    """Expected profit of milling ``quantity`` herbs (see ``conversion.calculate_conversion``)."""
    try:
        result = conversion.calculate_conversion(
            herb_price_copper, quantity, yields, output_prices, names, ah_cut, sell_through,
            target_roi, yield_bounds, input_label="the herb")
    except ValueError as exc:
        raise ValueError(str(exc).replace("Input price", "Herb price")) from None
    return MillingResult(**vars(result))
