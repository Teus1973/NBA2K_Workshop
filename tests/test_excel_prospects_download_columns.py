"""Excel Prospects sheet: fixed 44-column download slice (UI table unchanged)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import openpyxl

from src import config
from src.exporters import excel_writer


def test_excel_download_column_keys_count_and_order() -> None:
    cols = excel_writer.PROSPECTS_EXCEL_DOWNLOAD_COLUMNS
    assert len(cols) == 44
    assert cols[:7] == (
        "last_name",
        "first_name",
        "pos",
        "secondary_position",
        "age",
        "height_in",
        "weight_lbs",
    )
    assert cols[7:] == tuple(config.RATING_ATTRIBUTES)


def test_export_prospects_sheet_headers_and_width() -> None:
    """Built workbook Prospects row 1 matches spec; only 44 data columns."""
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
        assert len(names) == 44
        assert names[:7] == [
            "last_name",
            "first_name",
            "Pos",
            "secondary_position",
            "Age",
            "Height",
            "weight_lbs",
        ]
        assert names[7:] == list(config.RATING_ATTRIBUTES)
    finally:
        Path(path).unlink(missing_ok=True)
