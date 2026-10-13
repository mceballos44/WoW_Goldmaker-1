import dataclasses

import pytest

from goldmaker import recipes
from goldmaker.recipes import Reagent, Recipe

HEADER = ("recipe_key,name,crafted_item_id,crafted_item_name,output_quantity,profession,required_skill,"
          "expansion,game_patch,game_recipe_id,source,last_verified,category,notes,"
          "reagent_slot,reagent_item_id,reagent_name,reagent_quantity,quality_label,auctionable,"
          "savings_eligible\n")


def row(key="r1", name="Thing", crafted="900", reagent="901", qty="5", **extra):
    cols = dict(recipe_key=key, name=name, crafted_item_id=crafted, crafted_item_name="", output_quantity="",
                profession="", required_skill="", expansion="", game_patch="", game_recipe_id="", source="",
                last_verified="", category="", notes="", reagent_slot="", reagent_item_id=reagent,
                reagent_name="", reagent_quantity=qty, quality_label="", auctionable="", savings_eligible="")
    cols.update(extra)
    return ",".join(cols[c] for c in recipes.CSV_COLUMNS) + "\n"


def parse(*rows, lookup=None):
    return recipes.parse_recipes_csv(HEADER + "".join(rows), lookup)


def test_basic_recipe_with_defaults():
    result = parse(row(), row(reagent="902", qty="1"))
    assert result.errors == []
    r = result.recipes[0]
    assert (r.key, r.name, r.crafted_item_id, r.output_quantity) == ("r1", "Thing", 900, 1)
    assert [(g.slot, g.item_id, g.quantity, g.auctionable, g.savings_eligible) for g in r.reagents] == [
        (1, 901, 5, True, False), (2, 902, 1, True, False)]


def test_nothing_is_assumed_savings_eligible_or_auctionable_false():
    g = parse(row()).recipes[0].reagents[0]
    assert g.savings_eligible is False and g.auctionable is True


def test_alternatives_share_a_slot_and_flags_are_read():
    result = parse(row(reagent_slot="1", reagent="901", quality_label="Q1", savings_eligible="yes"),
                   row(reagent_slot="1", reagent="902", quality_label="Q2", auctionable="no"))
    r = result.recipes[0]
    assert list(r.slots()) == [1] and len(r.slots()[1]) == 2
    assert r.reagents[0].savings_eligible and not r.reagents[1].auctionable
    assert r.reagents[0].quality_label == "Q1"


def test_auto_slots_do_not_collide_with_explicit_ones():
    r = parse(row(reagent_slot="1"), row(reagent="902"), row(reagent="903")).recipes[0]
    assert sorted(g.slot for g in r.reagents) == [1, 2, 3]


@pytest.mark.parametrize("bad_row, fragment", [
    (row(key=""), "recipe_key is required"),
    (row(name=""), "name is required"),
    (row(crafted=""), "give an item ID or an item name"),
    (row(crafted="abc"), "not a number"),
    (row(qty="0"), "at least 1"),
    (row(qty="2.5"), "whole number"),
    (row(qty=""), "reagent_quantity is required"),
    (row(auctionable="maybe"), "true or false"),
    (row(last_verified="13/10/2026"), "YYYY-MM-DD"),
    (row(crafted="-5"), "positive"),
])
def test_invalid_rows_are_reported_and_skipped(bad_row, fragment):
    result = parse(bad_row)
    assert result.recipes == []
    assert any(fragment in e for e in result.errors), result.errors


def test_recipe_without_reagents_is_rejected():
    result = parse(row(reagent="", qty=""))
    assert result.recipes == [] and any("no reagents" in e for e in result.errors)


def test_duplicate_reagent_in_a_slot_is_rejected():
    result = parse(row(reagent_slot="1"), row(reagent_slot="1"))
    assert result.recipes == [] and any("appears twice" in e for e in result.errors)


def test_conflicting_header_values_across_rows_is_an_error():
    result = parse(row(profession="Jewelcrafting"), row(reagent="902", profession="Alchemy"))
    assert result.recipes == [] and any("differs from the first row" in e for e in result.errors)


def test_blank_header_cells_on_later_rows_are_fine():
    r = parse(row(profession="Jewelcrafting"), row(reagent="902")).recipes[0]
    assert r.profession == "Jewelcrafting"


def test_one_bad_recipe_does_not_block_the_good_one():
    result = parse(row(key="good"), row(key="bad", qty="0"))
    assert [r.key for r in result.recipes] == ["good"] and len(result.errors) == 1


def test_missing_recipe_key_column():
    result = recipes.parse_recipes_csv("name,crafted_item_id\nX,1\n")
    assert result.recipes == [] and "recipe_key" in result.errors[0]


def test_empty_and_header_only_inputs():
    assert recipes.parse_recipes_csv("").errors == ["The file is empty."]
    assert recipes.parse_recipes_csv(recipes.csv_template()).recipes == []


