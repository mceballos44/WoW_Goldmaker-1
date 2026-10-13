"""Milling tab: profit calculator plus experiment tracking and yield calibration."""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

from goldmaker import config, database, milling, pricing
from goldmaker.currency import copper_to_gold, format_gold, gold_to_copper
from ui_common import db, default_ah_cut_pct, item_picker, reliability_badge

MODE_CALIBRATED = "Calibrated from my experiments"
MODE_MANUAL = "Manual yields"
GOLD_FMT = st.column_config.NumberColumn(format="%.2f")


def render(table: pd.DataFrame) -> None:
    st.header("Milling")
    st.caption("Expected yields come only from experiments you record (or numbers you type). "
               "No drop rates are built into this app.")
    tab_calc, tab_exp = st.tabs(["Calculator", "Experiments"])
    with tab_calc:
        render_calculator(table)
    with tab_exp:
        render_experiments(table)


# --- Calculator -------------------------------------------------------------------------------

def _choose_herb(table: pd.DataFrame, herbs: pd.DataFrame) -> Optional[tuple]:
    options = ["Search all items"]
    if not herbs.empty:
        options.insert(0, "Herbs with experiments")
    # The key changes when experiments first exist, so the default (herbs with experiments)
    # takes effect instead of a stale "Search all items" choice.
    source = st.radio("Herb", options, horizontal=True,
                      key=f"mill_herb_source_{'exp' if not herbs.empty else 'none'}")
    if source == "Herbs with experiments":
        labels = {
            f"{r.herb_name or 'Herb'} ({r.herb_item_id}) - {r.experiments} experiment(s), "
            f"{int(r.total_herbs)} herbs": (int(r.herb_item_id), r.herb_name)
            for r in herbs.itertuples()
        }
        return labels[st.selectbox("Herb with experiments", list(labels), key="mill_herb_exp")]
    return item_picker(table, "mill_herb", "Herb")


def _table_row(table: pd.DataFrame, item_id: int) -> Optional[pd.Series]:
    rows = table[table["item_id"] == item_id] if not table.empty else table
    return None if rows.empty else rows.iloc[0]


