"""
NBA2K26 Workshop — session file logging.

Mirrors SubtitleForge/src/logger.py: idempotent FileHandler attached to a
named logger tree (`nba2k_workshop.*`) writing to the project-root session
log. Modules get a child logger via :func:`get_logger`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_LOG_PATH = _PROJECT_ROOT / "nba2k_workshop_session.log"

_SESSION_CONFIGURED = False


def configure_session_logging() -> None:
    """Attach the file handler to the ``nba2k_workshop`` logger (idempotent)."""
    global _SESSION_CONFIGURED
    if _SESSION_CONFIGURED:
        return
    if os.environ.get("NBA2K_WORKSHOP_TESTING") == "1":
        _SESSION_CONFIGURED = True
        return

    root = logging.getLogger("nba2k_workshop")
    root.setLevel(logging.INFO)
    root.propagate = False

    fh = logging.FileHandler(SESSION_LOG_PATH, encoding="utf-8", mode="a")
    fh.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    _SESSION_CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under ``nba2k_workshop``."""
    configure_session_logging()
    return logging.getLogger(f"nba2k_workshop.{name}")