def test_semicolon_and_tab_delimiters_and_bom():
    text = (HEADER + row()).replace(",", ";")
    assert parse_text(text).recipes[0].key == "r1"
    assert parse_text((HEADER + row()).replace(",", "\t")).recipes[0].key == "r1"
    assert parse_text("\ufeff" + HEADER + row()).recipes[0].key == "r1"


def parse_text(text):
    return recipes.parse_recipes_csv(text)


# --- names resolved through a lookup, never guessed ------------------------------------------------

def test_names_resolve_only_when_unambiguous():
    lookup = {"Gold Glass": [(901, "Gold Glass")], "Dup": [(1, "Dup"), (2, "Dup")]}.get
    ok = parse(row(crafted="", reagent="", qty="5", reagent_name="Gold Glass", crafted_item_name="Vial"),
               lookup=lambda n: lookup(n, []))
    assert ok.recipes == [] and any("Vial" in e for e in ok.errors)        # crafted name not found

    lk = lambda n: {"Vial": [(900, "Vial")], "Gold Glass": [(901, "Gold Glass")],
                    "Dup": [(1, "Dup"), (2, "Dup")]}.get(n, [])
    good = parse(row(crafted="", crafted_item_name="Vial", reagent="", reagent_name="Gold Glass"), lookup=lk)
    assert good.errors == [] and good.recipes[0].crafted_item_id == 900
    assert good.recipes[0].reagents[0].item_id == 901

    ambiguous = parse(row(reagent="", reagent_name="Dup"), lookup=lk)
    assert ambiguous.recipes == [] and any("matches several items" in e for e in ambiguous.errors)


def test_names_without_a_lookup_ask_for_ids():
    result = parse(row(reagent="", reagent_name="Gold Glass"))
    assert result.recipes == [] and any("use an item ID" in e for e in result.errors)


def test_id_wins_over_name():
    lk = lambda n: [(1, "Wrong")]
    r = parse(row(reagent="901", reagent_name="Whatever"), lookup=lk).recipes[0]
    assert r.reagents[0].item_id == 901 and r.reagents[0].item_name == "Whatever"


# --- JSON ------------------------------------------------------------------------------------------

def test_json_accepts_list_or_wrapped_object_and_both_reagent_spellings():
    body = ('[{"recipe_key":"r","name":"N","crafted_item_id":9,"reagents":'
            '[{"item_id":1,"quantity":2},{"reagent_item_id":3,"reagent_quantity":4,"auctionable":false}]}]')
    for text in (body, '{"recipes":' + body + "}"):
        r = recipes.parse_recipes(text).recipes[0]
        assert [(g.item_id, g.quantity, g.auctionable) for g in r.reagents] == [(1, 2, True), (3, 4, False)]


@pytest.mark.parametrize("text", ["{not json", '"just a string"', "[1, 2]"])
def test_bad_json_is_an_error_not_a_crash(text):
    assert recipes.parse_recipes(text).errors


def test_json_recipe_without_reagents_list_is_rejected():
    result = recipes.parse_recipes('[{"recipe_key":"r","name":"N","crafted_item_id":9}]')
    assert result.recipes == [] and result.errors


# --- round trip & warnings --------------------------------------------------------------------------

def _full_recipe():
    return Recipe(
        key="decor1", name='Test "Decor", fancy', crafted_item_id=900, crafted_item_name="Decor",
        output_quantity=2, profession="Carpentry", required_skill=25, expansion="Legion",
        game_patch="12.1.5", game_recipe_id=77, source="user notes", last_verified="2026-10-13",
        category="housing", notes="line, with comma",
        reagents=(Reagent(1, 901, 4, "Lumber", "", False, False),
                  Reagent(2, 902, 1, "Cloth Q1", "Q1", True, True),
                  Reagent(2, 903, 1, "Cloth Q2", "Q2", True, True)))


def test_csv_and_json_round_trip_exactly():
    original = [_full_recipe()]
    assert recipes.parse_recipes_csv(recipes.recipes_to_csv(original)).recipes == original
    assert recipes.parse_recipes_json(recipes.recipes_to_json(original)).recipes == original


def test_warnings_for_unverified_data():
    r = parse(row()).recipes[0]
    messages = recipes.recipe_warnings(r)
    assert any("Unverified" in m for m in messages) and any("patch" in m for m in messages)
    assert recipes.recipe_warnings(_full_recipe()) == []


def test_validate_recipe_direct():
    assert recipes.validate_recipe(_full_recipe()) == []
    bad = dataclasses.replace(_full_recipe(), reagents=(), output_quantity=0)
    assert len(recipes.validate_recipe(bad)) == 2


def test_shipped_example_file_parses_cleanly_and_is_clearly_fake():
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "samples" / "recipes_EXAMPLE.csv").read_text(encoding="utf-8")
    result = recipes.parse_recipes(text)
    assert result.errors == [] and len(result.recipes) == 2
    assert all(r.crafted_item_id >= 999_000_000 for r in result.recipes)       # not real item IDs
    assert all("EXAMPLE" in r.name for r in result.recipes)
    chair = result.recipes[0]
    assert len(chair.slots()[1]) == 2 and chair.reagents[2].auctionable is False
