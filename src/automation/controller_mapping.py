# Automation logic synced with schema v6 and vgamepad Xbox 360 bridge

from __future__ import annotations

import contextlib
import concurrent.futures
import re
import sys
import threading
import time
from collections.abc import Callable, Generator
from typing import TYPE_CHECKING

from src import config

if TYPE_CHECKING:
    from PIL import Image


OnProgress = Callable[[float, str], None]

# Strict remote-play targeting: positive substring must match AND no negative substring.
_CHIAKI_POSITIVE_TITLE_SUBSTRINGS: tuple[str, ...] = (
    "chiaki",
    "remote play",
)
_CHIAKI_EXCLUDE_TITLE_SUBSTRINGS: tuple[str, ...] = (
    "nba2k workshop",
    "nba 2k workshop",
    "- streamlit",
    "streamlit",
    "browser",
    "localhost:",
    "127.0.0.1",
    "google chrome",
    "microsoft edge",
    "internet explorer",
    "mozilla firefox",
    "chrome",
    " msedge ",
    " edge",
    "edge —",
    "firefox",
    "brave browser",
    "brave",
    "vivaldi",
    "opera",
    "safari",
    "arc.browser",
)

_CHIAKI_FOCUS_ACTIVATE_SETTLE_S = 0.18

# Bounding box relative to Chiaki window top-left **(dpi-aware rect from pygetwindow)**.
# Tune via Streamlit Automation Settings or pass ``ocr_roi_relative_xywh`` to push/diagnostic APIs.
DEFAULT_OCR_ROI_RELATIVE_XYWH: tuple[int, int, int, int] = (730, 480, 60, 40)
_RATING_CELL_BBOX_RELATIVE_XYWH = DEFAULT_OCR_ROI_RELATIVE_XYWH

_READ_RATING_RETRY_DELAY_S = 0.1
_READ_RATING_MAX_ATTEMPTS = 3

# Max plausible one-shot adjustment (25→99 delta == 74). Beyond this OCR is wrong.
_MAX_RATING_DELTA_STEPS = 74


def _default_nav_label(column_key: str) -> str:
    base = column_key[:-3] if column_key.endswith("_2k") else column_key
    return base.replace("_", " ").title()


def _build_index_to_nav_map() -> dict[int, str]:
    cols = config.PROSPECTS_TABLE_COLUMNS
    nav = {i: _default_nav_label(cols[i]) for i in range(len(cols))}
    anchors = {
        cols.index("overall_2k"): "Overall",
        cols.index("driving_layup_2k"): "Driving Layup",
        cols.index("post_hook_2k"): "Post Hook",
        cols.index("post_fade_2k"): "Post Fade",
        cols.index("intangibles_2k"): "Integnagbles",
        cols.index("durability_2k"): "Durablity",
    }
    nav.update(anchors)
    return nav


INDEX_TO_NAV_MAP: dict[int, str] = _build_index_to_nav_map()

_RATING_INDICES: frozenset[int] = frozenset(
    config.PROSPECTS_TABLE_COLUMNS.index(attr) for attr in config.RATING_ATTRIBUTES
)

_OVERALL_2K_ANCHOR_INDEX = 34
# Schema v6 PS5 menu literals (typo parity): intangibles → Integnagbles, durability → Durablity
_INTANGIBLES2K_NAV_INDEX = 68
_DURABILITY2K_NAV_INDEX = 70

_STABILITY_INPUT_DELAY_S = 0.15
_BUFFER_FLUSH_INTERVAL_COLS = 10
_BUFFER_FLUSH_PAUSE_S = 0.4
_OVERALL_FIELD_SETTLE_S = 0.5
_OVERALL_ANCHOR_EXTRA_SETTLE_S = 0.3
_STICK_HOLD_S = 0.1
_DPAD_NAV_HOLD_S = 0.05
_DPAD_NAV_SETTLE_S = 0.15

_dpi_api_initialized = False

# Lazy executor for MSS + Pillow + Tesseract offload (caller thread waits on futures).
_rating_ocr_executor: concurrent.futures.ThreadPoolExecutor | None = None
_rating_ocr_executor_lock = threading.Lock()


