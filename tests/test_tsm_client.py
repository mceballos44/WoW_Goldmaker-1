from datetime import timezone

import pandas as pd
import pytest

from goldmaker import tsm_client as tsm
from goldmaker.database import get_prices, list_datasets

COMMODITIES_CSV = (
    "itemId,name,marketValue,minBuyout,recent,historical,avgSalePrice,saleRate,soldPerDay,updatedAt\n"
    "1,Felwort,720000,700000,710000,730000,725000,0.5,120.5,1760000000\n"
    "2,Roseate Pigment,20000,0,21000,19000,,0.8,300,1760000000\n"
    "3,No Price Item,,,,,,0.1,1,1760000000\n"
).encode()

# A "region"-style file that lacks several columns and has no name column.
REGION_CSV = "itemId,marketValue,historical\n1,700000,710000\n99,5000,6000\n".encode()


def test_build_url_matches_documented_patterns():
    base = "https://public-data.tradeskillmaster.com/retail/us"
    assert tsm.build_url("commodities", "us") == f"{base}/commodities.csv"
    assert tsm.build_url("region", "us") == f"{base}/region/items.csv"
    assert tsm.build_url("realm", "us", "area-52") == f"{base}/realm/area-52/items.csv"


def test_build_url_validation():
    with pytest.raises(ValueError):
        tsm.build_url("realm", "us", None)
    with pytest.raises(ValueError):
        tsm.build_url("realm", "us", "../etc/passwd")
    with pytest.raises(ValueError):
        tsm.build_url("commodities", "mars")
    with pytest.raises(ValueError):
        tsm.build_url("auctions", "us")


def test_slugify_realm():
    assert tsm.slugify_realm("Area 52") == "area-52"
    assert tsm.slugify_realm("  Moon Guard ") == "moon-guard"
    assert tsm.slugify_realm("Cho'gall") == "chogall"


def test_normalize_maps_columns_and_treats_zero_price_as_missing():
    df, info = tsm.normalize_dataframe(tsm.read_csv_bytes(COMMODITIES_CSV))
    assert list(df.columns) == tsm.CANONICAL_COLUMNS
    felwort = df[df.item_id == 1].iloc[0]
    assert felwort.market_value == 720_000
    pigment = df[df.item_id == 2].iloc[0]
    assert pd.isna(pigment.min_buyout)          # 0 -> no data
    assert pd.isna(pigment.avg_sale_price)      # blank -> no data
    assert info["columns_missing"] == []


def test_normalize_does_not_assume_missing_columns_exist():
    df, info = tsm.normalize_dataframe(tsm.read_csv_bytes(REGION_CSV))
    assert set(info["columns_missing"]) >= {"name", "min_buyout", "sold_per_day", "updated_at"}
    assert df["min_buyout"].isna().all()
    assert df["name"].isna().all()


def test_normalize_handles_region_prefixed_headers():
    csv = b"itemId,regionMarketValue,regionHistorical\n5,1000,2000\n"
    df, _ = tsm.normalize_dataframe(tsm.read_csv_bytes(csv))
    assert df.iloc[0].market_value == 1000
    assert df.iloc[0].historical == 2000


def test_normalize_rejects_file_without_item_id():
    with pytest.raises(tsm.TSMFormatError):
        tsm.normalize_dataframe(tsm.read_csv_bytes(b"foo,bar\n1,2\n"))


@pytest.mark.parametrize("value, expected_year", [
    ("1760000000", 2025),            # unix seconds
    ("1760000000000", 2025),         # unix milliseconds
    ("2025-10-09T08:00:00Z", 2025),  # ISO string
])
def test_parse_source_timestamp_formats(value, expected_year):
    ts = tsm.parse_source_timestamp(pd.Series([value, value]))
    assert ts is not None and ts.year == expected_year
    assert ts.tzinfo is not None and ts.utcoffset() == timezone.utc.utcoffset(None)


def test_parse_source_timestamp_missing():
    assert tsm.parse_source_timestamp(pd.Series([pd.NA, None])) is None


def test_fetch_dataset_success_and_cache_file(conn, tmp_path):
    result = tsm.fetch_dataset(conn, "commodities", "us",
                               downloader=lambda url: COMMODITIES_CSV, cache_dir=tmp_path)
    assert result.ok and result.rows == 3
    assert (tmp_path / "us_commodities.csv").exists()
    datasets = list_datasets(conn)
    assert datasets.loc[0, "source_updated_at"] is not None
    prices = get_prices(conn, "us", "area-52")
    assert len(prices) == 3


def test_failed_refresh_keeps_previous_cached_data(conn, tmp_path):
    tsm.fetch_dataset(conn, "commodities", "us",
                      downloader=lambda url: COMMODITIES_CSV, cache_dir=tmp_path)

    def broken(url):
        raise tsm.TSMDownloadError("offline")

    result = tsm.fetch_dataset(conn, "commodities", "us", downloader=broken, cache_dir=tmp_path)
    assert not result.ok and result.cached_data_available
    assert len(get_prices(conn, "us", "area-52")) == 3     # untouched


def test_garbage_download_does_not_destroy_cache(conn, tmp_path):
    tsm.fetch_dataset(conn, "commodities", "us",
                      downloader=lambda url: COMMODITIES_CSV, cache_dir=tmp_path)
    result = tsm.fetch_dataset(conn, "commodities", "us",
                               downloader=lambda url: b"<html>error</html>", cache_dir=tmp_path)
    assert not result.ok
    assert len(get_prices(conn, "us", "area-52")) == 3


def test_refresh_replaces_rows_instead_of_duplicating(conn, tmp_path):
    for _ in range(2):
        tsm.fetch_dataset(conn, "commodities", "us",
                          downloader=lambda url: COMMODITIES_CSV, cache_dir=tmp_path)
    assert len(get_prices(conn, "us", "area-52")) == 3


def test_realm_data_only_returned_for_selected_realm(conn, tmp_path):
    tsm.fetch_dataset(conn, "realm", "us", "area-52",
                      downloader=lambda url: REGION_CSV, cache_dir=tmp_path)
    assert len(get_prices(conn, "us", "area-52")) == 2
    assert len(get_prices(conn, "us", "illidan")) == 0
    assert len(get_prices(conn, "eu", "area-52")) == 0
