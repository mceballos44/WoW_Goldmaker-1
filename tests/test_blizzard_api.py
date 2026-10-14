"""Blizzard Game Data API importer. No network: a fake session returns canned documents
shaped like the documented endpoints (placeholder IDs only)."""

import pytest

from goldmaker import blizzard_api
from goldmaker.blizzard_api import BlizzardAPIError, BlizzardClient, recipe_from_api


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, docs, token_status=200):
        self.docs, self.token_status, self.calls = docs, token_status, []

    def post(self, url, data, auth, timeout):
        self.calls.append(("POST", url))
        return FakeResponse(self.token_status, {"access_token": "tok", "expires_in": 3600})

    def get(self, url, params, timeout, headers):
        path = url.split(".api.blizzard.com", 1)[1]
        self.calls.append(("GET", path, params["namespace"], headers["Authorization"]))
        return FakeResponse(200, self.docs[path]) if path in self.docs else FakeResponse(404, {})


DOCS = {
    "/data/wow/profession/index": {"professions": [{"id": 9001, "name": "Carpentry"}]},
    "/data/wow/profession/9001": {"skill_tiers": [{"id": 9101, "name": "Test Carpentry"}]},
    "/data/wow/profession/9001/skill-tier/9101": {"categories": [
        {"name": "Decor - Chairs", "recipes": [{"id": 9201, "name": "Test Chair"}]},
        {"name": "Tools", "recipes": [{"id": 9202, "name": "Test Saw"}]}]},
    "/data/wow/recipe/9201": {
        "id": 9201, "name": "Test Chair", "crafted_item": {"id": 999100, "name": "Test Chair"},
        "reagents": [{"reagent": {"id": 999101, "name": "Plank"}, "quantity": 8},
                     {"reagent": {"id": 999102, "name": "Nail"}, "quantity": 2}],
        "crafted_quantity": {"value": 1}},
}


def client(tmp_path, docs=DOCS, **kw):
    return BlizzardClient("id", "secret", "us", session=FakeSession(docs, **kw), cache_dir=tmp_path)


def test_browse_professions_tiers_categories(tmp_path):
    c = client(tmp_path)
    assert c.professions() == [(9001, "Carpentry")]
    assert c.skill_tiers(9001) == [(9101, "Test Carpentry")]
    cats = c.categories(9001, 9101)
    assert [x["name"] for x in blizzard_api.matching_categories(cats, ["decor"])] == ["Decor - Chairs"]
    get = [call for call in c.session.calls if call[0] == "GET"][0]
    assert get[2] == "static-us" and get[3] == "Bearer tok"
    assert sum(1 for call in c.session.calls if call[0] == "POST") == 1      # token is re-used


def test_recipe_is_cached_locally(tmp_path):
    c = client(tmp_path)
    c.recipe(9201)
    c2 = client(tmp_path, docs={})                                          # nothing online now
    assert c2.recipe(9201)["name"] == "Test Chair"
    with pytest.raises(BlizzardAPIError):
        c2.recipe(9201, refresh=True)                                      # refresh goes online -> 404


def test_bad_credentials_and_missing_settings(tmp_path):
    with pytest.raises(BlizzardAPIError, match="rejected"):
        client(tmp_path, token_status=401).professions()
    with pytest.raises(BlizzardAPIError, match="Settings"):
        BlizzardClient("", "", "us")
    with pytest.raises(BlizzardAPIError):
        BlizzardClient("a", "b", "xx")


def test_recipe_conversion():
    conv = recipe_from_api(DOCS["/data/wow/recipe/9201"], "Carpentry", "Test Carpentry", "Decor - Chairs",
                           fetched_on="2026-10-13")
    r = conv.recipe
    assert conv.problems == [] and r.key == "bnet-9201" and r.game_recipe_id == 9201
    assert r.crafted_item_id == 999100 and r.output_quantity == 1
    assert [(g.slot, g.item_id, g.quantity) for g in r.reagents] == [(1, 999101, 8), (2, 999102, 2)]
    assert r.source == "Blizzard Game Data API (2026-10-13)" and r.last_verified is None   # not verified
    assert r.category == "Decor - Chairs" and r.expansion == "Test Carpentry"


