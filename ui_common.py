"""Small Streamlit widgets shared by several tabs."""

from __future__ import annotations

from contextlib import closing
from typing import Optional

import pandas as pd
import streamlit as st

from goldmaker import config, database, pricing

NO_CHOICE = "- pick from search results -"


def db():
    """Short-lived database connection; always use as ``with db() as conn``."""
    return closing(database.connect())


def default_ah_cut_pct() -> float:
    """AH commission (percent) saved in Settings, else the configured default."""
    with db() as conn:
        raw = database.get_setting(conn, "ah_cut_pct", str(config.DEFAULT_AH_CUT_PCT))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return config.DEFAULT_AH_CUT_PCT


def item_picker(table: pd.DataFrame, key: str, label: str = "Item") -> Optional[tuple]:
    """Search the cached price table by name/ID and return ``(item_id, name)``.

    Falls back to typing an item ID by hand, so items that are not in any TSM
    file (or when nothing is cached yet) can still be used.
    """
    c1, c2 = st.columns([2, 2])
    query = c1.text_input(f"{label}: search name or ID", key=f"{key}_q")
    matches = (pricing.search_table(table, query).head(200)
               if query.strip() and not table.empty else table.iloc[0:0])
    options = {f"{n if isinstance(n, str) else 'Unnamed'} ({int(i)})": (int(i), n if isinstance(n, str) else None)
               for i, n in zip(matches["item_id"], matches["name"])}
    choice = c2.selectbox(f"{label}: matches", [NO_CHOICE] + list(options), key=f"{key}_pick")
    if choice != NO_CHOICE:
        return options[choice]

    with st.expander("Or enter an item ID by hand"):
        m1, m2 = st.columns([1, 2])
        manual_id = int(m1.number_input("Item ID", min_value=0, value=0, step=1, key=f"{key}_manual_id"))
        manual_name = m2.text_input("Name (optional)", key=f"{key}_manual_name")
        if manual_id > 0:
            return manual_id, (manual_name.strip() or None)
    return None


def reliability_badge(reliability: str) -> str:
    return {"none": "no data", "single batch": "single batch - no uncertainty",
            "low": "low", "moderate": "moderate",
            "reference": "reference data - not your experiments"}.get(reliability, reliability)
