"""Housing / Crafting tab: compare recipes, calculate one in detail, manage recipes and stock.

No recipe data is built in. Recipes come from files you import or from the editor, and
each one records its source, patch and last-verified date so stale data stays visible.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

from goldmaker import config, crafting, database, pricing, profiles, recipes
from goldmaker.crafting import Mechanics
from goldmaker.currency import copper_to_gold, format_gold, gold_to_copper
from ui_common import db, default_ah_cut_pct, item_picker

GOLD = st.column_config.NumberColumn(format="%.2f")
CHEAPEST = "Cheapest priced"
NEW_RECIPE = "+ New recipe"
EXAMPLE_KEY = "user-example-gold-sunglass-vial"
NO_PROFILE = "No profile (enter values by hand)"


def render(table: pd.DataFrame) -> None:
    st.header("Housing / Crafting")
    st.caption("Compare what crafting is worth against current prices. Recipes are yours: import them "
               "or type them in - nothing about reagents or quantities is built into this app.")
    index = pricing.PriceIndex(table)
    recipe_list = load_recipes()
    tab_compare, tab_calc, tab_recipes, tab_stock = st.tabs(
        ["Compare recipes", "Calculator", "Recipes", "Stock & costs"])
    with tab_compare:
        render_compare(index, recipe_list)
    with tab_calc:
        render_calculator(index, recipe_list)
    with tab_recipes:
        render_recipes(table, index, recipe_list)
    with tab_stock:
        render_stock(table)


# --- data helpers -----------------------------------------------------------------------------------

def load_recipes() -> list:
    with db() as conn:
        return [recipes.Recipe.from_row(row) for row in database.list_recipe_rows(conn)]


def load_resources() -> tuple:
    """(opportunity cost by item, units on hand by item) in copper / units."""
    with db() as conn:
        df = database.list_item_resources(conn)
    opportunity = {int(r.item_id): int(r.opportunity_cost_copper)
                   for r in df.itertuples() if pd.notna(r.opportunity_cost_copper)}
    on_hand = {int(r.item_id): int(r.on_hand) for r in df.itertuples() if pd.notna(r.on_hand)}
    return opportunity, on_hand


def filter_profession(recipe_list: list, profession: Optional[str]) -> list:
    """Recipes whose profession contains ``profession`` (case-insensitive); all when None."""
    if not profession:
        return recipe_list
    wanted = profession.lower()
    return [r for r in recipe_list if r.profession and wanted in r.profession.lower()]


def profile_picker(prefix: str, profession: Optional[str] = None):
    """Optional character profile whose stats prefill Resourcefulness / Multicraft. Returns a profile or None."""
    with db() as conn:
        choices = profiles.list_profile_choices(conn, profession)
    if not choices:
        st.caption("No imported character profile" + (f" with {profession}" if profession else "")
                   + " - import one on *Character Profiles* to prefill your stats.")
        return None
    labels = [NO_PROFILE] + [f"{c} - {p}" for c, p in choices]
    pick = st.selectbox("Character profile", range(len(labels)), format_func=lambda i: labels[i],
                        key=f"{prefix}_profile",
                        help="Prefills Resourcefulness / Multicraft from the stats saved on Character Profiles.")
    if pick == 0:
        return None
    with db() as conn:
        profile = profiles.load_profile(conn, *choices[pick - 1])
    for warning in profiles.profile_warnings(profile):
        st.caption(f"⚠️ {warning}")
    return profile


def mechanics_inputs(prefix: str, profile=None) -> Mechanics:
    """Optional Resourcefulness / Multicraft inputs. Nothing is applied unless both fields of a pair are filled.

    With a profile, the fields start from its saved values (chance and measured size).
    """
    tag = profile.label if profile is not None else "manual"
    pre = {}
    if profile is not None:
        pre = {"rc": profile.resourcefulness_pct, "rs": profile.resourcefulness_saved_pct,
               "mc": profile.multicraft_pct, "me": profile.multicraft_extra_pct}
    title = "Resourcefulness / Multicraft (optional - off unless both fields of a pair are filled)"
    with st.expander(title + (f" - from {profile.label}" if profile is not None else ""),
                     expanded=profile is not None):
        st.caption("This app does not know the real numbers. Enter values you have measured. "
                   "Resourcefulness only saves reagents marked 'savings eligible' in the recipe; "
                   "Multicraft only adds output. The two effects are applied separately, never added together. "
                   "Ingenuity and concentration are not modelled.")
        c1, c2, c3, c4 = st.columns(4)
        rc = c1.number_input("Resourcefulness chance (%)", 0.0, 100.0, value=pre.get("rc"), step=1.0,
                             placeholder="off", key=f"{prefix}_rc_{tag}")
        rs = c2.number_input("Share of eligible reagents saved (%)", 0.0, 100.0, value=pre.get("rs"), step=1.0,
                             placeholder="off", key=f"{prefix}_rs_{tag}")
        mc = c3.number_input("Multicraft chance (%)", 0.0, 100.0, value=pre.get("mc"), step=1.0,
                             placeholder="off", key=f"{prefix}_mc_{tag}")
        me = c4.number_input("Extra output on proc (% of base)", 0.0, value=pre.get("me"), step=5.0,
                             placeholder="off", key=f"{prefix}_me_{tag}")
        if profile is not None:
            _, notes = crafting.mechanics_from_profile(profile)
            for note in notes:
                st.caption(note)
    return Mechanics(
        resourcefulness_chance=(rc or 0.0) / 100,
        resourcefulness_saved_fraction=None if rs is None else rs / 100,
        multicraft_chance=(mc or 0.0) / 100,
        multicraft_extra_fraction=None if me is None else me / 100)


# --- Compare ----------------------------------------------------------------------------------------

def render_compare(index: pricing.PriceIndex, recipe_list: list, prefix: str = "cmp",
                   profession: Optional[str] = None) -> None:
    recipe_list = filter_profession(recipe_list, profession)
    if not recipe_list:
        st.info("No recipes yet. Add or import some on the *Recipes* tab.")
        return
    opportunity, on_hand = load_resources()

    def options(attr: str) -> list:
        return sorted({getattr(r, attr) for r in recipe_list if getattr(r, attr)})

    f1, f2, f3, f4 = st.columns(4)
    sel_prof = f1.multiselect("Profession", options("profession"), key=f"{prefix}_prof")
    sel_exp = f2.multiselect("Expansion", options("expansion"), key=f"{prefix}_exp")
    sel_patch = f3.multiselect("Patch", options("game_patch"), key=f"{prefix}_patch")
    sel_cat = f4.multiselect("Category", options("category"), key=f"{prefix}_cat")

    p1, p2, p3, p4 = st.columns(4)
    ah_cut = p1.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key=f"{prefix}_ahcut")
    sell_through = p2.number_input("Expected sell-through (%)", 0.0, 100.0, value=100.0, step=5.0,
                                   key=f"{prefix}_sell")
    sort_by = p3.selectbox("Rank by", ["Profit / craft (g)", "ROI %", "Profit at stock cap (g)",
                                       "Cost / craft (g)"], key=f"{prefix}_sort")
    only_priced = p4.checkbox("Only fully priced", value=True, key=f"{prefix}_priced")
    profile = profile_picker(prefix, profession)
    mechanics = mechanics_inputs(prefix, profile)

    chosen = [r for r in recipe_list
              if (not sel_prof or r.profession in sel_prof) and (not sel_exp or r.expansion in sel_exp)
              and (not sel_patch or r.game_patch in sel_patch) and (not sel_cat or r.category in sel_cat)]
    try:
        evaluations = crafting.evaluate_recipes(
            chosen, index.get, index.demand, opportunity, on_hand,
            ah_cut=ah_cut / 100, sell_through=sell_through / 100, mechanics=mechanics)
    except ValueError as exc:
        st.error(str(exc))
        return

    rows = []
    for e in evaluations:
        res = e.result
        cap = (None if res.crafts_possible is None or res.profit_per_craft is None
               else res.profit_per_craft * res.crafts_possible)
        rows.append({
            "Recipe": e.recipe.name, "Profession": e.recipe.profession, "Expansion": e.recipe.expansion,
            "Patch": e.recipe.game_patch, "Category": e.recipe.category,
            "Cost / craft (g)": copper_to_gold(res.cost_per_craft),
            "Output price (g)": copper_to_gold(res.output_price_copper),
            "Profit / craft (g)": copper_to_gold(res.profit_per_craft),
            "ROI %": None if res.roi is None else res.roi * 100,
            "Break-even price (g)": copper_to_gold(res.breakeven_output_price),
            "Sold / day (TSM)": e.sold_per_day,
            "Crafts possible (stock)": res.crafts_possible,
            "Profit at stock cap (g)": copper_to_gold(cap),
            "Recipe verified": e.recipe.last_verified or "never",
            "Flags": "; ".join(e.flags), "Key": e.recipe.key,
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        st.info("No recipes match these filters.")
        return
    if only_priced:
        frame = frame[frame["Profit / craft (g)"].notna()]
    frame = frame.sort_values(sort_by, ascending=False, na_position="last")

    flagged = lambda text: int(frame["Flags"].str.contains(text, regex=False).sum())
    st.caption(f"{len(frame)} of {len(chosen)} recipes shown. "
               f"Unverified: {flagged('recipe unverified')} - stale prices: {flagged('stale prices')} - "
               f"free reagent assumed: {flagged('free reagent assumed')} - missing price: {flagged('no price')}.")
    st.dataframe(frame.drop(columns=["Key"]), hide_index=True, column_config={
        "Cost / craft (g)": GOLD, "Output price (g)": GOLD, "Profit / craft (g)": GOLD,
        "ROI %": st.column_config.NumberColumn(format="%.1f"), "Break-even price (g)": GOLD,
        "Sold / day (TSM)": st.column_config.NumberColumn(format="%.1f"),
        "Crafts possible (stock)": st.column_config.NumberColumn(format="%d"),
        "Profit at stock cap (g)": GOLD})

    chart = frame[frame["Profit / craft (g)"].notna()].head(15)
    if not chart.empty:
        labels = [name if (frame["Recipe"] == name).sum() == 1 else f"{name} ({key})"
                  for name, key in zip(chart["Recipe"], chart["Key"])]
        st.markdown("**Profit per craft - top recipes (g)**")
        st.bar_chart(pd.DataFrame({"Profit per craft (g)": chart["Profit / craft (g)"].values}, index=labels))

    st.warning("These are theoretical margins from today's listed prices, not demonstrated sales. "
               "Profit assumes every unit sells at that price; the TSM 'sold / day' column is only an "
               "indicator of demand and may be blank for some datasets.")


# --- Calculator -------------------------------------------------------------------------------------

def render_calculator(index: pricing.PriceIndex, recipe_list: list, prefix: str = "calc",
                      profession: Optional[str] = None) -> None:
    recipe_list = filter_profession(recipe_list, profession)
    if not recipe_list:
        st.info("No recipes yet. Add or import some on the *Recipes* tab.")
        return
    opportunity, on_hand = load_resources()
    labels = {f"{r.name} - {r.profession or 'no profession'} ({r.key})": r for r in recipe_list}
    recipe = labels[st.selectbox("Recipe", list(labels), key=f"{prefix}_recipe")]

    for warning in recipes.recipe_warnings(recipe):
        st.caption(f"⚠️ {warning}")
    profile = profile_picker(prefix, profession)
    if profile is not None and recipe.required_skill is not None:
        if profile.crafting_skill is None:
            st.caption(f"Recipe needs skill {recipe.required_skill}; this profile has no crafting skill entered.")
        else:
            st.caption(f"Recipe skill {recipe.required_skill} vs your crafting skill {profile.crafting_skill:g}. "
                       "Skill vs recipe difficulty decides output quality in game; that is not modelled - use "
                       "the quality-tier section with odds from your own crafts.")
    with st.expander("Recipe details"):
        st.write({"Source": recipe.source or "-", "Patch": recipe.game_patch or "-",
                  "Last verified": recipe.last_verified or "never", "Expansion": recipe.expansion or "-",
                  "Required skill": recipe.required_skill, "Game recipe ID": recipe.game_recipe_id,
                  "Notes": recipe.notes or "-"})

    choices: dict = {}
    for slot, options in recipe.slots().items():
        if len(options) > 1:
            names = {f"{o.item_name or o.item_id} ({o.quality_label or 'no label'}) x{o.quantity}": o.item_id
                     for o in options}
            pick = st.radio(f"Reagent slot {slot} has alternatives", [CHEAPEST] + list(names),
                            horizontal=True, key=f"{prefix}_slot_{recipe.key}_{slot}")
            if pick != CHEAPEST:
                choices[slot] = names[pick]

    c1, c2, c3 = st.columns(3)
    crafts = int(c1.number_input("Number of crafts", min_value=1, value=10, step=1, key=f"{prefix}_n_{recipe.key}"))
    ah_cut = c2.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key=f"{prefix}_ahcut")
    sell_through = c3.number_input("Expected sell-through (%)", 0.0, 100.0, value=100.0, step=5.0,
                                   key=f"{prefix}_sell")

    output = index.get(recipe.crafted_item_id)
    output_name = recipe.crafted_item_name or output.name or f"item {recipe.crafted_item_id}"
    default_output_g = None if output.is_missing else copper_to_gold(output.copper)
    output_g = st.number_input(f"{output_name}: price per unit (g)", min_value=0.0, value=default_output_g,
                               step=0.01, format="%.4f", placeholder="no price found - enter one",
                               key=f"{prefix}_out_{recipe.key}",
                               help="Prefilled from your price data. Editing changes this calculation only.")
    mechanics = mechanics_inputs(prefix, profile)

    rows, defaults = [], {}
    for r in recipe.reagents:
        res = index.get(r.item_id)
        name = r.item_name or res.name or f"item {r.item_id}"
        if r.auctionable:
            default = None if res.is_missing else copper_to_gold(res.copper)
            source = res.source + (" (stale?)" if res.is_stale and not res.is_override and not res.is_missing else "")
        else:
            cost = opportunity.get(r.item_id)
            default = None if cost is None else copper_to_gold(cost)
            source = "opportunity cost" if cost is not None else "none set (counts as free)"
        defaults[r.item_id] = (res, default)
        rows.append({"Slot": r.slot, "Item ID": r.item_id,
                     "Item": name + (f" [{r.quality_label}]" if r.quality_label else ""),
                     "Qty / craft": r.quantity, "Auctionable": r.auctionable,
                     "Price or opportunity cost (g)": default, "Source": source,
                     "In stock": on_hand.get(r.item_id)})
    st.markdown("**Reagents** - edit a price to change this calculation only. For non-auctionable reagents the "
                "value is an opportunity cost (leave empty to count it as free).")
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, key=f"{prefix}_editor_{recipe.key}",
        disabled=["Slot", "Item ID", "Item", "Qty / craft", "Auctionable", "Source", "In stock"],
        column_config={"Item ID": st.column_config.NumberColumn(format="%d"),
                       "Price or opportunity cost (g)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                       "In stock": st.column_config.NumberColumn(format="%d")})

    prices, opps = {}, dict(opportunity)
    for _, row in edited.iterrows():
        value = row["Price or opportunity cost (g)"]
        copper = gold_to_copper(value) if pd.notna(value) else None
        if row["Auctionable"]:
            prices[int(row["Item ID"])] = copper
        elif copper is None:
            opps.pop(int(row["Item ID"]), None)
        else:
            opps[int(row["Item ID"])] = copper
    names = {int(r["Item ID"]): r["Item"] for _, r in edited.iterrows()}
    output_copper = gold_to_copper(output_g) if output_g is not None else None

    try:
        result = crafting.calculate_craft(
            recipe, prices, output_copper, opportunity_costs=opps, on_hand=on_hand, choices=choices,
            names=names, ah_cut=ah_cut / 100, sell_through=sell_through / 100, mechanics=mechanics)
    except ValueError as exc:
        st.error(str(exc))
        return
    _render_craft_result(result, recipe, crafts, output_name, defaults, prices, output, index,
                         sell_through / 100)
    render_breakeven_materials(result, mechanics, prefix, recipe)
    render_quality_tiers(result, recipe, index, prefix)


def _render_craft_result(result, recipe, crafts, output_name, defaults, prices, output, index,
                         sell_through) -> None:
    st.divider()
    if not result.complete:
        st.warning("Cannot finish the calculation: no price for **" + ", ".join(result.missing)
                   + "**. Enter it above. Missing prices are never counted as 0.")
    for assumption in result.assumptions:
        st.warning(assumption)

    stale = []
    for line in result.lines:
        res, default_g = defaults[line.item_id]
        if (line.auctionable and not res.is_missing and res.is_stale and not res.is_override
                and prices.get(line.item_id) == gold_to_copper(default_g)):
            stale.append(line.name)
    if not output.is_missing and output.is_stale and not output.is_override:
        stale.append(output_name)
    if stale:
        st.warning("These prices come from TSM data older than the warning threshold (or with no timestamp): "
                   + ", ".join(stale) + ".")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Profit per craft", format_gold(result.profit_per_craft))
    m2.metric("ROI", f"{result.roi * 100:,.1f}%" if result.roi is not None else "n/a")
    m3.metric("Cost per craft", format_gold(result.cost_per_craft))
    m4.metric("Break-even output price", format_gold(result.breakeven_output_price))

    st.markdown("**Profit at different scales** (linear - assumes prices do not move as you sell)")
    scale = pd.DataFrame([{
        "Crafts": n, "Cost (g)": copper_to_gold(result.cost_for(n)),
        "Net revenue (g)": copper_to_gold(result.net_for(n)),
        "Profit (g)": copper_to_gold(result.profit_for(n)),
    } for n in dict.fromkeys((1, 10, 100, crafts))])
    st.dataframe(scale, hide_index=True, column_config={
        "Crafts": st.column_config.NumberColumn(format="%d"),
        "Cost (g)": GOLD, "Net revenue (g)": GOLD, "Profit (g)": GOLD})

    st.markdown("**Reagent breakdown (per craft)**")
    st.dataframe(pd.DataFrame([{
        "Item": l.name, "Qty": l.quantity, "Price basis": l.price_basis,
        "Unit (g)": copper_to_gold(l.unit_price_copper), "Line cost (g)": copper_to_gold(l.line_cost_copper),
        "Savings eligible": l.savings_eligible, "In stock": l.on_hand} for l in result.lines]),
        hide_index=True, column_config={"Unit (g)": GOLD, "Line cost (g)": GOLD})
    st.caption(f"Expected output per craft: {result.output_units:g} x {output_name}"
               + (f" - expected reagent savings {format_gold(result.expected_savings)}"
                  if result.expected_savings else ""))

    if result.crafts_possible is not None:
        capped = min(crafts, result.crafts_possible)
        st.info(f"Stock limit: you can make **{result.crafts_possible}** craft(s) from what is on hand "
                f"(limited by {result.limiting_item}). Profit for {capped} craft(s): "
                f"**{format_gold(result.profit_for(capped))}**. Stock limits only cover items you have recorded.")

    render_demand(index, recipe, result, crafts, sell_through)
    for note in result.notes:
        st.caption(note)


def render_demand(index: pricing.PriceIndex, recipe: recipes.Recipe, result, crafts: int,
                  sell_through: float) -> None:
    sold, rate = index.demand(recipe.crafted_item_id)
    units = result.output_units * crafts * sell_through
    days = crafting.days_to_sell(units, sold)
    st.markdown("**Demand indicator**")
    st.write({"Units to sell": round(units, 1), "TSM sold / day": sold, "TSM sale rate": rate,
              "Days of daily volume": None if days is None else round(days, 2)})
    if days is not None and days > 1:
        st.warning(f"That is {days:.1f} days of TSM-reported daily sales. Profit assumes it all sells at "
                   "today's price (one day is a rule of thumb, not a limit).")


def render_breakeven_materials(result, mechanics: Mechanics, prefix: str, recipe: recipes.Recipe) -> None:
    """Highest price per reagent at which the craft still breaks even (e.g. an expensive herb)."""
    rows = []
    for line in result.lines:
        limit = crafting.breakeven_reagent_price(result, line.item_id, mechanics)
        rows.append({"Reagent": line.name, "Qty / craft": line.quantity,
                     "Current price (g)": copper_to_gold(line.unit_price_copper),
                     "Break-even price (g)": copper_to_gold(limit),
                     "Headroom (g)": (None if limit is None or line.unit_price_copper is None
                                      else copper_to_gold(limit - line.unit_price_copper))})
    st.markdown("**Break-even material prices** - the most you can pay for one reagent (others at the "
                "prices above) before the craft stops breaking even")
    st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
        "Current price (g)": GOLD, "Break-even price (g)": GOLD, "Headroom (g)": GOLD})
    st.caption("Needs the output price and every other reagent priced. Includes expected Resourcefulness "
               "savings for savings-eligible reagents when that effect is switched on.")


def render_quality_tiers(result, recipe: recipes.Recipe, index: pricing.PriceIndex, prefix: str) -> None:
    """Output quality tiers (each its own item ID): profit per tier, expected profit from the user's odds."""
    with db() as conn:
        tiers = database.list_output_tiers(conn, recipe.key)
    with st.expander(f"Output quality tiers ({len(tiers)} set)" if tiers else
                     "Output quality tiers (e.g. silver / gold quality) - not set for this recipe",
                     expanded=bool(tiers)):
        st.caption("Each quality of the crafted item is a separate item with its own item ID and price. "
                   "The app does not know your odds of each quality - enter them from your own crafts.")
        edit = pd.DataFrame([{"Tier": t["tier_label"], "Item ID": t["item_id"], "Name": t["item_name"]}
                             for t in tiers] or [{"Tier": "", "Item ID": None, "Name": ""}])
        edited = st.data_editor(edit, num_rows="dynamic", hide_index=True, key=f"{prefix}_tiers_{recipe.key}",
                                column_config={"Item ID": st.column_config.NumberColumn(min_value=1, step=1,
                                                                                       format="%d")})
        if st.button("Save quality tiers", key=f"{prefix}_tiers_save_{recipe.key}"):
            rows = [{"tier_label": str(r["Tier"]).strip(), "item_id": int(r["Item ID"]),
                     "item_name": (str(r["Name"]).strip() or None) if pd.notna(r["Name"]) else None}
                    for _, r in edited.iterrows()
                    if pd.notna(r["Item ID"]) and str(r["Tier"] or "").strip()]
            try:
                with db() as conn:
                    database.save_output_tiers(conn, recipe.key, rows)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.toast("Quality tiers saved")
                st.rerun()
        if not tiers:
            return

        prices, odds, cols = {}, {}, st.columns(min(len(tiers), 4))
        for n, t in enumerate(tiers):
            res = index.get(t["item_id"])
            col = cols[n % len(cols)]
            name = t["item_name"] or res.name or f"item {t['item_id']}"
            price_g = col.number_input(f"{t['tier_label']}: {name} price (g)", min_value=0.0,
                                       value=None if res.is_missing else copper_to_gold(res.copper), step=0.01,
                                       format="%.4f", placeholder="no price", key=f"{prefix}_tp_{recipe.key}_{n}")
            chance = col.number_input(f"{t['tier_label']}: chance (%)", 0.0, 100.0, value=None, step=1.0,
                                      placeholder="your odds", key=f"{prefix}_tc_{recipe.key}_{n}")
            prices[t["item_id"]] = gold_to_copper(price_g) if price_g is not None else None
            if chance is not None:
                odds[t["tier_label"]] = chance / 100
        analysis = crafting.analyse_quality_tiers(
            result, [(t["tier_label"], t["item_id"], t["item_name"]) for t in tiers], prices, odds)
        st.dataframe(pd.DataFrame([{
            "Tier": l.label, "Price (g)": copper_to_gold(l.price_copper),
            "Net per craft if all this tier (g)": copper_to_gold(l.net_per_craft),
            "Profit per craft if all this tier (g)": copper_to_gold(l.profit_if_all),
            "Profitable?": {True: "yes", False: "no", None: "unknown"}[l.profitable],
            "Your chance %": None if l.probability is None else l.probability * 100,
        } for l in analysis.lines]), hide_index=True, column_config={
            "Price (g)": GOLD, "Net per craft if all this tier (g)": GOLD,
            "Profit per craft if all this tier (g)": GOLD,
            "Your chance %": st.column_config.NumberColumn(format="%.1f")})
        q1, q2 = st.columns(2)
        q1.metric("Break-even price for any tier", format_gold(analysis.breakeven_output_price))
        q2.metric("Expected profit per craft (your odds)", format_gold(analysis.expected_profit_per_craft))
        for note in analysis.notes:
            st.caption(note)


