"""Parse profession data exported by the CharExporter addon (zwinout/CharExporter).

Supported inputs (all produced by the addon itself):

1. ``SavedVariables\\CharExporter.lua`` - written on logout when "Auto-save on
   Logout" is ticked. Contains ``CharExporterDB.global["Name-Realm"]`` for every
   alt that has logged out with auto-save on. This is the only source that
   covers all alts at once.
2. JSON copied from the ``/charexport`` window (one character).
3. CSV copied from the ``/charexport`` window (one character,
   ``Category,Key,Value`` rows).

What the addon DOES export for each profession (verified from its source):
    name, skill level / max, concentration (current / max at export time),
    knowledge points *spent* (sum of ranks in the specialization tree),
    specialization tabs/paths/perks, equipped profession gear (name, item level,
    link), and a recipe count.

What it does NOT export: Multicraft, Resourcefulness, Ingenuity or crafting
speed percentages. Those must be entered by hand (see ``database`` ->
``profession_stats``).

Cautions kept with the data:
* ``recipes_known`` comes from an API call made without naming the profession,
  so it is stored but should not be trusted for calculations.
* Concentration is a snapshot; it changes over time.
* Enum meanings (Completed / Earned / Unlocked ...) are the addon's own
  interpretation, copied from its formatter.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional

from .lua_parser import LuaParseError, parse_lua_assignments

SAVED_VARIABLE_NAME = "CharExporterDB"


class CharExporterError(ValueError):
    """The text is not a usable CharExporter export."""


@dataclass
class ProfessionSnapshot:
    name: str
    skill_level: Optional[int] = None
    max_skill_level: Optional[int] = None
    concentration: Optional[float] = None
    concentration_max: Optional[float] = None
    knowledge_points_spent: Optional[int] = None
    recipes_known: Optional[int] = None            # unverified, see module docstring
    equipment: list = field(default_factory=list)  # [{name, ilvl, slot, link}]
    specializations: list = field(default_factory=list)  # nested dicts, see _spec_*


@dataclass
class CharacterSnapshot:
    name: str
    realm: str
    class_name: Optional[str] = None
    spec: Optional[str] = None
    level: Optional[int] = None
    race: Optional[str] = None
    faction: Optional[str] = None
    snapshot_at: Optional[datetime] = None          # UTC, when the addon gathered the data
    professions: list = field(default_factory=list)
    professions_included: bool = True               # False if the Professions category was off
    source: str = ""
    warnings: list = field(default_factory=list)

    @property
    def character_key(self) -> str:
        return f"{self.name}-{self.realm}"

    @property
    def has_identity(self) -> bool:
        return bool(self.name.strip()) and bool(self.realm.strip())


@dataclass
class ParseResult:
    characters: list
    source_format: str                      # "savedvariables" | "json" | "csv"
    warnings: list = field(default_factory=list)


def apply_identity(snapshot: CharacterSnapshot, name: str, realm: str) -> CharacterSnapshot:
    return replace(snapshot, name=name.strip(), realm=realm.strip())


# --- small converters -------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number     # NaN -> None


def _int(value: Any) -> Optional[int]:
    number = _num(value)
    return None if number is None or number in (float("inf"), float("-inf")) else int(number)


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _unix_to_utc(value: Any) -> Optional[datetime]:
    seconds = _num(value)
    if seconds is None or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


# State enums, copied from the addon's own CSV formatter (Formatters.lua).
def _tab_state(value: Any) -> str:
    if isinstance(value, str):
        return value
    return {1: "Unlocked", 2: "Unlockable"}.get(_int(value), "Locked")


def _path_state(value: Any) -> str:
    if isinstance(value, str):
        return value
    return {2: "Completed", 1: "Progressing"}.get(_int(value), "Locked")


def _perk_state(value: Any) -> str:
    if isinstance(value, str):
        return value
    return {2: "Earned", 1: "Pending"}.get(_int(value), "Unearned")


# --- Lua / JSON record -> snapshot -------------------------------------------------

def _spec_node(raw: Any) -> dict:
    node = _as_dict(raw)
    return {
        "name": _text(node.get("name")) or "",
        "current_rank": _int(node.get("currentRank")) or 0,
        "max_rank": _int(node.get("maxRank")) or 0,
        "state": _path_state(node.get("state")),
        "perks": [
            {"name": _text(_as_dict(p).get("name")) or "",
             "state": _perk_state(_as_dict(p).get("state")),
             "is_major": bool(_as_dict(p).get("isMajor"))}
            for p in _as_list(node.get("perks"))
        ],
        "children": [_spec_node(c) for c in _as_list(node.get("children"))],
    }


def _spec_tab(raw: Any) -> dict:
    tab = _as_dict(raw)
    return {
        "name": _text(tab.get("name")) or "",
        "state": _tab_state(tab.get("state")),
        "paths": [_spec_node(p) for p in _as_list(tab.get("paths"))],
    }


def _profession_from_dict(raw: dict) -> Optional[ProfessionSnapshot]:
    name = _text(raw.get("name"))
    if not name:
        return None
    return ProfessionSnapshot(
        name=name,
        skill_level=_int(raw.get("skillLevel")),
        max_skill_level=_int(raw.get("maxSkillLevel")),
        concentration=_num(raw.get("concentration")),
        concentration_max=_num(raw.get("concentrationMax")),
        knowledge_points_spent=_int(raw.get("knowledgePoints")),
        recipes_known=_int(raw.get("recipesKnown")),
        equipment=[
            {"name": _text(_as_dict(e).get("name")) or "Unknown",
             "ilvl": _int(_as_dict(e).get("ilvl")),
             "slot": _text(_as_dict(e).get("slot")),
             "link": _text(_as_dict(e).get("link"))}
            for e in _as_list(raw.get("equipment"))
        ],
        specializations=[_spec_tab(t) for t in _as_list(raw.get("specializations"))],
    )


def _character_from_record(record: dict, key_hint: Optional[str], source: str) -> CharacterSnapshot:
    basic = _as_dict(record.get("basic"))
    name = _text(basic.get("name")) or ""
    realm = _text(basic.get("realm")) or ""
    name = "" if name == "Unknown" else name
    realm = "" if realm == "Unknown" else realm
    if (not name or not realm) and key_hint and "-" in key_hint:
        hint_name, hint_realm = key_hint.split("-", 1)   # realm may itself contain '-'
        name, realm = name or hint_name.strip(), realm or hint_realm.strip()

    warnings: list = []
    professions_included = "professions" in record
    professions = []
    for raw in _as_list(record.get("professions")):
        profession = _profession_from_dict(_as_dict(raw))
        if profession is None:
            warnings.append("A profession entry had no name and was skipped.")
        else:
            professions.append(profession)
    if not professions_included:
        warnings.append("No profession data in this export. Tick the 'Professions' "
                        "category in CharExporter and export again.")

    snapshot_at = _unix_to_utc(record.get("timestamp"))
    if snapshot_at is None:
        warnings.append("No export timestamp found; the age of this data is unknown.")

    return CharacterSnapshot(
        name=name, realm=realm,
        class_name=_text(basic.get("class")), spec=_text(basic.get("spec")),
        level=_int(basic.get("level")), race=_text(basic.get("race")),
        faction=_text(basic.get("faction")),
        snapshot_at=snapshot_at, professions=professions,
        professions_included=professions_included, source=source, warnings=warnings,
    )


# --- format parsers ---------------------------------------------------------------------

def parse_savedvariables(text: str) -> ParseResult:
    try:
        assignments = parse_lua_assignments(text)
    except LuaParseError as exc:
        raise CharExporterError(f"Could not read the Lua file: {exc}") from exc
    if SAVED_VARIABLE_NAME not in assignments:
        raise CharExporterError(
            f"This Lua file has no '{SAVED_VARIABLE_NAME}' variable, so it is not a "
            "CharExporter SavedVariables file.")

    saved = _as_dict(assignments[SAVED_VARIABLE_NAME])
    records = _as_dict(saved.get("global"))
    result = ParseResult(characters=[], source_format="savedvariables")
    ignored = []
    for key, record in records.items():
        if isinstance(record, dict) and any(k in record for k in ("timestamp", "professions", "basic")):
            result.characters.append(_character_from_record(record, str(key), "SavedVariables"))
        else:
            ignored.append(str(key))
    if ignored:
        result.warnings.append(f"{len(ignored)} unrecognised entr{'y' if len(ignored) == 1 else 'ies'} ignored: "
                               + ", ".join(ignored[:10]))
    if not records and saved:
        result.warnings.append("The file has no 'global' section with characters. Top-level keys found: "
                               + ", ".join(str(k) for k in list(saved)[:10]))
    if not result.characters:
        result.warnings.append(
            "No saved characters found. In-game: /charexport -> tick 'Auto-save on Logout', "
            "then log out of each alt (or /reload) so the file is written.")
    return result


def parse_json_export(text: str) -> ParseResult:
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise CharExporterError(f"Could not read the JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise CharExporterError("Expected a JSON object from CharExporter.")
    snapshot = _character_from_record(data, None, "JSON paste")
    return ParseResult([snapshot], "json")


_SKILL_RE = re.compile(r"Skill:\s*(\d+)\s*/\s*(\d+)")
_CONC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_GEAR_RE = re.compile(r"^(.*?)(?:\s*\((\d+)\))?$")
_PATH_RE = re.compile(r"^(\d+)\s*/\s*(\d+)\s*\((.*)\)\s*$")


def parse_csv_export(text: str) -> ParseResult:
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    if not rows or [c.strip() for c in rows[0][:3]] != ["Category", "Key", "Value"]:
        raise CharExporterError("Expected a CharExporter CSV starting with 'Category,Key,Value'.")

    basic: dict = {}
    timestamp = None
    profs: dict = {}            # profession name -> raw dict (insertion ordered)
    nodes: dict = {}            # tuple(key parts) -> spec node dict

    def prof(name: str) -> dict:
        # The CSV formatter omits concentration / knowledge points / recipes when they are 0,
        # so a missing row means 0 here (verified in the addon's Formatters.lua).
        return profs.setdefault(name, {
            "name": name, "concentration": 0, "concentrationMax": 0,
            "knowledgePoints": 0, "recipesKnown": 0, "equipment": [], "specializations": []})

    def tab_of(parts: list) -> Optional[dict]:
        for tab in prof(parts[0])["specializations"]:
            if tab["name"] == parts[1]:
                return tab
        return None

    for row in rows[1:]:
        if len(row) < 3:
            continue
        category, key, value = row[0].strip(), row[1], row[2]
        parts = [p.strip() for p in key.split(" >> ")]

        if category == "Metadata" and key == "ExportTime":
            try:   # the addon writes the client's LOCAL time without a time zone
                timestamp = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").astimezone(timezone.utc)
            except ValueError:
                timestamp = None
        elif category == "Basic":
            basic[key] = value
        elif category == "Profession":
            m = _SKILL_RE.search(value)
            p = prof(key)
            if m:
                p["skillLevel"], p["maxSkillLevel"] = int(m.group(1)), int(m.group(2))
        elif category == "Concentration":
            m = _CONC_RE.search(value)
            if m:
                prof(key)["concentration"], prof(key)["concentrationMax"] = int(m.group(1)), int(m.group(2))
        elif category == "Knowledge Points":
            prof(key)["knowledgePoints"] = _int(value)
        elif category == "Recipes Known":
            prof(key)["recipesKnown"] = _int(value)
        elif category == "Profession Gear" and len(parts) == 2:
            m = _GEAR_RE.match(value)
            prof(parts[0])["equipment"].append(
                {"name": (m.group(1) if m else value), "ilvl": int(m.group(2)) if m and m.group(2) else None,
                 "slot": parts[1]})
        elif category == "Spec Tab" and len(parts) == 2:
            prof(parts[0])["specializations"].append({"name": parts[1], "state": value, "paths": []})
        elif category == "Spec Path" and len(parts) >= 3:
            m = _PATH_RE.match(value)
            node = {"name": parts[-1], "currentRank": int(m.group(1)) if m else 0,
                    "maxRank": int(m.group(2)) if m else 0, "state": m.group(3) if m else "Locked",
                    "perks": [], "children": []}
            nodes[tuple(parts)] = node
            if len(parts) == 3:
                tab = tab_of(parts)
                if tab is not None:
                    tab["paths"].append(node)
            else:
                parent = nodes.get(tuple(parts[:-1]))
                if parent is not None:
                    parent["children"].append(node)
        elif category == "Spec Perk" and len(parts) >= 4:
            parent = nodes.get(tuple(parts[:-1]))
            if parent is not None:
                parent["perks"].append({"name": parts[-1], "state": value, "isMajor": False})

    record: dict = {"basic": basic}
    if any(row and row[0].strip() == "Profession" for row in rows[1:]):
        record["professions"] = list(profs.values())   # else: category was not exported
    snapshot = _character_from_record(record, None, "CSV paste")
    snapshot.snapshot_at = timestamp
    snapshot.warnings = [w for w in snapshot.warnings if "No export timestamp" not in w]
    if timestamp is None:
        snapshot.warnings.append("No export time found; the age of this data is unknown.")
    else:
        snapshot.warnings.append("CSV export times carry no time zone; the time was read as this "
                                 "computer's local time.")
    return ParseResult([snapshot], "csv")


def parse_any(text: str) -> ParseResult:
    """Detect the format and parse it."""
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped:
        raise CharExporterError("The input is empty.")
    if stripped.startswith("{"):
        return parse_json_export(stripped)
    if stripped.startswith("Category,Key,Value") or stripped.startswith('"Category"'):
        return parse_csv_export(stripped)
    if SAVED_VARIABLE_NAME in stripped[:5000] or stripped.startswith("--"):
        return parse_savedvariables(stripped)
    raise CharExporterError(
        "Unrecognised format. Expected CharExporter.lua (SavedVariables), JSON, or "
        "CSV from the /charexport window.")
