"""Prospecting tab: ore -> gems, crystal glass and other outputs (see ``ui_processing``).

No gem distributions are built in: yields come from your recorded prospecting batches.
Ores are compared by the item IDs in your data, never by similar-looking names.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

import ui_processing

SPEC = ui_processing.ProcessSpec(
    process="prospecting", title="Prospecting", unit="ore", units="ore", verb="prospect",
    verb_past="prospected", prefix="pros", output_example="a gem or crystal glass", operation="prospect",
    extra_cost_help="Anything else each ore costs you to prospect (for example a reagent, if your "
                    "prospecting needs one), in gold per ore. Leave 0 if there is none.")


def render(table: pd.DataFrame) -> None:
    ui_processing.render(SPEC, table)
    st.caption(
        "Quality tiers of gems are separate items with their own item IDs - record each one as its own "
        "output. Profession talents that change prospecting yields are not modelled; they show up in "
        "your experiments if you record batches with the talents you have.")
