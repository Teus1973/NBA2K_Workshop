**Last updated: 2026-04-28**

# NBA2K26 Rookie Rating Tool — Release Notes

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
- **SQLite schema** (`src/db.py`) for all tables specified in `PLAN.md` section 2.1:
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
