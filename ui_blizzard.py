"""Blizzard Game Data API: credentials (Settings) and recipe import (Housing / Crafting)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from goldmaker import blizzard_api, database
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


def render_import() -> None:
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
                    horizontal=True, key="bnet_mode")
    try:
        if mode.startswith("Search"):
            found = _search_all(client)
        else:
            found = _browse(client)
    except BlizzardAPIError as exc:
        st.error(str(exc))
        return
    if found:
        _fetch_and_import(client, found)


def _search_all(client) -> list:
    words = st.text_input("Category name contains (comma-separated)", ", ".join(blizzard_api.HOUSING_KEYWORDS),
                          key="bnet_words",
                          help="Category names come from the API. If nothing matches, browse one profession to see "
                               "the real names and adjust these words.")
    only_latest = st.checkbox("Only each profession's newest skill tier (current expansion)", value=True,
                              key="bnet_latest")
    if st.button("Search", key="bnet_search"):
        hits = []
        professions = client.professions()
        progress = st.progress(0.0, text="Reading professions...")
        for n, (pid, pname) in enumerate(professions, start=1):
            tiers = client.skill_tiers(pid)
            if only_latest and tiers:
                tiers = [max(tiers, key=lambda t: t[0])]       # highest id = newest tier (an assumption, shown below)
            for tid, tname in tiers:
                for cat in blizzard_api.matching_categories(client.categories(pid, tid), words.split(",")):
                    hits.append({"profession": pname, "tier": tname, "category": cat["name"],
                                 "recipes": cat["recipes"]})
            progress.progress(n / max(len(professions), 1), text=f"Read {pname}")
        st.session_state["bnet_hits"] = hits
    hits = st.session_state.get("bnet_hits")
    if hits is None:
        if only_latest:
            st.caption("'Newest tier' is taken as the tier with the highest ID - untick to search every tier.")
        return []
    if not hits:
        st.warning("No category matched. Browse a profession to see its category names, then change the words.")
        return []
    labels = {f"{h['profession']} / {h['tier']} / {h['category']} ({len(h['recipes'])})": h for h in hits}
    chosen = st.multiselect("Categories to import", list(labels), default=list(labels), key="bnet_hit_pick")
    return [(labels[c]["profession"], labels[c]["tier"], labels[c]["category"], rid, rname)
            for c in chosen for rid, rname in labels[c]["recipes"]]


def _browse(client) -> list:
    professions = dict((name, pid) for pid, name in client.professions())
    pname = st.selectbox("Profession", sorted(professions), key="bnet_prof")
    tiers = dict((name, tid) for tid, name in client.skill_tiers(professions[pname]))
    if not tiers:
        st.info("This profession has no skill tiers in the API.")
        return []
    tname = st.selectbox("Skill tier (expansion)", list(tiers), index=len(tiers) - 1, key=f"bnet_tier_{pname}")
    cats = client.categories(professions[pname], tiers[tname])
    labels = {f"{c['name']} ({len(c['recipes'])})": c for c in cats}
    default = [l for l, c in labels.items() if blizzard_api.matching_categories([c], blizzard_api.HOUSING_KEYWORDS)]
    chosen = st.multiselect("Categories", list(labels), default=default, key=f"bnet_cats_{pname}_{tname}")
    return [(pname, tname, labels[c]["name"], rid, rname) for c in chosen for rid, rname in labels[c]["recipes"]]


def _fetch_and_import(client, wanted: list) -> None:
    st.caption(f"{len(wanted)} recipe(s) selected. Each is one request the first time; after that they come from "
               "a local cache (`data/cache/blizzard`).")
    refresh = st.checkbox("Re-download instead of using the cache", key="bnet_refresh")
    if st.button(f"Fetch {len(wanted)} recipe(s)", type="primary", key="bnet_fetch"):
        today, converted = date.today().isoformat(), []
        progress = st.progress(0.0)
        for n, (prof, tier, cat, rid, rname) in enumerate(wanted, start=1):
            try:
                raw = client.recipe(rid, refresh=refresh)
            except BlizzardAPIError as exc:
                converted.append((rname, None, [str(exc)], []))
            else:
                conv = blizzard_api.recipe_from_api(raw, prof, tier, cat, today)
                converted.append((rname, conv.recipe, conv.problems, conv.notes))
            progress.progress(n / len(wanted), text=f"{n} / {len(wanted)}")
        st.session_state["bnet_converted"] = converted

    converted = st.session_state.get("bnet_converted")
    if not converted:
        return
    ok = [c for c in converted if c[1] is not None]
    st.dataframe(pd.DataFrame([{
        "Recipe": name, "Status": "ready" if recipe else "skipped",
        "Crafted item ID": recipe.crafted_item_id if recipe else None,
        "Reagents": ", ".join(f"{g.quantity}x {g.item_name or g.item_id}" for g in recipe.reagents) if recipe else "",
        "Problems / notes": "; ".join(problems + notes)} for name, recipe, problems, notes in converted]),
        hide_index=True, column_config={"Crafted item ID": st.column_config.NumberColumn(format="%d")})
    st.caption(f"{len(ok)} ready, {len(converted) - len(ok)} skipped. Importing replaces recipes with the same key "
               "(bnet-<recipe id>), so re-importing updates them.")
    if ok and st.button(f"Import {len(ok)} recipe(s)", type="primary", key="bnet_import"):
        with db() as conn:
            for _, recipe, _, _ in ok:
                database.save_recipe(conn, recipe)
        st.session_state.pop("bnet_converted", None)
        st.success(f"Imported {len(ok)} recipe(s). They appear under Compare / Calculator; set quantities you "
                   "have checked in game and a last-verified date on the Recipes tab.")
