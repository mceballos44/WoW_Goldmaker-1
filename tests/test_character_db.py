import dataclasses
from datetime import datetime, timedelta, timezone

import pytest

from goldmaker import charexporter as ce
from goldmaker import database, profiles

T0 = datetime(2025, 10, 9, 8, 0, tzinfo=timezone.utc)


def make_snapshot(name="Alt", realm="Area 52", when=T0, professions=None, included=True):
    if professions is None:
        professions = [ce.ProfessionSnapshot(
            "Alchemy", 100, 100, 850, 1000, 12, 150,
            equipment=[{"name": "Apron", "ilvl": 250, "slot": "Accessory", "link": None}],
            specializations=[{"name": "Tab", "state": "Unlocked", "paths": [
                {"name": "Root", "current_rank": 3, "max_rank": 5, "state": "Progressing",
                 "perks": [{"name": "P", "state": "Earned", "is_major": False}], "children": [
                     {"name": "Kid", "current_rank": 1, "max_rank": 1, "state": "Completed",
                      "perks": [], "children": []}]}]}])]
    return ce.CharacterSnapshot(name=name, realm=realm, class_name="Mage", level=90,
                                snapshot_at=when, professions=professions,
                                professions_included=included, source="test")


def test_new_then_updated_then_unchanged_then_older(conn):
    snap = make_snapshot()
    assert database.snapshot_status(conn, snap) == "new"
    assert database.save_character_snapshot(conn, snap) == "new"

    assert database.snapshot_status(conn, snap) == "unchanged"
    assert database.save_character_snapshot(conn, snap) == "unchanged"

    newer = dataclasses.replace(snap, snapshot_at=T0 + timedelta(days=1))
    assert database.save_character_snapshot(conn, newer) == "updated"

    assert database.save_character_snapshot(conn, snap) == "older"      # stale file refused
    stored = database.list_characters(conn).iloc[0]["snapshot_at"]
    assert datetime.fromisoformat(stored) == T0 + timedelta(days=1)


def test_force_allows_older_snapshot(conn):
    newer = make_snapshot(when=T0 + timedelta(days=1))
    database.save_character_snapshot(conn, newer)
    older = dataclasses.replace(newer, snapshot_at=T0)
    assert database.save_character_snapshot(conn, older, force=True) == "updated"
    assert datetime.fromisoformat(database.list_characters(conn).iloc[0]["snapshot_at"]) == T0


def test_unknown_snapshot_time_is_importable(conn):
    database.save_character_snapshot(conn, make_snapshot(when=T0))
    no_time = make_snapshot(when=None)
    assert database.snapshot_status(conn, no_time) == "updated"
    assert database.save_character_snapshot(conn, no_time) == "updated"


def test_requires_identity(conn):
    with pytest.raises(ValueError):
        database.save_character_snapshot(conn, make_snapshot(name="", realm=""))


def test_profession_data_roundtrip(conn):
    database.save_character_snapshot(conn, make_snapshot())
    row = database.list_character_professions(conn).iloc[0]
    assert (row.profession, row.skill_level, row.concentration, row.knowledge_points_spent) == ("Alchemy", 100, 850, 12)
    assert profiles.parse_json_column(row.equipment_json)[0]["name"] == "Apron"
    rows = profiles.specializations_to_rows(profiles.parse_json_column(row.specializations_json))
    assert [r["Path"].strip() for r in rows] == ["Root", "↳ Kid"]
    assert rows[0]["Perks earned"] == "1/1"


def test_reimport_keeps_manual_stats(conn):
    database.save_character_snapshot(conn, make_snapshot())
    database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", 120, 18.5, 22.0, 9.0, "my gear")
    database.save_character_snapshot(conn, make_snapshot(when=T0 + timedelta(days=2)))
    stats = database.get_profession_stats(conn, "Alt-Area 52", "Alchemy")
    assert (stats["multicraft_pct"], stats["resourcefulness_pct"], stats["notes"]) == (18.5, 22.0, "my gear")