# --- Recipes ------------------------------------------------------------------------------------------

REAGENT_COLUMNS = ["Slot", "Item ID", "Name", "Quantity", "Quality", "Auctionable", "Savings eligible"]
SAMPLE_CSV = config.PROJECT_ROOT / "samples" / "recipes_EXAMPLE.csv"


def render_recipes(table: pd.DataFrame, index: pricing.PriceIndex, recipe_list: list) -> None:
    st.subheader("Your recipes")
    if recipe_list:
        st.dataframe(pd.DataFrame([{
            "Key": r.key, "Name": r.name, "Profession": r.profession, "Expansion": r.expansion,
            "Patch": r.game_patch, "Category": r.category, "Last verified": r.last_verified or "never",
            "Reagent lines": len(r.reagents), "Source": r.source} for r in recipe_list]), hide_index=True)
    else:
        st.info("No recipes yet. Import a file, load the example, or add one with the editor below.")
    _quickstart(index, recipe_list)
    _import_export(index, recipe_list)
    st.divider()
    _editor(table, index, recipe_list)


def _quickstart(index: pricing.PriceIndex, recipe_list: list) -> None:
    with st.expander("Load my Gold Sunglass Vial example (5 Gold Glass + 1 Gold Stone)"):
        wanted = {"vial": "Gold Sunglass Vial", "glass": "Gold Glass", "stone": "Gold Stone"}
        chosen, ready = {}, True
        for key, name in wanted.items():
            found = index.lookup_name(name)
            if len(found) == 1:
                chosen[key] = found[0]
                st.caption(f"{name}: item {found[0][0]}")
            elif found:
                labels = {f"{n} ({i})": (i, n) for i, n in found}
                chosen[key] = labels[st.selectbox(f"Several items are called '{name}' - which one?",
                                                  list(labels), key=f"ex_{key}")]
            else:
                ready = False
                st.warning(f"'{name}' was not found in the cached price data. Refresh prices, or add the "
                           "recipe by hand below using item IDs.")
        if not ready:
            return
        if EXAMPLE_KEY in {r.key for r in recipe_list}:
            st.success("The example recipe is already saved.")
        elif st.button("Save example recipe", key="ex_save"):
            recipe = recipes.Recipe(
                key=EXAMPLE_KEY, name="Gold Sunglass Vial", crafted_item_id=chosen["vial"][0],
                crafted_item_name=chosen["vial"][1], profession="Jewelcrafting",
                reagents=(recipes.Reagent(1, chosen["glass"][0], 5, chosen["glass"][1]),
                          recipes.Reagent(2, chosen["stone"][0], 1, chosen["stone"][1])),
                source="User-supplied example; quantities not verified against the game.",
                notes="User's example prices: Gold Glass 104g, Gold Stone 3.29g, Gold Sunglass Vial 300g.")
            with db() as conn:
                database.save_recipe(conn, recipe)
            st.toast("Example recipe saved (marked unverified)")
            st.rerun()


