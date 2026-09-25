from __future__ import annotations

import subprocess
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

for name in ["ui.pid", "api.pid"]:
    pid_file = BASE_DIR / "data" / name

    if not pid_file.exists():
        continue

    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        pass

    try:
        pid_file.unlink()
    except Exception:
        pass
