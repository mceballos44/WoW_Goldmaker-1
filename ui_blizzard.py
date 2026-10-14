"""Blizzard Game Data API: credentials (Settings) and recipe import (Housing / Crafting)."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd
import streamlit as st

from goldmaker import blizzard_api, database, item_info
from goldmaker.blizzard_api import BlizzardAPIError
from ui_common import db


def _credentials() -> tuple:
    with db() as conn:
        return (database.get_setting(conn, blizzard_api.SETTING_ID, "") or "",
                database.get_setting(conn, blizzard_api.SETTING_SECRET, "") or "")


def render_settings() -> None:
    st.markdown("**Blizzard API (recipe import)**")
    client_id, secret = _credentials()
    st.caption("Your own client from develop.battle.net -> API Access. Stored only in this PC's database "
               "(the git-ignored `data` folder) and sent only to Blizzard. Never put them in a file in the repo.")
    c1, c2 = st.columns(2)
    new_id = c1.text_input("Client ID", value=client_id, key="bnet_id")
    new_secret = c2.text_input("Client secret", value=secret, type="password", key="bnet_secret")
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save and test", key="bnet_save"):
        with db() as conn:
            database.set_setting(conn, blizzard_api.SETTING_ID, new_id.strip())
            database.set_setting(conn, blizzard_api.SETTING_SECRET, new_secret.strip())
        try:
            found = blizzard_api.BlizzardClient(new_id, new_secret, st.session_state.get("region", "us")).professions()
        except BlizzardAPIError as exc:
            st.error(str(exc))
        else:
            st.success(f"Connected - Blizzard lists {len(found)} professions.")
    if (client_id or secret) and b2.button("Forget credentials", key="bnet_forget"):
        with db() as conn:
            database.set_setting(conn, blizzard_api.SETTING_ID, "")
            database.set_setting(conn, blizzard_api.SETTING_SECRET, "")
        st.rerun()


def _client():
    client_id, secret = _credentials()
    return blizzard_api.BlizzardClient(client_id, secret, st.session_state.get("region", "us"))


def render_import(prefix: str = "bnet", latest_only_default: bool = True, tier_filter_default: str = "",
                  lookup=None) -> None:
    """Search / browse Blizzard recipes and import them. ``prefix`` keeps widget keys unique per tab.

    ``lookup(name) -> [(item_id, name)]`` searches the cached prices by exact name (``PriceIndex.lookup_name``).
    """
    st.caption("Imports recipes (reagents, quantities, crafted item) from Blizzard's official Game Data API. "
               "They arrive marked **unverified** with source 'Blizzard Game Data API (date)': the API is known to "
               "leave out optional / quality reagents on modern recipes, so compare a few in game before "
               "trusting them. Recipes the API cannot describe (no crafted item, no reagents) are listed, not "
               "guessed.")
    client_id, secret = _credentials()
    if not (client_id and secret):
        st.info("Enter your client ID and secret on the **Settings** tab first.")
        return
    try:
        client = _client()
    except BlizzardAPIError as exc:
        st.error(str(exc))
        return

    mode = st.radio("Find recipes", ["Search every profession for housing categories", "Browse one profession"],
                    horizontal=True, key=f"{prefix}_mode")
    try:
        if mode.startswith("Search"):
            found = _search_all(client, prefix, latest_only_default, tier_filter_default)
        else:
            found = _browse(client, prefix)
    except BlizzardAPIError as exc:
        st.error(str(exc))
        return
    if found:
        _fetch_and_import(client, found, prefix, lookup)


def _search_all(client, prefix: str, latest_only_default: bool, tier_filter_default: str) -> list:
    c1, c2 = st.columns([2, 1])
    words = c1.text_input("Category name contains (comma-separated)", ", ".join(blizzard_api.HOUSING_KEYWORDS),
                          key=f"{prefix}_words",
                          help="Category names come from the API. If nothing matches, browse one profession to see "
                               "the real names and adjust these words.")
    tier_filter = c2.text_input("Expansion: skill tier name contains", value=tier_filter_default,
                                placeholder="e.g. Legion - empty = all", key=f"{prefix}_tierfilter",
                                help="Skill tiers are named per expansion, e.g. 'Legion Tailoring'.")
    only_latest = st.checkbox("Only each profession's newest skill tier", value=latest_only_default,
                              key=f"{prefix}_latest",
                              help="'Newest' is taken as the tier with the highest ID. Untick to search every expansion.")
    if st.button("Search", key=f"{prefix}_search"):
        hits = []
        professions = client.professions()
        progress = st.progress(0.0, text="Reading professions...")
        for n, (pid, pname) in enumerate(professions, start=1):
            tiers = client.skill_tiers(pid)
            if tier_filter.strip():
                tiers = [t for t in tiers if tier_filter.strip().lower() in t[1].lower()]
            if only_latest and tiers:
                tiers = [max(tiers, key=lambda t: t[0])]
            for tid, tname in tiers:
                for cat in blizzard_api.matching_categories(client.categories(pid, tid), words.split(",")):
                    hits.append({"profession": pname, "tier": tname, "category": cat["name"],
                                 "recipes": cat["recipes"]})
            progress.progress(n / max(len(professions), 1), text=f"Read {pname}")
        st.session_state[f"{prefix}_hits"] = hits
    hits = st.session_state.get(f"{prefix}_hits")
    if hits is None:
        return []
    if not hits:
        st.warning("No category matched. Browse a profession to see its category names, then change the words.")
        return []
    labels = {f"{h['profession']} / {h['tier']} / {h['category']} ({len(h['recipes'])})": h for h in hits}
    chosen = st.multiselect("Categories to import", list(labels), default=list(labels), key=f"{prefix}_hit_pick")
    return [(labels[c]["profession"], labels[c]["tier"], labels[c]["category"], rid, rname)
            for c in chosen for rid, rname in labels[c]["recipes"]]


def _browse(client, prefix: str) -> list:
    professions = dict((name, pid) for pid, name in client.professions())
    pname = st.selectbox("Profession", sorted(professions), key=f"{prefix}_prof")
    tiers = dict((name, tid) for tid, name in client.skill_tiers(professions[pname]))
    if not tiers:
        st.info("This profession has no skill tiers in the API.")
        return []
    tname = st.selectbox("Skill tier (expansion)", list(tiers), index=len(tiers) - 1, key=f"{prefix}_tier_{pname}")
    cats = client.categories(professions[pname], tiers[tname])
    labels = {f"{c['name']} ({len(c['recipes'])})": c for c in cats}
    default = [l for l, c in labels.items() if blizzard_api.is_housing(c["name"])]
    chosen = st.multiselect("Categories", list(labels), default=default, key=f"{prefix}_cats_{pname}_{tname}")
    return [(pname, tname, labels[c]["name"], rid, rname) for c in chosen for rid, rname in labels[c]["recipes"]]


def _item_finder(client, lookup):
    """Exact-name item lookup: your price data first, then Blizzard's item search."""
    def find(name: str) -> list:
        found = lookup(name) if lookup is not None else []
        if found:
            return found
        try:
            return client.search_items(name)
        except BlizzardAPIError:
            return []
    return find


