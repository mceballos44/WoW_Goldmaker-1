import pytest

from goldmaker import crafting
from goldmaker.crafting import Mechanics, calculate_craft
from goldmaker.currency import gold_to_copper as G
from goldmaker.pricing import PriceResult
from goldmaker.recipes import Reagent, Recipe

# Placeholder IDs only - real item IDs always come from the user's data.
GLASS, STONE, VIAL, LUMBER = 901, 902, 900, 903


def vial_recipe(**kw):
    """The user's example: 5 Gold Glass + 1 Gold Stone -> 1 Gold Sunglass Vial."""
    return Recipe(key="vial", name="Gold Sunglass Vial", crafted_item_id=VIAL,
                  reagents=(Reagent(1, GLASS, 5, "Gold Glass"), Reagent(2, STONE, 1, "Gold Stone")), **kw)


USER_PRICES = {GLASS: G(104), STONE: G(3.29)}


def test_user_example_at_user_prices():
    r = calculate_craft(vial_recipe(), USER_PRICES, G(300), ah_cut=0.05)
    assert r.complete
    assert r.cost_per_craft == pytest.approx(G(523.29))            # 5*104 + 3.29
    assert r.net_per_craft == pytest.approx(G(285))                # 300 * 0.95
    assert r.profit_per_craft == pytest.approx(G(-238.29))
    assert r.breakeven_output_price == pytest.approx(G(523.29) / 0.95)
    assert r.roi == pytest.approx(-238.29 / 523.29)


def test_profit_per_1_10_100_crafts_is_linear():
    r = calculate_craft(vial_recipe(), USER_PRICES, G(600), ah_cut=0.05)
    per = G(570) - G(523.29)
    assert r.profit_per_craft == pytest.approx(per)
    assert r.profit_for(10) == pytest.approx(per * 10)
    assert r.profit_for(100) == pytest.approx(per * 100)
    assert r.cost_for(10) == pytest.approx(G(5232.9))
    assert r.net_for(100) == pytest.approx(G(57000))


def test_sell_through_and_output_quantity():
    r = calculate_craft(vial_recipe(output_quantity=2), USER_PRICES, G(300), ah_cut=0.0, sell_through=0.5)
    assert r.output_units == 2 and r.net_per_craft == pytest.approx(G(300))     # 2 * 300 * 0.5


def test_missing_reagent_or_output_price_is_not_zero():
    r = calculate_craft(vial_recipe(), {GLASS: G(104), STONE: None}, G(300))
    assert not r.complete and r.missing == ("Gold Stone",)
    assert r.cost_per_craft is None and r.profit_per_craft is None and r.roi is None
    r2 = calculate_craft(vial_recipe(), USER_PRICES, None, names={VIAL: "Gold Sunglass Vial"})
    assert r2.missing == ("Gold Sunglass Vial",) and r2.profit_per_craft is None
    assert r2.cost_per_craft == pytest.approx(G(523.29))          # cost still known


def test_zero_price_is_real_missing_is_not():
    r = calculate_craft(vial_recipe(), {GLASS: 0, STONE: 0}, G(10), ah_cut=0.0)
    assert r.complete and r.profit_per_craft == G(10)


# --- non-auctionable reagents ------------------------------------------------------------------------

def housing_recipe():
    return Recipe(key="decor", name="Decor", crafted_item_id=VIAL,
                  reagents=(Reagent(1, GLASS, 2, "Cloth"), Reagent(2, LUMBER, 4, "Fel-Touched Lumber", auctionable=False)))


def test_non_auctionable_without_cost_is_free_but_flagged():
    r = calculate_craft(housing_recipe(), {GLASS: G(10)}, G(100), ah_cut=0.0)
    assert r.complete and r.cost_per_craft == G(20)
    assert any("counted as free" in a for a in r.assumptions)
    assert [l.price_basis for l in r.lines] == ["market", "assumed free"]


