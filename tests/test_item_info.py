"""Item facts (soulbound, icons, quality tiers) and self-gathered item costs. Synthetic documents only."""

import pandas as pd
import pytest

from goldmaker import crafting, database, item_info
from goldmaker.currency import gold_to_copper as G
from goldmaker.item_info import ItemInfo
from goldmaker.recipes import Reagent, Recipe

LUMBER, NAIL, FENCE = 999601, 999602, 999600


def lumber_doc(binding="ON_ACQUIRE"):
    return {"id": LUMBER, "name": "Test Lumber", "quality": {"type": "COMMON"},
            "preview_item": {"binding": {"type": binding, "name": "Binds when picked up"}}}


MEDIA = {"assets": [{"key": "icon", "value": "https://render.worldofwarcraft.com/us/icons/56/test.jpg"}]}


def fence():
    return Recipe(key="fence", name="Test Fence", crafted_item_id=FENCE,
                  reagents=(Reagent(1, LUMBER, 6, "Test Lumber"), Reagent(2, NAIL, 2, "Nail")))


def test_parse_item_binding_icon_rarity():
    info = item_info.parse_item(lumber_doc(), MEDIA)
    assert info.soulbound and info.icon_url.endswith("test.jpg") and info.rarity == "COMMON"
    assert info.crafting_tier is None                       # no explicit field -> unknown, not guessed
    assert not item_info.parse_item(lumber_doc("ON_EQUIP")).soulbound
    assert not item_info.parse_item({"id": 1}).soulbound


def test_explicit_crafting_quality_field_is_read():
    doc = {"id": 5, "preview_item": {"crafting_reagent_quality": {"tier": 2}}}
    assert item_info.parse_item(doc).crafting_tier == 2


def test_soulbound_reagent_no_longer_blocks_the_calculation():
    recipe = item_info.apply_to_recipe(fence(), {LUMBER: ItemInfo(LUMBER, "Test Lumber", binding="ON_ACQUIRE")})
    assert [g.auctionable for g in recipe.reagents] == [False, True]
    r = crafting.calculate_craft(recipe, {NAIL: G(1)}, G(100), ah_cut=0.0)        # no lumber price at all
    assert r.complete and r.cost_per_craft == G(2)
    assert any("counted as free" in a for a in r.assumptions)
    timed = crafting.calculate_craft(recipe, {NAIL: G(1)}, G(100), opportunity_costs={LUMBER: G(3)}, ah_cut=0.0)
    assert timed.cost_per_craft == G(20)


def test_badges_from_tier_or_label():
    assert item_info.badge("Q1") == "🥉" and item_info.badge("silver") == "🥈" and item_info.badge("Gold") == "🥇"
    assert item_info.badge("", ItemInfo(1, crafting_tier=3)) == "🥇" and item_info.badge("") == ""


def test_item_info_storage(conn):
    database.save_item_info(conn, [item_info.parse_item(lumber_doc(), MEDIA)])
    stored = database.load_item_info(conn)[LUMBER]
    assert stored.soulbound and stored.icon_url and stored.name == "Test Lumber"


def test_fetch_uses_item_and_media_endpoints(tmp_path):
    from goldmaker.blizzard_api import BlizzardClient
    from tests.test_blizzard_api import FakeSession
    docs = {f"/data/wow/item/{LUMBER}": lumber_doc(), f"/data/wow/media/item/{LUMBER}": MEDIA}
    client = BlizzardClient("id", "secret", "us", session=FakeSession(docs), cache_dir=tmp_path)
    found, errors = item_info.fetch(client, [LUMBER, NAIL])
    assert found[LUMBER].soulbound and found[LUMBER].icon_url and NAIL not in found and len(errors) == 1


# --- time cost of self-gathered items -------------------------------------------------------------------

def test_time_cost_per_unit():
    assert crafting.time_cost_per_unit(G(3000), 150) == pytest.approx(G(20))     # 3000 g/h, 150 lumber/h
    assert crafting.time_cost_per_unit(None, 150) is None
    assert crafting.time_cost_per_unit(G(3000), None) is None


def test_typed_cost_wins_over_time_cost(conn):
    database.upsert_item_resource(conn, LUMBER, "Test Lumber", gather_units_per_hour=150)
    database.upsert_item_resource(conn, NAIL, "Nail", opportunity_cost_copper=G(5), gather_units_per_hour=10)
    database.upsert_item_resource(conn, 999603, "Other")
    costs, basis = crafting.resource_costs(database.list_item_resources(conn), G(3000))
    assert costs[LUMBER] == pytest.approx(G(20)) and basis[LUMBER] == "time cost"
    assert costs[NAIL] == G(5) and basis[NAIL] == "your cost"
    assert 999603 not in costs
    assert crafting.resource_costs(database.list_item_resources(conn), None)[0] == {NAIL: G(5)}
    with pytest.raises(ValueError):
        database.upsert_item_resource(conn, LUMBER, gather_units_per_hour=0)
