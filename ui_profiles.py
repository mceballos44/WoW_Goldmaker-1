"""Character Profiles tab: import alts from CharExporter, view them, enter crafting stats."""

from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

from goldmaker import auto_import, blizzard_api, blizzard_profile, charexporter, database, profiles, tsm_client, wow_files
from ui_common import saved

SOURCE_DISK = "Read SavedVariables from this PC (all alts)"
SOURCE_UPLOAD = "Upload a file"
SOURCE_PASTE = "Paste export text"
STATUS_LABELS = {
    "new": "New",
    "updated": "Newer than stored",
    "unchanged": "Same snapshot as stored",
    "older": "OLDER than stored",
}


def db():
    return closing(database.connect())


def _fmt_time(value) -> str:
    if value is None or pd.isna(value):
        return "unknown"
    return pd.Timestamp(value).strftime("%Y-%m-%d %H:%M UTC")


def render() -> None:
    st.header("Character Profiles")
    st.caption("Import your alts' professions from the CharExporter addon, then enter the crafting "
               "stats it cannot export. Saved profiles feed the calculators in later stages.")
    results = st.session_state.pop("cp_last_results", None)
    if results:
        for kind, message in results:
            getattr(st, kind)(message)

    tab_import, tab_bnet, tab_chars, tab_stats = st.tabs(
        ["Import from CharExporter", "Battle.net (all characters)", "Characters", "Profession stats"])
    with tab_import:
        render_import()
    with tab_bnet:
        render_battlenet()
    with tab_chars:
        render_characters()
    with tab_stats:
        render_stats()


# --- Import -----------------------------------------------------------------------------

WATCH_SECONDS = 15


def run_auto_import(force: bool = False) -> bool:
    """Import characters if CharExporter.lua changed since the last check. Returns True if any were stored.

    Cheap when nothing changed (only the file's last-written time is read).
    """
    with db() as conn:
        if not force and not auto_import.is_enabled(conn):
            return False
        try:
            report = auto_import.check_and_import(conn, force=force)
        except Exception as exc:  # never let a bad file stop the app
            st.session_state["cp_auto_errors"] = [f"Automatic character import failed: {exc}"]
            return False
    if report.files_read or force or "cp_auto_report" not in st.session_state:
        st.session_state["cp_auto_report"] = report      # keep the last report that actually read a file
    st.session_state["cp_auto_errors"] = [f"Character import: {e}" for e in report.errors]
    if report.changed:
        stamp = datetime.now().strftime("%H:%M:%S")
        st.session_state["cp_auto_message"] = f"{stamp} - characters updated from CharExporter: " + \
                                              ", ".join(report.changed)
        st.toast(f"Imported {len(report.changed)} character snapshot(s) from CharExporter")
    return bool(report.changed)


@st.fragment(run_every=WATCH_SECONDS)
def auto_import_watcher() -> None:
    """Re-checks the file every few seconds while the app is open, so each logout is caught even if
    CharExporter's file only holds the character that logged out last. Put it in the sidebar."""
    if run_auto_import():
        st.rerun(scope="app")                            # refresh every tab with the new characters
    message = st.session_state.get("cp_auto_message")
    if message:
        st.success(message)
    for error in st.session_state.get("cp_auto_errors", []):
        st.warning(error)


