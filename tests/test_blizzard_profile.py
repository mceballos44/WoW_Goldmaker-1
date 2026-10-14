"""Blizzard Profile API: account characters, professions, known recipes (canned documents, fake names)."""

from urllib.parse import parse_qs, urlparse

from goldmaker import blizzard_profile, database
from goldmaker.blizzard_api import BlizzardClient
from tests.test_blizzard_api import FakeResponse, FakeSession

ACCOUNT = {"wow_accounts": [{"characters": [
    {"name": "Alpha", "realm": {"slug": "test-realm", "name": "Test Realm"}, "level": 80,
     "playable_class": {"name": "Mage"}, "faction": {"name": "Alliance"}},
    {"name": "Lowbie", "realm": {"slug": "test-realm", "name": "Test Realm"}, "level": 5}]}]}
PROFESSIONS = {"primaries": [{"profession": {"id": 197, "name": "Tailoring"}, "tiers": [
    {"skill_points": 100, "max_skill_points": 100, "tier": {"id": 9301, "name": "Legion Tailoring"},
     "known_recipes": [{"id": 9401, "name": "Test Rug"}, {"id": 9402, "name": "Test Curtain"}]},
    {"skill_points": 20, "max_skill_points": 100, "tier": {"id": 9302, "name": "Test Tailoring"}}]}],
    "secondaries": [{"profession": {"name": "Cooking"}, "tiers": [{"tier": {"id": 9303, "name": "Test Cooking"}}]}]}


def test_authorize_url_has_scope_state_and_redirect():
    url = blizzard_profile.authorize_url("cid", "http://localhost:8501/", "xyz")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["wow.profile"] and q["state"] == ["xyz"] and q["redirect_uri"] == ["http://localhost:8501/"]
    assert q["response_type"] == ["code"]


def test_parse_account_and_professions():
    chars = blizzard_profile.parse_account(ACCOUNT)
    assert [c.key for c in chars] == ["Alpha-Test Realm", "Lowbie-Test Realm"]
    assert chars[0].class_name == "Mage" and chars[0].faction == "Alliance"
    tiers, known = blizzard_profile.parse_professions(PROFESSIONS)
    assert [t["tier_name"] for t in tiers] == ["Legion Tailoring", "Test Tailoring", "Test Cooking"]
    assert [k["recipe_id"] for k in known] == [9401, 9402]


def test_import_keeps_charexporter_data_and_records_known_recipes(conn, tmp_path):
    from tests.test_character_db import make_snapshot
    database.save_character_snapshot(conn, make_snapshot(name="Alpha", realm="Test Realm"))
    before = conn.execute("SELECT snapshot_at FROM characters WHERE character_key='Alpha-Test Realm'").fetchone()[0]
    session = FakeSession({"/profile/wow/character/test-realm/alpha/professions": PROFESSIONS})
    client = BlizzardClient("id", "secret", "us", session=session, cache_dir=tmp_path)
    report = blizzard_profile.import_characters(conn, client, blizzard_profile.parse_account(ACCOUNT), min_level=10)
    assert report.imported == [("Alpha-Test Realm", 3, 2)]
    assert report.skipped == [("Lowbie-Test Realm", "level 5 is below 10")]
    after = conn.execute("SELECT snapshot_at FROM characters WHERE character_key='Alpha-Test Realm'").fetchone()[0]
    assert after == before                                              # CharExporter snapshot untouched
    assert len(database.list_character_professions(conn, "Alpha-Test Realm")) > 0
    assert database.known_recipe_holders(conn) == {9401: ["Alpha-Test Realm"], 9402: ["Alpha-Test Realm"]}
    assert set(database.list_profession_tiers(conn)["tier_name"]) == {"Legion Tailoring", "Test Tailoring",
                                                                      "Test Cooking"}


def test_unknown_character_is_skipped_with_reason(conn, tmp_path):
    client = BlizzardClient("id", "secret", "us", session=FakeSession({}), cache_dir=tmp_path)
    c = blizzard_profile.AccountCharacter("Ghost", "test-realm", "Test Realm", 80)
    report = blizzard_profile.import_characters(conn, client, [c])
    assert report.imported == [] and "no profession data" in report.skipped[0][1]
    assert database.list_characters(conn).empty


def test_exchange_code_errors_mention_redirect(tmp_path):
    import pytest
    from goldmaker.blizzard_api import BlizzardAPIError

    class Session(FakeSession):
        def post(self, url, data, auth, timeout):
            return FakeResponse(400, {})

    client = BlizzardClient("id", "secret", "us", session=Session({}), cache_dir=tmp_path)
    with pytest.raises(BlizzardAPIError, match="redirect URL"):
        blizzard_profile.exchange_code(client, "code", "http://localhost:8501/")