def _import_export(index: pricing.PriceIndex, recipe_list: list) -> None:
    with st.expander("Import recipes (CSV or JSON)"):
        st.markdown(
            "One row per reagent (CSV) or a `reagents` list per recipe (JSON). Give item **IDs**, or names that "
            "match exactly one item in your price data. Reagents that share a `reagent_slot` are alternatives "
            "(for example quality tiers). Set `auctionable` to false for items that are not on the AH, and "
            "`savings_eligible` only where you know a reagent can be saved. Fill in `source`, `game_patch` "
            "and `last_verified` so the data stays traceable. Importing a recipe with an existing key replaces it.")
        template = SAMPLE_CSV.read_text(encoding="utf-8") if SAMPLE_CSV.exists() else recipes.csv_template()
        c1, c2 = st.columns(2)
        c1.download_button("Download example file (fake data)", template, "recipes_EXAMPLE.csv", "text/csv",
                           key="rc_dl_example")
        c2.download_button("Download empty template", recipes.csv_template(), "recipes_template.csv", "text/csv",
                           key="rc_dl_template")
        upload = st.file_uploader("Recipe file", type=["csv", "json", "txt"], key="rc_upload")
        pasted = st.text_area("...or paste CSV / JSON here", height=120, key="rc_paste")
        text = upload.getvalue().decode("utf-8-sig", errors="replace") if upload is not None else pasted
        if text.strip():
            result = recipes.parse_recipes(text, lookup=index.lookup_name)
            existing = {r.key for r in recipe_list}
            if result.errors:
                st.error(f"{len(result.errors)} problem(s) - recipes with problems are skipped:")
                st.code("\n".join(result.errors[:40]), language=None)
            if result.recipes:
                st.dataframe(pd.DataFrame([{
                    "Key": r.key, "Name": r.name, "Reagent lines": len(r.reagents),
                    "Action": "replaces existing" if r.key in existing else "new"} for r in result.recipes]),
                    hide_index=True)
                if result.warnings:
                    with st.expander(f"{len(result.warnings)} data-quality warning(s)"):
                        st.code("\n".join(result.warnings[:60]), language=None)
                if st.button(f"Import {len(result.recipes)} recipe(s)", type="primary", key="rc_do_import"):
                    with db() as conn:
                        for recipe in result.recipes:
                            database.save_recipe(conn, recipe)
                    st.toast(f"Imported {len(result.recipes)} recipe(s)")
                    st.rerun()
    if recipe_list:
        st.markdown("**Export**")
        e1, e2 = st.columns(2)
        e1.download_button("Download all recipes (JSON)", recipes.recipes_to_json(recipe_list),
                           "recipes.json", "application/json", key="rc_dl_json")
        e2.download_button("Download all recipes (CSV)", recipes.recipes_to_csv(recipe_list),
                           "recipes.csv", "text/csv", key="rc_dl_csv")