def test_conversion_reports_gaps_instead_of_guessing():
    enchant = recipe_from_api({"id": 1, "name": "Enchant", "reagents": [
        {"reagent": {"id": 5, "name": "Dust"}, "quantity": 1}]}, None, None, None)
    assert enchant.recipe is None and "no crafted item" in enchant.problems[0]
    empty = recipe_from_api({"id": 2, "name": "X", "crafted_item": {"id": 7}}, None, None, None)
    assert empty.recipe is None and any("no reagents" in p for p in empty.problems)
    ranged = recipe_from_api({"id": 3, "name": "Y", "crafted_item": {"id": 7},
                              "crafted_quantity": {"minimum": 2, "maximum": 4},
                              "reagents": [{"reagent": {"id": 5}, "quantity": 1}],
                              "modified_crafting_slots": [{"slot_type": {"id": 1}}]}, None, None, None)
    assert ranged.recipe.output_quantity == 2
    assert any("2-4" in n for n in ranged.notes) and any("optional / quality" in n for n in ranged.notes)


def test_faction_specific_output():
    conv = recipe_from_api({"id": 4, "name": "Banner", "alliance_crafted_item": {"id": 11},
                            "horde_crafted_item": {"id": 12},
                            "reagents": [{"reagent": {"id": 5}, "quantity": 1}]}, None, None, None)
    assert conv.recipe.crafted_item_id == 11 and "Alliance" in conv.notes[0]


# --- housing decor recipes: the API names no crafted item ------------------------------------------

DECOR = {"id": 9501, "name": "Test Fence", "reagents": [{"reagent": {"id": 999103, "name": "Plank"}, "quantity": 6}],
         "crafted_quantity": {"value": 1}}


def test_decor_recipe_matched_by_exact_unique_name():
    conv = recipe_from_api(DECOR, "Carpentry", "Legion Carpentry", "Decor", find_item=lambda n: [(999300, n)])
    assert conv.recipe.crafted_item_id == 999300 and any("matched by exact name" in n for n in conv.notes)
    assert conv.recipe.last_verified is None


def test_decor_recipe_ambiguous_or_unknown_name_is_skipped_but_shows_reagents():
    many = recipe_from_api(DECOR, None, None, None, find_item=lambda n: [(1, n), (2, n)])
    assert many.recipe is None and "several items" in many.problems[0]
    none = recipe_from_api(DECOR, None, None, None, find_item=lambda n: [])
    assert none.recipe is None and "fields in the response: crafted_quantity, id, name, reagents" in none.problems[0]
    assert [(g.item_id, g.quantity) for g in none.reagents] == [(999103, 6)]


def test_other_crafted_field_is_used_and_noted():
    raw = dict(DECOR, crafted_decor={"id": 999301, "name": "Test Fence"})
    conv = recipe_from_api(raw, None, None, None)
    assert conv.recipe.crafted_item_id == 999301 and "crafted_decor" in conv.notes[0]


def test_item_search_keeps_only_exact_names(tmp_path):
    class Search(FakeSession):
        def get(self, url, params, timeout, headers):
            assert params["name.en_US"] == "Test Fence" and params["namespace"] == "static-us"
            return FakeResponse(200, {"results": [
                {"data": {"id": 999300, "name": {"en_US": "Test Fence"}}},
                {"data": {"id": 999302, "name": {"en_US": "Test Fence Post"}}}]})
    c = BlizzardClient("id", "secret", "us", session=Search({}), cache_dir=tmp_path)
    assert c.search_items("Test Fence") == [(999300, "Test Fence")]
    assert c.search_items("  ") == []
