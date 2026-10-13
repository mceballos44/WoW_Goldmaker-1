import pytest

from goldmaker import database, pricing
from goldmaker.milling import Experiment, estimate_yields


def add_felwort(conn):
    return database.add_milling_experiment(
        conn, herb_item_id=1, herb_name="Felwort", herbs_used=40,
        outputs=[{"item_id": 2, "name": "Roseate Pigment", "quantity": 500},
                 {"item_id": 3, "name": "Sallow Pigment", "quantity": 100}],
        performed_at="2026-10-13", notes="first batch", herb_cost_each_copper=720_000,
        observed_prices={2: 20_000, 3: 400_000})


def test_roundtrip_feeds_the_calculator(conn):
    add_felwort(conn)
    rows = database.list_milling_experiments(conn, herb_item_id=1)
    assert rows[0]["herbs_used"] == 40 and rows[0]["herb_cost_each_copper"] == 720_000
    assert rows[0]["observed_prices"] == {2: 20_000, 3: 400_000}
    summary = estimate_yields([Experiment.from_row(r) for r in rows])
    assert summary.yields() == {2: 12.5, 3: 2.5}
    assert {e.name for e in summary.estimates} == {"Roseate Pigment", "Sallow Pigment"}


def test_filter_by_herb_and_newest_first(conn):
    first = add_felwort(conn)
    second = database.add_milling_experiment(conn, 1, 10, [{"item_id": 2, "name": None, "quantity": 120}])
    database.add_milling_experiment(conn, 99, 5, [{"item_id": 7, "name": "Other", "quantity": 1}])
    assert [r["id"] for r in database.list_milling_experiments(conn, 1)] == [second, first]
    assert len(database.list_milling_experiments(conn)) == 3


def test_duplicate_outputs_are_summed_and_zero_is_kept(conn):
    database.add_milling_experiment(conn, 1, 10, [
        {"item_id": 2, "name": "A", "quantity": 5}, {"item_id": 2, "name": None, "quantity": 7},
        {"item_id": 3, "name": "B", "quantity": 0}])
    outs = {o["item_id"]: o["quantity"] for o in database.list_milling_experiments(conn)[0]["outputs"]}
    assert outs == {2: 12, 3: 0}


@pytest.mark.parametrize("herbs, outputs", [
    (0, [{"item_id": 2, "name": None, "quantity": 1}]),
    (-5, [{"item_id": 2, "name": None, "quantity": 1}]),
    (10, []),
    (10, [{"item_id": 2, "name": None, "quantity": -1}]),
])
def test_invalid_experiments_rejected_and_nothing_saved(conn, herbs, outputs):
    with pytest.raises(ValueError):
        database.add_milling_experiment(conn, 1, herbs, outputs)
    assert database.list_milling_experiments(conn) == []


def test_delete_cascades_to_outputs(conn):
    exp_id = add_felwort(conn)
    database.delete_milling_experiment(conn, exp_id)
    assert database.list_milling_experiments(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM milling_experiment_outputs").fetchone()[0] == 0


def test_herb_summary(conn):
    add_felwort(conn)
    database.add_milling_experiment(conn, 1, 20, [{"item_id": 2, "name": None, "quantity": 100}])
    herbs = database.list_milling_herbs(conn)
    assert herbs.iloc[0].to_dict() == {"herb_item_id": 1, "herb_name": "Felwort",
                                       "experiments": 2, "total_herbs": 60}


def test_find_by_name_exact_and_ambiguous():
    import pandas as pd
    table = pd.DataFrame({"item_id": [1, 2, 3], "name": ["Felwort", "felwort ", "Felwort Seed"]})
    assert list(pricing.find_by_name(table, "FELWORT").item_id) == [1, 2]      # ambiguous -> caller asks
    assert pricing.find_by_name(table, "nothing").empty
    assert pricing.find_by_name(table.iloc[0:0], "Felwort").empty