def _get_rating_ocr_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Single shared pool — OCR workloads do not belong on Streamlit/GIL-heavy UI step."""
    global _rating_ocr_executor
    with _rating_ocr_executor_lock:
        if _rating_ocr_executor is None:
            _rating_ocr_executor = concurrent.futures.ThreadPoolExecutor(
                max_workers=2,
                thread_name_prefix="rating_ocr",
            )
        return _rating_ocr_executor


def _ensure_process_dpi_aware() -> None:
    """Prefer per-monitor DPI when resolving pygetwindow / MSS coordinates."""
    global _dpi_api_initialized
    if _dpi_api_initialized:
        return
    if sys.platform != "win32":
        _dpi_api_initialized = True
        return
    try:
        import ctypes

        # PROCESS_PER_MONITOR_DPI_AWARE = 2
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass
    _dpi_api_initialized = True


def _title_matches_chiaki_strict_positive(lc_title: str) -> bool:
    return any(pat in lc_title for pat in _CHIAKI_POSITIVE_TITLE_SUBSTRINGS)


def _title_matches_chiaki_strict_negative(lc_title: str) -> bool:
    t = lc_title
    # Pad so edge tokens like "... — Edge" still match anchored substrings safely.
    t_edge = f" {t}"
    return any(pat in t or pat in t_edge for pat in _CHIAKI_EXCLUDE_TITLE_SUBSTRINGS)


def _chiaki_window_candidate_score(raw_title: str) -> int:
    """Higher is better when multiple positives match."""
    t = raw_title.lower()
    sc = 0
    if "chiaki-ng" in t.replace(" ", ""):
        sc += 20
    if "chiaki" in t:
        sc += 12
    if "remote play" in t:
        sc += 6
    return sc


def locate_chiaki_window_pygetwindow() -> tuple[object, str]:
    """Locate best pygetwindow target and its title (strict +/- title filters)."""
    _ensure_process_dpi_aware()
    try:
        import pygetwindow as gw  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "pygetwindow is required for OCR / window locate. pip install pygetwindow"
        ) from e

    candidates: list[tuple[int, object, str]] = []
    for w in gw.getAllWindows():
        raw_title = (w.title or "").strip()
        if not raw_title or w.width <= 0 or w.height <= 0:
            continue
        lc = raw_title.lower()
        if _title_matches_chiaki_strict_negative(lc):
            continue
        if not _title_matches_chiaki_strict_positive(lc):
            continue
        candidates.append((_chiaki_window_candidate_score(raw_title), w, raw_title))

    if not candidates:
        raise RuntimeError(
            "Chiaki / Remote Play window not found (or ruled out browsers/Streamlit).\n"
            "Open Chiaki-ng, ensure title contains Chiaki or Remote Play, "
            "and rename tabs so no browser title contains NBA2K Workshop."
        )

    candidates.sort(key=lambda x: -x[0])
    _score, winner, sel_title = candidates[0]

    lc_sel = sel_title.lower()
    if _title_matches_chiaki_strict_negative(lc_sel):
        raise RuntimeError(
            f"Suspect window title after strict filter violated exclusion list: {sel_title!r}"
        )

    lt, tp, ww, hh = (
        int(winner.left),
        int(winner.top),
        int(winner.width),
        int(winner.height),
    )
    print(
        "[automation] Chiaki target window:"
        f" title={sel_title!r} left={lt} top={tp} width={ww} height={hh}",
        flush=True,
    )
    return winner, sel_title


def find_chiaki_window_pygetwindow() -> object:
    """Return pygetwindow's window object that best matches Chiaki / Remote Play semantics."""
    return locate_chiaki_window_pygetwindow()[0]


def resolve_chiaki_window_rect_pygetwindow() -> tuple[int, int, int, int]:
    """Return ``(left, top, width, height)`` for the strict-matched Chiaki / Remote Play window."""
    win, _title = locate_chiaki_window_pygetwindow()
    return (int(win.left), int(win.top), int(win.width), int(win.height))