def _render_auto_import() -> None:
    with db() as conn:
        enabled = auto_import.is_enabled(conn)
        locations = auto_import.search_locations(conn)
    st.markdown("**Automatic import**")
    new_value = st.toggle(f"Import automatically whenever WoW has written a new CharExporter.lua "
                          f"(checked every {WATCH_SECONDS} s while the app is open)",
                          value=enabled, key="cp_auto_toggle",
                          help="Each logout makes WoW rewrite the file; the app reads it within a few seconds and "
                               "keeps every character it has seen, so switching alts one after another is fine "
                               "while the app is open. Only the file's last-written time is read unless it changed. Older snapshots never replace newer ones; your typed-in "
                               "stats are never touched; nothing is written to the WoW folder.")
    if new_value != enabled:
        with db() as conn:
            auto_import.set_enabled(conn, new_value)
        st.rerun()
    report = st.session_state.get("cp_auto_report")
    if not locations:
        st.caption("No WoW folder known yet - set it once below (*Find CharExporter.lua*) and it is remembered.")
    elif report is not None:
        if report.files:
            st.caption(f"Watching: {', '.join(report.files)}. "
                       f"Last check imported {len(report.changed)} new/updated snapshot(s).")
        else:
            st.caption(f"No CharExporter.lua found in {', '.join(locations)} yet. WoW creates it when you log out "
                       "(or /reload) with the addon's Auto-save on Logout ticked.")
    if st.button("Check now", key="cp_auto_now"):
        run_auto_import(force=True)
        st.rerun()
    st.divider()


def render_import() -> None:
    with st.expander("How to set up CharExporter (one time)", expanded=False):
        st.markdown(
            "1. Install **CharExporter** (CurseForge: *CharExporter* by zwinout) into "
            "`World of Warcraft\\_retail_\\Interface\\AddOns`.\n"
            "2. In game, type `/charexport`, make sure the **Professions** category is ticked, and "
            "tick **Auto-save on Logout**.\n"
            "3. Log in to each alt, open the window once (so the data is gathered out of combat), "
            "and log out - or `/reload`. WoW only writes the file on logout/reload.\n"
            "4. Here, choose *Read SavedVariables from this PC*. One file holds every alt that "
            "has logged out with auto-save on.\n\n"
            "**Not exported by CharExporter:** Multicraft, Resourcefulness and Ingenuity "
            "percentages. Enter those on the *Profession stats* tab. Re-importing never "
            "overwrites what you typed.")

    _render_auto_import()
    method = st.radio("Manual import - source", [SOURCE_DISK, SOURCE_UPLOAD, SOURCE_PASTE], horizontal=True,
                      key="cp_method")
    if method == SOURCE_DISK:
        _source_from_disk()
    elif method == SOURCE_UPLOAD:
        _source_from_upload()
    else:
        _source_from_paste()

    for line in st.session_state.get("cp_file_report", []) if method == SOURCE_DISK else []:
        st.caption(line)
    pending = st.session_state.get("cp_pending")
    if pending:
        st.divider()
        _render_pending(pending)


def _parse_into_pending(text: str, label: str) -> None:
    try:
        result = charexporter.parse_any(text)
    except charexporter.CharExporterError as exc:
        st.session_state.pop("cp_pending", None)
        st.error(str(exc))
        return
    st.session_state["cp_pending"] = {"result": result, "label": label}


