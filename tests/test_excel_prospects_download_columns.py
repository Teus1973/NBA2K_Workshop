"""Excel Prospects sheet: download slice = bio + ``height_ft`` + stats + ratings."""

from __future__ import annotations

import tempfile
from pathlib import Path

import openpyxl

from src import config
from src.exporters import excel_writer


def test_excel_download_column_keys_count_and_order() -> None:
    cols = excel_writer.PROSPECTS_EXCEL_DOWNLOAD_COLUMNS
    n_stat = len(config.STAT_COLUMNS)
    n_rtg = len(config.RATING_ATTRIBUTES)
    assert len(cols) == 8 + n_stat + n_rtg
    assert cols[:8] == (
        "last_name",
        "first_name",
        "pos",
        "secondary_position",
        "age",
        "height_in",
        "height_ft",
        "weight_lbs",
    )
    assert cols[8 : 8 + n_stat] == tuple(config.STAT_COLUMNS)
    assert cols[8 + n_stat :] == tuple(config.RATING_ATTRIBUTES)


def test_export_prospects_sheet_headers_and_width() -> None:
    """Prospects row 1: friendly headers for leading physical cols; stats use DB keys."""
    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    path = tmp.name
    try:
        excel_writer.export_to_excel(path, provenance_by_slug={})
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            ws = wb["Prospects"]
            row = next(ws.iter_rows(min_row=1, max_row=1, values_only=True))
        finally:
            wb.close()
        names = [c for c in row if c is not None and str(c).strip() != ""]
        n_stat = len(config.STAT_COLUMNS)
        n_rtg = len(config.RATING_ATTRIBUTES)
        assert len(names) == 8 + n_stat + n_rtg
        assert names[:8] == [
            "last_name",
            "first_name",
            "Pos",
            "secondary_position",
            "Age",
            "Height (in)",
            "Height (ft)",
            "Weight (lbs)",
        ]
        assert names[8 : 8 + n_stat] == list(config.STAT_COLUMNS)
        assert names[8 + n_stat :] == list(config.RATING_ATTRIBUTES)
    finally:
        Path(path).unlink(missing_ok=True)