def test_non_auctionable_with_opportunity_cost():
    r = calculate_craft(housing_recipe(), {GLASS: G(10)}, G(100), opportunity_costs={LUMBER: G(5)}, ah_cut=0.0)
    assert r.cost_per_craft == G(20) + G(20) and r.assumptions == ()
    assert r.lines[1].price_basis == "opportunity cost"


def test_market_price_of_non_auctionable_item_is_ignored():
    r = calculate_craft(housing_recipe(), {GLASS: G(10), LUMBER: G(999)}, G(100), ah_cut=0.0)
    assert r.cost_per_craft == G(20)


def test_stock_limits_number_of_crafts():
    r = calculate_craft(housing_recipe(), {GLASS: G(10)}, G(100), on_hand={LUMBER: 11, GLASS: 100})
    assert r.crafts_possible == 2 and r.limiting_item == "Fel-Touched Lumber"      # 11 // 4
    assert calculate_craft(housing_recipe(), {GLASS: G(10)}, G(100), on_hand={LUMBER: 3}).crafts_possible == 0
    assert calculate_craft(housing_recipe(), {GLASS: G(10)}, G(100)).crafts_possible is None


# --- quality alternatives ------------------------------------------------------------------------------

def slot_recipe():
    return Recipe(key="q", name="Q", crafted_item_id=VIAL, reagents=(
        Reagent(1, 1, 3, "Cloth Q1", "Q1"), Reagent(1, 2, 2, "Cloth Q2", "Q2")))


def test_cheapest_alternative_chosen_unless_caller_picks():
    prices = {1: G(10), 2: G(10)}                    # Q1: 3*10=30, Q2: 2*10=20
    assert calculate_craft(slot_recipe(), prices, G(100)).lines[0].item_id == 2
    assert calculate_craft(slot_recipe(), prices, G(100), choices={1: 1}).lines[0].item_id == 1


def test_alternative_without_a_price_is_skipped_but_not_treated_as_free():
    r = calculate_craft(slot_recipe(), {1: G(10), 2: None}, G(100))
    assert r.lines[0].item_id == 1 and r.complete
    none = calculate_craft(slot_recipe(), {1: None, 2: None}, G(100))
    assert not none.complete and none.profit_per_craft is None


# --- Resourcefulness and Multicraft are separate and opt-in ---------------------------------------------

def mech_recipe():
    return Recipe(key="m", name="M", crafted_item_id=VIAL, reagents=(
        Reagent(1, 1, 10, "Eligible", savings_eligible=True),
        Reagent(2, 2, 5, "NotEligible", savings_eligible=False)))


MECH_PRICES = {1: 100, 2: 200}                      # 1000 + 1000 = 2000 copper base


def test_no_mechanics_by_default():
    r = calculate_craft(mech_recipe(), MECH_PRICES, 5000, ah_cut=0.0)
    assert r.base_cost == 2000 and r.cost_per_craft == 2000 and r.output_units == 1


def test_resourcefulness_saves_only_eligible_reagents():
    m = Mechanics(resourcefulness_chance=0.2, resourcefulness_saved_fraction=0.5)
    r = calculate_craft(mech_recipe(), MECH_PRICES, 5000, ah_cut=0.0, mechanics=m)
    assert r.expected_savings == pytest.approx(0.2 * 0.5 * 1000)       # eligible cost only
    assert r.cost_per_craft == pytest.approx(1900)


def test_multicraft_changes_output_not_cost():
    m = Mechanics(multicraft_chance=0.25, multicraft_extra_fraction=1.0)
    r = calculate_craft(mech_recipe(), MECH_PRICES, 5000, ah_cut=0.0, mechanics=m)
    assert r.output_units == pytest.approx(1.25) and r.cost_per_craft == 2000


def test_both_mechanics_apply_independently_not_additively():
    m = Mechanics(0.2, 0.5, 0.25, 1.0)
    r = calculate_craft(mech_recipe(), MECH_PRICES, 5000, ah_cut=0.0, mechanics=m)
    assert r.cost_per_craft == pytest.approx(1900) and r.output_units == pytest.approx(1.25)
    assert r.profit_per_craft == pytest.approx(5000 * 1.25 - 1900)


