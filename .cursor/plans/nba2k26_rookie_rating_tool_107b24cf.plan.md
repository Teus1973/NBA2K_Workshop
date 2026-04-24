---
name: NBA2K26 Rookie Rating Tool
overview: A local Streamlit tool that scrapes 2kratings.com + NBA.com stats/combine data to reverse-engineer NBA 2K26 attribute formulas on current NBA players, then applies those formulas to the top 120 prospects of the 2026 draft class to produce an exportable (Excel / Google Sheets) roster with editable formulas, per-player overrides, and a change audit log.
todos:
  - id: scaffold
    content: "Bootstrap repo: requirements.txt (streamlit, requests, beautifulsoup4, pandas, nba_api, openpyxl, scikit-learn, pyyaml, python-dotenv, duckduckgo-search, google-api-python-client, google-auth-oauthlib), src/ layout, .env, .gitignore, README.md with dated header, launcher.bat."
    status: completed
  - id: core
    content: Implement src/config.py, src/logger.py, src/db.py (SQLite schema + migrations for all tables in section 2.1), src/audit.py append-only helpers.
    status: completed
  - id: scraper-2kratings
    content: Build src/scrapers/twokratings.py with polite rate-limiting, HTML cache, and unit tests against fixtures for 3 representative players (guard / wing / big).
    status: completed
  - id: scraper-nba
    content: Build src/scrapers/nba_stats.py and src/scrapers/nba_combine.py wrapping nba_api with retry/backoff; verify DraftCombineStats still exposes Speed/Agility/Vertical 2K columns (document findings).
    status: completed
  - id: scraper-espn
    content: Build src/scrapers/espn_bigboard.py + one supplementary big-board scraper to reach 120 prospects; write src/scrapers/sports_reference_cbb.py and src/scrapers/international.py.
    status: completed
  - id: reference-tab
    content: "Phase 1 Streamlit: Reference tab showing ~500 NBA players joined across ratings + stats + combine (read-only, sortable, CSV export)."
    status: completed
  - id: calibration
    content: "src/calibration/build_corpus.py + fit_formulas.py: fit v1 linear models for every attribute in section 4.2, write YAMLs under data/formulas/, compute R^2/MAE per attribute for the Formulas tab."
    status: completed
  - id: formula-apply
    content: "src/formulas/registry.py + apply.py: load YAML, apply to a prospect row including combine-override path, league-3pt penalty, and height_delta reconciliation."
    status: completed
  - id: prospects-tab
    content: "Phase 2 Streamlit: Prospects tab with all user-specified columns, per-cell color coding (scraped / combine / computed / manual-override), add-player dialog, remove-player button."
    status: completed
  - id: exporters
    content: "src/exporters/excel_writer.py (openpyxl, 4 sheets: Reference/Prospects/Logs/Formulas) and gsheets_writer.py reusing SubtitleForge Google creds."
    status: completed
  - id: formulas-tab
    content: "Formulas tab: YAML editor per attribute, live recalc button, fit metrics display, version history with rollback."
    status: completed
  - id: logs-tab
    content: "Logs tab: filterable view of audit_log, newest-first, download as CSV, clear filter."
    status: completed
  - id: scouting
    content: "src/scrapers/scouting.py: pull ESPN blurbs + optional DuckDuckGo search; apply keyword modulations from data/scouting_keywords.yaml; every modulation logged."
    status: completed
  - id: upload-csv
    content: "Settings tab: upload CSV/XLSX of additional players or overrides; validate schema; merge into prospects table with full audit trail."
    status: completed
  - id: auto-refresh
    content: CLI entrypoint (python -m nba2k_workshop refresh) + Windows Task Scheduler XML template for weekly stats refresh and post-combine one-shot.
    status: completed
  - id: europe-tab
    content: "Phase 2 Europeans tab: Euroleague scraper + apply same formulas with FIBA-line adjustment; deferred behind a feature flag."
    status: completed
  - id: tests
    content: "tests/: parsers (fixtures checked in), formulas (known-input known-output), height reconciliation, DB migrations. Target pytest green on every phase."
    status: completed
isProject: false
---

# NBA2K26 Rookie Rating Tool — Roadmap

## 0. Pre-flight assumptions (confirm or correct)

