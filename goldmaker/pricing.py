"""Combine cached TSM data and manual overrides into one price table.

Every calculator added in later stages should obtain prices through
``resolve_price`` so that missing prices, overrides and stale data are handled
in exactly one place.

Priority for a single item:
    1. manual override (if set)
    2. commodities dataset, then realm dataset, then region dataset
       - the first one that has a value for the selected price basis
    3. otherwise the price is MISSING (None) - never silently 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from . import config
from .currency import is_missing, series_copper_to_gold
from .tsm_client import PRICE_COLUMNS

TABLE_COLUMNS = [
    "item_id", "name", "scope", "sources_available", "tsm_copper",
    "override_copper", "override_note", "effective_copper", "effective_source",
    "market_value", "min_buyout", "recent", "historical", "avg_sale_price",
    "sale_rate", "sold_per_day", "updated_at", "age_hours", "tsm_stale",
]


def build_price_table(prices: pd.DataFrame, overrides: pd.DataFrame,
                      basis: str = config.DEFAULT_PRICE_BASIS,
                      now: Optional[datetime] = None, priority: Optional[tuple] = None) -> pd.DataFrame:
    """One row per item with the effective price (copper) and its provenance.

    ``priority`` is the dataset order (default ``config.source_priority()``).
    """
    if basis not in PRICE_COLUMNS:
        raise ValueError(f"Unknown price basis '{basis}'. Choose from {PRICE_COLUMNS}.")
    now = now or datetime.now(timezone.utc)
    rank = {scope: i for i, scope in enumerate(priority or config.source_priority())}

    if prices.empty:
        table = pd.DataFrame(columns=TABLE_COLUMNS)
    else:
        df = prices.copy()
        # Some files may not carry names; borrow a name from any dataset that has one.
        names = df.dropna(subset=["name"]).drop_duplicates("item_id").set_index("item_id")["name"]
        df["name"] = df["item_id"].map(names)

        available = (
            df.groupby("item_id")["scope"]
            .agg(lambda s: ", ".join(sorted(set(s), key=rank.get)))
            .rename("sources_available")
        )
        df["_rank"] = df["scope"].map(rank)
        df["_has_value"] = df[basis].notna()
        df = (
            df.sort_values(["item_id", "_has_value", "_rank"], ascending=[True, False, True])
            .drop_duplicates("item_id", keep="first")
            .merge(available, left_on="item_id", right_index=True)
        )
        df["tsm_copper"] = df[basis]
        df["updated_at"] = pd.to_datetime(df["source_updated_at"], utc=True, errors="coerce")
        df["age_hours"] = (now - df["updated_at"]).dt.total_seconds() / 3600
        threshold = df["scope"].map(config.STALE_AFTER_HOURS)
        # Unknown timestamp is treated as stale: we cannot vouch for it.
        df["tsm_stale"] = df["age_hours"].isna() | (df["age_hours"] > threshold)
        table = df

    table = _apply_overrides(table, overrides)
    return table.reindex(columns=TABLE_COLUMNS).sort_values("item_id").reset_index(drop=True)


def _apply_overrides(table: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    if overrides is None or overrides.empty:
        table["override_copper"] = float("nan")
        table["override_note"] = None
    else:
        ov = overrides.rename(columns={"price_copper": "override_copper",
                                       "note": "override_note",
                                       "name": "override_name"})
        ov = ov[["item_id", "override_name", "override_copper", "override_note"]]
        # Drop any placeholder override columns (present on an empty table) first.
        table = table.drop(columns=["override_copper", "override_note"], errors="ignore")
        table = table.merge(ov, on="item_id", how="outer")
        # Overrides for items that are not in any TSM file still need a row.
        table["name"] = table["name"].where(table["name"].notna(), table["override_name"])
        table = table.drop(columns=["override_name"])

    for col in ("tsm_copper", "sale_rate", "sold_per_day"):
        if col not in table:
            table[col] = float("nan")
    if "tsm_stale" not in table:
        table["tsm_stale"] = True
    table["tsm_stale"] = table["tsm_stale"].fillna(True).astype(bool)

    has_override = table["override_copper"].notna()
    table["effective_copper"] = table["override_copper"].where(has_override, table["tsm_copper"])
    scope = table["scope"] if "scope" in table else pd.Series(None, index=table.index)
    source = scope.where(table["tsm_copper"].notna(), "missing").fillna("missing")
    table["effective_source"] = source.where(~has_override, "override")
    return table


# --- Single-item lookup used by every calculator ------------------------------

@dataclass(frozen=True)
class PriceResult:
    item_id: int
    name: Optional[str]
    copper: Optional[float]          # None => missing, NOT zero
    source: str                      # override | commodities | realm | region | missing
    is_override: bool
    is_stale: bool
    updated_at: Optional[datetime]

    @property
    def is_missing(self) -> bool:
        return self.copper is None


def _result_from_row(row: pd.Series, item_id: int) -> PriceResult:
    copper = None if is_missing(row["effective_copper"]) else float(row["effective_copper"])
    is_override = row["effective_source"] == "override"
    updated = row["updated_at"]
    return PriceResult(
        item_id=item_id,
        name=None if is_missing(row["name"]) else str(row["name"]),
        copper=copper,
        source=str(row["effective_source"]),
        is_override=bool(is_override),
        # An override is the user's own number, so TSM staleness does not apply to it.
        is_stale=False if is_override else bool(row["tsm_stale"]),
        updated_at=None if is_missing(updated) else updated.to_pydatetime(),
    )


def _missing_result(item_id: int) -> PriceResult:
    return PriceResult(item_id, None, None, "missing", False, True, None)


def resolve_price(table: pd.DataFrame, item_id: int) -> PriceResult:
    """Look up one item. Unknown items and items without a price are 'missing'."""
    rows = table[table["item_id"] == item_id]
    if rows.empty:
        return _missing_result(item_id)
    return _result_from_row(rows.iloc[0], item_id)


class PriceIndex:
    """Fast repeated lookups against one price table (same answers as ``resolve_price``)."""

    def __init__(self, table: pd.DataFrame):
        self._table = (table.drop_duplicates("item_id").set_index("item_id", drop=False)
                       if not table.empty else table)

    def row(self, item_id: int):
        """The raw table row (Series) for an item, or None."""
        if self._table.empty or item_id not in self._table.index:
            return None
        return self._table.loc[item_id]

    def get(self, item_id: int) -> PriceResult:
        row = self.row(item_id)
        return _missing_result(item_id) if row is None else _result_from_row(row, item_id)

    def demand(self, item_id: int) -> tuple:
        """(sold_per_day, sale_rate) from TSM, each None when not provided."""
        row = self.row(item_id)
        if row is None:
            return None, None
        def clean(v):
            return None if is_missing(v) else float(v)
        return clean(row["sold_per_day"]), clean(row["sale_rate"])

    def lookup_name(self, name: str) -> list:
        """[(item_id, name)] for every item whose name matches exactly (case-insensitive)."""
        found = find_by_name(self._table, name) if not self._table.empty else self._table
        return [(int(i), str(n)) for i, n in zip(found["item_id"], found["name"])] if len(found) else []


# --- Display helpers ---------------------------------------------------------------

def search_table(table: pd.DataFrame, query: str) -> pd.DataFrame:
    """Filter by item ID (digits) or case-insensitive name substring."""
    q = (query or "").strip()
    if not q:
        return table
    if q.isdigit():
        return table[table["item_id"] == int(q)]
    return table[table["name"].fillna("").str.contains(q, case=False, regex=False)]


def to_display_frame(table: pd.DataFrame) -> pd.DataFrame:
    """Copper -> gold and friendly column names for the UI. Input is not modified."""
    out = pd.DataFrame({
        "Item ID": table["item_id"],
        "Name": table["name"],
        "Price source": table["effective_source"],
        "Effective price (g)": series_copper_to_gold(table["effective_copper"]),
        "TSM price (g)": series_copper_to_gold(table["tsm_copper"]),
        "Override (g)": series_copper_to_gold(table["override_copper"]),
        "Market value (g)": series_copper_to_gold(table["market_value"]),
        "Min buyout (g)": series_copper_to_gold(table["min_buyout"]),
        "Recent (g)": series_copper_to_gold(table["recent"]),
        "Historical (g)": series_copper_to_gold(table["historical"]),
        "Avg sale price (g)": series_copper_to_gold(table["avg_sale_price"]),
        "Sale rate": table["sale_rate"],
        "Sold / day": table["sold_per_day"],
        "TSM data time (UTC)": table["updated_at"],
        "Age (h)": table["age_hours"],
        "Stale?": table["tsm_stale"],
    })
    return out


def find_by_name(table: pd.DataFrame, name: str) -> pd.DataFrame:
    """Rows whose name matches ``name`` exactly (case-insensitive).

    More than one row means the name is ambiguous - the caller must ask the
    user which item ID they mean rather than guessing.
    """
    wanted = (name or "").strip().lower()
    if not wanted or table.empty:
        return table.iloc[0:0]
    return table[table["name"].fillna("").str.strip().str.lower() == wanted]
