"""Jewelcrafting and Alchemy tabs: the crafting calculator filtered to one profession,
with character profiles (skill, Multicraft, Resourcefulness, Ingenuity) per crafter.

Recipes come from the Housing / Crafting tab's recipe list (set the recipe's profession).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from goldmaker import pricing, profiles
import ui_crafting
from ui_common import db

NOTES = {
    "Jewelcrafting": "Gems and jewelry. Prospecting (ore -> gems) has its own tab.",
    "Alchemy": "Flasks, potions and other consumables. Expensive reagents (e.g. a rare herb) are covered by "
               "the break-even material price; silver vs gold results by the output quality tiers.",
}


def render(table: pd.DataFrame, profession: str) -> None:
    st.header(profession)
    st.caption(NOTES.get(profession, "") + " Recipes are the ones you imported whose profession contains "
               f"'{profession}'. Ingenuity and concentration are recorded but not modelled.")
    index = pricing.PriceIndex(table)
    recipe_list = ui_crafting.load_recipes()
    prefix = profession[:4].lower()
    tab_calc, tab_cmp, tab_prof = st.tabs(["Calculator", "Compare recipes", "Profiles"])
    with tab_calc:
        ui_crafting.render_calculator(index, recipe_list, prefix=f"{prefix}_calc", profession=profession)
    with tab_cmp:
        ui_crafting.render_compare(index, recipe_list, prefix=f"{prefix}_cmp", profession=profession)
    with tab_prof:
        render_profiles(profession)
    if not ui_crafting.filter_profession(recipe_list, profession):
        st.caption(f"Tip: on *Housing / Crafting -> Recipes*, set a recipe's profession to '{profession}' "
                   "to see it here.")


def render_profiles(profession: str) -> None:
    with db() as conn:
        choices = profiles.list_profile_choices(conn, profession)
        loaded = [profiles.load_profile(conn, c, p) for c, p in choices]
    if not loaded:
        st.info(f"No imported character has a profession matching '{profession}'. Import your alts on "
                "*Character Profiles*, then type in their stats on its *Profession stats* tab.")
        return
    st.dataframe(pd.DataFrame([{
        "Character": p.character_key, "Profession": p.profession,
        "Skill (exported)": p.skill_level_exported, "Crafting skill": p.crafting_skill,
        "Multicraft %": p.multicraft_pct, "Multicraft extra %": p.multicraft_extra_pct,
        "Resourcefulness %": p.resourcefulness_pct, "Resourcefulness saved %": p.resourcefulness_saved_pct,
        "Ingenuity %": p.ingenuity_pct, "Concentration (snapshot)": p.concentration_at_snapshot,
        "Knowledge points": p.knowledge_points_spent,
        "Snapshot": profiles.snapshot_age_text(p.snapshot_at),
        "Not entered": ", ".join(p.missing_stats),
    } for p in loaded]), hide_index=True)
    st.caption("Edit these on *Character Profiles -> Profession stats*. Empty means not entered (never 0%). "
               "Pick a profile in the Calculator to prefill Resourcefulness / Multicraft.")
