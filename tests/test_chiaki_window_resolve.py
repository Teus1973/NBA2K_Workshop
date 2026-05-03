"""Strict Chiaki / pygetwindow title filtering (no desktop capture required)."""

from __future__ import annotations

import contextlib
import sys
import types

import pytest

import src.automation.controller_mapping as cm


def _install_pygetwindow_stub(monkeypatch: pytest.MonkeyPatch, windows: list[object]) -> None:
    stub = types.ModuleType("pygetwindow")
    stub.getAllWindows = lambda: windows  # noqa: E731
    monkeypatch.setitem(sys.modules, "pygetwindow", stub)


@pytest.fixture(autouse=True)
def _suppress_dpi_and_foreground(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cm, "_ensure_process_dpi_aware", lambda: None)

    @contextlib.contextmanager
    def fake_fg(_win: object):
        yield

    monkeypatch.setattr(cm, "_temporary_activate_chiaki_window", fake_fg)


def _fake_win(
    title: str,
    *,
    width: int = 800,
    height: int = 600,
    left: int = 10,
    top: int = 20,
) -> object:
    return types.SimpleNamespace(
        title=title,
        width=width,
        height=height,
        left=left,
        top=top,
        activate=lambda: None,
    )


def test_strict_filter_drops_browser_and_prefers_chiaki(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pygetwindow_stub(
        monkeypatch,
        [
            _fake_win("NBA2K Workshop - Google Chrome"),
            _fake_win("localhost:8501 - Streamlit"),
            _fake_win("Chiaki-ng | Session", left=111),
            _fake_win("Microsoft Edge"),
        ],
    )
    win_obj, sel = cm.locate_chiaki_window_pygetwindow()
    assert sel == "Chiaki-ng | Session"
    assert int(win_obj.left) == 111


def test_remote_play_positive_without_browser_tokens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_pygetwindow_stub(
        monkeypatch,
        [_fake_win("PS Remote Play", left=5)],
    )
    _, sel = cm.locate_chiaki_window_pygetwindow()
    assert "Remote Play" in sel


def test_no_matching_window_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_pygetwindow_stub(
        monkeypatch,
        [_fake_win("Untitled"),
         _fake_win("Calculator"),
         ],
    )
    with pytest.raises(RuntimeError):
        cm.locate_chiaki_window_pygetwindow()