- **Draft year in scope**: 2026 NBA Draft (late June 2026). Combine is May 10, 2026 — tool must gracefully handle pre-combine state.
- **UI**: Streamlit, mirroring `k:\work\SubtitleForge` patterns (`src/config.py`, `src/logger.py`, `.env` via `python-dotenv`, per-user data under `%APPDATA%\NBA2KWorkshop`).
- **Primary ratings source**: `2kratings.com` scrape (1 req/sec, cached). Local CSV override supported as override pathway.
- **Storage**: SQLite DB (`data/workshop.db`) for reference ratings, combine, stats, audit log — pattern borrowed from Chronos (`c:\work\time-travel\Chronos\Python\chronos_logs.db`).
- **Phase 1 deliverable**: Reference page + Prospects page (120 players). European page is Phase 2 / deferred.

## 1. Reusable scaffolding (what we lift from existing projects)

- **SubtitleForge** (`k:\work\SubtitleForge`)
  - [src/config.py](k:/work/SubtitleForge/src/config.py) — `PROJECT_ROOT`, `.env` loading, per-user data dir helper, `TempManager` pattern.
  - [src/logger.py](k:/work/SubtitleForge/src/logger.py) — single FileHandler on a named child logger (`nba2k_workshop.*`).
  - [requirements.txt](k:/work/SubtitleForge/requirements.txt) — already pins `streamlit`, `requests`, `python-dotenv`, `google-api-python-client`, `google-auth-oauthlib`.
  - [sync_docs.py](k:/work/SubtitleForge/sync_docs.py) — Google Docs/Drive sync; reusable for Google Sheets export.
  - [launcher.py](k:/work/SubtitleForge/launcher.py) / `LaunchSubtitleForge.bat` — Streamlit bootstrap + port-check pattern.
- **Chronos** (`c:\work\time-travel`)
  - `Chronos/Python/asset_api/_forge_runner.py` — async background task queue pattern for long scrapes.
  - `chronos_logs.db` — SQLite audit log pattern (structured change events).
  - `Chronos/Python/orchestrator.py` — multi-stage pipeline pattern for the calibration job.

## 2. Data schema

### 2.1 SQLite tables (`data/workshop.db`)