@contextlib.contextmanager
def _temporary_activate_chiaki_window(win: object) -> Generator[None, None, None]:
    """Focus-and-grab prelude: foreground Chiaki shortly, then restore previous HWND on Win32."""
    if sys.platform != "win32":
        try:
            win.activate()
        except Exception:
            pass
        time.sleep(_CHIAKI_FOCUS_ACTIVATE_SETTLE_S)
        yield
        return

    prev_hwnd = 0
    try:
        import ctypes

        prev_hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    except Exception:
        prev_hwnd = 0

    try:
        try:
            win.activate()
        except Exception:
            pass
        time.sleep(_CHIAKI_FOCUS_ACTIVATE_SETTLE_S)
        yield
    finally:
        if prev_hwnd:
            try:
                import ctypes

                ctypes.windll.user32.SetForegroundWindow(prev_hwnd)
            except Exception:
                pass


def _mss_grab_rgb_pil(region: dict[str, int]) -> "Image.Image":
    import mss  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    with mss.mss() as sct:
        shot = sct.grab(region)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def _pil_calibration_frame_suggests_wrong_shell(rgb: "Image.Image") -> bool:
    """Heuristic-only: ultra-flat pale frames often resemble Streamlit / browser dashboards."""
    try:
        import numpy as np  # noqa: PLC0415
    except ImportError:
        return False

    from PIL import Image  # noqa: PLC0415

    wdg, htg = rgb.size
    if wdg < 48 or htg < 48:
        return False
    _bilt = getattr(
        getattr(Image, "Resampling", Image),
        "BILINEAR",
        Image.BILINEAR,
    )
    small = rgb.resize(
        (
            min(280, max(160, wdg // 6)),
            min(158, max(120, htg // 6)),
        ),
        resample=_bilt,
    )
    gray = np.asarray(small.convert("L"), dtype=np.float32)
    h, wd = gray.shape
    band = max(6, wd // 12)
    left_mean = float(gray[:, :band].mean())
    mean_all = float(gray.mean())
    std_all = float(gray.std())
    return bool(mean_all > 239.5 and std_all < 14.5 and left_mean > 242.8)


def _apply_target_mismatch_watermark(rgb: "Image.Image", *, subtitle: str) -> "Image.Image":
    """Opaque banner so mis-targeted dashboards are visibly flagged."""
    from PIL import Image, ImageDraw, ImageFont

    layered = rgb.convert("RGBA")
    w, h = layered.size
    banner_h = min(116, max(56, int(h * 0.096)))
    bar = Image.new("RGBA", (w, banner_h), (205, 0, 35, 190))
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    overlay.paste(bar, (0, 0))
    draw_overlay = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("arial.ttf", max(22, banner_h // 4))
        font_small = ImageFont.truetype("arial.ttf", max(14, banner_h // 6))
    except OSError:
        font = ImageFont.load_default()
        font_small = font

    headline = "TARGET MISMATCH"
    draw_overlay.text((16, banner_h // 8), headline, fill=(255, 255, 210, 255), font=font)
    wrap = subtitle.strip()[:340]
    draw_overlay.text(
        (16, banner_h // 2),
        wrap,
        fill=(255, 255, 255, 235),
        font=font_small,
    )
    out = Image.alpha_composite(layered, overlay).convert("RGB")
    return out


def _roi_abs_from_window_rect(
    win: tuple[int, int, int, int],
    bbox_xywh: tuple[int, int, int, int],
) -> dict[str, int]:
    left, top, _w_win, _h_win = win
    dx, dy, rw, rh = bbox_xywh
    return {"left": left + dx, "top": top + dy, "width": rw, "height": rh}


def capture_rating_cell_image_pil(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> "Image.Image":
    """Capture the Detailed Grid rating cell ROI as an RGB Pillow image."""
    bbox = roi_relative_xywh or _RATING_CELL_BBOX_RELATIVE_XYWH
    win_obj, _tit = locate_chiaki_window_pygetwindow()
    rect = (int(win_obj.left), int(win_obj.top), int(win_obj.width), int(win_obj.height))
    region = _roi_abs_from_window_rect(rect, bbox)
    with _temporary_activate_chiaki_window(win_obj):
        try:
            return _mss_grab_rgb_pil(region)
        except ImportError as e:
            raise RuntimeError(
                "mss + Pillow required for OCR capture. pip install mss Pillow"
            ) from e


_CALIBRATION_ROI_OUTLINE_PX = 5


def get_ocr_calibration_preview_pair(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> tuple["Image.Image", "Image.Image"]:
    """Single Chiaki grab: full-window context with ROI rectangle + raw ROI crop (no border).

    Used by Streamlit calibration (dual preview). OCR push still uses
    :func:`capture_rating_cell_image_pil`; anchor asserts remain in
    :func:`push_prospect_row_to_controller` only.
    """
    bbox = roi_relative_xywh or _RATING_CELL_BBOX_RELATIVE_XYWH
    dx, dy, rw, rh = bbox
    win_obj, matched_title = locate_chiaki_window_pygetwindow()
    left_i = int(win_obj.left)
    top_i = int(win_obj.top)
    ww_i = int(win_obj.width)
    hh_i = int(win_obj.height)
    full_mon = {"left": left_i, "top": top_i, "width": ww_i, "height": hh_i}

    try:
        from PIL import Image as PILImageMod
        from PIL import ImageDraw
    except ImportError as e:
        raise RuntimeError("Pillow required for calibration overlay.") from e

    with _temporary_activate_chiaki_window(win_obj):
        rgb_full = _mss_grab_rgb_pil(full_mon)

    suggest_mismatch = _pil_calibration_frame_suggests_wrong_shell(rgb_full)
    x1, y1 = max(0, dx), max(0, dy)
    x2, y2 = min(ww_i - 1, dx + rw - 1), min(hh_i - 1, dy + rh - 1)
    if x2 >= x1 and y2 >= y1:
        roi_crop = rgb_full.crop((x1, y1, x2 + 1, y2 + 1)).copy()
    else:
        roi_crop = PILImageMod.new("RGB", (8, 8), (48, 48, 48))

    draw = ImageDraw.Draw(rgb_full)
    if x2 >= x1 and y2 >= y1:
        draw.rectangle(
            [x1, y1, x2, y2],
            outline=(255, 0, 0),
            width=_CALIBRATION_ROI_OUTLINE_PX,
        )

    if suggest_mismatch:
        subline = (
            "Heuristic: capture resembles a pale localhost / browser chrome layout. "
            f"Matched title: {matched_title!r}"
        )
        rgb_full = _apply_target_mismatch_watermark(rgb_full, subtitle=subline)
    return rgb_full, roi_crop


def get_ocr_calibration_context_overlay(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> "Image.Image":
    """Capture the full Chiaki client area and annotate the MSS ROI with a red frame + optional watermark."""
    ctx, _roi = get_ocr_calibration_preview_pair(
        roi_relative_xywh=roi_relative_xywh,
    )
    return ctx


def get_ocr_diagnostic_image(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> "Image.Image":
    """Return the current rating-cell crop as seen by OCR (preview for Streamlit UI)."""
    return capture_rating_cell_image_pil(roi_relative_xywh=roi_relative_xywh)


def _tesseract_rating_text(rgb: "Image.Image") -> str:
    """Run Tesseract OCR for a bounded digit-ish read."""
    try:
        import pytesseract  # noqa: PLC0415
    except ImportError as e:
        raise RuntimeError(
            "pytesseract required for OCR ratings. pip install pytesseract"
        ) from e

    gray = rgb.convert("L")
    cfg = "--psm 7 -c tessedit_char_whitelist=0123456789"
    raw = str(pytesseract.image_to_string(gray, config=cfg) or "").strip()
    return raw


def parse_rating_digits_from_text(t: str) -> int | None:
    """First two-digit rating in string in [25, 99]; else None."""
    for chunk in re.findall(r"\d{2}", re.sub(r"\s+", "", t)):
        val = int(chunk)
        if 25 <= val <= 99:
            return val
    return None


def preview_tesseract_rating_at_roi(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> tuple[str, int | None]:
    """Single MSS crop + Tesseract pass for Vision Lab QA (no PS5 anchor / nav literals)."""
    img = capture_rating_cell_image_pil(roi_relative_xywh=roi_relative_xywh)
    raw = _tesseract_rating_text(img)
    return raw, parse_rating_digits_from_text(raw)


def _read_rating_via_ocr_sync_loop(
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> int:
    """Worker: capture ROI + OCR, up to 3 attempts × 0.1s backoff."""
    last_raw = ""

    for _ in range(_READ_RATING_MAX_ATTEMPTS):
        img = capture_rating_cell_image_pil(roi_relative_xywh=roi_relative_xywh)
        try:
            raw = _tesseract_rating_text(img)
        except RuntimeError:
            raise
        last_raw = raw
        val = parse_rating_digits_from_text(raw)
        if val is not None:
            return val
        time.sleep(_READ_RATING_RETRY_DELAY_S)

    raise RuntimeError(
        f"OCR failed after {_READ_RATING_MAX_ATTEMPTS} attempts "
        f"(blur/animation?): last_tesseract={last_raw!r}"
    )


def read_rating_via_ocr_threaded_blocking(
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> int:
    """Offload capture + OCR retries to thread pool so native OCR work releases GIL."""
    ex = _get_rating_ocr_executor()
    fut = ex.submit(_read_rating_via_ocr_sync_loop, roi_relative_xywh)
    return fut.result()


def _right_stick_delta_pulse(gamepad: object, direction_up: bool) -> None:
    """One discrete step: Neutral → Deflected → Neutral with update after each change."""
    y = 1.0 if direction_up else -1.0
    gamepad.right_joystick_float(0.0, 0.0)
    gamepad.update()
    gamepad.right_joystick_float(0.0, y)
    gamepad.update()
    time.sleep(_STICK_HOLD_S)
    gamepad.right_joystick_float(0.0, 0.0)
    gamepad.update()


def _apply_rating_delta_from_ocr(
    gamepad: object,
    target_rating: int,
    *,
    roi_relative_xywh: tuple[int, int, int, int] | None = None,
) -> None:
    current = read_rating_via_ocr_threaded_blocking(roi_relative_xywh=roi_relative_xywh)
    delta = int(target_rating) - int(current)
    if delta == 0:
        return
    if abs(delta) > _MAX_RATING_DELTA_STEPS:
        raise RuntimeError(
            f"Refusing OCR delta magnitude |{delta}| over sanity cap "
            f"{_MAX_RATING_DELTA_STEPS} (likely misread OCR; current_read={current}, "
            f"target={target_rating})."
        )
    upward = delta > 0
    pulses = abs(delta)
    for _ in range(pulses):
        _right_stick_delta_pulse(gamepad, upward)


def _assert_overall_2k_anchor() -> None:
    """Fail fast if column order drift would mis-target the first rating push (Overall)."""
    cols = config.PROSPECTS_TABLE_COLUMNS
    try:
        idx = cols.index("overall_2k")
    except ValueError as e:
        raise RuntimeError(
            "Automation anchor mismatch: overall_2k not in PROSPECTS_TABLE_COLUMNS."
        ) from e
    if idx != _OVERALL_2K_ANCHOR_INDEX:
        raise RuntimeError(
            "Automation anchor mismatch: overall_2k must be mapped to index 34 "
            f"(current index {idx}). Refusing to move the stick."
        )
    if cols[_OVERALL_2K_ANCHOR_INDEX] != "overall_2k":
        raise RuntimeError(
            "Automation anchor mismatch: PROSPECTS_TABLE_COLUMNS[34] must be "
            f"overall_2k (found {cols[_OVERALL_2K_ANCHOR_INDEX]!r}). "
            "Refusing to move the stick."
        )


def _assert_typo_menu_anchors() -> None:
    """PS5 edit screen spellings must align with schema v6 indices (68 / 70, not 1-based col 71)."""
    cols = config.PROSPECTS_TABLE_COLUMNS
    if cols[_INTANGIBLES2K_NAV_INDEX] != "intangibles_2k":
        raise RuntimeError(
            "Automation anchor mismatch: index 68 must be intangibles_2k for Integnagbles."
        )
    if INDEX_TO_NAV_MAP[_INTANGIBLES2K_NAV_INDEX] != "Integnagbles":
        raise RuntimeError(
            "INDEX_TO_NAV_MAP[68] must be Integnagbles (menu spelling)."
        )
    if cols.index("durability_2k") != _DURABILITY2K_NAV_INDEX:
        raise RuntimeError(
            "Automation anchor mismatch: durability_2k must be index 70 for Durablity."
        )
    if cols[_DURABILITY2K_NAV_INDEX] != "durability_2k":
        raise RuntimeError(
            "Automation anchor mismatch: index 70 must be durability_2k."
        )
    if INDEX_TO_NAV_MAP[_DURABILITY2K_NAV_INDEX] != "Durablity":
        raise RuntimeError(
            "INDEX_TO_NAV_MAP[70] must be Durablity (menu spelling)."
        )


def _effective_rating(raw: object) -> int | None:
    """Return controller rating in [25, 99] from sheet cell (schema +1 step)."""
    if raw is None:
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    val += 1
    return max(25, min(99, val))


def _rating_to_right_stick_y(rating: int) -> float:
    """Map [25, 99] to [-1, 1]: positive Y nudges the stick Up (higher rating)."""
    lo, hi = 25, 99
    return (rating - lo) / (hi - lo) * 2.0 - 1.0


def _apply_rating_input(gamepad: object, rating: int) -> None:
    """Detailed Grid View: right analog Y drives the rating (up = increase, down = decrease)."""
    y = float(_rating_to_right_stick_y(rating))
    gamepad.right_joystick_float(0.0, y)
    gamepad.update()
    time.sleep(_STICK_HOLD_S)
    gamepad.right_joystick_float(0.0, 0.0)
    gamepad.update()


def _dpad_left_nav_step(gamepad: object) -> None:
    """One D-pad Left to move to the next attribute column in the grid."""
    import vgamepad as vg

    gamepad.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
    gamepad.update()
    time.sleep(_DPAD_NAV_HOLD_S)
    gamepad.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT)
    gamepad.update()
    time.sleep(_DPAD_NAV_SETTLE_S)


def _navigate_to_field(gamepad: object, nav_command: str) -> None:
    """Placeholder hook for future menu traversal (INDEX_TO_NAV_MAP labels)."""
    _ = (gamepad, nav_command)


def _potential_sheet_value(raw: object) -> int | None:
    """Potential meta-column value mapped to [25, 99] without the rating +1 bump."""
    if raw is None:
        return None
    try:
        val = int(round(float(raw)))
    except (TypeError, ValueError):
        return None
    return max(25, min(99, val))


def _apply_potential_meta(gamepad: object, raw: object) -> None:
    """Meta-column 88 (index 87): numeric potential uses same stick bridge."""
    rating = _potential_sheet_value(raw)
    if rating is None:
        return
    _navigate_to_field(gamepad, "Potential")
    _apply_rating_input(gamepad, rating)


def neutralize_virtual_stick(gamepad: object) -> None:
    """Return both virtual analog sticks to neutral; safe after errors or interrupts."""
    try:
        gamepad.left_joystick_float(0.0, 0.0)
        gamepad.right_joystick_float(0.0, 0.0)
        gamepad.update()
    except Exception:
        pass


def send_capture_handshake(gamepad: object) -> None:
    """Brief D-pad down pulse for Chiaki-ng controller-capture discovery.

    Independent of prospect automation: does not call
    :func:`push_prospect_row_to_controller`, does not read ``row_data``, and does not use
    ``overall_2k`` (index 34), ``Integnagbles`` (68), or ``Durablity`` (70). Only the
    virtual D-pad is toggled; right-stick rating pushes are untouched.
    """
    import vgamepad as vg

    gamepad.press_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
    gamepad.update()
    time.sleep(0.2)
    gamepad.release_button(button=vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN)
    gamepad.update()


def push_prospect_row_to_controller(
    row_data: list,
    *,
    gamepad: object,
    edit_player_mode: bool = False,
    use_ocr_feedback: bool = False,
    ocr_roi_relative_xywh: tuple[int, int, int, int] | None = None,
    on_progress: OnProgress | None = None,
) -> None:
    """Push one prospect row (87 core columns + optional potential at index 87) via vgamepad.

    ``gamepad`` must be the single UI/session ``VX360Gamepad`` bound to
    ``st.session_state['automation_gamepad']`` from the Prospects UI (singleton pattern:
    one device, no ad-hoc ``VX360Gamepad()`` here). Avoids ghost HID / duplicate
    virtual controllers on Windows.

    When ``use_ocr_feedback`` is True, rating pushes use MSS + OCR for the rating cell,
    delta-nudging the right stick in ±1 pulses; otherwise the legacy calibrated
    single-stick deflection applies.

    Optional ``ocr_roi_relative_xywh`` overrides the MSS crop bbox (dx, dy, w, h) from
    the Chiaki window top-left—same tuple as Automation Settings diagnostics.
    """
    _assert_overall_2k_anchor()
    _assert_typo_menu_anchors()
    gp = gamepad

    def _post_column_stability(col_idx: int) -> None:
        if (col_idx + 1) % _BUFFER_FLUSH_INTERVAL_COLS == 0:
            gp.update()
            time.sleep(_BUFFER_FLUSH_PAUSE_S)
        time.sleep(_STABILITY_INPUT_DELAY_S)

    try:
        for idx in range(87):
            label = INDEX_TO_NAV_MAP[idx]
            raw = row_data[idx] if idx < len(row_data) else None
            frac = (idx + 1) / 87.0

            if idx < 34 and not edit_player_mode:
                if on_progress:
                    on_progress(frac, f"Skipping {label}…")
                _post_column_stability(idx)
                continue

            if idx in _RATING_INDICES:
                rating = _effective_rating(raw)
                if rating is None:
                    if on_progress:
                        on_progress(frac, f"Skipping {label} (no value)…")
                    _post_column_stability(idx)
                    continue
                if on_progress:
                    preview = (
                        f"Pushing {label} via OCR Δ…"
                        if use_ocr_feedback
                        else f"Pushing {label}…"
                    )
                    on_progress(frac, preview)
                _navigate_to_field(gp, label)
                if idx == _OVERALL_2K_ANCHOR_INDEX:
                    if config.PROSPECTS_TABLE_COLUMNS[idx] != "overall_2k":
                        raise RuntimeError(
                            "Automation: overall_2k must be at loop index 34; refusing to push."
                        )
                    # Calibration pause before first right-stick movement at Overall.
                    time.sleep(_OVERALL_FIELD_SETTLE_S)
                    time.sleep(_OVERALL_ANCHOR_EXTRA_SETTLE_S)
                if use_ocr_feedback:
                    _apply_rating_delta_from_ocr(
                        gp, rating, roi_relative_xywh=ocr_roi_relative_xywh
                    )
                else:
                    _apply_rating_input(gp, rating)
                _dpad_left_nav_step(gp)
            else:
                if on_progress:
                    on_progress(frac, f"Skipping {label}…")

            _post_column_stability(idx)

        if len(row_data) > 87:
            pot_raw = row_data[87]
            if _potential_sheet_value(pot_raw) is not None:
                if on_progress:
                    on_progress(1.0, "Pushing Potential…")
                _apply_potential_meta(gp, pot_raw)
                time.sleep(_STABILITY_INPUT_DELAY_S)
            elif on_progress:
                on_progress(1.0, "Skipping Potential (no numeric value)…")
        elif on_progress:
            on_progress(1.0, "Done.")
    finally:
        neutralize_virtual_stick(gp)