def _source_from_disk() -> None:
    with db() as conn:
        saved = database.get_setting(conn, "wow_path", "")
    roots = wow_files.default_wow_roots()
    default = saved or (str(roots[0]) if roots else r"C:\Program Files (x86)\World of Warcraft")
    path_text = st.text_input(
        "WoW folder, WTF\\Account folder, or the CharExporter.lua file", value=default, key="cp_path",
        help="Any of: the game folder, ...\\_retail_, ...\\WTF\\Account, an account folder, "
             "or the full path to CharExporter.lua")
    if st.button("Find CharExporter.lua"):
        with db() as conn:
            database.set_setting(conn, "wow_path", path_text)
        st.session_state["cp_found"] = [str(p) for p in wow_files.find_charexporter_files(path_text)]
        st.session_state["cp_found_for"] = path_text

    found = st.session_state.get("cp_found", [])
    if found:
        def label(p: str) -> str:
            try:
                stamp = datetime.fromtimestamp(Path(p).stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            except OSError:
                stamp = "?"
            return f"{p}   (file last written {stamp})"

        st.markdown(f"**{len(found)} CharExporter file(s) found** - one per WoW account folder:")
        st.caption("\n".join(f"- {label(p)}" for p in found))
        st.caption("All of them are read together, so alts on different WoW accounts all appear. The last-written "
                   "time is when WoW last saved the file (on logout / reload).")
        if st.button("Read all files", type="primary"):
            _read_files(found)
    elif "cp_found_for" in st.session_state:
        st.warning("No CharExporter.lua found there. It is only created after you log out (or "
                   "/reload) with **Auto-save on Logout** ticked. Check the folder, or use "
                   "*Upload a file* / *Paste export text* instead.")


def _read_files(paths: list) -> None:
    """Parse every file and merge the characters into one preview (newest snapshot per character wins)."""
    merged: dict = {}
    warnings, per_file = [], []
    for path in paths:
        try:
            result = charexporter.parse_savedvariables(wow_files.read_text_file(path))
        except (OSError, ValueError) as exc:
            per_file.append(f"{path}: could not be read - {exc}")
            continue
        per_file.append(f"{path}: {len(result.characters)} character(s)")
        warnings.extend(f"{Path(path).parent.parent.name}: {w}" for w in result.warnings)
        for c in result.characters:
            key = c.character_key or id(c)
            old = merged.get(key)
            if old is None or (c.snapshot_at and (old.snapshot_at is None or c.snapshot_at > old.snapshot_at)):
                merged[key] = c
    st.session_state["cp_file_report"] = per_file
    st.session_state["cp_pending"] = {
        "result": charexporter.ParseResult(characters=list(merged.values()), source_format="savedvariables",
                                           warnings=warnings),
        "label": f"{len(paths)} file(s)"}


def _source_from_upload() -> None:
    upload = st.file_uploader("CharExporter.lua, or a saved JSON/CSV export",
                              type=["lua", "json", "csv", "txt"], key="cp_upload")
    if upload is not None and st.button("Read file", type="primary"):
        _parse_into_pending(upload.getvalue().decode("utf-8-sig", errors="replace"), upload.name)


def _source_from_paste() -> None:
    text = st.text_area("Paste the JSON or CSV from the /charexport window", height=200, key="cp_paste")
    if st.button("Read pasted text", type="primary"):
        _parse_into_pending(text, "pasted text")


def _render_pending(pending: dict) -> None:
    result = pending["result"]
    st.subheader("Preview")
    st.caption(f"Read {len(result.characters)} character(s) from **{pending['label']}** "
               f"({result.source_format}). Nothing is saved until you press Import.")
    for warning in result.warnings:
        st.warning(warning)
    for character in result.characters:
        for warning in character.warnings:
            st.warning(f"{character.character_key or 'Character'}: {warning}")
    if not result.characters:
        return

    needs_identity = any(not c.has_identity for c in result.characters)
    if needs_identity:
        st.info("This export has no character name/realm (the 'Basic' category was off). "
                "Fill them in below.")

    rows = []
    with db() as conn:
        for c in result.characters:
            status = database.snapshot_status(conn, c) if c.has_identity else "new"
            rows.append({
                "Import": status in ("new", "updated"),
                "Name": c.name, "Realm": c.realm, "Class": c.class_name, "Level": c.level,
                "Snapshot time (UTC)": c.snapshot_at,
                "Professions": ", ".join(p.name for p in c.professions) or "(none in export)",
                "Compared with stored": STATUS_LABELS[status],
            })
    editable = ["Import", "Name", "Realm"] if needs_identity else ["Import"]
    edited = st.data_editor(
        pd.DataFrame(rows), hide_index=True, key="cp_preview",
        disabled=[c for c in rows[0] if c not in editable],
        column_config={
            "Import": st.column_config.CheckboxColumn(),
            "Level": st.column_config.NumberColumn(format="%d"),
            "Snapshot time (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        })

    force = st.checkbox("Allow older or identical snapshots to replace stored data", key="cp_force",
                        help="Normally an older snapshot is skipped so a stale file cannot overwrite "
                             "fresher data.")
    c1, c2 = st.columns([1, 1])
    if c1.button("Import selected", type="primary"):
        messages = []
        with db() as conn:
            for (_, row), original in zip(edited.iterrows(), result.characters):
                if not row["Import"]:
                    continue
                snap = charexporter.apply_identity(original, str(row["Name"] or ""), str(row["Realm"] or ""))
                if not snap.has_identity:
                    messages.append(("error", "A selected character has no name/realm; it was not imported."))
                    continue
                status = database.save_character_snapshot(conn, snap, force=force)
                if status in ("new", "updated"):
                    messages.append(("success", f"{snap.character_key}: {STATUS_LABELS[status].lower()} "
                                                f"- {len(snap.professions)} profession(s) saved."))
                else:
                    messages.append(("info", f"{snap.character_key}: skipped "
                                             f"({STATUS_LABELS[status].lower()})."))
        if not messages:
            messages.append(("info", "Nothing was selected."))
        st.session_state["cp_last_results"] = messages
        st.session_state.pop("cp_pending", None)
        st.rerun()
    if c2.button("Discard preview"):
        st.session_state.pop("cp_pending", None)
        st.rerun()


# --- Characters ---------------------------------------------------------------------------

def render_characters() -> None:
    with db() as conn:
        chars = database.list_characters(conn)
        profs = database.list_character_professions(conn)
    if chars.empty:
        st.info("No characters imported yet. Use the *Import from CharExporter* tab.")
        return

    now = datetime.now(timezone.utc)
    chars["snapshot_dt"] = pd.to_datetime(chars["snapshot_at"], utc=True, errors="coerce")
    overview = pd.DataFrame({
        "Character": chars["character_key"],
        "Class": chars["class_name"],
        "Level": chars["level"],
        "Professions": chars["professions"].fillna(""),
        "Snapshot time (UTC)": chars["snapshot_dt"],
        "Snapshot age": [profiles.snapshot_age_text(t, now) for t in chars["snapshot_dt"]],
    })
    st.dataframe(overview, hide_index=True, column_config={
        "Level": st.column_config.NumberColumn(format="%d"),
        "Snapshot time (UTC)": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
    })
    old = chars[(now - chars["snapshot_dt"]).dt.days > profiles.SNAPSHOT_STALE_DAYS]
    if not old.empty:
        st.warning(f"Older than {profiles.SNAPSHOT_STALE_DAYS} days: " + ", ".join(old["character_key"])
                   + ". Log those alts in and out with CharExporter auto-save on, then re-import.")

    selected = st.selectbox("Character", chars["character_key"], key="cp_view_char")
    mine = profs[profs["character_key"] == selected]
    if mine.empty:
        st.caption("No professions stored for this character.")
    else:
        table = pd.DataFrame({
            "Profession": mine["profession"],
            "Skill as exported": [f"{s} / {m}" if pd.notna(s) else "?" for s, m in
                                  zip(mine["skill_level"], mine["max_skill_level"])],
            "Concentration (at snapshot)": [f"{c:g} / {m:g}" if pd.notna(c) and pd.notna(m) and m > 0 else "-"
                                            for c, m in zip(mine["concentration"], mine["concentration_max"])],
            "Knowledge points spent": mine["knowledge_points_spent"],
            "Recipes (unverified)": mine["recipes_known"],
        })
        st.dataframe(table, hide_index=True, column_config={
            "Knowledge points spent": st.column_config.NumberColumn(format="%d"),
            "Recipes (unverified)": st.column_config.NumberColumn(format="%d"),
        })
        st.caption("Concentration is the value when the data was exported - it changes over time "
                   "and is not estimated here. 'Recipes' comes from an addon call that does not name the "
                   "profession, so treat it as unverified. 'Skill as exported' may be the base profession "
                   "rather than the current-expansion crafting skill; use the stats tab for the latter.")

        profession = st.selectbox("Details for", mine["profession"], key="cp_view_prof")
        row = mine[mine["profession"] == profession].iloc[0]
        d1, d2 = st.columns(2)
        with d1:
            st.markdown("**Specializations**")
            spec_rows = profiles.specializations_to_rows(profiles.parse_json_column(row["specializations_json"]))
            if spec_rows:
                st.dataframe(pd.DataFrame(spec_rows), hide_index=True)
            else:
                st.caption("No specialization data.")
        with d2:
            st.markdown("**Profession gear**")
            gear = profiles.parse_json_column(row["equipment_json"])
            if gear:
                st.dataframe(pd.DataFrame(gear).drop(columns=["link"], errors="ignore")
                             .rename(columns={"name": "Item", "ilvl": "Item level", "slot": "Slot"}),
                             hide_index=True)
                st.caption("Gear stat bonuses are not exported, only the item.")
            else:
                st.caption("No profession gear exported.")

    with st.expander("Remove this character"):
        st.caption("Deletes the imported data and any stats you typed for this character.")
        if st.checkbox(f"Yes, remove {selected}", key=f"cp_del_ok_{selected}"):
            if st.button("Remove character"):
                with db() as conn:
                    database.delete_character(conn, selected)
                st.session_state["cp_last_results"] = [("success", f"Removed {selected}.")]
                st.rerun()


# --- Profession stats ----------------------------------------------------------------------

def render_stats() -> None:
    with db() as conn:
        choices = profiles.list_profile_choices(conn)
    if not choices:
        st.info("Import at least one character first.")
        return

    st.caption("CharExporter does not export these. Read them from the profession window "
               "(the stats shown on the crafting page) and type them in. Leave a field empty if you "
               "do not know it - empty means *not entered*, which is different from 0.")
    labels = [f"{c} - {p}" for c, p in choices]
    index = st.selectbox("Character and profession", range(len(choices)),
                         format_func=lambda i: labels[i], key="cp_stats_choice")
    key, profession = choices[index]

    with db() as conn:
        profile = profiles.load_profile(conn, key, profession)
        existing = database.get_profession_stats(conn, key, profession) or {}

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Skill as exported", f"{profile.skill_level_exported} / {profile.max_skill_level_exported}"
              if profile.skill_level_exported is not None else "?")
    m2.metric("Concentration (snapshot)",
              f"{profile.concentration_at_snapshot:g} / {profile.concentration_max:g}"
              if profile.concentration_at_snapshot is not None and profile.concentration_max else "-")
    m3.metric("Knowledge points spent", profile.knowledge_points_spent
              if profile.knowledge_points_spent is not None else "?")
    m4.metric("Snapshot", profiles.snapshot_age_text(profile.snapshot_at))

    suffix = f"{key}_{profession}"
    c1, c2, c3, c4 = st.columns(4)
    skill = c1.number_input("Crafting skill", min_value=0.0, value=existing.get("crafting_skill"),
                            step=1.0, placeholder="not entered", key=f"cp_skill_{suffix}",
                            help="The skill shown for the recipe you craft (current-expansion line).")
    multicraft = c2.number_input("Multicraft %", min_value=0.0, value=existing.get("multicraft_pct"),
                                 step=0.1, format="%.2f", placeholder="not entered", key=f"cp_mc_{suffix}")
    resourcefulness = c3.number_input("Resourcefulness %", min_value=0.0,
                                      value=existing.get("resourcefulness_pct"), step=0.1, format="%.2f",
                                      placeholder="not entered", key=f"cp_rs_{suffix}")
    ingenuity = c4.number_input("Ingenuity %", min_value=0.0, value=existing.get("ingenuity_pct"),
                                step=0.1, format="%.2f", placeholder="not entered", key=f"cp_in_{suffix}")
    st.caption("Optional, for the crafting calculators: how big each effect is when it happens. The game does not "
               "show these as one number - measure them from your own crafts (e.g. reagents returned per "
               "Resourcefulness proc). Without them, Resourcefulness / Multicraft stay switched off.")
    s1, s2, _ = st.columns([1, 1, 2])
    saved_pct = s1.number_input("Resourcefulness: share of eligible reagents saved per proc (%)", min_value=0.0,
                                max_value=100.0, value=existing.get("resourcefulness_saved_pct"), step=1.0,
                                placeholder="not measured", key=f"cp_rss_{suffix}")
    extra_pct = s2.number_input("Multicraft: extra output per proc (% of base)", min_value=0.0,
                                value=existing.get("multicraft_extra_pct"), step=5.0,
                                placeholder="not measured", key=f"cp_mce_{suffix}")
    notes = st.text_input("Notes (e.g. which gear/buffs these stats include)",
                          value=existing.get("notes") or "", key=f"cp_notes_{suffix}")

    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save stats", type="primary"):
        with db() as conn:
            database.upsert_profession_stats(conn, key, profession, skill, multicraft,
                                             resourcefulness, ingenuity, notes, saved_pct, extra_pct)
        st.toast(f"Saved stats for {key} - {profession}")
        st.rerun()
    if existing and b2.button("Clear saved stats"):
        with db() as conn:
            database.delete_profession_stats(conn, key, profession)
        st.rerun()

    for warning in profiles.profile_warnings(profile):
        st.warning(warning)
    if not profile.missing_stats and not profiles.profile_warnings(profile):
        st.success("This profile is complete and current.")

    with db() as conn:
        saved = database.list_profession_stats(conn)
    if not saved.empty:
        st.subheader("All saved stats")
        st.dataframe(saved.rename(columns={
            "character_key": "Character", "profession": "Profession", "crafting_skill": "Crafting skill",
            "multicraft_pct": "Multicraft %", "resourcefulness_pct": "Resourcefulness %",
            "ingenuity_pct": "Ingenuity %", "notes": "Notes", "updated_at": "Updated (UTC)",
            "resourcefulness_saved_pct": "Resourcefulness saved %", "multicraft_extra_pct": "Multicraft extra %"}),
            hide_index=True)


# --- Battle.net (Blizzard Profile API) -----------------------------------------------------------

def _bnet_client():
    with db() as conn:
        cid = database.get_setting(conn, blizzard_api.SETTING_ID, "") or ""
        secret = database.get_setting(conn, blizzard_api.SETTING_SECRET, "") or ""
    return blizzard_api.BlizzardClient(cid, secret, st.session_state.get("region", "us"))


def _redirect_uri() -> str:
    with db() as conn:
        return database.get_setting(conn, blizzard_profile.SETTING_REDIRECT, blizzard_profile.DEFAULT_REDIRECT)


def handle_oauth_callback() -> None:
    """When Battle.net sends the browser back with ?code=...&state=..., import the account's characters."""
    params = st.query_params
    code, state = params.get("code"), params.get("state")
    if not code:
        return
    with db() as conn:
        expected = database.get_setting(conn, blizzard_profile.SETTING_STATE, "")
        database.set_setting(conn, blizzard_profile.SETTING_STATE, "")          # one use only
    st.query_params.clear()
    if not expected or state != expected:
        st.session_state["cp_bnet_messages"] = [("error", "Battle.net sign-in could not be matched to this app "
                                                          "(old or foreign link). Please sign in again.")]
        return
    try:
        client = _bnet_client()
        token = blizzard_profile.exchange_code(client, code, _redirect_uri())
        characters = blizzard_profile.account_characters(client, token)
        with db() as conn:
            report = blizzard_profile.import_characters(conn, client, characters, token,
                                                        min_level=st.session_state.get("cp_bnet_minlevel", 10))
    except blizzard_api.BlizzardAPIError as exc:
        st.session_state["cp_bnet_messages"] = [("error", str(exc))]
        return
    st.session_state["cp_bnet_messages"] = _report_messages(report, len(characters))


def _report_messages(report, total: int) -> list:
    messages = [("success", f"Battle.net: {len(report.imported)} of {total} character(s) imported with their "
                            "professions and known recipes.")]
    if report.skipped:
        messages.append(("info", "Skipped: " + "; ".join(f"{k} ({why})" for k, why in report.skipped[:20])))
    return messages


def render_battlenet() -> None:
    for kind, message in st.session_state.pop("cp_bnet_messages", []):
        getattr(st, kind)(message)
    with db() as conn:
        has_key = bool(database.get_setting(conn, blizzard_api.SETTING_ID, ""))
    if not has_key:
        st.info("Add your Blizzard API client ID and secret on **Settings** first.")
        return
    st.caption("Gets every character on your Battle.net account with professions per expansion (e.g. *Legion "
               "Tailoring* skill) and the recipes each one knows - used by the Housing finder for *who can craft "
               "this*. Multicraft / Resourcefulness are not in Blizzard's data; keep typing those on *Profession stats*.")

    st.markdown("**Sign in with Battle.net** (lists all your characters)")
    redirect = st.text_input("Redirect URL", value=_redirect_uri(), key="cp_bnet_redirect",
                             help="Add exactly this address under *Redirect URLs* of your client on develop.battle.net. "
                                  "It is where Battle.net sends you back after signing in.")
    st.number_input("Skip characters below level", 1, 90, value=saved("cp_bnet_minlevel", 10), key="cp_bnet_minlevel")
    if st.button("Prepare sign-in link", key="cp_bnet_prepare"):
        state = blizzard_profile.new_state()
        with db() as conn:
            database.set_setting(conn, blizzard_profile.SETTING_REDIRECT, redirect.strip())
            database.set_setting(conn, blizzard_profile.SETTING_STATE, state)
        st.session_state["cp_bnet_link"] = blizzard_profile.authorize_url(_bnet_client().client_id, redirect.strip(),
                                                                          state)
    if st.session_state.get("cp_bnet_link"):
        st.link_button("Sign in with Battle.net", st.session_state["cp_bnet_link"], type="primary")
        st.caption("Opens Battle.net. After you allow access you come back to this app in a new tab and the "
                   "characters are imported. The sign-in token is used once and not saved.")

    st.divider()
    st.markdown("**Or refresh characters by name** (no sign-in needed)")
    c1, c2, c3 = st.columns([2, 2, 1])
    name = c1.text_input("Character name", key="cp_bnet_name")
    realm = c2.text_input("Realm slug", value=st.session_state.get("realm_slug", ""), key="cp_bnet_realm")
    if c3.button("Fetch", key="cp_bnet_fetch") and name.strip() and realm.strip():
        _run_profile_import([blizzard_profile.AccountCharacter(name.strip(), realm.strip().lower(),
                                                               _realm_name(realm.strip()))])
    with db() as conn:
        stored = database.list_characters(conn)
    if not stored.empty and st.button(f"Refresh professions of all {len(stored)} stored character(s)",
                                      key="cp_bnet_all"):
        _run_profile_import([blizzard_profile.AccountCharacter(r.name, tsm_client.slugify_realm(r.realm), r.realm)
                             for r in stored.itertuples()])
        st.caption("Realm slugs were derived from realm names; a realm with unusual characters may be skipped.")

    with db() as conn:
        tiers = database.list_profession_tiers(conn)
    if not tiers.empty:
        st.subheader("Profession skill per expansion")
        st.dataframe(tiers.drop(columns=["tier_id"]).rename(columns={
            "character_key": "Character", "profession": "Profession", "tier_name": "Skill tier",
            "skill_points": "Skill", "max_skill_points": "Max", "fetched_at": "Fetched (UTC)"}), hide_index=True)


def _realm_name(slug: str) -> str:
    """Display name for a slug typed by hand: an existing character's realm if it matches, else the slug."""
    with db() as conn:
        stored = database.list_characters(conn)
    for realm in stored["realm"].dropna().unique() if not stored.empty else []:
        if tsm_client.slugify_realm(realm) == slug.lower():
            return realm
    return slug.replace("-", " ").title()


def _run_profile_import(characters: list) -> None:
    try:
        client = _bnet_client()
        with db() as conn:
            report = blizzard_profile.import_characters(conn, client, characters)
    except blizzard_api.BlizzardAPIError as exc:
        st.error(str(exc))
        return
    for kind, message in _report_messages(report, len(characters)):
        getattr(st, kind)(message)
