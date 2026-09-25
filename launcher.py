from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Same ports as V11.7 so a compatible completed V11.7 frontend build can be
# reused immediately. START.bat replaces an old V11.7 process on these ports.
UI_PORT = 5285
API_PORT = 5286

UI_URL = f"http://127.0.0.1:{UI_PORT}/#overview"
API_VERSION_URL = f"http://127.0.0.1:{API_PORT}/version"

API_PID = BASE_DIR / "data" / "api.pid"
UI_PID = BASE_DIR / "data" / "ui.pid"


def start_github_auto_update():
    updater = BASE_DIR / "github_auto_update.py"
    if not updater.exists() or not (BASE_DIR / ".git").exists():
        return
    try:
        subprocess.Popen(
            [sys.executable, str(updater)],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=hidden_flags(),
            close_fds=True,
        )
    except Exception:
        pass


def state_runtime_json():
    local = os.environ.get("LOCALAPPDATA")

    if local:
        return (
            Path(local)
            / "NunesRecruitmentConsole"
            / "FastRuntime"
            / "runtime.json"
        )

    return (
        BASE_DIR
        / "data"
        / "persistent"
        / "FastRuntime"
        / "runtime.json"
    )


def runtime_info():
    try:
        value = json.loads(
            state_runtime_json().read_text(
                encoding="utf-8"
            )
        )
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def port_open(port):
    sock = socket.socket()
    sock.settimeout(0.22)

    try:
        sock.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            sock.close()
        except Exception:
            pass


def api_is_v11_11_5():
    if not port_open(API_PORT):
        return False

    try:
        with urllib.request.urlopen(
            API_VERSION_URL,
            timeout=0.8,
        ) as response:
            data = json.loads(
                response.read().decode(
                    "utf-8",
                    "replace",
                )
            )

        return (
            data.get("version") == "V11.11.5"
            and int(data.get("port", 0)) == API_PORT
        )
    except Exception:
        return False


def find_ui_browser():
    candidates = [
        os.path.expandvars(
            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
        ),
        os.path.expandvars(
            r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
        ),
    ]

    for value in candidates:
        if value and Path(value).exists():
            return value

    return None


def open_ui():
    browser = find_ui_browser()

    if browser:
        subprocess.Popen(
            [browser, UI_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    else:
        webbrowser.open(UI_URL)


def hidden_flags():
    if sys.platform != "win32":
        return 0

    return (
        getattr(
            subprocess,
            "CREATE_NO_WINDOW",
            0,
        )
        | getattr(
            subprocess,
            "DETACHED_PROCESS",
            0,
        )
    )


def runtime_python(info):
    exe = Path(info.get("python_exe") or "")

    if not exe.exists():
        return None

    pythonw = exe.parent / "pythonw.exe"

    if pythonw.exists():
        return pythonw

    return exe


def spawn_api(info):
    if api_is_v11_11_5():
        return None

    if port_open(API_PORT):
        return False

    exe = runtime_python(info)

    if not exe:
        return False

    log_path = BASE_DIR / "data" / "api.log"
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log = open(
        log_path,
        "a",
        encoding="utf-8",
        buffering=1,
    )

    proc = subprocess.Popen(
        [
            str(exe),
            str(BASE_DIR / "app.py"),
        ],
        cwd=str(BASE_DIR),
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=hidden_flags(),
        close_fds=True,
    )

    API_PID.write_text(
        str(proc.pid),
        encoding="utf-8",
    )

    return proc


def spawn_ui(info):
    if port_open(UI_PORT):
        return None

    node = Path(info.get("node_exe") or "")
    next_bin = Path(info.get("next_bin") or "")
    frontend = Path(
        info.get("frontend_dir") or ""
    )

    if (
        not node.exists()
        or not next_bin.exists()
        or not frontend.exists()
    ):
        return False

    log_path = BASE_DIR / "data" / "ui.log"
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log = open(
        log_path,
        "a",
        encoding="utf-8",
        buffering=1,
    )

    proc = subprocess.Popen(
        [
            str(node),
            str(next_bin),
            "start",
            "-p",
            str(UI_PORT),
        ],
        cwd=str(frontend),
        stdout=log,
        stderr=log,
        stdin=subprocess.DEVNULL,
        creationflags=hidden_flags(),
        close_fds=True,
    )

    UI_PID.write_text(
        str(proc.pid),
        encoding="utf-8",
    )

    return proc


def main():
    background = "--background" in sys.argv[1:]
    info = runtime_info()

    if not info:
        return 4

    start_github_auto_update()

    # If it is already running, repeated double-click is virtually instant.
    if api_is_v11_11_5() and port_open(UI_PORT):
        if not background:
            open_ui()
        return 0

    # If V11.7 or another process still owns the same ports, START.bat normally
    # clears it first. Do not kill arbitrary processes from Python.
    if port_open(API_PORT) and not api_is_v11_11_5():
        return 2

    api_proc = spawn_api(info)

    if api_proc is False:
        return 2

    # Start frontend immediately rather than waiting for API initialization.
    ui_proc = spawn_ui(info)

    if ui_proc is False:
        return 3

    ui_opened = False
    api_ready = api_is_v11_11_5()
    ui_ready = port_open(UI_PORT)

    # Parallel readiness loop. Browser opens as soon as the dashboard responds;
    # API may finish a fraction later and the UI's normal polling recovers.
    deadline = time.monotonic() + 10

    while time.monotonic() < deadline:
        if not ui_ready:
            ui_ready = port_open(UI_PORT)

        if not api_ready:
            api_ready = api_is_v11_11_5()

        if ui_ready and not background and not ui_opened:
            open_ui()
            ui_opened = True

        if ui_ready and api_ready:
            return 0

        time.sleep(0.06)

    if not ui_ready:
        return 3

    if not api_ready:
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