def test_chance_without_magnitude_is_not_applied_and_says_so():
    m = Mechanics(resourcefulness_chance=0.3, multicraft_chance=0.3)
    r = calculate_craft(mech_recipe(), MECH_PRICES, 5000, ah_cut=0.0, mechanics=m)
    assert r.cost_per_craft == 2000 and r.output_units == 1
    assert sum("not applied" in n for n in r.notes) == 2


def test_resourcefulness_with_no_eligible_reagent_saves_nothing_and_says_so():
    recipe = Recipe(key="x", name="X", crafted_item_id=VIAL, reagents=(Reagent(1, 1, 10, "A"),))
    m = Mechanics(resourcefulness_chance=0.5, resourcefulness_saved_fraction=0.5)
    r = calculate_craft(recipe, {1: 100}, 5000, mechanics=m)
    assert r.cost_per_craft == 1000 and any("savings-eligible" in n for n in r.notes)


def test_not_modelled_note_always_present():
    assert crafting.NOT_MODELLED_NOTE in calculate_craft(vial_recipe(), USER_PRICES, G(1)).notes


@pytest.mark.parametrize("kwargs", [
    {"ah_cut": 1.0}, {"ah_cut": -0.1}, {"sell_through": 1.1}, {"output_price": -1},
    {"mechanics": Mechanics(resourcefulness_chance=1.5)}, {"mechanics": Mechanics(multicraft_chance=-0.1)},
    {"mechanics": Mechanics(resourcefulness_saved_fraction=2)}, {"mechanics": Mechanics(multicraft_extra_fraction=-1)},
])
def test_invalid_parameters_rejected(kwargs):
    base = dict(recipe=vial_recipe(), prices=USER_PRICES, output_price=G(300))
    base.update(kwargs)
    with pytest.raises(ValueError):
        calculate_craft(**base)


# --- evaluating many recipes ------------------------------------------------------------------------------

def stub_price_of(table, stale=()):
    def price_of(item_id):
        copper, name = table.get(item_id, (None, None))
        return PriceResult(item_id, name, copper, "commodities" if copper is not None else "missing",
                           False, item_id in stale, None)
    return price_of


def test_evaluate_flags_and_ranking():
    verified = vial_recipe(last_verified="2026-10-13")
    unverified = Recipe(key="u", name="U", crafted_item_id=5, reagents=(Reagent(1, 6, 1, "Thing"),))
    table = {GLASS: (G(1), "Gold Glass"), STONE: (G(1), "Gold Stone"), VIAL: (G(100), "Vial"), 5: (G(50), "U item")}
    evals = crafting.evaluate_recipes([unverified, verified], stub_price_of(table, stale={VIAL}),
                                      lambda i: (30.0, 0.5) if i == VIAL else (None, None), ah_cut=0.0)
    by_key = {e.recipe.key: e for e in evals}
    assert "recipe unverified" in by_key["u"].flags and any("no price" in f for f in by_key["u"].flags)
    assert "stale prices" in by_key["vial"].flags and "recipe unverified" not in by_key["vial"].flags
    assert by_key["vial"].sold_per_day == 30.0
    ranked = sorted(evals, key=crafting.rank_key)
    assert [e.recipe.key for e in ranked] == ["vial", "u"]            # priced first, unpriced last


def test_overridden_prices_are_not_flagged_stale():
    table = {GLASS: (G(1), "G"), STONE: (G(1), "S"), VIAL: (G(100), "V")}
    def price_of(i):
        copper, name = table[i]
        return PriceResult(i, name, copper, "override", True, False, None)
    ev = crafting.evaluate_recipes([vial_recipe(last_verified="2026-10-13")], price_of, lambda i: (None, None))
    assert "stale prices" not in ev[0].flags


def test_days_to_sell():
    assert crafting.days_to_sell(100, 50) == 2.0
    for unknown in (None, 0, float("nan")):
        assert crafting.days_to_sell(100, unknown) is None
