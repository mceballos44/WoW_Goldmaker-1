"""Milling tab: profit calculator, experiment tracking and herb comparison (see ``ui_processing``)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from goldmaker import database, pricing
from goldmaker.currency import gold_to_copper
import ui_processing
from ui_common import db


def render(table: pd.DataFrame) -> None:
    ui_processing.render(SPEC, table)


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


SPEC = ui_processing.ProcessSpec(
    process="milling", title="Milling", unit="herb", units="herbs", verb="mill", verb_past="milled",
    prefix="mill", output_example="a pigment", operation="mill", quickstart=_felwort_quickstart)
