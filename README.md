# WoW Goldmaker

A local, browser-based World of Warcraft Retail goldmaking dashboard
(Python + Streamlit + Pandas + SQLite). Nothing is deployed or sent to the cloud;
the only network access is downloading TradeSkillMaster's public CSV files.

**Current status: all 5 stages built** - prices and overrides (1), character import,
milling (2), recipes and crafting/housing (3), prospecting plus Jewelcrafting / Alchemy with
character profiles (4), and the opportunity scanner, AH investment scenarios and Excel
import/export (5). Everything is tested with synthetic data only; nothing has been run against
real game data yet - see [`TODO.md`](TODO.md) for what still needs checking with you.

| Tab | What it does |
|---|---|
| Dashboard | Data freshness and counts |
| Opportunities | Every recipe and herb/ore with experiments, ranked against current prices |
| Milling / Prospecting | Calculator, experiment log with calibrated yields, comparison of herbs / ores |
| Jewelcrafting / Alchemy | Crafting calculator for that profession with your character profiles, quality tiers, break-even material prices |
| Housing / Crafting | All recipes: compare, calculate, import/export, stock and opportunity costs |
| AH Investment | Buy now, sell later: bearish / baseline / bullish scenarios |
| Price Database | Search prices, manual overrides, offline CSV import |
| Character Profiles | Import alts from CharExporter, type in profession stats |
| Import / Export | Excel workbook export, import template, import |
| Settings | Default AH cut, data paths |

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

**It is automatic.** With *Auto-save on Logout* ticked, CharExporter writes every alt into its
SavedVariables file each time you log out or `/reload` (WoW only lets addons write that one file, and
only then). Every time the app starts or refreshes it checks that file's last-written time and, when it
changed, imports the new snapshots - a note appears in the sidebar. While the app is open it also re-checks
every 15 seconds, so you can log out of one alt after another: each logout is picked up within seconds, and the
app keeps every character it has seen (a new import never removes the others). That also covers a file that
only ends up holding the last character. Every account folder (`WTF\Account\<each account>`) is read. The app looks in the WoW folder you saved once (*Find CharExporter.lua*), otherwise in the
usual install folders. Rules: older snapshots never replace newer ones, your typed-in stats are never
touched, nothing is ever written to the WoW folder. Turn it off or force a re-read with *Check now* at the
top of *Character Profiles -> Import from CharExporter*.

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

## Recipes, crafting and housing (Stage 3)

The **Housing / Crafting** tab has four parts:

* **Compare recipes** - ranks every recipe by profit per craft, ROI, profit at your stock limit or cost, with
  filters for profession, expansion, patch and category. Flags show unverified recipes, stale prices, missing
  prices and free reagents that were assumed.
* **Calculator** - one recipe in detail: cost, profit, ROI, break-even output price, profit for 1 / 10 / 100 /
  your own number of crafts, a reagent breakdown, stock limits and a TSM demand indicator. Prices are prefilled
  and editable for that calculation only.
* **Recipes** - list, edit, import (CSV or JSON, file or paste) and export. Importing a recipe whose key already
  exists replaces it; recipes with problems are skipped and the problems are listed.
* **Stock & costs** - units on hand and an optional opportunity cost for items you cannot simply buy.

**No recipe data ships with the app.** I could not verify a current, reliable recipe dataset (including the
Patch 12.1.5 housing quantities), so every reagent and quantity comes from you. Each recipe records its
`source`, `game_patch` and `last_verified` date; recipes without a verification date are flagged everywhere.
The example in `samples/recipes_EXAMPLE.csv` is deliberately fake (item IDs 999000xxx).

Recipe file format - one row per reagent (CSV) or a `reagents` list (JSON):
`recipe_key, name, crafted_item_id | crafted_item_name, output_quantity, profession, required_skill, expansion,
game_patch, game_recipe_id, source, last_verified, category, notes, reagent_slot, reagent_item_id | reagent_name,
reagent_quantity, quality_label, auctionable, savings_eligible`.
Names are accepted only when they match exactly one item in your cached prices; otherwise use the item ID.
Reagents sharing a `reagent_slot` are alternatives (for example quality tiers): the calculator uses the cheapest
priced one unless you pick.

How mechanics are handled (and not):
* Resourcefulness and Multicraft are **off by default** and modelled as two separate expected-value effects:
  Resourcefulness = chance x share of *savings-eligible* reagents saved; Multicraft = chance x extra output.
  Each applies only if you fill in **both** its chance and its size. The app knows neither number - enter
  values you have measured. They are never added together and no reagent is assumed eligible.
* Not modelled: Ingenuity, concentration, skill thresholds, and the price impact of
  selling many items. Output quality tiers and break-even material prices: see Stage 4 (they work on
  this tab too).
