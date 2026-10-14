"""Recipe import from Blizzard's official World of Warcraft Game Data API.

Uses the documented profession endpoints (developers.blizzard.com):
    POST https://oauth.battle.net/token                      client-credentials token
    GET  /data/wow/profession/index                          professions
    GET  /data/wow/profession/{id}                           skill tiers (one per expansion)
    GET  /data/wow/profession/{id}/skill-tier/{tier}         categories and their recipes
    GET  /data/wow/recipe/{id}                               reagents, crafted item, quantity
on https://{region}.api.blizzard.com with namespace ``static-{region}``.

The client ID and secret are the user's own; they are stored only in the local
database (``data/``, git-ignored) and sent only to Blizzard.

What the API gives is not taken as verified: recipes import with source "Blizzard Game
Data API (<date>)", no ``last_verified`` date, and every gap is reported (no crafted item,
no reagents, a quantity range). Quality-tier reagents and optional/finishing reagents are
known to be incomplete in this API for modern recipes - compare with the game.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from . import config
from .recipes import Reagent, Recipe, validate_recipe

TOKEN_URL = "https://oauth.battle.net/token"
API_HOSTS = {"us": "https://us.api.blizzard.com", "eu": "https://eu.api.blizzard.com",
             "kr": "https://kr.api.blizzard.com", "tw": "https://tw.api.blizzard.com"}
DEFAULT_LOCALE = "en_US"
CACHE_DIR = config.CACHE_DIR / "blizzard"
SETTING_ID = "blizzard_client_id"
SETTING_SECRET = "blizzard_client_secret"
# Suggested words for finding housing/decor categories. Only a starting point: category names
# come from the API, so the user can change these.
HOUSING_KEYWORDS = ("decor", "furnish", "furniture", "housing", "home")


class BlizzardAPIError(Exception):
    pass


class BlizzardClient:
    """Small client for the Game Data API. ``session`` can be replaced in tests."""

    def __init__(self, client_id: str, client_secret: str, region: str = "us",
                 locale: str = DEFAULT_LOCALE, session=None, cache_dir: Optional[Path] = CACHE_DIR):
        if not (client_id or "").strip() or not (client_secret or "").strip():
            raise BlizzardAPIError("Enter your Blizzard API client ID and secret in Settings first.")
        if region not in API_HOSTS:
            raise BlizzardAPIError(f"Unknown region '{region}'. Use one of {', '.join(API_HOSTS)}.")
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self.region, self.locale = region, locale
        self.session = session or requests.Session()
        self.cache_dir = cache_dir
        self._token: Optional[str] = None
        self._token_expires = 0.0

    # -- auth / transport ----------------------------------------------------------------------
    def token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        try:
            response = self.session.post(TOKEN_URL, data={"grant_type": "client_credentials"},
                                         auth=(self.client_id, self.client_secret),
                                         timeout=config.HTTP_TIMEOUT_SECONDS)
        except requests.RequestException as exc:
            raise BlizzardAPIError(f"Could not reach Blizzard's login server: {exc}") from exc
        if response.status_code in (400, 401):
            raise BlizzardAPIError("Blizzard rejected the client ID / secret. Check them in Settings "
                                   "(or create a new secret on develop.battle.net).")
        if response.status_code != 200:
            raise BlizzardAPIError(f"Token request failed (HTTP {response.status_code}).")
        data = response.json()
        self._token = data.get("access_token")
        if not self._token:
            raise BlizzardAPIError("Blizzard's token response had no access token.")
        self._token_expires = time.time() + float(data.get("expires_in", 0))
        return self._token

    def get(self, path: str, cache_key: Optional[str] = None) -> dict:
        """GET a static game-data document. ``cache_key`` stores the JSON locally for re-use."""
        cached = self._cache_file(cache_key)
        if cached is not None and cached.is_file():
            return json.loads(cached.read_text(encoding="utf-8"))
        url = API_HOSTS[self.region] + path
        params = {"namespace": f"static-{self.region}", "locale": self.locale}
        try:
            response = self.session.get(url, params=params, timeout=config.HTTP_TIMEOUT_SECONDS,
                                        headers={"Authorization": f"Bearer {self.token()}",
                                                 "User-Agent": config.USER_AGENT})
        except requests.RequestException as exc:
            raise BlizzardAPIError(f"Request failed for {path}: {exc}") from exc
        if response.status_code == 404:
            raise BlizzardAPIError(f"Not found: {path}")
        if response.status_code == 429:
            raise BlizzardAPIError("Blizzard's rate limit was hit - wait a minute and try again.")
        if response.status_code != 200:
            raise BlizzardAPIError(f"HTTP {response.status_code} for {path}")
        data = response.json()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _cache_file(self, key: Optional[str]) -> Optional[Path]:
        if key is None or self.cache_dir is None:
            return None
        safe = re.sub(r"[^\w\-]+", "_", f"{self.region}_{self.locale}_{key}")
        return Path(self.cache_dir) / f"{safe}.json"

    # -- endpoints ------------------------------------------------------------------------------
    def professions(self) -> list:
        """[(id, name)]"""
        data = self.get("/data/wow/profession/index")
        return [(p["id"], p.get("name") or str(p["id"])) for p in data.get("professions", [])]

    def skill_tiers(self, profession_id: int) -> list:
        """[(id, name)] - one tier per expansion, e.g. 'Khaz Algar Alchemy'."""
        data = self.get(f"/data/wow/profession/{int(profession_id)}")
        return [(t["id"], t.get("name") or str(t["id"])) for t in data.get("skill_tiers", [])]

    def categories(self, profession_id: int, tier_id: int) -> list:
        """[{"name", "recipes": [(id, name)]}] for one skill tier."""
        data = self.get(f"/data/wow/profession/{int(profession_id)}/skill-tier/{int(tier_id)}")
        return [{"name": c.get("name") or "(unnamed)",
                 "recipes": [(r["id"], r.get("name") or str(r["id"])) for r in c.get("recipes", [])]}
                for c in data.get("categories", [])]

    def recipe(self, recipe_id: int, refresh: bool = False) -> dict:
        key = None if refresh else f"recipe_{int(recipe_id)}"
        return self.get(f"/data/wow/recipe/{int(recipe_id)}", cache_key=key)


def matching_categories(categories: list, keywords) -> list:
    """Categories whose name contains any keyword (case-insensitive)."""
    words = [k.strip().lower() for k in keywords if k and k.strip()]
    return [c for c in categories if any(w in c["name"].lower() for w in words)]


# --- converting API documents into recipes (pure) ------------------------------------------------

@dataclass
class Conversion:
    recipe: Optional[Recipe]
    problems: list = field(default_factory=list)      # the recipe could not be imported
    notes: list = field(default_factory=list)         # imported, but check this


def _ref(value) -> tuple:
    if isinstance(value, dict):
        return value.get("id"), value.get("name")
    return None, None


def recipe_from_api(raw: dict, profession: Optional[str], skill_tier: Optional[str],
                    category: Optional[str], fetched_on: Optional[str] = None) -> Conversion:
    """Turn one ``/data/wow/recipe/{id}`` document into a ``Recipe`` (or report why not)."""
    fetched_on = fetched_on or date.today().isoformat()
    recipe_id = raw.get("id")
    name = raw.get("name") or f"recipe {recipe_id}"
    result = Conversion(None)

    crafted_id, crafted_name = _ref(raw.get("crafted_item"))
    if crafted_id is None:
        alliance, horde = _ref(raw.get("alliance_crafted_item")), _ref(raw.get("horde_crafted_item"))
        crafted_id, crafted_name = alliance if alliance[0] is not None else horde
        if crafted_id is not None and alliance[0] != horde[0] and None not in (alliance[0], horde[0]):
            result.notes.append(f"Faction-specific output: imported the {'Alliance' if alliance[0] == crafted_id else 'Horde'} item.")
    if crafted_id is None:
        result.problems.append("the API lists no crafted item (enchants and similar have none)")

    quantity = raw.get("crafted_quantity") or {}
    if "value" in quantity:
        output_quantity = quantity["value"]
    else:
        low, high = quantity.get("minimum"), quantity.get("maximum")
        output_quantity = low or 1
        if low is not None and high is not None and low != high:
            result.notes.append(f"The API gives an output range {low}-{high}; the minimum ({low}) was used.")
    try:
        output_quantity = max(1, int(float(output_quantity)))
    except (TypeError, ValueError):
        output_quantity = 1
        result.notes.append("Output quantity missing in the API; 1 was used.")

    reagents = []
    for slot, entry in enumerate(raw.get("reagents") or [], start=1):
        item_id, item_name = _ref(entry.get("reagent"))
        qty = entry.get("quantity")
        if item_id is None or not qty:
            result.problems.append(f"reagent #{slot} has no item or quantity")
            continue
        reagents.append(Reagent(slot=slot, item_id=int(item_id), quantity=int(qty), item_name=item_name))
    if not raw.get("reagents"):
        result.problems.append("the API lists no reagents")
    if raw.get("modified_crafting_slots"):
        result.notes.append(f"{len(raw['modified_crafting_slots'])} optional / quality reagent slot(s) are not "
                            "described by the API - check the recipe in game.")
    if result.problems:
        return result

    recipe = Recipe(
        key=f"bnet-{recipe_id}", name=name, crafted_item_id=int(crafted_id), crafted_item_name=crafted_name,
        reagents=tuple(reagents), output_quantity=output_quantity, profession=profession,
        expansion=skill_tier, game_recipe_id=int(recipe_id) if recipe_id is not None else None,
        source=f"Blizzard Game Data API ({fetched_on})", last_verified=None, category=category,
        notes="; ".join(result.notes))
    problems = validate_recipe(recipe)
    if problems:
        result.problems.extend(problems)
        return result
    result.recipe = recipe
    return result
