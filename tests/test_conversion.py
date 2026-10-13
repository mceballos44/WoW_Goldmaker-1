"""Shared input -> outputs maths used by milling and prospecting (placeholder IDs only)."""

import pytest

from goldmaker import conversion, database
from goldmaker.conversion import Batch, Wording, calculate_conversion, estimate_yields
from goldmaker.currency import gold_to_copper as G

ORE, GEM, GLASS = 950, 951, 952
ORE_WORDS = Wording("ore", "ore", "prospected")


def test_extra_cost_per_unit_is_part_of_cost_and_max_price():
    # 1 ore -> 0.2 gems at 100g + 1 glass at 10g = 30g gross, 28.5g net at 5%
    r = calculate_conversion(G(20), 100, {GEM: 0.2, GLASS: 1.0}, {GEM: G(100), GLASS: G(10)},
                             ah_cut=0.05, extra_cost_per_unit=G(1.5))
    assert r.complete
    assert r.net_per_unit == pytest.approx(G(28.5))
    assert r.cost_per_unit == G(21.5)
    assert r.profit_per_unit == pytest.approx(G(7))
    assert r.cost == G(2150) and r.profit == pytest.approx(G(700))
    assert r.roi == pytest.approx(7 / 21.5)
    # break-even ore price leaves room for the extra cost
    assert r.max_purchase_price == pytest.approx(G(27))


def test_missing_input_price_uses_input_label():
    r = calculate_conversion(None, 10, {GEM: 1.0}, {GEM: G(5)}, input_label="Copper Ore")
    assert not r.complete and r.missing_prices == ("Copper Ore",)
    assert r.profit is None and r.max_purchase_price == pytest.approx(G(4.75))


def test_negative_extra_cost_rejected():
    with pytest.raises(ValueError):
        calculate_conversion(G(1), 1, {GEM: 1.0}, {GEM: G(1)}, extra_cost_per_unit=-1)


def test_wording_in_warnings():
    batches = [Batch(ORE, 20, {GEM: 4}), Batch(ORE, 20, {GEM: 6})]
    s = estimate_yields(batches, ORE_WORDS)
    assert s.total_units == 40 and s.yields() == {GEM: 0.25}
    assert any("40 ore prospected" in w for w in s.warnings)
    assert "ore" in estimate_yields([], ORE_WORDS).warnings[0]


def test_prospecting_experiments_are_kept_apart_from_milling(conn):
    database.add_milling_experiment(conn, 1, 40, [{"item_id": 2, "name": "Pigment", "quantity": 500}])
    database.add_experiment(conn, "prospecting", ORE, 100, [
        {"item_id": GEM, "name": "Gem", "quantity": 18}, {"item_id": GLASS, "name": "Glass", "quantity": 95}],
        input_name="Test Ore")
    assert len(database.list_milling_experiments(conn)) == 1
    rows = database.list_experiments(conn, "prospecting")
    assert len(rows) == 1 and rows[0]["process"] == "prospecting"
    assert len(database.list_experiments(conn, None)) == 2
    inputs = database.list_experiment_inputs(conn, "prospecting")
    assert inputs.iloc[0].to_dict() == {"herb_item_id": ORE, "herb_name": "Test Ore",
                                        "experiments": 1, "total_herbs": 100}
    summary = estimate_yields([Batch.from_row(r) for r in rows], ORE_WORDS)
    assert summary.yields() == {GEM: 0.18, GLASS: 0.95}


def test_unknown_process_rejected(conn):
    with pytest.raises(ValueError):
        database.add_experiment(conn, "smelting", ORE, 1, [{"item_id": GEM, "quantity": 1}])
    with pytest.raises(ValueError):
        database.list_experiments(conn, "smelting")


def test_old_database_is_migrated(tmp_path):
    """A database from Stage 2/3 (no process column, no mechanic sizes) still opens and works."""
    import sqlite3
    path = tmp_path / "old.sqlite3"
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE milling_experiments (id INTEGER PRIMARY KEY AUTOINCREMENT, herb_item_id INTEGER NOT NULL,
            herb_name TEXT, herbs_used INTEGER NOT NULL, performed_at TEXT, notes TEXT,
            herb_cost_each_copper INTEGER, observed_prices_json TEXT, created_at TEXT NOT NULL);
        CREATE TABLE profession_stats (character_key TEXT NOT NULL, profession TEXT NOT NULL,
            crafting_skill REAL, multicraft_pct REAL, resourcefulness_pct REAL, ingenuity_pct REAL,
            notes TEXT, updated_at TEXT NOT NULL, PRIMARY KEY (character_key, profession));
        INSERT INTO milling_experiments (herb_item_id, herb_name, herbs_used, created_at)
            VALUES (1, 'Felwort', 40, '2026-10-13');
    """)
    old.commit()
    old.close()
    conn = database.connect(path)
    try:
        rows = database.list_milling_experiments(conn)
        assert len(rows) == 1 and rows[0]["process"] == "milling"
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(profession_stats)")}
        assert {"resourcefulness_saved_pct", "multicraft_extra_pct"} <= columns
    finally:
        conn.close()
    database.connect(path).close()      # running the migration twice is harmless


def test_days_of_volume_reexported_by_milling():
    from goldmaker import milling
    assert milling.days_of_volume is conversion.days_of_volume