def _reagent_rows(recipe: Optional[recipes.Recipe]) -> list:
    if recipe is None:
        return []
    return [{"Slot": r.slot, "Item ID": r.item_id, "Name": r.item_name, "Quantity": r.quantity,
             "Quality": r.quality_label, "Auctionable": r.auctionable,
             "Savings eligible": r.savings_eligible} for r in recipe.reagents]


def _rows_to_reagents(frame: pd.DataFrame, index: pricing.PriceIndex) -> tuple:
    errors, pending, used = [], [], set()
    for position, (_, row) in enumerate(frame.iterrows(), start=1):
        no_item, no_qty = pd.isna(row["Item ID"]), pd.isna(row["Quantity"])
        if no_item and no_qty:
            continue
        if no_item or no_qty:
            errors.append(f"Reagent row {position}: item ID and quantity are both required.")
            continue
        if row["Quantity"] != int(row["Quantity"]):
            errors.append(f"Reagent row {position}: quantity must be a whole number.")
            continue
        item_id = int(row["Item ID"])
        slot = None if pd.isna(row["Slot"]) else int(row["Slot"])
        if slot is not None:
            used.add(slot)
        name = row["Name"] if isinstance(row["Name"], str) and row["Name"].strip() else index.get(item_id).name
        quality = row["Quality"] if isinstance(row["Quality"], str) else ""
        pending.append([slot, item_id, int(row["Quantity"]), name, quality,
                        True if pd.isna(row["Auctionable"]) else bool(row["Auctionable"]),
                        False if pd.isna(row["Savings eligible"]) else bool(row["Savings eligible"])])
    reagents, next_slot = [], 1
    for slot, item_id, quantity, name, quality, auctionable, eligible in pending:
        if slot is None:
            while next_slot in used:
                next_slot += 1
            slot = next_slot
            used.add(slot)
        reagents.append(recipes.Reagent(slot, item_id, quantity, name, quality, auctionable, eligible))
    return reagents, errors


