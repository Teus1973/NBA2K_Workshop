**Last updated: 2026-05-01**

# NBA2K26 Rookie Rating Tool (NBA2K Workshop)

A local, personal-use Streamlit tool that estimates **NBA 2K26** ratings for the **top 120 prospects of the 2026 NBA Draft**. It scrapes `2kratings.com` and `nba.com/stats` to build a calibration corpus of currently rostered NBA players, fits transparent per-attribute regression formulas (viewable and editable in the UI), and applies those formulas to each prospect using their college / international stats and (post-combine) official NBA Draft Combine measurements.

**Rating engines (sidebar):** use **Calibrated** for YAML / linear models trained on the NBA reference set, or **Excel 2026 class** for the same structural logic as the `2026 class` sheet in the project workbook (plus a tuned overall from YAML). Switching engines requires a **recompute** (Settings or Formulas) to refresh `prospect_ratings_computed`.

> **Runs 100% locally.** No cloud, no paid APIs, no user accounts. Free sources only (2kratings, nba.com, espn.com, sports-reference.com, proballers.com, duckduckgo).

---

## Quick start

**First time only** — create the venv and install deps:

```powershell
# Python 3.11+ recommended
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Optional: copy env template and tweak rate-limits / Google creds
Copy-Item .env.example .env
```

**Every other time** — double-click the launcher script, or run the packaged Windows binary when present:

```text
LaunchNBA2KWorkshop.bat        <-- normal use; self-heals venv, hides console
NBA2K Workshop.exe             <-- optional PyInstaller build (see scripts/build_workshop_launcher.ps1)
StartNBA2KWorkshop.cmd         <-- picks NBA2K Workshop.exe / other launcher names in order
TroubleshootNBA2KWorkshop.bat  <-- visible console for debugging startup errors
```

Bundled branding lives under `assets/` (`app_logo.png` sidebar + favicon source, `app_icon.ico` for the `.exe`). Rebuild the launcher after changing art.

Manual fallback (activated venv):

```powershell
streamlit run app.py --server.port 8506
```

### What the launcher does

Mirrors the SubtitleForge pattern (see `launcher.py`):

1. If the venv or `streamlit` is broken, `LaunchNBA2KWorkshop.bat` rebuilds it from `requirements.txt`.
2. `launcher.py` then:
   - Detects if port **8506** is already LISTENING and healthy → opens your browser and exits (no double-launch).
   - If the port is held by a dead process, kills **only** that PID via `netstat -aon` + `taskkill /F /PID`.
   - Otherwise spawns Streamlit fully **detached** (no lingering CMD window) with output piped to `streamlit_launcher.log`.
   - Polls `http://localhost:8506` for up to 60s, then opens your default browser.
3. On timeout, a native Windows MessageBox tells you to check `streamlit_launcher.log` or run the troubleshoot script.

Tweak the launcher with env vars (in `.env` or your shell):

| Variable | Default | Purpose |
|---|---|---|
| `NBA2K_WORKSHOP_PORT` | `8506` | Change the listening port |
| `NBA2K_WORKSHOP_LAUNCH_TIMEOUT` | `60` | Seconds to wait for the first HTTP 200 before showing a timeout dialog |

The app opens at `http://localhost:8506` with the following tabs:

- **Reference** — current NBA players with 2025-26 stats, combine measurements, and scraped 2K26 ratings (ground-truth dataset).
- **Prospects** — 2026 draft prospects with estimated 2K26 ratings (add / remove / override). Numeric stat columns are shown with **two decimal places** in the table.
- **Scouting** — ESPN lines plus optional local **Ollama** write-ups; **Save to database** (or **Auto-save new AI scouting**, on by default) persists text to SQLite so a browser reload keeps your work. Recompute ratings after saving if formulas use scouting hints.
- **Europe** — (Phase 2) Euroleague players with the same rating schema.
- **Formulas** — live YAML editor for every rating formula (Calibrated engine) with fit metrics and rollback; **Recalculate** runs a **fast bulk write** (one SQLite transaction, throttled progress).
- **Logs** — append-only change log (stat refreshes, rating recalcs, user edits).
- **Settings** — scrape refresh, **Recompute prospect ratings** (same batched path), CSV/XLSX upload, Excel / Google Sheets export, full pipeline.

---

## Project layout

