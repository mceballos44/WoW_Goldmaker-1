"""Profiles -> mechanics, break-even material price, quality tiers (placeholder IDs only)."""

import pytest

from goldmaker import crafting, database, profiles
from goldmaker.crafting import Mechanics, analyse_quality_tiers, breakeven_reagent_price, calculate_craft
from goldmaker.currency import gold_to_copper as G
from goldmaker.recipes import Reagent, Recipe

LOTUS, HERB, FLASK_SILVER, FLASK_GOLD, FLASK = 961, 962, 963, 964, 960


def flask_recipe():
    return Recipe(key="flask", name="Test Flask", crafted_item_id=FLASK, profession="Alchemy",
                  reagents=(Reagent(1, LOTUS, 1, "Lotus", savings_eligible=False),
                            Reagent(2, HERB, 10, "Herb", savings_eligible=True)))


def profile(**kw):
    base = dict(character_key="A-B", profession="Alchemy", skill_level_exported=1,
                max_skill_level_exported=1, concentration_at_snapshot=None, concentration_max=None,
                knowledge_points_spent=None, snapshot_at=None)
    return profiles.ProfessionProfile(**{**base, **kw})


# --- mechanics from a profile ---------------------------------------------------------------------

def test_profile_with_chance_and_size_applies_both_effects_separately():
    m, notes = crafting.mechanics_from_profile(profile(
        resourcefulness_pct=20, resourcefulness_saved_pct=30, multicraft_pct=10, multicraft_extra_pct=50))
    assert m.resourcefulness_applied and m.multicraft_applied
    assert m.resourcefulness_chance == pytest.approx(0.2) and m.resourcefulness_saved_fraction == pytest.approx(0.3)
    assert m.multicraft_chance == pytest.approx(0.1) and m.multicraft_extra_fraction == pytest.approx(0.5)
    assert len(notes) == 1          # only the "proc chance" assumption


def test_profile_without_sizes_applies_nothing_and_says_why():
    m, notes = crafting.mechanics_from_profile(profile(resourcefulness_pct=20, multicraft_pct=None))
    assert not m.resourcefulness_applied and not m.multicraft_applied
    text = " ".join(notes)
    assert "no measured size" in text and "Multicraft % is not entered" in text


def test_profile_sizes_are_stored_and_loaded(conn):
    from tests.test_character_db import make_snapshot
    database.save_character_snapshot(conn, make_snapshot())
    database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", resourcefulness_pct=20,
                                     resourcefulness_saved_pct=30, multicraft_extra_pct=0)
    p = profiles.load_profile(conn, "Alt-Area 52", "Alchemy")
    assert p.resourcefulness_saved_pct == 30 and p.multicraft_extra_pct == 0
    assert "Crafting skill" in p.missing_stats            # sizes do not change missing_stats
    with pytest.raises(ValueError):
        database.upsert_profession_stats(conn, "Alt-Area 52", "Alchemy", resourcefulness_saved_pct=150)


def test_profile_choices_filter_by_profession(conn):
    from tests.test_character_db import make_snapshot
    database.save_character_snapshot(conn, make_snapshot())
    every = profiles.list_profile_choices(conn)
    assert profiles.list_profile_choices(conn, "alchemy") == [c for c in every if "Alchemy" in c[1]]
    assert profiles.list_profile_choices(conn, "Nothing") == []


# --- break-even material price ---------------------------------------------------------------------

def test_breakeven_price_of_the_expensive_reagent():
    # flask sells 500g, 5% cut -> 475 net; herbs 10 x 5g = 50g; lotus break-even = 425g
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, G(500), ah_cut=0.05)
    assert breakeven_reagent_price(r, LOTUS) == pytest.approx(G(425))
    # at the break-even lotus price the profit really is 0
    r0 = calculate_craft(flask_recipe(), {LOTUS: G(425), HERB: G(5)}, G(500), ah_cut=0.05)
    assert r0.profit_per_craft == pytest.approx(0)


def test_breakeven_works_when_that_reagent_has_no_price():
    r = calculate_craft(flask_recipe(), {LOTUS: None, HERB: G(5)}, G(500), ah_cut=0.05)
    assert r.profit_per_craft is None
    assert breakeven_reagent_price(r, LOTUS) == pytest.approx(G(425))


