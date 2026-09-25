from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_ROOT = Path(os.environ.get("LOCALAPPDATA") or (BASE_DIR / "data" / "persistent"))
if os.environ.get("LOCALAPPDATA"):
    STATE_ROOT = STATE_ROOT / "NunesRecruitmentConsole"
STATE_ROOT.mkdir(parents=True, exist_ok=True)
STATUS_FILE = STATE_ROOT / "github_update_status.json"
LOCK_PORT = 5298
POLL_SECONDS = 60
DEFAULT_REPOSITORY_URL = "https://github.com/Nunes-instruments/indeed_auomation.git"


def _write_status(**values):
    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        **values,
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:
        pass


def _run(args, timeout=60, check=False):
    proc = subprocess.run(
        args,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
        creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
    )
    if check and proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "command failed").strip())
    return proc


def _git():
    return shutil.which("git.exe") or shutil.which("git")


def _branch(git):
    result = _run([git, "rev-parse", "--abbrev-ref", "HEAD"], timeout=10)
    name = result.stdout.strip()
    return name if result.returncode == 0 and name and name != "HEAD" else "main"


def _origin_exists(git):
    result = _run([git, "remote", "get-url", "origin"], timeout=10)
    if result.returncode != 0 or not result.stdout.strip():
        return False

    current = result.stdout.strip()
    if current.rstrip("/") != DEFAULT_REPOSITORY_URL.rstrip("/"):
        # This project has one canonical update repository.
        _run([git, "remote", "set-url", "origin", DEFAULT_REPOSITORY_URL], timeout=10)
    return True


def _clean_worktree(git):
    result = _run([git, "status", "--porcelain", "--untracked-files=no"], timeout=15)
    return result.returncode == 0 and not result.stdout.strip()


def _head(git, ref="HEAD"):
    result = _run([git, "rev-parse", ref], timeout=10)
    return result.stdout.strip() if result.returncode == 0 else ""


def _prepare_and_restart():
    _write_status(state="building", message="GitHub update pulled; rebuilding changed frontend/runtime files.")
    prep = _run(
        [sys.executable, str(BASE_DIR / "fast_setup.py"), "--prepare", "--system-python", sys.executable],
        timeout=900,
    )
    if prep.returncode != 0:
        raise RuntimeError((prep.stderr or prep.stdout or "fast setup failed")[-3000:])

    if os.name == "nt":
        _run([sys.executable, str(BASE_DIR / "stop_server.py")], timeout=30)
        subprocess.Popen(
            [sys.executable, str(BASE_DIR / "launcher.py"), "--background"],
            cwd=str(BASE_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
    _write_status(state="updated", message="Latest GitHub version applied. Refresh the browser.")


def check_once():
    git = _git()
    if not git:
        _write_status(state="waiting", message="Git is not installed; auto-update is inactive.")
        return False
    if not (BASE_DIR / ".git").exists():
        _write_status(state="waiting", message="This folder is not connected to GitHub yet. Run GITHUB_SETUP_ONCE.bat.")
        return False
    if not _origin_exists(git):
        _write_status(state="waiting", message="GitHub origin is not configured. Run GITHUB_SETUP_ONCE.bat.")
        return False
    if not _clean_worktree(git):
        _write_status(state="paused", message="Local code changes exist; auto-pull paused to avoid overwriting them.")
        return False

    branch = _branch(git)
    fetch = _run([git, "fetch", "--quiet", "origin", branch], timeout=120)
    if fetch.returncode != 0:
        _write_status(state="waiting", message=(fetch.stderr or "GitHub fetch failed")[-1000:])
        return False

    local = _head(git, "HEAD")
    remote = _head(git, f"origin/{branch}")
    if not remote or local == remote:
        _write_status(state="current", message=f"GitHub {branch} is current.", commit=local[:12])
        return False

    pull = _run([git, "pull", "--ff-only", "origin", branch], timeout=180)
    if pull.returncode != 0:
        _write_status(state="paused", message=(pull.stderr or pull.stdout or "GitHub pull failed")[-1500:])
        return False

    _prepare_and_restart()
    return True


def main():
    # Single-instance lock without external packages.
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", LOCK_PORT))
        lock.listen(1)
    except OSError:
        return 0

    _write_status(state="starting", message="GitHub auto-update watcher started.")
    while True:
        try:
            check_once()
        except Exception as exc:
            _write_status(state="error", message=str(exc)[:1800])
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    raise SystemExit(main())
