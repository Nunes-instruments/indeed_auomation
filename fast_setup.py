from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

LOCALAPPDATA = os.environ.get("LOCALAPPDATA")
if LOCALAPPDATA:
    STATE_DIR = Path(LOCALAPPDATA) / "NunesRecruitmentConsole"
else:
    STATE_DIR = BASE_DIR / "data" / "persistent"

RUNTIME_DIR = STATE_DIR / "FastRuntime"
RUNTIME_JSON = RUNTIME_DIR / "runtime.json"
MANIFEST_JSON = RUNTIME_DIR / "manifest.json"

SHARED_PYTHON_ENV = RUNTIME_DIR / "python_env"
SHARED_NODE_DIR = RUNTIME_DIR / "node_runtime"
SHARED_NODE_MODULES = SHARED_NODE_DIR / "node_modules"

REQUIREMENTS = BASE_DIR / "requirements.txt"
PACKAGE_JSON = FRONTEND_DIR / "package.json"

# Windows compatibility policy:
# - Python 3.10 through 3.13 are accepted when required packages are available.
# - Node.js 20.9+ is accepted (22/24/26 are all fine).
# This avoids forcing one exact runtime version on every Windows PC.
APP_VERSION = "V11.11.6"
COMPATIBLE_CACHE_VERSIONS = {
    "V11.11.6",
}
MIN_PYTHON = (3, 10)
MAX_PYTHON_EXCLUSIVE = (3, 14)
MIN_NODE = (20, 9, 0)


REQUIRED_IMPORTS = [
    "flask",
    "requests",
    "bs4",
    "pypdf",
    "docx",
    "websocket",
    "fitz",
]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def dependency_hash(package_path: Path) -> str:
    data = json.loads(package_path.read_text(encoding="utf-8"))
    payload = {
        "dependencies": data.get("dependencies") or {},
        "devDependencies": data.get("devDependencies") or {},
    }
    return sha256_bytes(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def frontend_hash(frontend: Path) -> str:
    """
    Hash only frontend runtime source/config. Package version is intentionally
    ignored so V11.8 can reuse the V11.7 build when the actual UI is unchanged.
    """
    h = hashlib.sha256()

    package = json.loads(
        (frontend / "package.json").read_text(encoding="utf-8")
    )
    normalized_package = {
        "scripts": package.get("scripts") or {},
        "dependencies": package.get("dependencies") or {},
        "devDependencies": package.get("devDependencies") or {},
    }
    h.update(
        json.dumps(
            normalized_package,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )

    files = []

    for name in ["next.config.ts", "tsconfig.json", "next-env.d.ts"]:
        p = frontend / name
        if p.exists():
            files.append(p)

    for folder in ["app", "components", "lib"]:
        root = frontend / folder
        if root.exists():
            for p in root.rglob("*"):
                if (
                    p.is_file()
                    and p.suffix.lower()
                    in {".ts", ".tsx", ".js", ".jsx", ".css", ".json"}
                ):
                    files.append(p)

    for p in sorted(
        files,
        key=lambda x: str(x.relative_to(frontend)).lower(),
    ):
        h.update(
            str(p.relative_to(frontend))
            .replace("\\", "/")
            .encode("utf-8")
        )
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")

    return h.hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2),
        encoding="utf-8",
    )



