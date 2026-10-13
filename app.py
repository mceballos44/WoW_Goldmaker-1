"""WoW Goldmaker - Streamlit entry point.

Run with:  streamlit run app.py        (or double-click run_app.bat on Windows)

Stage 1: TSM price download + local cache, searchable price table, manual price overrides.
Character profile import from CharExporter. Stage 2: milling. Stage 3: recipes, crafting/housing.
Stage 4: prospecting, Jewelcrafting / Alchemy with profiles and quality tiers.
Stage 5: opportunity scanner, AH investment scenarios, Excel import/export.
"""

from __future__ import annotations

import time
from contextlib import closing
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from goldmaker import config, database, pricing, tsm_client
import ui_crafting
import ui_excel
import ui_investment
import ui_milling
import ui_profession
import ui_profiles
import ui_prospecting
import ui_scanner
from goldmaker.currency import format_gold, gold_to_copper

st.set_page_config(page_title="WoW Goldmaker", page_icon="💰", layout="wide")

TAB_NAMES = [
    "Dashboard", "Opportunities", "Milling", "Prospecting", "Jewelcrafting", "Alchemy",
    "Housing / Crafting", "AH Investment", "Price Database", "Character Profiles",
    "Import / Export", "Settings",
]
SCOPE_LABELS = {
    "commodities": "Commodities (region-wide)",
    "realm": "Realm items (non-commodity)",
    "region": "Region items (regional aggregates)",
}
MANUAL_ITEM = "— enter item ID manually —"


# --- Small helpers -----------------------------------------------------------------

def db():
    """Open a short-lived DB connection (always use with ``closing``)."""
    return closing(database.connect())


def describe_age(ts) -> str:
    if ts is None or pd.isna(ts):
        return "unknown time"
    age = datetime.now(timezone.utc) - pd.Timestamp(ts).to_pydatetime()
    hours = age.total_seconds() / 3600
    if hours < 1:
        return f"{int(age.total_seconds() // 60)} min ago"
    if hours < 48:
        return f"{hours:.1f} h ago"
    return f"{hours / 24:.1f} days ago"


def init_state() -> None:
    """Load saved sidebar choices once per browser session."""
    if st.session_state.get("_initialised"):
        return
    with db() as conn:
        st.session_state["region"] = database.get_setting(conn, "region", config.DEFAULT_REGION)
        st.session_state["realm_slug"] = database.get_setting(conn, "realm_slug", config.DEFAULT_REALM_SLUG)
        st.session_state["basis"] = database.get_setting(conn, "basis", config.DEFAULT_PRICE_BASIS)
    st.session_state["_initialised"] = True


def persist_choices(region: str, realm_slug: str, basis: str) -> None:
    with db() as conn:
        for key, value in (("region", region), ("realm_slug", realm_slug), ("basis", basis)):
            if database.get_setting(conn, key) != value:
                database.set_setting(conn, key, value)


@st.cache_data(show_spinner=False)
def load_price_table(version: str, region: str, realm_slug: str, basis: str,
                     time_bucket: int) -> pd.DataFrame:
    """Build the combined price table. Cached until data/overrides change.

    ``version`` and ``time_bucket`` exist only to invalidate the cache
    (``time_bucket`` refreshes the stale/age columns every 5 minutes).
    """
    with db() as conn:
        prices = database.get_prices(conn, region, realm_slug)
        overrides = database.list_overrides(conn)
    return pricing.build_price_table(prices, overrides, basis)


def dataset_rows(region: str, realm_slug: str) -> dict:
    """Metadata for the three relevant datasets, keyed by scope (None if absent)."""
    with db() as conn:
        datasets = database.list_datasets(conn)
    found = {}
    for scope in config.SCOPES:
        key = tsm_client.dataset_key(scope, region, realm_slug)
        match = datasets[datasets["dataset_key"] == key]
        found[scope] = match.iloc[0] if not match.empty else None
    return found


# --- Sidebar ---------------------------------------------------------------------------

