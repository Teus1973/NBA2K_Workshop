"""Tests for Remote Play controller bridging (no vgamepad import required)."""

from __future__ import annotations

import inspect
import sys
import types

import pytest

from src import config
from src.automation import controller_mapping as cm
from src.automation.controller_mapping import (
    INDEX_TO_NAV_MAP,
    _apply_rating_delta_from_ocr,
    parse_rating_digits_from_text,
    _effective_rating,
    _potential_sheet_value,
    push_prospect_row_to_controller,
    send_capture_handshake,
)


class _FakeGamepad:
    def __init__(self) -> None:
        self.left_calls: list[tuple[float, float]] = []
        self.right_calls: list[tuple[float, float]] = []
        self.updates = 0
        self.dpad_release_count = 0

    def left_joystick_float(self, x_value: float, y_value: float = 0.0) -> None:
        self.left_calls.append((float(x_value), float(y_value)))

    def right_joystick_float(self, x_value: float, y_value: float = 0.0) -> None:
        self.right_calls.append((float(x_value), float(y_value)))

    def press_button(self, *, button: object) -> None:
        _ = button

    def release_button(self, *, button: object) -> None:
        _ = button
        self.dpad_release_count += 1

    def update(self) -> None:
        self.updates += 1


@pytest.fixture
def no_automation_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.automation.controller_mapping.time.sleep",
        lambda *_a, **_k: None,
    )
    stub_vg = types.ModuleType("vgamepad")
    stub_vg.XUSB_BUTTON = types.SimpleNamespace(
        XUSB_GAMEPAD_DPAD_DOWN=object(),
        XUSB_GAMEPAD_DPAD_LEFT=object(),
        XUSB_GAMEPAD_DPAD_RIGHT=object(),
    )
    monkeypatch.setitem(sys.modules, "vgamepad", stub_vg)


def test_index_to_nav_map_covers_87_columns() -> None:
    assert len(INDEX_TO_NAV_MAP) == len(config.PROSPECTS_TABLE_COLUMNS)
    assert set(INDEX_TO_NAV_MAP.keys()) == set(range(87))


def test_anchor_literals_match_schema_columns() -> None:
    assert INDEX_TO_NAV_MAP[34] == "Overall"
    assert INDEX_TO_NAV_MAP[35] == "Driving Layup"
    assert config.PROSPECTS_TABLE_COLUMNS.index("intangibles_2k") == 68
    assert INDEX_TO_NAV_MAP[68] == "Integnagbles"
    assert config.PROSPECTS_TABLE_COLUMNS.index("durability_2k") == 70
    assert INDEX_TO_NAV_MAP[70] == "Durablity"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("post_hook_2k")] == "Post Hook"
    assert INDEX_TO_NAV_MAP[config.PROSPECTS_TABLE_COLUMNS.index("post_fade_2k")] == "Post Fade"


