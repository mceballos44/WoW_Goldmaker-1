"""Milling calculator and empirical yield calibration.

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

import math
from dataclasses import dataclass, field
from typing import Optional

# Two-sided 95% Student-t critical values by degrees of freedom (standard table).
_T95 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
        8: 2.306, 9: 2.262, 10: 2.228, 12: 2.179, 15: 2.131, 20: 2.086,
        25: 2.060, 30: 2.042}

# Rule-of-thumb thresholds for the reliability label. These are this app's
# own heuristics, not statistical guarantees.
MIN_EXPERIMENTS_MODERATE = 5
MIN_HERBS_MODERATE = 200


def t_critical_95(degrees_of_freedom: int) -> Optional[float]:
    """95% two-sided t value; uses the next lower tabulated df (slightly wider)."""
    if degrees_of_freedom < 1:
        return None
    if degrees_of_freedom > 30:
        return 2.0
    usable = [df for df in _T95 if df <= degrees_of_freedom]
    return _T95[max(usable)]


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
        return Experiment(
            id=row.get("id"), herb_item_id=row["herb_item_id"], herb_name=row.get("herb_name"),
            herbs_used=row["herbs_used"], performed_at=row.get("performed_at"),
            notes=row.get("notes") or "",
            outputs={o["item_id"]: o["quantity"] for o in row["outputs"]},
            output_names={o["item_id"]: o.get("item_name") for o in row["outputs"]},
        )


@dataclass(frozen=True)
class YieldEstimate:
    item_id: int
    name: Optional[str]
    total_output: int
    mean_per_herb: float
    std_error: Optional[float]          # None with fewer than 2 experiments
    ci_low: Optional[float]
    ci_high: Optional[float]


@dataclass(frozen=True)
class YieldSummary:
    n_experiments: int
    total_herbs: int
    estimates: tuple
    reliability: str                    # none | single batch | low | moderate
    warnings: tuple

    def yields(self) -> dict:
        return {e.item_id: e.mean_per_herb for e in self.estimates}

    def bounds(self) -> dict:
        return {e.item_id: (e.ci_low, e.ci_high) for e in self.estimates
                if e.ci_low is not None and e.ci_high is not None}


def estimate_yields(experiments: list) -> YieldSummary:
    """Pool experiments for ONE herb into per-herb yields with uncertainty.

    The mean is total output / total herbs. The standard error treats each
    experiment as one observation (ratio estimator), so uncertainty needs at
    least two experiments. A single large batch gives a point estimate only:
    log several smaller batches to see how much yields vary.

    An output that did not appear in an experiment counts as 0 for it.
    """
    valid = [e for e in experiments if e.herbs_used > 0]
    k = len(valid)
    total_herbs = sum(e.herbs_used for e in valid)
    if k == 0:
        return YieldSummary(0, 0, (), "none", ("No experiments recorded for this herb.",))

    names: dict = {}
    for e in valid:
        for item_id, name in e.output_names.items():
            if name and item_id not in names:
                names[item_id] = name
    item_ids = sorted({i for e in valid for i in e.outputs})

    estimates = []
    for item_id in item_ids:
        total = sum(e.outputs.get(item_id, 0) for e in valid)
        mean = total / total_herbs
        se = low = high = None
        if k >= 2:
            residuals = sum((e.outputs.get(item_id, 0) - mean * e.herbs_used) ** 2 for e in valid)
            se = math.sqrt((k / (k - 1)) * residuals / total_herbs ** 2)
            t = t_critical_95(k - 1)
            low, high = max(0.0, mean - t * se), mean + t * se
        estimates.append(YieldEstimate(item_id, names.get(item_id), total, mean, se, low, high))

    warnings = []
    if k == 1:
        reliability = "single batch"
        warnings.append("Only one experiment: these yields are a point estimate with no "
                        "uncertainty. Do not treat one batch as a drop rate.")
    elif k < MIN_EXPERIMENTS_MODERATE or total_herbs < MIN_HERBS_MODERATE:
        reliability = "low"
    else:
        reliability = "moderate"
    if k >= 2 and k < MIN_EXPERIMENTS_MODERATE:
        warnings.append(f"Only {k} experiments (rule of thumb: at least {MIN_EXPERIMENTS_MODERATE}).")
    if k >= 2 and total_herbs < MIN_HERBS_MODERATE:
        warnings.append(f"Only {total_herbs} herbs milled in total "
                        f"(rule of thumb: at least {MIN_HERBS_MODERATE}).")
    return YieldSummary(k, total_herbs, tuple(estimates), reliability, tuple(warnings))


# --- Profit calculation -----------------------------------------------------------------

@dataclass(frozen=True)
class OutputLine:
    item_id: int
    name: Optional[str]
    yield_per_herb: float
    units: float                        # expected units for the whole quantity
    price_copper: Optional[float]
    gross_copper: Optional[float]       # units * price, before sell-through and fees


@dataclass(frozen=True)
class MillingResult:
    quantity: int
    complete: bool                      # every needed price is known
    missing_prices: tuple               # names / ids of items lacking a price
    lines: tuple
    herb_price_copper: Optional[float]
    ah_cut: float
    sell_through: float
    target_roi: float
    gross_per_herb: Optional[float]     # sum(yield * price)
    net_per_herb: Optional[float]       # after sell-through and AH cut
    profit_per_herb: Optional[float]
    profit_per_herb_low: Optional[float]
    profit_per_herb_high: Optional[float]
    max_purchase_price: Optional[float]  # highest herb price that still meets target ROI

    # -- totals for the chosen quantity --
    @property
    def cost(self) -> Optional[float]:
        return None if self.herb_price_copper is None else self.herb_price_copper * self.quantity

    @property
    def gross(self) -> Optional[float]:
        return None if self.gross_per_herb is None else self.gross_per_herb * self.quantity

    @property
    def fees(self) -> Optional[float]:
        if self.gross is None:
            return None
        return self.gross * self.sell_through * self.ah_cut

    @property
    def net_revenue(self) -> Optional[float]:
        return None if self.net_per_herb is None else self.net_per_herb * self.quantity

    @property
    def profit(self) -> Optional[float]:
        return None if self.profit_per_herb is None else self.profit_per_herb * self.quantity

    @property
    def roi(self) -> Optional[float]:
        """Profit divided by cost (0.25 = 25%). None if cost is unknown or zero."""
        if self.profit is None or not self.cost:
            return None
        return self.profit / self.cost

    def profit_for(self, herbs: int) -> Optional[float]:
        return None if self.profit_per_herb is None else self.profit_per_herb * herbs

    def cost_for(self, herbs: int) -> Optional[float]:
        return None if self.herb_price_copper is None else self.herb_price_copper * herbs

    def net_revenue_for(self, herbs: int) -> Optional[float]:
        return None if self.net_per_herb is None else self.net_per_herb * herbs


def calculate_milling(herb_price_copper: Optional[float], quantity: int, yields: dict,
                      output_prices: dict, names: Optional[dict] = None,
                      ah_cut: float = 0.05, sell_through: float = 1.0,
                      target_roi: float = 0.0, yield_bounds: Optional[dict] = None) -> MillingResult:
    """Expected profit of milling ``quantity`` herbs.

    ``yields``        item_id -> expected units per herb
    ``output_prices`` item_id -> copper per unit, or None when unknown
    ``yield_bounds``  item_id -> (low, high) yield per herb, for a rough range
    ``ah_cut``        commission as a fraction (0.05 = 5%)
    ``sell_through``  fraction of output expected to actually sell (unsold = worth 0)
    ``target_roi``    fraction used for the maximum purchase price (0 = break-even)

    A missing output price makes the result incomplete (profit is None); it is
    never treated as 0. The maximum purchase price needs only the output prices,
    so it is still returned when only the herb price is unknown.
    """
    if quantity < 0:
        raise ValueError("Quantity cannot be negative.")
    if not 0 <= ah_cut < 1:
        raise ValueError("AH cut must be at least 0 and below 1 (e.g. 0.05 for 5%).")
    if not 0 <= sell_through <= 1:
        raise ValueError("Sell-through must be between 0 and 1.")
    if target_roi <= -1:
        raise ValueError("Target ROI must be above -100%.")
    if any(y < 0 for y in yields.values()):
        raise ValueError("Yields cannot be negative.")
    if herb_price_copper is not None and herb_price_copper < 0:
        raise ValueError("Herb price cannot be negative.")
    names = names or {}

    lines, missing = [], []
    for item_id, per_herb in yields.items():
        price = output_prices.get(item_id)
        label = names.get(item_id) or f"item {item_id}"
        if price is None and per_herb > 0:
            missing.append(label)
        gross = None if price is None else per_herb * quantity * price
        lines.append(OutputLine(item_id, names.get(item_id), per_herb, per_herb * quantity,
                                price, gross))

    complete_outputs = not missing
    keep = (1 - ah_cut) * sell_through
    gross_per_herb = (sum(y * output_prices[i] for i, y in yields.items() if y > 0)
                      if complete_outputs else None)
    net_per_herb = None if gross_per_herb is None else gross_per_herb * keep

    profit_per_herb = None
    if net_per_herb is not None and herb_price_copper is not None:
        profit_per_herb = net_per_herb - herb_price_copper

    low = high = None
    if yield_bounds and net_per_herb is not None and herb_price_copper is not None:
        def net_at(index: int) -> float:
            total = 0.0
            for i, y in yields.items():
                if y <= 0:
                    continue
                bound = yield_bounds.get(i)
                total += (bound[index] if bound else y) * output_prices[i]
            return total * keep
        low, high = net_at(0) - herb_price_copper, net_at(1) - herb_price_copper

    max_price = None if net_per_herb is None else net_per_herb / (1 + target_roi)

    return MillingResult(
        quantity=quantity,
        complete=complete_outputs and herb_price_copper is not None,
        missing_prices=tuple(missing) + (() if herb_price_copper is not None else ("the herb",)),
        lines=tuple(lines), herb_price_copper=herb_price_copper, ah_cut=ah_cut,
        sell_through=sell_through, target_roi=target_roi, gross_per_herb=gross_per_herb,
        net_per_herb=net_per_herb, profit_per_herb=profit_per_herb,
        profit_per_herb_low=low, profit_per_herb_high=high, max_purchase_price=max_price,
    )


def days_of_volume(units: float, sold_per_day: Optional[float]) -> Optional[float]:
    """How many days of TSM-reported sales volume ``units`` would equal.

    ``None`` when the daily volume is unknown or zero.
    """
    if sold_per_day is None or sold_per_day != sold_per_day or sold_per_day <= 0:
        return None
    return units / sold_per_day
