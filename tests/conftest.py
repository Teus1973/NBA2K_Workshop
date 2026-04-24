"""Shared pytest config for NBA2K Workshop tests.

- Adds the project root to sys.path so `from src import ...` works.
- Marks the test environment so logger.py skips opening the session log.
- Redirects DB + cache to a temp workspace.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Keep the real session log pristine during tests.
os.environ["NBA2K_WORKSHOP_TESTING"] = "1"


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Isolate sqlite DB per test."""
    from src import config
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(config, "DB_PATH", db_path)
    from src import db as _db  # late import so patch takes effect
    conn = _db.connect(db_path)
    yield conn
    conn.close()
