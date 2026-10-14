"""Item facts from Blizzard's Item API: name, icon, binding (soulbound?), rarity, crafting quality tier.

Used so that
* soulbound / account-bound reagents (e.g. lumber you gather yourself) are treated as NOT on the Auction
  House: they cost their opportunity cost, or nothing (said so) - the calculation never waits for a price;
* tables can show item icons and a bronze / silver / gold badge where the quality tier is known.

The crafting quality tier is read only from fields Blizzard sends (see ``_crafting_tier``); when no such
field exists it stays unknown - it is never guessed from item IDs or names.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Optional

from .blizzard_api import BlizzardAPIError, BlizzardClient

# Binding types that make an item impossible to buy on the AH.
BOUND_TYPES = {"ON_ACQUIRE", "TO_ACCOUNT", "TO_BNETACCOUNT", "QUEST", "ACCOUNT", "WARBAND", "ON_ACQUIRE_ACCOUNT"}
TIER_BADGES = {1: "🥉", 2: "🥈", 3: "🥇"}
_LABEL_TIERS = {"q1": 1, "bronze": 1, "tier 1": 1, "rank 1": 1,
                "q2": 2, "silver": 2, "tier 2": 2, "rank 2": 2,
                "q3": 3, "gold": 3, "tier 3": 3, "rank 3": 3}


@dataclass(frozen=True)
class ItemInfo:
    item_id: int
    name: Optional[str] = None
    icon_url: Optional[str] = None
    binding: Optional[str] = None          # Blizzard binding type, e.g. ON_ACQUIRE
    rarity: Optional[str] = None           # COMMON / UNCOMMON / RARE / EPIC ...
    crafting_tier: Optional[int] = None    # 1 / 2 / 3 when Blizzard says so

    @property
    def soulbound(self) -> bool:
        return (self.binding or "").upper() in BOUND_TYPES


def _crafting_tier(doc: dict) -> Optional[int]:
    """Look for an explicit crafting-quality field anywhere in the item document (not the rarity)."""
    def walk(node, depth=0):
        if depth > 4 or not isinstance(node, dict):
            return None
        for key, value in node.items():
            k = key.lower()
            if ("crafting" in k or "reagent_quality" in k or "crafted_quality" in k) and "quality" in k:
                if isinstance(value, (int, float)) and 1 <= int(value) <= 5:
                    return int(value)
                if isinstance(value, dict):
                    for sub in ("tier", "rank", "value", "id"):
                        if isinstance(value.get(sub), (int, float)) and 1 <= int(value[sub]) <= 5:
                            return int(value[sub])
            if isinstance(value, dict):
                found = walk(value, depth + 1)
                if found:
                    return found
        return None
    return walk(doc)


def parse_item(doc: dict, media: Optional[dict] = None) -> ItemInfo:
    preview = doc.get("preview_item") or {}
    binding = (preview.get("binding") or doc.get("binding") or {}).get("type")
    icon = None
    for asset in (media or {}).get("assets", []):
        if asset.get("key") == "icon":
            icon = asset.get("value")
    name = doc.get("name")
    if isinstance(name, dict):
        name = name.get("en_US") or next(iter(name.values()), None)
    return ItemInfo(int(doc["id"]), name, icon, binding, (doc.get("quality") or {}).get("type"), _crafting_tier(doc))


def fetch(client: BlizzardClient, item_ids, progress: Optional[Callable] = None) -> tuple:
    """({item_id: ItemInfo}, [errors]) - documents are cached locally by the client."""
    found, errors = {}, []
    ids = sorted({int(i) for i in item_ids})
    for n, item_id in enumerate(ids, start=1):
        try:
            doc = client.item(item_id)
            try:
                media = client.item_media(item_id)
            except BlizzardAPIError:
                media = None
            found[item_id] = parse_item(doc, media)
        except BlizzardAPIError as exc:
            errors.append(f"item {item_id}: {exc}")
        if progress:
            progress(n / len(ids))
    return found, errors


def apply_to_recipe(recipe, infos: dict):
    """Mark soulbound reagents as not auctionable, and fill in missing names / quality labels."""
    reagents = []
    for r in recipe.reagents:
        info = infos.get(r.item_id)
        if info is None:
            reagents.append(r)
            continue
        label = r.quality_label or (f"Q{info.crafting_tier}" if info.crafting_tier else "")
        reagents.append(replace(r, auctionable=r.auctionable and not info.soulbound,
                                item_name=r.item_name or info.name, quality_label=label))
    return replace(recipe, reagents=tuple(reagents))


def tier_of(quality_label: Optional[str], info: Optional[ItemInfo] = None) -> Optional[int]:
    if info is not None and info.crafting_tier:
        return info.crafting_tier
    return _LABEL_TIERS.get((quality_label or "").strip().lower())


def badge(quality_label: Optional[str], info: Optional[ItemInfo] = None) -> str:
    tier = tier_of(quality_label, info)
    return TIER_BADGES.get(tier, "") if tier else ""
