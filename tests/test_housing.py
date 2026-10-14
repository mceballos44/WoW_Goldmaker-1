"""Housing finder selection and 'who can craft it' (fake characters, placeholder IDs)."""

import pandas as pd

from goldmaker import housing
from goldmaker.recipes import Reagent, Recipe


def recipe(key, category, profession="Tailoring", tier="Legion Tailoring", game_id=None):
    return Recipe(key=key, name=key, crafted_item_id=999500, reagents=(Reagent(1, 999501, 1),),
                  category=category, profession=profession, expansion=tier, game_recipe_id=game_id)


TIERS = pd.DataFrame([
    {"character_key": "Alpha-R", "profession": "Tailoring", "tier_name": "Legion Tailoring"},
    {"character_key": "Bravo-R", "profession": "Tailoring", "tier_name": "Legion Tailoring"},
    {"character_key": "Charlie-R", "profession": "Tailoring", "tier_name": "Test Tailoring"}])
CHAREXPORTER = pd.DataFrame([{"character_key": "Delta-R", "profession": "Khaz Algar Tailoring"}])


def test_housing_selection_and_expansion():
    rs = [recipe("rug", "Decor - Rugs"), recipe("cloak", "Cloaks"), recipe("mine", "My Housing stuff")]
    assert [r.key for r in housing.housing_recipes(rs)] == ["rug", "mine"]
    assert housing.expansion_of(rs[0]) == "Legion"
    assert housing.expansion_of(recipe("x", "Decor", tier=None)) == "unknown"


def test_crafters_ordering_known_then_tier_then_profession():
    r = recipe("rug", "Decor", game_id=9401)
    c = housing.crafters_for(r, {9401: ["Alpha-R"]}, TIERS, CHAREXPORTER)
    assert c.known_by == ("Alpha-R",) and c.has_tier == ("Bravo-R",)
    assert c.has_profession == ("Charlie-R", "Delta-R")
    assert housing.rank(c) == 0 and housing.who_text(c) == "knows it: Alpha-R"
    nobody = housing.crafters_for(recipe("x", "Decor", profession="Alchemy", tier="Legion Alchemy"),
                                  {}, TIERS, CHAREXPORTER)
    assert housing.rank(nobody) == 3 and housing.who_text(nobody) == "nobody"
    empty = housing.crafters_for(r, {}, pd.DataFrame(), pd.DataFrame())
    assert housing.rank(empty) == 3
