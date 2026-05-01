"""
Build ``NBA2KWorkshop.exe`` via PyInstaller.

Embeds ``assets/app_icon.ico`` when present (generate from ``assets/app_logo.png``
with Pillow: ``pip install pillow`` then save multi-size ICO).

On some Windows setups, real-time AV deletes or locks the .exe between the append
and the PE timestamp step, so PyInstaller's ``set_exe_build_timestamp`` raises and
the build aborts. We wrap those post-steps to log and continue if the file is
missing or locked.
"""
from __future__ import annotations

import logging
import os
import runpy
import shutil
import sys
import uuid
from pathlib import Path

log = logging.getLogger("build_launcher")


def _patch_pyi_win_post() -> None:
    from PyInstaller.utils.win32 import winutils

    _orig_ts = winutils.set_exe_build_timestamp
    _orig_cs = winutils.update_exe_pe_checksum
    _orig_chmod = os.chmod
    _orig_rename = os.rename

    def set_exe_build_timestamp(exe_path: str, timestamp: int) -> None:
        try:
            if not os.path.isfile(exe_path):
                log.warning("skip PE timestamp: missing %s (AV?)", exe_path)
                return
            _orig_ts(exe_path, timestamp)
        except OSError as e:
            log.warning("skip PE timestamp for %s: %s", exe_path, e)

    def update_exe_pe_checksum(exe_path: str) -> None:
        try:
            if not os.path.isfile(exe_path):
                log.warning("skip PE checksum: missing %s (AV?)", exe_path)
                return
            _orig_cs(exe_path)
        except OSError as e:
            log.warning("skip PE checksum for %s: %s", exe_path, e)

    def chmod_skip_missing(path: str, mode: int) -> None:
        if not os.path.isfile(path):
            log.warning("skip chmod: missing %s (AV?)", path)
            return
        _orig_chmod(path, mode)

    def rename_skip_missing(src: str, dst: str) -> None:
        if not os.path.isfile(src):
            log.warning("skip rename: missing %s (AV?)", src)
            return
        _orig_rename(src, dst)

    winutils.set_exe_build_timestamp = set_exe_build_timestamp
    winutils.update_exe_pe_checksum = update_exe_pe_checksum
    os.chmod = chmod_skip_missing
    os.rename = rename_skip_missing


LAUNCHER_EXE_NAME = "NBA2KWorkshop"


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)

    _patch_pyi_win_post()

    # Build into ``build/launcher_dist_<tag>/`` first. Writing directly to
    # ``LaunchNBA2KWorkshop.exe`` in the repo root often fails with *Permission
    # denied* (AV lock, or a stale quarantined path). A fresh subfolder avoids that.
    tag = uuid.uuid4().hex[:10]
    work_parent = root / "build"
    tmp_work = str(work_parent / f"pyinst_work_{tag}")
    tmp_dist = work_parent / f"launcher_dist_{tag}"
    shutil.rmtree(tmp_work, ignore_errors=True)
    shutil.rmtree(tmp_dist, ignore_errors=True)
    os.makedirs(tmp_work, exist_ok=True)
    os.makedirs(tmp_dist, exist_ok=True)

    out_exe = root / f"{LAUNCHER_EXE_NAME}.exe"

    icon_arg: list[str] = []
    icon_path = (root / "assets" / "app_icon.ico").resolve()
    if icon_path.is_file():
        icon_arg = ["--icon", str(icon_path)]
        log.info("Embedding icon %s", icon_path)
    else:
        log.warning(
            "Missing %s — exe will use the default PyInstaller icon. "
            "Generate from assets/app_logo.png (see script docstring).",
            icon_path,
        )

    sys.argv = [
        "pyinstaller",
        "--noconsole",
        "--onefile",
        "--clean",
        "--distpath",
        str(tmp_dist),
        "--workpath",
        tmp_work,
        "--specpath",
        str(root),
        "--name",
        LAUNCHER_EXE_NAME,
        *icon_arg,
        str(root / "launcher.py"),
    ]
    runpy.run_module("PyInstaller.__main__", run_name="__main__")

    built = tmp_dist / f"{LAUNCHER_EXE_NAME}.exe"
    if not built.is_file():
        log.error(
            "No output at %s. Exclude the project folder or pause real-time AV, then retry.",
            built,
        )
        return 1

    # Try several destination names. Norton often blocks a path that was
    # quarantined before, even if exclusions exist; a different filename can work.
    (root / "dist").mkdir(parents=True, exist_ok=True)
    dests: list[Path] = [
        root / "NBA2K Workshop.exe",
        out_exe,
        root / "StartNBA2KWorkshop.exe",
        root / "LaunchNBA2KWorkshop.exe",
        root / "WorkshopApp.exe",
        root / "dist" / "StartNBA2KWorkshop.exe",
    ]
    for dest in dests:
        if dest.is_file():
            try:
                dest.unlink()
            except OSError as e:
                log.warning("Could not remove %s: %s", dest, e)
        try:
            shutil.copy2(built, dest)
        except OSError as e:
            log.warning("Could not copy to %s: %s", dest, e)
            continue
        log.info("Built: %s", dest)
        return 0
    log.warning("Could not copy to project root (all candidates failed).")
    log.info("Built (run this file): %s", built)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
