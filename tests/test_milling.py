import math

import pytest

from goldmaker import milling
from goldmaker.currency import gold_to_copper
from goldmaker.milling import Experiment, calculate_milling, estimate_yields

# Placeholder IDs. The real item IDs come from the user's price data, never from this code.
HERB, ROSEATE, SALLOW = 1, 2, 3
G = gold_to_copper


def felwort_batch():
    """The user's first experiment: 40 herbs -> 500 Roseate + 100 Sallow."""
    return Experiment(herb_item_id=HERB, herbs_used=40, herb_name="Felwort",
                      outputs={ROSEATE: 500, SALLOW: 100},
                      output_names={ROSEATE: "Roseate Pigment", SALLOW: "Sallow Pigment"})


# --- the user's Felwort data -------------------------------------------------------------

def test_felwort_single_batch_yields_are_a_point_estimate_only():
    summary = estimate_yields([felwort_batch()])
    assert summary.n_experiments == 1 and summary.total_herbs == 40
    assert summary.yields() == {ROSEATE: 12.5, SALLOW: 2.5}
    assert summary.reliability == "single batch"
    assert all(e.std_error is None and e.ci_low is None for e in summary.estimates)
    assert any("one experiment" in w.lower() for w in summary.warnings)
    assert summary.bounds() == {}


def test_felwort_profit_with_user_prices():
    summary = estimate_yields([felwort_batch()])
    result = calculate_milling(
        herb_price_copper=G(72), quantity=40, yields=summary.yields(),
        output_prices={ROSEATE: G(2), SALLOW: G(40)}, ah_cut=0.05)
    # per herb: 12.5*2g + 2.5*40g = 125g gross; 118.75g after a 5% cut; minus 72g cost
    assert result.complete
    assert result.gross_per_herb == G(125)
    assert result.net_per_herb == pytest.approx(G(118.75))
    assert result.profit_per_herb == pytest.approx(G(46.75))
    assert result.cost == G(72) * 40
    assert result.gross == G(125) * 40 == G(5000)
    assert result.fees == pytest.approx(G(250))
    assert result.net_revenue == pytest.approx(G(4750))
    assert result.profit == pytest.approx(G(1870))
    assert result.roi == pytest.approx(46.75 / 72)
    assert result.max_purchase_price == pytest.approx(G(118.75))


def test_profit_for_1_100_1000_herbs():
    result = calculate_milling(G(72), 40, {ROSEATE: 12.5, SALLOW: 2.5},
                               {ROSEATE: G(2), SALLOW: G(40)})
    assert result.profit_for(1) == pytest.approx(G(46.75))
    assert result.profit_for(100) == pytest.approx(G(4675))
    assert result.profit_for(1000) == pytest.approx(G(46750))
    assert result.cost_for(100) == G(7200)
    assert result.net_revenue_for(100) == pytest.approx(G(11875))


# --- calculator rules -------------------------------------------------------------------------

def test_sell_through_scales_revenue_and_unsold_is_worth_zero():
    full = calculate_milling(G(10), 1, {ROSEATE: 1}, {ROSEATE: G(100)}, ah_cut=0.0, sell_through=1.0)
    half = calculate_milling(G(10), 1, {ROSEATE: 1}, {ROSEATE: G(100)}, ah_cut=0.0, sell_through=0.5)
    assert full.net_per_herb == G(100) and half.net_per_herb == G(50)
    assert half.profit_per_herb == G(40)


def test_target_roi_lowers_max_purchase_price():
    r = calculate_milling(None, 1, {ROSEATE: 1}, {ROSEATE: G(120)}, ah_cut=0.0, target_roi=0.20)
    assert r.max_purchase_price == pytest.approx(G(100))


def test_missing_output_price_is_not_treated_as_zero():
    r = calculate_milling(G(72), 40, {ROSEATE: 12.5, SALLOW: 2.5},
                          {ROSEATE: G(2), SALLOW: None}, names={SALLOW: "Sallow Pigment"})
    assert not r.complete
    assert r.missing_prices == ("Sallow Pigment",)
    assert r.profit is None and r.roi is None and r.max_purchase_price is None
    assert r.gross_per_herb is None


def test_zero_price_is_a_real_price_unlike_missing():
    r = calculate_milling(G(1), 1, {ROSEATE: 10}, {ROSEATE: 0}, ah_cut=0.0)
    assert r.complete and r.profit_per_herb == -G(1)


def test_missing_herb_price_still_gives_max_purchase_price():
    r = calculate_milling(None, 100, {ROSEATE: 2}, {ROSEATE: G(50)}, ah_cut=0.05)
    assert not r.complete and "the herb" in r.missing_prices
    assert r.profit is None and r.cost is None
    assert r.max_purchase_price == pytest.approx(G(95))


