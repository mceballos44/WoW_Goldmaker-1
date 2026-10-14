"""Local SQLite storage: downloaded TSM datasets, manual overrides, settings.

Design rules
------------
* Downloaded TSM data lives in ``prices`` and is only ever replaced by a new
  download/import of the same dataset. It is never edited by the user.
* Manual price overrides live in their own table (``price_overrides``) so the
  original TSM numbers are always preserved.
* Prices are stored in copper.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, TYPE_CHECKING

import pandas as pd

from . import config

if TYPE_CHECKING:  # pragma: no cover
    from .charexporter import CharacterSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    dataset_key        TEXT PRIMARY KEY,
    region             TEXT NOT NULL,
    scope              TEXT NOT NULL,
    realm_slug         TEXT,
    url                TEXT,
    fetched_at         TEXT NOT NULL,
    source_updated_at  TEXT,
    row_count          INTEGER,
    columns_seen       TEXT,
    columns_missing    TEXT,
    origin             TEXT,
    cache_file         TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    dataset_key     TEXT NOT NULL REFERENCES datasets(dataset_key) ON DELETE CASCADE,
    item_id         INTEGER NOT NULL,
    name            TEXT,
    market_value    REAL,
    min_buyout      REAL,
    recent          REAL,
    historical      REAL,
    avg_sale_price  REAL,
    sale_rate       REAL,
    sold_per_day    REAL,
    PRIMARY KEY (dataset_key, item_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS price_overrides (
    item_id       INTEGER PRIMARY KEY,
    name          TEXT,
    price_copper  INTEGER NOT NULL,
    note          TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- Characters imported from CharExporter (snapshot data; replaced on re-import).
CREATE TABLE IF NOT EXISTS characters (
    character_key  TEXT PRIMARY KEY,          -- "Name-Realm"
    name           TEXT NOT NULL,
    realm          TEXT NOT NULL,
    class_name     TEXT,
    spec           TEXT,
    level          INTEGER,
    race           TEXT,
    faction        TEXT,
    snapshot_at    TEXT,                      -- when the addon gathered the data (UTC)
    imported_at    TEXT NOT NULL,
    source         TEXT
);

CREATE TABLE IF NOT EXISTS character_professions (
    character_key           TEXT NOT NULL REFERENCES characters(character_key) ON DELETE CASCADE,
    profession              TEXT NOT NULL,
    skill_level             INTEGER,
    max_skill_level         INTEGER,
    concentration           REAL,
    concentration_max       REAL,
    knowledge_points_spent  INTEGER,
    recipes_known           INTEGER,          -- unverified, never used in calculations
    equipment_json          TEXT,
    specializations_json    TEXT,
    snapshot_at             TEXT,
    imported_at             TEXT NOT NULL,
    PRIMARY KEY (character_key, profession)
);

-- Processing experiments (milling, prospecting): one row per batch, plus the items it produced.
-- The table keeps its original milling name; "herb_*" columns hold the input item (herb or ore).
CREATE TABLE IF NOT EXISTS milling_experiments (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    process                TEXT NOT NULL DEFAULT 'milling',
    herb_item_id           INTEGER NOT NULL,
    herb_name              TEXT,
    herbs_used             INTEGER NOT NULL CHECK (herbs_used > 0),
    performed_at           TEXT,
    notes                  TEXT,
    herb_cost_each_copper  INTEGER,          -- context only, not used in yields
    observed_prices_json   TEXT,             -- {item_id: copper} context only
    created_at             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_milling_herb ON milling_experiments(herb_item_id);

CREATE TABLE IF NOT EXISTS milling_experiment_outputs (
    experiment_id  INTEGER NOT NULL REFERENCES milling_experiments(id) ON DELETE CASCADE,
    item_id        INTEGER NOT NULL,
    item_name      TEXT,
    quantity       INTEGER NOT NULL CHECK (quantity >= 0),
    PRIMARY KEY (experiment_id, item_id)
);

-- Crafting recipes. Contents come only from user imports/edits - nothing is built in.
CREATE TABLE IF NOT EXISTS recipes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_key         TEXT NOT NULL UNIQUE,
    name               TEXT NOT NULL,
    crafted_item_id    INTEGER NOT NULL,
    crafted_item_name  TEXT,
    output_quantity    INTEGER NOT NULL DEFAULT 1 CHECK (output_quantity > 0),
    profession         TEXT,
    required_skill     INTEGER,
    expansion          TEXT,
    game_patch         TEXT,
    game_recipe_id     INTEGER,
    source             TEXT,
    last_verified      TEXT,
    category           TEXT,
    notes              TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recipe_reagents (
    recipe_id         INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    slot              INTEGER NOT NULL,      -- reagents sharing a slot are alternatives
    item_id           INTEGER NOT NULL,
    item_name         TEXT,
    quantity          INTEGER NOT NULL CHECK (quantity > 0),
    quality_label     TEXT,
    auctionable       INTEGER NOT NULL DEFAULT 1,
    savings_eligible  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (recipe_id, slot, item_id)
);

-- Per-item stock and opportunity cost (e.g. non-auctionable reagents).
CREATE TABLE IF NOT EXISTS item_resources (
    item_id                  INTEGER PRIMARY KEY,
    name                     TEXT,
    on_hand                  INTEGER CHECK (on_hand IS NULL OR on_hand >= 0),
    opportunity_cost_copper  INTEGER CHECK (opportunity_cost_copper IS NULL OR opportunity_cost_copper >= 0),
    notes                    TEXT,
    updated_at               TEXT NOT NULL
);

-- Stats the user enters by hand. Kept apart from imported data so a re-import
-- can never overwrite them. NULL means "not entered", never 0.
CREATE TABLE IF NOT EXISTS profession_stats (
    character_key        TEXT NOT NULL REFERENCES characters(character_key) ON DELETE CASCADE,
    profession           TEXT NOT NULL,
    crafting_skill       REAL,
    multicraft_pct       REAL,
    resourcefulness_pct  REAL,
    ingenuity_pct        REAL,
    notes                TEXT,
    updated_at           TEXT NOT NULL,
    resourcefulness_saved_pct  REAL,          -- measured share of eligible reagents saved per proc
    multicraft_extra_pct       REAL,          -- measured extra output per proc, % of base output
    PRIMARY KEY (character_key, profession)
);

-- Reference yields typed in from an outside source (e.g. Wowhead community data). Kept apart
-- from experiments so they are never pooled with the user's own measurements.
CREATE TABLE IF NOT EXISTS reference_yields (
    process              TEXT NOT NULL,
    input_item_id        INTEGER NOT NULL,
    input_name           TEXT,
    output_item_id       INTEGER NOT NULL,
    output_name          TEXT,
    chance_pct           REAL NOT NULL CHECK (chance_pct >= 0 AND chance_pct <= 100),
    avg_quantity         REAL NOT NULL CHECK (avg_quantity >= 0),
    units_per_operation  INTEGER NOT NULL CHECK (units_per_operation > 0),
    yield_per_unit       REAL NOT NULL,
    source               TEXT NOT NULL,
    recorded_at          TEXT NOT NULL,
    PRIMARY KEY (process, input_item_id, output_item_id)
);

-- Professions per expansion tier and known recipes, from Blizzard's Profile API. Separate from
-- character_professions (CharExporter) so neither import overwrites the other.
CREATE TABLE IF NOT EXISTS character_profession_tiers (
    character_key     TEXT NOT NULL REFERENCES characters(character_key) ON DELETE CASCADE,
    profession        TEXT NOT NULL,
    tier_id           INTEGER NOT NULL,
    tier_name         TEXT,
    skill_points      INTEGER,
    max_skill_points  INTEGER,
    fetched_at        TEXT NOT NULL,
    PRIMARY KEY (character_key, tier_id)
);

CREATE TABLE IF NOT EXISTS character_known_recipes (
    character_key  TEXT NOT NULL REFERENCES characters(character_key) ON DELETE CASCADE,
    recipe_id      INTEGER NOT NULL,
    recipe_name    TEXT,
    tier_id        INTEGER,
    fetched_at     TEXT NOT NULL,
    PRIMARY KEY (character_key, recipe_id)
);

-- Output quality tiers of a recipe: each tier is its own item ID (e.g. silver / gold quality).
CREATE TABLE IF NOT EXISTS recipe_output_tiers (
    recipe_id   INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    tier_label  TEXT NOT NULL,
    item_id     INTEGER NOT NULL CHECK (item_id > 0),
    item_name   TEXT,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (recipe_id, tier_label)
);
"""

