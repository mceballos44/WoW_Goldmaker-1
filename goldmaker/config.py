"""Central configuration: paths, TSM URLs, and tunable constants."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"          # raw CSV files exactly as downloaded
DB_PATH = DATA_DIR / "goldmaker.sqlite3"

# --- TradeSkillMaster public data ------------------------------------------
# Documented at https://tradeskillmaster.com/public-data
TSM_BASE_URL = "https://public-data.tradeskillmaster.com"
GAME_TYPE = "retail"
REGIONS = ("us", "eu", "kr", "tw")
DEFAULT_REGION = "us"
DEFAULT_REALM_SLUG = "area-52"

# Dataset scopes this app understands.
#   commodities -> {base}/retail/{region}/commodities.csv
#   region      -> {base}/retail/{region}/region/items.csv
#   realm       -> {base}/retail/{region}/realm/{slug}/items.csv
SCOPES = ("commodities", "realm", "region")

# When two datasets contain the same item, the first scope in this list that
# has a value wins. Commodities are region-wide live listings, realm files are
# per-realm non-commodity listings, region files are regional aggregates.
SOURCE_PRIORITY = ("commodities", "realm", "region")

# Blizzard Auction House API snapshots (current listings, refreshed about hourly by Blizzard).
# They have no names and no sales history; see goldmaker/blizzard_prices.py.
BNET_SCOPES = ("bnet_commodities", "bnet_realm")
ALL_SCOPES = SCOPES + BNET_SCOPES


def source_priority(prefer_blizzard: bool = False) -> tuple:
    """Order in which datasets are tried for an item's price (first with a value wins)."""
    if prefer_blizzard:
        return ("bnet_commodities", "bnet_realm") + SOURCE_PRIORITY
    return ("commodities", "realm", "bnet_commodities", "bnet_realm", "region")

HTTP_TIMEOUT_SECONDS = 60
USER_AGENT = "WoWGoldmaker/0.1 (personal local analysis tool)"

# --- Currency ---------------------------------------------------------------
COPPER_PER_GOLD = 10_000

# --- Staleness ----------------------------------------------------------------
# TSM states realm/commodity files update every few hours and region files
# daily. These are *warning thresholds* for this app, not TSM guarantees.
STALE_AFTER_HOURS = {"commodities": 12, "realm": 12, "region": 48, "bnet_commodities": 3, "bnet_realm": 3}

PRICE_BASIS_LABELS = {
    "market_value": "Market value",
    "min_buyout": "Min buyout",
    "recent": "Recent",
    "historical": "Historical",
    "avg_sale_price": "Avg sale price",
}
DEFAULT_PRICE_BASIS = "market_value"


def ensure_dirs() -> None:
    """Create the data and cache folders if they do not exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

# --- Auction House ---------------------------------------------------------------------
# Default commission used by calculators, as a percent. This is the standard retail
# AH cut as I understand it - verify in game. It is editable in Settings and in each calculator.
DEFAULT_AH_CUT_PCT = 5.0