* A reagent marked `auctionable = false` is priced by your opportunity cost; with none set it counts as free
  **and the app says so**. Stock limits (`crafts possible`) use your recorded units on hand.
* Missing prices on auctionable items block the profit calculation; they are never treated as 0.
* Everything is theoretical margin at today's listed prices, not demonstrated sales.

## Prospecting (Stage 4)

*Prospecting* works exactly like Milling, for ore: a **Calculator** (profit, ROI, cost, the maximum ore
price for your target ROI, per-output breakdown, liquidity check), an **Experiments** log and
**Compare ore**, which ranks every ore that has experiments at current prices (for example Copper Ore vs
Silver Ore - by the item IDs in your data, never by similar names).

* Yields come only from your recorded batches; no gem distributions are built in. Record every output
  (each gem quality, crystal glass, anything else) as its own item.
* *Other cost per ore* covers a prospecting reagent or anything else you pay per ore (default 0).
* *Ore per prospect* is optional and only used for the "per prospect" figures - the app does not
  assume a number; check it in game.
* Profession talents that change yields are not modelled; they show up in your experiments.
* **Reference yields** (Milling and Prospecting each have a sub-tab): with no experiments yet, type in what
  a site such as Wowhead shows - per output, the chance per mill / prospect and the average quantity, plus
  how many herbs / ore one operation uses and the source. They are stored apart from your experiments,
  never pooled with them, and every result using them says "reference data - not your experiments".
  Once an item has your own experiments, those are used instead (Opportunities, comparisons).

Milling and prospecting share one module (`goldmaker/conversion.py`), so both behave identically.
Your existing milling experiments are kept (the database is upgraded in place the first time you
start this version).

## Jewelcrafting and Alchemy (Stage 4)

These tabs show the crafting calculator for one profession: recipes whose *profession* contains
"Jewelcrafting" / "Alchemy" (set it when you import or edit recipes). On top of Stage 3 they add:

* **Character profile** - pick an alt; its saved Resourcefulness % and Multicraft % prefill the
  mechanics. Each effect also needs its measured **size** (share of reagents saved per proc / extra
  output per proc), entered on *Character Profiles -> Profession stats*. Without a size the effect
  stays off and the app says so. Effects are never added together.
* **Skill vs recipe** - your crafting skill next to the recipe's required skill. Quality from skill vs
  difficulty is *not* modelled.
* **Break-even material prices** - for each reagent, the most you can pay (others at current prices)
  before the craft stops breaking even. Works even if that reagent has no price yet (e.g. an expensive
  herb), and includes expected Resourcefulness savings for savings-eligible reagents.
* **Output quality tiers** - each quality (silver, gold, ...) is its own item ID. You get profit per
  craft *if every craft lands in that tier* (is silver profitable? is gold?), the break-even price, and an
  expected profit only from **your own** odds of each tier (they must add up to 100%).
* **Profiles** sub-tab - every alt with that profession and which stats are still missing.

Not modelled: Ingenuity, concentration, skill thresholds, price impact.

## Opportunities (Stage 5)

Ranks every recipe and every herb/ore with experiments by profit per operation, ROI, profit per hour,
lowest investment or fastest to sell. Each row says what one *operation* is (one craft, or N herbs /
ore once you set the mill / prospect size). Gold per hour only appears once you enter how long an
operation takes. Flags show stale prices, unverified recipes, small-sample yields, missing prices and
assumed-free reagents; *days of daily volume* compares your planned output with TSM's sold-per-day.
Everything is a theoretical margin at listed prices - listed inventory is never assumed to sell.

## AH Investment (Stage 5)

Buy now, sell later. Enter the current price (prefilled if you pick an item), quantity, other costs
(lost deposits, re-listing), AH cut, your expected future price and sell-through. You get investment
cost, gross / net revenue, profit, ROI and break-even resale price for bearish / baseline / bullish
scenarios (spread is your choice; each scenario is editable). One click downloads the scenarios as an
Excel sheet with live formulas.

## Excel import / export (Stage 5)

*Import / Export -> Build export* writes one workbook: Info (time, region/realm, price basis, AH cut, TSM
data times, assumptions), price snapshot, manual prices, recipes, material breakdown, crafting
profitability, experiments, calibrated yields, the latest Opportunities scan, and investment scenarios.
Profit columns are formulas; missing prices stay blank (never 0).

Import reads the sheets **Recipes** (recipe CSV columns), **Prices** (`item_id, name, price_gold, note`
-> manual overrides) and **Experiments** (`experiment_ref, process, input_item_id, input_name,
units_used, performed_at, notes, output_item_id, output_name, quantity`; rows sharing an
`experiment_ref` are one batch, `process` is `milling` or `prospecting`). An exported workbook can be
edited and imported back; experiments already stored are skipped so nothing is double-counted.
Download the empty template from the same tab.