def _fetch_and_import(client, wanted: list, prefix: str, lookup=None) -> None:
    st.caption(f"{len(wanted)} recipe(s) selected. Each is one request the first time; after that they come from "
               "a local cache (`data/cache/blizzard`). Item facts (icon, soulbound, quality) are fetched too.")
    refresh = st.checkbox("Re-download instead of using the cache", key=f"{prefix}_refresh")
    if st.button(f"Fetch {len(wanted)} recipe(s)", type="primary", key=f"{prefix}_fetch"):
        today, converted = date.today().isoformat(), []
        find_item = _item_finder(client, lookup)
        progress = st.progress(0.0, text="Recipes...")
        for n, (prof, tier, cat, rid, rname) in enumerate(wanted, start=1):
            entry = {"name": rname, "rid": rid, "meta": (prof, tier, cat), "recipe": None, "problems": [],
                     "notes": [], "reagents": []}
            try:
                raw = client.recipe(rid, refresh=refresh)
            except BlizzardAPIError as exc:
                entry["problems"] = [str(exc)]
            else:
                conv = blizzard_api.recipe_from_api(raw, prof, tier, cat, today, find_item=find_item)
                entry.update(recipe=conv.recipe, problems=conv.problems, notes=conv.notes, reagents=conv.reagents)
            converted.append(entry)
            progress.progress(n / len(wanted), text=f"Recipes {n} / {len(wanted)}")
        ids = {g.item_id for e in converted for g in e["reagents"]}
        ids |= {e["recipe"].crafted_item_id for e in converted if e["recipe"]}
        infos, errors = item_info.fetch(client, ids, lambda f: progress.progress(f, text="Item icons and binding..."))
        with db() as conn:
            database.save_item_info(conn, infos.values())
        for e in converted:
            if e["recipe"] is not None:
                e["recipe"] = item_info.apply_to_recipe(e["recipe"], infos)
        st.session_state[f"{prefix}_converted"] = converted
        st.session_state[f"{prefix}_item_errors"] = errors

    converted = st.session_state.get(f"{prefix}_converted")
    if not converted:
        return
    for error in st.session_state.get(f"{prefix}_item_errors", [])[:5]:
        st.caption(f"Item info: {error}")
    with db() as conn:
        infos = database.load_item_info(conn)

    def reagent_text(reagents) -> str:
        parts = []
        for g in reagents:
            info = infos.get(g.item_id)
            bound = " (soulbound)" if info and info.soulbound else ""
            parts.append(f"{g.quantity}x {item_info.badge(g.quality_label, info)}{g.item_name or g.item_id}{bound}")
        return ", ".join(parts)

    frame = pd.DataFrame([{
        "Icon": (infos.get(e["recipe"].crafted_item_id).icon_url
                 if e["recipe"] and infos.get(e["recipe"].crafted_item_id) else None),
        "Recipe": e["name"], "Status": "ready" if e["recipe"] else "skipped",
        "Crafted item ID": e["recipe"].crafted_item_id if e["recipe"] else None,
        "Reagents": reagent_text(e["recipe"].reagents if e["recipe"] else e["reagents"]),
        "Problems / notes": "; ".join(e["problems"] + e["notes"])} for e in converted])
    st.markdown("**Preview** - for a skipped recipe you can type the crafted item ID (look the item up in game or "
                "on the AH) and it is imported too.")
    edited = st.data_editor(frame, hide_index=True, key=f"{prefix}_preview_editor",
                            disabled=["Icon", "Recipe", "Status", "Reagents", "Problems / notes"],
                            column_config={"Icon": st.column_config.ImageColumn(width="small"),
                                           "Crafted item ID": st.column_config.NumberColumn(min_value=1, step=1,
                                                                                            format="%d")})
    ready = []
    for e, (_, row) in zip(converted, edited.iterrows()):
        if e["recipe"] is not None:
            ready.append(e["recipe"])
        elif pd.notna(row["Crafted item ID"]) and e["reagents"]:
            typed = int(row["Crafted item ID"])
            raw = client.recipe(e["rid"])
            conv = blizzard_api.recipe_from_api(raw, *e["meta"], date.today().isoformat(),
                                                find_item=lambda _n, i=typed: [(i, None)])
            if conv.recipe is not None:
                ready.append(item_info.apply_to_recipe(replace(conv.recipe, notes=(
                    conv.recipe.notes + "; " if conv.recipe.notes else "") + "Crafted item ID typed by the user."),
                    infos))
    soulbound = sum(1 for r in ready for g in r.reagents if not g.auctionable)
    st.caption(f"{len(ready)} ready, {len(converted) - len(ready)} skipped. {soulbound} soulbound reagent line(s) are "
               "marked as not on the AH (they cost nothing unless you set an opportunity cost on *Stock & costs*). "
               "Importing replaces recipes with the same key (bnet-<recipe id>), so re-importing updates them.")
    with st.expander("Show what Blizzard sent for one recipe (to check or report a problem)"):
        options = {f"{e['name']} (recipe {e['rid']})": e["rid"] for e in converted}
        pick = st.selectbox("Recipe", list(options), key=f"{prefix}_raw_pick")
        try:
            st.json(client.recipe(options[pick]))
        except BlizzardAPIError as exc:
            st.error(str(exc))
        st.caption("Copy this into the chat if a recipe is skipped or looks wrong - it contains no personal data.")
    if ready and st.button(f"Import {len(ready)} recipe(s)", type="primary", key=f"{prefix}_import"):
        with db() as conn:
            for recipe in ready:
                database.save_recipe(conn, recipe)
        st.session_state.pop(f"{prefix}_converted", None)
        st.success(f"Imported {len(ready)} recipe(s). Check a few quantities in game and set a last-verified date on "
                   "the Recipes tab.")


def render_item_info_refresh(recipe_list: list, key: str) -> None:
    """Button: fetch icons / soulbound / quality for every reagent and crafted item of the given recipes."""
    ids = {g.item_id for r in recipe_list for g in r.reagents} | {r.crafted_item_id for r in recipe_list}
    client_id, secret = _credentials()
    if not ids or not (client_id and secret):
        return
    if st.button(f"Update item icons / soulbound info from Blizzard ({len(ids)} items)", key=key):
        progress = st.progress(0.0)
        try:
            infos, errors = item_info.fetch(_client(), ids, progress.progress)
        except BlizzardAPIError as exc:
            st.error(str(exc))
            return
        with db() as conn:
            database.save_item_info(conn, infos.values())
        st.success(f"Updated {len(infos)} item(s); {sum(i.soulbound for i in infos.values())} are soulbound / "
                   "account-bound and are treated as not on the AH." + (f" {len(errors)} failed." if errors else ""))
