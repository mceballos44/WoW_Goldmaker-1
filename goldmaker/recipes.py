"""Recipe model plus CSV / JSON import and export.

NO RECIPE DATA LIVES IN THIS CODE. Recipes (reagents, quantities, outputs) come
only from files the user imports or edits by hand, so every quantity is
traceable to a source the user chose. Each recipe carries ``source``,
``game_patch`` and ``last_verified`` so stale or unverified data is visible.

File format (one row per reagent; recipe fields repeat on each row of a recipe)
-----------------------------------------------------------------------------
recipe_key        required, unique text id you choose (groups the rows)
name              required
crafted_item_id   or crafted_item_name (looked up in your cached prices)
output_quantity   default 1
profession, required_skill, expansion, game_patch, game_recipe_id,
source, last_verified (YYYY-MM-DD), category, notes
reagent_slot      reagents sharing a slot are ALTERNATIVES (e.g. quality tiers);
                  blank = its own slot
reagent_item_id   or reagent_name (looked up in your cached prices)
reagent_quantity  required
quality_label     free text, e.g. "Q1" / "Silver"
auctionable       true/false, default true. false = not on the AH (see Resources)
savings_eligible  true/false, default false. Only true reagents can benefit from
                  Resourcefulness-style savings; nothing is assumed eligible.

JSON uses the same field names, with a "reagents" list per recipe whose entries
use item_id / item_name / quantity / slot / quality_label / auctionable /
savings_eligible (the ``reagent_`` prefixed names are accepted too).
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

CSV_COLUMNS = [
    "recipe_key", "name", "crafted_item_id", "crafted_item_name", "output_quantity",
    "profession", "required_skill", "expansion", "game_patch", "game_recipe_id",
    "source", "last_verified", "category", "notes",
    "reagent_slot", "reagent_item_id", "reagent_name", "reagent_quantity",
    "quality_label", "auctionable", "savings_eligible",
]
_HEADER_FIELDS = CSV_COLUMNS[:14]
_TRUE = {"1", "true", "t", "yes", "y", "x"}
_FALSE = {"0", "false", "f", "no", "n"}

# name -> [(item_id, name), ...]; used to turn item names into IDs.
Lookup = Callable[[str], list]


@dataclass(frozen=True)
class Reagent:
    slot: int
    item_id: int
    quantity: int
    item_name: Optional[str] = None
    quality_label: str = ""
    auctionable: bool = True
    savings_eligible: bool = False


@dataclass(frozen=True)
class Recipe:
    key: str
    name: str
    crafted_item_id: int
    reagents: tuple
    output_quantity: int = 1
    crafted_item_name: Optional[str] = None
    profession: Optional[str] = None
    required_skill: Optional[int] = None
    expansion: Optional[str] = None
    game_patch: Optional[str] = None
    game_recipe_id: Optional[int] = None
    source: str = ""
    last_verified: Optional[str] = None
    category: Optional[str] = None
    notes: str = ""
    id: Optional[int] = None

    def slots(self) -> dict:
        """slot -> list of alternative reagents, in slot order."""
        grouped: dict = {}
        for reagent in self.reagents:
            grouped.setdefault(reagent.slot, []).append(reagent)
        return dict(sorted(grouped.items()))

    @staticmethod
    def from_row(row: dict) -> "Recipe":
        """Build from the dict shape returned by ``database.list_recipe_rows``."""
        reagents = tuple(
            Reagent(slot=r["slot"], item_id=r["item_id"], quantity=r["quantity"],
                    item_name=r.get("item_name"), quality_label=r.get("quality_label") or "",
                    auctionable=bool(r.get("auctionable", 1)),
                    savings_eligible=bool(r.get("savings_eligible", 0)))
            for r in row["reagents"])
        return Recipe(
            id=row.get("id"), key=row["recipe_key"], name=row["name"],
            crafted_item_id=row["crafted_item_id"], crafted_item_name=row.get("crafted_item_name"),
            output_quantity=row["output_quantity"], profession=row.get("profession"),
            required_skill=row.get("required_skill"), expansion=row.get("expansion"),
            game_patch=row.get("game_patch"), game_recipe_id=row.get("game_recipe_id"),
            source=row.get("source") or "", last_verified=row.get("last_verified"),
            category=row.get("category"), notes=row.get("notes") or "", reagents=reagents)


@dataclass
class ImportResult:
    recipes: list = field(default_factory=list)
    errors: list = field(default_factory=list)      # recipes with errors are skipped entirely
    warnings: list = field(default_factory=list)


# --- validation -------------------------------------------------------------------------------

def validate_recipe(recipe: Recipe) -> list:
    """Problems that make a recipe unusable (empty list = fine)."""
    problems = []
    if not recipe.key.strip():
        problems.append("recipe key is empty")
    if not recipe.name.strip():
        problems.append("name is empty")
    if recipe.crafted_item_id <= 0:
        problems.append("crafted item ID must be positive")
    if recipe.output_quantity <= 0:
        problems.append("output quantity must be at least 1")
    if recipe.required_skill is not None and recipe.required_skill < 0:
        problems.append("required skill cannot be negative")
    if recipe.last_verified:
        try:
            date.fromisoformat(recipe.last_verified)
        except ValueError:
            problems.append("last_verified must look like YYYY-MM-DD")
    if not recipe.reagents:
        problems.append("recipe has no reagents")
    seen = set()
    for r in recipe.reagents:
        if r.slot <= 0:
            problems.append(f"reagent slot {r.slot} must be positive")
        if r.item_id <= 0:
            problems.append("a reagent item ID must be positive")
        if r.quantity <= 0:
            problems.append(f"reagent {r.item_name or r.item_id}: quantity must be at least 1")
        if (r.slot, r.item_id) in seen:
            problems.append(f"reagent {r.item_name or r.item_id} appears twice in slot {r.slot}")
        seen.add((r.slot, r.item_id))
    return problems


def recipe_warnings(recipe: Recipe) -> list:
    """Cautions about data quality (not errors)."""
    warnings = []
    if not recipe.last_verified:
        warnings.append("Unverified: no verification date. Check quantities against the game.")
    if not recipe.game_patch:
        warnings.append("No game patch recorded for these quantities.")
    if not recipe.source:
        warnings.append("No source recorded.")
    return warnings


# --- reading files ---------------------------------------------------------------------------

def _clean(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value, label: str, errors: list, required: bool = False) -> Optional[int]:
    text = _clean(value)
    if text is None:
        if required:
            errors.append(f"{label} is required")
        return None
    try:
        number = float(text.replace(",", ""))
    except ValueError:
        errors.append(f"{label} '{text}' is not a number")
        return None
    if number != int(number):
        errors.append(f"{label} '{text}' must be a whole number")
        return None
    return int(number)


def _bool(value, default: bool, label: str, errors: list) -> bool:
    text = (_clean(value) or "").lower()
    if not text:
        return default
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    errors.append(f"{label} '{text}' should be true or false")
    return default


def _resolve_item(id_value, name_value, lookup: Optional[Lookup], label: str, errors: list) -> tuple:
    """Return (item_id, name). IDs win; names are looked up and must be unambiguous."""
    item_id = _int(id_value, f"{label} item ID", errors)
    name = _clean(name_value)
    if item_id is not None:
        return item_id, name
    if name is None:
        errors.append(f"{label}: give an item ID or an item name")
        return None, None
    if lookup is None:
        errors.append(f"{label} '{name}': names can only be resolved once prices are cached - use an item ID")
        return None, name
    candidates = lookup(name)
    if len(candidates) == 1:
        return candidates[0][0], candidates[0][1]
    if not candidates:
        errors.append(f"{label} '{name}' was not found in your price data "
                      "(non-auctionable items need an item ID)")
    else:
        ids = ", ".join(str(c[0]) for c in candidates[:6])
        errors.append(f"{label} '{name}' matches several items ({ids}) - use the item ID")
    return None, name


def _build(rows: list, lookup: Optional[Lookup]) -> ImportResult:
    result = ImportResult()
    groups: dict = {}
    for number, raw in rows:
        row = {(k or "").strip().lower(): v for k, v in raw.items() if k is not None}
        key = _clean(row.get("recipe_key"))
        if key is None:
            result.errors.append(f"Row {number}: recipe_key is required")
            continue
        groups.setdefault(key, []).append((number, row))

    for key, group in groups.items():
        errors: list = []
        first_number, first = group[0]
        header = {f: _clean(first.get(f)) for f in _HEADER_FIELDS}
        for number, row in group[1:]:
            for f in _HEADER_FIELDS:
                value = _clean(row.get(f))
                if value is not None and header[f] is not None and value != header[f]:
                    errors.append(f"row {number}: '{f}' differs from the first row of this recipe")
                elif header[f] is None and value is not None:
                    header[f] = value

        crafted_id, crafted_name = _resolve_item(header["crafted_item_id"], header["crafted_item_name"],
                                                 lookup, "Crafted item", errors)
        output_qty = _int(header["output_quantity"], "output_quantity", errors)
        required_skill = _int(header["required_skill"], "required_skill", errors)
        game_recipe_id = _int(header["game_recipe_id"], "game_recipe_id", errors)
        if not header["name"]:
            errors.append("name is required")

        reagents, used_slots, next_slot = [], set(), 1
        pending = []
        for number, row in group:
            errs: list = []
            if _clean(row.get("reagent_item_id")) is None and _clean(row.get("reagent_name")) is None \
                    and _clean(row.get("reagent_quantity")) is None:
                continue                                 # a row without any reagent data
            item_id, item_name = _resolve_item(row.get("reagent_item_id"), row.get("reagent_name"),
                                               lookup, "Reagent", errs)
            quantity = _int(row.get("reagent_quantity"), "reagent_quantity", errs, required=True)
            slot = _int(row.get("reagent_slot"), "reagent_slot", errs)
            auctionable = _bool(row.get("auctionable"), True, "auctionable", errs)
            eligible = _bool(row.get("savings_eligible"), False, "savings_eligible", errs)
            errors.extend(f"row {number}: {e}" for e in errs)
            if slot is not None:
                used_slots.add(slot)
            pending.append((slot, item_id, item_name, quantity, _clean(row.get("quality_label")) or "",
                            auctionable, eligible))
        for slot, item_id, item_name, quantity, label, auctionable, eligible in pending:
            if slot is None:
                while next_slot in used_slots:
                    next_slot += 1
                slot = next_slot
                used_slots.add(slot)
            if item_id is not None and quantity is not None:
                reagents.append(Reagent(slot, item_id, quantity, item_name, label, auctionable, eligible))

        if not errors and crafted_id is not None:
            recipe = Recipe(
                key=key, name=header["name"], crafted_item_id=crafted_id, crafted_item_name=crafted_name,
                output_quantity=output_qty if output_qty is not None else 1,
                profession=header["profession"], required_skill=required_skill,
                expansion=header["expansion"], game_patch=header["game_patch"],
                game_recipe_id=game_recipe_id, source=header["source"] or "",
                last_verified=header["last_verified"], category=header["category"],
                notes=header["notes"] or "", reagents=tuple(reagents))
            errors.extend(validate_recipe(recipe))
            if not errors:
                result.recipes.append(recipe)
                result.warnings.extend(f"{key}: {w}" for w in recipe_warnings(recipe))
        if errors:
            result.errors.extend(f"Recipe '{key}': {e}" for e in errors)
    return result


def _detect_delimiter(text: str) -> str:
    first = text.splitlines()[0] if text.splitlines() else ""
    return max(",;\t", key=first.count)


def parse_recipes_csv(text: str, lookup: Optional[Lookup] = None) -> ImportResult:
    text = text.lstrip("\ufeff")
    if not text.strip():
        return ImportResult(errors=["The file is empty."])
    reader = csv.DictReader(io.StringIO(text), delimiter=_detect_delimiter(text))
    headers = {(h or "").strip().lower() for h in (reader.fieldnames or [])}
    if "recipe_key" not in headers:
        return ImportResult(errors=["Missing the 'recipe_key' column. Expected columns: "
                                    + ", ".join(CSV_COLUMNS)])
    rows = [(i, row) for i, row in enumerate(reader, start=2)]
    return _build(rows, lookup)


def parse_recipes_json(text: str, lookup: Optional[Lookup] = None) -> ImportResult:
    try:
        data = json.loads(text.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        return ImportResult(errors=[f"Could not read the JSON: {exc}"])
    if isinstance(data, dict):
        data = data.get("recipes", [])
    if not isinstance(data, list):
        return ImportResult(errors=["Expected a list of recipes (or an object with a 'recipes' list)."])

    rows = []
    for index, recipe in enumerate(data, start=1):
        if not isinstance(recipe, dict):
            return ImportResult(errors=[f"Recipe #{index} is not an object."])
        base = {f: recipe.get(f) for f in _HEADER_FIELDS}
        reagents = recipe.get("reagents") or [{}]
        for reagent in reagents:
            row = dict(base)
            row["reagent_slot"] = reagent.get("slot", reagent.get("reagent_slot"))
            row["reagent_item_id"] = reagent.get("item_id", reagent.get("reagent_item_id"))
            row["reagent_name"] = reagent.get("item_name", reagent.get("reagent_name", reagent.get("name")))
            row["reagent_quantity"] = reagent.get("quantity", reagent.get("reagent_quantity"))
            row["quality_label"] = reagent.get("quality_label")
            row["auctionable"] = reagent.get("auctionable")
            row["savings_eligible"] = reagent.get("savings_eligible")
            rows.append((f"recipe #{index}", row))
    return _build(rows, lookup)


def parse_recipes(text: str, lookup: Optional[Lookup] = None) -> ImportResult:
    """Detect JSON vs CSV and parse."""
    stripped = text.lstrip("\ufeff").lstrip()
    if stripped.startswith(("[", "{")):
        return parse_recipes_json(stripped, lookup)
    return parse_recipes_csv(text, lookup)


# --- writing files -------------------------------------------------------------------------------

def _flat_rows(recipes: list) -> list:
    rows = []
    for r in recipes:
        header = {
            "recipe_key": r.key, "name": r.name, "crafted_item_id": r.crafted_item_id,
            "crafted_item_name": r.crafted_item_name or "", "output_quantity": r.output_quantity,
            "profession": r.profession or "", "required_skill": "" if r.required_skill is None else r.required_skill,
            "expansion": r.expansion or "", "game_patch": r.game_patch or "",
            "game_recipe_id": "" if r.game_recipe_id is None else r.game_recipe_id,
            "source": r.source, "last_verified": r.last_verified or "",
            "category": r.category or "", "notes": r.notes,
        }
        for g in r.reagents:
            rows.append({**header, "reagent_slot": g.slot, "reagent_item_id": g.item_id,
                         "reagent_name": g.item_name or "", "reagent_quantity": g.quantity,
                         "quality_label": g.quality_label, "auctionable": str(g.auctionable).lower(),
                         "savings_eligible": str(g.savings_eligible).lower()})
    return rows


def recipes_to_csv(recipes: list) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(_flat_rows(recipes))
    return buffer.getvalue()


def recipes_to_json(recipes: list) -> str:
    out = []
    for r in recipes:
        out.append({
            "recipe_key": r.key, "name": r.name, "crafted_item_id": r.crafted_item_id,
            "crafted_item_name": r.crafted_item_name, "output_quantity": r.output_quantity,
            "profession": r.profession, "required_skill": r.required_skill, "expansion": r.expansion,
            "game_patch": r.game_patch, "game_recipe_id": r.game_recipe_id, "source": r.source,
            "last_verified": r.last_verified, "category": r.category, "notes": r.notes,
            "reagents": [{"slot": g.slot, "item_id": g.item_id, "item_name": g.item_name,
                          "quantity": g.quantity, "quality_label": g.quality_label,
                          "auctionable": g.auctionable, "savings_eligible": g.savings_eligible}
                         for g in r.reagents]})
    return json.dumps(out, indent=2, ensure_ascii=False)


def csv_template() -> str:
    """Header row only - no data."""
    return ",".join(CSV_COLUMNS) + "\r\n"