PROCESSES = ("milling", "prospecting")

# Columns added after the first release. ``connect`` adds them to older databases.
_ADDED_COLUMNS = {
    "milling_experiments": [("process", "TEXT NOT NULL DEFAULT 'milling'")],
    "profession_stats": [("resourcefulness_saved_pct", "REAL"), ("multicraft_extra_pct", "REAL")],
}

PRICE_TABLE_COLUMNS = [
    "item_id", "name", "market_value", "min_buyout", "recent",
    "historical", "avg_sale_price", "sale_rate", "sold_per_day",
]


@dataclass
class DatasetMeta:
    dataset_key: str
    region: str
    scope: str
    realm_slug: Optional[str]
    url: str
    fetched_at: str                      # when WE downloaded/imported it (UTC ISO)
    source_updated_at: Optional[str]     # TSM's own updatedAt (UTC ISO)
    row_count: int
    columns_seen: list
    columns_missing: list
    origin: str                          # "download" or "manual import"
    cache_file: Optional[str]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Open (and initialise) the database. Callers should close it."""
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring a database created by an older version up to date (adds columns only)."""
    for table, columns in _ADDED_COLUMNS.items():
        present = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, definition in columns:
            if name not in present:
                with conn:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_process "
                 "ON milling_experiments(process, herb_item_id)")