```
NBA2K_Workshop\
  app.py                        Streamlit entrypoint (all tabs)
  assets\                       App logo (PNG) + Windows icon (ICO) for UI and PyInstaller
  launcher.py                   Hidden Streamlit bootstrap + health probe
  LaunchNBA2KWorkshop.bat       Double-click launcher (self-heals venv)
  TroubleshootNBA2KWorkshop.bat Visible-console fallback for startup errors
  streamlit_launcher.log        Rolling launch log (gitignored)
  requirements.txt
  README.md                    this file
  PLAN.md                      detailed roadmap (do not edit)
  RELEASE_NOTES.md
  .env.example                 copy to .env
  data\
    workshop.db                SQLite (gitignored)
    cache\{2kratings,nba,espn,cbb}\    HTTP cache (gitignored)
    formulas\*.yaml            editable per-attribute formulas
    user_uploads\              your CSV/XLSX imports
    exports\                   generated .xlsx files
  src\
    config.py, logger.py, db.py, audit.py, bulk_recalc.py, scouting_persist.py
    scrapers\{twokratings,nba_stats,nba_combine,espn_bigboard,espn_mens_cbb,
              sports_reference_cbb,international,scouting,wikidata,stat_normalize}.py
    calibration\{build_corpus,fit_formulas,evaluate}.py
    formulas\{registry,apply,excel_2026_class}.py
    exporters\{excel_writer,gsheets_writer,data_loader}.py
    ui\{reference_tab,prospects_tab,scouting_tab,europe_tab,formulas_tab,
        logs_tab,settings_tab}.py
  tests\
```

---

## Data sources (all free)

| Source | What we pull | Module |
|---|---|---|
| `2kratings.com` | 45 attributes per NBA player (height, weight, wingspan, Close Shot, 3PT, Speed, Strength, …) | `src/scrapers/twokratings.py` |
| `nba.com/stats` via `nba_api` | regular-season + playoff stats, Draft Combine Anthro / Drills / Stats | `src/scrapers/nba_stats.py`, `nba_combine.py` |
| `espn.com` big board | top-100 prospects + school + position + scouting prose | `src/scrapers/espn_bigboard.py` |
| `sports-reference.com/cbb` | NCAA college stats with shot-zone splits | `src/scrapers/sports_reference_cbb.py` |
| `espn.com` NCAA men's basketball | supplemental fills when SR rows are sparse | `src/scrapers/espn_mens_cbb.py` |
| `basketball-reference.com/international` | international / non-NCAA career totals (after Proballers) | `src/scrapers/international.py` |
| `wikidata.org` | dates of birth when SR pages omit **Born:** | `src/scrapers/wikidata.py` |
| `proballers.com` / Euroleague | international stats (NZNBL, NBL, Euroleague, ACB, LNB) | `src/scrapers/international.py` |

Rate-limit knobs live in `.env` (`NBA2K_WORKSHOP_SCRAPE_RPS`, `NBA2K_WORKSHOP_USER_AGENT`). All raw responses are cached under `data/cache/` and only refetched when TTL expires or a user explicitly triggers a refresh.

---

## Formula transparency

Every rating is computed from a YAML file under `data/formulas/`. Example (`strength_2k.yaml`):

```yaml
attribute: strength_2k
version: 1
type: linear_regression
features:
  - {name: weight_lbs, coef: 0.42}
  - {name: height_in,  coef: 0.18}
  - {name: bmi,        coef: -0.05}
intercept: 12.0
clamp: [25, 99]
notes: "Fit on 412 current NBA players. R² = 0.78. MAE = 4.1."
```

You can edit the coefficients in the **Formulas** tab and re-run the whole Prospects table with one click. Every recalc writes an audit-log row per changed cell.

---

## Audit log / "Logs" tab

Every auto-update writes a row to `audit_log` with `before → after`. Filterable by:

- **actor** — `system` (scheduled / on-demand refresh) or `user` (manual edit)
- **action** — `stat_refresh`, `rating_recalc`, `player_added`, `player_removed`, `formula_edit`, `override_set`
- **entity** — player slug
- **field** — attribute name

Download the current filter as CSV from the Logs tab.

---

## Acceptance criteria (from the original spec)

- [x] Output ratings for 120 eligible 2026 draft prospects
- [x] Free tools only (no paid APIs, no cloud services)
- [x] Logs page flags every auto-update
- [x] All formulas viewable and modifiable in the UI
- [x] Reuses SubtitleForge + Chronos patterns (Streamlit shell, `src/logger.py`, SQLite audit DB, per-user `%APPDATA%` config)
- [x] Runs locally on Windows (no network dependency beyond scraping)

---

## Related projects

- **SubtitleForge** (`k:\work\SubtitleForge`) — donor patterns: Streamlit shell, `src/config.py`, `src/logger.py`, `.env` loading, Google Docs sync (reused for Google Sheets export).
- **Chronos** (`c:\work\time-travel`) — donor patterns: SQLite audit DB (`chronos_logs.db`), orchestrator pipelines, async task runner.

See `PLAN.md` for the complete architecture and phase roadmap.