def test_typo_menu_anchors_accept_alternate_game_spellings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Session-start validation allows corrected or variant 2K menu strings (case-insensitive)."""
    monkeypatch.setitem(cm.INDEX_TO_NAV_MAP, 68, "Intangibles")
    monkeypatch.setitem(cm.INDEX_TO_NAV_MAP, 70, "durabiilty")
    cm._assert_typo_menu_anchors()


def test_effective_rating_applies_plus_one_and_clamps() -> None:
    assert _effective_rating(24) == 25
    assert _effective_rating(50) == 51
    assert _effective_rating(98) == 99
    assert _effective_rating(99) == 99


def test_potential_meta_skips_plus_one_bump() -> None:
    assert _potential_sheet_value(77) == 77


def test_overall_2k_locked_at_schema_index_34() -> None:
    assert config.PROSPECTS_TABLE_COLUMNS[34] == "overall_2k"
    assert config.PROSPECTS_TABLE_COLUMNS.index("overall_2k") == 34


def test_push_prospect_row_accepts_gamepad_and_ocr_kwargs() -> None:
    sig = inspect.signature(push_prospect_row_to_controller)
    assert "gamepad" in sig.parameters
    assert sig.parameters["gamepad"].default is inspect.Parameter.empty
    assert sig.parameters["use_ocr_feedback"].default is False
    assert sig.parameters["ocr_roi_relative_xywh"].default is None


def test_preview_tesseract_rating_at_roi_calibration_signature() -> None:
    sig = inspect.signature(cm.preview_tesseract_rating_at_roi)
    assert "roi_relative_xywh" in sig.parameters


def test_roi_relative_xywh_absolute_region() -> None:
    roi = cm._roi_abs_from_window_rect((100, 200, 640, 480), (420, 220, 100, 36))
    assert roi == {"left": 520, "top": 420, "width": 100, "height": 36}


def test_shave_roi_insets_lr_and_tb_before_ocr() -> None:
    from PIL import Image

    img = Image.new("RGB", (60, 40), color=(128, 128, 128))
    out = cm._shave_roi(img)
    assert out.size == (46, 32)


def test_parse_rating_digits_from_text() -> None:
    assert parse_rating_digits_from_text("") is None
    assert parse_rating_digits_from_text("abc") is None
    assert parse_rating_digits_from_text("80") == 80
    assert parse_rating_digits_from_text("24 80") == 80
    assert parse_rating_digits_from_text("12 25") == 25


def test_ocr_sync_loop_retries_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PIL import Image

    img = Image.new("RGB", (8, 8))
    n = {"tess": 0}
    sleeps: list[float] = []
    monkeypatch.setattr(cm, "capture_rating_cell_image_pil", lambda **kw: img)

    def _fake_tesseract(_rgb: object) -> str:
        n["tess"] += 1
        if n["tess"] < 3:
            return ""
        return "81"

    monkeypatch.setattr(cm, "_tesseract_rating_text", _fake_tesseract)

    def _rec_sleep(seconds: float) -> None:
        sleeps.append(float(seconds))

    monkeypatch.setattr("src.automation.controller_mapping.time.sleep", _rec_sleep)
    assert cm._read_rating_via_ocr_sync_loop() == 81
    assert n["tess"] == 3
    assert sleeps == [cm._READ_RATING_RETRY_DELAY_S, cm._READ_RATING_RETRY_DELAY_S]


def test_delta_pulse_sequence_updates_per_step(
    monkeypatch: pytest.MonkeyPatch, no_automation_sleep
) -> None:
    gp = _FakeGamepad()
    monkeypatch.setattr(
        cm, "read_rating_via_ocr_threaded_blocking", lambda *a, **k: 76
    )
    _apply_rating_delta_from_ocr(gp, 79)
    assert gp.right_calls == [
        (0.0, 0.0),
        (0.0, 1.0),
        (0.0, 0.0),
    ] * 3
    assert gp.updates == 9


def test_delta_downward_uses_negative_y(
    monkeypatch: pytest.MonkeyPatch, no_automation_sleep
) -> None:
    gp = _FakeGamepad()
    monkeypatch.setattr(
        cm, "read_rating_via_ocr_threaded_blocking", lambda *a, **k: 82
    )
    _apply_rating_delta_from_ocr(gp, 80)
    assert gp.right_calls == [
        (0.0, 0.0),
        (0.0, -1.0),
        (0.0, 0.0),
    ] * 2


def test_push_overall_with_ocr_delta_path(
    monkeypatch: pytest.MonkeyPatch, no_automation_sleep
) -> None:
    gp = _FakeGamepad()
    row = [None] * 87
    row[34] = 79  # effective 80
    monkeypatch.setattr(
        cm, "read_rating_via_ocr_threaded_blocking", lambda *a, **k: 77
    )
    push_prospect_row_to_controller(
        row, gamepad=gp, use_ocr_feedback=True,
    )
    assert gp.right_calls.count((0.0, 1.0)) == 3
    assert gp.right_calls[-1] == (0.0, 0.0)


def test_skips_bio_stats_without_edit_player_mode(no_automation_sleep) -> None:
    gp = _FakeGamepad()
    row = [None] * 87
    row[34] = 79  # overall → effective 80
    push_prospect_row_to_controller(row, gamepad=gp)
    # right-stick deflect + right-stick reset + finally dual-stick neutralize
    assert len(gp.right_calls) == 3
    assert gp.right_calls[-1] == (0.0, 0.0)
    assert gp.left_calls == [(0.0, 0.0)]
    assert gp.dpad_release_count == 1


def test_edit_player_mode_processes_early_columns_when_ratings_present(
    no_automation_sleep,
) -> None:
    gp = _FakeGamepad()
    row = [None] * 87
    row[5] = 50  # pos column — not a rating; should not emit stick when never rating idx
    push_prospect_row_to_controller(row, edit_player_mode=True, gamepad=gp)
    assert gp.left_calls == [(0.0, 0.0)]
    assert gp.right_calls == [(0.0, 0.0)]


def test_potential_index_87_emits_input(no_automation_sleep) -> None:
    gp = _FakeGamepad()
    row = [None] * 88
    row[87] = 82
    push_prospect_row_to_controller(row, gamepad=gp)
    assert gp.right_calls


def test_send_capture_handshake_dpad_only_does_not_touch_left_stick(
    monkeypatch: pytest.MonkeyPatch,
    no_automation_sleep,
) -> None:
    btn = object()
    stub_vg = types.ModuleType("vgamepad")
    stub_vg.XUSB_BUTTON = types.SimpleNamespace(XUSB_GAMEPAD_DPAD_DOWN=btn)
    monkeypatch.setitem(sys.modules, "vgamepad", stub_vg)

    class _RecordingPad:
        def __init__(self) -> None:
            self.button_events: list[tuple[str, object]] = []
            self.updates = 0

        def press_button(self, *, button: object) -> None:
            self.button_events.append(("press", button))

        def release_button(self, *, button: object) -> None:
            self.button_events.append(("release", button))

        def left_joystick_float(self, x_value: float, y_value: float = 0.0) -> None:
            raise AssertionError("handshake must not move the left stick (anchor-safe)")

        def right_joystick_float(self, x_value: float, y_value: float = 0.0) -> None:
            raise AssertionError("handshake must not move the right stick (anchor-safe)")

        def update(self) -> None:
            self.updates += 1

    gp = _RecordingPad()
    send_capture_handshake(gp)
    assert gp.button_events == [("press", btn), ("release", btn)]
    assert gp.updates == 2
