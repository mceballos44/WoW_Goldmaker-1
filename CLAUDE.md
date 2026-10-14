# WoW Goldmaker - notes for Claude sessions

Local Streamlit app (Python, Pandas, SQLite) for World of Warcraft Retail goldmaking analysis.
Owner: Deadwalker2000. The repo is PUBLIC - never commit personal data.

## Run and test
* VS Code: Ctrl+Shift+B runs the app (task "Run Goldmaker"), F5 debugs, `pytest` is configured.
* Terminal: `.venv\Scripts\python.exe -m streamlit run app.py` and `.venv\Scripts\python.exe -m pytest -q`.
* Run the full test suite before every commit. All tests use synthetic data (no network).

## Layout
* `goldmaker/` - logic, no Streamlit imports. Pure functions where possible (`conversion.py`, `milling.py`,
  `crafting.py`, `scanner.py`, `investment.py`).
* Milling and prospecting share `conversion.py` (maths) and `ui_processing.py` (UI). Add a new
  "input -> outputs" process there (and to `database.PROCESSES`), never by copy-paste.
* Jewelcrafting / Alchemy reuse `ui_crafting.render_calculator` / `render_compare` with a `prefix` (widget keys
  must be unique per tab) and a `profession` filter.
* `app.py`, `ui_*.py` - Streamlit UI only; keep maths out of them.
* Schema changes: add new columns to `_ADDED_COLUMNS` in `database.py` so existing user databases upgrade
  in place; never drop or rename columns (the experiments table keeps its `milling_`/`herb_` names).
* Reference yields (outside data such as Wowhead) live in `reference_yields`, never in the experiment tables,
  and keep reliability `reference` so every result says so. Do not scrape Wowhead.
* Character auto-import (`goldmaker/auto_import.py`) runs on every app run; it must stay read-only on the WoW
  folder and keep the snapshot-ordering rules.
* Blizzard API (`goldmaker/blizzard_api.py`): credentials only in the settings table (local DB), never in files.
  Imported recipes stay unverified (no `last_verified`) and report gaps instead of guessing.
* Blizzard AH prices are datasets with scopes `bnet_commodities` / `bnet_realm` (`config.BNET_SCOPES`); order via
  `config.source_priority()`. Profile API data lives in `character_profession_tiers` / `character_known_recipes`,
  never in `character_professions` (CharExporter's). OAuth user tokens are never stored.
* Inputs the user should not retype: give the widget `value=saved(key, default)` (or `saved_index` /
  `saved_choices`) from `ui_common`; `persist_ui_state()` at the end of `main()` stores them. Do not persist
  prefilled prices or the per-tab AH cut. Manual yields live in `manual_yields`.
* Item facts (icon, binding, rarity, crafting tier) live in `item_info`; `ui_crafting.load_recipes()` applies them so
  soulbound reagents are never auctionable. Self-gathered costs: `crafting.resource_costs()` (typed cost wins,
  then gold-per-hour / units-per-hour). Never infer quality tiers from IDs or names.
* `TODO.md` - open issues, things to verify, ideas. Keep it current with every change.
* `tests/` - pytest. `samples/` - FAKE example files only.
* `data/` - runtime SQLite and cached CSVs; git-ignored.

## Hard rules
* Money is COPPER internally (10,000 copper = 1 gold). Convert only at the UI edge (`goldmaker/currency.py`).
* A missing price is `None`/NaN and must never become 0. Show what is missing; block the calculation.
* Do not invent game data: no recipe quantities, drop rates, profession mechanics, item IDs or TSM endpoints.
  Where verified data is unavailable, use editable inputs/imports and say what is unverified.
* TSM data is a periodic snapshot, never "real-time". Always show its timestamp and flag stale data.
* Resourcefulness, Multicraft etc. are modelled separately, only from user-entered measured values.
* Label assumptions next to results (AH cut, linear revenue, sell-through).
* Never write into WoW game folders; SavedVariables are read-only input.
* Never commit `data/`, real SavedVariables, tokens or character names.

## Workflow
* Commit only when asked; small, logical commits; describe the why.
* Changes may arrive as a `git bundle` from a claude.ai chat: `git pull <file>.bundle main`.

## Roadmap
Done: Stages 1-5 (prices, character import, milling, recipes/crafting/housing, prospecting + Jewelcrafting/Alchemy
with profiles and quality tiers, opportunity scanner, AH investment, Excel import/export).
Next: verify against the user's real data - see `TODO.md`.
