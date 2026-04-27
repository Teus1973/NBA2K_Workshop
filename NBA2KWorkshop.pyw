"""
Double-click on Windows: runs the workshop launcher with pythonw (no console).

If .pyw is not associated with Python, use LaunchNBA2KWorkshop.vbs instead.
"""
from __future__ import annotations

import os
import subprocess
import sys

def main() -> None:
    root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root)
    launcher = os.path.join(root, "launcher.py")
    subprocess.Popen(
        [sys.executable, launcher],
        cwd=root,
        close_fds=True,
    )


if __name__ == "__main__":
    main()
