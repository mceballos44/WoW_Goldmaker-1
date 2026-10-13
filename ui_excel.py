"""Import / Export tab: Excel workbook export, template download and import."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from goldmaker import excel_io, pricing
from ui_common import db, default_ah_cut_pct

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def render(table: pd.DataFrame, context: dict) -> None:
    st.header("Import / Export (Excel)")
    st.subheader("Export")
    st.caption("One workbook with an Info sheet (time, region/realm, price basis, AH cut, TSM data times, "
               "assumptions), the price snapshot, your manual prices, recipes and material breakdowns, crafting "
               "profitability, experiments and calibrated yields, and the latest Opportunities scan. Profit "
               "columns are formulas; missing prices stay blank (never 0).")
    c1, c2 = st.columns(2)
    ah_cut = c1.number_input("AH cut for the crafting sheet (%)", 0.0, 99.0, value=default_ah_cut_pct(), step=0.5,
                             key="xl_ahcut")
    sell = c2.number_input("Sell-through for the crafting sheet (%)", 0.0, 100.0, value=100.0, step=5.0,
                           key="xl_sell")
    if st.button("Build export", type="primary", key="xl_build"):
        with db() as conn:
            data = excel_io.collect(conn, table, {**context, "AH cut %": ah_cut, "Sell-through %": sell},
                                    ah_cut / 100, sell / 100, st.session_state.get("scan_last"))
        st.session_state["xl_bytes"] = excel_io.build_workbook(data)
    if st.session_state.get("xl_bytes"):
        st.download_button("Download goldmaker_export.xlsx", st.session_state["xl_bytes"], "goldmaker_export.xlsx",
                           XLSX, key="xl_dl")
    st.caption("Open the Opportunities tab first if you want its ranking included.")

    st.divider()
    st.subheader("Import")
    st.caption("Reads the sheets **Recipes**, **Prices** (manual prices in gold) and **Experiments** - the same "
               "layout the export writes, so you can edit an export and read it back. Recipes with an existing "
               "key are replaced; prices become manual overrides; experiments already stored are skipped.")
    st.download_button("Download an empty import template", excel_io.template_workbook(),
                       "goldmaker_import_template.xlsx", XLSX, key="xl_template")
    upload = st.file_uploader("Workbook (.xlsx)", type=["xlsx"], key="xl_upload")
    if upload is None:
        return
    index = pricing.PriceIndex(table)
    parsed = excel_io.read_workbook(upload.getvalue(), index.lookup_name)
    st.write({"Sheets found": ", ".join(parsed.sheets_found) or "none",
              "Recipes": len(parsed.recipes.recipes) if parsed.recipes else 0,
              "Manual prices": len(parsed.prices), "Experiments": len(parsed.experiments)})
    problems = list(parsed.errors) + (list(parsed.recipes.errors) if parsed.recipes else [])
    for problem in problems:
        st.error(problem)
    for warning in (parsed.recipes.warnings if parsed.recipes else []):
        st.warning(warning)
    if problems:
        st.caption("Rows with problems are skipped; everything else can still be imported.")
    if parsed.empty:
        return
    if st.button("Import into the app", type="primary", key="xl_apply"):
        with db() as conn:
            counts = excel_io.apply_import(conn, parsed)
        st.success(f"Imported {counts['recipes']} recipe(s), {counts['prices']} manual price(s), "
                   f"{counts['experiments']} experiment(s)"
                   + (f"; skipped {counts['experiments_skipped']} already stored." if counts["experiments_skipped"]
                      else "."))
