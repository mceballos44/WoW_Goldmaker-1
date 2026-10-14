"""Housing finder: which housing / decor recipes you have, what they earn, and who can craft them.

A recipe counts as housing when its category contains one of the housing words (Blizzard's category
names, or a category you typed such as "Housing"). "Who can craft it" comes from Blizzard's Profile API
(recipes a character knows, profession tiers it has) and from CharExporter's profession names.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .blizzard_api import HOUSING_KEYWORDS, is_housing


def housing_recipes(recipes: list, keywords=HOUSING_KEYWORDS) -> list:
    return [r for r in recipes if is_housing(r.category, keywords)]


def expansion_of(recipe) -> str:
    """'Legion' from a skill tier like 'Legion Tailoring'; the tier itself if it does not end in the profession."""
    tier = (recipe.expansion or "").strip()
    profession = (recipe.profession or "").strip()
    if profession and tier.lower().endswith(profession.lower()):
        tier = tier[: -len(profession)].strip()
    return tier or "unknown"


@dataclass(frozen=True)
class Crafters:
    known_by: tuple            # characters that know the recipe (Blizzard)
    has_tier: tuple            # characters with that expansion's profession tier (Blizzard), recipe not known
    has_profession: tuple      # characters with the profession at all (either source)


def crafters_for(recipe, known_holders: dict, tiers: pd.DataFrame, professions: pd.DataFrame) -> Crafters:
    known = tuple(known_holders.get(recipe.game_recipe_id, ())) if recipe.game_recipe_id else ()
    tier_chars, prof_chars = set(), set()
    name = (recipe.profession or "").lower()
    if not tiers.empty:
        if recipe.expansion:
            tier_chars = set(tiers.loc[tiers["tier_name"].fillna("").str.lower() == recipe.expansion.lower(),
                                       "character_key"])
        if name:
            prof_chars |= set(tiers.loc[tiers["profession"].fillna("").str.lower().str.contains(name, regex=False),
                                        "character_key"])
    if name and not professions.empty:
        prof_chars |= set(professions.loc[professions["profession"].fillna("").str.lower()
                                          .str.contains(name, regex=False), "character_key"])
    return Crafters(known, tuple(sorted(tier_chars - set(known))),
                    tuple(sorted(prof_chars - set(known) - tier_chars)))


def who_text(c: Crafters) -> str:
    if c.known_by:
        return "knows it: " + ", ".join(c.known_by)
    if c.has_tier:
        return "has the tier (recipe not known): " + ", ".join(c.has_tier)
    if c.has_profession:
        return "has the profession: " + ", ".join(c.has_profession)
    return "nobody"


def rank(c: Crafters) -> int:
    """0 = someone knows it, 1 = someone has the tier, 2 = someone has the profession, 3 = nobody."""
    return 0 if c.known_by else 1 if c.has_tier else 2 if c.has_profession else 3
