**Last updated: 2026-05-02**

# NBA2K26 Rookie Rating Tool — Roadmap

> Canonical project roadmap. Plain-text mirrors (`Documents/PLAN.txt`) exist for Google Drive / NotebookLM sync alongside this file.

## 0. Pre-flight assumptions

- **Draft year in scope**: 2026 NBA Draft (late June 2026). Combine is May 10, 2026 — tool must gracefully handle pre-combine state.
- **UI**: Streamlit, mirroring `k:\work\SubtitleForge` patterns (`src/config.py`, `src/logger.py`, `.env` via `python-dotenv`, per-user data under `%APPDATA%\NBA2KWorkshop`).
- **Primary ratings source**: `2kratings.com` scrape (1 req/sec, cached). Local CSV override supported as override pathway.
- **Storage**: SQLite DB (`data/workshop.db`) for reference ratings, combine, stats, audit log — pattern borrowed from Chronos (`c:\work\time-travel\Chronos\Python\chronos_logs.db`).
- **Prospects sheet**: **87-column** framework (`PROSPECTS_TABLE_COLUMNS` in `src/config.py`) plus optional **`potential`** after column **87** where automation applies.

## 1. Reusable scaffolding

### From SubtitleForge (`k:\work\SubtitleForge`)

- `src/config.py` — `PROJECT_ROOT`, `.env` loading, per-user data dir helper.
- `src/logger.py` — single FileHandler on a named child logger.
- `requirements.txt` — pins `streamlit`, `requests`, `python-dotenv`, `google-api-python-client`.
- `sync_docs.py` — Google Docs/Drive sync; reusable for Google Sheets export.
- `launcher.py` / `LaunchSubtitleForge.bat` — Streamlit bootstrap + port-check.

### From Chronos (`c:\work\time-travel`)

- `Chronos/Python/asset_api/_forge_runner.py` — async task queue pattern.
- `chronos_logs.db` — SQLite audit log pattern.
- `Chronos/Python/orchestrator.py` — multi-stage pipeline pattern.

## 2. Data schema

### 2.1 SQLite tables (`data/workshop.db`)

- `nba_players`, `nba_ratings_2k26`, `nba_stats_season`
- `combine_measurements`, `combine_drills`
- `prospects`, `prospect_stats`, `prospect_ratings_computed`
- `audit_log`, `formulas`

### 2.2 Output spreadsheet

- **Reference** — all rostered NBA players (ground-truth).
- **Prospects** — top 120 for 2026 draft (**87-column** header order).
- **Europeans** — (Euro roadmap) Euroleague.
- **Logs** — readable audit log (newest-first).
- **Formulas** — YAML + coefficients embedded.

## 3. Scraping strategy

### 3.1 Sources

- `twokratings.py` — `https://www.2kratings.com/{slug}`, rate-limit 1 req/sec, HTML cache.
- `nba_stats.py` — wraps `nba_api.stats.endpoints`.
- `nba_combine.py` — `DraftCombinePlayerAnthro`, `DraftCombineDrillResults`, `DraftCombineStats`.
- `espn_bigboard.py` — ESPN 2026 big board (print view).
- `sports_reference_cbb.py` — college stats.
- `international.py` — Euroleague / ACB / NBL / NZNBL.

### 3.2 Scouting reports

- ESPN blurbs + optional DuckDuckGo search.
- Keyword dictionary `data/scouting_keywords.yaml` modulates ratings ±2-5.
- Every modulation written to `audit_log`.

## 4. Reverse-engineering 2K26 formulas

Fit linear models per attribute on ~350-450 currently rostered NBA players.

### 4.2 Linear baseline

Per-attribute **linear regression** on the NBA reference corpus is the default modeling shape (coefficients live in YAML under `data/formulas/`).

### 4.3 YAML formula schema

Each file under `data/formulas/` follows the loader shape in `src/formulas/registry.py` (`attribute`, `version`, `type`, `features`, `intercept`, optional `clamp`, …).

### Physicals

- `strength_2k`: weight, height, bmi, wingspan
- `vertical_2k` / `speed_2k` / `agility_2k`: combine-override → else regression
- `stamina_2k`, `hustle_2k`, `speed_with_ball_2k`: composites

### Shooting

- `three_point_shot_2k`: 3P%, 3PA/36, FT% + **league penalty** (NCAA / Euroleague / HS)
- `free_throws_2k`, `mid_range_shot_2k`, `close_shot_2k`, `shot_iq_2k`, `offensive_consistency_2k`

### Inside scoring

- `driving_layup_2k`, `driving_dunk_2k`, `standing_dunk_2k`, `post_control_2k`, `draw_foul_2k`, `hands_2k`

### Playmaking

- `ball_handle_2k`, `pass_iq_2k`, `pass_accuracy_2k`, `pass_vision_2k`

### Defense

- `interior_defense_2k`, `perimeter_defense_2k`, `block_2k`, `steal_2k`
- `defensive_rebound_2k`, `offensive_rebound_2k`
- `help_defense_iq_2k`, `pass_perception_2k`, `defensive_consistency_2k`

### Height reconciliation

Piecewise `height_delta(wo_shoes_in, pos)` fit on past combine alumni.

### Overall Rating

Position-weighted composite (one weight vector per position) learned from data, target ±1.

## 5. Project structure

See **`Documents/README.md`** for the full tree.

## 6. Roadmap phases

### Phase 1: Controller Bridge & UI Integration — **COMPLETED** (v0.4.0)

- Virtual Xbox 360 bridge (`vgamepad`) + Prospects **Automation Settings** sidebar (controller init, **Edit player mode**, **Push to PS5**).
- **`INDEX_TO_NAV_MAP`** over **87** indices; literal navigation strings **Integnagbles** and **Durablity** preserved for automation/menu parity.
- Progress-tracked loop; **`finally`** stick neutralization; **+1** attribute adjustment vs sheet values when emulating **2K’s internal scale**.

### Phase 2: Batch Push & Quality Control

- Queue multiple prospects for sequential or batched pushes (respecting Remote Play latency budgets).
- Pre-flight validation (missing ratings, duplicate slugs), optional dry-run / logging export for QC sign-off.

### Phase 3: Chiaki-ng Memory Hooks

- Research / prototype integration with **Chiaki-ng** session semantics for resilient Remote Play orchestration (future milestone; scope TBD).

## 7. Legacy phased delivery checklist

1. Scaffold + config + SQLite + logger  
2. Reference data ingest (2kratings + nba_api)  
3. Calibration (v1 formulas)  
4. Prospects ingest (ESPN + college/international stats)  
5. Combine override path (post May 10, 2026)  
6. Excel + Google Sheets export  
7. UX (add/remove/upload/override)  
8. Scouting-report ingestion  
9. Europeans tab  
10. Auto-update schedule  

## 8. Out of scope for v1

- `.ROS` roster injection (2K proprietary format)
- Paid sources (Synergy, Second Spectrum)
- Non-Euroleague international leagues beyond Phase 2
