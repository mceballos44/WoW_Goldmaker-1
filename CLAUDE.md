# WoW Goldmaker - notes for Claude sessions

Local Streamlit app (Python, Pandas, SQLite) for World of Warcraft Retail goldmaking analysis.
Owner: Deadwalker2000. The repo is PUBLIC - never commit personal data.

## Run and test
* VS Code: Ctrl+Shift+B runs the app (task "Run Goldmaker"), F5 debugs, `pytest` is configured.
* Terminal: `.venv\Scripts\python.exe -m streamlit run app.py` and `.venv\Scripts\python.exe -m pytest -q`.
* Run the full test suite before every commit. All tests use synthetic data (no network).

## Layout
* `goldmaker/` - logic, no Streamlit imports. Pure functions where possible (`milling.py`, `crafting.py`).
* `app.py`, `ui_*.py` - Streamlit UI only; keep maths out of them.
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
Done: Stage 1 (TSM prices/overrides), character import (CharExporter), Stage 2 (milling), Stage 3 (recipes/crafting/housing).
Next: Stage 4 (prospecting, jewelcrafting/alchemy using saved profiles), Stage 5 (opportunity scanner, AH investment scenarios, Excel import/export).
