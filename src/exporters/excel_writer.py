"""
openpyxl-based Excel writer.

Builds a four-sheet workbook (Reference / Prospects / Logs / Formulas). The
Prospects sheet color-codes cells by source (scraped / combine / computed /
manual-override) using the provenance dict produced by
:func:`src.formulas.apply.apply_to_prospect`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils.dataframe import dataframe_to_rows

from .. import audit, config
from ..logger import get_logger
from . import data_loader

log = get_logger("exporters.excel_writer")


# Color palette for the Prospects sheet provenance coding (Documents/PLAN.md sec 2.2).
COLOR_SCRAPED = PatternFill("solid", fgColor="C6EFCE")  # green
COLOR_COMBINE = PatternFill("solid", fgColor="BDD7EE")  # blue
COLOR_FORMULA = PatternFill("solid", fgColor="FFEB9C")  # yellow
COLOR_MANUAL = PatternFill("solid", fgColor="D9D9D9")   # grey
HEADER_FILL = PatternFill("solid", fgColor="305496")
HEADER_FONT = Font(bold=True, color="FFFFFF")


PROVENANCE_FILLS: dict[str, PatternFill] = {
    "scraped": COLOR_SCRAPED,
    "combine": COLOR_COMBINE,
    "formula": COLOR_FORMULA,
    "manual": COLOR_MANUAL,
}


def _write_df(ws, df: pd.DataFrame, *, freeze: str = "A2") -> None:
    if df is None or df.empty:
        ws.append(["(no data)"])
        return
    for i, row in enumerate(dataframe_to_rows(df, index=False, header=True)):
        ws.append(row)
        if i == 0:
            for cell in ws[ws.max_row]:
                cell.fill = HEADER_FILL
                cell.font = HEADER_FONT
                cell.alignment = Alignment(horizontal="center")
    ws.freeze_panes = freeze
    for col in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0
                      for c in col), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(max(length + 2, 10), 36)


def _apply_prospect_provenance(
    ws,
    df: pd.DataFrame,
    provenance_by_slug: Mapping[str, Mapping[str, str]],
) -> None:
    if df.empty or not provenance_by_slug:
        return
    col_index = {name: idx + 1 for idx, name in enumerate(df.columns)}
    slug_col = col_index.get("slug")
    if slug_col is None:
        return
    for i, slug in enumerate(df["slug"].tolist(), start=2):
        prov = provenance_by_slug.get(slug) or {}
        for attr, source in prov.items():
            if attr not in col_index:
                continue
            fill = PROVENANCE_FILLS.get(source.split("+")[0])
            if fill is None:
                continue
            ws.cell(row=i, column=col_index[attr]).fill = fill


# ---------------------------------------------------------------------------
def export_to_excel(
    out_path: Path | str,
    *,
    provenance_by_slug: Mapping[str, Mapping[str, str]] | None = None,
    season: str = config.CURRENT_SEASON,
    season_type: str = "Regular",
) -> Path:
    """Write the four-sheet Excel workbook and return the output path."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws_ref = wb.active
    ws_ref.title = "Reference"
    ws_pro = wb.create_sheet("Prospects")
    ws_eur = wb.create_sheet("Europeans")
    ws_log = wb.create_sheet("Logs")
    ws_for = wb.create_sheet("Formulas")

    ref_df = data_loader.load_reference_df(season, season_type)
    pro_df = data_loader.load_prospects_df()
    log_df = data_loader.load_audit_df(limit=5000)
    for_df = data_loader.load_formulas_df()

    _write_df(ws_ref, ref_df)
    _write_df(ws_pro, pro_df)
    _apply_prospect_provenance(ws_pro, pro_df, provenance_by_slug or {})
    _write_df(ws_eur, pd.DataFrame({"note": ["Phase 2 / deferred"]}))
    _write_df(ws_log, log_df)
    _write_df(ws_for, for_df.drop(columns=["yaml_blob"], errors="ignore")
              if not for_df.empty else for_df)

    wb.save(str(out_path))

    audit.log_event(
        action="export_excel",
        entity_type="export",
        entity_slug=out_path.name,
        note=(f"reference={len(ref_df)} prospects={len(pro_df)} "
              f"logs={len(log_df)} formulas={len(for_df)}"),
    )
    log.info("Excel export -> %s (%d prospects, %d reference)",
             out_path, len(pro_df), len(ref_df))
    return out_path
