**Last updated: 2026-05-14**

# NBA2K26 Rookie Rating Tool — Release Notes

## v0.9.4 — 2026-05-14

### Draft Combine ingestion, prospect linking, and merge pipeline

- **`src/scrapers/nba_combine.py`**: fetch **DraftCombinePlayerAnthro** and **DraftCombineDrillResults** for **NBA + G-League**, prefer NBA on duplicate keys; skip refetch when cache JSON is empty; map combine `PLAYER_ID` to workshop slugs via `prospects.nba_id`, normalized name resolution (generational suffix stripping), first-name aliases (**Nate** ↔ **Nathaniel**), and mirror rows as `prospect:{slug}` beside `nba:{id}` upserts.
- **`src/utils.py`**: central **`KNOWN_NBA_IDS`** slug → combine `PLAYER_ID` pins for names that still diverge between ESPN and `nba.com`.
- **`src/db.py` / `src/exporters/data_loader.py`**: `prospects.nba_id` join path plus slug-keyed combine mirrors; **`combine_height_in`** maps from height **without shoes**; coalesced prospect physicals / reference merges updated accordingly.
- **`src/formatting.py`**: optional **fractional-inch** height display strings for combine-style tapes.
- **`src/formulas/`** (`apply.py`, `excel_2026_class.py`): align combine-driven features and vertical handling with merged prospect rows.
- **Exporters / UI**: Excel, Google Sheets, and Prospects paths stay consistent with widened prospect column merge behavior where applicable.
- **Tests**: `tests/test_nba_combine_names.py`; updates across DB migration, Excel column, formatting, and formula tests.

---

## v0.9.3 — 2026-05-06

### PS5 push stability, OCR hygiene, and audit visibility

- **Controller bridge** (`src/automation/controller_mapping.py`): rating sweep **starts at index 34** when Edit player mode is off (no wasted iterations over bio/stats). After each rating, **D-pad Right** advances columns (LTR detailed grid). **ROI inset** before Tesseract (**~7 px** left/right, **~4 px** top/bottom) trims yellow column dividers and the in-cell controller glyph while Vision Lab still shows the user’s full ROI box. **Post–D-pad blur settle** (**0.3 s**, `asyncio.sleep` when no event loop is running, else `time.sleep`) runs immediately before OCR capture. **Anchor checks** accept alternate literal PS5 spellings for the intangibles/durability columns. OCR reads **retry** on empty or non-parsable output (**3×**, **0.1 s** backoff). Console **telemetry** per rating cell: `[OCR Read: …] [Target: …] [Pulse Count: …]`; legacy path prints `n/a` for OCR.
- **Audit** (`src/audit.py`, `src/ui/prospects_tab.py`): every **Push to PS5** completion or failure logs **`automation_push`** (slug + outcome `note`). Clearing the log no longer leaves a “silent” session—new pushes produce rows.
- **Logs UI** (`src/ui/logs_tab.py`): **Export & clear** surfaced on the main page (CSV respects filters; clear wipes all rows with confirmation + rerun).
- **Vision Lab** (`src/ui/vision_lab_tab.py`): **grid-start preset** / defaults aligned with the current built-in Chiaki ROI tuple for wide layouts.
- **Tests** (`tests/test_controller_mapping.py`): shave dimensions, alternate anchor spellings, vgamepad stubs updated for D-pad Right.

---

## v0.6.2 — 2026-05-03

### Vision Lab, OCR tooling, and export UX

- **Vision Lab** tab (`src/ui/vision_lab_tab.py`): split-pane workstation to capture the **Chiaki-ng** Remote Play window, nudge an **OCR ROI** relative to that window, and preview **Tesseract** reads alongside context/zoom thumbnails (session-backed ROI aligns with automation overrides).
- **Dependencies** (`requirements.txt`): **`mss`**, **`pygetwindow`**, **`Pillow`**, **`pytesseract`** — install **[Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki)** and ensure it is on **`PATH`** for OCR preview and capture helpers.
- **Automation** (`src/automation/controller_mapping.py`): Chiaki window resolution helpers, OCR preview/calibration utilities, and expanded controller/OCR integration tests (`tests/test_controller_mapping.py`, `tests/test_chiaki_window_resolve.py`).
- **Workbook export** (`src/ui/workbook_export.py`): shared **Excel / Google Sheets** build section with per-tab **`slot`** keys and cached `.xlsx` bytes; wired from **Settings** and **Prospects** so downloads survive reruns. Clears cached export after DB mutations.
- **Excel / Google Sheets writers**: workbook layout updates including a ratings-focused **Prospects** sheet for download (freeze row 1 / columns A–B); Google Sheets parity where applicable (`tests/test_excel_prospects_download_columns.py`).
- **Config / schema polish**: workbook column parity and **`team_total_games`**-related scrape fixes remain documented under v0.5.0 notes.

---

## v0.6.1 — 2026-05-03

### Stick persistence and navigation settle