def _version_tuple(text):
    parts = []
    for token in str(text or "").strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def python_version_ok(exe: Path) -> bool:
    if not exe or not Path(exe).exists():
        return False

    code = (
        "import sys;"
        "print('%d.%d.%d' % "
        "(sys.version_info.major,sys.version_info.minor,sys.version_info.micro))"
    )

    try:
        result = subprocess.run(
            [str(exe), "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        )
        if result.returncode != 0:
            return False

        version = _version_tuple(result.stdout)
        return (
            version >= (MIN_PYTHON[0], MIN_PYTHON[1], 0)
            and version < (
                MAX_PYTHON_EXCLUSIVE[0],
                MAX_PYTHON_EXCLUSIVE[1],
                0,
            )
        )
    except Exception:
        return False


def find_system_python_candidates():
    """Find compatible Python across common Windows installation styles."""
    seen = set()
    candidates = []

    def add(value):
        if not value:
            return
        try:
            p = Path(value)
        except Exception:
            return

        key = str(p).lower()
        if key in seen:
            return
        seen.add(key)

        if p.exists():
            candidates.append(p)

    # Current interpreter first.
    add(sys.executable)

    # PATH aliases.
    for name in ("python.exe", "python", "python3.exe", "python3"):
        found = shutil.which(name)
        if found:
            add(found)

    # Windows Python launcher, newest compatible versions first.
    if os.name == "nt":
        py_launcher = shutil.which("py.exe") or shutil.which("py")
        if py_launcher:
            for tag in ("-3.13", "-3.12", "-3.11", "-3.10"):
                try:
                    result = subprocess.run(
                        [
                            py_launcher,
                            tag,
                            "-c",
                            "import sys;print(sys.executable)",
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        timeout=8,
                    )
                    if result.returncode == 0:
                        add(result.stdout.strip())
                except Exception:
                    pass

        # Common per-user and machine-wide locations.
        local_python_root = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs"
            / "Python"
        )

        if local_python_root.exists():
            for pattern in (
                "Python313/python.exe",
                "Python312/python.exe",
                "Python311/python.exe",
                "Python310/python.exe",
            ):
                add(local_python_root / pattern)

        # Machine-wide CPython installers commonly use C:\Program Files\Python3xx
        # rather than a nested C:\Program Files\Python\Python3xx folder.
        for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
            root_value = os.environ.get(env_name)
            if not root_value:
                continue
            root = Path(root_value)

            for folder in (
                "Python313",
                "Python312",
                "Python311",
                "Python310",
            ):
                add(root / folder / "python.exe")
                add(root / "Python" / folder / "python.exe")

    return [
        p
        for p in candidates
        if python_version_ok(p)
    ]


def python_imports_ok(exe: Path) -> bool:
    if not exe or not exe.exists() or not python_version_ok(exe):
        return False

    code = (
        "import "
        + ",".join(REQUIRED_IMPORTS)
        + ";print('NUNES_RUNTIME_OK')"
    )

    try:
        result = subprocess.run(
            [str(exe), "-c", code],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=18,
        )
        return (
            result.returncode == 0
            and "NUNES_RUNTIME_OK" in (result.stdout or "")
        )
    except Exception:
        return False


def frontend_ready(frontend: Path) -> bool:
    return (
        frontend.exists()
        and (frontend / ".next" / "BUILD_ID").exists()
        and (
            frontend
            / "node_modules"
            / "next"
            / "dist"
            / "bin"
            / "next"
        ).exists()
    )


def node_modules_ready(path: Path) -> bool:
    return (
        path.exists()
        and (path / "next" / "dist" / "bin" / "next").exists()
        and (path / "react" / "package.json").exists()
    )


def compatible_previous_roots():
    """
    Look for already-installed V11.x folders in the same parent, Downloads and
    Desktop. The newest compatible folder wins.
    """
    current = BASE_DIR.resolve()
    candidates = {}

    roots = [
        BASE_DIR.parent,
        Path.home() / "Downloads",
        Path.home() / "Desktop",
        Path.home() / "Documents",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "NunesRecruitmentConsole",
    ]

    patterns = [
        "NUNES_V11*",
        "Nunes_Indeed_Recruitment_Console_V11*",
    ]

    for root in roots:
        if not root.exists():
            continue

        for pattern in patterns:
            try:
                for p in root.glob(pattern):
                    if not p.is_dir():
                        continue

                    try:
                        resolved = p.resolve()
                    except Exception:
                        resolved = p

                    if resolved == current:
                        continue

                    key = str(resolved).lower()

                    try:
                        mtime = p.stat().st_mtime
                    except Exception:
                        mtime = 0

                    old = candidates.get(key)
                    if old is None or mtime > old[0]:
                        candidates[key] = (mtime, p)
            except Exception:
                pass

    return [
        item[1]
        for item in sorted(
            candidates.values(),
            key=lambda item: item[0],
            reverse=True,
        )
    ]


def same_requirements(root: Path) -> bool:
    old = root / "requirements.txt"
    if not old.exists():
        return False

    try:
        return file_hash(old) == file_hash(REQUIREMENTS)
    except Exception:
        return False


def same_frontend_dependencies(root: Path) -> bool:
    old = root / "frontend" / "package.json"
    if not old.exists():
        return False

    try:
        return dependency_hash(old) == dependency_hash(PACKAGE_JSON)
    except Exception:
        return False


def same_frontend_source(root: Path) -> bool:
    old = root / "frontend"
    if not old.exists():
        return False

    try:
        return frontend_hash(old) == frontend_hash(FRONTEND_DIR)
    except Exception:
        return False


def system_python_from_arg(value: str | None) -> Path:
    if value:
        p = Path(value)
        if python_version_ok(p):
            return p

    for candidate in find_system_python_candidates():
        return candidate

    raise RuntimeError(
        "Compatible Python was not found. "
        "Install Python 3.10, 3.11, 3.12 or 3.13."
    )


def find_python_runtime(system_python: Path):
    # 0) If the PC already has every required package, use it directly.
    # This is the fastest possible first run and avoids creating a venv.
    if python_imports_ok(system_python):
        return system_python, "system-ready"

    # 1) Current folder from an earlier run.
    current = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    if python_imports_ok(current):
        return current, "current"

    # 2) Shared cache from earlier V11.8/future V11.x run.
    shared = SHARED_PYTHON_ENV / "Scripts" / "python.exe"
    manifest = read_json(MANIFEST_JSON)

    if (
        manifest.get("requirements_hash") == file_hash(REQUIREMENTS)
        and python_imports_ok(shared)
    ):
        return shared, "shared"

    # 3) Reuse a compatible previous V11.x environment directly.
    for root in compatible_previous_roots():
        if not same_requirements(root):
            continue

        exe = root / ".venv" / "Scripts" / "python.exe"

        if python_imports_ok(exe):
            return exe, f"previous:{root.name}"

    return None, None


def find_frontend_runtime():
    # 1) Current folder if already built.
    if frontend_ready(FRONTEND_DIR):
        return FRONTEND_DIR, "current"

    # 2) Previously selected frontend runtime.
    runtime = read_json(RUNTIME_JSON)
    selected = Path(runtime.get("frontend_dir") or "")

    if selected and frontend_ready(selected):
        try:
            if (
                dependency_hash(selected / "package.json")
                == dependency_hash(PACKAGE_JSON)
                and frontend_hash(selected)
                == frontend_hash(FRONTEND_DIR)
            ):
                return selected, "saved"
        except Exception:
            pass

    # 3) Reuse a previous compatible V11.x production build directly.
    for root in compatible_previous_roots():
        old_frontend = root / "frontend"

        if (
            frontend_ready(old_frontend)
            and same_frontend_dependencies(root)
            and same_frontend_source(root)
        ):
            return old_frontend, f"previous:{root.name}"

    return None, None


def find_previous_node_modules():
    if node_modules_ready(FRONTEND_DIR / "node_modules"):
        return FRONTEND_DIR / "node_modules", "current"

    if node_modules_ready(SHARED_NODE_MODULES):
        manifest = read_json(MANIFEST_JSON)
        if (
            manifest.get("node_dependency_hash")
            == dependency_hash(PACKAGE_JSON)
        ):
            return SHARED_NODE_MODULES, "shared"

    for root in compatible_previous_roots():
        if not same_frontend_dependencies(root):
            continue

        modules = root / "frontend" / "node_modules"
        if node_modules_ready(modules):
            return modules, f"previous:{root.name}"

    return None, None


def run(cmd, cwd=None):
    result = subprocess.run(
        [str(x) for x in cmd],
        cwd=str(cwd) if cwd else None,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Command failed: "
            + " ".join(str(x) for x in cmd)
        )


def _node_version_ok(exe: Path) -> bool:
    if not exe or not Path(exe).exists():
        return False

    try:
        result = subprocess.run(
            [str(exe), "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=8,
        )
        return (
            result.returncode == 0
            and _version_tuple(result.stdout) >= MIN_NODE
        )
    except Exception:
        return False


def find_node_executable():
    candidates = []

    def add(value):
        if not value:
            return
        p = Path(value)
        if p.exists() and p not in candidates:
            candidates.append(p)

    add(shutil.which("node.exe"))
    add(shutil.which("node"))

    if os.name == "nt":
        for root in [
            os.environ.get("ProgramFiles"),
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("LOCALAPPDATA"),
        ]:
            if not root:
                continue
            base = Path(root)
            add(base / "nodejs" / "node.exe")
            add(base / "Programs" / "nodejs" / "node.exe")

    for candidate in candidates:
        if _node_version_ok(candidate):
            return candidate

    return None


def _npm_for_node(node: Path):
    node = Path(node)

    names = (
        ["npm.cmd", "npm.exe", "npm"]
        if os.name == "nt"
        else ["npm", "npm-cli.js"]
    )

    for name in names:
        p = node.parent / name
        if p.exists():
            return p

    found = shutil.which("npm.cmd") or shutil.which("npm")
    return Path(found) if found else None


def install_node_with_winget():
    """
    Last-resort Windows first-run bootstrap.
    It runs only when Node is missing and no reusable runtime can launch the UI.
    """
    if os.name != "nt":
        return None

    winget = shutil.which("winget.exe") or shutil.which("winget")
    if not winget:
        return None

    print("[SETUP] Node.js is missing. Installing Node.js LTS once...")

    try:
        result = subprocess.run(
            [
                winget,
                "install",
                "--id",
                "OpenJS.NodeJS.LTS",
                "--exact",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
            ],
            timeout=600,
        )
        if result.returncode not in (0,):
            return None
    except Exception:
        return None

    # winget may update PATH only for future processes, so search standard
    # install locations directly after installation.
    return find_node_executable()


def node_cmd():
    value = find_node_executable()

    if not value:
        value = install_node_with_winget()

    if not value:
        raise RuntimeError(
            "Node.js 20.9 or newer was not found. "
            "Install Node.js LTS once, then run START.bat again."
        )

    return Path(value)


def npm_cmd():
    node = node_cmd()
    npm = _npm_for_node(node)

    if not npm:
        raise RuntimeError(
            "npm was not found next to the installed Node.js runtime."
        )

    return str(npm)


def create_shared_python(system_python: Path):
    shared = SHARED_PYTHON_ENV / "Scripts" / "python.exe"

    if SHARED_PYTHON_ENV.exists():
        shutil.rmtree(
            SHARED_PYTHON_ENV,
            ignore_errors=True,
        )

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    print("[SETUP] Creating shared Python runtime once...")
    run(
        [
            system_python,
            "-m",
            "venv",
            SHARED_PYTHON_ENV,
        ]
    )

    print("[SETUP] Installing Python packages once...")
    run(
        [
            shared,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--prefer-binary",
            "--only-binary=:all:",
            "--upgrade-strategy",
            "only-if-needed",
            "--no-input",
            "--no-compile",
            "-r",
            REQUIREMENTS,
        ]
    )

    if not python_imports_ok(shared):
        raise RuntimeError(
            "Python runtime verification failed."
        )

    manifest = read_json(MANIFEST_JSON)
    manifest["requirements_hash"] = file_hash(REQUIREMENTS)
    write_json(MANIFEST_JSON, manifest)

    return shared


def create_shared_node_modules():
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    SHARED_NODE_DIR.mkdir(parents=True, exist_ok=True)

    # Only dependency sections matter for the shared node runtime.
    package = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    shared_package = {
        "name": "nunes-recruitment-shared-runtime",
        "private": True,
        "dependencies": package.get("dependencies") or {},
        "devDependencies": package.get("devDependencies") or {},
    }

    write_json(
        SHARED_NODE_DIR / "package.json",
        shared_package,
    )

    print("[SETUP] Installing dashboard packages once...")
    run(
        [
            npm_cmd(),
            "install",
            "--no-audit",
            "--no-fund",
            "--prefer-offline",
            "--progress=false",
        ],
        cwd=SHARED_NODE_DIR,
    )

    if not node_modules_ready(SHARED_NODE_MODULES):
        raise RuntimeError(
            "Dashboard package verification failed."
        )

    manifest = read_json(MANIFEST_JSON)
    manifest["node_dependency_hash"] = dependency_hash(PACKAGE_JSON)
    write_json(MANIFEST_JSON, manifest)

    return SHARED_NODE_MODULES



def _remove_node_modules_path(path: Path):
    """Safely remove a normal node_modules folder, symlink, or Windows junction."""
    if not path.exists() and not path.is_symlink():
        return

    try:
        if path.is_symlink():
            path.unlink()
            return
    except Exception:
        pass

    if os.name == "nt":
        # rmdir removes a directory junction itself without deleting its target.
        result = subprocess.run(
            ["cmd", "/d", "/c", "rmdir", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0 and not path.exists():
            return

    shutil.rmtree(path, ignore_errors=True)


def _copy_node_modules_fast(source: Path, target: Path):
    """
    Last-resort local copy. On Windows robocopy is materially faster than a
    Python file-by-file copy for node_modules.
    """
    _remove_node_modules_path(target)

    if os.name == "nt" and shutil.which("robocopy"):
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [
                "robocopy",
                str(source),
                str(target),
                "/E",
                "/COPY:DAT",
                "/DCOPY:DAT",
                "/R:1",
                "/W:1",
                "/NFL",
                "/NDL",
                "/NJH",
                "/NJS",
                "/NP",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # robocopy 0..7 are successful/non-fatal result codes.
        if result.returncode < 8 and node_modules_ready(target):
            return target

    shutil.copytree(source, target, dirs_exist_ok=False)
    return target


def attach_local_node_modules(modules: Path):
    """
    Next.js 16/Turbopack requires next/package.json to be resolvable from the
    current frontend workspace. NODE_PATH pointing at another V11.x folder is
    not enough.

    Reuse remains fast by putting a local node_modules junction/symlink inside
    this frontend. If linking is unavailable, make one local disk copy rather
    than downloading/installing packages again.
    """
    modules = Path(modules)
    local = FRONTEND_DIR / "node_modules"

    if node_modules_ready(local):
        return local

    if not node_modules_ready(modules):
        raise RuntimeError("Reusable dashboard packages are incomplete.")

    try:
        if local.resolve() == modules.resolve():
            return local
    except Exception:
        pass

    _remove_node_modules_path(local)
    local.parent.mkdir(parents=True, exist_ok=True)

    linked = False

    if os.name == "nt":
        # Directory junctions do not require developer mode and are ideal for
        # reusing a previous V11.x node_modules tree on the same PC.
        result = subprocess.run(
            ["cmd", "/d", "/c", "mklink", "/J", str(local), str(modules)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        linked = result.returncode == 0 and node_modules_ready(local)
    else:
        try:
            os.symlink(str(modules), str(local), target_is_directory=True)
            linked = node_modules_ready(local)
        except Exception:
            linked = False

    if linked:
        print("[FAST] Linked dashboard packages into current workspace.")
        return local

    print("[FAST] Link unavailable; copying cached dashboard packages locally once...")
    copied = _copy_node_modules_fast(modules, local)

    if not node_modules_ready(copied):
        raise RuntimeError("Local dashboard package preparation failed.")

    return copied


def build_current_frontend(modules: Path):
    node = node_cmd()

    # Critical V11.9.1 fix: make Next/React visible from THIS frontend root.
    # V11.9 only set NODE_PATH to another folder, which Next.js 16 Turbopack
    # intentionally rejects as outside the hermetic workspace.
    local_modules = attach_local_node_modules(modules)
    next_bin = local_modules / "next" / "dist" / "bin" / "next"

    if not next_bin.exists():
        raise RuntimeError("Next.js runtime was not found in local node_modules.")

    env = os.environ.copy()
    env["NODE_ENV"] = "production"
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env["NODE_PATH"] = str(local_modules)

    print("[SETUP] Building dashboard once (local packages, Webpack compatibility mode)...")

    # Next.js 16 defaults to Turbopack. With a reused package tree, Webpack is
    # the reliable compatibility path and avoids Turbopack's external-workspace
    # hermetic package restriction shown in the V11.9 error.
    result = subprocess.run(
        [
            str(node),
            str(next_bin),
            "build",
            "--webpack",
        ],
        cwd=str(FRONTEND_DIR),
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(
            "Dashboard production build failed. "
            "Local node_modules is present; see the build output above for the exact source error."
        )

    if not (FRONTEND_DIR / ".next" / "BUILD_ID").exists():
        raise RuntimeError("Dashboard BUILD_ID is missing after build.")

    return FRONTEND_DIR



def prepare(system_python: Path):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

    python_exe, python_source = find_python_runtime(system_python)

    if python_exe:
        print(
            "[FAST] Reusing Python packages:",
            python_source,
        )
    else:
        python_exe = create_shared_python(system_python)
        python_source = "shared-new"

    frontend_dir, frontend_source = find_frontend_runtime()

    if frontend_dir:
        print(
            "[FAST] Reusing dashboard build:",
            frontend_source,
        )
    else:
        modules, module_source = find_previous_node_modules()

        if modules:
            print(
                "[FAST] Reusing dashboard packages:",
                module_source,
            )
        else:
            modules = create_shared_node_modules()
            module_source = "shared-new"

        frontend_dir = build_current_frontend(modules)
        frontend_source = "current-new-build"

    node = node_cmd()
    next_bin = (
        frontend_dir
        / "node_modules"
        / "next"
        / "dist"
        / "bin"
        / "next"
    )

    # A reused .next build normally has local node_modules.
    if not next_bin.exists():
        modules, _ = find_previous_node_modules()

        if not modules:
            modules = create_shared_node_modules()

        next_bin = (
            modules
            / "next"
            / "dist"
            / "bin"
            / "next"
        )

    data = {
        "version": APP_VERSION,
        "python_exe": str(python_exe),
        "system_python": str(system_python),
        "node_exe": str(node),
        "frontend_dir": str(frontend_dir),
        "next_bin": str(next_bin),
        "requirements_hash": file_hash(REQUIREMENTS),
        "node_dependency_hash": dependency_hash(PACKAGE_JSON),
        "frontend_hash": frontend_hash(FRONTEND_DIR),
        "python_source": python_source,
        "frontend_source": frontend_source,
    }

    write_json(RUNTIME_JSON, data)

    if not runtime_ready():
        raise RuntimeError(
            "Fast runtime was created but did not pass final verification."
        )

    return data


def runtime_quick_ready() -> bool:
    """Daily-start check: paths only. No imports, dependency resolution or full source hashing."""
    data = read_json(RUNTIME_JSON)
    if data.get("version") not in COMPATIBLE_CACHE_VERSIONS:
        return False

    python_exe = Path(data.get("python_exe") or "")
    frontend_dir = Path(data.get("frontend_dir") or "")
    next_bin = Path(data.get("next_bin") or "")
    node_exe = Path(data.get("node_exe") or "")

    return (
        python_exe.exists()
        and node_exe.exists()
        and next_bin.exists()
        and frontend_dir.exists()
        and (frontend_dir / ".next" / "BUILD_ID").exists()
    )


def runtime_ready() -> bool:
    data = read_json(RUNTIME_JSON)

    if data.get("version") not in COMPATIBLE_CACHE_VERSIONS:
        return False

    try:
        if data.get("requirements_hash") != file_hash(REQUIREMENTS):
            return False

        if data.get("node_dependency_hash") != dependency_hash(PACKAGE_JSON):
            return False

        if data.get("frontend_hash") != frontend_hash(FRONTEND_DIR):
            return False
    except Exception:
        return False

    python_exe = Path(data.get("python_exe") or "")
    frontend_dir = Path(data.get("frontend_dir") or "")
    next_bin = Path(data.get("next_bin") or "")
    node_exe = Path(data.get("node_exe") or "")

    return (
        python_imports_ok(python_exe)
        and frontend_ready(frontend_dir)
        and next_bin.exists()
        and node_exe.exists()
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--quick-check", action="store_true")
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--system-python")
    args = parser.parse_args()

    if args.quick_check:
        return 0 if runtime_quick_ready() else 1

    if args.check:
        return 0 if runtime_ready() else 1

    system_python = system_python_from_arg(
        args.system_python
    )

    prepare(system_python)

    print()
    print("[OK] Fast runtime ready.")
    print("[OK] Future starts skip pip, npm install and dashboard build.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
