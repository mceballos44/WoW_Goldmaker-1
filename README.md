# WoW Goldmaker

A local, browser-based World of Warcraft Retail goldmaking dashboard
(Python + Streamlit + Pandas + SQLite). Nothing is deployed or sent to the cloud;
the only network access is downloading TradeSkillMaster's public CSV files.

**Current status: Stage 1 of 5, plus character import** - price download, local
cache, searchable price table, manual price overrides, and importing your alts'
professions from the CharExporter addon. The other tabs are placeholders.

## Working in VS Code (recommended)

Double-click **`open_in_vscode.bat`**. It creates the `.venv`, installs the dependencies and
opens this folder in VS Code (needs Python 3.10+ and VS Code; the installer normally puts
`code` on your PATH). After that, day to day:

1. VS Code asks whether you trust the folder - choose **Trust** - and offers the recommended
   Python extensions - install them. If the status bar (bottom right) does not already show
   the `.venv` interpreter, click it and pick `.venv`.
2. Start the app: **Ctrl+Shift+B** (task *Run Goldmaker*) or **F5** (same, with the debugger -
   breakpoints in `app.py`, `ui_*.py` and `goldmaker/*.py`).
3. View it inside VS Code: **Ctrl+Shift+P -> "Simple Browser: Show"** -> `http://localhost:8501`.
   Drag that tab next to your code to work side by side. (Or Ctrl+click the URL in the
   terminal to use your normal browser.)
4. Edit and **save**: the app re-runs by itself (`runOnSave` in `.streamlit/config.toml`).
5. Tests: the beaker icon in the sidebar (pytest is pre-configured), or the task *Run tests*
   (Terminal -> Run Task).
6. Stop the app with the trash-can icon on its terminal panel (or Ctrl+C).

What is set up for you: `.vscode/settings.json` (interpreter, pytest), `launch.json` (debug
the Streamlit app / tests), `tasks.json` (run app, run tests, update environment),
`extensions.json` (recommended extensions) and `.streamlit/config.toml`.

Caveats: these files are standard VS Code configuration and the JSON is validated, but I could
not run VS Code itself while building them, so tell me if a task or debug config misbehaves.
Streamlit usually works inside Simple Browser, but file uploads and (from Stage 5) Excel
downloads may be restricted in an embedded browser - use your normal browser for those.
`settings.json` points at the Windows interpreter path (`.venv/Scripts/python.exe`).

## Run it without VS Code (Windows 11)

Double-click `run_app.bat`. The first run creates a `.venv`, installs the
dependencies and opens the app at http://localhost:8501.

Manual alternative in a VS Code terminal:

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Use it

1. Pick **Region** and **Realm slug** in the sidebar (default `us` / `area-52`).
   The slug is the last part of your realm's address on tradeskillmaster.com
   (e.g. `https://tradeskillmaster.com/retail/us/area-52`).
2. Click **Refresh Prices**. Three datasets can be downloaded:
   commodities (region-wide), realm items, and region items.
3. Open **Price Database** to search items and see prices in gold.
4. Add a **manual override** for any item (including items TSM does not list,
   such as non-auctionable materials). Overrides are stored separately and never
   modify the TSM data.

## Importing your alts (CharExporter)