def test_breakeven_includes_resourcefulness_on_eligible_reagent():
    m = Mechanics(resourcefulness_chance=0.5, resourcefulness_saved_fraction=0.5)   # 25% saved
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, G(500), ah_cut=0.05, mechanics=m)
    # herb line costs 50g but only 75% of it on average -> 37.5g; lotus not eligible
    assert breakeven_reagent_price(r, LOTUS, m) == pytest.approx(G(475 - 37.5))
    # herbs are eligible: 10 herbs at p each cost 7.5 p on average
    assert breakeven_reagent_price(r, HERB, m) == pytest.approx(G(175) / 7.5)


def test_breakeven_unknown_when_output_or_other_reagent_unpriced():
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: None}, G(500))
    assert breakeven_reagent_price(r, LOTUS) is None
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, None)
    assert breakeven_reagent_price(r, LOTUS) is None
    assert breakeven_reagent_price(r, 12345) is None


# --- quality tiers ---------------------------------------------------------------------------------

TIERS = [("Silver", FLASK_SILVER, "Flask (silver)"), ("Gold", FLASK_GOLD, "Flask (gold)")]


def test_tier_profit_if_all_crafts_land_in_one_tier():
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, G(400), ah_cut=0.05)   # cost 350g
    q = analyse_quality_tiers(r, TIERS, {FLASK_SILVER: G(300), FLASK_GOLD: G(500)})
    silver, gold = q.lines
    assert silver.profit_if_all == pytest.approx(G(285 - 350)) and silver.profitable is False
    assert gold.profit_if_all == pytest.approx(G(475 - 350)) and gold.profitable is True
    assert q.breakeven_output_price == pytest.approx(G(350) / 0.95)
    assert q.expected_profit_per_craft is None and "does not know the odds" in " ".join(q.notes)


def test_expected_profit_only_from_complete_user_odds():
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, G(400), ah_cut=0.0)   # cost 350g
    prices = {FLASK_SILVER: G(300), FLASK_GOLD: G(500)}
    q = analyse_quality_tiers(r, TIERS, prices, {"Silver": 0.75, "Gold": 0.25})
    assert q.expected_net_per_craft == pytest.approx(G(350)) and q.expected_profit_per_craft == pytest.approx(0)
    bad = analyse_quality_tiers(r, TIERS, prices, {"Silver": 0.5, "Gold": 0.3})
    assert bad.expected_profit_per_craft is None and "80%" in " ".join(bad.notes)


def test_tier_with_chance_but_no_price_blocks_expected_profit():
    r = calculate_craft(flask_recipe(), {LOTUS: G(300), HERB: G(5)}, G(400))
    q = analyse_quality_tiers(r, TIERS, {FLASK_SILVER: G(300), FLASK_GOLD: None}, {"Silver": 0.5, "Gold": 0.5})
    assert q.lines[1].profit_if_all is None and q.expected_profit_per_craft is None
    ok = analyse_quality_tiers(r, TIERS, {FLASK_SILVER: G(300), FLASK_GOLD: None}, {"Silver": 1.0, "Gold": 0.0})
    assert ok.expected_profit_per_craft is not None


def test_output_tiers_storage(conn):
    database.save_recipe(conn, flask_recipe())
    database.save_output_tiers(conn, "flask", [
        {"tier_label": "Silver", "item_id": FLASK_SILVER, "item_name": "Flask (silver)"},
        {"tier_label": "Gold", "item_id": FLASK_GOLD}])
    assert [t["tier_label"] for t in database.list_output_tiers(conn, "flask")] == ["Silver", "Gold"]
    database.save_recipe(conn, flask_recipe())               # re-import keeps the tiers
    assert len(database.list_output_tiers(conn, "flask")) == 2
    with pytest.raises(ValueError):
        database.save_output_tiers(conn, "flask", [{"tier_label": "A", "item_id": 1},
                                                   {"tier_label": "A", "item_id": 2}])
    with pytest.raises(ValueError):
        database.save_output_tiers(conn, "nope", [])
    database.delete_recipe(conn, "flask")
    assert database.list_output_tiers(conn, "flask") == []
