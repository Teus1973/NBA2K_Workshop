"""
Vision Lab — Chiaki OCR ROI calibration (responsive split-pane workstation layout).

Push-to-PS5 anchor checks (:func:`~src.automation.controller_mapping.push_prospect_row_to_controller`)
are unchanged; this tab does not alter ``overall_2k`` index 34 or menu literals **Integnagbles** /
**Durablity**.
"""

from __future__ import annotations

import io
import time

import streamlit as st

_VISION_LAB_CAPTURE_INTERVAL_S = 0.2

# Coordinate Jump presets (pixels relative to Chiaki window top-left).
ROI_JUMP_1080P_GRID_START_XYWH: tuple[int, int, int, int] = (1660, 1035, 60, 40)
# Wide upper band to sanity-check header copy such as "Draft Class" (layout-dependent).
ROI_JUMP_HEADER_CHECK_XYWH: tuple[int, int, int, int] = (400, 28, 720, 96)

try:
    from PIL import Image as PILImage

    PILImageType = PILImage.Image
except ImportError:  # pragma: no cover
    PILImageType = object  # type: ignore[misc, assignment]

from ..automation.controller_mapping import (
    DEFAULT_OCR_ROI_RELATIVE_XYWH,
    get_ocr_calibration_preview_pair,
    preview_tesseract_rating_at_roi,
)

_SESSION_PREVIEW_CONTEXT_PNG_KEY = "automation_last_context_png"
_SESSION_PREVIEW_ZOOM_PNG_KEY = "automation_last_zoom_png"
_SESSION_PREVIEW_PNG_LEGACY = "automation_last_crop_png"
_SESSION_PREVIEW_LEGACY_KEY = "automation_last_crop"

_ROI_NUDGE_SPEED_PX: dict[str, int] = {
    "Fine (1px)": 1,
    "Standard (10px)": 10,
    "Coarse (50px)": 50,
}

ROI_AXIS_KEYS: dict[str, str] = {
    "x": "automation_ocr_roi_x",
    "y": "automation_ocr_roi_y",
    "w": "automation_ocr_roi_w",
    "h": "automation_ocr_roi_h",
}


def _mark_capture_stamp() -> None:
    st.session_state["_vision_lab_capture_mono"] = time.monotonic()


def invalidate_automation_ocr_crop_preview() -> None:
    """Drop cached PNG preview (Chiaki stale or OCR turned off after editing numbers)."""
    st.session_state.pop(_SESSION_PREVIEW_CONTEXT_PNG_KEY, None)
    st.session_state.pop(_SESSION_PREVIEW_ZOOM_PNG_KEY, None)
    st.session_state.pop(_SESSION_PREVIEW_PNG_LEGACY, None)
    st.session_state.pop(_SESSION_PREVIEW_LEGACY_KEY, None)
    st.session_state.pop("_vision_lab_capture_mono", None)


def _ensure_automation_roi_session_defaults() -> None:
    """Seed ROI widget keys once so ``number_input`` can omit ``value=`` (key-bound only)."""
    d = DEFAULT_OCR_ROI_RELATIVE_XYWH
    st.session_state.setdefault("automation_ocr_roi_x", d[0])
    st.session_state.setdefault("automation_ocr_roi_y", d[1])
    st.session_state.setdefault("automation_ocr_roi_w", d[2])
    st.session_state.setdefault("automation_ocr_roi_h", d[3])


def automation_ocr_roi_tuple() -> tuple[int, int, int, int]:
    """Session-backed ROI bbox (aligned with OCR preview + OCR push overrides)."""
    d = DEFAULT_OCR_ROI_RELATIVE_XYWH
    return (
        int(st.session_state.get("automation_ocr_roi_x", d[0])),
        int(st.session_state.get("automation_ocr_roi_y", d[1])),
        int(st.session_state.get("automation_ocr_roi_w", d[2])),
        int(st.session_state.get("automation_ocr_roi_h", d[3])),
    )


def _pil_to_png_bytes_rgb(img: "PILImageType") -> bytes:
    buf = io.BytesIO()
    if getattr(img, "mode", "") != "RGB":
        img = img.convert("RGB")
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _pil_zoom_crosshair_200pct_nn(roi: "PILImageType") -> "PILImageType":
    """200% nearest-neighbor upscale of OCR ROI plus center crosshair."""
    try:
        from PIL import ImageDraw
    except ImportError:
        return roi
    w, h = roi.size
    if min(w, h) <= 0:
        return roi
    resample = getattr(
        getattr(PILImage, "Resampling", PILImage),
        "NEAREST",
        0,
    )
    z = roi.resize((w * 2, h * 2), resample=resample)
    zw, zh = z.size
    cx, cy = zw // 2, zh // 2
    draw = ImageDraw.Draw(z)
    shadow = (0, 0, 0)
    hi = (0, 255, 255)
    for ox, oy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        draw.line([(0, cy + oy), (zw - 1, cy + oy)], fill=shadow, width=1)
        draw.line([(cx + ox, 0), (cx + ox, zh - 1)], fill=shadow, width=1)
    draw.line([(0, cy), (zw - 1, cy)], fill=hi, width=1)
    draw.line([(cx, 0), (cx, zh - 1)], fill=hi, width=1)
    return z


