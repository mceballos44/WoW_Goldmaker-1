import math

import pandas as pd

from goldmaker.currency import (copper_to_gold, format_gold, gold_to_copper,
                                series_copper_to_gold)


def test_copper_to_gold_basic():
    assert copper_to_gold(10_000) == 1.0
    assert copper_to_gold(720_000) == 72.0
    assert copper_to_gold(32_900) == 3.29
    assert copper_to_gold(1) == 0.0001


def test_gold_to_copper_is_exact_for_decimal_prices():
    assert gold_to_copper(72) == 720_000
    assert gold_to_copper(3.29) == 32_900          # float 3.29 * 10000 would be 32899.999...
    assert gold_to_copper(104) == 1_040_000
    assert gold_to_copper(0.0001) == 1


def test_round_trip():
    for copper in (1, 99, 10_000, 32_900, 123_456_789):
        assert gold_to_copper(copper_to_gold(copper)) == copper


def test_missing_stays_missing_never_zero():
    assert copper_to_gold(None) is None
    assert copper_to_gold(float("nan")) is None
    assert copper_to_gold(pd.NA) is None
    assert gold_to_copper(None) is None
    assert gold_to_copper(float("nan")) is None
    assert format_gold(None) == "n/a"


def test_zero_is_a_real_value():
    assert copper_to_gold(0) == 0.0
    assert gold_to_copper(0) == 0


def test_series_conversion_keeps_nan():
    result = series_copper_to_gold(pd.Series([10_000, float("nan"), 25_000]))
    assert result.iloc[0] == 1.0
    assert math.isnan(result.iloc[1])
    assert result.iloc[2] == 2.5


def test_format_gold():
    assert format_gold(12_345_600) == "1,234.56g"
    assert format_gold(5_000, decimals=1) == "0.5g"
