"""AH Investment tab: buy now, sell later - bearish / baseline / bullish scenarios."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from goldmaker import excel_io, investment, pricing
from goldmaker.currency import copper_to_gold, format_gold, gold_to_copper
from ui_common import default_ah_cut_pct, item_picker

GOLD = st.column_config.NumberColumn(format="%.2f")


def render(table: pd.DataFrame, context: dict) -> None:
    st.header("AH Investment")
    st.caption("Buy before a patch, sell after. Every future price and sell-through number is **your own "
               "estimate** - the app has no forecast. The three scenarios show how much the result depends "
               "on those guesses.")
    picked = item_picker(table, "inv_item", "Item (optional - prefills the current price)")
    current = pricing.resolve_price(table, picked[0]) if picked and not table.empty else None
    name = (picked[1] if picked else None) or (current.name if current else None) or ""
    tag = picked[0] if picked else "none"

    c1, c2, c3, c4 = st.columns(4)
    buy_g = c1.number_input("Current (buy) price, g each", min_value=0.0, step=0.01, format="%.4f",
                            value=None if current is None or current.is_missing else copper_to_gold(current.copper),
                            placeholder="enter a price", key=f"inv_buy_{tag}")
    quantity = int(c2.number_input("Quantity", min_value=1, value=100, step=10, key="inv_qty"))
    other_g = c3.number_input("Deposits lost / other costs (g, total)", min_value=0.0, value=0.0, step=1.0,
                              key="inv_other", help="Re-listing costs, lost deposits, anything else - one total.")
    ah_cut = c4.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key="inv_ahcut")
    if current is not None and not current.is_missing:
        st.caption(f"Buy price prefilled from {current.source}" + (" (data may be stale)" if current.is_stale else ""))

    d1, d2, d3 = st.columns(3)
    expected_g = d1.number_input("Expected future price (g each, baseline)", min_value=0.0, value=None,
                                 step=0.01, format="%.4f", placeholder="your estimate", key="inv_expected")
    sell_pct = d2.number_input("Expected sell-through (%)", 0.0, 100.0, value=80.0, step=5.0, key="inv_sell",
                               help="Share of the units you expect to sell. Unsold units count as 0.")
    spread_pct = d3.number_input("Bearish / bullish spread (+/- %)", 0.0, 95.0, value=20.0, step=5.0,
                                 key="inv_spread", help="Your own choice of how far the other scenarios sit "
                                                        "below/above the baseline. Edit each one below.")
    if buy_g is None or expected_g is None:
        st.info("Enter the current price and your expected future price to see the scenarios.")
        return

    defaults = investment.default_scenarios(gold_to_copper(expected_g), sell_pct / 100, spread_pct / 100)
    frame = pd.DataFrame([{"Scenario": s.name, "Resale price (g)": copper_to_gold(s.resale_price_copper),
                           "Sell-through (%)": s.sell_through * 100} for s in defaults])
    st.markdown("**Scenarios** - edit any price or sell-through")
    edited = st.data_editor(frame, hide_index=True, disabled=["Scenario"],
                            key=f"inv_scen_{expected_g}_{sell_pct}_{spread_pct}", column_config={
                                "Resale price (g)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
                                "Sell-through (%)": st.column_config.NumberColumn(min_value=0.0, max_value=100.0)})
    try:
        scenarios = [investment.Scenario(r["Scenario"], gold_to_copper(r["Resale price (g)"]),
                                         float(r["Sell-through (%)"]) / 100) for _, r in edited.iterrows()]
        result = investment.calculate_investment(gold_to_copper(buy_g), quantity, scenarios, ah_cut / 100,
                                                 gold_to_copper(other_g))
    except (ValueError, TypeError) as exc:
        st.error(str(exc) if isinstance(exc, ValueError) else "Every scenario needs a price and a sell-through.")
        return

    base = result.scenario("Baseline")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Investment cost", format_gold(result.investment_cost))
    m2.metric("Baseline profit", format_gold(base.profit))
    m3.metric("Baseline ROI", f"{base.roi * 100:,.1f}%" if base.roi is not None else "n/a")
    m4.metric("Break-even resale price (baseline)", format_gold(base.breakeven_resale_price))

    rows = pd.DataFrame([{
        "Scenario": s.name, "Resale price (g)": copper_to_gold(s.resale_price_copper),
        "Units sold": s.units_sold, "Gross (g)": copper_to_gold(s.gross), "AH fees (g)": copper_to_gold(s.fees),
        "Net revenue (g)": copper_to_gold(s.net_revenue), "Profit (g)": copper_to_gold(s.profit),
        "ROI %": None if s.roi is None else s.roi * 100,
        "Break-even resale (g)": copper_to_gold(s.breakeven_resale_price)} for s in result.scenarios])
    st.dataframe(rows, hide_index=True, column_config={
        "Resale price (g)": GOLD, "Gross (g)": GOLD, "AH fees (g)": GOLD, "Net revenue (g)": GOLD,
        "Profit (g)": GOLD, "Break-even resale (g)": GOLD, "Units sold": st.column_config.NumberColumn(format="%.1f"),
        "ROI %": st.column_config.NumberColumn(format="%.1f")})
    st.bar_chart(rows.set_index("Scenario")[["Profit (g)"]])

    data = excel_io.ExportData(context={**context, "Item": name or "-"}, investment=result, investment_name=name)
    st.download_button("Download these scenarios as Excel (with formulas)", excel_io.build_workbook(data),
                       "investment_scenarios.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="inv_xlsx")
    st.caption(f"Assumptions: AH commission {ah_cut:g}% of each sale; units that do not sell are worth 0; "
               "selling does not move the price; deposits/other costs are the total you entered. "
               "Not financial certainty - patch-day prices can move far outside these scenarios.")
