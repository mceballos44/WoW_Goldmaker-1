"""AH investment calculator: buy now (e.g. before a patch), sell later.

Pure functions, money in COPPER. Every future price and sell-through figure is the
user's own estimate - the app has no forecast. Three scenarios (bearish / baseline /
bullish) show how sensitive the result is to those guesses.

Assumptions kept explicit
-------------------------
* The AH commission is one percentage of each sale; per-auction rounding is ignored.
* Units that do not sell are worth 0 (conservative). Re-listing costs and lost
  deposits go into ``other_costs`` as one total.
* Selling does not move the price (no price impact).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

SCENARIO_NAMES = ("Bearish", "Baseline", "Bullish")


@dataclass(frozen=True)
class Scenario:
    name: str
    resale_price_copper: float
    sell_through: float                 # 0..1 share of the units that sell


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    resale_price_copper: float
    sell_through: float
    units_sold: float
    gross: float
    fees: float
    net_revenue: float
    profit: float
    roi: Optional[float]
    breakeven_resale_price: Optional[float]   # at this scenario's sell-through


@dataclass(frozen=True)
class InvestmentResult:
    quantity: int
    buy_price_copper: float
    other_costs_copper: float
    ah_cut: float
    investment_cost: float              # quantity * buy price + other costs
    scenarios: tuple

    def scenario(self, name: str) -> Optional[ScenarioResult]:
        return next((s for s in self.scenarios if s.name == name), None)


def default_scenarios(expected_price_copper: float, sell_through: float,
                      spread: float = 0.2) -> list:
    """Bearish / baseline / bullish around the user's expected price.

    ``spread`` is the user's chosen +/- share (0.2 = 20% lower / higher); it is not a forecast.
    """
    if not 0 <= spread < 1:
        raise ValueError("Spread must be at least 0 and below 100%.")
    return [Scenario("Bearish", expected_price_copper * (1 - spread), sell_through),
            Scenario("Baseline", expected_price_copper, sell_through),
            Scenario("Bullish", expected_price_copper * (1 + spread), sell_through)]


def calculate_investment(buy_price_copper: float, quantity: int, scenarios: list,
                         ah_cut: float = 0.05, other_costs_copper: float = 0.0) -> InvestmentResult:
    """Cost, revenue, profit, ROI and break-even resale price for each scenario."""
    if buy_price_copper is None:
        raise ValueError("Enter the current (buy) price - a missing price is never treated as 0.")
    if buy_price_copper < 0:
        raise ValueError("Buy price cannot be negative.")
    if quantity <= 0:
        raise ValueError("Quantity must be at least 1.")
    if not 0 <= ah_cut < 1:
        raise ValueError("AH cut must be at least 0 and below 1 (e.g. 0.05 for 5%).")
    if other_costs_copper < 0:
        raise ValueError("Other costs cannot be negative.")
    cost = buy_price_copper * quantity + other_costs_copper

    results = []
    for s in scenarios:
        if s.resale_price_copper is None:
            raise ValueError(f"{s.name}: enter a resale price.")
        if s.resale_price_copper < 0:
            raise ValueError(f"{s.name}: resale price cannot be negative.")
        if not 0 <= s.sell_through <= 1:
            raise ValueError(f"{s.name}: sell-through must be between 0 and 100%.")
        sold = quantity * s.sell_through
        gross = sold * s.resale_price_copper
        fees = gross * ah_cut
        net = gross - fees
        profit = net - cost
        per_unit_keep = sold * (1 - ah_cut)
        results.append(ScenarioResult(
            name=s.name, resale_price_copper=s.resale_price_copper, sell_through=s.sell_through,
            units_sold=sold, gross=gross, fees=fees, net_revenue=net, profit=profit,
            roi=profit / cost if cost else None,
            breakeven_resale_price=cost / per_unit_keep if per_unit_keep > 0 else None))
    return InvestmentResult(quantity, buy_price_copper, other_costs_copper, ah_cut, cost, tuple(results))
