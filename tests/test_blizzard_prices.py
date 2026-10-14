"""Blizzard AH listings -> prices (synthetic listings, placeholder IDs)."""

from datetime import datetime, timezone

import pandas as pd
import pytest

from goldmaker import blizzard_prices, config, database, pricing
from tests.test_blizzard_api import FakeResponse, FakeSession
from goldmaker.blizzard_api import BlizzardClient


def test_commodity_prices_min_and_cheapest_share_estimate():
    auctions = [{"item": {"id": 901}, "quantity": 10, "unit_price": 100},
                {"item": {"id": 901}, "quantity": 10, "unit_price": 200},
                {"item": {"id": 901}, "quantity": 80, "unit_price": 10_000}]       # 100 units listed
    row = blizzard_prices.auctions_to_prices(auctions).iloc[0]
    assert row["min_buyout"] == 100
    # cheapest 15 units: 10 x 100 + 5 x 200 -> mean 133.33
    assert row["market_value"] == pytest.approx(2000 / 15)
    assert pd.isna(row["sold_per_day"]) and row["name"] is None


def test_realm_auctions_use_buyout_per_unit_and_skip_bid_only():
    auctions = [{"item": {"id": 902, "bonus_lists": [1]}, "quantity": 2, "buyout": 1000},
                {"item": {"id": 902}, "quantity": 1, "buyout": 800},
                {"item": {"id": 903}, "quantity": 1, "bid": 50}]
    df = blizzard_prices.auctions_to_prices(auctions)
    assert list(df["item_id"]) == [902] and df.iloc[0]["min_buyout"] == 500


def test_fetch_stores_dataset_and_tsm_wins_by_default(conn, tmp_path):
    docs = {"/data/wow/auctions/commodities": {"auctions": [{"item": {"id": 901}, "quantity": 5, "unit_price": 300}]},
            "/data/wow/realm/test-realm": {"connected_realm": {"href": "https://x/data/wow/connected-realm/77?n=1"}},
            "/data/wow/connected-realm/77/auctions": {"auctions": [{"item": {"id": 904}, "quantity": 1, "buyout": 70}]}}
    client = BlizzardClient("id", "secret", "us", session=FakeSession(docs), cache_dir=tmp_path)
    r1 = blizzard_prices.fetch(conn, client, "bnet_commodities", "us")
    r2 = blizzard_prices.fetch(conn, client, "bnet_realm", "us", "test-realm")
    assert r1.ok and r2.ok and r1.rows == 1 and r2.dataset_key == "us-bnet-realm-test-realm"

    now = datetime.now(timezone.utc).isoformat()
    tsm = pd.DataFrame([{"item_id": 901, "name": "Glass", "market_value": 250.0, "min_buyout": None, "recent": None,
                         "historical": None, "avg_sale_price": None, "sale_rate": None, "sold_per_day": 9.0}])
    database.replace_dataset(conn, database.DatasetMeta("us-commodities", "us", "commodities", None, "", now, now,
                                                        1, [], [], "test", None), tsm)
    prices = database.get_prices(conn, "us", "test-realm")
    assert set(prices["scope"]) == {"commodities", "bnet_commodities", "bnet_realm"}
    table = pricing.build_price_table(prices, database.list_overrides(conn))
    assert pricing.resolve_price(table, 901).source == "commodities"         # TSM first by default
    assert pricing.resolve_price(table, 904).source == "bnet_realm"          # Blizzard fills the gap
    assert pricing.resolve_price(table, 901).name == "Glass"                 # name borrowed from TSM
    blizzard_first = pricing.build_price_table(prices, database.list_overrides(conn),
                                               priority=config.source_priority(prefer_blizzard=True))
    hit = pricing.resolve_price(blizzard_first, 901)
    assert hit.source == "bnet_commodities" and hit.copper == 300


def test_failed_fetch_keeps_cached_data(conn, tmp_path):
    client = BlizzardClient("id", "secret", "us", session=FakeSession({}), cache_dir=tmp_path)
    result = blizzard_prices.fetch(conn, client, "bnet_realm", "us", "nowhere")
    assert not result.ok and "Not found" in result.error and not result.cached_data_available
