"""Small Streamlit widgets shared by several tabs."""

from __future__ import annotations

from contextlib import closing
from datetime import date, datetime
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


# --- remembering inputs across sessions ----------------------------------------------------------------
# Wrap a widget's starting value in ``saved(key, default)``; ``persist_ui_state()`` (end of every app run)
# writes the current values of those widgets to the database, so the app reopens as it was left.

def _store() -> dict:
    if "_ui_saved" not in st.session_state:
        with db() as conn:
            st.session_state["_ui_saved"] = database.load_ui_state(conn)
        st.session_state["_ui_keys"] = set()
    return st.session_state["_ui_saved"]


def saved(key: str, default=None):
    """The value this widget had last time (any earlier session), else ``default``."""
    store = _store()
    st.session_state["_ui_keys"].add(key)
    return store.get(key, default)


def saved_index(key: str, options: list, default: int = 0) -> int:
    """Index for a selectbox / radio: last choice if it is still an option."""
    value = saved(key)
    return options.index(value) if value in options else min(default, max(len(options) - 1, 0))


def saved_choices(key: str, options: list, default: Optional[list] = None) -> list:
    """Default list for a multiselect: last choices that are still options."""
    return [v for v in saved(key, default or []) if v in options]


def _jsonable(value):
    if isinstance(value, (date, datetime)):
        return None
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value if isinstance(value, (str, int, float, bool)) or value is None else None


def persist_ui_state() -> None:
    """Save remembered widget values that changed during this run."""
    store = _store()
    changed = False
    for key in st.session_state.get("_ui_keys", set()):
        if key in st.session_state:
            value = _jsonable(st.session_state[key])
            if store.get(key, object()) != value:
                store[key] = value
                changed = True
    if changed:
        with db() as conn:
            database.save_ui_state(conn, store)
