# TODO - WoW Goldmaker

Running list of open issues, things to verify and their solutions. Newest first within each section.
Move items to **Done** with a one-line note on how they were solved (and the commit, if useful).

## Verify with real data (needs you, on your PC)

- [ ] **Reset the Blizzard client secret** on develop.battle.net (it was pasted into a chat), then paste the new
      one in *Settings -> Blizzard API* and press **Save and test**.
- [ ] **Battle.net sign-in.** Add `http://localhost:8501/` under Redirect URLs of your client on
      develop.battle.net, then *Character Profiles -> Battle.net -> Sign in*. Check all alts arrive.
- [ ] **Blizzard AH prices, first live run.** Refresh with the two Blizzard boxes ticked; compare a few prices with
      the game / TSM. The market estimate (cheapest 15% of listed units) is this app's choice - say if you want another.
- [ ] **Housing decor: crafted item by name.** First live run found Legion housing categories (Suramar Fence, Tauren
      Soup Pot, Dalaran Auto-Hammer...) but Blizzard's recipe data names no crafted item for them. Now matched by
      exact unique name (prices, then Blizzard item search). Check a few matched item IDs; if recipes are still
      skipped, paste one *Show what Blizzard sent* response.
- [ ] **Housing finder, first live run.** Find & import with *Legion* in the expansion box; tell me the category names
      found and whether known recipes show up under "Who can craft".
- [ ] **Why only one CharExporter character?** *Character Profiles -> Import from CharExporter -> Find* now lists every
      file found and the preview lists any skipped entries by name. Send me what it shows if alts are still missing.
