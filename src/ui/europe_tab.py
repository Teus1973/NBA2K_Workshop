"""
Europeans tab (Phase 2 / deferred behind feature flag).

Placeholder UI that explains the roadmap and, if a future Euroleague scrape
has been run, shows those prospects with the same formulas applied.
"""

from __future__ import annotations

import os

import streamlit as st

FEATURE_FLAG = "NBA2K_WORKSHOP_EUROPE"


def render() -> None:
    st.header("Europeans -- Phase 2")
    st.info(
        "Phase 2 feature. When enabled this tab will show Euroleague "
        "prospects scored with the same per-attribute formulas but with "
        "a different 3pt-line penalty calibration.\n\n"
        f"Enable by setting the env var `{FEATURE_FLAG}=1`.",
    )
    enabled = os.environ.get(FEATURE_FLAG, "").strip() == "1"
    if not enabled:
        return

    st.caption("Feature flag ON -- placeholder content")
    st.write(
        "Coming soon:\n"
        "- Euroleague season-stats scraper\n"
        "- FIBA-line 3pt penalty retune\n"
        "- Separate Europe tab in the Excel export"
    )