- `nba_players` — `player_id` (NBA ID), `first`, `last`, `team`, `pos`, `height_in`, `weight_lbs`, `wingspan_in`, `age`, `updated_at`.
- `nba_ratings_2k26` — `player_id`, 1 column per attribute (≈45 columns matching the user's list + Potential + Overall Durability), `scraped_at`, `source_url`.
- `nba_stats_season` — `player_id`, `season` (e.g. `2025-26`), `gp`, `min`, `pts`, `fgm`, `fga`, `fg_pct`, `fg3m`, `fg3a`, `fg3_pct`, `ftm`, `fta`, `ft_pct`, `oreb`, `dreb`, `reb`, `ast`, `tov`, `stl`, `blk`, `pf`, `source` (nba_api / college), `updated_at`.
- `combine_measurements` — `player_id_or_slug`, `year`, `height_wo_shoes_in`, `height_w_shoes_in`, `wingspan_in`, `weight_lbs`, `std_reach_in`, `body_fat_pct`, `hand_length_in`, `hand_width_in`.
- `combine_drills` — `player_id_or_slug`, `year`, `lane_agility_sec`, `shuttle_sec`, `three_quarter_sprint_sec`, `standing_vert_in`, `max_vert_in`, `bench_reps`, `c_speed_2k`, `c_speed_wb_2k`, `c_vertical_2k`, `c_agility_2k`.
- `prospects` — `slug`, `first`, `last`, `pos`, `school_or_team`, `age`, `espn_rank`, `other_rank`, `status` (`active` / `withdrew` / `undecided`), `added_by` (`system` / `user`), `updated_at`.
- `prospect_ratings_computed` — `slug`, 1 column per attribute, `formula_version`, `computed_at`, `manual_override_json` (per-attribute user overrides).
- `audit_log` — `id`, `ts`, `actor` (`system` / `user`), `action` (`stat_refresh` / `rating_recalc` / `player_added` / `player_removed` / `formula_edit` / `override_set`), `entity_slug`, `field`, `before`, `after`, `note`. (Drives the Logs tab.)
- `formulas` — `attribute`, `version`, `yaml_blob`, `edited_at`, `edited_by`. (YAML is also mirrored to `data/formulas/*.yaml` on disk for easy git diffs.)

### 2.2 Output spreadsheet (3 sheets)

- **Reference**: all rostered NBA players with their 2025-26 stats, physicals, combine anthro/drills (where they attended), and their 2K26 scraped attributes — this is the ground-truth page.
- **Prospects**: top 120 for the 2026 draft with the exact column set the user specified. Cells are colored: green = scraped, blue = combine override, yellow = formula-derived, grey = manual override.
- **Europeans**: deferred to Phase 2; schema pre-created, empty.
- **Logs**: readable view of `audit_log` (newest-first).
- **Formulas**: current YAML + coefficients so the exported file is self-documenting.

## 3. Scraping strategy — 120 prospects

### 3.1 Sources (each has a thin module under `src/scrapers/`)

- `twokratings.py` — `https://www.2kratings.com/{slug}`. Parse the `#Outside Scoring / #Athleticism / #Inside Scoring / #Playmaking / #Defense / #Rebounding` sections into a dict of ~45 attributes. Also the `About` block for height/weight/wingspan. Rate-limit 1 req/sec, `requests` + `beautifulsoup4`, cache raw HTML under `data/cache/2kratings/{slug}.html`.
- `nba_stats.py` — wraps `nba_api.stats.endpoints` (`LeagueDashPlayerStats`, `LeagueDashPlayerBioStats`, `CommonPlayerInfo`, `DraftHistory`) with retry + backoff; all raw JSON cached under `data/cache/nba/`.
- `nba_combine.py` — wraps `DraftCombinePlayerAnthro`, `DraftCombineDrillResults`, `DraftCombineStats` (which exposes the pre-computed `SPEED_AGILITY_RANK`, `SPRINT` etc. — investigate whether the legacy "2K"-scaled columns are still present; if not, compute from raw drill times using a calibration table).
- `espn_bigboard.py` — scrape the 2026 big board HTML (ESPN print view: `espn.com/espn/print?id=46886245` is the cleanest). Top 100 + supplementary scrape from one of: The Ringer, Tankathon, NBADraft.net to reach 120.
- `sports_reference_cbb.py` — college per-game and advanced stats (`sports-reference.com/cbb/players/{slug}.html`).
- `proballers_or_euroleague.py` — international stats for prospects playing in non-NCAA leagues (Karim Lopez, Hannes Steinbach, Sergio de Larrea, Dash Daniels, Adam Atamna, Michael Ruzic, Mouhamed Faye, Luigi Suigo, Dame Sarr, etc.). Covers Euroleague, ACB, LNB, NBL, NZNBL.

### 3.2 Prospect resolution pipeline

```
big board (ESPN + supplement)
   --> canonicalize name + school
   --> try match in: nba_combine (if invited) / sports-reference-cbb / proballers
   --> write prospects row + stats + combine rows
   --> mark missing-data fields for re-fetch
```

### 3.3 Scouting-reports add-on (Requirement 4)

- Pull ESPN big board prose blurbs per prospect (already in the page we fetch for ranks).
- Optional: DuckDuckGo HTML search (`duckduckgo-search` pip package) for `"{player} scouting report"` limited to espn.com / nbadraft.net / theringer.com / sports-reference.com.
- Keyword dictionary (`data/scouting_keywords.yaml`) modulates ratings by ±2–5 (e.g. `"elite athlete"` → +3 Vertical/Speed; `"streaky shooter"` → -2 Offensive Consistency). All modulations logged to `audit_log`.

## 4. Reverse-engineering the 2K26 formulas

### 4.1 Calibration corpus

~350–450 currently rostered NBA players (from `nba_ratings_2k26` ∩ `nba_stats_season` ∩ `combine_measurements` where available). Split 80/20 train/test. Hold out rookies so we evaluate on the exact population the tool will score.

### 4.2 Per-attribute model recipe

All models clamp to `[25, 99]`, round to int, and live under `src/formulas/` as a YAML + a thin Python callable. Each formula is **editable in the Formulas tab** and recomputation is one click; before/after deltas stream into the audit log.

**Physicals**
- `strength_2k`: `LinearRegression(features=[weight_lbs, height_in, bmi, wingspan_in])`. Expected: weight dominates; a tall-thin player (high height, low weight) gets mid; a short-heavy gets high; tall-heavy gets very high. Matches user's example.
- `vertical_2k`: If combine attended → `c_vertical_2k` override (NBA pre-scaled). Else `LinearRegression(max_vert_in, weight_lbs, height_in)` fitted on combine alumni.
- `speed_2k`: Combine override if attended; else `LinearRegression(three_quarter_sprint_sec(inv), weight_lbs, height_in)`.
- `agility_2k`: Combine override; else `LinearRegression(lane_agility_sec(inv), shuttle_sec(inv))`.
- `speed_with_ball_2k`: derived as `speed_2k - k * (position in {C, PF}) + f(ball_handle_2k)`; calibrated.
- `stamina_2k`: `LinearRegression(min_per_game, gp)`.
- `hustle_2k`: weak signal; `LinearRegression(stl_per36, oreb_per36, blk_per36) + scouting modulation`.

**Shooting**
- `three_point_shot_2k`: `LinearRegression(fg3_pct, fg3a_per36, ft_pct, efg_catch_and_shoot?)` with a **league penalty** feature — `-α` for NCAA (line 22'1.75"), `-β` for FIBA/Euroleague (22'1.75"), `-γ` for high-school/G-League Ignite-successor. α/β/γ fit from past rookies' rookie-year 2K rating vs pre-draft college 3pt.
- `free_throws_2k`: near-linear in `ft_pct`, light weight on `fta_volume`.
- `mid_range_shot_2k`, `close_shot_2k`: from shot-zone data (sports-reference split: 0-3ft, 3-10ft, 10-16ft, 16ft-3P).
- `shot_iq_2k`, `offensive_consistency_2k`: composite of shooting splits + USG% + TOV%.

**Finishing / inside scoring**
- `driving_layup_2k`, `driving_dunk_2k`: `LinearRegression(rim_fg%, dunks_per36, max_vert_in, weight_lbs)`.
- `standing_dunk_2k`: `LinearRegression(std_reach_in, max_vert_in, pos==C)`.
- `post_control_2k`, `draw_foul_2k`, `hands_2k`: features = post-up FG%, FTA-rate, turnover%.

**Playmaking**
- `ball_handle_2k`, `pass_iq_2k`, `pass_accuracy_2k`, `pass_vision_2k`: features = AST%, AST/TO, USG%, position, on-ball vs off-ball. Individual coefficients per attribute.

**Defense**
- `interior_defense_2k`: `LinearRegression(blk_per36, dreb_per36, height_in, wingspan_in, weight_lbs)`.
- `perimeter_defense_2k`: `LinearRegression(stl_per36, wingspan_in - height_in, lane_agility_sec(inv))` + scouting modulation.
- `block_2k`, `steal_2k`: direct-ish linear fits.
- `defensive_rebound_2k`, `offensive_rebound_2k`: `LinearRegression(reb_rates, height_in, weight_lbs, wingspan_in)`.
- `help_defense_iq_2k`, `pass_perception_2k`, `defensive_consistency_2k`: composites + scouting.

**Height reconciliation (combine without shoes → NBA listed)**

Build a lookup of combine alumni whose NBA listed height is known: compute `delta = listed - wo_shoes` binned by wo-shoes height. Early evidence from past combines shows ~1.0–1.5in add for guards, ~0.5–1.0in for bigs; fit a small piecewise function `height_delta(height_wo_shoes_in, pos)` and show it in the Formulas tab.

**Wingspan effect**

Treat `wingspan_minus_height` as a feature in `block_2k`, `steal_2k`, `interior_defense_2k`, `perimeter_defense_2k`, `driving_dunk_2k`, `standing_dunk_2k`. Coefficient quantified per attribute during calibration.

**Overall Rating 2K**

2K's Overall is a weighted sum of the attributes by position archetype — not a free regression. We will fit a position-aware linear combination on the calibration corpus (one weight vector per `pos ∈ {PG, SG, SF, PF, C}`) and validate it reproduces published Overalls within ±1.

**Potential (A+ → D)**

Logistic/ordinal regression on `age`, `espn_rank`, `scouting_phrases`, projected-overall-at-peak heuristics. Nice-to-have; Phase 3.

### 4.3 Formula storage format (YAML)

```yaml
attribute: strength_2k
version: 1
type: linear_regression
features:
  - name: weight_lbs
    coef: 0.42
  - name: height_in
    coef: 0.18
  - name: bmi
    coef: -0.05
intercept: 12.0
clamp: [25, 99]
notes: |
  Fit on 412 current NBA players. R^2 = 0.78. MAE = 4.1.
```

## 5. Project structure

```
k:\work\NBA2K_Workshop\
  app.py                       # Streamlit entrypoint (tabs: Reference / Prospects / Europe / Logs / Formulas / Settings)
  launcher.py                  # Port-check + browser launch (mirrors SubtitleForge)
  LaunchNBA2KWorkshop.bat
  requirements.txt
  README.md                    # (dated per user rule)
  PLAN.md                      # this file
  RELEASE_NOTES.md
  .env                         # GOOGLE_*, user-agent, rate-limit knobs
  data/
    workshop.db
    cache/2kratings/, cache/nba/, cache/espn/, cache/cbb/
    formulas/*.yaml
    user_uploads/
    exports/
  src/
    __init__.py
    config.py
    logger.py
    db.py                      # sqlite schema + migrations
    scrapers/
      twokratings.py
      nba_stats.py
      nba_combine.py
      espn_bigboard.py
      sports_reference_cbb.py
      international.py
      scouting.py
    calibration/
      build_corpus.py
      fit_formulas.py          # outputs data/formulas/*.yaml
      evaluate.py
    formulas/
      registry.py              # load yaml -> callable
      apply.py                 # compute all ratings for a row
    exporters/
      excel_writer.py          # openpyxl
      gsheets_writer.py        # reuses SubtitleForge google creds
    ui/
      reference_tab.py
      prospects_tab.py
      europe_tab.py
      logs_tab.py
      formulas_tab.py
      settings_tab.py
    audit.py                   # append-only log helpers
  tests/
    test_scrapers_parsing.py
    test_formulas.py
    test_height_reconciliation.py
```

## 6. Phased delivery (maps to todos below)

Each phase ends with a working Streamlit build and a passing `pytest`.

1. Scaffold + config + SQLite + logger (half-day).
2. Reference data ingest: scrape 2kratings for ~500 NBA players, pull nba_api stats/combine, populate `Reference` tab.
3. Calibration: fit v1 of every formula; write `data/formulas/*.yaml`; show fit metrics in Formulas tab.
4. Prospects ingest: ESPN top 100 + 20 supplementary; college/international stats; populate `Prospects` tab with v1 ratings.
5. Combine override path (ready for May 10, 2026): after-combine refresh button wires `c_*_2k` columns to override computed values.
6. Excel + Google Sheets export; formulas + logs sheets embedded.
7. UX — add/remove player; upload CSV/XLSX; per-cell manual override; editable formulas tab with live recalc.
8. Scouting-report ingestion + keyword modulation.
9. Europeans tab (Euroleague full roster, no filter to 120; same formulas).
10. Auto-update schedule (Windows Task Scheduler helper script).

## 7. Suggestions & remarks for the user

- **NBA pre-computes `2K`-scaled combine columns** in some historical `DraftCombineStats` responses (Speed 2K, Agility 2K, Vertical 2K). If those columns are still present in the 2026 endpoint, we use them verbatim (matching the user's "C Speed 2K" column) — huge accuracy win. If absent, we fit a small 3-feature regression from raw drill times against the same label for past attendees who now have 2K26 ratings, then reproduce them.
- **Age-aware potential boost**: 2K rewards younger prospects with higher Potential ratings; an 18.0-year-old freshman at ESPN rank 10 deserves more upside headroom than a 23.0-year-old senior at rank 10. Propose encoding this as a separate `potential_2k` formula rather than inflating per-attribute ratings.
- **Pre-combine fallback** (we're 16 days out): before May 10, physicals come from the prospect's school/team listing (with a known ~1in inflation vs combine). After May 10, the tool offers a one-click refresh that rewrites physicals and flags every changed cell in the Logs tab.
- **3PT line penalty** is a real, measurable effect — we'll fit it rather than hard-code it. Expected magnitude: roughly `-2 to -4` on `three_point_shot_2k` for NCAA shooters' raw 3P% vs an equivalent NBA shooter; similar for Euroleague/FIBA.
- **Overall 2K ≠ average of attributes** — it's a position-weighted composite. We'll learn the weights from data per position bucket rather than guessing; accept ±1 error target.
- **Scraping manners**: 1 req/sec on 2kratings.com, polite `User-Agent`, respect `robots.txt`. For personal use this is fine; we'll surface a note in the Settings tab.
- **Free-only constraint** — every source chosen (2kratings, nba.com/stats via `nba_api`, espn.com, sports-reference.com, proballers.com) is free. No paid APIs, no LLM keys required. Optional local-Ollama hook for scouting-report phrase extraction (you already run Ollama in Chronos) stays off by default.
- **Google Sheets export**: we reuse SubtitleForge's existing `google-api-python-client` + `google-auth-oauthlib` credentials; no new OAuth consent screen needed if you already have a `credentials.json` locally.
- **Logs page** is append-only and filterable by `actor`, `action`, `entity_slug`, `field`; ratings cells link back to the log entry that last changed them.
- **Formula edits are versioned**: the `formulas.version` column + on-disk YAML history let you compare v1 vs v2 and roll back; every recalc stamps `prospect_ratings_computed.formula_version` for traceability.

## 8. Out of scope for v1 (call out now, do later)

- Live in-game roster file injection (`.ROS` format) — 2K does not publish the schema; the market tool for this is third-party and Windows-only. v1 ships the spreadsheet only. v2 can add a NBA2K-editing tool handoff if you want.
- Private/paid sources (Synergy, Second Spectrum) — excluded by the free-only acceptance criterion.
- Non-Euroleague international leagues beyond Phase 2 Europe (NBL, CBA specifically covered only for the handful of 2026-draft prospects playing there).