def render_sidebar() -> tuple[str, str, str]:
    st.sidebar.title("💰 WoW Goldmaker")
    st.sidebar.caption("Retail • local app • prices from TSM public CSV data")

    region = st.sidebar.selectbox("Region", config.REGIONS, key="region",
                                  format_func=str.upper)
    realm_slug = st.sidebar.text_input(
        "Realm slug", key="realm_slug",
        help="Use the slug from the realm's address on tradeskillmaster.com, "
             "e.g. 'area-52' for https://tradeskillmaster.com/retail/us/area-52",
    ).strip().lower()
    with st.sidebar.expander("Need help finding the slug?"):
        name = st.text_input("Realm name", placeholder="Area 52", key="realm_name_helper")
        if name:
            st.code(tsm_client.slugify_realm(name))
            st.caption("Only a suggestion. Realms with apostrophes or accents may differ - "
                       "copy the slug from TSM's address bar if the download fails.")

    basis = st.sidebar.selectbox(
        "Price basis", list(config.PRICE_BASIS_LABELS), key="basis",
        format_func=config.PRICE_BASIS_LABELS.get,
        help="Which TSM field is used as the item's price in calculations.",
    )

    st.sidebar.markdown("**Datasets to refresh**")
    chosen = [scope for scope in config.SCOPES
              if st.sidebar.checkbox(SCOPE_LABELS[scope], value=scope != "region",
                                     key=f"fetch_{scope}")]

    if st.sidebar.button("🔄 Refresh Prices", type="primary"):
        if "realm" in chosen and not tsm_client.is_valid_slug(realm_slug):
            st.sidebar.error("Enter a valid realm slug first.")
        else:
            with st.spinner("Downloading from TradeSkillMaster..."):
                with db() as conn:
                    st.session_state["refresh_results"] = [
                        tsm_client.fetch_dataset(conn, scope, region, realm_slug)
                        for scope in chosen
                    ]

    for result in st.session_state.get("refresh_results", []):
        label = SCOPE_LABELS[result.scope]
        if result.ok:
            st.sidebar.success(f"{label}: {result.rows:,} items")
            if result.columns_missing:
                st.sidebar.caption("Not in this file: " + ", ".join(result.columns_missing))
        elif result.cached_data_available:
            st.sidebar.warning(f"{label}: download failed - using previously cached data.\n\n{result.error}")
        else:
            st.sidebar.error(f"{label}: download failed and nothing is cached.\n\n{result.error}")

    st.sidebar.divider()
    st.sidebar.markdown("**Data status**")
    for scope, row in dataset_rows(region, realm_slug).items():
        if row is None:
            st.sidebar.caption(f"• {scope}: not downloaded")
        else:
            updated = pd.to_datetime(row["source_updated_at"], utc=True, errors="coerce")
            st.sidebar.caption(
                f"• {scope}: TSM data from "
                f"{updated:%Y-%m-%d %H:%M} UTC ({describe_age(updated)})"
                if pd.notna(updated) else f"• {scope}: TSM timestamp unavailable"
            )
    st.sidebar.caption("TSM publishes periodic snapshots. These prices are **not real-time**.")

    persist_choices(region, realm_slug, basis)
    return region, realm_slug, basis


# --- Tabs ------------------------------------------------------------------------------

def render_dashboard(region: str, realm_slug: str, table: pd.DataFrame) -> None:
    st.header("Dashboard")
    st.info("All five stages are installed: prices and overrides, character import, milling, recipes and "
            "crafting/housing, prospecting, Jewelcrafting / Alchemy with profiles, the opportunity scanner, "
            "AH investment scenarios and Excel import/export. See *Opportunities* for a ranked overview.")
    rows = dataset_rows(region, realm_slug)
    cols = st.columns(4)
    cols[0].metric("Items with a price", f"{int(table['effective_copper'].notna().sum()):,}" if not table.empty else "0")
    cols[1].metric("Manual overrides", f"{int(table['override_copper'].notna().sum()):,}" if not table.empty else "0")
    cols[2].metric("Region / realm", f"{region.upper()} / {realm_slug}")
    cols[3].metric("Datasets cached", f"{sum(r is not None for r in rows.values())} / {len(rows)}")

    st.subheader("Data freshness")
    status = []
    for scope, row in rows.items():
        if row is None:
            status.append({"Dataset": scope, "TSM data time (UTC)": None, "Age": "-", "Rows": None,
                           "Status": "Not downloaded"})
            continue
        updated = pd.to_datetime(row["source_updated_at"], utc=True, errors="coerce")
        age_h = (datetime.now(timezone.utc) - updated.to_pydatetime()).total_seconds() / 3600 if pd.notna(updated) else None
        stale = age_h is None or age_h > config.STALE_AFTER_HOURS[scope]
        status.append({"Dataset": scope, "TSM data time (UTC)": updated if pd.notna(updated) else None,
                       "Age": describe_age(updated), "Rows": int(row["row_count"]),
                       "Status": "⚠️ Possibly stale" if stale else "OK"})
    st.dataframe(pd.DataFrame(status), hide_index=True)
    if any(s["Status"].startswith("⚠️") for s in status):
        st.warning("Some data is older than this app's warning threshold "
                   f"({config.STALE_AFTER_HOURS}). Press Refresh Prices, and remember TSM itself "
                   "only publishes periodic snapshots.")
    if table.empty:
        st.warning("No price data is cached yet. Click **Refresh Prices** in the sidebar.")