def test_item_with_zero_yield_does_not_need_a_price():
    r = calculate_milling(G(1), 1, {ROSEATE: 5, SALLOW: 0}, {ROSEATE: G(10), SALLOW: None}, ah_cut=0.0)
    assert r.complete and r.profit_per_herb == G(49)


@pytest.mark.parametrize("kwargs", [
    {"quantity": -1}, {"ah_cut": 1.0}, {"ah_cut": -0.1}, {"sell_through": 1.5},
    {"target_roi": -1.0}, {"yields": {ROSEATE: -1}}, {"herb_price_copper": -5},
])
def test_invalid_inputs_rejected(kwargs):
    base = dict(herb_price_copper=G(1), quantity=1, yields={ROSEATE: 1}, output_prices={ROSEATE: G(1)})
    base.update(kwargs)
    with pytest.raises(ValueError):
        calculate_milling(**base)


def test_zero_quantity_is_fine():
    r = calculate_milling(G(72), 0, {ROSEATE: 1}, {ROSEATE: G(5)})
    assert r.profit == 0 and r.cost == 0 and r.roi is None


# --- calibration statistics ---------------------------------------------------------------------

def _exp(herbs, **outs):
    ids = {"a": 10, "b": 11}
    return Experiment(herb_item_id=HERB, herbs_used=herbs, outputs={ids[k]: v for k, v in outs.items()})


def test_two_experiments_confidence_interval_matches_hand_calculation():
    # rates: 250/20 and 270/20. mean 13. residuals (250-260)^2+(270-260)^2 = 200
    # var = (2/1) * 200 / 40^2 = 0.25 -> se 0.5; t(df=1)=12.706
    summary = estimate_yields([_exp(20, a=250), _exp(20, a=270)])
    est = summary.estimates[0]
    assert est.mean_per_herb == 13.0
    assert est.std_error == pytest.approx(0.5)
    assert est.ci_low == pytest.approx(13 - 12.706 * 0.5)
    assert est.ci_high == pytest.approx(13 + 12.706 * 0.5)


def test_identical_experiments_have_zero_error_but_are_still_low_reliability():
    summary = estimate_yields([_exp(20, a=100), _exp(20, a=100)])
    est = summary.estimates[0]
    assert est.std_error == 0 and est.ci_low == est.ci_high == 5.0
    assert summary.reliability == "low"


def test_ci_lower_bound_never_negative():
    summary = estimate_yields([_exp(10, a=0), _exp(10, a=30)])
    assert summary.estimates[0].ci_low == 0.0


def test_item_missing_from_an_experiment_counts_as_zero():
    summary = estimate_yields([_exp(10, a=10), _exp(10, a=10, b=5)])
    by_item = {e.item_id: e for e in summary.estimates}
    assert by_item[11].total_output == 5 and by_item[11].mean_per_herb == 0.25


def test_reliability_levels():
    assert estimate_yields([]).reliability == "none"
    assert estimate_yields([_exp(500, a=1)]).reliability == "single batch"
    assert estimate_yields([_exp(100, a=1)] * 3).reliability == "low"
    assert estimate_yields([_exp(20, a=1)] * 6).reliability == "low"          # only 120 herbs
    assert estimate_yields([_exp(50, a=1)] * 5).reliability == "moderate"     # 5 runs, 250 herbs


def test_experiments_with_zero_herbs_are_ignored():
    summary = estimate_yields([_exp(0, a=5), _exp(10, a=10)])
    assert summary.n_experiments == 1 and summary.yields() == {10: 1.0}


def test_t_critical_values():
    assert milling.t_critical_95(0) is None
    assert milling.t_critical_95(1) == 12.706
    assert milling.t_critical_95(11) == 2.228        # falls back to the df=10 value (wider)
    assert milling.t_critical_95(500) == 2.0


def test_profit_range_from_yield_bounds_is_ordered_and_contains_estimate():
    summary = estimate_yields([_exp(20, a=250), _exp(20, a=270)])
    r = calculate_milling(G(5), 100, summary.yields(), {10: G(1)}, ah_cut=0.0,
                          yield_bounds=summary.bounds())
    assert r.profit_per_herb_low < r.profit_per_herb < r.profit_per_herb_high


def test_no_range_without_bounds():
    r = calculate_milling(G(5), 100, {10: 5}, {10: G(1)})
    assert r.profit_per_herb_low is None and r.profit_per_herb_high is None


def test_days_of_volume():
    assert milling.days_of_volume(500, 250) == 2.0
    assert milling.days_of_volume(500, None) is None
    assert milling.days_of_volume(500, 0) is None
    assert milling.days_of_volume(500, float("nan")) is None