def _delete_recipe(key: str) -> None:
    with db() as conn:
        database.delete_recipe(conn, key)
    st.session_state["rc_pick"] = NEW_RECIPE
    st.session_state["rc_ver"] = st.session_state.get("rc_ver", 0) + 1


def _editor(table: pd.DataFrame, index: pricing.PriceIndex, recipe_list: list) -> None:
    st.subheader("Add or edit a recipe")
    options = [NEW_RECIPE] + [f"{r.name} ({r.key})" for r in recipe_list]
    pick = st.selectbox("Recipe", options, key="rc_pick")
    existing = None if pick == NEW_RECIPE or pick not in options else recipe_list[options.index(pick) - 1]
    version = st.session_state.get("rc_ver", 0)
    sfx = f"{existing.key if existing else 'new'}_{version}"

    c1, c2 = st.columns(2)
    key_in = c1.text_input("Recipe key (your own unique ID, cannot be changed later)",
                           value=existing.key if existing else "", disabled=existing is not None,
                           key=f"rc_key_{sfx}", placeholder="e.g. decor-oak-chair")
    name = c2.text_input("Name", value=existing.name if existing else "", key=f"rc_name_{sfx}")

    picked = item_picker(table, f"rc_crafted_{sfx}", "Crafted item")
    if picked:
        crafted_id, crafted_name = picked
    elif existing:
        crafted_id, crafted_name = existing.crafted_item_id, existing.crafted_item_name
    else:
        crafted_id = crafted_name = None
    st.caption("Crafted item: " + (f"{crafted_name or 'unnamed'} (item {crafted_id})" if crafted_id else "not chosen yet")
               + (" - search above to change it." if existing else ""))

    d1, d2, d3, d4 = st.columns(4)
    output_qty = int(d1.number_input("Output quantity", min_value=1, step=1,
                                     value=existing.output_quantity if existing else 1, key=f"rc_oq_{sfx}"))
    profession = d2.text_input("Profession", value=(existing.profession or "") if existing else "", key=f"rc_prof_{sfx}")
    expansion = d3.text_input("Expansion", value=(existing.expansion or "") if existing else "", key=f"rc_exp_{sfx}")
    patch = d4.text_input("Game patch", value=(existing.game_patch or "") if existing else "", key=f"rc_patch_{sfx}",
                          placeholder="e.g. 12.1.5")
    e1, e2, e3, e4 = st.columns(4)
    skill = e1.number_input("Required skill", min_value=0, step=1, value=existing.required_skill if existing else None,
                            placeholder="optional", key=f"rc_skill_{sfx}")
    game_id = e2.number_input("Game recipe ID", min_value=0, step=1, value=existing.game_recipe_id if existing else None,
                              placeholder="optional", key=f"rc_gid_{sfx}")
    category = e3.text_input("Category", value=(existing.category or "") if existing else "", key=f"rc_cat_{sfx}",
                             placeholder="e.g. housing")
    verified_text = e4.text_input("Last verified (YYYY-MM-DD)", value=(existing.last_verified or "") if existing else "",
                                  key=f"rc_ver_{sfx}")
    source = st.text_input("Source of these quantities", value=existing.source if existing else "", key=f"rc_src_{sfx}",
                           placeholder="where you got them (in-game, website, your notes)")
    notes = st.text_area("Notes", value=existing.notes if existing else "", height=70, key=f"rc_notes_{sfx}")
    verified_today = st.checkbox("I checked these quantities in the game today (sets 'last verified' to today)",
                                 key=f"rc_vt_{sfx}")

    st.markdown("**Reagents** - add rows below the table or use the search. Reagents that share a slot number are "
                "alternatives (e.g. quality tiers); leave Slot empty for a separate slot.")
    rows_key = f"rc_rows_{sfx}"
    rows = st.session_state.setdefault(rows_key, _reagent_rows(existing))
    edited = st.data_editor(
        pd.DataFrame(rows, columns=REAGENT_COLUMNS), num_rows="dynamic", hide_index=True,
        key=f"rc_reagents_{sfx}_{len(rows)}",
        column_config={
            "Slot": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
            "Item ID": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
            "Quantity": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
            "Auctionable": st.column_config.CheckboxColumn(default=True),
            "Savings eligible": st.column_config.CheckboxColumn(default=False)})
    with st.expander("Add a reagent by searching"):
        found = item_picker(table, f"rc_addr_{sfx}", "Reagent")
        a1, a2, a3 = st.columns(3)
        add_qty = int(a1.number_input("Quantity", min_value=1, value=1, step=1, key=f"rc_addq_{sfx}"))
        add_auct = a2.checkbox("Auctionable", value=True, key=f"rc_adda_{sfx}")
        add_elig = a3.checkbox("Savings eligible", value=False, key=f"rc_adde_{sfx}")
        if found and st.button("Add reagent", key=f"rc_addbtn_{sfx}"):
            current = edited.to_dict("records")
            current.append({"Slot": None, "Item ID": found[0], "Name": found[1], "Quantity": add_qty, "Quality": "",
                            "Auctionable": add_auct, "Savings eligible": add_elig})
            st.session_state[rows_key] = current
            st.rerun()

    if st.button("Save recipe", type="primary", key=f"rc_save_{sfx}"):
        errors = []
        recipe_key = existing.key if existing else key_in.strip()
        if not recipe_key:
            errors.append("Give the recipe a key.")
        elif existing is None and recipe_key in {r.key for r in recipe_list}:
            errors.append("A recipe with that key already exists - pick it in the list above to edit it.")
        if crafted_id is None:
            errors.append("Choose the crafted item.")
        reagents, row_errors = _rows_to_reagents(edited, index)
        errors += row_errors
        last_verified = date.today().isoformat() if verified_today else (verified_text.strip() or None)
        recipe = None
        if not errors:
            recipe = recipes.Recipe(
                key=recipe_key, name=name.strip(), crafted_item_id=int(crafted_id), crafted_item_name=crafted_name,
                reagents=tuple(reagents), output_quantity=output_qty, profession=profession.strip() or None,
                required_skill=None if skill is None else int(skill), expansion=expansion.strip() or None,
                game_patch=patch.strip() or None, game_recipe_id=None if not game_id else int(game_id),
                source=source.strip(), last_verified=last_verified, category=category.strip() or None,
                notes=notes.strip())
            errors += recipes.validate_recipe(recipe)
        if errors:
            for message in errors:
                st.error(message)
        else:
            with db() as conn:
                database.save_recipe(conn, recipe)
            st.session_state["rc_ver"] = version + 1
            st.toast(f"Saved recipe '{recipe.name}'")
            st.rerun()

    if existing:
        with st.expander("Delete this recipe"):
            if st.checkbox(f"Yes, delete '{existing.name}'", key=f"rc_delok_{sfx}"):
                st.button("Delete recipe", on_click=_delete_recipe, args=(existing.key,), key=f"rc_del_{sfx}")