# --- Datasets & prices --------------------------------------------------------

def _py(value):
    """Convert NaN/NA/numpy scalars into plain Python values for sqlite3."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def replace_dataset(conn: sqlite3.Connection, meta: DatasetMeta, df: pd.DataFrame) -> None:
    """Atomically replace one dataset (metadata + all price rows)."""
    rows = [
        (meta.dataset_key, *[_py(v) for v in row])
        for row in df[PRICE_TABLE_COLUMNS].itertuples(index=False, name=None)
    ]
    with conn:  # single transaction: either everything is replaced or nothing
        conn.execute("DELETE FROM prices WHERE dataset_key = ?", (meta.dataset_key,))
        conn.execute(
            """INSERT OR REPLACE INTO datasets
               (dataset_key, region, scope, realm_slug, url, fetched_at,
                source_updated_at, row_count, columns_seen, columns_missing,
                origin, cache_file)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (meta.dataset_key, meta.region, meta.scope, meta.realm_slug, meta.url,
             meta.fetched_at, meta.source_updated_at, meta.row_count,
             json.dumps(meta.columns_seen), json.dumps(meta.columns_missing),
             meta.origin, meta.cache_file),
        )
        conn.executemany(
            f"""INSERT INTO prices (dataset_key, {", ".join(PRICE_TABLE_COLUMNS)})
                VALUES ({", ".join("?" * (len(PRICE_TABLE_COLUMNS) + 1))})""",
            rows,
        )


def dataset_exists(conn: sqlite3.Connection, dataset_key: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM datasets WHERE dataset_key = ?", (dataset_key,)
    ).fetchone()
    return row is not None