- **Left stick hold**: rating pulses keep the deflected stick for **0.1 s** after `update()` before neutralizing, so Chiaki-ng / 2K registers the value reliably.
- **D-pad menu step**: after each rating applied in the **87-column** loop, a **D-pad Down** press/release (**0.05 s** hold, **0.15 s** post-release settle) advances the in-game editor row.
- **Overall anchor (index 34)**: in-loop check that column **34** is still **`overall_2k`**, plus an **extra 0.3 s** settle after the existing **0.5 s** pause so the cursor animation can finish before the first stick push.

---

## v0.5.1 — 2026-05-03

### Hotfix: vgamepad API alignment and Chiaki-ng stability throttling

- **vgamepad**: left stick moves use **positional** `left_joystick_float(x, y)` calls (not `x_value` / `y_value` keywords) for API compatibility.
- **Chiaki-ng / Remote Play**: **0.15 s** spacing after each column step; **buffer flush** every **10** columns (`gamepad.update()` + **0.4 s** pause) to reduce input buffer overflows.
- **Overall anchor (index 34)**: **0.5 s** settle time after navigation and before the first stick value push so the PS5 menu can settle on **Overall**.
- **Schema v6 menu literals**: runtime checks keep **Integnagbles** tied to **`intangibles_2k` at index 68** and **Durablity** to **`durability_2k` at index 70** (0-based sheet column; durability is not index 71 in this schema).

---

## v0.5.0 — 2026-05-02

### Highlights

- **Availability-adjusted durability** (Excel 2026 class engine): `durability_2k` now subtracts an **availability penalty** from games-played vs **team total games** (`gp_ratio = gp / team_total_games`; below 90% attendance applies `(0.90 - gp_ratio) * 40`). Base remains `85 - 1.5 * max(0, age - 19)`, clamped **[25, 99]** after rounding.
- **Schema v6**: `team_total_games` (**INTEGER**) on `prospect_stats` and `nba_stats_season`. The **stats band** (indices 14–33) gains `team_total_games` immediately after `gp`; **`pf` is no longer in that band** so **`overall_2k` stays at index 34** (controller automation literals **Durablity** / **Integnagbles** unchanged).

### Data / scrapers

- **Sports-Reference CBB**: resolves **team total games** from the school season roster page (**max games** column); falls back to the player’s **gp** when the school page is missing or ambiguous.
- **ESPN men’s CBB**: persists `team_total_games` (defaults to **gp** until a richer team schedule signal exists); **resolver** fixed so multi-hit name search actually walks candidates and matches **school**.
- **NBA `nba_api` league totals**: `team_total_games` = **max GP on that TEAM_ID** for the season (same fallback intent as roster max).
- **International / Proballers**: `team_total_games` defaults to **gp** when no team schedule is available.

---

## v0.4.0 — 2026-05-02

### Highlights

- **UI-to-Console Bridge**: Streamlit-triggered automation that maps the **87-column** prospect framework to a virtual Xbox 360 controller for **PS5 Remote Play** (see `src/automation/controller_mapping.py` and Prospects tab).

### Features

- **Automation Settings** expander in the **sidebar**: virtual controller initialization, **Edit player mode** toggle, and wiring to session state for a single `vgamepad.VX360Gamepad()` instance.
- **Push to PS5** action on the Prospects tab for the selected player: progress UI (`st.progress`) over the **87-column** loop with live status text (e.g. pushing rating rows including navigation labels **Integnagbles** and **Durablity** as bridged from `INDEX_TO_NAV_MAP`).

### Logic / engineering

- **`finally`-safe stick neutralization**: virtual left stick is returned to neutral **`(0, 0)`** after each push path (driver errors included).
- **+1 rating adjustment**: sheet values are bumped by **+1** before clamping to **[25, 99]** for attributes so automation aligns with **2K’s internal attribute scale** during controller emulation.

---

## v0.3.0 — 2026-05-01

### Highlights

- **NBA2K Workshop** branding: sidebar logo and browser favicon from `assets/app_logo.png` (transparent PNG); Windows launcher icon from `assets/app_icon.ico`. Optional **`NBA2K Workshop.exe`** output from `scripts/build_workshop_launcher.py` / `.ps1`; `StartNBA2KWorkshop.cmd` prefers that name when present.
- **Prospect stats**: ESPN NCAA men’s basketball scraper (`src/scrapers/espn_mens_cbb.py`) fills gaps when Sports-Reference CBB rows are sparse or `team.$ref` is missing; SR pipeline merges supplemental ESPN fields and may label source `sports-reference+espn-mcb`.
- **International**: Basketball-Reference international player pages (`src/scrapers/international.py`) after Proballers for non-NCAA career totals; slug variants for lookup resilience.
- **Dates of birth**: bulk enrichment uses **Wikidata** (`src/scrapers/wikidata.py`) when SR omits **Born:**; defensive handling so one bad lookup does not abort the batch.
- **Scouting / Wikipedia**: throttling and retries on HTTP **429** with `Retry-After` awareness (`src/scrapers/scouting.py`).
- **Settings** tab copy clarifies NCAA CBB vs international pipelines and DOB sources.

### Added / changed

