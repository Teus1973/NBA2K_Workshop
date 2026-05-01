# -*- mode: python ; coding: utf-8 -*-
# Run from repo root: pyinstaller LaunchNBA2KWorkshop.spec
# Or: python scripts/build_workshop_launcher.py

import os

_spec_dir = os.path.dirname(os.path.abspath(SPEC))
_launcher = os.path.join(_spec_dir, "launcher.py")
_icon_path = os.path.join(_spec_dir, "assets", "app_icon.ico")
_icon_kw = {"icon": _icon_path} if os.path.isfile(_icon_path) else {}

a = Analysis(
    [_launcher],
    pathex=[_spec_dir],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NBA2KWorkshop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    **_icon_kw,
)
