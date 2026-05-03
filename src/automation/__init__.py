"""Automation helpers (Remote Play / virtual controller bridging)."""

from src.automation.controller_mapping import (
    INDEX_TO_NAV_MAP,
    get_ocr_calibration_context_overlay,
    get_ocr_calibration_preview_pair,
    get_ocr_diagnostic_image,
    neutralize_virtual_stick,
    preview_tesseract_rating_at_roi,
    push_prospect_row_to_controller,
    send_capture_handshake,
)

__all__ = [
    "INDEX_TO_NAV_MAP",
    "get_ocr_calibration_context_overlay",
    "get_ocr_calibration_preview_pair",
    "get_ocr_diagnostic_image",
    "neutralize_virtual_stick",
    "preview_tesseract_rating_at_roi",
    "push_prospect_row_to_controller",
    "send_capture_handshake",
]