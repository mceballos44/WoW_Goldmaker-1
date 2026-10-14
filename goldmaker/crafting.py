"""Generic crafting profit calculator (pure functions, money in copper).

What is modelled, and what deliberately is not
----------------------------------------------
* Material cost, output quantity, AH commission, sell-through, break-even price.
* Quality-specific reagents as *alternatives within a slot* (cheapest chosen
  unless the caller picks one).
* Non-auctionable reagents: priced by an opportunity cost the user sets; if
  none is set they are counted as free AND reported as an assumption.
* Inventory limits: how many crafts the user's on-hand stock allows.
* Resourcefulness and Multicraft are modelled as two SEPARATE expected-value
  effects with user-supplied parameters:
    - Resourcefulness: chance x (fraction of ELIGIBLE reagent quantity saved)
    - Multicraft:      chance x (extra output as a fraction of base output)
  The app does not know the real magnitudes. They are applied only when the
  user enters BOTH the chance and the magnitude; otherwise they are skipped and
  the result says so. No reagent is assumed eligible unless flagged.
* Output quality: each quality tier is its own item ID with its own price. The
  app never guesses tier odds - ``analyse_quality_tiers`` shows the profit if
  every craft lands in one tier, and an expected profit only from odds the user
  enters (they must add up to 100%).
* Break-even material price (``breakeven_reagent_price``): the highest price of
  one reagent at which a craft still breaks even.
* Mechanics can be filled from a saved profession profile
  (``mechanics_from_profile``); the size of each effect must be measured by the user.
* NOT modelled yet: Ingenuity, concentration, skill thresholds / recipe difficulty,
  price impact of selling many items.

A missing price makes the result incomplete (profit is None); it is never 0.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional

from .recipes import Reagent, Recipe

NOT_MODELLED_NOTE = ("Ingenuity, concentration, skill vs recipe difficulty and price impact are not "
                     "modelled in this calculator.")


@dataclass(frozen=True)
class Mechanics:
    resourcefulness_chance: float = 0.0                       # 0..1
    resourcefulness_saved_fraction: Optional[float] = None    # 0..1 of eligible reagent quantity
    multicraft_chance: float = 0.0                            # 0..1
    multicraft_extra_fraction: Optional[float] = None         # >= 0, extra output / base output

    def validate(self) -> None:
        for label, value in (("Resourcefulness chance", self.resourcefulness_chance),
                             ("Multicraft chance", self.multicraft_chance)):
            if not 0 <= value <= 1:
                raise ValueError(f"{label} must be between 0 and 1.")
        f = self.resourcefulness_saved_fraction
        if f is not None and not 0 <= f <= 1:
            raise ValueError("Resourcefulness saved fraction must be between 0 and 1.")
        if self.multicraft_extra_fraction is not None and self.multicraft_extra_fraction < 0:
            raise ValueError("Multicraft extra fraction cannot be negative.")

    @property
    def resourcefulness_applied(self) -> bool:
        return self.resourcefulness_chance > 0 and self.resourcefulness_saved_fraction is not None

    @property
    def multicraft_applied(self) -> bool:
        return self.multicraft_chance > 0 and self.multicraft_extra_fraction is not None


@dataclass(frozen=True)
class ReagentLine:
    slot: int
    item_id: int
    name: Optional[str]
    quantity: int
    quality_label: str
    auctionable: bool
    savings_eligible: bool
    unit_price_copper: Optional[float]
    price_basis: str                  # market | opportunity cost | assumed free | missing
    line_cost_copper: Optional[float]
    on_hand: Optional[int]


@dataclass(frozen=True)
class CraftResult:
    complete: bool
    missing: tuple
    assumptions: tuple
    notes: tuple
    lines: tuple                       # the CHOSEN reagent of each slot
    output_units: float                # expected units per craft
    output_price_copper: Optional[float]
    ah_cut: float
    sell_through: float
    base_cost: Optional[float]         # reagent cost per craft before any savings
    expected_savings: Optional[float]
    cost_per_craft: Optional[float]    # expected, after savings
    gross_per_craft: Optional[float]
    net_per_craft: Optional[float]     # after sell-through and AH cut
    profit_per_craft: Optional[float]
    breakeven_output_price: Optional[float]
    crafts_possible: Optional[int]     # limited by on-hand stock; None = no stock limits known
    limiting_item: Optional[str]

    @property
    def roi(self) -> Optional[float]:
        if self.profit_per_craft is None or not self.cost_per_craft:
            return None
        return self.profit_per_craft / self.cost_per_craft

    def profit_for(self, crafts: int) -> Optional[float]:
        return None if self.profit_per_craft is None else self.profit_per_craft * crafts

    def cost_for(self, crafts: int) -> Optional[float]:
        return None if self.cost_per_craft is None else self.cost_per_craft * crafts

    def net_for(self, crafts: int) -> Optional[float]:
        return None if self.net_per_craft is None else self.net_per_craft * crafts


def _unit_price(reagent: Reagent, prices: dict, opportunity: dict) -> tuple:
    """(unit price or None, basis)."""
    if reagent.auctionable:
        price = prices.get(reagent.item_id)
        return price, ("market" if price is not None else "missing")
    cost = opportunity.get(reagent.item_id)
    if cost is None:
        return 0.0, "assumed free"
    return cost, "opportunity cost"


def _pick(options: list, choice: Optional[int], prices: dict, opportunity: dict) -> Reagent:
    """Use the caller's choice, else the cheapest priced alternative."""
    if choice is not None:
        for option in options:
            if option.item_id == choice:
                return option
    best, best_cost = None, None
    for option in options:
        price, basis = _unit_price(option, prices, opportunity)
        if basis == "missing":
            continue
        cost = price * option.quantity
        if best_cost is None or cost < best_cost:
            best, best_cost = option, cost
    return best or options[0]


