"""Everything the user enters survives closing the app (synthetic data)."""

import pytest

from goldmaker import database


def test_manual_yields_round_trip_and_replace(conn):
    database.save_manual_yields(conn, "milling", 1, [{"item_id": 2, "name": "Pigment", "yield_per_unit": 12.5},
                                                     {"item_id": 3, "name": None, "yield_per_unit": 0.0}])
    rows = database.list_manual_yields(conn, "milling", 1)
    assert [(r["output_item_id"], r["yield_per_unit"]) for r in rows] == [(2, 12.5), (3, 0.0)]
    database.save_manual_yields(conn, "milling", 1, [{"item_id": 2, "name": "Pigment", "yield_per_unit": 11.0}])
    assert [r["yield_per_unit"] for r in database.list_manual_yields(conn, "milling", 1)] == [11.0]
    assert database.list_manual_yields(conn, "prospecting", 1) == []         # kept per process
    assert database.list_experiments(conn, "milling") == []                    # never mixed with experiments
    with pytest.raises(ValueError):
        database.save_manual_yields(conn, "milling", 1, [{"item_id": 2, "yield_per_unit": -1}])


def test_ui_state_round_trip(conn):
    assert database.load_ui_state(conn) == {}
    database.save_ui_state(conn, {"mill_qty_1": 250, "house_exp": ["Legion"], "scan_priced": False})
    assert database.load_ui_state(conn) == {"mill_qty_1": 250, "house_exp": ["Legion"], "scan_priced": False}
    database.set_setting(conn, database.UI_STATE_KEY, "not json")
    assert database.load_ui_state(conn) == {}                                 # a broken value never breaks the app


def test_values_survive_closing_and_reopening_the_app(tmp_path, monkeypatch):
    """Two separate app sessions on the same database, like closing today and opening tomorrow."""
    from streamlit.testing.v1 import AppTest
    from goldmaker import config
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "persist.sqlite3")
    conn = database.connect()
    database.save_manual_yields(conn, "milling", 777, [{"item_id": 778, "name": "Test Pigment",
                                                        "yield_per_unit": 3.5}])
    conn.close()

    first = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=120).run()
    assert not first.exception
    first.number_input(key="scan_ops").set_value(55)
    first.number_input(key="inv_spread").set_value(35.0)
    first.multiselect(key="scan_kinds").set_value(["milling"])
    first.checkbox(key="scan_priced").uncheck()
    first.run()
    assert not first.exception

    second = AppTest.from_file(str(config.PROJECT_ROOT / "app.py"), default_timeout=120).run()   # new session
    assert not second.exception
    assert not [w for w in second.warning if "Session State" in str(w.value)]
    assert second.number_input(key="scan_ops").value == 55
    assert second.number_input(key="inv_spread").value == 35.0
    assert second.multiselect(key="scan_kinds").value == ["milling"]
    assert second.checkbox(key="scan_priced").value is False

    # the herb with only manual yields is offered straight away, in manual mode, with the saved yield
    milling = [t for t in second.tabs if t.label == "Milling"][0]
    pick = [s for s in milling.selectbox if s.key == "mill_input_exp"][0]
    assert any("manual yields only" in o for o in pick.options)
    editor_frames = [d.value for d in milling.dataframe if "Yield per herb" in getattr(d.value, "columns", [])]
    modes = [r.value for r in milling.radio if r.key and r.key.startswith("mill_mode_777")]
    assert modes == ["Manual yields"]