def render_calculator(table: pd.DataFrame) -> None:
    with db() as conn:
        herbs = database.list_milling_herbs(conn)
    herb = _choose_herb(table, herbs)
    if herb is None:
        st.info("Choose a herb above to start.")
        return
    herb_id, herb_name = herb

    with db() as conn:
        experiments = [milling.Experiment.from_row(r)
                       for r in database.list_milling_experiments(conn, herb_id)]
    summary = milling.estimate_yields(experiments)
    herb_price = pricing.resolve_price(table, herb_id) if not table.empty else None
    herb_label = herb_name or (herb_price.name if herb_price else None) or f"item {herb_id}"

    if summary.n_experiments:
        st.caption(f"**{herb_label}**: {summary.n_experiments} experiment(s), {summary.total_herbs} herbs "
                   f"milled. Reliability: **{reliability_badge(summary.reliability)}**.")
        for warning in summary.warnings:
            st.warning(warning)
        modes = [MODE_CALIBRATED, MODE_MANUAL]
    else:
        st.info(f"No experiments recorded for {herb_label} yet. Enter yields by hand below, or record "
                "an experiment on the *Experiments* tab.")
        modes = [MODE_MANUAL]
    mode = st.radio("Yield source", modes, horizontal=True, key=f"mill_mode_{herb_id}")

    reset = st.session_state.get("mill_reset", 0)
    c1, c2, c3, c4, c5 = st.columns(5)
    quantity = int(c1.number_input("Herbs to mill", min_value=1, value=100, step=10,
                                   key=f"mill_qty_{herb_id}"))
    default_herb_g = (copper_to_gold(herb_price.copper)
                      if herb_price is not None and not herb_price.is_missing else None)
    herb_price_g = c2.number_input("Herb price (g each)", min_value=0.0, value=default_herb_g, step=0.01,
                                   format="%.4f", placeholder="no price found - enter one",
                                   key=f"mill_herbprice_{herb_id}_{reset}",
                                   help="Prefilled from your price data. Editing it changes this calculation only.")
    ah_cut_pct = c3.number_input("AH cut (%)", min_value=0.0, max_value=99.0, value=default_ah_cut_pct(),
                                 step=0.5, key="mill_ahcut")
    sell_through_pct = c4.number_input("Expected sell-through (%)", min_value=0.0, max_value=100.0,
                                       value=100.0, step=5.0, key="mill_sellthrough",
                                       help="Share of the output you expect to actually sell. Unsold items count as 0.")
    target_roi_pct = c5.number_input("Target ROI (%)", min_value=-99.0, value=0.0, step=5.0,
                                     key="mill_targetroi",
                                     help="Used for the maximum purchase price. 0 means break-even.")
    if herb_price is not None and not herb_price.is_missing:
        st.caption(f"Herb price prefilled from: {herb_price.source}"
                   + (" (data may be stale)" if herb_price.is_stale else ""))
    if st.button("Reset prices to current data", key="mill_reset_btn"):
        st.session_state["mill_reset"] = reset + 1
        st.rerun()

    # ---- editable table of outputs -------------------------------------------------------------
    extra_key = f"mill_extra_{herb_id}"
    extra = st.session_state.setdefault(extra_key, [])
    rows, seen = [], set()
    for est in summary.estimates:
        rows.append({"id": est.item_id, "name": est.name, "yield": est.mean_per_herb,
                     "lo": est.ci_low, "hi": est.ci_high})
        seen.add(est.item_id)
    for item_id, name in extra:
        if item_id not in seen:
            rows.append({"id": item_id, "name": name, "yield": 0.0, "lo": None, "hi": None})
            seen.add(item_id)

    if mode == MODE_MANUAL:
        with st.expander("Add an output item"):
            picked = item_picker(table, "mill_add", "Output item")
            if picked and st.button("Add output", key="mill_add_btn"):
                extra.append(picked)
                st.rerun()
    if not rows:
        st.info("Add at least one output item (for example a pigment) to calculate.")
        return

    defaults: dict = {}
    display = []
    for r in rows:
        res = pricing.resolve_price(table, r["id"]) if not table.empty else None
        trow = _table_row(table, r["id"])
        name = r["name"] or (res.name if res else None) or f"item {r['id']}"
        defaults[r["id"]] = res
        range_text = (f"{r['lo']:.2f} - {r['hi']:.2f}"
                      if mode == MODE_CALIBRATED and r["lo"] is not None else "")
        display.append({
            "Item ID": r["id"], "Item": name, "Yield per herb": r["yield"], "95% range": range_text,
            "Price (g)": copper_to_gold(res.copper) if res is not None and not res.is_missing else None,
            "Price source": res.source if res is not None else "missing",
            "Sold / day (TSM)": None if trow is None else trow["sold_per_day"],
        })
    frame = pd.DataFrame(display)
    locked = ["Item ID", "Item", "95% range", "Price source", "Sold / day (TSM)"]
    if mode == MODE_CALIBRATED:
        locked.append("Yield per herb")
    st.markdown("**Outputs** - edit prices (and yields in manual mode); the results update immediately.")
    edited = st.data_editor(
        frame, hide_index=True, disabled=locked,
        key=f"mill_editor_{herb_id}_{mode}_{len(extra)}_{reset}",
        column_config={
            "Item ID": st.column_config.NumberColumn(format="%d"),
            "Yield per herb": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Price (g)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Sold / day (TSM)": st.column_config.NumberColumn(format="%.1f"),
        })

    yields = {int(r["Item ID"]): float(r["Yield per herb"] or 0.0) for _, r in edited.iterrows()}
    prices = {int(r["Item ID"]): (gold_to_copper(r["Price (g)"]) if pd.notna(r["Price (g)"]) else None)
              for _, r in edited.iterrows()}
    names = {int(r["Item ID"]): r["Item"] for _, r in edited.iterrows()}
    bounds = summary.bounds() if mode == MODE_CALIBRATED else None
    herb_copper = gold_to_copper(herb_price_g) if herb_price_g is not None else None

    try:
        result = milling.calculate_milling(
            herb_copper, quantity, yields, prices, names, ah_cut=ah_cut_pct / 100,
            sell_through=sell_through_pct / 100, target_roi=target_roi_pct / 100, yield_bounds=bounds)
    except ValueError as exc:
        st.error(str(exc))
        return

    _render_results(result, summary, mode, herb_label, quantity, names, defaults, edited, prices, target_roi_pct)