def test_dropped_profession_is_removed_when_snapshot_includes_professions(conn):
    two = make_snapshot(professions=[ce.ProfessionSnapshot("Alchemy", 1, 1), ce.ProfessionSnapshot("Herbalism", 1, 1)])
    database.save_character_snapshot(conn, two)
    one = make_snapshot(when=T0 + timedelta(days=1), professions=[ce.ProfessionSnapshot("Alchemy", 2, 2)])
    database.save_character_snapshot(conn, one)
    assert list(database.list_character_professions(conn).profession) == ["Alchemy"]


def test_snapshot_without_professions_category_keeps_existing_professions(conn):
    database.save_character_snapshot(conn, make_snapshot())
    partial = make_snapshot(when=T0 + timedelta(days=1), professions=[], included=False)
    database.save_character_snapshot(conn, partial)
    assert list(database.list_character_professions(conn).profession) == ["Alchemy"]


def test_snapshot_with_empty_professions_clears_them(conn):
    database.save_character_snapshot(conn, make_snapshot())
    database.save_character_snapshot(conn, make_snapshot(when=T0 + timedelta(days=1), professions=[]))
    assert database.list_character_professions(conn).empty


def test_delete_character_cascades_to_stats(conn):
    database.save_character_snapshot(conn, make_snapshot())
    database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", multicraft_pct=10)
    database.delete_character(conn, "Alt-Area 52")
    assert database.list_characters(conn).empty
    assert database.list_character_professions(conn).empty
    assert database.list_profession_stats(conn).empty


def test_negative_stats_rejected(conn):
    database.save_character_snapshot(conn, make_snapshot())
    with pytest.raises(ValueError):
        database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", multicraft_pct=-1)


# --- profiles ------------------------------------------------------------------------

def test_profile_missing_stats_are_none_not_zero(conn):
    database.save_character_snapshot(conn, make_snapshot())
    p = profiles.load_profile(conn, "Alt-Area 52", "Alchemy")
    assert p.multicraft_pct is None and p.resourcefulness_pct is None
    assert set(p.missing_stats) == {"Crafting skill", "Multicraft %", "Resourcefulness %", "Ingenuity %"}
    assert p.concentration_at_snapshot == 850 and p.knowledge_points_spent == 12


def test_profile_partial_stats_and_zero_is_a_real_value(conn):
    database.save_character_snapshot(conn, make_snapshot())
    database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", multicraft_pct=0, resourcefulness_pct=15)
    p = profiles.load_profile(conn, "Alt-Area 52", "Alchemy")
    assert p.multicraft_pct == 0 and p.resourcefulness_pct == 15
    assert set(p.missing_stats) == {"Crafting skill", "Ingenuity %"}


def test_profile_unknown_returns_none(conn):
    assert profiles.load_profile(conn, "Nobody-Nowhere", "Alchemy") is None


def test_profile_warnings():
    now = T0 + timedelta(days=10)
    p = profiles.ProfessionProfile("A-B", "Alchemy", 1, 1, 1, 1, 1, T0,
                                   missing_stats=("Multicraft %",))
    text = " ".join(profiles.profile_warnings(p, now))
    assert "Multicraft %" in text and "10 days old" in text
    fresh = profiles.ProfessionProfile("A-B", "Alchemy", 1, 1, 1, 1, 1, now - timedelta(hours=1),
                                       crafting_skill=1, multicraft_pct=1, resourcefulness_pct=1,
                                       ingenuity_pct=1, stats_updated_at=now)
    assert profiles.profile_warnings(fresh, now) == []


def test_stats_entered_before_latest_import_flagged():
    p = profiles.ProfessionProfile("A-B", "Alchemy", 1, 1, 1, 1, 1, T0 + timedelta(days=1),
                                   crafting_skill=1, multicraft_pct=1, resourcefulness_pct=1,
                                   ingenuity_pct=1, stats_updated_at=T0)
    assert any("before the latest import" in w for w in profiles.profile_warnings(p, T0 + timedelta(days=1)))
