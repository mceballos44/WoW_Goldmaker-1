"""Opportunities tab: rank every configured recipe and processing conversion against current prices."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from goldmaker import conversion, crafting, database, pricing, scanner
from goldmaker.currency import copper_to_gold
import ui_crafting
from ui_common import db, default_ah_cut_pct, reliability_badge

GOLD = st.column_config.NumberColumn(format="%.2f")
KINDS = ("crafting", "milling", "prospecting")
SIZE_HELP = {"milling": "herbs per mill", "prospecting": "ore per prospect"}


def _setting_float(key: str) -> Optional[float]:
    with db() as conn:
        raw = database.get_setting(conn, key)
    try:
        return float(raw) if raw not in (None, "") else None
    except ValueError:
        return None


def _operation_settings() -> dict:
    """Per kind: seconds per operation and (for processing) units per operation - user settings."""
    with st.expander("Operation sizes and times (needed for 'per operation' and gold per hour)"):
        st.caption("The app does not know these - check in game and enter them. Empty = unknown: profit is then "
                   "shown per single input unit and gold per hour is left blank.")
        values, cols = {}, st.columns(len(KINDS))
        for col, kind in zip(cols, KINDS):
            col.markdown(f"**{kind.title()}**")
            seconds = col.number_input("Seconds per operation", min_value=0.1, step=0.5,
                                       value=_setting_float(f"op_seconds_{kind}"), placeholder="unknown",
                                       key=f"scan_sec_{kind}")
            size = None
            if kind in SIZE_HELP:
                stored = _setting_float(f"op_units_{kind}")
                size = col.number_input(SIZE_HELP[kind].capitalize(), min_value=1, step=1,
                                        value=None if stored is None else int(stored), placeholder="unknown",
                                        key=f"scan_size_{kind}")
            values[kind] = (seconds, size)
        if st.button("Save these settings", key="scan_save"):
            with db() as conn:
                for kind, (seconds, size) in values.items():
                    database.set_setting(conn, f"op_seconds_{kind}", "" if seconds is None else str(seconds))
                    if kind in SIZE_HELP:
                        database.set_setting(conn, f"op_units_{kind}", "" if size is None else str(int(size)))
            st.toast("Saved")
    return values


def collect(table: pd.DataFrame, ah_cut: float, sell_through: float, operations: int,
            settings: dict) -> list:
    """All opportunities (recipes + every input item with experiments)."""
    index = pricing.PriceIndex(table)
    opportunity, on_hand = ui_crafting.load_resources()
    evaluations = crafting.evaluate_recipes(ui_crafting.load_recipes(), index.get, index.demand, opportunity,
                                            on_hand, ah_cut=ah_cut, sell_through=sell_through)
    found = scanner.from_recipes(evaluations, operations, settings["crafting"][0], sell_through)
    with db() as conn:
        for process in database.PROCESSES:
            experiments = database.list_experiments(conn, process)
            for r in database.list_experiment_inputs(conn, process).itertuples():
                batches = [conversion.Batch.from_row(e) for e in experiments if e["herb_item_id"] == r.herb_item_id]
                seconds, size = settings[process]
                opp = scanner.from_conversion(
                    process, int(r.herb_item_id), r.herb_name, conversion.estimate_yields(batches), index.get,
                    index.demand, operations, None if size is None else int(size), seconds, ah_cut, sell_through)
                if opp is not None:
                    found.append(opp)
    return found


def render(table: pd.DataFrame) -> None:
    st.header("Opportunities")
    st.caption("Every recipe and every herb/ore with recorded experiments, ranked against current prices. "
               "These are **theoretical margins**, not demonstrated sales - nothing here assumes listed "
               "inventory will sell.")
    if table.empty:
        st.warning("No prices cached yet. Click **Refresh Prices** in the sidebar.")
    c1, c2, c3, c4 = st.columns(4)
    ah_cut = c1.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key="scan_ahcut")
    sell = c2.number_input("Expected sell-through (%)", 0.0, 100.0, value=100.0, step=5.0, key="scan_sell")
    operations = int(c3.number_input("Planned operations", min_value=1, value=20, step=5, key="scan_ops",
                                     help="Used for required investment and the liquidity check."))
    by = c4.selectbox("Rank by", list(scanner.RANKINGS), key="scan_rank")
    settings = _operation_settings()
    f1, f2, f3 = st.columns(3)
    kinds = f1.multiselect("Kinds", KINDS, default=list(KINDS), key="scan_kinds")
    only_priced = f2.checkbox("Only fully priced", value=True, key="scan_priced")
    hide_small = f3.checkbox("Hide small-sample yields", value=False, key="scan_small")

    opportunities = [o for o in collect(table, ah_cut / 100, sell / 100, operations, settings) if o.kind in kinds]
    st.session_state["scan_last"] = opportunities          # reused by the Excel export
    if only_priced:
        opportunities = [o for o in opportunities if o.complete]
    if hide_small:
        opportunities = [o for o in opportunities if o.reliability not in scanner.SMALL_SAMPLE]
    if not opportunities:
        st.info("Nothing to show. Add recipes (Housing / Crafting) or record milling / prospecting experiments.")
        return

    ranked = scanner.rank(opportunities, by)
    frame = pd.DataFrame([{
        "Kind": o.kind, "Name": o.name, "One operation": o.operation,
        "Profit / op (g)": copper_to_gold(o.profit_per_op), "ROI %": None if o.roi is None else o.roi * 100,
        "Profit / hour (g)": copper_to_gold(o.profit_per_hour), "Cost / op (g)": copper_to_gold(o.cost_per_op),
        f"Investment for {operations} ops (g)": copper_to_gold(o.investment),
        "Days of daily volume": o.liquidity_days, "Reliability": reliability_badge(o.reliability),
        "Flags": "; ".join(o.flags)} for o in ranked])
    st.dataframe(frame, hide_index=True, column_config={
        "Profit / op (g)": GOLD, "Profit / hour (g)": GOLD, "Cost / op (g)": GOLD,
        f"Investment for {operations} ops (g)": GOLD, "ROI %": st.column_config.NumberColumn(format="%.1f"),
        "Days of daily volume": st.column_config.NumberColumn(format="%.2f")})
    slow = [o.name for o in ranked if o.liquidity_days is not None and o.liquidity_days > 1]
    if slow:
        st.warning(f"Selling {operations} operations' output would take more than a day of TSM-reported volume for: "
                   + ", ".join(slow[:10]) + ". Expect slower sales or lower prices.")
    flagged = sum(1 for o in ranked if o.flags)
    st.caption(f"{len(ranked)} opportunities, {flagged} with warnings (stale prices, unverified recipes, small "
               "samples, assumed-free reagents). Days of daily volume is blank when TSM gives no sales data. "
               "Different kinds use different operations - compare profit per hour where you entered times.")