- **Tests**: `test_wikidata.py`, `test_bbintl_parse.py`, `test_espn_mens_cbb.py`, `test_stat_normalize.py`; updates to scouting and SR DOB tests.
- **`LaunchNBA2KWorkshop.spec`**: portable paths relative to the spec file; PyInstaller embeds `assets/app_icon.ico` when present.
- **`.gitignore`**: generated `NBA2KWorkshop.spec`, spaced launcher exe name, existing launcher binaries.

---

## v0.2.0 — 2026-04-28

### Highlights

- **Excel 2026 class** rating engine (`src/formulas/excel_2026_class.py`) selectable in the app sidebar; pairs with trained **overall_2k** YAML. Attributes retuned for steal, shot IQ, projected speed / agility, offensive and defensive consistency while preserving combine and `raw_*` overrides.
- **Bulk recompute** (`src/bulk_recalc.py`) uses a **single SQLite transaction** for all prospect rating writes and **throttles** progress callbacks so Streamlit stays responsive on Windows; Settings / Formulas / pipeline use the same path.
- **Scouting tab** loads saved DB text even when the session cache had empty placeholders; **Auto-save new AI scouting to the database** (default on) persists Ollama output with the same merge rules as manual Save (`src/scouting_persist.py`).
- **Data tables** round float columns to two decimal places in shared loaders (`src/exporters/data_loader.py`).

### Added / changed

- Tests: `test_excel_2026_class.py`, `test_bulk_recalc.py`, `test_scouting_save_merge.py`, and related coverage.
- Optional launcher / build helper scripts under `scripts/` and alternative entry files (`NBA2KWorkshop.pyw`, `StartNBA2KWorkshop.cmd`, etc.) as present in the repo.

---

## v0.1.0 — 2026-04-24 (MVP)

### Highlights

- Streamlit app boots end-to-end with six tabs (Reference, Prospects, Europeans, Formulas, Logs, Settings).
- 569 NBA player profiles + 569 season-stat rows + 81 combine alumni + 120 2026 draft prospects successfully ingested during smoke testing.
- 36 YAML formulas generated (33 2K attributes + 3 combine scalings) and wired into a one-click prospect recalc that writes 120 `prospect_ratings_computed` rows with color-coded provenance.
- Excel export produces a 5-sheet workbook (Reference / Prospects / Europeans / Logs / Formulas) with per-cell source colors; smoke-tested at 570×73 / 121×53 / 40×10 / 37×8.
- 23/23 pytest tests passing (scraper parsing fixtures, formula known-input/known-output, DB migrations, height reconciliation).

### Added

- **Project scaffold** mirroring SubtitleForge patterns (`src/` layout, `.env` config, per-user data dir, Streamlit launcher).
- **SQLite schema** (`src/db.py`) for all tables specified in `Documents/PLAN.md` section 2.1:
  - `nba_players`, `nba_ratings_2k26`, `nba_stats_season`
  - `combine_measurements`, `combine_drills`
  - `prospects`, `prospect_stats`, `prospect_ratings_computed`
  - `audit_log`, `formulas`
- **Audit log** helpers (`src/audit.py`) — append-only change journal used by every scraper, recalc, and user edit.
- **Scrapers**:
  - `src/scrapers/twokratings.py` — per-player attribute scrape with polite rate limiter and HTML cache.
  - `src/scrapers/nba_stats.py` + `nba_combine.py` — `nba_api` wrappers with retry/backoff.
  - `src/scrapers/espn_bigboard.py` — 2026 big-board parser (ESPN print view).
  - `src/scrapers/sports_reference_cbb.py` — NCAA college stats.
  - `src/scrapers/international.py` — Euroleague / ACB / NBL / NZNBL stats.
  - `src/scrapers/scouting.py` — ESPN blurbs + optional DuckDuckGo search; keyword modulation from `data/scouting_keywords.yaml`.
- **Calibration pipeline** (`src/calibration/`) — builds corpus, fits linear-regression formulas per attribute, writes YAML to `data/formulas/`.
- **Formula engine** (`src/formulas/`) — loads YAML, applies to a prospect row with combine-override, league-3pt penalty, and height-delta reconciliation.
- **Streamlit UI** (`app.py` + `src/ui/`) — six tabs (Reference, Prospects, Europe, Formulas, Logs, Settings).
- **Excel exporter** (`src/exporters/excel_writer.py`) — 5-sheet workbook with color-coded cells.
- **Google Sheets exporter** (`src/exporters/gsheets_writer.py`) — reuses SubtitleForge credentials pattern.
- **CLI entrypoint** — `python -m nba2k_workshop refresh` for scheduled stat refreshes.
- **Tests** — parsers, formulas, height reconciliation, DB migrations.

### Known limitations

- Combine data for the 2026 class is not yet published (event: May 10, 2026). The tool renders a pre-combine state and exposes a one-click post-combine refresh.
- `two_point_shot_2k` / Post Hook / Post Fade are modelled inside the composite `post_control_2k` formula for v1.
- European tab is schema-only (feature-flag gated).