The **Character Profiles** tab reads professions from the free
[CharExporter](https://www.curseforge.com/wow/addons/charexporter) addon
(by zwinout). One-time setup:

1. Install CharExporter into `World of Warcraft\_retail_\Interface\AddOns`.
2. In game: `/charexport`, keep **Professions** ticked, tick **Auto-save on Logout**.
3. Log in to each alt and log out (or `/reload`). WoW writes the file only then.
4. In this app: *Character Profiles -> Import from CharExporter -> Read SavedVariables
   from this PC*, point it at your WoW folder, **Find**, **Read file**, review the
   preview, **Import selected**.

The file lives at
`World of Warcraft\_retail_\WTF\Account\<ACCOUNT>\SavedVariables\CharExporter.lua`
and holds every alt that logged out with auto-save on. You can also upload that
file, or paste the JSON / CSV from the `/charexport` window (single character).
Try it first with the files in `samples/` (synthetic characters, not real data).

**What is imported:** skill level, concentration (value at export time), knowledge
points spent, specialization tree, profession gear (item names/levels).

**What is NOT available from any addon I found:** Multicraft, Resourcefulness and
Ingenuity percentages. Type them in on the *Profession stats* tab. They are stored
separately, so re-importing never overwrites them, and an empty field means
"not entered" (never 0%).

Safety rules:
* An older snapshot never replaces a newer one unless you tick the override box.
* The Lua file is parsed as data only; nothing in it is ever executed.
* Concentration is a snapshot and is not estimated forward.

Unverified points (kept visible in the UI):
* The SavedVariables layout (`CharExporterDB.global["Name-Realm"]`) was read from the
  addon's source code, not from a real saved file. If your first import finds nothing
  or errors, open the file and check it starts with `CharExporterDB = {`.
* "Recipes known" is stored but not trusted: the addon's API call does not name the
  profession.
* "Skill as exported" may be the base profession rather than the current-expansion
  crafting skill - hence the separate manual *Crafting skill* field.
* CSV exports carry the client's local time without a time zone; it is read as this
  computer's local time.
* Profession names follow your game client's language.

## Milling calculator (Stage 2)

*Milling -> Calculator* gives, for any herb: profit, ROI, cost, gross value, AH fees,
net revenue, the **maximum herb price** that still meets your target ROI (0% = break-even),
profit per 1 / 100 / 1,000 herbs, a per-output breakdown, a liquidity check and charts.
Prices are prefilled from your price data and can be edited for that calculation only.
A missing price blocks the profit calculation (it is never counted as 0).

**Yields come only from your experiments** (*Milling -> Experiments*) or numbers you type:
no drop rates are built in. Record each batch separately.

* With one experiment you get a point estimate and a warning - not a drop rate.
* With two or more, you get a standard error and a 95% interval (each experiment is one
  observation; Student-t). The labels "low" / "moderate" use rule-of-thumb thresholds
  (5+ experiments and 200+ herbs for "moderate") - they are not statistical guarantees.
* An output that did not appear in an experiment counts as 0 for it.

Your first batch (40 Felwort bought at 72g -> 500 Roseate Pigment, 100 Sallow Pigment) can
be loaded with one click; item IDs are looked up by exact name in your cached prices, and
you are asked to choose if a name matches several items. With your observed prices
(Roseate 2g, Sallow 40g) and a 5% AH cut, that works out to 125g gross / 118.75g net per herb,
46.75g profit per herb, 64.9% ROI, and a break-even herb price of 118.75g
(1,870g profit on the 40 herbs). This is checked by the automated tests.

Assumptions shown next to every result: the AH commission is a single percentage
(default 5%, editable in *Settings*; per-auction rounding and deposits ignored);
revenue is linear in quantity (selling more does not move the price); unsold output is
worth 0. The liquidity check compares expected units with TSM's `soldPerDay` and warns above
one day of volume - a rule of thumb, and `soldPerDay` may be missing from some files.

## How prices are chosen for an item

1. Manual override, if you set one
2. Otherwise the first of **commodities -> realm -> region** that has a value for
   the selected *price basis* (market value, min buyout, recent, historical, avg sale price)
3. Otherwise the price is **missing** - shown as blank and returned as `None`,
   never as 0.

## Data honesty notes

* TSM files are periodic **snapshots**. The app shows TSM's own `updatedAt`
  time and flags data older than the thresholds in `goldmaker/config.py`
  (12 h realm/commodities, 48 h region). Those thresholds are this app's
  choice, not a TSM guarantee.
* The parser does not assume every file has every column. Headers are matched
  case-insensitively; anything not found is left blank. See
  *Price Database -> Dataset details* for the columns seen/missing per file.
* A price of 0 or below in a TSM file is treated as "no price".
* `updatedAt` is parsed as unix seconds, unix milliseconds or an ISO date string.
* Pet files are not used yet (they are keyed by species, not item).
* If a download fails, the previous cached data is kept and a warning is shown.
  If TSM is unreachable, save the CSV in your browser and use
  *Import a TSM CSV file manually*.
* Overrides apply to an item ID across all realms.
* Unverified: the exact column headers in each TSM file (the public-data page
  was fetched while building this, but its column tables did not load), and the
  `kr` / `tw` region slugs. The tolerant parser and the "columns seen" display
  exist so you can check this on your first download.

## Project layout

```
app.py                  Streamlit UI (tabs, sidebar)
goldmaker/
  config.py             paths, URLs, thresholds
  currency.py           copper <-> gold (Decimal-safe), formatting
  tsm_client.py         URL building, download, CSV normalisation, ingest
  database.py           SQLite: datasets, prices, overrides, settings
  pricing.py            price resolution, overrides, staleness, display table
  lua_parser.py         safe Lua data parser (SavedVariables), never executes code
  charexporter.py       CharExporter SavedVariables / JSON / CSV parsing
  wow_files.py          finds CharExporter.lua on disk
  profiles.py           merged profession profiles for calculators
  milling.py            milling profit maths and yield calibration (pure functions)
ui_profiles.py          Character Profiles tab
ui_milling.py           Milling tab
ui_common.py            shared widgets (item picker)
open_in_vscode.bat      set up + open this folder in VS Code
setup_env.bat           create .venv and install dependencies
.vscode/ .streamlit/    VS Code settings/tasks/debug configs, Streamlit config
samples/                synthetic CharExporter files to try the importer
tests/                  pytest suite
data/                   created at runtime: goldmaker.sqlite3 and raw CSV cache
```

## Tests

```bat
.venv\Scripts\activate
pytest
```

Covers milling maths (including your Felwort batch), yield statistics, experiment storage, the Lua parser, CharExporter parsing (all three formats), snapshot-ordering rules,
manual-stat preservation, profile completeness, currency conversion, URL building, CSV normalisation (including missing
columns), failed-download handling, price priority, overrides, missing-price
handling and staleness. Tests use synthetic CSV data, not live TSM files.

## Roadmap

* Stage 2: milling calculator + experiment tracking - **done**
* Stage 3: generic crafting recipes + housing calculator (CSV/JSON recipe import)
* Stage 4: prospecting, jewelcrafting/alchemy calculators using the saved profiles
* Stage 5: opportunity scanner, investment scenarios, Excel import/export
