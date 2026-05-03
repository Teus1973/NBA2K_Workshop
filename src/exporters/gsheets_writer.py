"""
Google Sheets writer (optional).

Reuses SubtitleForge's OAuth flow: if ``config.GOOGLE_CREDENTIALS_PATH`` points
to a ``credentials.json`` client-secret file we run a short-lived browser OAuth
flow and cache the resulting token under ``%APPDATA%\\NBA2KWorkshop\\``.

When the Google libraries aren't installed the module raises a clear
ImportError with a pip-install hint.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from .. import audit, config
from ..logger import get_logger
from . import data_loader

log = get_logger("exporters.gsheets_writer")


SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

TOKEN_PATH = config.get_user_data_dir() / "gsheets_token.json"


def _apply_prospects_sheet_focus(service: Any, spreadsheet_id: str) -> None:
    """Hide cols A–B and stats band **O–AH** (indices **14–33**); freeze row 1 + 4 cols."""
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties",
    ).execute()
    prospects_sid: int | None = None
    for sh in meta.get("sheets", []):
        props = sh.get("properties") or {}
        if props.get("title") == "Prospects":
            prospects_sid = props.get("sheetId")
            break
    if prospects_sid is None:
        return
    requests_body: list[dict[str, Any]] = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": prospects_sid,
                    "gridProperties": {
                        "frozenRowCount": 1,
                        "frozenColumnCount": 4,
                    },
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": prospects_sid,
                    "dimension": "COLUMNS",
                    "startIndex": 0,
                    "endIndex": 2,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {
                    "sheetId": prospects_sid,
                    "dimension": "COLUMNS",
                    "startIndex": 14,
                    "endIndex": 34,
                },
                "properties": {"hiddenByUser": True},
                "fields": "hiddenByUser",
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests_body},
    ).execute()


# ---------------------------------------------------------------------------
def _require_google() -> Any:
    try:
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from googleapiclient.discovery import build  # noqa: F401
        from google.auth.transport.requests import Request  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Google Sheets export requires `pip install google-api-python-client "
            "google-auth-httplib2 google-auth-oauthlib`."
        ) from exc
    return True


def _get_credentials():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except Exception as exc:
            log.warning("failed to load gsheets token: %s", exc)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        if not config.GOOGLE_CREDENTIALS_PATH:
            raise RuntimeError(
                "GOOGLE_CREDENTIALS_PATH not set -- point it at your "
                "OAuth client-secret JSON (credentials.json).")
        client_path = Path(config.GOOGLE_CREDENTIALS_PATH)
        if not client_path.is_file():
            raise FileNotFoundError(f"{client_path} not found")
        flow = InstalledAppFlow.from_client_secrets_file(str(client_path), SCOPES)
        creds = flow.run_local_server(port=0)
        TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


# ---------------------------------------------------------------------------
def export_to_gsheets(
    title: str = "NBA2K26 Workshop Export",
    *,
    season: str = config.CURRENT_SEASON,
    season_type: str = "Regular",
) -> str:
    """Create (or update) a Google Sheet with the same 4 tabs as the Excel export.

    Returns the spreadsheet URL. Raises if Google auth is missing.
    """
    _require_google()
    from googleapiclient.discovery import build

    creds = _get_credentials()
    service = build("sheets", "v4", credentials=creds)

    ref_df = data_loader.load_reference_df(season, season_type)
    pro_df = data_loader.load_prospects_df()
    log_df = data_loader.load_audit_df(limit=5000)
    for_df = data_loader.load_formulas_df().drop(
        columns=["yaml_blob"], errors="ignore")

    body = {
        "properties": {"title": title},
        "sheets": [
            {"properties": {"title": t}}
            for t in ("Reference", "Prospects", "Europeans", "Logs", "Formulas")
        ],
    }
    created = service.spreadsheets().create(body=body).execute()
    sheet_id = created["spreadsheetId"]

    def write(name: str, df: pd.DataFrame) -> None:
        if df is None or df.empty:
            values = [["(no data)"]]
        else:
            values = [list(df.columns)] + df.astype(object).where(
                df.notna(), "").values.tolist()
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{name}!A1",
            valueInputOption="RAW",
            body={"values": values},
        ).execute()

    write("Reference", ref_df)
    write("Prospects", pro_df)
    write("Europeans", pd.DataFrame({"note": ["Phase 2 / deferred"]}))
    write("Logs", log_df)
    write("Formulas", for_df)

    _apply_prospects_sheet_focus(service, sheet_id)

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"
    audit.log_event(
        action="export_gsheets",
        entity_type="export",
        entity_slug=sheet_id,
        note=url,
    )
    log.info("Google Sheets export -> %s", url)
    return url
