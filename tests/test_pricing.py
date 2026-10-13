from datetime import datetime, timedelta, timezone

import pandas as pd

from goldmaker import database, pricing
from goldmaker.currency import gold_to_copper

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def _prices(rows):
    cols = ["item_id", "name", "market_value", "min_buyout", "recent", "historical",
            "avg_sale_price", "sale_rate", "sold_per_day", "scope", "dataset_key",
            "source_updated_at", "fetched_at"]
    df = pd.DataFrame(rows, columns=cols)
    return df


def _row(item_id, name, mv, scope, updated=NOW - timedelta(hours=1), min_buyout=None):
    return (item_id, name, mv, min_buyout, None, None, None, None, None, scope,
            f"us:{scope}", updated.isoformat(), NOW.isoformat())


NO_OVERRIDES = pd.DataFrame(columns=["item_id", "name", "price_copper", "note"])


def test_commodity_wins_over_realm_and_region():
    prices = _prices([
        _row(1, "Felwort", 500, "region"),
        _row(1, "Felwort", 600, "realm"),
        _row(1, "Felwort", 700, "commodities"),
    ])
    table = pricing.build_price_table(prices, NO_OVERRIDES, "market_value", now=NOW)
    r = pricing.resolve_price(table, 1)
    assert r.copper == 700 and r.source == "commodities"
    assert table.loc[0, "sources_available"] == "commodities, realm, region"


def test_falls_through_to_next_source_when_basis_missing():
    prices = _prices([
        _row(1, "X", None, "commodities", min_buyout=None),
        _row(1, "X", 600, "realm"),
    ])
    r = pricing.resolve_price(pricing.build_price_table(prices, NO_OVERRIDES, "market_value", now=NOW), 1)
    assert r.copper == 600 and r.source == "realm"


def test_missing_price_is_none_not_zero():
    prices = _prices([_row(1, "No Price", None, "commodities")])
    table = pricing.build_price_table(prices, NO_OVERRIDES, "market_value", now=NOW)
    r = pricing.resolve_price(table, 1)
    assert r.copper is None and r.is_missing and r.source == "missing"


def test_unknown_item_is_missing():
    table = pricing.build_price_table(_prices([_row(1, "A", 5, "commodities")]),
                                      NO_OVERRIDES, now=NOW)
    r = pricing.resolve_price(table, 424242)
    assert r.is_missing and r.source == "missing"


def test_override_wins_and_original_tsm_value_is_preserved():
    prices = _prices([_row(1, "Felwort", 720_000, "commodities")])
    overrides = pd.DataFrame([{"item_id": 1, "name": None,
                               "price_copper": gold_to_copper(72), "note": "my buy price"}])
    table = pricing.build_price_table(prices, overrides, now=NOW)
    r = pricing.resolve_price(table, 1)
    assert r.is_override and r.source == "override" and r.copper == 720_000
    assert table.loc[0, "tsm_copper"] == 720_000
    prices2 = _prices([_row(1, "Felwort", 650_000, "commodities")])
    table2 = pricing.build_price_table(prices2, overrides, now=NOW)
    assert pricing.resolve_price(table2, 1).copper == 720_000
    assert table2.loc[0, "tsm_copper"] == 650_000


def test_override_for_item_not_in_tsm_data():
    prices = _prices([_row(1, "A", 5, "commodities")])
    overrides = pd.DataFrame([{"item_id": 999, "name": "Warbound Fel-Touched Lumber",
                               "price_copper": 50_000, "note": ""}])
    table = pricing.build_price_table(prices, overrides, now=NOW)
    r = pricing.resolve_price(table, 999)
    assert r.copper == 50_000 and r.is_override and r.name == "Warbound Fel-Touched Lumber"


def test_override_with_no_tsm_data_at_all():
    overrides = pd.DataFrame([{"item_id": 7, "name": "Thing", "price_copper": 100, "note": ""}])
    table = pricing.build_price_table(_prices([]), overrides, now=NOW)
    assert pricing.resolve_price(table, 7).copper == 100
    assert pricing.resolve_price(table, 8).is_missing


def test_empty_everything():
    table = pricing.build_price_table(_prices([]), NO_OVERRIDES, now=NOW)
    assert table.empty and list(table.columns) == pricing.TABLE_COLUMNS


def test_staleness_flags():
    fresh = _row(1, "Fresh", 1, "commodities", updated=NOW - timedelta(hours=2))
    old = _row(2, "Old", 1, "commodities", updated=NOW - timedelta(hours=30))
    region_ok = _row(3, "RegionOk", 1, "region", updated=NOW - timedelta(hours=30))
    unknown = (4, "Unknown", 1, None, None, None, None, None, None, "realm",
               "us:realm:x", None, NOW.isoformat())
    table = pricing.build_price_table(_prices([fresh, old, region_ok, unknown]),
                                      NO_OVERRIDES, now=NOW)
    stale = dict(zip(table.item_id, table.tsm_stale))
    assert stale == {1: False, 2: True, 3: False, 4: True}


def test_override_is_never_reported_stale():
    prices = _prices([_row(1, "Old", 1, "commodities", updated=NOW - timedelta(days=5))])
    overrides = pd.DataFrame([{"item_id": 1, "name": None, "price_copper": 5, "note": ""}])
    r = pricing.resolve_price(pricing.build_price_table(prices, overrides, now=NOW), 1)
    assert r.is_override and not r.is_stale


def test_name_borrowed_from_other_dataset():
    prices = _prices([_row(1, None, 5, "commodities"), _row(1, "Named", 6, "region")])
    table = pricing.build_price_table(prices, NO_OVERRIDES, now=NOW)
    assert table.loc[0, "name"] == "Named"


def test_search_table_by_name_and_id():
    prices = _prices([_row(10, "Felwort", 1, "commodities"), _row(11, "Sallow Pigment", 1, "commodities")])
    table = pricing.build_price_table(prices, NO_OVERRIDES, now=NOW)
    assert list(pricing.search_table(table, "fel").item_id) == [10]
    assert list(pricing.search_table(table, "11").item_id) == [11]
    assert len(pricing.search_table(table, "")) == 2


def test_display_frame_uses_gold():
    prices = _prices([_row(1, "Felwort", 720_000, "commodities")])
    table = pricing.build_price_table(prices, NO_OVERRIDES, now=NOW)
    shown = pricing.to_display_frame(table)
    assert shown.loc[0, "Effective price (g)"] == 72.0
    assert table.loc[0, "effective_copper"] == 720_000      # source table untouched


def test_invalid_basis_rejected():
    import pytest
    with pytest.raises(ValueError):
        pricing.build_price_table(_prices([]), NO_OVERRIDES, "bogus", now=NOW)
