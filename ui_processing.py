"""Shared UI for "one input item -> outputs" processing (milling, prospecting).

Each tab: a calculator, experiment tracking with yield calibration, and a comparison of
every input item that has experiments. Yields come only from recorded experiments or
numbers the user types - no drop rates are built in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Optional

import pandas as pd
import streamlit as st

from goldmaker import conversion, database, pricing, scanner
from goldmaker.currency import copper_to_gold, format_gold, gold_to_copper
from ui_common import db, default_ah_cut_pct, item_picker, reliability_badge, saved, saved_index

MODE_CALIBRATED = "Calibrated from my experiments"
MODE_REFERENCE = "Reference yields (outside data)"
MODE_MANUAL = "Manual yields"
GOLD_FMT = st.column_config.NumberColumn(format="%.2f")


@dataclass(frozen=True)
class ProcessSpec:
    process: str                    # database process name
    title: str                      # "Milling"
    unit: str                       # "herb"
    units: str                      # "herbs"
    verb: str                       # "mill"
    verb_past: str                  # "milled"
    prefix: str                     # widget key prefix, unique per tab
    output_example: str             # "a pigment"
    operation: str                  # "mill" / "prospect": one use of the ability
    extra_cost_help: Optional[str] = None   # shows an "other cost per unit" input when set
    quickstart: Optional[Callable] = None   # extra widget at the top of the Experiments tab

    @property
    def wording(self) -> conversion.Wording:
        return conversion.Wording(self.unit, self.units, self.verb_past)

    @property
    def unit_title(self) -> str:
        return self.unit[:1].upper() + self.unit[1:]


def render(spec: ProcessSpec, table: pd.DataFrame) -> None:
    st.header(spec.title)
    st.caption("Expected yields come only from experiments you record (or numbers you type). "
               "No drop rates are built into this app.")
    tab_calc, tab_exp, tab_ref, tab_cmp = st.tabs(
        ["Calculator", "Experiments", "Reference yields (e.g. Wowhead)", f"Compare {spec.units}"])
    with tab_calc:
        render_calculator(spec, table)
    with tab_exp:
        render_experiments(spec, table)
    with tab_ref:
        render_reference(spec, table)
    with tab_cmp:
        render_compare(spec, table)


def _summary(spec: ProcessSpec, input_id: int, experiments: Optional[list] = None):
    if experiments is None:
        with db() as conn:
            experiments = database.list_experiments(conn, spec.process, input_id)
    batches = [conversion.Batch.from_row(e) for e in experiments if e["herb_item_id"] == input_id]
    return conversion.estimate_yields(batches, spec.wording)


# --- Calculator -------------------------------------------------------------------------------

def _known_inputs(spec: ProcessSpec) -> pd.DataFrame:
    """Input items with experiments, plus those that only have reference yields (experiments = 0)."""
    with db() as conn:
        inputs = database.list_experiment_inputs(conn, spec.process)
        refs = database.list_reference_inputs(conn, spec.process)
        manual = database.list_manual_inputs(conn, spec.process)
    frames = [inputs] if not inputs.empty else []
    extra = refs[~refs["input_item_id"].isin(inputs["herb_item_id"])]
    if not extra.empty:
        frames.append(pd.DataFrame({"herb_item_id": extra["input_item_id"], "herb_name": extra["input_name"],
                                    "experiments": 0, "total_herbs": 0, "kind": "reference yields only"}))
    known = set(inputs["herb_item_id"]) | set(refs["input_item_id"])
    manual = manual[~manual["input_item_id"].isin(known)]
    if not manual.empty:
        frames.append(pd.DataFrame({"herb_item_id": manual["input_item_id"], "herb_name": None,
                                    "experiments": 0, "total_herbs": 0, "kind": "manual yields only"}))
    if not frames:
        return inputs
    return pd.concat(frames, ignore_index=True)


def _reference(spec: ProcessSpec, input_id: int) -> conversion.YieldSummary:
    with db() as conn:
        return conversion.reference_summary(database.list_reference_yields(conn, spec.process, input_id))


def _choose_input(spec: ProcessSpec, table: pd.DataFrame, inputs: pd.DataFrame) -> Optional[tuple]:
    p = spec.prefix
    options = ["Search all items"]
    if not inputs.empty:
        options.insert(0, f"{spec.units.title()} with saved yields")
    # The key changes when experiments first exist, so that default takes effect.
    src_key = f"{p}_src_{'exp' if not inputs.empty else 'none'}"
    source = st.radio(spec.unit_title, options, horizontal=True, key=src_key,
                      index=saved_index(src_key, options))
    if source != "Search all items":
        labels = {
            (f"{r.herb_name or spec.unit_title} ({r.herb_item_id}) - "
             + (f"{r.experiments} experiment(s), {int(r.total_herbs)} {spec.units}" if r.experiments
                else getattr(r, "kind", None) or "reference yields only")):
            (int(r.herb_item_id), r.herb_name if isinstance(r.herb_name, str) else None)
            for r in inputs.itertuples()
        }
        # Labels contain counts that change, so the last choice is remembered by item ID.
        last_key = f"{p}_last_input_id"
        ids = [v[0] for v in labels.values()]
        last = saved(last_key)
        pick = st.selectbox(spec.unit_title, list(labels), key=f"{p}_input_exp",
                            index=ids.index(last) if last in ids else 0)
        st.session_state[last_key] = labels[pick][0]
        return labels[pick]
    return item_picker(table, f"{p}_input", spec.unit_title)


def _table_row(table: pd.DataFrame, item_id: int) -> Optional[pd.Series]:
    rows = table[table["item_id"] == item_id] if not table.empty else table
    return None if rows.empty else rows.iloc[0]


def render_calculator(spec: ProcessSpec, table: pd.DataFrame) -> None:
    p = spec.prefix
    chosen = _choose_input(spec, table, _known_inputs(spec))
    if chosen is None:
        st.info(f"Choose a{'n' if spec.unit[0] in 'aeiou' else ''} {spec.unit} above to start.")
        return
    input_id, input_name = chosen

    calibrated = _summary(spec, input_id)
    reference = _reference(spec, input_id)
    input_price = pricing.resolve_price(table, input_id) if not table.empty else None
    label = input_name or (input_price.name if input_price else None) or f"item {input_id}"

    modes = []
    if calibrated.n_experiments:
        modes.append(MODE_CALIBRATED)
    if reference.estimates:
        modes.append(MODE_REFERENCE)
    modes.append(MODE_MANUAL)
    if not calibrated.n_experiments:
        st.info(f"No experiments recorded for {label} yet. "
                + ("Reference yields are available, or enter yields by hand. " if reference.estimates
                   else "Enter yields by hand below, add reference yields, or ")
                + "record an experiment on the *Experiments* tab.")
    with db() as conn:
        manual_saved = database.list_manual_yields(conn, spec.process, input_id)
    mode_key = f"{p}_mode_{input_id}_{len(modes)}"
    default_mode = modes.index(MODE_MANUAL) if manual_saved and not calibrated.n_experiments else 0
    mode = st.radio("Yield source", modes, horizontal=True, key=mode_key,
                    index=saved_index(mode_key, modes, default_mode))
    summary = reference if mode == MODE_REFERENCE else calibrated
    if mode == MODE_MANUAL and not calibrated.estimates:
        summary = reference                       # start manual yields from the reference numbers
    if mode == MODE_CALIBRATED:
        st.caption(f"**{label}**: {summary.n_experiments} experiment(s), {summary.total_units} {spec.units} "
                   f"{spec.verb_past}. Reliability: **{reliability_badge(summary.reliability)}**.")
    if mode != MODE_MANUAL:
        for warning in summary.warnings:
            st.warning(warning)

    reset = st.session_state.get(f"{p}_reset", 0)
    c1, c2, c3, c4, c5 = st.columns(5)
    quantity = int(c1.number_input(f"{spec.units.title()} to {spec.verb}", min_value=1, step=10,
                                   value=saved(f"{p}_qty_{input_id}", 100), key=f"{p}_qty_{input_id}"))
    default_g = (copper_to_gold(input_price.copper)
                 if input_price is not None and not input_price.is_missing else None)
    price_g = c2.number_input(f"{spec.unit_title} price (g each)", min_value=0.0, value=default_g, step=0.01,
                              format="%.4f", placeholder="no price found - enter one",
                              key=f"{p}_price_{input_id}_{reset}",
                              help="Prefilled from your price data. Editing it changes this calculation only.")
    ah_cut_pct = c3.number_input("AH cut (%)", min_value=0.0, max_value=99.0, value=default_ah_cut_pct(),
                                 step=0.5, key=f"{p}_ahcut")
    sell_through_pct = c4.number_input("Expected sell-through (%)", min_value=0.0, max_value=100.0,
                                       value=saved(f"{p}_sellthrough", 100.0), step=5.0, key=f"{p}_sellthrough",
                                       help="Share of the output you expect to actually sell. Unsold items count as 0.")
    target_roi_pct = c5.number_input("Target ROI (%)", min_value=-99.0, value=saved(f"{p}_targetroi", 0.0),
                                     step=5.0,
                                     key=f"{p}_targetroi",
                                     help="Used for the maximum purchase price. 0 means break-even.")
    e1, e2, _ = st.columns([1, 1, 3])
    extra_g = 0.0
    if spec.extra_cost_help:
        extra_g = e1.number_input(f"Other cost per {spec.unit} (g)", min_value=0.0, step=0.01,
                                  value=saved(f"{p}_extra_{input_id}", 0.0),
                                  format="%.4f", key=f"{p}_extra_{input_id}", help=spec.extra_cost_help)
    per_op = e2.number_input(f"{spec.units.title()} per {spec.operation}", min_value=1, step=1,
                             value=saved(f"{p}_perop_{input_id}"), placeholder="optional",
                             key=f"{p}_perop_{input_id}",
                             help=f"How many {spec.units} one {spec.operation} uses - check in game. Only used "
                                  f"for the 'per {spec.operation}' figures; leave empty to skip them.")
    if input_price is not None and not input_price.is_missing:
        st.caption(f"{spec.unit_title} price prefilled from: {input_price.source}"
                   + (" (data may be stale)" if input_price.is_stale else ""))
    if st.button("Reset prices to current data", key=f"{p}_reset_btn"):
        st.session_state[f"{p}_reset"] = reset + 1
        st.rerun()

    # ---- editable table of outputs -------------------------------------------------------------
    extra_key = f"{p}_extra_outputs_{input_id}"
    extra = st.session_state.setdefault(extra_key, [])
    rows, seen = [], set()
    if mode == MODE_MANUAL and manual_saved:      # yields typed earlier, kept in the database
        for m in manual_saved:
            rows.append({"id": m["output_item_id"], "name": m["output_name"], "yield": m["yield_per_unit"],
                         "lo": None, "hi": None})
            seen.add(m["output_item_id"])
    for est in summary.estimates if not (mode == MODE_MANUAL and manual_saved) else ():
        rows.append({"id": est.item_id, "name": est.name, "yield": est.mean_per_unit,
                     "lo": est.ci_low, "hi": est.ci_high})
        seen.add(est.item_id)
    for item_id, name in extra:
        if item_id not in seen:
            rows.append({"id": item_id, "name": name, "yield": 0.0, "lo": None, "hi": None})
            seen.add(item_id)

    if mode == MODE_MANUAL:
        with st.expander("Add an output item"):
            picked = item_picker(table, f"{p}_add", "Output item")
            if picked and st.button("Add output", key=f"{p}_add_btn"):
                extra.append(picked)
                st.rerun()
    if not rows:
        st.info(f"Add at least one output item (for example {spec.output_example}) to calculate.")
        return

    defaults: dict = {}
    display = []
    for r in rows:
        res = pricing.resolve_price(table, r["id"]) if not table.empty else None
        trow = _table_row(table, r["id"])
        name = r["name"] or (res.name if res else None) or f"item {r['id']}"
        defaults[r["id"]] = res
        range_text = (f"{r['lo']:.3f} - {r['hi']:.3f}"
                      if mode == MODE_CALIBRATED and r["lo"] is not None else "")
        display.append({
            "Item ID": r["id"], "Item": name, f"Yield per {spec.unit}": r["yield"], "95% range": range_text,
            "Price (g)": copper_to_gold(res.copper) if res is not None and not res.is_missing else None,
            "Price source": res.source if res is not None else "missing",
            "Sold / day (TSM)": None if trow is None else trow["sold_per_day"],
        })
    frame = pd.DataFrame(display)
    yield_col = f"Yield per {spec.unit}"
    locked = ["Item ID", "Item", "95% range", "Price source", "Sold / day (TSM)"]
    if mode in (MODE_CALIBRATED, MODE_REFERENCE):
        locked.append(yield_col)
    st.markdown("**Outputs** - edit prices (and yields in manual mode); the results update immediately.")
    edited = st.data_editor(
        frame, hide_index=True, disabled=locked,
        key=f"{p}_editor_{input_id}_{mode}_{len(extra)}_{reset}",
        column_config={
            "Item ID": st.column_config.NumberColumn(format="%d"),
            yield_col: st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Price (g)": st.column_config.NumberColumn(min_value=0.0, format="%.4f"),
            "Sold / day (TSM)": st.column_config.NumberColumn(format="%.1f"),
        })

    yields = {int(r["Item ID"]): float(r[yield_col] or 0.0) for _, r in edited.iterrows()}
    if mode == MODE_MANUAL:
        _remember_manual_yields(spec, input_id, edited, yield_col, manual_saved,
                                {r["id"]: r["yield"] for r in rows})
    prices = {int(r["Item ID"]): (gold_to_copper(r["Price (g)"]) if pd.notna(r["Price (g)"]) else None)
              for _, r in edited.iterrows()}
    names = {int(r["Item ID"]): r["Item"] for _, r in edited.iterrows()}
    bounds = summary.bounds() if mode == MODE_CALIBRATED else None
    input_copper = gold_to_copper(price_g) if price_g is not None else None

    try:
        result = conversion.calculate_conversion(
            input_copper, quantity, yields, prices, names, ah_cut=ah_cut_pct / 100,
            sell_through=sell_through_pct / 100, target_roi=target_roi_pct / 100, yield_bounds=bounds,
            extra_cost_per_unit=gold_to_copper(extra_g) or 0, input_label=label)
    except ValueError as exc:
        st.error(str(exc))
        return

    _render_results(spec, result, summary, mode, quantity, names, defaults, edited, prices,
                    target_roi_pct, per_op)


def _remember_manual_yields(spec, input_id: int, edited: pd.DataFrame, yield_col: str, stored: list,
                            shown: dict) -> None:
    """Save manual yields once the user has changed them, so they survive closing the app.

    ``shown`` are the yields the table started with; merely opening manual mode saves nothing.
    """
    rows = [{"item_id": int(r["Item ID"]), "name": r["Item"], "yield_per_unit": float(r[yield_col] or 0.0)}
            for _, r in edited.iterrows()]
    before = {m["output_item_id"]: m["yield_per_unit"] for m in stored}
    now = {r["item_id"]: r["yield_per_unit"] for r in rows}
    edited_by_user = now != {k: float(v or 0.0) for k, v in shown.items()}
    if (edited_by_user or (stored and now != before)) and any(now.values()):
        with db() as conn:
            database.save_manual_yields(conn, spec.process, input_id, rows)
        st.caption("Manual yields saved - they will be here next time.")
    elif stored:
        st.caption("Manual yields are saved and reloaded automatically.")


def _render_results(spec, result, summary, mode, quantity, names, defaults, edited, prices,
                    target_roi_pct, per_op) -> None:
    st.divider()
    if not result.complete:
        st.warning("Cannot finish the calculation: no price for **" + ", ".join(result.missing_prices)
                   + "**. Enter it in the table or the price field. Missing prices are never counted as 0.")

    stale = []
    for item_id, res in defaults.items():
        if res is not None and not res.is_missing and res.is_stale and not res.is_override:
            if prices.get(item_id) == gold_to_copper(copper_to_gold(res.copper)):
                stale.append(names.get(item_id) or str(item_id))
    if stale:
        st.warning("These prices come from TSM data older than the warning threshold (or with no "
                   "timestamp): " + ", ".join(stale) + ". Refresh prices or check them in game.")

    unit = spec.unit
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Net profit ({quantity:,} {spec.units})", format_gold(result.profit))
    m2.metric("ROI", f"{result.roi * 100:,.1f}%" if result.roi is not None else "n/a")
    m3.metric(f"Profit per {unit}", format_gold(result.profit_per_unit))
    m4.metric(f"Max {unit} price (ROI {target_roi_pct:g}%)", format_gold(result.max_purchase_price))

    n1, n2, n3, n4 = st.columns(4)
    n1.metric("Cost", format_gold(result.cost))
    n2.metric("Gross value", format_gold(result.gross))
    n3.metric("AH fees", format_gold(result.fees))
    n4.metric("Net revenue", format_gold(result.net_revenue))
    if per_op:
        o1, o2, o3, _ = st.columns(4)
        o1.metric(f"Profit per {spec.operation}", format_gold(result.profit_for(per_op)))
        o2.metric(f"Net value per {spec.operation}", format_gold(result.net_revenue_for(per_op)))
        o3.metric(f"{spec.operation.title()}s in this batch", f"{quantity / per_op:,.1f}")
    if result.extra_cost_per_unit:
        st.caption(f"Cost includes {format_gold(result.extra_cost_per_unit)} of other costs per {unit}.")

    if result.profit_per_unit_low is not None:
        st.info(f"Rough profit per {unit} range using each output's own 95% yield bounds: "
                f"{format_gold(result.profit_per_unit_low)} to {format_gold(result.profit_per_unit_high)}. "
                "This is wider than a true joint interval and only reflects yield uncertainty, not price changes.")
    elif mode == MODE_CALIBRATED and summary.n_experiments == 1:
        st.info("Yields come from a single batch, so no uncertainty range can be shown.")

    st.markdown("**Profit at different scales** (linear - assumes prices do not move as you sell)")
    scale = pd.DataFrame([{
        spec.units.title(): n,
        "Cost (g)": copper_to_gold(result.cost_for(n)),
        "Net revenue (g)": copper_to_gold(result.net_revenue_for(n)),
        "Profit (g)": copper_to_gold(result.profit_for(n)),
    } for n in dict.fromkeys((1, 100, 1000, quantity))])
    st.dataframe(scale, hide_index=True, column_config={
        spec.units.title(): st.column_config.NumberColumn(format="%d"),
        "Cost (g)": GOLD_FMT, "Net revenue (g)": GOLD_FMT, "Profit (g)": GOLD_FMT})

    st.markdown("**Output breakdown**")
    keep = (1 - result.ah_cut) * result.sell_through
    st.dataframe(pd.DataFrame([{
        "Item": names.get(line.item_id), "Expected units": line.units,
        "Price (g)": copper_to_gold(line.price_copper),
        "Gross (g)": copper_to_gold(line.gross_copper),
        "Net after fees & sell-through (g)": copper_to_gold(None if line.gross_copper is None else line.gross_copper * keep),
    } for line in result.lines]), hide_index=True, column_config={
        "Expected units": st.column_config.NumberColumn(format="%.1f"),
        "Price (g)": GOLD_FMT, "Gross (g)": GOLD_FMT, "Net after fees & sell-through (g)": GOLD_FMT})

    # ---- liquidity ------------------------------------------------------------------------------
    sold = {int(r["Item ID"]): r["Sold / day (TSM)"] for _, r in edited.iterrows()}
    liquidity, slow = [], []
    for line in result.lines:
        units_to_sell = line.units * result.sell_through
        days = conversion.days_of_volume(units_to_sell, sold.get(line.item_id))
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
    if result.net_per_unit is not None:
        ch1, ch2 = st.columns(2)
        with ch1:
            st.markdown(f"**Net value per {unit} by output (g)**")
            st.bar_chart(pd.DataFrame({
                "Item": [names.get(l.item_id) for l in result.lines],
                f"Net value per {unit} (g)": [copper_to_gold(l.yield_per_unit * (l.price_copper or 0) * keep)
                                              for l in result.lines],
            }).set_index("Item"))
        with ch2:
            st.markdown(f"**Profit per {unit} vs. {unit} price (g)**")
            top = max(copper_to_gold(result.net_per_unit) * 1.5, 1.0)
            extra = copper_to_gold(result.extra_cost_per_unit) or 0.0
            xs = [top * i / 40 for i in range(41)]
            st.line_chart(pd.DataFrame({
                f"{spec.unit_title} price (g)": xs,
                f"Profit per {unit} (g)": [copper_to_gold(result.net_per_unit) - x - extra for x in xs],
            }).set_index(f"{spec.unit_title} price (g)"))

    st.caption(
        f"Assumptions: AH commission {result.ah_cut * 100:g}% of each sale (per-auction rounding ignored); "
        f"{result.sell_through * 100:g}% of output sells, the rest is worth 0; deposits ignored; "
        "revenue scales linearly with quantity.")


# --- Experiments --------------------------------------------------------------------------------

def render_experiments(spec: ProcessSpec, table: pd.DataFrame) -> None:
    st.caption("Record each batch separately. One big batch gives a number but no uncertainty - "
               "several smaller batches show how much the yields actually vary.")
    if spec.quickstart:
        spec.quickstart(table)
    _record_experiment(spec, table)
    _experiment_overview(spec)


def _record_experiment(spec: ProcessSpec, table: pd.DataFrame) -> None:
    p = f"{spec.prefix}_exp"
    with st.expander("Record a new experiment"):
        chosen = item_picker(table, f"{p}_input", spec.unit_title)
        c1, c2, c3 = st.columns(3)
        used = int(c1.number_input(f"{spec.units.title()} {spec.verb_past}", min_value=1, value=20, step=1,
                                   key=f"{p}_used"))
        when = c2.date_input("Date", value=date.today(), key=f"{p}_date")
        cost_each = c3.number_input(f"{spec.unit_title} cost each (g, optional)", min_value=0.0, value=None,
                                    step=0.01, format="%.4f", placeholder="optional", key=f"{p}_cost")

        outputs = st.session_state.setdefault(f"{p}_outputs", [])
        if chosen is not None:
            with db() as conn:
                previous = database.list_experiments(conn, spec.process, chosen[0])
            known = {o["item_id"]: o["item_name"] for e in previous for o in e["outputs"]}
            if known and st.button("Use output items from earlier experiments", key=f"{p}_prefill"):
                for item_id, name in known.items():
                    if item_id not in [o["item_id"] for o in outputs]:
                        outputs.append({"item_id": item_id, "name": name})
                st.rerun()

        st.markdown("**Items received**")
        picked = item_picker(table, f"{p}_out", "Output item")
        if picked and st.button("Add output item", key=f"{p}_add_out"):
            if picked[0] not in [o["item_id"] for o in outputs]:
                outputs.append({"item_id": picked[0], "name": picked[1]})
            st.rerun()

        quantities = {}
        for out in list(outputs):
            q1, q2 = st.columns([4, 1])
            quantities[out["item_id"]] = int(q1.number_input(
                f"{out['name'] or 'Item'} ({out['item_id']}) - quantity received", min_value=0, value=0,
                step=1, key=f"{p}_qty_{out['item_id']}"))
            if q2.button("Remove", key=f"{p}_rm_{out['item_id']}"):
                outputs.remove(out)
                st.rerun()
        notes = st.text_input("Notes", key=f"{p}_notes")

        if st.button("Save experiment", type="primary", key=f"{p}_save"):
            if chosen is None:
                st.error(f"Choose the {spec.unit} first.")
            elif not outputs:
                st.error("Add at least one output item.")
            else:
                try:
                    with db() as conn:
                        database.add_experiment(
                            conn, spec.process, chosen[0], used,
                            [{"item_id": o["item_id"], "name": o["name"], "quantity": quantities[o["item_id"]]}
                             for o in outputs],
                            input_name=chosen[1], performed_at=str(when), notes=notes,
                            input_cost_each_copper=gold_to_copper(cost_each) if cost_each is not None else None)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state[f"{p}_outputs"] = []
                    st.toast("Experiment saved")
                    st.rerun()


def _experiment_overview(spec: ProcessSpec) -> None:
    p = f"{spec.prefix}_exp"
    with db() as conn:
        experiments = database.list_experiments(conn, spec.process)
        inputs = database.list_experiment_inputs(conn, spec.process)
    st.subheader("Recorded experiments")
    if not experiments:
        st.info("No experiments yet.")
        return

    st.dataframe(pd.DataFrame([{
        "ID": e["id"], spec.unit_title: e["herb_name"] or e["herb_item_id"],
        f"{spec.units.title()} {spec.verb_past}": e["herbs_used"],
        "Received": ", ".join(f"{o['quantity']}x {o['item_name'] or o['item_id']}" for o in e["outputs"]),
        "Date": e["performed_at"],
        f"{spec.unit_title} cost each (g)": copper_to_gold(e["herb_cost_each_copper"]),
        "Observed prices (g)": ", ".join(f"{k}: {copper_to_gold(v):g}" for k, v in e["observed_prices"].items()),
        "Notes": e["notes"],
    } for e in experiments]), hide_index=True, column_config={
        f"{spec.unit_title} cost each (g)": st.column_config.NumberColumn(format="%.2f")})
    d1, d2 = st.columns([2, 1])
    to_delete = d1.selectbox("Delete an experiment", [e["id"] for e in experiments], key=f"{p}_delete_id")
    if d2.button("Delete", key=f"{p}_delete_btn"):
        with db() as conn:
            database.delete_experiment(conn, to_delete)
        st.rerun()

    st.subheader("Calibrated yields")
    labels = {f"{r.herb_name or r.herb_item_id} ({r.herb_item_id})": int(r.herb_item_id)
              for r in inputs.itertuples()}
    input_id = labels[st.selectbox(spec.unit_title, list(labels), key=f"{p}_summary_input")]
    summary = _summary(spec, input_id, experiments)
    st.caption(f"{summary.n_experiments} experiment(s), {summary.total_units} {spec.units} {spec.verb_past}, "
               f"{sum(e.total_output for e in summary.estimates)} items received in total. "
               f"Reliability: **{reliability_badge(summary.reliability)}**.")
    st.dataframe(pd.DataFrame([{
        "Item": e.name or e.item_id, "Total received": e.total_output,
        f"Mean per {spec.unit}": e.mean_per_unit, "Std. error": e.std_error,
        "95% low": e.ci_low, "95% high": e.ci_high} for e in summary.estimates]),
        hide_index=True, column_config={
            c: st.column_config.NumberColumn(format="%.3f")
            for c in (f"Mean per {spec.unit}", "Std. error", "95% low", "95% high")})
    for warning in summary.warnings:
        st.warning(warning)
    st.caption("Uncertainty treats each recorded experiment as one observation, so it needs at least two. "
               f"Reliability labels use rule-of-thumb thresholds (5+ experiments and 200+ {spec.units} for "
               "'moderate'), not a statistical guarantee.")


# --- Compare inputs -------------------------------------------------------------------------------

def render_compare(spec: ProcessSpec, table: pd.DataFrame) -> None:
    p = f"{spec.prefix}_cmp"
    with db() as conn:
        experiments = database.list_experiments(conn, spec.process)
    inputs = _known_inputs(spec)
    if inputs.empty:
        st.info(f"Record experiments (or reference yields) for at least one {spec.unit} to compare them here. "
                f"Each {spec.unit} is identified by its item ID from your data - never by a similar-looking name.")
        return
    st.caption(f"Every {spec.unit} with experiments, at current prices, using its calibrated yields. One with "
               "only reference yields uses those and says so in the Reliability column.")
    c1, c2, c3 = st.columns(3)
    ah_cut = c1.number_input("AH cut (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5, key=f"{p}_ahcut")
    sell = c2.number_input("Expected sell-through (%)", 0.0, 100.0, value=saved(f"{p}_sell", 100.0), step=5.0,
                           key=f"{p}_sell")
    quantity = int(c3.number_input(f"{spec.units.title()} per comparison", min_value=1,
                                   value=saved(f"{p}_qty", 100), step=10,
                                   key=f"{p}_qty"))
    index = pricing.PriceIndex(table)
    rows = []
    for r in inputs.itertuples():
        summary = _summary(spec, int(r.herb_item_id), experiments)
        if not summary.n_experiments:
            summary = _reference(spec, int(r.herb_item_id))
        opp = scanner.from_conversion(spec.process, int(r.herb_item_id), r.herb_name, summary, index.get,
                                      index.demand, operations=quantity, ah_cut=ah_cut / 100,
                                      sell_through=sell / 100)
        if opp is None:
            continue
        rows.append({
            spec.unit_title: opp.name, "Item ID": int(r.herb_item_id),
            f"Profit per {spec.unit} (g)": copper_to_gold(opp.profit_per_op),
            f"Profit for {quantity} (g)": copper_to_gold(None if opp.profit_per_op is None
                                                         else opp.profit_per_op * quantity),
            "ROI %": None if opp.roi is None else opp.roi * 100,
            f"Cost per {spec.unit} (g)": copper_to_gold(opp.cost_per_op),
            "Days of daily volume": opp.liquidity_days,
            "Experiments": summary.n_experiments, "Reliability": reliability_badge(summary.reliability),
            "Flags": "; ".join(f for f in opp.flags if not f.startswith("operation size")),
        })
    if not rows:
        st.info(f"No {spec.unit} has experiments or reference yields yet - manual yields are used in the "
                "Calculator only.")
        return
    frame = pd.DataFrame(rows).sort_values(f"Profit per {spec.unit} (g)", ascending=False, na_position="last")
    st.dataframe(frame, hide_index=True, column_config={
        "Item ID": st.column_config.NumberColumn(format="%d"),
        f"Profit per {spec.unit} (g)": GOLD_FMT, f"Profit for {quantity} (g)": GOLD_FMT,
        f"Cost per {spec.unit} (g)": GOLD_FMT, "ROI %": st.column_config.NumberColumn(format="%.1f"),
        "Days of daily volume": st.column_config.NumberColumn(format="%.2f")})
    priced = frame[frame[f"Profit per {spec.unit} (g)"].notna()]
    if not priced.empty:
        st.bar_chart(priced.set_index(spec.unit_title)[[f"Profit per {spec.unit} (g)"]])
    st.caption("No 'other cost per unit' is included here; use the Calculator for that. "
               "Margins are theoretical at listed prices, not demonstrated sales.")


# --- Reference yields -----------------------------------------------------------------------------

def render_reference(spec: ProcessSpec, table: pd.DataFrame) -> None:
    """Type in outside drop data (e.g. Wowhead). Stored apart from experiments and always labelled."""
    p = f"{spec.prefix}_ref"
    st.caption(f"Use this when you have no experiments yet: copy the numbers a site such as Wowhead shows for "
               f"a {spec.unit} - for each output, the **chance** it appears per {spec.operation} and the "
               f"**average quantity** when it does. These are community estimates, not your data: they are "
               "never mixed into your experiments, and every result that uses them is labelled.")
    chosen = item_picker(table, f"{p}_input", spec.unit_title)
    if chosen is None:
        _reference_overview(spec)
        return
    input_id, input_name = chosen
    with db() as conn:
        existing = database.list_reference_yields(conn, spec.process, input_id)
    c1, c2 = st.columns([1, 2])
    per_op = c1.number_input(f"{spec.units.title()} per {spec.operation}", min_value=1, step=1,
                             value=existing[0]["units_per_operation"] if existing else None,
                             placeholder="check in game", key=f"{p}_perop_{input_id}",
                             help=f"Drop chances on such sites are per {spec.operation}. This converts them "
                                  f"to a yield per {spec.unit}.")
    source = c2.text_input("Source", value=existing[0]["source"] if existing else "",
                           placeholder="e.g. Wowhead item page, 2026-10-13", key=f"{p}_src_{input_id}")
    frame = pd.DataFrame([{"Output item ID": r["output_item_id"], "Output name": r["output_name"],
                           "Chance per operation (%)": r["chance_pct"], "Average quantity": r["avg_quantity"]}
                          for r in existing] or [{"Output item ID": None, "Output name": "",
                                                  "Chance per operation (%)": None, "Average quantity": None}])
    st.markdown("**Outputs** - add a row per item; item IDs must match your price data")
    edited = st.data_editor(frame, num_rows="dynamic", hide_index=True, key=f"{p}_editor_{input_id}",
                            column_config={
                                "Output item ID": st.column_config.NumberColumn(min_value=1, step=1, format="%d"),
                                "Chance per operation (%)": st.column_config.NumberColumn(min_value=0.0,
                                                                                          max_value=100.0),
                                "Average quantity": st.column_config.NumberColumn(min_value=0.0)})
    rows = [{"item_id": int(r["Output item ID"]),
             "name": (str(r["Output name"]).strip() or None) if pd.notna(r["Output name"]) else None,
             "chance_pct": float(r["Chance per operation (%)"]), "avg_quantity": float(r["Average quantity"]),
             "units_per_operation": per_op}
            for _, r in edited.iterrows()
            if pd.notna(r["Output item ID"]) and pd.notna(r["Chance per operation (%)"])
            and pd.notna(r["Average quantity"])]
    if per_op and rows:
        st.dataframe(pd.DataFrame([{
            "Output": r["name"] or r["item_id"],
            f"Yield per {spec.unit}": conversion.yield_from_drop(r["chance_pct"], r["avg_quantity"], per_op)}
            for r in rows]), hide_index=True,
            column_config={f"Yield per {spec.unit}": st.column_config.NumberColumn(format="%.4f")})
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save reference yields", type="primary", key=f"{p}_save_{input_id}"):
        if not per_op:
            st.error(f"Enter how many {spec.units} one {spec.operation} uses.")
        elif not rows:
            st.error("Add at least one output with a chance and an average quantity.")
        else:
            try:
                with db() as conn:
                    database.save_reference_yields(conn, spec.process, input_id, input_name, rows, source)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.toast("Reference yields saved")
                st.rerun()
    if existing and b2.button("Delete these reference yields", key=f"{p}_del_{input_id}"):
        with db() as conn:
            database.delete_reference_yields(conn, spec.process, input_id)
        st.rerun()
    _reference_overview(spec)


def _reference_overview(spec: ProcessSpec) -> None:
    with db() as conn:
        refs = database.list_reference_inputs(conn, spec.process)
    if refs.empty:
        return
    st.subheader("Saved reference yields")
    st.dataframe(refs.rename(columns={"input_item_id": "Item ID", "input_name": spec.unit_title,
                                      "outputs": "Outputs", "source": "Source", "recorded_at": "Saved (UTC)"}),
                 hide_index=True, column_config={"Item ID": st.column_config.NumberColumn(format="%d")})