def export_context(region: str, realm_slug: str, basis: str) -> dict:
    """What an Excel export or report should state about the data it was built from."""
    context = {"Region": region.upper(), "Realm slug": realm_slug,
               "Price basis": config.PRICE_BASIS_LABELS.get(basis, basis)}
    for scope, row in dataset_rows(region, realm_slug).items():
        context[f"TSM {scope} data time (UTC)"] = (row["source_updated_at"] if row is not None
                                                   else "not downloaded")
    return context


def render_price_database(table: pd.DataFrame, region: str, realm_slug: str) -> None:
    st.header("Price Database")
    st.caption("Prices are shown in gold (stored internally in copper). "
               "TSM files are snapshots; check the data time column - they are not real-time.")

    if table.empty:
        st.warning("No prices cached yet. Click **Refresh Prices** in the sidebar, "
                   "or import a CSV below if TSM is unreachable.")
    else:
        stale_share = table.loc[table["effective_source"] != "override", "tsm_stale"].mean()
        if stale_share and stale_share > 0:
            st.warning("Some TSM data is older than the warning threshold or has no timestamp.")

        f1, f2, f3, f4 = st.columns([3, 2, 1, 1])
        query = f1.text_input("Search (name or item ID)", key="pdb_query")
        sources = f2.multiselect("Price source", sorted(table["effective_source"].unique()),
                                 key="pdb_sources")
        only_priced = f3.checkbox("Priced only", value=True, key="pdb_priced")
        only_override = f4.checkbox("Overrides only", key="pdb_overrides")

        view = pricing.search_table(table, query)
        if sources:
            view = view[view["effective_source"].isin(sources)]
        if only_priced:
            view = view[view["effective_copper"].notna()]
        if only_override:
            view = view[view["override_copper"].notna()]

        st.caption(f"{len(view):,} of {len(table):,} items shown")
        gold = st.column_config.NumberColumn(format="%.2f")
        st.dataframe(
            pricing.to_display_frame(view),
            hide_index=True,
            height=480,
            column_config={
                "Item ID": st.column_config.NumberColumn(format="%d"),
                "Effective price (g)": gold, "TSM price (g)": gold, "Override (g)": gold,
                "Market value (g)": gold, "Min buyout (g)": gold, "Recent (g)": gold,
                "Historical (g)": gold, "Avg sale price (g)": gold,
                "Sale rate": st.column_config.NumberColumn(format="%.3f"),
                "Sold / day": st.column_config.NumberColumn(format="%.1f"),
                "TSM data time (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
                "Age (h)": st.column_config.NumberColumn(format="%.1f"),
            },
        )

    st.divider()
    render_overrides(table)
    st.divider()
    render_manual_import(region, realm_slug)
    render_dataset_details()


def render_overrides(table: pd.DataFrame) -> None:
    st.subheader("Manual price overrides")
    st.caption("Overrides are stored separately and never change the downloaded TSM data. "
               "They apply to this item on every realm, and win over TSM prices in calculations. "
               "Use them for nonauctionable items or your own known prices.")

    query = st.text_input("Find an item (name or ID)", key="ov_query")
    matches = pricing.search_table(table, query).head(200) if query.strip() and not table.empty else table.iloc[0:0]
    labels = {f"{n if isinstance(n, str) else 'Unnamed'} ({i})": (int(i), n if isinstance(n, str) else None)
              for i, n in zip(matches["item_id"], matches["name"])}
    choice = st.selectbox("Item", [MANUAL_ITEM] + list(labels), key="ov_choice")

    if choice == MANUAL_ITEM:
        c1, c2 = st.columns([1, 2])
        item_id = int(c1.number_input("Item ID", min_value=1, value=1, step=1, key="ov_manual_id"))
        item_name = c2.text_input("Item name (optional, for your reference)", key="ov_manual_name") or None
    else:
        item_id, item_name = labels[choice]

    current = pricing.resolve_price(table, item_id) if not table.empty else None
    if current is not None and not current.is_missing:
        st.caption(f"Current effective price: **{format_gold(current.copper)}** (source: {current.source})")
    elif current is not None:
        st.caption("This item currently has **no price** in the loaded data.")

    c1, c2 = st.columns([1, 2])
    default_gold = (current.copper / config.COPPER_PER_GOLD) if current and not current.is_missing else 0.0
    price_gold = c1.number_input("Override price (gold)", min_value=0.0, value=float(default_gold),
                                 step=0.01, format="%.4f", key=f"ov_price_{item_id}")
    note = c2.text_input("Note (why / where this price comes from)", key=f"ov_note_{item_id}")

    b1, _ = st.columns([1, 3])
    if b1.button("Save override", type="primary"):
        with db() as conn:
            database.upsert_override(conn, item_id, gold_to_copper(price_gold), note, item_name)
        st.toast(f"Override saved for item {item_id}: {price_gold:,.4f}g")
        st.rerun()

    with db() as conn:
        overrides = database.list_overrides(conn)
    if overrides.empty:
        st.caption("No overrides yet.")
        return
    shown = overrides.assign(**{"Price (g)": overrides["price_copper"] / config.COPPER_PER_GOLD})
    st.dataframe(
        shown[["item_id", "name", "Price (g)", "note", "updated_at"]].rename(columns={
            "item_id": "Item ID", "name": "Name", "note": "Note", "updated_at": "Updated (UTC)"}),
        hide_index=True,
        column_config={"Item ID": st.column_config.NumberColumn(format="%d"),
                       "Price (g)": st.column_config.NumberColumn(format="%.4f")},
    )
    r1, r2 = st.columns([2, 1])
    to_remove = r1.selectbox(
        "Remove an override", overrides["item_id"].tolist(), key="ov_remove",
        format_func=lambda i: f"{overrides.loc[overrides.item_id == i, 'name'].iloc[0] or 'Item'} ({i})",
    )
    if r2.button("Remove override"):
        with db() as conn:
            database.delete_override(conn, int(to_remove))
        st.toast(f"Override removed for item {to_remove}")
        st.rerun()