def _render_results(result, summary, mode, herb_label, quantity, names, defaults, edited, prices,
                    target_roi_pct) -> None:
    st.divider()
    if not result.complete:
        st.warning("Cannot finish the calculation: no price for **" + ", ".join(result.missing_prices)
                   + "**. Enter it in the table or herb price field. Missing prices are never counted "
                     "as 0.")

    stale = []
    for item_id, res in defaults.items():
        if res is not None and not res.is_missing and res.is_stale and not res.is_override:
            default_copper = res.copper
            if prices.get(item_id) == gold_to_copper(copper_to_gold(default_copper)):
                stale.append(names.get(item_id) or str(item_id))
    if stale:
        st.warning("These prices come from TSM data older than the warning threshold (or with no "
                   "timestamp): " + ", ".join(stale) + ". Refresh prices or check them in game.")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Net profit ({quantity:,} herbs)", format_gold(result.profit))
    m2.metric("ROI", f"{result.roi * 100:,.1f}%" if result.roi is not None else "n/a")
    m3.metric("Profit per herb", format_gold(result.profit_per_herb))
    m4.metric(f"Max herb price (ROI {target_roi_pct:g}%)", format_gold(result.max_purchase_price))

    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Cost", format_gold(result.cost))
    n2.metric("Gross value", format_gold(result.gross))
    n3.metric("AH fees", format_gold(result.fees))
    n4.metric("Net revenue", format_gold(result.net_revenue))

    if result.profit_per_herb_low is not None:
        st.info(f"Rough profit per herb range using each output's own 95% yield bounds: "
                f"{format_gold(result.profit_per_herb_low)} to {format_gold(result.profit_per_herb_high)}. "
                "This is wider than a true joint interval and only reflects yield uncertainty, not price changes.")
    elif mode == MODE_CALIBRATED and summary.n_experiments == 1:
        st.info("Yields come from a single batch, so no uncertainty range can be shown.")

    st.markdown("**Profit at different scales** (linear - assumes prices do not move as you sell)")
    scale_rows = []
    for herbs in (1, 100, 1000, quantity):
        scale_rows.append({
            "Herbs": herbs,
            "Cost (g)": copper_to_gold(result.cost_for(herbs)),
            "Net revenue (g)": copper_to_gold(result.net_revenue_for(herbs)),
            "Profit (g)": copper_to_gold(result.profit_for(herbs)),
        })
    scale = pd.DataFrame(scale_rows).drop_duplicates("Herbs")
    st.dataframe(scale, hide_index=True, column_config={
        "Herbs": st.column_config.NumberColumn(format="%d"),
        "Cost (g)": GOLD_FMT, "Net revenue (g)": GOLD_FMT, "Profit (g)": GOLD_FMT})

    st.markdown("**Output breakdown**")
    keep = (1 - result.ah_cut) * result.sell_through
    breakdown = pd.DataFrame([{
        "Item": names.get(line.item_id), "Expected units": line.units,
        "Price (g)": copper_to_gold(line.price_copper),
        "Gross (g)": copper_to_gold(line.gross_copper),
        "Net after fees & sell-through (g)": copper_to_gold(None if line.gross_copper is None else line.gross_copper * keep),
    } for line in result.lines])
    st.dataframe(breakdown, hide_index=True, column_config={
        "Expected units": st.column_config.NumberColumn(format="%.1f"),
        "Price (g)": GOLD_FMT, "Gross (g)": GOLD_FMT, "Net after fees & sell-through (g)": GOLD_FMT})

    # ---- liquidity ------------------------------------------------------------------------------
    sold = {int(r["Item ID"]): r["Sold / day (TSM)"] for _, r in edited.iterrows()}
    liquidity, slow = [], []
    for line in result.lines:
        units_to_sell = line.units * result.sell_through
        days = milling.days_of_volume(units_to_sell, sold.get(line.item_id))
        liquidity.append({"Item": names.get(line.item_id), "Units to sell": units_to_sell,
                          "TSM sold / day": sold.get(line.item_id), "Days of daily volume": days})
        if days is not None and days > 1:
            slow.append(f"{names.get(line.item_id)} ({days:.1f} days)")
    st.markdown("**Liquidity check**")
    st.dataframe(pd.DataFrame(liquidity), hide_index=True, column_config={
        "Units to sell": st.column_config.NumberColumn(format="%.1f"),
        "TSM sold / day": st.column_config.NumberColumn(format="%.1f"),
        "Days of daily volume": st.column_config.NumberColumn(format="%.2f")})
    if slow:
        st.warning("This output is larger than a day of TSM-reported sales for: " + ", ".join(slow)
                   + ". Profit above assumes everything sells at today's price - large batches usually "
                     "push prices down or take longer to sell. (One day is a rule of thumb, not a limit.)")
    st.caption("A missing 'sold / day' means TSM did not provide it for that dataset, not that nothing sells.")

    # ---- charts ---------------------------------------------------------------------------------
    if result.net_per_herb is not None:
        ch1, ch2 = st.columns(2)
        with ch1:
            st.markdown("**Net value per herb by output (g)**")
            values = pd.DataFrame({
                "Item": [names.get(l.item_id) for l in result.lines],
                "Net value per herb (g)": [copper_to_gold(l.yield_per_herb * (l.price_copper or 0) * keep)
                                           for l in result.lines],
            }).set_index("Item")
            st.bar_chart(values)
        with ch2:
            st.markdown("**Profit per herb vs. herb price (g)**")
            top = max(copper_to_gold(result.net_per_herb) * 1.5, 1.0)
            xs = [top * i / 40 for i in range(41)]
            st.line_chart(pd.DataFrame({
                "Herb price (g)": xs,
                "Profit per herb (g)": [copper_to_gold(result.net_per_herb) - x for x in xs],
            }).set_index("Herb price (g)"))

    st.caption(
        f"Assumptions: AH commission {result.ah_cut * 100:g}% of each sale (per-auction rounding ignored); "
        f"{result.sell_through * 100:g}% of output sells, the rest is worth 0; deposits ignored; "
        "revenue scales linearly with quantity.")


