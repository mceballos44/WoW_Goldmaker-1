import pytest

from goldmaker import database


def test_override_upsert_update_delete(conn):
    database.upsert_override(conn, 1, 720_000, "first", name="Felwort")
    database.upsert_override(conn, 1, 800_000, "second")
    ov = database.list_overrides(conn)
    assert len(ov) == 1
    assert ov.loc[0, "price_copper"] == 800_000
    assert ov.loc[0, "name"] == "Felwort"        # kept when not supplied again
    database.delete_override(conn, 1)
    assert database.list_overrides(conn).empty


def test_override_rejects_negative(conn):
    with pytest.raises(ValueError):
        database.upsert_override(conn, 1, -5)


def test_zero_override_is_allowed(conn):
    database.upsert_override(conn, 1, 0, "self-farmed")
    assert database.list_overrides(conn).loc[0, "price_copper"] == 0


def test_settings_roundtrip(conn):
    assert database.get_setting(conn, "region", "us") == "us"
    database.set_setting(conn, "region", "eu")
    database.set_setting(conn, "region", "kr")
    assert database.get_setting(conn, "region") == "kr"


def test_data_version_changes(conn):
    before = database.data_version(conn)
    database.upsert_override(conn, 1, 5)
    assert database.data_version(conn) != before
