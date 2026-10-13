"""Profit opportunity scanner: rank recipes and processing (milling, prospecting) together.

Everything here is a THEORETICAL margin at the current listed prices. Nothing is a
demonstrated sale, and listed inventory is never assumed to sell: liquidity is shown
as "days of TSM-reported daily volume" next to every row.

Units differ between kinds, so each row says what one *operation* is:
* crafting   - one craft
* processing - ``units_per_operation`` input units (for example the number of herbs a
  single mill uses), or one input unit when the user has not set it. The app does not
  know these numbers; they are user settings.

Gold per hour is shown only when the user has entered how long one operation takes.
Money is in COPPER.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import conversion
from .crafting import RecipeEvaluation, days_to_sell

SMALL_SAMPLE = ("single batch", "low")


@dataclass(frozen=True)
class Opportunity:
    kind: str                           # crafting | milling | prospecting
    name: str
    key: str                            # recipe key or input item id
    operation: str                      # what one operation is, e.g. "1 craft", "5 ore"
    cost_per_op: Optional[float]
    profit_per_op: Optional[float]
    roi: Optional[float]
    investment: Optional[float]         # cost for the planned number of operations
    seconds_per_op: Optional[float]
    liquidity_days: Optional[float]     # days of TSM daily volume to sell the planned output
    reliability: str
    flags: tuple

    @property
    def complete(self) -> bool:
        return self.profit_per_op is not None

    @property
    def profit_per_hour(self) -> Optional[float]:
        if self.profit_per_op is None or not self.seconds_per_op:
            return None
        return self.profit_per_op * 3600 / self.seconds_per_op


def from_recipes(evaluations: list, operations: int, seconds_per_craft: Optional[float] = None,
                 sell_through: float = 1.0) -> list:
    """One opportunity per evaluated recipe (see ``crafting.evaluate_recipes``)."""
    found = []
    for e in evaluations:
        res = e.result
        units = res.output_units * operations * sell_through
        found.append(Opportunity(
            kind="crafting", name=e.recipe.name, key=e.recipe.key, operation="1 craft",
            cost_per_op=res.cost_per_craft, profit_per_op=res.profit_per_craft, roi=res.roi,
            investment=res.cost_for(operations), seconds_per_op=seconds_per_craft,
            liquidity_days=days_to_sell(units, e.sold_per_day),
            reliability="recipe verified" if e.recipe.last_verified else "recipe unverified",
            flags=e.flags))
    return found


def from_conversion(process: str, input_item_id: int, input_name: Optional[str],
                    summary: conversion.YieldSummary, price_of: Callable, demand_of: Callable,
                    operations: int, units_per_operation: Optional[int] = None,
                    seconds_per_op: Optional[float] = None, ah_cut: float = 0.05,
                    sell_through: float = 1.0,
                    extra_cost_per_unit: float = 0.0) -> Optional[Opportunity]:
    """One opportunity for an input item with recorded experiments (None without any).

    ``price_of(item_id)``  -> ``pricing.PriceResult``
    ``demand_of(item_id)`` -> ``(sold_per_day, sale_rate)``
    """
    if summary.n_experiments == 0:
        return None
    per_op = units_per_operation or 1
    looked_up = {i: price_of(i) for i in {input_item_id, *summary.yields()}}
    prices = {i: (None if p.is_missing else p.copper) for i, p in looked_up.items()}
    names = {e.item_id: e.name or (looked_up[e.item_id].name) for e in summary.estimates}
    name = input_name or looked_up[input_item_id].name or f"item {input_item_id}"
    result = conversion.calculate_conversion(
        prices[input_item_id], per_op * operations, summary.yields(), prices, names, ah_cut,
        sell_through, extra_cost_per_unit=extra_cost_per_unit, input_label=name)

    flags = []
    if result.missing_prices:
        flags.append("no price: " + ", ".join(result.missing_prices))
    if summary.reliability in SMALL_SAMPLE:
        flags.append(f"small sample ({summary.n_experiments} experiment(s), "
                     f"{summary.total_units} units)")
    stale = [p.name or str(i) for i, p in looked_up.items()
             if not p.is_missing and p.is_stale and not p.is_override]
    if stale:
        flags.append("stale prices")
    if units_per_operation is None:
        flags.append("operation size not set (per 1 unit)")

    days = []
    for line in result.lines:
        d = conversion.days_of_volume(line.units * sell_through, demand_of(line.item_id)[0])
        if d is not None:
            days.append(d)

    return Opportunity(
        kind=process, name=name, key=str(input_item_id), operation=f"{per_op} {name}",
        cost_per_op=result.cost_for(per_op), profit_per_op=result.profit_for(per_op),
        roi=result.roi, investment=result.cost, seconds_per_op=seconds_per_op,
        liquidity_days=max(days) if days else None, reliability=summary.reliability,
        flags=tuple(flags))


RANKINGS = {
    "Profit per operation": lambda o: o.profit_per_op,
    "ROI": lambda o: o.roi,
    "Profit per hour": lambda o: o.profit_per_hour,
    "Lowest investment": lambda o: None if o.investment is None else -o.investment,
    "Fastest to sell": lambda o: None if o.liquidity_days is None else -o.liquidity_days,
}


def rank(opportunities: list, by: str = "Profit per operation") -> list:
    """Best first; rows without a value for the chosen measure go last (never treated as 0)."""
    measure = RANKINGS[by]
    return sorted(opportunities, key=lambda o: (measure(o) is None, -(measure(o) or 0.0)))