def list_datasets(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM datasets ORDER BY dataset_key", conn)


def get_prices(conn: sqlite3.Connection, region: str, realm_slug: str) -> pd.DataFrame:
    """All cached price rows relevant to one region + realm, with dataset info."""
    query = """
        SELECT p.item_id, p.name, p.market_value, p.min_buyout, p.recent,
               p.historical, p.avg_sale_price, p.sale_rate, p.sold_per_day,
               d.scope, d.dataset_key, d.source_updated_at, d.fetched_at
        FROM prices p
        JOIN datasets d ON d.dataset_key = p.dataset_key
        WHERE d.region = ?
          AND (d.scope IN ('commodities', 'region', 'bnet_commodities')
               OR (d.scope IN ('realm', 'bnet_realm') AND d.realm_slug = ?))
    """
    return pd.read_sql_query(query, conn, params=(region, realm_slug))


# --- Manual overrides ---------------------------------------------------------

def upsert_override(conn: sqlite3.Connection, item_id: int, price_copper: int,
                    note: str = "", name: Optional[str] = None) -> None:
    if price_copper is None or price_copper < 0:
        raise ValueError("Override price must be zero or greater (copper).")
    now = utc_now_iso()
    with conn:
        conn.execute(
            """INSERT INTO price_overrides
               (item_id, name, price_copper, note, created_at, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 price_copper = excluded.price_copper,
                 note         = excluded.note,
                 name         = COALESCE(excluded.name, price_overrides.name),
                 updated_at   = excluded.updated_at""",
            (int(item_id), name or None, int(price_copper), note, now, now),
        )


def delete_override(conn: sqlite3.Connection, item_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM price_overrides WHERE item_id = ?", (int(item_id),))


def list_overrides(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT item_id, name, price_copper, note, created_at, updated_at "
        "FROM price_overrides ORDER BY item_id",
        conn,
    )


# --- Settings -------------------------------------------------------------------

def get_setting(conn: sqlite3.Connection, key: str, default: Optional[str] = None):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def data_version(conn: sqlite3.Connection) -> str:
    """A cheap fingerprint that changes whenever prices or overrides change.

    Used as a cache key by the UI.
    """
    d = conn.execute(
        "SELECT COALESCE(MAX(fetched_at), ''), COUNT(*) FROM datasets"
    ).fetchone()
    o = conn.execute(
        "SELECT COALESCE(MAX(updated_at), ''), COUNT(*) FROM price_overrides"
    ).fetchone()
    return f"{d[0]}|{d[1]}|{o[0]}|{o[1]}"


# --- Characters imported from CharExporter -----------------------------------------

def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def snapshot_status(conn: sqlite3.Connection, snap: "CharacterSnapshot") -> str:
    """How an incoming snapshot compares with what is stored.

    ``new``       - character not stored yet
    ``updated``   - snapshot is newer than stored (or times are unknown)
    ``unchanged`` - same snapshot time as stored
    ``older``     - snapshot is older than stored
    """
    row = conn.execute(
        "SELECT snapshot_at FROM characters WHERE character_key = ?", (snap.character_key,)
    ).fetchone()
    if row is None:
        return "new"
    if snap.snapshot_at is None or row["snapshot_at"] is None:
        return "updated"
    stored = datetime.fromisoformat(row["snapshot_at"])
    incoming = snap.snapshot_at.astimezone(timezone.utc)
    if incoming == stored:
        return "unchanged"
    return "updated" if incoming > stored else "older"


def save_character_snapshot(conn: sqlite3.Connection, snap: "CharacterSnapshot",
                            force: bool = False) -> str:
    """Store an imported character and its professions.

    Returns ``new`` / ``updated`` / ``unchanged`` / ``older``. ``unchanged`` and
    ``older`` snapshots are NOT written unless ``force`` is True, so importing a
    stale file cannot silently replace fresher data. Manually entered
    ``profession_stats`` are never touched.
    """
    if not snap.has_identity:
        raise ValueError("Character name and realm are required.")
    status = snapshot_status(conn, snap)
    if status in ("unchanged", "older") and not force:
        return status

    now = utc_now_iso()
    snap_iso = _iso(snap.snapshot_at)
    with conn:
        conn.execute(
            """INSERT INTO characters
               (character_key, name, realm, class_name, spec, level, race, faction,
                snapshot_at, imported_at, source)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(character_key) DO UPDATE SET
                 class_name  = COALESCE(excluded.class_name, characters.class_name),
                 spec        = COALESCE(excluded.spec, characters.spec),
                 level       = COALESCE(excluded.level, characters.level),
                 race        = COALESCE(excluded.race, characters.race),
                 faction     = COALESCE(excluded.faction, characters.faction),
                 snapshot_at = excluded.snapshot_at,
                 imported_at = excluded.imported_at,
                 source      = excluded.source""",
            (snap.character_key, snap.name, snap.realm, snap.class_name, snap.spec,
             snap.level, snap.race, snap.faction, snap_iso, now, snap.source),
        )
        if snap.professions_included:
            names = [p.name for p in snap.professions]
            if names:
                marks = ",".join("?" * len(names))
                conn.execute(
                    f"DELETE FROM character_professions WHERE character_key = ? "
                    f"AND profession NOT IN ({marks})", (snap.character_key, *names))
            else:
                conn.execute("DELETE FROM character_professions WHERE character_key = ?",
                             (snap.character_key,))
        for p in snap.professions:
            conn.execute(
                """INSERT OR REPLACE INTO character_professions
                   (character_key, profession, skill_level, max_skill_level, concentration,
                    concentration_max, knowledge_points_spent, recipes_known,
                    equipment_json, specializations_json, snapshot_at, imported_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snap.character_key, p.name, p.skill_level, p.max_skill_level,
                 p.concentration, p.concentration_max, p.knowledge_points_spent,
                 p.recipes_known, json.dumps(p.equipment), json.dumps(p.specializations),
                 snap_iso, now),
            )
    return "updated" if status in ("unchanged", "older") else status


def list_characters(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT c.*, (SELECT GROUP_CONCAT(profession, ', ') FROM
                          (SELECT profession FROM character_professions p
                           WHERE p.character_key = c.character_key ORDER BY profession))
                        AS professions
           FROM characters c ORDER BY c.name, c.realm""",
        conn,
    )


def list_character_professions(conn: sqlite3.Connection,
                               character_key: Optional[str] = None) -> pd.DataFrame:
    query = "SELECT * FROM character_professions"
    params: tuple = ()
    if character_key is not None:
        query += " WHERE character_key = ?"
        params = (character_key,)
    return pd.read_sql_query(query + " ORDER BY character_key, profession", conn, params=params)


def delete_character(conn: sqlite3.Connection, character_key: str) -> None:
    """Delete a character together with its professions and manual stats."""
    with conn:
        conn.execute("DELETE FROM characters WHERE character_key = ?", (character_key,))


# --- Manually entered profession stats ---------------------------------------------

def upsert_profession_stats(conn: sqlite3.Connection, character_key: str, profession: str,
                            crafting_skill: Optional[float] = None,
                            multicraft_pct: Optional[float] = None,
                            resourcefulness_pct: Optional[float] = None,
                            ingenuity_pct: Optional[float] = None,
                            notes: str = "",
                            resourcefulness_saved_pct: Optional[float] = None,
                            multicraft_extra_pct: Optional[float] = None) -> None:
    for label, value in (("crafting skill", crafting_skill), ("multicraft", multicraft_pct),
                         ("resourcefulness", resourcefulness_pct), ("ingenuity", ingenuity_pct),
                         ("resourcefulness saved share", resourcefulness_saved_pct),
                         ("multicraft extra output", multicraft_extra_pct)):
        if value is not None and value < 0:
            raise ValueError(f"{label} cannot be negative.")
    if resourcefulness_saved_pct is not None and resourcefulness_saved_pct > 100:
        raise ValueError("resourcefulness saved share cannot be above 100%.")
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO profession_stats
               (character_key, profession, crafting_skill, multicraft_pct,
                resourcefulness_pct, ingenuity_pct, notes, updated_at,
                resourcefulness_saved_pct, multicraft_extra_pct)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (character_key, profession, crafting_skill, multicraft_pct,
             resourcefulness_pct, ingenuity_pct, notes, utc_now_iso(),
             resourcefulness_saved_pct, multicraft_extra_pct),
        )


def get_profession_stats(conn: sqlite3.Connection, character_key: str,
                         profession: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM profession_stats WHERE character_key = ? AND profession = ?",
        (character_key, profession),
    ).fetchone()
    return dict(row) if row else None


def delete_profession_stats(conn: sqlite3.Connection, character_key: str, profession: str) -> None:
    with conn:
        conn.execute("DELETE FROM profession_stats WHERE character_key = ? AND profession = ?",
                     (character_key, profession))


def list_profession_stats(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM profession_stats ORDER BY character_key, profession", conn)


# --- Processing experiments (milling, prospecting) ---------------------------------------

def _check_process(process: str) -> str:
    if process not in PROCESSES:
        raise ValueError(f"Unknown process '{process}'. Choose from {PROCESSES}.")
    return process


def add_experiment(conn: sqlite3.Connection, process: str, input_item_id: int, units_used: int,
                   outputs: list, input_name: Optional[str] = None,
                   performed_at: Optional[str] = None, notes: str = "",
                   input_cost_each_copper: Optional[int] = None,
                   observed_prices: Optional[dict] = None) -> int:
    """Record one batch (e.g. 40 herbs milled, 100 ore prospected) and return its id.

    ``outputs`` is a list of ``{"item_id", "name", "quantity"}`` dicts. Duplicate
    item ids are summed. Quantity 0 is allowed (an item that was expected but
    did not drop is real information).
    """
    _check_process(process)
    if units_used is None or units_used <= 0:
        raise ValueError("Units used must be greater than zero.")
    merged: dict = {}
    for out in outputs:
        quantity = int(out["quantity"])
        if quantity < 0:
            raise ValueError("Output quantities cannot be negative.")
        item_id = int(out["item_id"])
        name, previous = merged.get(item_id, (None, 0))
        merged[item_id] = (out.get("name") or name, previous + quantity)
    if not merged:
        raise ValueError("Add at least one output item.")
    with conn:
        cursor = conn.execute(
            """INSERT INTO milling_experiments
               (process, herb_item_id, herb_name, herbs_used, performed_at, notes,
                herb_cost_each_copper, observed_prices_json, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (process, int(input_item_id), input_name, int(units_used), performed_at, notes,
             input_cost_each_copper,
             json.dumps({str(k): v for k, v in observed_prices.items()}) if observed_prices else None,
             utc_now_iso()),
        )
        experiment_id = cursor.lastrowid
        conn.executemany(
            "INSERT INTO milling_experiment_outputs (experiment_id, item_id, item_name, quantity) "
            "VALUES (?,?,?,?)",
            [(experiment_id, item_id, name, qty) for item_id, (name, qty) in merged.items()],
        )
    return experiment_id


def list_experiments(conn: sqlite3.Connection, process: Optional[str] = "milling",
                     input_item_id: Optional[int] = None) -> list:
    """Experiments (newest first) as dicts, each with an ``outputs`` list.

    ``process=None`` returns every process. The input item is in ``herb_item_id`` /
    ``herb_name`` / ``herbs_used`` (original column names, also used for ore).
    """
    clauses, params = [], []
    if process is not None:
        clauses.append("process = ?")
        params.append(_check_process(process))
    if input_item_id is not None:
        clauses.append("herb_item_id = ?")
        params.append(int(input_item_id))
    query = "SELECT * FROM milling_experiments"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    experiments = [dict(r) for r in conn.execute(query + " ORDER BY id DESC", params)]
    for exp in experiments:
        exp["outputs"] = [dict(r) for r in conn.execute(
            "SELECT item_id, item_name, quantity FROM milling_experiment_outputs "
            "WHERE experiment_id = ? ORDER BY item_id", (exp["id"],))]
        raw = exp.pop("observed_prices_json")
        exp["observed_prices"] = {int(k): v for k, v in json.loads(raw).items()} if raw else {}
    return experiments


def delete_experiment(conn: sqlite3.Connection, experiment_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM milling_experiments WHERE id = ?", (int(experiment_id),))


def list_experiment_inputs(conn: sqlite3.Connection, process: str = "milling") -> pd.DataFrame:
    """Input items (herbs / ores) that have experiments, with counts."""
    return pd.read_sql_query(
        """SELECT herb_item_id, MAX(herb_name) AS herb_name,
                  COUNT(*) AS experiments, SUM(herbs_used) AS total_herbs
           FROM milling_experiments WHERE process = ?
           GROUP BY herb_item_id ORDER BY herb_name""",
        conn, params=(_check_process(process),),
    )


# Milling-named wrappers (the original Stage 2 API).

def add_milling_experiment(conn: sqlite3.Connection, herb_item_id: int, herbs_used: int,
                           outputs: list, herb_name: Optional[str] = None,
                           performed_at: Optional[str] = None, notes: str = "",
                           herb_cost_each_copper: Optional[int] = None,
                           observed_prices: Optional[dict] = None) -> int:
    if herbs_used is None or herbs_used <= 0:
        raise ValueError("Herbs used must be greater than zero.")
    return add_experiment(conn, "milling", herb_item_id, herbs_used, outputs, herb_name,
                          performed_at, notes, herb_cost_each_copper, observed_prices)


def list_milling_experiments(conn: sqlite3.Connection,
                             herb_item_id: Optional[int] = None) -> list:
    return list_experiments(conn, "milling", herb_item_id)


def delete_milling_experiment(conn: sqlite3.Connection, experiment_id: int) -> None:
    delete_experiment(conn, experiment_id)


def list_milling_herbs(conn: sqlite3.Connection) -> pd.DataFrame:
    return list_experiment_inputs(conn, "milling")


# --- Recipes -------------------------------------------------------------------------------

def save_recipe(conn: sqlite3.Connection, recipe) -> str:
    """Insert or replace a recipe (matched on ``recipe_key``). Returns ``new`` or ``replaced``.

    ``recipe`` is a ``goldmaker.recipes.Recipe``. Reagents are replaced wholesale.
    """
    now = utc_now_iso()
    existing = conn.execute("SELECT id FROM recipes WHERE recipe_key = ?", (recipe.key,)).fetchone()
    fields = (recipe.name, recipe.crafted_item_id, recipe.crafted_item_name, recipe.output_quantity,
              recipe.profession, recipe.required_skill, recipe.expansion, recipe.game_patch,
              recipe.game_recipe_id, recipe.source, recipe.last_verified, recipe.category, recipe.notes)
    with conn:
        if existing is None:
            cursor = conn.execute(
                """INSERT INTO recipes (recipe_key, name, crafted_item_id, crafted_item_name,
                   output_quantity, profession, required_skill, expansion, game_patch,
                   game_recipe_id, source, last_verified, category, notes, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (recipe.key, *fields, now, now))
            recipe_id = cursor.lastrowid
        else:
            recipe_id = existing["id"]
            conn.execute(
                """UPDATE recipes SET name=?, crafted_item_id=?, crafted_item_name=?, output_quantity=?,
                   profession=?, required_skill=?, expansion=?, game_patch=?, game_recipe_id=?,
                   source=?, last_verified=?, category=?, notes=?, updated_at=? WHERE id=?""",
                (*fields, now, recipe_id))
            conn.execute("DELETE FROM recipe_reagents WHERE recipe_id = ?", (recipe_id,))
        conn.executemany(
            """INSERT INTO recipe_reagents (recipe_id, slot, item_id, item_name, quantity,
               quality_label, auctionable, savings_eligible) VALUES (?,?,?,?,?,?,?,?)""",
            [(recipe_id, r.slot, r.item_id, r.item_name, r.quantity, r.quality_label,
              int(r.auctionable), int(r.savings_eligible)) for r in recipe.reagents])
    return "new" if existing is None else "replaced"


def list_recipe_rows(conn: sqlite3.Connection, recipe_key: Optional[str] = None) -> list:
    """Recipes as dicts (with a ``reagents`` list), ordered by name."""
    query, params = "SELECT * FROM recipes", ()
    if recipe_key is not None:
        query, params = query + " WHERE recipe_key = ?", (recipe_key,)
    rows = [dict(r) for r in conn.execute(query + " ORDER BY name COLLATE NOCASE, recipe_key", params)]
    for row in rows:
        row["reagents"] = [dict(r) for r in conn.execute(
            "SELECT slot, item_id, item_name, quantity, quality_label, auctionable, savings_eligible "
            "FROM recipe_reagents WHERE recipe_id = ? ORDER BY slot, item_id", (row["id"],))]
    return rows


def recipe_keys(conn: sqlite3.Connection) -> set:
    return {r["recipe_key"] for r in conn.execute("SELECT recipe_key FROM recipes")}


def delete_recipe(conn: sqlite3.Connection, recipe_key: str) -> None:
    with conn:
        conn.execute("DELETE FROM recipes WHERE recipe_key = ?", (recipe_key,))


# --- Item resources (stock on hand, opportunity cost) ---------------------------------------

def upsert_item_resource(conn: sqlite3.Connection, item_id: int, name: Optional[str] = None,
                         on_hand: Optional[int] = None,
                         opportunity_cost_copper: Optional[int] = None, notes: str = "") -> None:
    if on_hand is not None and on_hand < 0:
        raise ValueError("Units on hand cannot be negative.")
    if opportunity_cost_copper is not None and opportunity_cost_copper < 0:
        raise ValueError("Opportunity cost cannot be negative.")
    with conn:
        conn.execute(
            """INSERT INTO item_resources (item_id, name, on_hand, opportunity_cost_copper, notes, updated_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(item_id) DO UPDATE SET
                 name = COALESCE(excluded.name, item_resources.name),
                 on_hand = excluded.on_hand,
                 opportunity_cost_copper = excluded.opportunity_cost_copper,
                 notes = excluded.notes, updated_at = excluded.updated_at""",
            (int(item_id), name, on_hand, opportunity_cost_copper, notes, utc_now_iso()))


def list_item_resources(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM item_resources ORDER BY name COLLATE NOCASE, item_id", conn)


def delete_item_resource(conn: sqlite3.Connection, item_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM item_resources WHERE item_id = ?", (int(item_id),))


# --- Recipe output quality tiers ------------------------------------------------------------------

def save_output_tiers(conn: sqlite3.Connection, recipe_key: str, tiers: list) -> None:
    """Replace a recipe's quality tiers. ``tiers`` = [{"tier_label", "item_id", "item_name"}]."""
    row = conn.execute("SELECT id FROM recipes WHERE recipe_key = ?", (recipe_key,)).fetchone()
    if row is None:
        raise ValueError(f"No recipe with key '{recipe_key}'.")
    labels = [str(t["tier_label"]).strip() for t in tiers]
    if any(not label for label in labels):
        raise ValueError("Every quality tier needs a label (e.g. 'Silver').")
    if len(set(labels)) != len(labels):
        raise ValueError("Quality tier labels must be unique within a recipe.")
    with conn:
        conn.execute("DELETE FROM recipe_output_tiers WHERE recipe_id = ?", (row["id"],))
        conn.executemany(
            "INSERT INTO recipe_output_tiers (recipe_id, tier_label, item_id, item_name, sort_order) "
            "VALUES (?,?,?,?,?)",
            [(row["id"], label, int(t["item_id"]), t.get("item_name"), n)
             for n, (label, t) in enumerate(zip(labels, tiers))])


def list_output_tiers(conn: sqlite3.Connection, recipe_key: str) -> list:
    return [dict(r) for r in conn.execute(
        """SELECT t.tier_label, t.item_id, t.item_name FROM recipe_output_tiers t
           JOIN recipes r ON r.id = t.recipe_id WHERE r.recipe_key = ? ORDER BY t.sort_order""",
        (recipe_key,))]


# --- Reference yields (outside sources, never pooled with experiments) -------------------------------

def save_reference_yields(conn: sqlite3.Connection, process: str, input_item_id: int,
                          input_name: Optional[str], rows: list, source: str) -> None:
    """Replace the reference yields of one input item.

    ``rows`` = [{"item_id", "name", "chance_pct", "avg_quantity", "units_per_operation"}].
    The yield per input unit is chance x average quantity / units per operation.
    """
    from .conversion import yield_from_drop
    _check_process(process)
    if not (source or "").strip():
        raise ValueError("Say where these numbers come from (e.g. 'Wowhead, 2026-10-13').")
    ids = [int(r["item_id"]) for r in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("Each output item can appear only once.")
    values = []
    now = utc_now_iso()
    for r in rows:
        per_unit = yield_from_drop(r["chance_pct"], r["avg_quantity"], r["units_per_operation"])
        values.append((process, int(input_item_id), input_name, int(r["item_id"]), r.get("name"),
                       float(r["chance_pct"]), float(r["avg_quantity"]), int(r["units_per_operation"]),
                       per_unit, source.strip(), now))
    with conn:
        conn.execute("DELETE FROM reference_yields WHERE process = ? AND input_item_id = ?",
                     (process, int(input_item_id)))
        conn.executemany(
            """INSERT INTO reference_yields (process, input_item_id, input_name, output_item_id, output_name,
               chance_pct, avg_quantity, units_per_operation, yield_per_unit, source, recorded_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""", values)


def list_reference_yields(conn: sqlite3.Connection, process: str,
                          input_item_id: Optional[int] = None) -> list:
    query, params = "SELECT * FROM reference_yields WHERE process = ?", [_check_process(process)]
    if input_item_id is not None:
        query, params = query + " AND input_item_id = ?", params + [int(input_item_id)]
    return [dict(r) for r in conn.execute(query + " ORDER BY input_item_id, output_item_id", params)]


def list_reference_inputs(conn: sqlite3.Connection, process: str) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT input_item_id, MAX(input_name) AS input_name, COUNT(*) AS outputs, MAX(source) AS source,
                  MAX(recorded_at) AS recorded_at
           FROM reference_yields WHERE process = ? GROUP BY input_item_id ORDER BY input_name""",
        conn, params=(_check_process(process),))


def delete_reference_yields(conn: sqlite3.Connection, process: str, input_item_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM reference_yields WHERE process = ? AND input_item_id = ?",
                     (_check_process(process), int(input_item_id)))


# --- Characters from Blizzard's Profile API -----------------------------------------------------

def upsert_character_basic(conn: sqlite3.Connection, character_key: str, name: str, realm: str,
                           class_name: Optional[str] = None, level: Optional[int] = None,
                           faction: Optional[str] = None, source: str = "Blizzard API") -> None:
    """Add a character if missing; otherwise only fill in empty class / level / faction.

    Never touches ``snapshot_at``, so CharExporter's snapshot-ordering rules are unaffected.
    """
    with conn:
        conn.execute(
            """INSERT INTO characters (character_key, name, realm, class_name, level, faction, snapshot_at,
                                       imported_at, source)
               VALUES (?,?,?,?,?,?,NULL,?,?)
               ON CONFLICT(character_key) DO UPDATE SET
                 class_name = COALESCE(characters.class_name, excluded.class_name),
                 level = CASE WHEN excluded.level IS NOT NULL AND (characters.level IS NULL
                              OR excluded.level > characters.level) THEN excluded.level ELSE characters.level END,
                 faction = COALESCE(characters.faction, excluded.faction)""",
            (character_key, name, realm, class_name, level, faction, utc_now_iso(), source))


def save_blizzard_professions(conn: sqlite3.Connection, character_key: str, tiers: list, known: list) -> None:
    """Replace a character's Blizzard profession tiers and known recipes.

    ``tiers`` = [{"profession", "tier_id", "tier_name", "skill_points", "max_skill_points"}]
    ``known`` = [{"recipe_id", "recipe_name", "tier_id"}]
    """
    now = utc_now_iso()
    with conn:
        conn.execute("DELETE FROM character_profession_tiers WHERE character_key = ?", (character_key,))
        conn.execute("DELETE FROM character_known_recipes WHERE character_key = ?", (character_key,))
        conn.executemany(
            "INSERT INTO character_profession_tiers VALUES (?,?,?,?,?,?,?)",
            [(character_key, t["profession"], int(t["tier_id"]), t.get("tier_name"), t.get("skill_points"),
              t.get("max_skill_points"), now) for t in tiers])
        conn.executemany(
            "INSERT OR REPLACE INTO character_known_recipes VALUES (?,?,?,?,?)",
            [(character_key, int(k["recipe_id"]), k.get("recipe_name"), k.get("tier_id"), now) for k in known])


def list_profession_tiers(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query("SELECT * FROM character_profession_tiers ORDER BY character_key, profession, tier_id",
                             conn)


def known_recipe_holders(conn: sqlite3.Connection) -> dict:
    """recipe_id -> sorted list of character keys that know it (from the Profile API)."""
    holders: dict = {}
    for row in conn.execute("SELECT recipe_id, character_key FROM character_known_recipes ORDER BY character_key"):
        holders.setdefault(row["recipe_id"], []).append(row["character_key"])
    return holders
