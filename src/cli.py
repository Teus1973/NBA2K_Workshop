"""
NBA2K26 Workshop command-line interface.

Usage::

    python -m src.cli bootstrap      # FIRST-TIME: pull every data source +
                                     # bulk-scrape 2kratings + refit + recalc
    python -m src.cli refresh        # full end-to-end refresh (no 2kratings)
    python -m src.cli refresh-stats  # just NBA stats + bio
    python -m src.cli refresh-combine
    python -m src.cli refresh-prospects
    python -m src.cli refresh-2kratings [--limit N] [--force]
    python -m src.cli refit
    python -m src.cli recalc
    python -m src.cli export-excel [OUT]

The ``refresh`` umbrella is what the Windows Task Scheduler job (see
``scripts/NBA2K_Workshop_Weekly.xml``) invokes weekly. ``bootstrap`` is the
one-time heavy lift that also sweeps 2kratings.com (~10 min at 1 rps).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import audit, config, db
from .logger import configure_session_logging, get_logger

log = get_logger("cli")


# ---------------------------------------------------------------------------
def refresh_stats() -> None:
    from .scrapers import nba_stats
    log.info("refresh_stats: fetching LeagueDash + bio...")
    totals = nba_stats.fetch_season_totals(
        season_type="Regular Season", force_refresh=True)
    bio = nba_stats.fetch_bio_stats(force_refresh=True)
    stat_rows = nba_stats.season_stats_rows(
        totals, season=config.CURRENT_SEASON, season_type="Regular")
    bio_rows_list = nba_stats.bio_rows(bio)
    conn = db.connect()
    try:
        nba_stats.upsert_players(conn, bio_rows_list)
        nba_stats.upsert_stats(conn, stat_rows)
    finally:
        conn.close()
    audit.log_event(
        action="stat_refresh",
        entity_type="nba_player",
        note=f"cli: {len(stat_rows)} season rows, {len(bio_rows_list)} players",
    )


def refresh_combine() -> None:
    from .scrapers import nba_combine
    conn = db.connect()
    try:
        n = nba_combine.refresh_all_years(conn)
    finally:
        conn.close()
    log.info("refresh_combine: %d combine rows", n)


def refresh_prospects() -> None:
    from .scrapers import espn_bigboard
    prospects = espn_bigboard.load_prospects(force_refresh=True)
    conn = db.connect()
    try:
        espn_bigboard.upsert_prospects(conn, prospects)
    finally:
        conn.close()
    log.info("refresh_prospects: %d prospects", len(prospects))


def refit_formulas() -> None:
    from .calibration import fit_formulas
    res = fit_formulas.fit_all()
    log.info("refit_formulas: %d attributes", len(res))


def recalc_ratings() -> None:
    from .exporters import data_loader
    from .formulas import apply as fapply, registry as _reg
    reg = _reg.load_registry()
    df = data_loader.load_prospects_df()
    conn = db.connect()
    try:
        n = 0
        for _, row in df.iterrows():
            ratings, _prov = fapply.apply_to_prospect(row.to_dict(), reg)
            cols = ["slug"] + list(config.RATING_ATTRIBUTES) + [
                "formula_version", "manual_override_json"]
            values = [row["slug"]] + [ratings.get(a) for a in config.RATING_ATTRIBUTES] + [1, None]
            placeholders = ", ".join(["?"] * len(cols))
            sql = (
                f"INSERT INTO prospect_ratings_computed ({', '.join(cols)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT(slug) DO UPDATE SET "
                + ", ".join(f"{c}=excluded.{c}" for c in cols[1:])
            )
            conn.execute(sql, values)
            n += 1
    finally:
        conn.close()
    audit.log_event(
        action="rating_recalc",
        entity_type="prospect",
        note=f"cli recalc: {n} prospects",
    )
    log.info("recalc_ratings: %d prospects", n)


def export_excel(out: Path | None = None) -> Path:
    from .exporters import excel_writer
    import pandas as pd
    out_path = out or (
        config.EXPORTS_DIR / f"nba2k26_{pd.Timestamp.now(tz='UTC'):%Y%m%d_%H%M%S}.xlsx")
    excel_writer.export_to_excel(out_path)
    return Path(out_path)


def refresh_2kratings(
    *,
    limit: int | None = None,
    force: bool = False,
    only_missing: bool = False,
) -> dict:
    """Bulk-scrape 2kratings.com for every player already in nba_players."""
    from .scrapers import twokratings
    log.info(
        "refresh_2kratings: start (limit=%s force=%s only_missing=%s)",
        limit, force, only_missing)

    def _cb(i: int, total: int, slug: str, status: str) -> None:
        if i == 1 or i % 25 == 0 or i == total:
            log.info("  [%d/%d] %-30s %s", i, total, slug, status)

    result = twokratings.bulk_scrape_and_upsert(
        limit=limit, force_refresh=force, only_missing=only_missing,
        progress_cb=_cb)
    log.info("refresh_2kratings: %s", result)
    return result


def refresh_prospect_stats() -> dict:
    """Bulk-scrape sports-reference CBB stats for every NCAA prospect."""
    from .scrapers import sports_reference_cbb as cbb
    log.info("refresh_prospect_stats: start")

    def _cb(i: int, total: int, slug: str, status: str) -> None:
        if i == 1 or i % 20 == 0 or i == total:
            log.info("  [%d/%d] %-30s %s", i, total, slug, status)

    result = cbb.bulk_scrape_ncaa_prospects(progress_cb=_cb)
    log.info("refresh_prospect_stats: %s", result)
    return result


def refresh_all() -> None:
    log.info("refresh_all: start")
    refresh_stats()
    refresh_combine()
    refresh_prospects()
    refit_formulas()
    recalc_ratings()
    log.info("refresh_all: done")


def bootstrap(*, limit_2k: int | None = None,
              skip_prospect_stats: bool = False) -> None:
    """First-time end-to-end pipeline.

    Order matters: bio/stats -> combine -> prospects -> 2kratings (slow) ->
    prospect CBB stats -> refit (needs the 2kratings corpus) -> recalc.
    """
    log.info("bootstrap: start")
    refresh_stats()
    refresh_combine()
    refresh_prospects()
    refresh_2kratings(limit=limit_2k, force=False)
    if not skip_prospect_stats:
        try:
            refresh_prospect_stats()
        except Exception as exc:  # noqa: BLE001
            log.warning("refresh_prospect_stats failed (non-fatal): %s", exc)
    refit_formulas()
    recalc_ratings()
    log.info("bootstrap: done")


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    configure_session_logging()
    parser = argparse.ArgumentParser(
        prog="nba2k_workshop",
        description="NBA2K26 Workshop command-line interface.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_boot = sub.add_parser(
        "bootstrap",
        help="first-time full pipeline incl. 2kratings sweep (~10 min)")
    p_boot.add_argument("--limit-2k", type=int, default=None,
                        help="Cap the 2kratings sweep (useful for smoke tests)")
    sub.add_parser("refresh",
                   help="stats + combine + prospects + refit + recalc")
    sub.add_parser("refresh-stats")
    sub.add_parser("refresh-combine")
    sub.add_parser("refresh-prospects")
    p_2k = sub.add_parser("refresh-2kratings",
                          help="bulk-scrape 2kratings.com for nba_players")
    p_2k.add_argument("--limit", type=int, default=None)
    p_2k.add_argument("--force", action="store_true",
                      help="Ignore HTML cache and refetch every page")
    p_2k.add_argument("--missing", action="store_true",
                      help="Only players without a nba_ratings_2k26 row")
    sub.add_parser("refresh-prospect-stats",
                   help="scrape sports-reference CBB stats for NCAA prospects")
    sub.add_parser("refit")
    sub.add_parser("recalc")
    p_exp = sub.add_parser("export-excel")
    p_exp.add_argument("out", nargs="?", default=None)
    args = parser.parse_args(argv)

    cmd = args.command
    if cmd == "bootstrap":
        bootstrap(limit_2k=args.limit_2k)
    elif cmd == "refresh":
        refresh_all()
    elif cmd == "refresh-stats":
        refresh_stats()
    elif cmd == "refresh-combine":
        refresh_combine()
    elif cmd == "refresh-prospects":
        refresh_prospects()
    elif cmd == "refresh-2kratings":
        refresh_2kratings(
            limit=args.limit, force=args.force, only_missing=args.missing)
    elif cmd == "refresh-prospect-stats":
        refresh_prospect_stats()
    elif cmd == "refit":
        refit_formulas()
    elif cmd == "recalc":
        recalc_ratings()
    elif cmd == "export-excel":
        p = export_excel(Path(args.out) if args.out else None)
        print(json.dumps({"path": str(p)}))
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