# --- Experiments --------------------------------------------------------------------------------

def render_experiments(table: pd.DataFrame) -> None:
    st.caption("Record each milling batch separately. One big batch gives a number but no uncertainty - "
               "several smaller batches show how much the yields actually vary.")
    _felwort_quickstart(table)
    _record_experiment(table)
    _experiment_overview()


def _felwort_quickstart(table: pd.DataFrame) -> None:
    with st.expander("Load my first experiment (40 Felwort -> 500 Roseate Pigment, 100 Sallow Pigment)"):
        wanted = {"herb": "Felwort", "roseate": "Roseate Pigment", "sallow": "Sallow Pigment"}
        chosen, ready = {}, True
        for key, name in wanted.items():
            candidates = pricing.find_by_name(table, name)
            if len(candidates) == 1:
                chosen[key] = (int(candidates.iloc[0]["item_id"]), name)
                st.caption(f"{name}: item {chosen[key][0]}")
            elif len(candidates) > 1:
                labels = {f"{name} ({int(i)})": (int(i), name) for i in candidates["item_id"]}
                chosen[key] = labels[st.selectbox(f"Several items are called '{name}' - which one?",
                                                  list(labels), key=f"fw_{key}")]
            else:
                ready = False
                st.warning(f"'{name}' was not found in the cached price data. Refresh prices, or record "
                           "the experiment manually below using its item ID.")
        if ready:
            herb, rose, sallow = chosen["herb"], chosen["roseate"], chosen["sallow"]
            with db() as conn:
                existing = database.list_milling_experiments(conn, herb[0])
            already = any(e["herbs_used"] == 40 and {o["item_id"]: o["quantity"] for o in e["outputs"]}
                          == {rose[0]: 500, sallow[0]: 100} for e in existing)
            if already:
                st.success("This experiment is already recorded.")
            elif st.button("Save this experiment", key="fw_save"):
                with db() as conn:
                    database.add_milling_experiment(
                        conn, herb[0], 40,
                        [{"item_id": rose[0], "name": rose[1], "quantity": 500},
                         {"item_id": sallow[0], "name": sallow[1], "quantity": 100}],
                        herb_name=herb[1], performed_at=str(date.today()),
                        notes="First experiment, supplied by user: 40 Felwort bought at 72g each.",
                        herb_cost_each_copper=gold_to_copper(72),
                        observed_prices={rose[0]: gold_to_copper(2), sallow[0]: gold_to_copper(40)})
                st.toast("Experiment saved")
                st.rerun()


