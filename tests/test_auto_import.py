"""Automatic CharExporter import and reference yields (synthetic files only)."""

import os
import shutil
from pathlib import Path

import pytest

from goldmaker import auto_import, conversion, database, scanner
from goldmaker.currency import gold_to_copper as G
from goldmaker.pricing import PriceResult

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "CharExporter_SAMPLE.lua"


def wow_tree(tmp_path) -> Path:
    """A fake WoW install with the synthetic sample as one account's SavedVariables."""
    saved = tmp_path / "World of Warcraft" / "_retail_" / "WTF" / "Account" / "ACC" / "SavedVariables"
    saved.mkdir(parents=True)
    shutil.copy(SAMPLE, saved / "CharExporter.lua")
    return tmp_path / "World of Warcraft"


# --- auto import -----------------------------------------------------------------------------

def test_first_check_imports_then_unchanged_file_is_not_reread(conn, tmp_path):
    root = wow_tree(tmp_path)
    database.set_setting(conn, "wow_path", str(root))
    first = auto_import.check_and_import(conn)
    assert len(first.files) == 1 and len(first.files_read) == 1
    assert first.changed and all(status == "new" for _, status in first.results)
    assert len(database.list_characters(conn)) == len(first.changed)

    second = auto_import.check_and_import(conn)
    assert second.files_read == [] and second.changed == []


def test_rewritten_file_is_read_again_but_same_snapshots_do_not_overwrite(conn, tmp_path):
    root = wow_tree(tmp_path)
    auto_import.check_and_import(conn, [str(root)])
    lua = next(root.rglob("CharExporter.lua"))
    later = lua.stat().st_mtime + 60
    os.utime(lua, (later, later))                   # WoW wrote the file again (logout)
    report = auto_import.check_and_import(conn, [str(root)])
    assert len(report.files_read) == 1
    assert {status for _, status in report.results} == {"unchanged"} and report.changed == []


def test_manual_stats_survive_auto_import(conn, tmp_path):
    root = wow_tree(tmp_path)
    report = auto_import.check_and_import(conn, [str(root)])
    key = report.changed[0]
    profession = database.list_character_professions(conn, key).iloc[0]["profession"]
    database.upsert_profession_stats(conn, key, profession, multicraft_pct=12.5)
    auto_import.check_and_import(conn, [str(root)], force=True)
    assert database.get_profession_stats(conn, key, profession)["multicraft_pct"] == 12.5


def test_no_folder_or_broken_file_is_reported_not_raised(conn, tmp_path):
    assert auto_import.check_and_import(conn, [str(tmp_path / "nowhere")]).files == []
    root = wow_tree(tmp_path)
    next(root.rglob("CharExporter.lua")).write_text("this is not lua {", encoding="utf-8")
    report = auto_import.check_and_import(conn, [str(root)])
    assert report.errors and report.changed == []


def test_enable_setting_and_locations(conn):
    assert auto_import.is_enabled(conn)               # on by default
    auto_import.set_enabled(conn, False)
    assert not auto_import.is_enabled(conn)
    database.set_setting(conn, "wow_path", r"D:\Games\WoW")
    assert auto_import.search_locations(conn) == [r"D:\Games\WoW"]


# --- reference yields --------------------------------------------------------------------------

def test_yield_from_drop():
    # 40% chance per operation, 2.5 on average, 5 inputs per operation -> 0.2 per input
    assert conversion.yield_from_drop(40, 2.5, 5) == pytest.approx(0.2)
    for bad in ((101, 1, 5), (10, -1, 5), (10, 1, 0), (None, 1, 5)):
        with pytest.raises(ValueError):
            conversion.yield_from_drop(*bad)


