"""Download, parse and cache TradeSkillMaster public CSV datasets.

Only the URL patterns documented at https://tradeskillmaster.com/public-data
are used:

    {base}/retail/{region}/commodities.csv
    {base}/retail/{region}/region/items.csv
    {base}/retail/{region}/realm/{realm-slug}/items.csv

Different files do not necessarily contain the same columns, so the parser
maps headers tolerantly (case/punctuation-insensitive) and leaves any column
it cannot find as missing (NaN) instead of guessing. The columns actually seen
in each file are recorded and shown in the UI.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

import pandas as pd
import requests

from . import config, database
from .database import DatasetMeta

CANONICAL_COLUMNS = [
    "item_id", "name", "market_value", "min_buyout", "recent", "historical",
    "avg_sale_price", "sale_rate", "sold_per_day", "updated_at",
]
PRICE_COLUMNS = ["market_value", "min_buyout", "recent", "historical", "avg_sale_price"]
RATE_COLUMNS = ["sale_rate", "sold_per_day"]

# Header (lower-cased, non-alphanumerics removed) -> canonical column.
_ALIASES = {
    "itemid": "item_id",
    "name": "name",
    "marketvalue": "market_value",
    "minbuyout": "min_buyout",
    "recent": "recent",
    "historical": "historical",
    "avgsaleprice": "avg_sale_price",
    "salerate": "sale_rate",
    "soldperday": "sold_per_day",
    "updatedat": "updated_at",
}


class TSMError(Exception):
    """Base class for TSM data problems."""


class TSMDownloadError(TSMError):
    """The file could not be downloaded."""


class TSMFormatError(TSMError):
    """The file was downloaded but is not a CSV we can use."""


@dataclass
class FetchResult:
    scope: str
    dataset_key: str
    ok: bool
    url: str = ""
    rows: int = 0
    source_updated_at: Optional[str] = None
    error: Optional[str] = None
    cached_data_available: bool = False
    columns_missing: list = field(default_factory=list)


# --- URL / key helpers ----------------------------------------------------------

_SLUG_RE = re.compile(r"[\w\-]+")


def is_valid_slug(slug: str) -> bool:
    return bool(slug) and _SLUG_RE.fullmatch(slug) is not None


def slugify_realm(name: str) -> str:
    """Best-effort realm name -> slug (e.g. "Area 52" -> "area-52").

    This is only a suggestion. TSM's page URL is the source of truth: open your
    realm on tradeskillmaster.com and copy the slug from the address bar,
    especially for realms with apostrophes or accented characters.
    """
    s = name.strip().lower().replace("'", "").replace("\u2019", "").replace(".", "")
    s = re.sub(r"[\s_]+", "-", s)
    s = re.sub(r"[^\w\-]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")


def build_url(scope: str, region: str, realm_slug: Optional[str] = None) -> str:
    if region not in config.REGIONS:
        raise ValueError(f"Unknown region '{region}'. Expected one of {config.REGIONS}.")
    base = f"{config.TSM_BASE_URL}/{config.GAME_TYPE}/{region}"
    if scope == "commodities":
        return f"{base}/commodities.csv"
    if scope == "region":
        return f"{base}/region/items.csv"
    if scope == "realm":
        if not realm_slug or not is_valid_slug(realm_slug):
            raise ValueError("A valid realm slug is required (e.g. 'area-52').")
        return f"{base}/realm/{quote(realm_slug)}/items.csv"
    raise ValueError(f"Unknown dataset scope '{scope}'.")


def dataset_key(scope: str, region: str, realm_slug: Optional[str] = None) -> str:
    if scope == "realm":
        return f"{region}:realm:{realm_slug}"
    return f"{region}:{scope}"


# --- Parsing ----------------------------------------------------------------------

def _header_key(header: str) -> str:
    key = re.sub(r"[^a-z0-9]", "", str(header).lower())
    # Region files may prefix price columns with "region" (e.g. regionMarketValue).
    if key not in _ALIASES and key.startswith("region") and key[6:] in _ALIASES:
        key = key[6:]
    return key


def read_csv_bytes(content: bytes) -> pd.DataFrame:
    try:
        return pd.read_csv(io.BytesIO(content), encoding="utf-8-sig", low_memory=False)
    except Exception as exc:  # pandas raises several different error types
        raise TSMFormatError(f"Could not parse the file as CSV: {exc}") from exc


def normalize_dataframe(raw: pd.DataFrame):
    """Map a raw TSM CSV onto the canonical columns.

    Returns ``(df, info)``. Columns the file does not have are left empty
    (NaN / None) rather than assumed. Prices <= 0 are treated as "no price".
    """
    rename, seen = {}, list(raw.columns)
    for col in raw.columns:
        canonical = _ALIASES.get(_header_key(col))
        if canonical and canonical not in rename.values():
            rename[col] = canonical
    ignored = [c for c in raw.columns if c not in rename]

    df = raw[list(rename)].rename(columns=rename)
    if "item_id" not in df.columns:
        raise TSMFormatError(
            "No item ID column found. Columns in file: " + ", ".join(map(str, seen))
        )

    missing = [c for c in CANONICAL_COLUMNS if c not in df.columns]
    for col in missing:
        df[col] = pd.NA if col in ("name", "updated_at") else float("nan")

    df["item_id"] = pd.to_numeric(df["item_id"], errors="coerce")
    df = df.dropna(subset=["item_id"]).copy()
    df["item_id"] = df["item_id"].astype("int64")
    df = df.drop_duplicates(subset="item_id", keep="first")

    for col in PRICE_COLUMNS:
        values = pd.to_numeric(df[col], errors="coerce").astype("float64")
        df[col] = values.where(values > 0)           # 0 / negative => no data
    for col in RATE_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("float64")
    df["name"] = df["name"].astype("object").where(df["name"].notna(), None)

    info = {"columns_seen": seen, "columns_missing": missing, "columns_ignored": ignored}
    return df.reset_index(drop=True)[CANONICAL_COLUMNS], info


def parse_source_timestamp(series: pd.Series) -> Optional[datetime]:
    """Parse TSM's ``updatedAt`` into a UTC datetime (None if unavailable).

    Accepts unix seconds, unix milliseconds, or ISO-style date strings, since
    the format is not assumed. The latest value is returned (TSM documents it
    as identical for every row in a file).
    """
    values = series.dropna()
    if values.empty:
        return None
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        latest = float(numeric.max())
        unit = "ms" if latest > 1e11 else "s"
        return pd.to_datetime(latest, unit=unit, utc=True).to_pydatetime()
    parsed = pd.to_datetime(values, errors="coerce", utc=True).dropna()
    if parsed.empty:
        return None
    return parsed.max().to_pydatetime()


# --- Download & ingest ------------------------------------------------------------

def download_bytes(url: str) -> bytes:
    try:
        response = requests.get(
            url,
            headers={"User-Agent": config.USER_AGENT},
            timeout=config.HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise TSMDownloadError(f"Download failed for {url}: {exc}") from exc
    return response.content


def _cache_path(key: str, cache_dir: Path) -> Path:
    safe = re.sub(r"[^\w\-]+", "_", key)
    return cache_dir / f"{safe}.csv"


def ingest_csv_bytes(conn, content: bytes, scope: str, region: str,
                     realm_slug: Optional[str] = None, url: str = "",
                     origin: str = "download",
                     cache_dir: Optional[Path] = None) -> FetchResult:
    """Parse CSV bytes, keep a raw copy on disk, and store them in SQLite.

    Raises TSMFormatError if the content is unusable; in that case nothing
    already stored is touched.
    """
    key = dataset_key(scope, region, realm_slug)
    raw = read_csv_bytes(content)
    df, info = normalize_dataframe(raw)
    source_ts = parse_source_timestamp(df["updated_at"])

    cache_dir = Path(cache_dir) if cache_dir else config.CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(key, cache_dir)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_bytes(content)
    tmp.replace(cache_file)

    meta = DatasetMeta(
        dataset_key=key, region=region, scope=scope, realm_slug=realm_slug,
        url=url, fetched_at=database.utc_now_iso(),
        source_updated_at=source_ts.isoformat() if source_ts else None,
        row_count=len(df), columns_seen=info["columns_seen"],
        columns_missing=info["columns_missing"], origin=origin,
        cache_file=str(cache_file),
    )
    database.replace_dataset(conn, meta, df)
    return FetchResult(
        scope=scope, dataset_key=key, ok=True, url=url, rows=len(df),
        source_updated_at=meta.source_updated_at, cached_data_available=True,
        columns_missing=info["columns_missing"],
    )


def fetch_dataset(conn, scope: str, region: str, realm_slug: Optional[str] = None,
                  downloader: Callable[[str], bytes] = download_bytes,
                  cache_dir: Optional[Path] = None) -> FetchResult:
    """Download one dataset. On failure, existing cached data is left intact."""
    key = dataset_key(scope, region, realm_slug)
    url = ""
    try:
        url = build_url(scope, region, realm_slug)
        content = downloader(url)
        return ingest_csv_bytes(conn, content, scope, region, realm_slug,
                                url=url, origin="download", cache_dir=cache_dir)
    except (TSMError, ValueError) as exc:
        return FetchResult(
            scope=scope, dataset_key=key, ok=False, url=url, error=str(exc),
            cached_data_available=database.dataset_exists(conn, key),
        )