def _store_roi_dual_preview(context_img: "PILImageType", roi_crop_img: "PILImageType") -> None:
    st.session_state[_SESSION_PREVIEW_CONTEXT_PNG_KEY] = _pil_to_png_bytes_rgb(context_img)
    st.session_state[_SESSION_PREVIEW_ZOOM_PNG_KEY] = _pil_to_png_bytes_rgb(
        _pil_zoom_crosshair_200pct_nn(roi_crop_img),
    )
    st.session_state.pop(_SESSION_PREVIEW_LEGACY_KEY, None)
    st.session_state.pop(_SESSION_PREVIEW_PNG_LEGACY, None)
    st.session_state.pop("_automation_roi_capture_last_error", None)


def _roi_capture_to_session_best_effort() -> bool:
    try:
        roi_bbox = automation_ocr_roi_tuple()
        ctx, roi_crop = get_ocr_calibration_preview_pair(roi_relative_xywh=roi_bbox)
        _store_roi_dual_preview(ctx, roi_crop)
        return True
    except RuntimeError as e:
        st.session_state.pop(_SESSION_PREVIEW_CONTEXT_PNG_KEY, None)
        st.session_state.pop(_SESSION_PREVIEW_ZOOM_PNG_KEY, None)
        st.session_state["_automation_roi_capture_last_error"] = str(e)
        return False
    except ImportError as e:
        st.session_state.pop(_SESSION_PREVIEW_CONTEXT_PNG_KEY, None)
        st.session_state.pop(_SESSION_PREVIEW_ZOOM_PNG_KEY, None)
        st.session_state["_automation_roi_capture_last_error"] = str(e)
        return False


def _on_roi_number_input_commit() -> None:
    if st.session_state.get("automation_use_ocr_feedback"):
        _roi_capture_to_session_best_effort()
        _mark_capture_stamp()
    else:
        invalidate_automation_ocr_crop_preview()


def _on_automation_ocr_feedback_changed() -> None:
    if st.session_state.get("automation_use_ocr_feedback"):
        _roi_capture_to_session_best_effort()
        _mark_capture_stamp()
    else:
        invalidate_automation_ocr_crop_preview()
        st.session_state.pop("_automation_roi_capture_last_error", None)


def _clamp_roi_axis(axis: str, raw: int) -> int:
    if axis == "x":
        return max(-5000, min(10_000, raw))
    if axis == "y":
        return max(-5000, min(10_000, raw))
    if axis in ("w", "h"):
        return max(8, min(8192, raw))
    raise ValueError(f"Unknown ROI axis {axis!r}")


def _apply_roi_preset(xywh: tuple[int, int, int, int]) -> None:
    """Snap ROI session keys to a preset box; refresh capture when OCR is live."""
    x, y, w, h = xywh
    st.session_state["automation_ocr_roi_x"] = _clamp_roi_axis("x", x)
    st.session_state["automation_ocr_roi_y"] = _clamp_roi_axis("y", y)
    st.session_state["automation_ocr_roi_w"] = _clamp_roi_axis("w", w)
    st.session_state["automation_ocr_roi_h"] = _clamp_roi_axis("h", h)
    if st.session_state.get("automation_use_ocr_feedback"):
        _roi_capture_to_session_best_effort()
        _mark_capture_stamp()


def nudge_roi(axis: str, delta: int) -> None:
    """Apply ROI delta and refresh MSS previews when OCR feedback is enabled."""
    ax = axis.lower().strip()
    if ax not in ROI_AXIS_KEYS:
        raise ValueError(f"axis must be one of {sorted(ROI_AXIS_KEYS)}; got {axis!r}")
    d = DEFAULT_OCR_ROI_RELATIVE_XYWH
    defaults_xywh = {"x": d[0], "y": d[1], "w": d[2], "h": d[3]}
    key = ROI_AXIS_KEYS[ax]
    current = int(st.session_state.get(key, defaults_xywh[ax]))
    st.session_state[key] = _clamp_roi_axis(ax, current + delta)
    if not st.session_state.get("automation_use_ocr_feedback"):
        return
    _roi_capture_to_session_best_effort()
    _mark_capture_stamp()