def test_reference_yields_are_stored_apart_from_experiments(conn):
    database.save_reference_yields(conn, "prospecting", 950, "Test Ore", [
        {"item_id": 951, "name": "Gem", "chance_pct": 40, "avg_quantity": 2.5, "units_per_operation": 5},
        {"item_id": 952, "name": "Glass", "chance_pct": 100, "avg_quantity": 5, "units_per_operation": 5}],
        source="Wowhead, test")
    assert database.list_experiments(conn, "prospecting") == []
    rows = database.list_reference_yields(conn, "prospecting", 950)
    summary = conversion.reference_summary(rows)
    assert summary.reliability == conversion.REFERENCE and summary.n_experiments == 0
    assert summary.yields() == pytest.approx({951: 0.2, 952: 1.0})
    assert "Wowhead, test" in summary.warnings[0]
    assert database.list_reference_inputs(conn, "prospecting").iloc[0]["outputs"] == 2
    database.delete_reference_yields(conn, "prospecting", 950)
    assert database.list_reference_yields(conn, "prospecting") == []


def test_reference_yields_need_a_source_and_unique_outputs(conn):
    row = {"item_id": 951, "chance_pct": 40, "avg_quantity": 1, "units_per_operation": 5}
    with pytest.raises(ValueError):
        database.save_reference_yields(conn, "prospecting", 950, None, [row], source=" ")
    with pytest.raises(ValueError):
        database.save_reference_yields(conn, "prospecting", 950, None, [row, row], source="x")


def test_scanner_flags_reference_yields():
    summary = conversion.reference_summary([{"output_item_id": 951, "output_name": "Gem", "yield_per_unit": 0.2,
                                             "source": "Wowhead"}])

    def price_of(item_id):
        return PriceResult(item_id, None, {950: G(10), 951: G(100)}[item_id], "commodities", False, False, None)

    opp = scanner.from_conversion("prospecting", 950, "Ore", summary, price_of, lambda i: (None, None), 1, ah_cut=0)
    assert opp.profit_per_op == pytest.approx(G(10)) and opp.reliability == conversion.REFERENCE
    assert any("reference yields" in f for f in opp.flags)
    assert not any("small sample" in f for f in opp.flags)


def one_character_file(name: str, timestamp: int) -> str:
    """A SavedVariables file holding ONLY one character (synthetic)."""
    return ('CharExporterDB = {\n\t["global"] = {\n'
            f'\t\t["{name}-Test Realm"] = {{\n\t\t\t["timestamp"] = {timestamp},\n'
            f'\t\t\t["basic"] = {{\n\t\t\t\t["name"] = "{name}",\n\t\t\t\t["realm"] = "Test Realm",\n\t\t\t}},\n'
            '\t\t},\n\t},\n}\n')


def test_file_that_only_keeps_the_last_character_still_builds_up_every_alt(conn, tmp_path):
    saved = tmp_path / "WoW" / "_retail_" / "WTF" / "Account" / "ACC" / "SavedVariables"
    saved.mkdir(parents=True)
    lua = saved / "CharExporter.lua"
    for n, name in enumerate(["Alpha", "Bravo", "Charlie"]):
        lua.write_text(one_character_file(name, 1760000000 + n), encoding="utf-8")
        stamp = 1760000000 + n * 100
        os.utime(lua, (stamp, stamp))               # WoW rewrote the file on this alt's logout
        assert auto_import.check_and_import(conn, [str(tmp_path / "WoW")]).changed == [f"{name}-Test Realm"]
    keys = set(database.list_characters(conn)["character_key"])
    assert keys == {"Alpha-Test Realm", "Bravo-Test Realm", "Charlie-Test Realm"}     # nobody was dropped


def test_every_account_folder_is_read(conn, tmp_path):
    account = tmp_path / "WoW" / "_retail_" / "WTF" / "Account"
    for acc, name in (("111#1", "Alpha"), ("111#2", "Bravo")):
        folder = account / acc / "SavedVariables"
        folder.mkdir(parents=True)
        (folder / "CharExporter.lua").write_text(one_character_file(name, 1760000000), encoding="utf-8")
    report = auto_import.check_and_import(conn, [str(tmp_path / "WoW")])
    assert len(report.files) == 2 and sorted(report.changed) == ["Alpha-Test Realm", "Bravo-Test Realm"]
