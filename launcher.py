"""
NBA2K26 Workshop launcher — mirrors the SubtitleForge pattern.

Behaviour
---------
1. Resolve the project root (works both as .py and, if you ever freeze it,
   as a PyInstaller .exe).
2. Check whether port 8506 is LISTENING. If it is, probe the HTTP endpoint.
   - Healthy  -> open browser, exit (no double-launch).
   - Stale    -> kill ONLY the PID(s) bound to :8506, wait, fall through.
3. Launch Streamlit via the bundled venv python, fully hidden (no CMD window).
4. Poll until the server is up, then open the default browser.

Env vars
--------
NBA2K_WORKSHOP_PORT            default 8506
NBA2K_WORKSHOP_LAUNCH_TIMEOUT  seconds to wait for first HTTP 200 (default 60)
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


def _resolve_project_dir() -> Path:
    """When frozen, find the workshop root (``app.py`` + ``venv``), not only the .exe folder.

    Supports **onefile** (exe next to ``app.py``) and **onedir** (exe in a subfolder like
    ``LaunchNBA2KWorkshop/LaunchNBA2KWorkshop.exe``).
    """
    if getattr(sys, "frozen", False):
        here = Path(sys.executable).resolve().parent
        for cand in (here, here.parent, here.parent.parent):
            app = cand / "app.py"
            vpy = cand / "venv" / "Scripts" / "python.exe"
            if app.is_file() and vpy.is_file():
                return cand
        return here
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project_dir()
VENV_PYTHON = PROJECT_DIR / "venv" / "Scripts" / "python.exe"
APP_PY = PROJECT_DIR / "app.py"
LOG_PATH = PROJECT_DIR / "streamlit_launcher.log"

PORT = int(os.environ.get("NBA2K_WORKSHOP_PORT", "8506"))
URL = f"http://localhost:{PORT}"

_STREAMLIT_LOG_FP = None


def _launch_wait_seconds() -> int:
    raw = os.environ.get("NBA2K_WORKSHOP_LAUNCH_TIMEOUT", "").strip()
    if raw.isdigit():
        return max(15, min(int(raw), 3600))
    return 60


def _port_listening() -> bool:
    """True if anything is LISTENING on PORT (v4 or v6)."""
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                if s.connect_ex((host, PORT)) == 0:
                    return True
        except OSError:
            pass
    return False


def _server_healthy() -> bool:
    """True if the Streamlit HTTP endpoint responds 200."""
    try:
        timeout = 30.0 if _port_listening() else 3.0
        with urllib.request.urlopen(URL, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _pids_on_port() -> list[int]:
    """Return LISTENING PIDs on PORT using netstat — no WMI dependency."""
    pids: list[int] = []
    try:
        out = subprocess.check_output(
            ["netstat", "-aon"],
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 5:
                continue
            local_endpoint = parts[1]
            state = parts[3]
            try:
                local_port = int(local_endpoint.rsplit(":", 1)[1])
            except (IndexError, ValueError):
                continue
            if local_port == PORT and state == "LISTENING":
                try:
                    pid = int(parts[4])
                    if pid not in pids:
                        pids.append(pid)
                except ValueError:
                    pass
    except Exception:
        pass
    return pids


def _kill_pids(pids: list[int]) -> None:
    """Force-kill only the supplied PIDs — never anything else."""
    for pid in pids:
        try:
            subprocess.call(
                ["taskkill", "/F", "/PID", str(pid)],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass


def _python_exe() -> str:
    """Prefer the project venv; fall back to the current interpreter."""
    if VENV_PYTHON.is_file():
        return str(VENV_PYTHON)
    return sys.executable


def _launch_streamlit() -> None:
    """Start Streamlit in the background, fully hidden (no CMD window)."""
    if not APP_PY.is_file():
        _show_error(
            f"app.py not found at:\n{APP_PY}\n\n"
            "Re-clone / re-download the project."
        )
        sys.exit(2)

    if not VENV_PYTHON.is_file():
        _show_error(
            f"Virtual environment not found:\n{VENV_PYTHON}\n\n"
            "Run LaunchNBA2KWorkshop.bat once to set up the environment."
        )
        sys.exit(1)

    cmd = [
        _python_exe(),
        "-m",
        "streamlit",
        "run",
        str(APP_PY),
        "--server.port",
        str(PORT),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]

    global _STREAMLIT_LOG_FP
    _STREAMLIT_LOG_FP = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    _STREAMLIT_LOG_FP.write(
        f"\n--- Streamlit launch {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"(port {PORT}) ---\n"
    )
    _STREAMLIT_LOG_FP.flush()

    creation = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )

    subprocess.Popen(
        cmd,
        cwd=str(PROJECT_DIR),
        creationflags=creation,
        stdout=_STREAMLIT_LOG_FP,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
    )


def _wait_for_server(timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _server_healthy():
            return True
        time.sleep(1)
    return False


def _show_error(msg: str, *, icon: int = 0x10) -> None:
    """Pop a native Windows MessageBox (MB_ICONERROR=0x10, MB_ICONWARNING=0x30)."""
    try:
        ctypes.windll.user32.MessageBoxW(0, msg, "NBA2K26 Workshop", icon)
    except Exception:
        print(msg, file=sys.stderr)


def main() -> int:
    if _port_listening():
        if _server_healthy():
            webbrowser.open(URL)
            return 0
        _kill_pids(_pids_on_port())
        time.sleep(2)

    _launch_streamlit()

    timeout = _launch_wait_seconds()
    if _wait_for_server(timeout):
        webbrowser.open(URL)
        return 0

    _show_error(
        f"The server did not respond within {timeout} seconds.\n\n"
        f"• Check streamlit_launcher.log in the app folder for the real error.\n"
        f"• Try {URL} in your browser — it may have started late.\n"
        f"• Run TroubleshootNBA2KWorkshop.bat to see live startup errors.\n\n"
        f"To wait longer next time, set env var:\n"
        f"  NBA2K_WORKSHOP_LAUNCH_TIMEOUT=180\n\n"
        f"Venv: {VENV_PYTHON}",
        icon=0x30,
    )
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
