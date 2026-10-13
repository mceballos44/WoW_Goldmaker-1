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

-- Milling experiments: one row per batch, plus the items it produced.
CREATE TABLE IF NOT EXISTS milling_experiments (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
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
    PRIMARY KEY (character_key, profession)
);
"""

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
    return conn


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
          AND (d.scope IN ('commodities', 'region')
               OR (d.scope = 'realm' AND d.realm_slug = ?))
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
                            notes: str = "") -> None:
    for label, value in (("crafting skill", crafting_skill), ("multicraft", multicraft_pct),
                         ("resourcefulness", resourcefulness_pct), ("ingenuity", ingenuity_pct)):
        if value is not None and value < 0:
            raise ValueError(f"{label} cannot be negative.")
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO profession_stats
               (character_key, profession, crafting_skill, multicraft_pct,
                resourcefulness_pct, ingenuity_pct, notes, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (character_key, profession, crafting_skill, multicraft_pct,
             resourcefulness_pct, ingenuity_pct, notes, utc_now_iso()),
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


# --- Milling experiments -------------------------------------------------------------

def add_milling_experiment(conn: sqlite3.Connection, herb_item_id: int, herbs_used: int,
                           outputs: list, herb_name: Optional[str] = None,
                           performed_at: Optional[str] = None, notes: str = "",
                           herb_cost_each_copper: Optional[int] = None,
                           observed_prices: Optional[dict] = None) -> int:
    """Record one milling batch and return its id.

    ``outputs`` is a list of ``{"item_id", "name", "quantity"}`` dicts. Duplicate
    item ids are summed. Quantity 0 is allowed (an item that was expected but
    did not drop is real information).
    """
    if herbs_used is None or herbs_used <= 0:
        raise ValueError("Herbs used must be greater than zero.")
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
               (herb_item_id, herb_name, herbs_used, performed_at, notes,
                herb_cost_each_copper, observed_prices_json, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (int(herb_item_id), herb_name, int(herbs_used), performed_at, notes,
             herb_cost_each_copper,
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


def list_milling_experiments(conn: sqlite3.Connection,
                             herb_item_id: Optional[int] = None) -> list:
    """Experiments (newest first) as dicts, each with an ``outputs`` list."""
    query = "SELECT * FROM milling_experiments"
    params: tuple = ()
    if herb_item_id is not None:
        query += " WHERE herb_item_id = ?"
        params = (int(herb_item_id),)
    experiments = [dict(r) for r in conn.execute(query + " ORDER BY id DESC", params)]
    for exp in experiments:
        exp["outputs"] = [dict(r) for r in conn.execute(
            "SELECT item_id, item_name, quantity FROM milling_experiment_outputs "
            "WHERE experiment_id = ? ORDER BY item_id", (exp["id"],))]
        raw = exp.pop("observed_prices_json")
        exp["observed_prices"] = {int(k): v for k, v in json.loads(raw).items()} if raw else {}
    return experiments


def delete_milling_experiment(conn: sqlite3.Connection, experiment_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM milling_experiments WHERE id = ?", (int(experiment_id),))


def list_milling_herbs(conn: sqlite3.Connection) -> pd.DataFrame:
    """Herbs that have experiments, with counts."""
    return pd.read_sql_query(
        """SELECT herb_item_id, MAX(herb_name) AS herb_name,
                  COUNT(*) AS experiments, SUM(herbs_used) AS total_herbs
           FROM milling_experiments GROUP BY herb_item_id ORDER BY herb_name""",
        conn,
    )
