from datetime import datetime, timezone
from pathlib import Path

import pytest

from goldmaker import charexporter as ce

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def _read(name):
    return (SAMPLES / name).read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def sv():
    return ce.parse_savedvariables(_read("CharExporter_SAMPLE.lua"))


def test_savedvariables_finds_all_characters(sv):
    assert sv.source_format == "savedvariables"
    assert [c.character_key for c in sv.characters] == ["Testalch-Area 52", "Testjewel-Azjol-Nerub"]


def test_basic_info_and_timestamp(sv):
    c = sv.characters[0]
    assert (c.name, c.realm, c.class_name, c.spec, c.level) == ("Testalch", "Area 52", "Mage", "Frost", 90)
    assert c.snapshot_at == datetime(2025, 10, 9, 8, 53, 20, tzinfo=timezone.utc)


def test_realm_with_hyphen_is_kept_whole(sv):
    c = sv.characters[1]
    assert c.realm == "Azjol-Nerub" and c.character_key == "Testjewel-Azjol-Nerub"


def test_profession_values(sv):
    alch = sv.characters[0].professions[0]
    assert alch.name == "Alchemy"
    assert (alch.skill_level, alch.max_skill_level) == (100, 100)
    assert (alch.concentration, alch.concentration_max) == (850, 1000)
    assert alch.knowledge_points_spent == 12
    assert alch.equipment[0]["name"] == "Test Alchemist's Apron"
    assert alch.equipment[0]["ilvl"] == 250 and alch.equipment[0]["slot"] == "Accessory"
    assert alch.equipment[0]["link"].startswith("|cnIQ4:")


def test_specialization_tree_and_state_mapping(sv):
    tab = sv.characters[0].professions[0].specializations[0]
    assert tab["name"] == "Potion Mastery" and tab["state"] == "Unlocked"
    path = tab["paths"][0]
    assert (path["current_rank"], path["max_rank"], path["state"]) == (5, 10, "Progressing")
    assert [(p["name"], p["state"], p["is_major"]) for p in path["perks"]] == [
        ("Test Perk A", "Earned", False), ("Test Perk B", "Unearned", True)]
    assert path["children"][0]["state"] == "Completed"


def test_profession_without_concentration_has_zero_max(sv):
    herb = sv.characters[0].professions[1]
    assert herb.concentration_max == 0 and herb.specializations == []


def test_json_and_csv_match_savedvariables_for_same_character(sv):
    expected = sv.characters[0]
    for filename in ("CharExporter_SAMPLE.json", "CharExporter_SAMPLE.csv"):
        got = ce.parse_any(_read(filename)).characters[0]
        assert got.character_key == expected.character_key
        assert got.snapshot_at == expected.snapshot_at
        for a, b in zip(got.professions, expected.professions):
            assert (a.name, a.skill_level, a.max_skill_level) == (b.name, b.skill_level, b.max_skill_level)
            assert (a.concentration, a.concentration_max) == (b.concentration, b.concentration_max)
            assert a.knowledge_points_spent == b.knowledge_points_spent
            assert a.equipment[0]["name"] == b.equipment[0]["name"] if b.equipment else not a.equipment
            assert len(a.specializations) == len(b.specializations)


def test_csv_adds_timezone_caution():
    c = ce.parse_csv_export(_read("CharExporter_SAMPLE.csv")).characters[0]
    assert any("time zone" in w for w in c.warnings)


def test_csv_quote_escaping():
    text = ('Category,Key,Value\r\n"Basic","name","Ab"\r\n"Basic","realm","R"\r\n'
            '"Profession","Alchemy","Skill: 5/10"\r\n'
            '"Profession Gear","Alchemy >> Tool","The ""Big"" Hammer (200)"\r\n')
    prof = ce.parse_csv_export(text).characters[0].professions[0]
    assert prof.equipment[0]["name"] == 'The "Big" Hammer' and prof.equipment[0]["ilvl"] == 200


def test_missing_professions_category_is_flagged():
    c = ce.parse_json_export('{"basic": {"name": "A", "realm": "B"}, "timestamp": 1760000000}').characters[0]
    assert not c.professions_included
    assert any("Professions" in w for w in c.warnings)


def test_export_without_basic_has_no_identity_until_supplied():
    c = ce.parse_json_export('{"professions": [{"name": "Alchemy", "skillLevel": 1}], "timestamp": 1}').characters[0]
    assert not c.has_identity
    fixed = ce.apply_identity(c, " Bob ", "Illidan")
    assert fixed.has_identity and fixed.character_key == "Bob-Illidan"


def test_unknown_placeholder_name_is_treated_as_missing():
    # The addon writes "Unknown" when an API call fails.
    c = ce.parse_json_export('{"basic": {"name": "Unknown", "realm": "Unknown"}}').characters[0]
    assert not c.has_identity


def test_empty_saved_file_warns_how_to_fix():
    result = ce.parse_savedvariables('CharExporterDB = { ["global"] = {} }')
    assert result.characters == [] and any("Auto-save" in w for w in result.warnings)


def test_wrong_lua_file_rejected():
    with pytest.raises(ce.CharExporterError, match="CharExporterDB"):
        ce.parse_savedvariables('SomeOtherAddonDB = { }')


@pytest.mark.parametrize("bad", ["", "   ", "hello world", "<html></html>", "{not json"])
def test_unrecognised_input_raises(bad):
    with pytest.raises(ce.CharExporterError):
        ce.parse_any(bad)


def test_autodetect_formats():
    assert ce.parse_any(_read("CharExporter_SAMPLE.lua")).source_format == "savedvariables"
    assert ce.parse_any(_read("CharExporter_SAMPLE.json")).source_format == "json"
    assert ce.parse_any(_read("CharExporter_SAMPLE.csv")).source_format == "csv"


def test_nameless_profession_skipped_with_warning():
    c = ce.parse_json_export('{"basic": {"name": "A", "realm": "B"}, "professions": [{"skillLevel": 5}]}').characters[0]
    assert c.professions == [] and any("no name" in w for w in c.warnings)


def test_garbage_numbers_become_none_not_crash():
    c = ce.parse_json_export('{"basic": {"name": "A", "realm": "B"}, "professions": '
                             '[{"name": "Alchemy", "skillLevel": "abc", "concentration": null}]}').characters[0]
    p = c.professions[0]
    assert p.skill_level is None and p.concentration is None
