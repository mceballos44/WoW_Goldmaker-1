# TODO - WoW Goldmaker

Running list of open issues, things to verify and their solutions. Newest first within each section.
Move items to **Done** with a one-line note on how they were solved (and the commit, if useful).

## Verify with real data (needs you, on your PC)

- [ ] **Reset the Blizzard client secret** on develop.battle.net (it was pasted into a chat), then paste the new
      one in *Settings -> Blizzard API* and press **Save and test**.
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

- [ ] Optional: Blizzard Profile API for alts' professions (no Multicraft / Resourcefulness there).

- [ ] Remember quality-tier odds per profile + recipe (today they are typed per calculation).
- [ ] Save investment scenarios in the database (today: Excel download only).
- [ ] Ingenuity and concentration modelling - only once you have measured how they behave.
- [ ] Price impact of selling many units (today: linear revenue + a liquidity warning).
- [ ] Automated recipe data (e.g. Blizzard game-data API - needs your API credentials; ask first).
- [ ] Pet datasets (keyed by species, not item) are ignored.
- [ ] Dashboard charts across calculators (today each calculator has its own charts).

## Done

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