- [ ] **Blizzard API importer, first live run.** Built against the documented endpoints but never run against
      the live API (the cloud session's network blocks battle.net). Try *Housing / Crafting -> Import from
      Blizzard API -> Search*. Tell me the housing category names it finds (or that it finds none), and compare
      2-3 imported housing recipes with the game. Unknown: whether housing decor recipes are in the API at all,
      and under which category names.

- [ ] **First real run on Windows.** `git pull`, then `.venv\Scripts\python.exe -m pip install -r requirements.txt`
      and start the app (Ctrl+Shift+B). Send screenshots / tracebacks of anything that breaks.
- [ ] **Database upgrade.** The first start of this version adds columns to your existing
      `data/goldmaker.sqlite3` (experiment process, profile mechanic sizes) and a quality-tier table.
      Check your Felwort experiment still shows on *Milling -> Experiments*. (Tested on a synthetic old
      database only.)
- [ ] **TSM CSV column headers.** Run *Refresh Prices* and look at *Price Database -> Dataset details*.
      Adjust `_ALIASES` in `goldmaker/tsm_client.py` if any expected column is listed as missing.
      Also unverified: `kr` / `tw` region slugs, the realm-slug helper.
- [ ] **Does your CharExporter.lua hold several alts?** Open it in VS Code and search for `"basic"` - one hit
      per stored character. Tell me how many you see after logging out of 2-3 alts.
- [ ] **CharExporter file layout.** If import finds nothing, send the first ~40 lines of
      `...\WTF\Account\<ACCOUNT>\SavedVariables\CharExporter.lua` (remove character names if you like).
- [ ] **AH commission 5%.** Default in `config.py`, editable in Settings. Confirm in game.
- [ ] **Operation sizes.** Herbs per mill and ore per prospect are *not* built in. Enter them on
      *Opportunities -> Operation sizes and times* once you have checked them in game.
- [ ] **Profile stat meaning.** The app assumes the Resourcefulness % / Multicraft % you type in are proc
      chances as shown in the profession window. Confirm.
- [ ] **VS Code configs** (`.vscode/`, `.bat` launchers) were never run in VS Code itself. File upload and
      the new Excel downloads may not work inside VS Code's Simple Browser - use a normal browser.

## Data I need from you (no defaults are invented)

- [ ] Your real professions per alt and their stats (Character Profiles -> Profession stats), including the
      measured Resourcefulness / Multicraft **sizes** if you want those effects modelled.
- [ ] Prospecting batches (ore, quantity, every output with its item ID) - record on *Prospecting -> Experiments*.
- [ ] More milling batches - several smaller batches give a real uncertainty range.
- [ ] Alchemy / Jewelcrafting / housing recipes (CSV, JSON or Excel import), with source, patch and
      last-verified date. Patch 12.1.5 housing quantities are still not built in.
- [ ] Quality-tier item IDs for the recipes where silver vs gold matters, and your observed odds.

## Ideas / not built yet

- [ ] **Auctionator prices** from its SavedVariables file (read-only, like CharExporter). Need the first ~40 lines of
      `...\WTF\Account\<ACCOUNT>\SavedVariables\Auctionator.lua` to see the layout before parsing it.
- [ ] Item names for Blizzard-only prices (Blizzard item API, cached) when no TSM data is loaded.
- [ ] Guide sites (wowgoldfarms.com etc.): no feed; numbers go in as reference yields by hand.

- [ ] Optional: Blizzard Profile API for alts' professions (no Multicraft / Resourcefulness there).

- [ ] Save investment scenarios in the database (today: Excel download only).
- [ ] Ingenuity and concentration modelling - only once you have measured how they behave.
- [ ] Price impact of selling many units (today: linear revenue + a liquidity warning).
- [ ] Automated recipe data (e.g. Blizzard game-data API - needs your API credentials; ask first).
- [ ] Pet datasets (keyed by species, not item) are ignored.
- [ ] Dashboard charts across calculators (today each calculator has its own charts).

## Done

- [x] 2026-10-14 App reopens as it was left: manual yields saved per herb/ore (only once edited), and calculator /
      filter inputs remembered in the database (`ui_common.saved` + `persist_ui_state`). Prices stay fresh.

- [x] 2026-10-14 Blizzard AH prices (commodities + realm), Battle.net sign-in / by-name character import with
      profession tiers and known recipes, Housing finder (all expansions, Legion default, who can craft),
      CharExporter folder import reads every file and reports skipped entries.

- [x] 2026-10-14 "CharExporter keeps only one character": the app now re-checks the file every 15 s while open and
      keeps every character it has imported, so alts can be switched one after another. Per the addon's source
      (zwinout/CharExporter, Jul 2026) the file should hold every alt under `global["Name-Realm"]`; if it does
      not, likely causes are alts on different WoW accounts (separate folders - all are read), logging out in
      combat (the addon skips saving), or a crash / Alt+F4 (WoW does not write SavedVariables).

- [x] 2026-10-14 Blizzard Game Data API recipe import (housing category search + browse), credentials stored
      locally, recipes imported as unverified with source and date; cache in `data/cache/blizzard` (git-ignored).

- [x] 2026-10-13 Automatic character import: CharExporter.lua is checked on every app run and imported when
      WoW rewrote it (logout / reload). Same safety rules as manual import.
- [x] 2026-10-13 Reference yields (e.g. Wowhead) for milling / prospecting, stored apart from experiments and
      always labelled. Wowhead is not scraped (no public API; scraping is fragile and likely against its terms).

- [x] 2026-10-13 Stage 5: opportunity scanner, AH investment scenarios, Excel export/import/template.
- [x] 2026-10-13 Stage 4: prospecting (shared `conversion.py` with milling - Felwort regression unchanged),
      Jewelcrafting / Alchemy tabs with profiles, quality tiers, break-even material prices.
- [x] 2026-10-13 Stage 3 applied from `goldmaker-update.bundle` (fast-forward, 219 tests passing).
- [x] README "current status" was stale (said Stage 1 of 5) - rewritten.

## Answers to questions

- **Does CharExporter export automatically?** Yes, with *Auto-save on Logout* ticked it writes every alt to
  its SavedVariables file on each logout or `/reload`. WoW lets addons write only that file and only at
  logout/reload, so no addon can export "live". The app now imports that file automatically whenever it
  changed (checked each time the app runs or refreshes). No addon found exports Multicraft /
  Resourcefulness / Ingenuity - type those in.