def calculate_craft(recipe: Recipe, prices: dict, output_price: Optional[float],
                    opportunity_costs: Optional[dict] = None, on_hand: Optional[dict] = None,
                    choices: Optional[dict] = None, names: Optional[dict] = None,
                    ah_cut: float = 0.05, sell_through: float = 1.0,
                    mechanics: Optional[Mechanics] = None) -> CraftResult:
    """Expected profit of ONE craft (scale with ``profit_for``).

    ``prices``            item_id -> copper (None = unknown) for auctionable reagents
    ``output_price``      copper per unit of the crafted item (None = unknown)
    ``opportunity_costs`` item_id -> copper, for non-auctionable reagents
    ``on_hand``           item_id -> units in stock (limits ``crafts_possible``)
    ``choices``           slot -> item_id picked among a slot's alternatives
    """
    if not 0 <= ah_cut < 1:
        raise ValueError("AH cut must be at least 0 and below 1 (e.g. 0.05 for 5%).")
    if not 0 <= sell_through <= 1:
        raise ValueError("Sell-through must be between 0 and 1.")
    if output_price is not None and output_price < 0:
        raise ValueError("Output price cannot be negative.")
    mechanics = mechanics or Mechanics()
    mechanics.validate()
    opportunity_costs = opportunity_costs or {}
    on_hand = on_hand or {}
    choices = choices or {}
    names = names or {}

    lines, missing, assumptions = [], [], []
    for slot, options in recipe.slots().items():
        reagent = _pick(options, choices.get(slot), prices, opportunity_costs)
        price, basis = _unit_price(reagent, prices, opportunity_costs)
        label = reagent.item_name or names.get(reagent.item_id) or f"item {reagent.item_id}"
        if basis == "missing":
            missing.append(label)
        if basis == "assumed free":
            assumptions.append(f"{label} is not auctionable and has no opportunity cost set, so it "
                               "is counted as free.")
        lines.append(ReagentLine(
            slot=slot, item_id=reagent.item_id, name=label, quantity=reagent.quantity,
            quality_label=reagent.quality_label, auctionable=reagent.auctionable,
            savings_eligible=reagent.savings_eligible, unit_price_copper=price, price_basis=basis,
            line_cost_copper=None if price is None else price * reagent.quantity,
            on_hand=on_hand.get(reagent.item_id)))

    if output_price is None:
        missing.append(recipe.crafted_item_name or names.get(recipe.crafted_item_id)
                       or f"output item {recipe.crafted_item_id}")

    notes = [NOT_MODELLED_NOTE]
    reagent_prices_known = all(l.line_cost_copper is not None for l in lines)
    base_cost = sum(l.line_cost_copper for l in lines) if reagent_prices_known else None

    expected_savings = None
    if base_cost is not None:
        expected_savings = 0.0
        if mechanics.resourcefulness_applied:
            eligible_cost = sum(l.line_cost_copper for l in lines if l.savings_eligible)
            expected_savings = (mechanics.resourcefulness_chance
                                * mechanics.resourcefulness_saved_fraction * eligible_cost)
            if not any(l.savings_eligible for l in lines):
                notes.append("Resourcefulness was entered but no reagent in this recipe is marked "
                             "savings-eligible, so it saves nothing.")
        elif mechanics.resourcefulness_chance > 0:
            notes.append("Resourcefulness chance entered but the saved fraction is not set - not applied.")
    elif mechanics.resourcefulness_chance > 0 and not mechanics.resourcefulness_applied:
        notes.append("Resourcefulness chance entered but the saved fraction is not set - not applied.")
    if mechanics.multicraft_chance > 0 and not mechanics.multicraft_applied:
        notes.append("Multicraft chance entered but the extra-output fraction is not set - not applied.")

    units = float(recipe.output_quantity)
    if mechanics.multicraft_applied:
        units *= 1 + mechanics.multicraft_chance * mechanics.multicraft_extra_fraction

    cost = None if base_cost is None else base_cost - expected_savings
    keep = (1 - ah_cut) * sell_through
    gross = None if output_price is None else units * output_price
    net = None if gross is None else gross * keep
    profit = None if net is None or cost is None else net - cost
    breakeven = None
    if cost is not None and units * keep > 0:
        breakeven = cost / (units * keep)

    limits = [(l.on_hand // l.quantity, l.name) for l in lines if l.on_hand is not None]
    crafts_possible = limiting = None
    if limits:
        crafts_possible, limiting = min(limits, key=lambda pair: pair[0])

    return CraftResult(
        complete=not missing, missing=tuple(missing), assumptions=tuple(assumptions),
        notes=tuple(notes), lines=tuple(lines), output_units=units, output_price_copper=output_price,
        ah_cut=ah_cut, sell_through=sell_through, base_cost=base_cost,
        expected_savings=expected_savings, cost_per_craft=cost, gross_per_craft=gross,
        net_per_craft=net, profit_per_craft=profit, breakeven_output_price=breakeven,
        crafts_possible=crafts_possible, limiting_item=limiting)


# --- evaluating many recipes (used for comparison tables) ------------------------------------------

@dataclass(frozen=True)
class RecipeEvaluation:
    recipe: Recipe
    result: CraftResult
    flags: tuple
    sold_per_day: Optional[float]
    sale_rate: Optional[float]


def evaluate_recipes(recipes: list, price_of: Callable, demand_of: Callable,
                     opportunity_costs: Optional[dict] = None, on_hand: Optional[dict] = None,
                     ah_cut: float = 0.05, sell_through: float = 1.0,
                     mechanics: Optional[Mechanics] = None) -> list:
    """Evaluate each recipe with default (cheapest) reagent choices.

    ``price_of(item_id)``  -> ``pricing.PriceResult``
    ``demand_of(item_id)`` -> ``(sold_per_day, sale_rate)``
    """
    evaluations = []
    for recipe in recipes:
        item_ids = {recipe.crafted_item_id} | {r.item_id for r in recipe.reagents}
        looked_up = {i: price_of(i) for i in item_ids}
        prices = {i: (None if p.is_missing else p.copper) for i, p in looked_up.items()}
        names = {i: p.name for i, p in looked_up.items() if p.name}
        result = calculate_craft(
            recipe, prices, prices[recipe.crafted_item_id], opportunity_costs, on_hand,
            names=names, ah_cut=ah_cut, sell_through=sell_through, mechanics=mechanics)

        flags = []
        if not recipe.last_verified:
            flags.append("recipe unverified")
        if result.missing:
            flags.append("no price: " + ", ".join(result.missing))
        if result.assumptions:
            flags.append("free reagent assumed")
        used = {l.item_id for l in result.lines if l.auctionable} | {recipe.crafted_item_id}
        stale = [looked_up[i].name or str(i) for i in used
                 if not looked_up[i].is_missing and looked_up[i].is_stale and not looked_up[i].is_override]
        if stale:
            flags.append("stale prices")
        sold, rate = demand_of(recipe.crafted_item_id)
        evaluations.append(RecipeEvaluation(recipe, result, tuple(flags), sold, rate))
    return evaluations


def rank_key(evaluation: RecipeEvaluation) -> tuple:
    """Sort key: priced recipes by profit (best first), unpriced ones last."""
    profit = evaluation.result.profit_per_craft
    return (profit is None, -(profit if profit is not None else 0.0))


def days_to_sell(units: float, sold_per_day: Optional[float]) -> Optional[float]:
    """Days of TSM-reported daily sales that ``units`` represents (None if unknown)."""
    if sold_per_day is None or (isinstance(sold_per_day, float) and math.isnan(sold_per_day)) \
            or sold_per_day <= 0:
        return None
    return units / sold_per_day


# --- profession profiles -> mechanics ------------------------------------------------------------

def mechanics_from_profile(profile) -> tuple:
    """(Mechanics, notes) from a ``profiles.ProfessionProfile``.

    The chance comes from the profile's Resourcefulness % / Multicraft %; the size of each
    effect comes from the separately measured fields. An effect with either part missing
    is left off and a note says why. Nothing is assumed additive.
    """
    def pct(value):
        return None if value is None else float(value) / 100

    notes = ["Assumes the Resourcefulness % and Multicraft % you entered are proc chances as shown "
             "in the profession window."]
    rc, rs = pct(profile.resourcefulness_pct), pct(profile.resourcefulness_saved_pct)
    mc, me = pct(profile.multicraft_pct), pct(profile.multicraft_extra_pct)
    for label, chance, size in (("Resourcefulness", rc, rs), ("Multicraft", mc, me)):
        if chance is None:
            notes.append(f"{label} % is not entered in this profile - not applied.")
        elif size is None:
            notes.append(f"{label}: the profile has a chance but no measured size - not applied.")
    mechanics = Mechanics(
        resourcefulness_chance=min(rc or 0.0, 1.0), resourcefulness_saved_fraction=rs,
        multicraft_chance=min(mc or 0.0, 1.0), multicraft_extra_fraction=me)
    mechanics.validate()
    return mechanics, notes


# --- break-even material price ---------------------------------------------------------------------

def breakeven_reagent_price(result: CraftResult, item_id: int,
                            mechanics: Optional[Mechanics] = None) -> Optional[float]:
    """Highest unit price of ``item_id`` (one of the chosen reagents) at which profit is 0.

    Works even when that reagent's own price is missing, as long as the output price and
    every OTHER reagent are priced. Expected Resourcefulness savings are included when the
    reagent is savings-eligible. ``None`` when it cannot be computed or the reagent is not used.
    """
    mechanics = mechanics or Mechanics()
    target = [l for l in result.lines if l.item_id == item_id]
    if not target or result.net_per_craft is None:
        return None
    line = target[0]

    def saved_share(l: ReagentLine) -> float:
        if mechanics.resourcefulness_applied and l.savings_eligible:
            return mechanics.resourcefulness_chance * mechanics.resourcefulness_saved_fraction
        return 0.0

    others = [l for l in result.lines if l is not line]
    if any(l.line_cost_copper is None for l in others):
        return None
    other_cost = sum(l.line_cost_copper * (1 - saved_share(l)) for l in others)
    per_unit = line.quantity * (1 - saved_share(line))
    if per_unit <= 0:
        return None
    return (result.net_per_craft - other_cost) / per_unit


# --- output quality tiers ------------------------------------------------------------------------

@dataclass(frozen=True)
class TierLine:
    label: str
    item_id: int
    name: Optional[str]
    price_copper: Optional[float]
    probability: Optional[float]          # 0..1, user-entered; None = not entered
    net_per_craft: Optional[float]        # if every craft gave this tier
    profit_if_all: Optional[float]
    profitable: Optional[bool]


@dataclass(frozen=True)
class QualityAnalysis:
    lines: tuple
    breakeven_output_price: Optional[float]   # the same for every tier
    probability_total: Optional[float]
    expected_net_per_craft: Optional[float]
    expected_profit_per_craft: Optional[float]
    notes: tuple


def analyse_quality_tiers(result: CraftResult, tiers: list, prices: dict,
                          probabilities: Optional[dict] = None) -> QualityAnalysis:
    """Profit per quality tier for a craft whose reagent cost is in ``result``.

    ``tiers``         [(label, item_id, name)] - each tier is its own item
    ``prices``        item_id -> copper (None = unknown)
    ``probabilities`` label -> 0..1 chance of that tier, typed by the user (optional)

    The expected profit is only given when the odds of every tier are entered, add up
    to 100%, and every tier with a non-zero chance has a price.
    """
    probabilities = probabilities or {}
    keep = (1 - result.ah_cut) * result.sell_through
    cost = result.cost_per_craft
    lines, notes = [], []
    for label, item_id, name in tiers:
        price = prices.get(item_id)
        net = None if price is None else result.output_units * price * keep
        profit = None if net is None or cost is None else net - cost
        lines.append(TierLine(label, item_id, name, price, probabilities.get(label), net, profit,
                              None if profit is None else profit > 0))

    entered = [l.probability for l in lines if l.probability is not None]
    total = sum(entered) if entered else None
    expected_net = expected_profit = None
    if not lines:
        notes.append("No quality tiers set for this recipe.")
    elif len(entered) < len(lines):
        notes.append("Enter the chance of every tier (from your own crafting results) to get an "
                     "expected profit. This app does not know the odds.")
    elif abs(total - 1.0) > 1e-6:
        notes.append(f"Tier chances add up to {total * 100:g}%, not 100% - no expected profit shown.")
    elif any(l.price_copper is None and l.probability > 0 for l in lines):
        notes.append("A tier with a non-zero chance has no price - no expected profit shown.")
    else:
        expected_net = sum(l.net_per_craft * l.probability for l in lines if l.probability > 0)
        expected_profit = None if cost is None else expected_net - cost
    if cost is None:
        notes.append("Reagent cost is incomplete, so tier profits cannot be calculated.")
    return QualityAnalysis(tuple(lines), result.breakeven_output_price, total, expected_net,
                           expected_profit, tuple(notes))


# --- cost of items you gather yourself ----------------------------------------------------------------

SETTING_GOLD_PER_HOUR = "time_value_gold_per_hour"


def time_cost_per_unit(gold_per_hour_copper: Optional[float], units_per_hour: Optional[float]) -> Optional[float]:
    """What one self-gathered unit costs in your time: your gold per hour / units gathered per hour."""
    def missing(v) -> bool:
        return v is None or v != v                       # None or NaN (an empty database cell)
    if missing(gold_per_hour_copper) or missing(units_per_hour) or units_per_hour <= 0 or gold_per_hour_copper < 0:
        return None
    return gold_per_hour_copper / units_per_hour


def resource_costs(resources, gold_per_hour_copper: Optional[float] = None) -> tuple:
    """(opportunity cost by item, how each was set) from ``database.list_item_resources`` rows.

    A cost you typed wins; otherwise a time cost from units gathered per hour x your gold per hour.
    """
    costs, basis = {}, {}
    for r in resources.itertuples():
        item_id = int(r.item_id)
        typed = getattr(r, "opportunity_cost_copper", None)
        if typed is not None and typed == typed:
            costs[item_id], basis[item_id] = float(typed), "your cost"
            continue
        timed = time_cost_per_unit(gold_per_hour_copper, getattr(r, "gather_units_per_hour", None))
        if timed is not None:
            costs[item_id], basis[item_id] = timed, "time cost"
    return costs, basis
