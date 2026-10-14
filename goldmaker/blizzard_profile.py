"""Characters and professions from Blizzard's Profile API.

Two ways in, both with the user's own API client:

* **Sign in with Battle.net** (OAuth authorization code, scope ``wow.profile``): lists every character
  on the account. Needs the redirect URL (default ``http://localhost:8501/``) added to the client on
  develop.battle.net. The user token is used once and not stored.
* **By name** (client credentials): any character's public profession page, so known characters can be
  refreshed without signing in.

Professions come per expansion tier (e.g. "Legion Tailoring") with skill points and the recipe IDs the
character knows - what the housing finder uses for "who can craft this". Multicraft / Resourcefulness are
not in the API.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote, urlencode

import requests

from . import config, database
from .blizzard_api import TOKEN_URL, BlizzardAPIError, BlizzardClient

AUTHORIZE_URL = "https://oauth.battle.net/authorize"
DEFAULT_REDIRECT = "http://localhost:8501/"
SETTING_REDIRECT = "blizzard_redirect_uri"
SETTING_STATE = "blizzard_oauth_state"


def new_state() -> str:
    return secrets.token_urlsafe(16)


def authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTHORIZE_URL + "?" + urlencode({"client_id": client_id, "redirect_uri": redirect_uri,
                                            "response_type": "code", "scope": "wow.profile", "state": state})


def exchange_code(client: BlizzardClient, code: str, redirect_uri: str) -> str:
    """Trade the code Blizzard sent back for a user access token."""
    try:
        response = client.session.post(TOKEN_URL, data={"grant_type": "authorization_code", "code": code,
                                                        "redirect_uri": redirect_uri},
                                       auth=(client.client_id, client.client_secret),
                                       timeout=config.HTTP_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise BlizzardAPIError(f"Could not reach Blizzard's login server: {exc}") from exc
    if response.status_code != 200:
        raise BlizzardAPIError(f"Battle.net sign-in failed (HTTP {response.status_code}). Check that the redirect "
                               "URL in Settings is listed exactly the same on your client at develop.battle.net.")
    token = response.json().get("access_token")
    if not token:
        raise BlizzardAPIError("Battle.net sign-in returned no token.")
    return token


@dataclass(frozen=True)
class AccountCharacter:
    name: str
    realm_slug: str
    realm_name: str
    level: Optional[int] = None
    class_name: Optional[str] = None
    faction: Optional[str] = None

    @property
    def key(self) -> str:
        """Same "Name-Realm" form CharExporter uses, so both sources meet in one character."""
        return f"{self.name}-{self.realm_name}"


def _name(value) -> Optional[str]:
    if isinstance(value, dict):
        name = value.get("name")
        if isinstance(name, dict):                       # some responses localise into a dict
            return name.get("en_US") or next(iter(name.values()), None)
        return name or value.get("type")
    return value


def parse_account(raw: dict) -> list:
    """Characters from ``/profile/user/wow``."""
    found = []
    for account in raw.get("wow_accounts", []):
        for c in account.get("characters", []):
            realm = c.get("realm") or {}
            if not c.get("name") or not realm.get("slug"):
                continue
            found.append(AccountCharacter(c["name"], realm["slug"], _name(realm) or realm["slug"], c.get("level"),
                                          _name(c.get("playable_class")), _name(c.get("faction"))))
    return found


def parse_professions(raw: dict) -> tuple:
    """(tiers, known recipes) from ``/profile/wow/character/{realm}/{name}/professions``."""
    tiers, known = [], []
    for group in ("primaries", "secondaries"):
        for entry in raw.get(group, []) or []:
            profession = _name(entry.get("profession")) or "?"
            for t in entry.get("tiers", []) or []:
                tier = t.get("tier") or {}
                if tier.get("id") is None:
                    continue
                tiers.append({"profession": profession, "tier_id": tier["id"], "tier_name": _name(tier),
                              "skill_points": t.get("skill_points"), "max_skill_points": t.get("max_skill_points")})
                for r in t.get("known_recipes", []) or []:
                    if r.get("id") is not None:
                        known.append({"recipe_id": r["id"], "recipe_name": r.get("name"), "tier_id": tier["id"]})
    return tiers, known


def account_characters(client: BlizzardClient, user_token: str) -> list:
    raw = client.get("/profile/user/wow", namespace="profile", token=user_token)
    return parse_account(raw)


def character_professions(client: BlizzardClient, realm_slug: str, name: str,
                          user_token: Optional[str] = None) -> dict:
    path = f"/profile/wow/character/{quote(realm_slug)}/{quote(name.lower())}/professions"
    return client.get(path, namespace="profile", token=user_token)


@dataclass
class ProfileReport:
    imported: list = field(default_factory=list)       # (character key, tiers, known recipes)
    skipped: list = field(default_factory=list)        # (character key, reason)


def import_characters(conn, client: BlizzardClient, characters: list, user_token: Optional[str] = None,
                      min_level: int = 1) -> ProfileReport:
    """Store each character and its profession tiers / known recipes."""
    report = ProfileReport()
    for c in characters:
        if c.level is not None and c.level < min_level:
            report.skipped.append((c.key, f"level {c.level} is below {min_level}"))
            continue
        try:
            raw = character_professions(client, c.realm_slug, c.name, user_token)
        except BlizzardAPIError as exc:
            reason = ("no profession data (inactive or never trained)" if "Not found" in str(exc) else str(exc))
            report.skipped.append((c.key, reason))
            continue
        tiers, known = parse_professions(raw)
        database.upsert_character_basic(conn, c.key, c.name, c.realm_name, c.class_name, c.level, c.faction)
        database.save_blizzard_professions(conn, c.key, tiers, known)
        report.imported.append((c.key, len(tiers), len(known)))
    return report