def _record_experiment(table: pd.DataFrame) -> None:
    with st.expander("Record a new experiment"):
        herb = item_picker(table, "exp_herb", "Herb")
        c1, c2, c3 = st.columns(3)
        herbs_used = int(c1.number_input("Herbs milled", min_value=1, value=20, step=1, key="exp_herbs_used"))
        when = c2.date_input("Date", value=date.today(), key="exp_date")
        cost_each = c3.number_input("Herb cost each (g, optional)", min_value=0.0, value=None, step=0.01,
                                    format="%.4f", placeholder="optional", key="exp_cost")

        outputs = st.session_state.setdefault("exp_outputs", [])
        if herb is not None:
            with db() as conn:
                previous = database.list_milling_experiments(conn, herb[0])
            known = {o["item_id"]: o["item_name"] for e in previous for o in e["outputs"]}
            if known and st.button("Use output items from earlier experiments", key="exp_prefill"):
                for item_id, name in known.items():
                    if item_id not in [o["item_id"] for o in outputs]:
                        outputs.append({"item_id": item_id, "name": name})
                st.rerun()

        st.markdown("**Items received**")
        picked = item_picker(table, "exp_out", "Output item")
        if picked and st.button("Add output item", key="exp_add_out"):
            if picked[0] not in [o["item_id"] for o in outputs]:
                outputs.append({"item_id": picked[0], "name": picked[1]})
            st.rerun()

        quantities = {}
        for out in list(outputs):
            q1, q2 = st.columns([4, 1])
            quantities[out["item_id"]] = int(q1.number_input(
                f"{out['name'] or 'Item'} ({out['item_id']}) - quantity received", min_value=0, value=0,
                step=1, key=f"exp_qty_{out['item_id']}"))
            if q2.button("Remove", key=f"exp_rm_{out['item_id']}"):
                outputs.remove(out)
                st.rerun()
        notes = st.text_input("Notes", key="exp_notes")

        if st.button("Save experiment", type="primary", key="exp_save"):
            if herb is None:
                st.error("Choose the herb first.")
            elif not outputs:
                st.error("Add at least one output item.")
            else:
                try:
                    with db() as conn:
                        database.add_milling_experiment(
                            conn, herb[0], herbs_used,
                            [{"item_id": o["item_id"], "name": o["name"], "quantity": quantities[o["item_id"]]}
                             for o in outputs],
                            herb_name=herb[1], performed_at=str(when), notes=notes,
                            herb_cost_each_copper=gold_to_copper(cost_each) if cost_each is not None else None)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state["exp_outputs"] = []
                    st.toast("Experiment saved")
                    st.rerun()


def _experiment_overview() -> None:
    with db() as conn:
        experiments = database.list_milling_experiments(conn)
        herbs = database.list_milling_herbs(conn)
    st.subheader("Recorded experiments")
    if not experiments:
        st.info("No experiments yet.")
        return

    rows = []
    for e in experiments:
        rows.append({
            "ID": e["id"], "Herb": e["herb_name"] or e["herb_item_id"], "Herbs milled": e["herbs_used"],
            "Received": ", ".join(f"{o['quantity']}x {o['item_name'] or o['item_id']}" for o in e["outputs"]),
            "Date": e["performed_at"],
            "Herb cost each (g)": copper_to_gold(e["herb_cost_each_copper"]),
            "Observed prices (g)": ", ".join(f"{k}: {copper_to_gold(v):g}" for k, v in e["observed_prices"].items()),
            "Notes": e["notes"],
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={
        "Herb cost each (g)": st.column_config.NumberColumn(format="%.2f")})
    d1, d2 = st.columns([2, 1])
    to_delete = d1.selectbox("Delete an experiment", [e["id"] for e in experiments], key="exp_delete_id")
    if d2.button("Delete", key="exp_delete_btn"):
        with db() as conn:
            database.delete_milling_experiment(conn, to_delete)
        st.rerun()

    st.subheader("Calibrated yields")
    labels = {f"{r.herb_name or r.herb_item_id} ({r.herb_item_id})": int(r.herb_item_id) for r in herbs.itertuples()}
    herb_id = labels[st.selectbox("Herb", list(labels), key="exp_summary_herb")]
    summary = milling.estimate_yields([milling.Experiment.from_row(e)
                                       for e in experiments if e["herb_item_id"] == herb_id])
    st.caption(f"{summary.n_experiments} experiment(s), {summary.total_herbs} herbs milled, "
               f"{sum(e.total_output for e in summary.estimates)} items received in total. "
               f"Reliability: **{reliability_badge(summary.reliability)}**.")
    st.dataframe(pd.DataFrame([{
        "Item": e.name or e.item_id, "Total received": e.total_output,
        "Mean per herb": e.mean_per_herb, "Std. error": e.std_error,
        "95% low": e.ci_low, "95% high": e.ci_high} for e in summary.estimates]),
        hide_index=True, column_config={
            c: st.column_config.NumberColumn(format="%.3f")
            for c in ("Mean per herb", "Std. error", "95% low", "95% high")})
    for warning in summary.warnings:
        st.warning(warning)
    st.caption("Uncertainty treats each recorded experiment as one observation, so it needs at least two. "
               "Reliability labels use rule-of-thumb thresholds (5+ experiments and 200+ herbs for "
               "'moderate'), not a statistical guarantee.")
