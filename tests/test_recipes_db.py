import dataclasses
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from goldmaker import database, pricing
from goldmaker.recipes import Reagent, Recipe

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def make_recipe(key="r1", name="Decor", reagents=None, **kw):
    reagents = reagents or (Reagent(1, 901, 4, "Lumber", "", False, False),
                            Reagent(2, 902, 1, "Cloth Q1", "Q1", True, True),
                            Reagent(2, 903, 1, "Cloth Q2", "Q2", True, True))
    return Recipe(key=key, name=name, crafted_item_id=900, reagents=tuple(reagents),
                  profession="Carpentry", expansion="Legion", game_patch="12.1.5", **kw)


def test_save_and_reload_is_lossless(conn):
    original = make_recipe(last_verified="2026-10-13", source="notes", category="housing",
                           notes="n", output_quantity=2, required_skill=10, game_recipe_id=5)
    assert database.save_recipe(conn, original) == "new"
    row = database.list_recipe_rows(conn)[0]
    assert dataclasses.replace(Recipe.from_row(row), id=None) == original
    assert row["id"] is not None


def test_saving_same_key_replaces_recipe_and_all_reagents(conn):
    database.save_recipe(conn, make_recipe())
    changed = make_recipe(name="Renamed", reagents=(Reagent(1, 950, 9, "Only"),))
    assert database.save_recipe(conn, changed) == "replaced"
    rows = database.list_recipe_rows(conn)
    assert len(rows) == 1 and rows[0]["name"] == "Renamed"
    assert [r["item_id"] for r in rows[0]["reagents"]] == [950]


def test_list_is_name_ordered_and_filterable_by_key(conn):
    database.save_recipe(conn, make_recipe("b", "Banner"))
    database.save_recipe(conn, make_recipe("a", "apple"))
    assert [r["name"] for r in database.list_recipe_rows(conn)] == ["apple", "Banner"]
    assert [r["recipe_key"] for r in database.list_recipe_rows(conn, "b")] == ["b"]
    assert database.recipe_keys(conn) == {"a", "b"}


def test_delete_recipe_removes_reagents(conn):
    database.save_recipe(conn, make_recipe())
    database.delete_recipe(conn, "r1")
    assert database.list_recipe_rows(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM recipe_reagents").fetchone()[0] == 0


def test_database_rejects_nonsense_even_if_validation_is_bypassed(conn):
    bad = make_recipe(reagents=(Reagent(1, 901, 0),))
    with pytest.raises(Exception):
        database.save_recipe(conn, bad)
    assert database.list_recipe_rows(conn) == []              # nothing half-saved


# --- item resources -------------------------------------------------------------------------------------

def test_item_resource_crud(conn):
    database.upsert_item_resource(conn, 903, "Fel-Touched Lumber", on_hand=12,
                                  opportunity_cost_copper=50_000, notes="from weekly")
    database.upsert_item_resource(conn, 903, None, on_hand=8, opportunity_cost_copper=None)
    df = database.list_item_resources(conn)
    assert len(df) == 1
    row = df.iloc[0]
    assert row["name"] == "Fel-Touched Lumber" and row["on_hand"] == 8
    assert pd.isna(row["opportunity_cost_copper"])           # cleared, not 0
    database.delete_item_resource(conn, 903)
    assert database.list_item_resources(conn).empty


@pytest.mark.parametrize("kwargs", [{"on_hand": -1}, {"opportunity_cost_copper": -5}])
def test_item_resource_validation(conn, kwargs):
    with pytest.raises(ValueError):
        database.upsert_item_resource(conn, 1, "x", **kwargs)


# --- PriceIndex agrees with resolve_price ---------------------------------------------------------------

def price_table():
    cols = ["item_id", "name", "market_value", "min_buyout", "recent", "historical", "avg_sale_price",
            "sale_rate", "sold_per_day", "scope", "dataset_key", "source_updated_at", "fetched_at"]
    stamp = (NOW - timedelta(hours=1)).isoformat()
    rows = [
        (1, "Felwort", 720_000, None, None, None, None, 0.5, 120.0, "commodities", "us:commodities", stamp, stamp),
        (2, "Pigment", 20_000, None, None, None, None, None, None, "commodities", "us:commodities", stamp, stamp),
        (3, "NoPrice", None, None, None, None, None, 0.1, 2.0, "commodities", "us:commodities", stamp, stamp),
        (4, "Stale", 5, None, None, None, None, None, None, "commodities", "us:commodities",
         (NOW - timedelta(days=3)).isoformat(), stamp),
    ]
    overrides = pd.DataFrame([{"item_id": 2, "name": None, "price_copper": 1, "note": "mine"}])
    return pricing.build_price_table(pd.DataFrame(rows, columns=cols), overrides, now=NOW)


def test_index_matches_resolve_price_for_every_item():
    table = price_table()
    index = pricing.PriceIndex(table)
    for item_id in [1, 2, 3, 4, 999]:
        assert index.get(item_id) == pricing.resolve_price(table, item_id)


def test_index_demand_and_missing_values():
    index = pricing.PriceIndex(price_table())
    assert index.demand(1) == (120.0, 0.5)
    assert index.demand(2) == (None, None)
    assert index.demand(999) == (None, None)


def test_index_name_lookup_is_exact_and_case_insensitive():
    index = pricing.PriceIndex(price_table())
    assert index.lookup_name("FELWORT") == [(1, "Felwort")]
    assert index.lookup_name("Fel") == [] and index.lookup_name("") == []


def test_index_on_empty_table_does_not_crash():
    empty = pricing.PriceIndex(pricing.build_price_table(
        pd.DataFrame(columns=price_table().columns[:0]), pd.DataFrame(columns=["item_id", "name", "price_copper", "note"])))
    assert empty.get(1).is_missing and empty.demand(1) == (None, None) and empty.lookup_name("x") == []