# --- Stock & costs -----------------------------------------------------------------------------------------

def render_stock(table: pd.DataFrame) -> None:
    st.caption("Track items you cannot simply buy (for example non-auctionable crafting materials). "
               "Mark such a reagent 'auctionable = false' in its recipe, then record your stock here. "
               "Stock caps how many crafts are possible; the opportunity cost is what you could get for one unit "
               "elsewhere. If you set no opportunity cost the item counts as free - and the app says so.")
    picked = item_picker(table, "stock_item", "Item")
    c1, c2, c3 = st.columns(3)
    on_hand = c1.number_input("Units on hand", min_value=0, step=1, value=None, placeholder="not tracked",
                              key="stock_qty")
    cost_g = c2.number_input("Opportunity cost per unit (g)", min_value=0.0, step=0.01, format="%.4f", value=None,
                             placeholder="none (counts as free)", key="stock_cost")
    notes = c3.text_input("Notes", key="stock_notes")
    if picked and st.button("Save item", type="primary", key="stock_save"):
        with db() as conn:
            database.upsert_item_resource(
                conn, picked[0], picked[1], None if on_hand is None else int(on_hand),
                None if cost_g is None else gold_to_copper(cost_g), notes)
        st.toast("Saved")
        st.rerun()

    with db() as conn:
        resources = database.list_item_resources(conn)
    if resources.empty:
        st.info("No items tracked yet.")
        return
    st.dataframe(pd.DataFrame({
        "Item ID": resources["item_id"], "Name": resources["name"], "On hand": resources["on_hand"],
        "Opportunity cost (g)": resources["opportunity_cost_copper"] / config.COPPER_PER_GOLD,
        "Notes": resources["notes"], "Updated (UTC)": resources["updated_at"]}),
        hide_index=True, column_config={
            "Item ID": st.column_config.NumberColumn(format="%d"),
            "On hand": st.column_config.NumberColumn(format="%d"),
            "Opportunity cost (g)": st.column_config.NumberColumn(format="%.4f")})
    d1, d2 = st.columns([2, 1])
    to_delete = d1.selectbox("Remove an item", resources["item_id"].tolist(), key="stock_del_id",
                             format_func=lambda i: f"{resources.loc[resources.item_id == i, 'name'].iloc[0] or 'Item'} ({i})")
    if d2.button("Remove", key="stock_del_btn"):
        with db() as conn:
            database.delete_item_resource(conn, int(to_delete))
        st.rerun()