def render() -> None:
    _ensure_automation_roi_session_defaults()

    st.header("Vision Lab")
    st.caption(
        "Three-column workstation (**Context → Zoom / OCR → Controls**). Enables delta-nudging "
        "during **Prospects → Push to PS5** when OCR feedback is on. Virtual controller setup "
        "stays under **Prospects → Automation Settings** (sidebar)."
    )

    toast = st.session_state.pop("_vision_lab_toast", None)
    if toast:
        st.success(toast)

    cap_err = st.session_state.pop("_vision_lab_capture_error", None)
    if cap_err:
        st.error(cap_err)

    pers_err = st.session_state.get("_automation_roi_capture_last_error")
    if pers_err:
        st.warning(pers_err)

    col_ctx, col_zoom, col_ctrl = st.columns([2, 1, 1])

    with col_ctx:
        st.markdown("##### Context View")
        ctx_slot = st.empty()

    with col_zoom:
        st.markdown("##### Zoomed ROI")
        zoom_slot = st.empty()
        st.markdown("##### OCR read")
        ocr_highlight_slot = st.empty()

    with col_ctrl:
        st.markdown("##### Calibration")
        st.toggle(
            "OCR rating feedback (Chiaki capture + Tesseract)",
            value=False,
            key="automation_use_ocr_feedback",
            on_change=_on_automation_ocr_feedback_changed,
            help=(
                "**Turn on for live previews (~5 FPS)** and for OCR delta-nudging during PS5 "
                "pushes. ROI is stored for **Prospects → Push to PS5**."
            ),
        )

        st.markdown("###### Coordinate Jump")
        st.caption("Snap ROI without clicking the image (Chiaki-relative pixels).")
        j1, j2, j3 = st.columns(3)
        with j1:
            if st.button(
                "1080p Grid Start",
                key="vision_lab_jump_1080p",
                help="Preset (745, 490, 60, 40) — 1080p grid rating cell start.",
            ):
                _apply_roi_preset(ROI_JUMP_1080P_GRID_START_XYWH)
        with j2:
            if st.button(
                "Header Check",
                key="vision_lab_jump_header",
                help="Upper title band (400, 28, 720, 96) to spot Draft Class / header text.",
            ):
                _apply_roi_preset(ROI_JUMP_HEADER_CHECK_XYWH)
        with j3:
            if st.button(
                "Reset to Default",
                key="vision_lab_jump_default",
                help=f"Restore built-in default {DEFAULT_OCR_ROI_RELATIVE_XYWH}.",
            ):
                _apply_roi_preset(DEFAULT_OCR_ROI_RELATIVE_XYWH)

        _ocr_on = bool(st.session_state.get("automation_use_ocr_feedback"))

        st.radio(
            "Nudge speed",
            options=list(_ROI_NUDGE_SPEED_PX.keys()),
            horizontal=True,
            index=1,
            key="automation_roi_nudge_speed_choice",
            help="XY D-pad step: Coarse to jump, Fine to pixel-align digits.",
        )
        pos_step_val = _ROI_NUDGE_SPEED_PX.get(
            str(st.session_state.get("automation_roi_nudge_speed_choice", "Standard (10px)")),
            10,
        )
        scale_step_px = 2

        st.caption("D-pad moves the ROI rectangle relative to Chiaki top-left.")
        pad, ctr, _ = st.columns([1, 2, 1])
        with ctr:
            if st.button("↑", key="vision_lab_roi_nudge_up", disabled=not _ocr_on):
                nudge_roi("y", -pos_step_val)

        row2 = st.columns(3)
        with row2[0]:
            if st.button("←", key="vision_lab_roi_nudge_left", disabled=not _ocr_on):
                nudge_roi("x", -pos_step_val)
        with row2[1]:
            if st.button(
                "Capture / refresh",
                key="vision_lab_roi_capture_refresh",
                disabled=not _ocr_on,
                help="Fresh MSS grab using current ROI (same path as PS5 OCR reads).",
            ):
                if _roi_capture_to_session_best_effort():
                    st.session_state["_vision_lab_toast"] = "Crop refreshed."
                    _mark_capture_stamp()
        with row2[2]:
            if st.button("→", key="vision_lab_roi_nudge_right", disabled=not _ocr_on):
                nudge_roi("x", pos_step_val)

        pad_b, ctr_b, _ = st.columns([1, 2, 1])
        with ctr_b:
            if st.button("↓", key="vision_lab_roi_nudge_down", disabled=not _ocr_on):
                nudge_roi("y", pos_step_val)

        st.caption(f"Resize ROI ±{scale_step_px}px per click.")
        row_scale = st.columns(4)
        with row_scale[0]:
            if st.button("Width +", key="vision_lab_roi_w_plus", disabled=not _ocr_on):
                nudge_roi("w", scale_step_px)
        with row_scale[1]:
            if st.button("Width −", key="vision_lab_roi_w_minus", disabled=not _ocr_on):
                nudge_roi("w", -scale_step_px)
        with row_scale[2]:
            if st.button("Height +", key="vision_lab_roi_h_plus", disabled=not _ocr_on):
                nudge_roi("h", scale_step_px)
        with row_scale[3]:
            if st.button("Height −", key="vision_lab_roi_h_minus", disabled=not _ocr_on):
                nudge_roi("h", -scale_step_px)

        if st.button(
            "Test OCR Read",
            key="vision_lab_test_ocr_read",
            disabled=not _ocr_on,
            help="One-shot Tesseract read from the current ROI (rating digits).",
        ):
            try:
                raw, parsed = preview_tesseract_rating_at_roi(
                    roi_relative_xywh=automation_ocr_roi_tuple(),
                )
                st.session_state["_vision_lab_ocr_last_raw"] = raw
                st.session_state["_vision_lab_ocr_last_err"] = None
                if parsed is not None:
                    st.session_state["_vision_lab_ocr_last_parsed"] = parsed
                else:
                    st.session_state.pop("_vision_lab_ocr_last_parsed", None)
            except RuntimeError as e:
                st.session_state["_vision_lab_ocr_last_err"] = str(e)
                st.session_state.pop("_vision_lab_ocr_last_parsed", None)
            except ImportError as e:
                st.session_state["_vision_lab_ocr_last_err"] = f"Missing dependency: {e}"
                st.session_state.pop("_vision_lab_ocr_last_parsed", None)

        st.divider()
        st.markdown("###### ROI overrides (pixels)")
        st.number_input(
            "ROI X",
            min_value=-5000,
            max_value=10000,
            step=1,
            key="automation_ocr_roi_x",
            on_change=_on_roi_number_input_commit,
        )
        st.number_input(
            "ROI Y",
            min_value=-5000,
            max_value=10000,
            step=1,
            key="automation_ocr_roi_y",
            on_change=_on_roi_number_input_commit,
        )
        st.number_input(
            "ROI width",
            min_value=8,
            max_value=8192,
            step=1,
            key="automation_ocr_roi_w",
            on_change=_on_roi_number_input_commit,
        )
        st.number_input(
            "ROI height",
            min_value=8,
            max_value=8192,
            step=1,
            key="automation_ocr_roi_h",
            on_change=_on_roi_number_input_commit,
        )

    _ocr_live = bool(st.session_state.get("automation_use_ocr_feedback"))
    if _ocr_live:
        now = time.monotonic()
        last = float(st.session_state.get("_vision_lab_capture_mono", 0.0))
        if now - last >= _VISION_LAB_CAPTURE_INTERVAL_S:
            _roi_capture_to_session_best_effort()
            _mark_capture_stamp()

    ctx_png = st.session_state.get(_SESSION_PREVIEW_CONTEXT_PNG_KEY)
    zoom_png = st.session_state.get(_SESSION_PREVIEW_ZOOM_PNG_KEY)

    if _ocr_live and ctx_png:
        ctx_slot.image(
            ctx_png,
            caption="Full Chiaki window — red ROI (5px); scaled to column width.",
            use_container_width=True,
        )
    elif _ocr_live:
        ctx_slot.info("Waiting for Chiaki capture…")
    else:
        ctx_slot.caption("Turn **OCR rating feedback** on to stream context view.")

    if _ocr_live and zoom_png:
        zoom_slot.image(
            zoom_png,
            caption="ROI crop — 200% nearest-neighbor + cyan crosshair (~5 FPS live).",
            use_container_width=True,
        )
    elif _ocr_live:
        zoom_slot.caption("Zoom preview pending capture.")
    else:
        zoom_slot.caption("Enable OCR feedback for zoomed ROI.")

    with ocr_highlight_slot.container():
        err_o = st.session_state.get("_vision_lab_ocr_last_err")
        parsed_o = st.session_state.get("_vision_lab_ocr_last_parsed")
        raw_o = st.session_state.get("_vision_lab_ocr_last_raw")
        if err_o:
            st.error(err_o)
            st.caption("Fix Chiaki ROI or dependencies, then run **Test OCR Read** again.")
        elif parsed_o is not None:
            st.caption("Parsed rating (Tesseract)")
            st.header(str(parsed_o))
            st.caption(f"Raw engine output: `{raw_o!r}`")
        elif raw_o is not None:
            st.metric(label="Tesseract rating", value="Unparsed", delta="Check raw output")
            st.caption(f"Raw output: `{raw_o!r}`")
        else:
            st.caption("Run **Test OCR Read** (controls column) to verify digits.")
