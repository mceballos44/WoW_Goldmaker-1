"""Character/profession profiles used by the calculators.

A profile merges two kinds of data and keeps them clearly apart:

* IMPORTED (from CharExporter): skill as exported, concentration snapshot,
  knowledge points spent, specialization tree, profession gear.
* MANUAL (typed in by the user): crafting skill, Multicraft %, Resourcefulness %,
  Ingenuity %. CharExporter does not export these.

A stat the user has not entered is ``None`` - never silently 0 - so a calculator
can refuse to run (or warn) instead of assuming "0% Multicraft".
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from . import database

MANUAL_STAT_FIELDS = {
    "crafting_skill": "Crafting skill",
    "multicraft_pct": "Multicraft %",
    "resourcefulness_pct": "Resourcefulness %",
    "ingenuity_pct": "Ingenuity %",
}
# Measured sizes of the Resourcefulness / Multicraft effects. Optional: they are only
# needed to model those effects, so they are not listed as "missing" stats.
MECHANIC_SIZE_FIELDS = {
    "resourcefulness_saved_pct": "Resourcefulness: share of eligible reagents saved %",
    "multicraft_extra_pct": "Multicraft: extra output % of base",
}
SNAPSHOT_STALE_DAYS = 7


@dataclass(frozen=True)
class ProfessionProfile:
    character_key: str
    profession: str
    # imported
    skill_level_exported: Optional[int]
    max_skill_level_exported: Optional[int]
    concentration_at_snapshot: Optional[float]
    concentration_max: Optional[float]
    knowledge_points_spent: Optional[int]
    snapshot_at: Optional[datetime]
    # manual (None = not entered)
    crafting_skill: Optional[float] = None
    multicraft_pct: Optional[float] = None
    resourcefulness_pct: Optional[float] = None
    ingenuity_pct: Optional[float] = None
    resourcefulness_saved_pct: Optional[float] = None
    multicraft_extra_pct: Optional[float] = None
    notes: str = ""
    stats_updated_at: Optional[datetime] = None
    missing_stats: tuple = field(default_factory=tuple)

    @property
    def label(self) -> str:
        return f"{self.character_key} - {self.profession}"


def _clean(value):
    return None if value is None or (isinstance(value, float) and value != value) else value


def _dt(value) -> Optional[datetime]:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def load_profile(conn, character_key: str, profession: str) -> Optional[ProfessionProfile]:
    """Return the merged profile, or None if that character/profession was never imported."""
    row = conn.execute(
        "SELECT * FROM character_professions WHERE character_key = ? AND profession = ?",
        (character_key, profession),
    ).fetchone()
    if row is None:
        return None
    stats = database.get_profession_stats(conn, character_key, profession) or {}
    values = {name: _clean(stats.get(name)) for name in MANUAL_STAT_FIELDS}
    sizes = {name: _clean(stats.get(name)) for name in MECHANIC_SIZE_FIELDS}
    return ProfessionProfile(
        character_key=character_key,
        profession=profession,
        skill_level_exported=row["skill_level"],
        max_skill_level_exported=row["max_skill_level"],
        concentration_at_snapshot=row["concentration"],
        concentration_max=row["concentration_max"],
        knowledge_points_spent=row["knowledge_points_spent"],
        snapshot_at=_dt(row["snapshot_at"]),
        notes=stats.get("notes") or "",
        stats_updated_at=_dt(stats.get("updated_at")),
        missing_stats=tuple(MANUAL_STAT_FIELDS[n] for n, v in values.items() if v is None),
        **values, **sizes,
    )


def list_profile_choices(conn, profession: Optional[str] = None) -> list:
    """(character_key, profession) pairs available for selection.

    ``profession`` filters case-insensitively by substring, so "Alchemy" also finds
    a profession exported as e.g. "Khaz Algar Alchemy". Names follow the game client's language.
    """
    df = database.list_character_professions(conn)
    pairs = list(zip(df["character_key"], df["profession"]))
    if profession:
        wanted = profession.lower()
        pairs = [(c, p) for c, p in pairs if wanted in str(p).lower()]
    return pairs


def profile_warnings(profile: ProfessionProfile, now: Optional[datetime] = None) -> list:
    """Human-readable cautions to show next to any calculation using this profile."""
    now = now or datetime.now(timezone.utc)
    warnings = []
    if profile.missing_stats:
        warnings.append("Not entered yet: " + ", ".join(profile.missing_stats)
                        + ". These are not exported by CharExporter; type them in from the "
                          "profession window.")
    if profile.snapshot_at is None:
        warnings.append("The import has no snapshot time, so its age is unknown.")
    else:
        age_days = (now - profile.snapshot_at).total_seconds() / 86400
        if age_days > SNAPSHOT_STALE_DAYS:
            warnings.append(f"Imported data is {age_days:.0f} days old; gear, knowledge points "
                            "and concentration may have changed.")
    if (profile.stats_updated_at is not None and profile.snapshot_at is not None
            and profile.snapshot_at > profile.stats_updated_at):
        warnings.append("Profession stats were entered before the latest import; re-check them "
                        "if your gear or spec changed.")
    return warnings


def specializations_to_rows(specializations: list) -> list:
    """Flatten the specialization tree for display (one row per tab/path)."""
    rows = []

    def walk(node: dict, tab: str, depth: int) -> None:
        perks = node.get("perks", [])
        earned = sum(1 for p in perks if p.get("state") == "Earned")
        rows.append({
            "Tab": tab,
            "Path": ("   " * depth) + ("↳ " if depth else "") + (node.get("name") or "(unnamed)"),
            "Rank": node.get("current_rank", 0),
            "Max rank": node.get("max_rank", 0),
            "State": node.get("state"),
            "Perks earned": f"{earned}/{len(perks)}" if perks else "",
        })
        for child in node.get("children", []):
            walk(child, tab, depth + 1)

    for tab in specializations or []:
        if not tab.get("paths"):
            rows.append({"Tab": tab.get("name"), "Path": "(tab only)", "Rank": 0, "Max rank": 0,
                         "State": tab.get("state"), "Perks earned": ""})
        for path in tab.get("paths", []):
            walk(path, tab.get("name"), 0)
    return rows


def parse_json_column(value) -> list:
    if not value or (isinstance(value, float) and value != value):
        return []
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return []
    return data if isinstance(data, list) else []


def snapshot_age_text(snapshot_at: Optional[datetime], now: Optional[datetime] = None) -> str:
    if snapshot_at is None or pd.isna(snapshot_at):
        return "unknown"
    now = now or datetime.now(timezone.utc)
    hours = (now - snapshot_at).total_seconds() / 3600
    if hours < 1:
        return f"{max(int(hours * 60), 0)} min ago"
    if hours < 48:
        return f"{hours:.1f} h ago"
    return f"{hours / 24:.1f} days ago"
