from __future__ import annotations

import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"


def is_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/summary", timeout=1) as response:
            return response.status == 200
    except OSError:
        return False


if not is_ready():
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, str(ROOT / "app.py"), "--port", "8765"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )
    for _ in range(30):
        if is_ready():
            break
        time.sleep(0.2)

if not is_ready():
    raise SystemExit("PulseGuard service failed to start.")

webbrowser.open(URL)

