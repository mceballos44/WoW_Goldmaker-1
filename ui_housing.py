"""Housing finder tab: every housing recipe in one place - profit, expansion, who can craft it."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from goldmaker import crafting, database, housing, pricing
from goldmaker.currency import copper_to_gold
import ui_blizzard
import ui_crafting
from ui_common import db, default_ah_cut_pct

GOLD = st.column_config.NumberColumn(format="%.2f")


def render(table: pd.DataFrame) -> None:
    st.header("Housing finder")
    st.caption("Every housing / decor recipe you have imported, ranked against current prices, with the "
               "expansion it belongs to and which of your characters can craft it.")
    tab_hunt, tab_find = st.tabs(["Hunt", "Find & import housing recipes (Blizzard API)"])
    with tab_find:
        st.caption("Searches every expansion's skill tiers - set the expansion box to *Legion* for Legion "
                   "recipes, or clear it for all.")
        ui_blizzard.render_import(prefix="house", latest_only_default=False, tier_filter_default="Legion")
    with tab_hunt:
        render_hunt(table)


def render_hunt(table: pd.DataFrame) -> None:
    recipes = housing.housing_recipes(ui_crafting.load_recipes())
    if not recipes:
        st.info("No housing recipes yet. Use *Find & import housing recipes* (needs your Blizzard API key), or give "
                "your own recipes a category containing 'Housing' or 'Decor'.")
        return
    with db() as conn:
        holders = database.known_recipe_holders(conn)
        tiers = database.list_profession_tiers(conn)
        professions = database.list_character_professions(conn)
    if tiers.empty:
        st.caption("Tip: *Character Profiles -> Battle.net* adds which recipes each character knows.")

    c1, c2, c3, c4 = st.columns(4)
    expansions = sorted({housing.expansion_of(r) for r in recipes})
    pick_exp = c1.multiselect("Expansion", expansions, key="house_exp")
    pick_prof = c2.multiselect("Profession", sorted({r.profession for r in recipes if r.profession}), key="house_prof")
    who = c3.selectbox("Crafter", ["Anyone", "One of my characters knows it", "I have the profession"],
                       key="house_who")
    only_priced = c4.checkbox("Only fully priced", value=True, key="house_priced")
    p1, p2 = st.columns(2)
    ah_cut = p1.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key="house_ahcut")
    sell = p2.number_input("Expected sell-through (%)", 0.0, 100.0, value=100.0, step=5.0, key="house_sell")

    chosen = [r for r in recipes if (not pick_exp or housing.expansion_of(r) in pick_exp)
              and (not pick_prof or r.profession in pick_prof)]
    index = pricing.PriceIndex(table)
    opportunity, on_hand = ui_crafting.load_resources()
    evaluations = crafting.evaluate_recipes(chosen, index.get, index.demand, opportunity, on_hand,
                                            ah_cut=ah_cut / 100, sell_through=sell / 100)
    rows = []
    for e in evaluations:
        c = housing.crafters_for(e.recipe, holders, tiers, professions)
        if who.startswith("One") and not c.known_by:
            continue
        if who.startswith("I have") and housing.rank(c) == 3:
            continue
        res = e.result
        rows.append({
            "Recipe": e.recipe.name, "Expansion": housing.expansion_of(e.recipe), "Profession": e.recipe.profession,
            "Category": e.recipe.category, "Profit / craft (g)": copper_to_gold(res.profit_per_craft),
            "ROI %": None if res.roi is None else res.roi * 100,
            "Cost / craft (g)": copper_to_gold(res.cost_per_craft),
            "Sell price (g)": copper_to_gold(res.output_price_copper), "Sold / day (TSM)": e.sold_per_day,
            "Who can craft": housing.who_text(c), "_rank": housing.rank(c), "Flags": "; ".join(e.flags)})
    frame = pd.DataFrame(rows)
    if frame.empty:
        st.info("No housing recipe matches these filters.")
        return
    if only_priced:
        frame = frame[frame["Profit / craft (g)"].notna()]
    frame = frame.sort_values(["Profit / craft (g)"], ascending=False, na_position="last")
    st.caption(f"{len(frame)} of {len(recipes)} housing recipes shown. Recipes from the Blizzard API are unverified "
               "until you check them in game; profits are theoretical at listed prices.")
    st.dataframe(frame.drop(columns=["_rank"]), hide_index=True, column_config={
        "Profit / craft (g)": GOLD, "Cost / craft (g)": GOLD, "Sell price (g)": GOLD,
        "ROI %": st.column_config.NumberColumn(format="%.1f"),
        "Sold / day (TSM)": st.column_config.NumberColumn(format="%.1f")})
    top = frame[frame["Profit / craft (g)"].notna()].head(15)
    if not top.empty:
        st.markdown("**Top housing recipes by profit per craft (g)**")
        st.bar_chart(pd.DataFrame({"Profit / craft (g)": top["Profit / craft (g)"].values},
                                  index=[f"{n} ({x})" for n, x in zip(top["Recipe"], top["Expansion"])]))
    st.caption("Open a recipe in *Housing / Crafting -> Calculator* for the reagent breakdown, stock limits and "
               "break-even material prices.")
