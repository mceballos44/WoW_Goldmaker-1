"""Prices from Blizzard's Auction House API (current listings).

Blizzard publishes a snapshot of every listing about once an hour: region-wide commodities
and per connected realm. A snapshot shows what is LISTED, not what sold, so:

* ``min_buyout``    = the lowest unit price listed
* ``market_value``  = this app's estimate: quantity-weighted mean unit price of the cheapest
                      ``MARKET_SHARE`` of listed units (ignores a few absurd high listings). It
                      is not TSM's market value and not a sale price.
* no names, no sale rate / sold per day (the API has neither) - names come from TSM data
  when you have it.
* items with variants (bonus IDs) are combined per item ID, taking the cheapest.

Data time: Blizzard's Last-Modified header when sent, otherwise the download time (said so
in the dataset's origin).
"""

from __future__ import annotations

from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import pandas as pd

from . import database
from .blizzard_api import BlizzardAPIError, BlizzardClient
from .database import DatasetMeta, PRICE_TABLE_COLUMNS
from .tsm_client import FetchResult

MARKET_SHARE = 0.15


def dataset_key(scope: str, region: str, realm_slug: Optional[str] = None) -> str:
    return f"{region}-bnet-commodities" if scope == "bnet_commodities" else f"{region}-bnet-realm-{realm_slug}"


def _unit_price(auction: dict) -> Optional[float]:
    """Copper per unit: commodities carry unit_price; other auctions a buyout for the whole stack."""
    quantity = auction.get("quantity") or 1
    if auction.get("unit_price") is not None:
        return float(auction["unit_price"])
    if auction.get("buyout") is not None:
        return float(auction["buyout"]) / quantity
    return None                       # bid-only auctions have no buyout price


def auctions_to_prices(auctions: list, market_share: float = MARKET_SHARE) -> pd.DataFrame:
    """One row per item ID in the database's price-table layout."""
    rows = []
    for a in auctions:
        item = a.get("item") or {}
        price = _unit_price(a)
        if item.get("id") is None or price is None or price <= 0:
            continue
        rows.append((int(item["id"]), price, int(a.get("quantity") or 1)))
    if not rows:
        return pd.DataFrame(columns=PRICE_TABLE_COLUMNS)
    frame = pd.DataFrame(rows, columns=["item_id", "price", "quantity"]).sort_values(["item_id", "price"])

    out = []
    for item_id, group in frame.groupby("item_id", sort=True):
        total = group["quantity"].sum()
        wanted = max(1.0, total * market_share)
        taken, cost = 0.0, 0.0
        for price, qty in zip(group["price"], group["quantity"]):
            use = min(qty, wanted - taken)
            cost += use * price
            taken += use
            if taken >= wanted:
                break
        out.append({"item_id": item_id, "name": None, "market_value": cost / taken,
                    "min_buyout": group["price"].iloc[0], "recent": None, "historical": None,
                    "avg_sale_price": None, "sale_rate": None, "sold_per_day": None})
    return pd.DataFrame(out, columns=PRICE_TABLE_COLUMNS)


def _iso(last_modified: Optional[str]) -> Optional[str]:
    if not last_modified:
        return None
    try:
        return parsedate_to_datetime(last_modified).astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def fetch(conn, client: BlizzardClient, scope: str, region: str,
          realm_slug: Optional[str] = None) -> FetchResult:
    """Download one Blizzard AH snapshot and store it. On failure, cached data is kept."""
    key = dataset_key(scope, region, realm_slug)
    try:
        if scope == "bnet_commodities":
            auctions, modified = client.commodity_auctions()
            url = "/data/wow/auctions/commodities"
        elif scope == "bnet_realm":
            realm_id = client.connected_realm_id(realm_slug)
            auctions, modified = client.realm_auctions(realm_id)
            url = f"/data/wow/connected-realm/{realm_id}/auctions"
        else:
            raise ValueError(f"Unknown Blizzard scope {scope}")
        df = auctions_to_prices(auctions)
        fetched = database.utc_now_iso()
        source_time = _iso(modified)
        meta = DatasetMeta(
            dataset_key=key, region=region, scope=scope,
            realm_slug=realm_slug if scope == "bnet_realm" else None, url="Blizzard API " + url,
            fetched_at=fetched, source_updated_at=source_time or fetched, row_count=len(df),
            columns_seen=["min_buyout", "market_value (app estimate)"],
            columns_missing=["name", "sale_rate", "sold_per_day", "recent", "historical", "avg_sale_price"],
            origin="Blizzard API" + ("" if source_time else " (data time = download time)"), cache_file=None)
        database.replace_dataset(conn, meta, df)
        return FetchResult(scope=scope, dataset_key=key, ok=True, url=url, rows=len(df),
                           source_updated_at=meta.source_updated_at, cached_data_available=True,
                           columns_missing=meta.columns_missing)
    except (BlizzardAPIError, ValueError) as exc:
        return FetchResult(scope=scope, dataset_key=key, ok=False, error=str(exc),
                           cached_data_available=database.dataset_exists(conn, key))