def render_manual_import(region: str, realm_slug: str) -> None:
    with st.expander("Import a TSM CSV file manually (offline fallback)"):
        st.caption("If the download is blocked, save the CSV from your browser and import it here. "
                   "It replaces the cached dataset for the selected region/realm.")
        scope = st.selectbox("Dataset type", config.SCOPES, format_func=SCOPE_LABELS.get, key="imp_scope")
        upload = st.file_uploader("CSV file", type=["csv"], key="imp_file")
        if upload is not None and st.button("Import file"):
            try:
                with db() as conn:
                    result = tsm_client.ingest_csv_bytes(
                        conn, upload.getvalue(), scope, region,
                        realm_slug if scope == "realm" else None,
                        url=f"(manual import) {upload.name}", origin="manual import")
                st.success(f"Imported {result.rows:,} items into '{result.dataset_key}'.")
                st.rerun()
            except tsm_client.TSMError as exc:
                st.error(str(exc))


def render_dataset_details() -> None:
    with st.expander("Dataset details (files, columns found / missing)"):
        with db() as conn:
            datasets = database.list_datasets(conn)
        if datasets.empty:
            st.caption("Nothing cached yet.")
            return
        st.dataframe(datasets.drop(columns=["cache_file"]), hide_index=True)
        st.caption(f"Raw CSV copies are kept in: {config.CACHE_DIR}")


def render_settings() -> None:
    st.header("Settings")
    st.markdown(f"**Database:** `{config.DB_PATH}`  \n**Raw CSV cache:** `{config.CACHE_DIR}`")
    st.markdown("**Stale-data warning thresholds (hours)** - configured in `goldmaker/config.py`:")
    st.json(config.STALE_AFTER_HOURS)
    st.caption("These are this app's own warning thresholds, not guarantees from TSM.")

    st.markdown("**Auction House commission**")
    with closing(database.connect()) as conn:
        current = float(database.get_setting(conn, "ah_cut_pct", str(config.DEFAULT_AH_CUT_PCT)))
    cut = st.number_input("Default AH cut (%)", min_value=0.0, max_value=99.0, value=current, step=0.5,
                          key="settings_ah_cut",
                          help="Used as the starting value in calculators. Each calculator can override it.")
    if st.button("Save default AH cut"):
        with closing(database.connect()) as conn:
            database.set_setting(conn, "ah_cut_pct", str(cut))
        st.toast("Saved")
    st.caption("5% is the standard retail AH commission as I understand it - verify it in game.")


# --- Main --------------------------------------------------------------------------------

def main() -> None:
    init_state()
    region, realm_slug, basis = render_sidebar()

    with db() as conn:
        version = database.data_version(conn)
    table = load_price_table(version, region, realm_slug, basis, int(time.time() // 300))

    context = export_context(region, realm_slug, basis)
    tabs = dict(zip(TAB_NAMES, st.tabs(TAB_NAMES)))
    with tabs["Dashboard"]:
        render_dashboard(region, realm_slug, table)
    with tabs["Opportunities"]:
        ui_scanner.render(table)
    with tabs["Milling"]:
        ui_milling.render(table)
    with tabs["Prospecting"]:
        ui_prospecting.render(table)
    with tabs["Jewelcrafting"]:
        ui_profession.render(table, "Jewelcrafting")
    with tabs["Alchemy"]:
        ui_profession.render(table, "Alchemy")
    with tabs["Housing / Crafting"]:
        ui_crafting.render(table)
    with tabs["AH Investment"]:
        ui_investment.render(table, context)
    with tabs["Price Database"]:
        render_price_database(table, region, realm_slug)
    with tabs["Character Profiles"]:
        ui_profiles.render()
    with tabs["Import / Export"]:
        ui_excel.render(table, context)
    with tabs["Settings"]:
        render_settings()

main()