## Recipes from the Blizzard API (housing and more)

*Housing / Crafting -> Import from Blizzard API* reads recipes from Blizzard's official Game Data API.

1. Create a client at develop.battle.net -> API Access (free) and paste the **client ID** and **secret** into
   *Settings -> Blizzard API*, then **Save and test**. They are stored only in your local database
   (`data/`, git-ignored).
2. **Search every profession for housing categories** looks through each profession's newest skill tier for
   category names containing words like *decor* / *furnish* / *housing* (editable - the real category names
   come from the API; if nothing matches, use **Browse one profession** to see them).
3. Tick the categories, **Fetch**, review the preview, **Import**.

Imported recipes get the key `bnet-<recipe id>` (re-importing updates them), source
"Blizzard Game Data API (date)", the profession, skill tier and category, and **no last-verified date** - so
they are flagged as unverified until you check them. The API is known to leave out optional / quality
reagents on modern recipes; recipes it cannot describe (no crafted item, no reagents) are listed and skipped,
never guessed. Downloads are cached in `data/cache/blizzard`.

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
goldmaker/              logic only - no Streamlit imports
  config.py             paths, URLs, thresholds
  currency.py           copper <-> gold (Decimal-safe), formatting
  tsm_client.py         URL building, download, CSV normalisation, ingest
  database.py           SQLite: prices, overrides, settings, characters, experiments, recipes (+ upgrades)
  pricing.py            price resolution, overrides, staleness, display table
  lua_parser.py         safe Lua data parser (SavedVariables), never executes code
  charexporter.py       CharExporter SavedVariables / JSON / CSV parsing
  wow_files.py          finds CharExporter.lua on disk
  profiles.py           merged profession profiles for calculators
  conversion.py         input item -> outputs maths, yield calibration, reference yields
  blizzard_api.py       Blizzard Game Data API client + recipe conversion
  auto_import.py        automatic CharExporter import (runs on every app start / refresh)
  milling.py            herb-named layer over conversion.py
  recipes.py            recipe model, validation, CSV/JSON import and export
  crafting.py           crafting profit, mechanics, profiles, quality tiers, break-even material price
  scanner.py            opportunity scanner (recipes + processing)
  investment.py         AH investment scenarios
  excel_io.py           Excel export / template / import
ui_processing.py        shared Milling / Prospecting UI
ui_milling.py           Milling tab (+ Felwort quick start)
ui_prospecting.py       Prospecting tab
ui_crafting.py          Housing / Crafting tab (calculator reused by Jewelcrafting / Alchemy)
ui_profession.py        Jewelcrafting and Alchemy tabs
ui_scanner.py           Opportunities tab
ui_investment.py        AH Investment tab
ui_excel.py             Import / Export tab
ui_blizzard.py          Blizzard API credentials (Settings) and recipe import
ui_profiles.py          Character Profiles tab
ui_common.py            shared widgets (item picker, db connection)
TODO.md                 open issues, things to verify, ideas
CLAUDE.md               rules and conventions for Claude sessions
open_in_vscode.bat      set up + open this folder in VS Code
setup_env.bat           create .venv and install dependencies
.vscode/ .streamlit/    VS Code settings/tasks/debug configs, Streamlit config
samples/                synthetic example files (FAKE data)
tests/                  pytest suite
data/                   created at runtime: goldmaker.sqlite3 and raw CSV cache (git-ignored)
```

## Tests

```bat
.venv\Scripts\activate
pytest
```

Covers the shared conversion maths and prospecting storage, database upgrades from older versions,
profile-driven mechanics, break-even material prices, quality tiers, investment scenarios, the
opportunity scanner, Excel export/import round trips, recipes import/validation, crafting maths
(including your Gold Sunglass Vial example), milling maths (including your Felwort batch), yield statistics, experiment storage, the Lua parser, CharExporter parsing (all three formats), snapshot-ordering rules,
manual-stat preservation, profile completeness, currency conversion, URL building, CSV normalisation (including missing
columns), failed-download handling, price priority, overrides, missing-price
handling and staleness. Tests use synthetic CSV data, not live TSM files.

## Roadmap

* Stage 1: TSM prices, cache, overrides - **done**
* Stage 2: milling calculator + experiment tracking - **done**
* Stage 3: generic crafting recipes + housing calculator (CSV/JSON recipe import) - **done**
* Stage 4: prospecting, Jewelcrafting / Alchemy with character profiles and quality tiers - **done**
* Stage 5: opportunity scanner, AH investment scenarios, Excel import/export - **done**
* Next: verify everything against real data with you - see [`TODO.md`](TODO.md)
