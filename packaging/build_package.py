"""Builds the distributable zip of the CMI Blog Revamp Agent.

What this produces (dist/cmi-blog-agent-<date>-<n>.zip):
  - backend/ with every .py module compiled to native .pyd extensions (Nuitka,
    compiled for Python 3.12) -- no readable Python source ships
  - one compiled secrets module (backend/bpk) built from the local .env, so no
    secret is in any plaintext file. By default it holds the user's LLM-proxy
    token, NOT the DeepSeek key (--direct ships the key; see generate_bpk).
    DB credentials are never baked: the package ships catalog.sqlite and never
    touches Postgres
  - backend/requirements.txt with EXACT pinned versions: produced from the build
    venv's `pip freeze`, minus build tooling and DB drivers. Every pin is a
    version proven to work against the compiled backend.
  - frontend/ untouched, HOW TO RUN.txt, start.bat derived from the repo's
    start.bat (the packaging block explained below)
  - pyruntime/ = the python.org embeddable Python 3.12 (amd64) plus pip, so
    end-users never install Python and no admin/root is ever needed. It is NOT
    a copy of a full installation (that shipped ~5000 files including the stdlib
    test suite, IDLE and turtle demos).

End-user flow (design decision: end-user PCs have unrestricted internet):
  extract anywhere, double-click start.bat -> the bundled interpreter pip-installs
  backend/requirements.txt directly into pyruntime/site-packages on the FIRST
  run (guarded by pyruntime\\.deps_done so later runs skip it in seconds), then
  reports.ensure_embedding_model downloads/caches the matching model, and the
  app starts. No admin, no installed Python required.

Dev flow stays byte-for-byte the behavior of the repo's start.bat. The build
derives the packaged start.bat from it by replacing the section between the
PACKAGING markers (dev python/venv/pip acquisition) with
packaging/packaged_block.bat, and by removing the ".env file" warning block
(secrets are baked into the compiled bpk module).

Usage (any Python >= 3.10 on the build machine):

    # one build per end user: mint their token first (cd monitor && npm run token -- create "Priya" 5)
    set MONITOR_PROXY_TOKEN=cmi_...                        # (PowerShell: $env:MONITOR_PROXY_TOKEN="cmi_...")
    python packaging/build_package.py --user "Priya"       # full build + zip, DeepSeek key NOT shipped
    python packaging/build_package.py --user "Priya" --no-zip   # stage only (for inspection)
    python packaging/build_package.py --direct             # LEGACY: ship the DeepSeek key itself

Proxy mode (default): the packaged app talks to <MONITOR_URL>/api/v1 with a per-user
token; the real DeepSeek key lives only in the Vercel project's env (see monitor/README.md).

Heavy caches live under packaging/_build and are safe across builds:
  pytool/  - full Python 3.12 install (the compiler's toolchain)
  venv/    - build venv (runtime deps + nuitka), created from pytool
  pyds/    - per-module compiled .pyd cache, keyed by source sha256, so
             unchanged modules are not recompiled after a stage wipe
  pyruntime/ - the clean embeddable runtime shipped in the zip
First run downloads the Python installer/embeddable zip/pip and compiles all
modules (5-25 min); later builds only recompile changed modules.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
BUILD = Path(__file__).resolve().parent / "_build"

# The toolchain must be the same minor version (3.12) as the shipped runtime so
# the compiled cp312 .pyd files load in the packaged interpreter.
PY_VERSION = "3.12.10"
PY_INSTALLER_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-amd64.exe"
PY_EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/python-{PY_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

# The app embeds text on CPU only (reports.py imports sentence-transformers; no
# cuda/device usage anywhere in backend/). Pinning torch from PyPI's default
# index would ship a ~2.9 GB Windows wheel that bundles CUDA DLLs, so build the
# requirements against the +cpu local-version builds from PyTorch's CPU-only
# wheel index instead.
CPU_TORCH_INDEX = "https://download.pytorch.org/whl/cpu"

PY_TOOLCHAIN = BUILD / "pytool"          # full 3.12 install used to compile/build
BUILD_VENV = BUILD / "venv"              # venv with runtime deps + nuitka
PYRUNTIME = BUILD / "pyruntime"          # clean embeddable runtime, shipped as pyruntime/
PYDS = BUILD / "pyds"                    # compiled-module cache: pyds/<stem>/<sha256>/
STAGE = BUILD / "stage"
DIST = REPO / "dist"

# start.bat's same fallback list: if the build machine already has a 3.12
# install that the installer refuses to duplicate (exit 1638), copy THAT
# install into pytool -- same file layout, same ABI.
EXISTING_312_DIRS = [
    Path(os.environ["LOCALAPPDATA"]) / "Programs" / "Python" / "Python312",
    Path("C:/Program Files/Python312"),
    Path("C:/Program Files (x86)/Python312"),
]

# backend .py files that must NOT ship: they are dev-only tooling (fixtures,
# replay harnesses, probes). Everything else under backend/ gets compiled.
BACKEND_EXCLUDE_DIRS = {"devtools", "scripts", "__pycache__", "output", "logs", ".search_cache"}
BACKEND_EXCLUDE_FILES = {"README.md"}

# root items that must not ship
ROOT_EXCLUDE = {
    ".git", ".code-review-graph", "venv", "__pycache__", "output", "input_docs",
    "docs", "dist", "packaging", ".env", ".env.example", "content.html", "out_teen.md",
    ".gitignore", ".claude",
}
ROOT_EXCLUDE_GLOBS = ("*.docx", "*.xlsx", "*.md", "Revised_*")

# Only these file kinds may remain under backend/ in the shipped package.
BACKEND_ALLOWED_SUFFIX = {".pyd"}
BACKEND_ALLOWED_NAMES = {"catalog.sqlite", "source_registry.json", "requirements.txt"}

# DB drivers / packaging tooling: present in the build venv but never imported by
# the packaged backend (it only uses catalog.sqlite; psycopg2 lives in
# backend/scripts/, which is excluded). Never shipped in requirements.txt.
PINS_EXCLUDE = {
    "psycopg2", "psycopg2-binary", "asyncpg", "pg8000",          # DB drivers
    "nuitka", "zstandard", "ordered-set",                        # the compiler
    "pip", "setuptools", "wheel", "ordered_set",                 # packaging tooling
}

HOW_TO_RUN = """CMI Blog Revamp Agent
=====================
How to run:
  1. Extract this whole folder to anywhere on the PC (e.g. Desktop). Keep all files together.
  2. Double-click  start.bat
  3. The app opens in your browser at http://127.0.0.1:8000

What to expect:
  - The FIRST run needs an internet connection. Python components are downloaded
    and installed inside this folder (several hundred MB), and the report-matching
    language model is cached in your user profile. This is a one-time setup and can
    take 5-15 minutes; progress shows in the window.
  - Every later run starts in seconds: no downloads, no installs.
  - No Python installation is needed -- a private Python runtime ships in the
    "pyruntime" folder. Nothing is installed outside this folder or your user
    profile; no administrator rights are required.
  - Closing the browser tab shuts the app down after a few seconds.
  - Run start.bat again any time to start a fresh copy. If a first run was
    interrupted (e.g. internet dropped), just run it again: it resumes.

Note: this app reports usage to its provider - how many documents you
  regenerate and their titles, the estimated API cost, and an approximate
  city-level location. No document text is ever sent.
"""


def download(url: str, dest: Path, min_size: int = 1) -> None:
    """Robust GET for slow/sputtering links (python.org can trickle at 30KB/s
    here and the first urlretrieve attempt died on a socket timeout). Writes
    chunks to disk as they arrive, then falls back to resumable curl.exe if a
    urllib attempt fails; validates the size either way."""
    log(f"downloading {url} ...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    ok = False
    last = None
    # 1) urllib, chunkwise, generous idle timeout
    for attempt in (1, 2):
        try:
            data = None
            with urllib.request.urlopen(url, timeout=120) as r:
                expect = int(r.headers.get("Content-Length", "0"))
                if expect and expect < min_size:
                    raise RuntimeError(f"Content-Length {expect} too small")
                with open(part, "wb") as fh:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        fh.write(chunk)
            size = part.stat().st_size
            if size < min_size or (expect and size != expect):
                raise RuntimeError(f"got {size} bytes (expected {expect or min_size})")
            part.replace(dest)
            ok = True
            break
        except Exception as e:  # noqa: BLE001 - fall through to curl
            last = e
            log(f"  urllib attempt {attempt} failed: {type(e).__name__}: {e}")
    # 2) curl.exe (ships with Windows 11) with resume + auto-retry
    if not ok:
        try:
            r = run(["curl.exe", "-L", "--fail", "--connect-timeout", "30",
                     "--continue-at", "-", "--retry", "5", "--speed-time", "120",
                     "--speed-limit", "2048", "-o", part, url], check=False)
            size = part.stat().st_size if part.is_file() else 0
            if r.returncode == 0 and size >= min_size:
                part.replace(dest)
                ok = True
            elif size:
                log(f"  curl got {size} bytes (rc={r.returncode})")
        except FileNotFoundError:
            log("  curl.exe unavailable")
    if not ok:
        die(f"could not download {url}: {last}")
    log(f"saved {dest} ({dest.stat().st_size / 1e6:.1f} MB)")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    show = not kwargs.pop("quiet", False)
    if show:
        print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def die(msg: str) -> None:
    raise SystemExit(f"[build] FATAL: {msg}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- step 1: build Pythons
def ensure_pytool() -> None:
    """A full Python 3.12.install on the BUILD machine, used to create the build
    venv that runs nuitka. Never shipped; not needed at runtime."""
    if (PY_TOOLCHAIN / "python.exe").is_file():
        return
    installer = BUILD / "installers" / PY_INSTALLER_URL.rsplit("/", 1)[-1]
    BUILD.mkdir(parents=True, exist_ok=True)
    if not installer.is_file():
        download(PY_INSTALLER_URL, installer, min_size=10_000_000)
    log(f"installing Python {PY_VERSION} into {PY_TOOLCHAIN} ...")
    r = run([
        installer, "/quiet", "InstallAllUsers=0", "PrependPath=0",
        "Include_test=0", "Include_launcher=0", f"TargetDir={PY_TOOLCHAIN}",
    ])
    if (PY_TOOLCHAIN / "python.exe").is_file():
        log("pytool ready")
        return
    if r.returncode == 1638:
        # 3.12 is already installed as a Windows product (another instance
        # registered under the same version) -- the installer refuses to put a
        # second copy anywhere. Reuse the existing per-machine install instead.
        src = next((d for d in EXISTING_312_DIRS if (d / "python.exe").is_file()), None)
        if src is None:
            die("installer exit 1638 but no existing Python 3.12 found to reuse")
        log(f"installer says Python 3.12 already exists on this machine -- copying {src} ...")
        shutil.copytree(src, PY_TOOLCHAIN, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("unins*", "__pycache__", "*.log"))
        if not (PY_TOOLCHAIN / "python.exe").is_file():
            die("pytool copy failed")
        log("pytool ready (reused existing install)")
        return
    die(f"python installer failed (exit {r.returncode})")


def _patch_pth() -> None:
    """Enable site + site-packages on the embeddable distribution so pip installs
    (done here at build time and later on the user's PC) are importable."""
    pth = PYRUNTIME / "python312._pth"
    if not pth.is_file():
        die("python312._pth missing from the embeddable distribution")
    text = pth.read_text(encoding="utf-8")
    lines = []
    for ln in text.splitlines():
        if ln.strip() == "#import site":
            lines.append("import site")
        elif ln.strip() == "import site":
            lines.append("import site")
        else:
            lines.append(ln)
    if "import site" not in lines:
        lines.append("import site")
    if not any(ln.strip().rstrip("\\/") == "Lib\\site-packages" for ln in lines):
        # insert right after the bare "." entry (the interpreter's own dir)
        idx = next(i for i, ln in enumerate(lines) if ln.strip() == ".")
        lines.insert(idx + 1, "Lib\\site-packages")
    pth.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (PYRUNTIME / "Lib" / "site-packages").mkdir(parents=True, exist_ok=True)


def ensure_pyruntime() -> None:
    """The SHIPPED runtime: python.org embeddable zip (clean, small, no admin)
    with pip added at build time. The embeddable distribution intentionally
    lacks tkinter/ensurepip/venv -- the packaged start.bat never uses them; it
    pip-installs directly into pyruntime on the user's first launch."""
    if (PYRUNTIME / "python.exe").is_file() and (PYRUNTIME / "Lib" / "site-packages" / "pip").is_dir():
        _patch_pth()  # re-assert; cheap and catches manual tinkering
        return
    log(f"building clean embeddable pyruntime ({PY_VERSION}) ...")
    zpath = BUILD / "installers" / PY_EMBED_URL.rsplit("/", 1)[-1]
    zpath.parent.mkdir(parents=True, exist_ok=True)
    if not zpath.is_file():
        download(PY_EMBED_URL, zpath, min_size=5_000_000)
    if PYRUNTIME.exists():
        shutil.rmtree(PYRUNTIME)  # e.g. a stale full-install copy from older builds
    with zipfile.ZipFile(zpath) as z:
        z.extractall(PYRUNTIME)
    _patch_pth()
    getpip = BUILD / "get-pip.py"
    if not getpip.is_file():
        download(GET_PIP_URL, getpip, min_size=100_000)
    log("bootstrapping pip into pyruntime ...")
    r = run([PYRUNTIME / "python.exe", str(getpip), "--no-warn-script-location", "--quiet"],
            check=False)
    if r.returncode != 0 or not (PYRUNTIME / "Lib" / "site-packages" / "pip").is_dir():
        die("pip bootstrap into pyruntime failed")
    r = run([PYRUNTIME / "python.exe", "-m", "pip", "--version"], quiet=True, capture_output=True, text=True)
    if r.returncode != 0:
        die("pyruntime cannot run pip")
    log(f"pyruntime ready: {r.stdout.strip()}")


# ---------------------------------------------------------------- build venv + pins
def _requirements_from_repo() -> list[str]:
    """Top-level runtime requirements the compiled backend needs, from the repo's
    requirements.txt minus dev/DB lines."""
    reqs = []
    for line in (BACKEND / "requirements.txt").read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        nick = re.split(r"[<>=!\[\s]", line, maxsplit=1)[0].lower().replace("_", "-")
        if nick in PINS_EXCLUDE:
            continue
        reqs.append(line)
    if not reqs:
        die("backend/requirements.txt is empty after filtering")
    return reqs


VENV_SKIP = {"sentence-transformers"}  # heavy subtree resolved for pins via dry-run only

# The sentence-transformers subtree is NOT installed in the build venv (see
# VENV_SKIP), so `pip freeze` cannot constrain it. These are the exact versions
# proven end-to-end (including cold first-run install + CPU embedding) by the
# 2026-10-25 package test -- keep the subtree locked to them.
STREE_PROVEN = {
    "sentence-transformers": "6.2.0",
    "transformers": "5.20.2",
    "tokenizers": "0.23.3",
    "safetensors": "0.8.0",
    "huggingface-hub": "2.3.0",
}


def _pip_index_versions(venv_py: Path, package: str, index_url: str) -> list[str]:
    """Metadata-only version list of `package` on `index_url` (no wheel download:
    `pip index versions` only reads the simple index)."""
    r = run([venv_py, "-m", "pip", "index", "versions", package,
             "--index-url", index_url, "--disable-pip-version-check",
             "--retries", "5", "--timeout", "60"],
            quiet=True, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"pip index versions {package} @ {index_url} failed:\n{r.stderr.strip()}")
    versions: list[str] = []
    for line in r.stdout.splitlines():
        m = re.match(r"^\s*Available versions:\s*(.+)$", line)
        if m:
            versions = [v.strip() for v in m.group(1).split(",") if v.strip()]
    if not versions:
        die(f"could not parse versions of {package} @ {index_url}:\n{r.stdout}")
    return versions


def ensure_build_venv() -> Path:
    """Venv the compiler runs in. Runtime deps (minus the torch-heavy
    sentence-transformers subtree, which nothing compiled imports) plus the
    compiler toolchain."""
    BUILD_VENV.mkdir(parents=True, exist_ok=True)
    venv_py = BUILD_VENV / "Scripts" / "python.exe"
    if venv_py.is_file():
        cfg = (BUILD_VENV / "pyvenv.cfg").read_text(encoding="utf-8")
        if f"home = {PY_TOOLCHAIN}" not in cfg:
            log(f"build venv was based on a different Python -- recreating "
                f"(expected after a pytool move) ...")
            shutil.rmtree(BUILD_VENV)
    if not venv_py.is_file():
        run([PY_TOOLCHAIN / "python.exe", "-m", "venv", BUILD_VENV], check=True)
    reqs = [r for r in _requirements_from_repo()
            if re.split(r"[<>=!\[\s]", r, maxsplit=1)[0].lower().replace("_", "-") not in VENV_SKIP]
    if not reqs:
        die("build venv requirement list is empty")
    marker = BUILD / ".venv_deps_done"
    want = hashlib.sha256("\n".join(reqs).encode("utf-8")).hexdigest()
    if not marker.is_file() or marker.read_text().strip() != want:
        log("installing build venv dependencies ...")
        run([venv_py, "-m", "pip", "install", "--disable-pip-version-check", *reqs], check=True)
        run([venv_py, "-m", "pip", "install", "--disable-pip-version-check",
             "nuitka", "zstandard"], check=True)
        marker.write_text(want)
    return venv_py


def resolve_pins(venv_py: Path) -> list[str]:
    """Exact pins for the shipped backend/requirements.txt.

    Produces a fully-pinned MINIMAL closure: one pip dry-run over the repo's
    whole runtime requirement list, constrained to (a) the versions the build
    venv compiles and smoke-tests against (`pip freeze`), (b) the proven
    sentence-transformers subtree (STREE_PROVEN, not installed in the venv),
    and (c) torch's +cpu build from PyTorch's CPU-only index (picked via a
    metadata-only version query -- highest stable 2.x, matching what the
    default index would have chosen). Every package in the dry-run report gets
    pinned; packages the venv merely has installed but the compiled backend
    (directly or transitively) never needs are dropped, and the CPU build
    removes the ~2.9 GB CUDA wheel. Full-transitive pinning keeps the user's
    install deterministic and without resolver backtracking.
    """
    r = run([venv_py, "-m", "pip", "freeze", "--disable-pip-version-check"],
            quiet=True, capture_output=True, text=True)
    if r.returncode != 0:
        die("pip freeze failed in build venv")
    installed = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if line and "==" in line:
            installed.append(line)

    # constraint file keeps the resolution pinned to the proven versions,
    # plus the proven sentence-transformers subtree (not in the venv) and
    # nothing else -- the dry-run below decides what makes the cut.
    constraints_lines = [l for l in installed
                         if l.split("==")[0].lower().replace("_", "-") not in PINS_EXCLUDE]
    constraints_lines += [f"{k}=={v}" for k, v in STREE_PROVEN.items()]
    if len(constraints_lines) <= len(STREE_PROVEN):
        die("build venv constraints list is empty")
    constraints = BUILD / "constraints_vfreeze.txt"
    constraints.write_text("\n".join(constraints_lines) + "\n", encoding="utf-8")
    report_path = BUILD / "resolve_report.json"
    top = _requirements_from_repo()
    # metadata-only query of the CPU-only wheel index (never guess the version)
    cpu_versions = [v.split('+', 1)[0] for v in _pip_index_versions(venv_py, "torch", CPU_TORCH_INDEX)
                    if re.fullmatch(r"2\.\d+\.\d+(\+cpu)?", v)]
    cpu_versions = sorted(set(cpu_versions), key=lambda s: [int(x) for x in s.split('.')], reverse=True)
    if not cpu_versions:
        die(f"{CPU_TORCH_INDEX} carries no stable 2.x torch builds "
            f"(got: {_pip_index_versions(venv_py, 'torch', CPU_TORCH_INDEX)[:8]})")
    cpu_torch_req = f"torch=={cpu_versions[0]}+cpu"
    log(f"CPU index torch 2.x: {cpu_versions} -> pinning {cpu_torch_req}")
    log(f"resolving fully-pinned closure of {len(top)} top-level deps "
        f"(dry-run, CPU torch) ...")
    r = run([venv_py, "-m", "pip", "install", "--dry-run", "--ignore-installed",
             "--quiet", "--disable-pip-version-check", "--only-binary=:all:",
             "--retries", "5", "--timeout", "60",
             "-c", str(constraints), "--extra-index-url", CPU_TORCH_INDEX,
             "--report", str(report_path), *top, cpu_torch_req],
            quiet=True, capture_output=True, text=True)
    if r.returncode != 0:
        die(f"pip dry-run resolution failed:\n{r.stdout}\n{r.stderr}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    closed = {}
    for item in report.get("install", []):
        meta = item.get("metadata", {})
        name = (meta.get("name") or item.get("download_info", {}).get("name", "")).lower().replace("_", "-")
        version = meta.get("version") or ""
        if not name or not version or name in PINS_EXCLUDE or name == "pip":
            continue
        closed[name] = version
    if not closed:
        die("no closure resolved")
    if not closed.get("torch", "").endswith("+cpu"):
        die(f"torch pin is not the +cpu CPU-only build: {closed.get('torch')!r}")
    pins = sorted((f"{name}=={version}" for name, version in closed.items()), key=str.lower)
    if len(pins) < 40:
        die(f"closure suspiciously small: {len(pins)} packages")
    lines = [
        "# torch comes from PyTorch's CPU-only wheel index: the app embeds text on",
        "# CPU only, so the +cpu build is everything it needs -- the default PyPI",
        "# index's win_amd64 torch wheel would additionally bundle ~2.9 GB of CUDA",
        "# DLLs. Everything below is fully pinned; pip must not backtrack.",
        f"--extra-index-url {CPU_TORCH_INDEX}",
        *pins,
    ]
    (BUILD / "req_pins.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"pins: {len(pins)} packages (torch {closed['torch']}, closure-minimal) "
        f"-> _build/req_pins.txt")
    return pins


# ---------------------------------------------------------------- stage layout
def reset_stage() -> None:
    """The stage is ALWAYS rebuilt from scratch so no stale text/bat/config/pyi
    artifact can ever leak into a zip. Compiled .pyd artifacts are cached under
    pyds/<stem>/<sha256>/ by compile_modules(), so this is cheap."""
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "backend").mkdir(parents=True)


def stage_root() -> None:
    log("staging frontend/ + HOW TO RUN.txt + packaged start.bat ...")
    shutil.copytree(REPO / "frontend", STAGE / "frontend")
    (STAGE / "HOW TO RUN.txt").write_text(HOW_TO_RUN, encoding="utf-8", newline="\r\n")
    _derive_packaged_start_bat()


def _derive_packaged_start_bat() -> None:
    bat = (REPO / "start.bat").read_text(encoding="utf-8", errors="replace")

    # 1) drop the dev-only ".env file" check (secrets are baked into bpk)
    marker = (
        'if not exist "%~dp0.env" (\n'
        '    echo WARNING: no .env file found next to this script -- the app needs one\n'
        '    echo ^(DB_URL and LLM settings^) to start. Ask whoever gave you this folder for it.\n'
        '    pause\n'
        '    exit /b 1\n'
        ')\n'
    )
    if marker not in bat:
        die("start.bat .env-warning block not found -- update _derive_packaged_start_bat()")
    bat = bat.replace(marker, "")

    # 2) replace the dev python/venv/pip acquisition with the packaged section
    b_start = "rem ==== PACKAGING BLOCK-START (replaced in the package) ====\n"
    b_end = "rem ==== PACKAGING BLOCK-END ====\n"
    if b_start not in bat or b_end not in bat:
        die("start.bat packaging markers missing -- add the PACKAGING BLOCK-START/END comments")
    head, rest = bat.split(b_start, 1)
    _, tail = rest.split(b_end, 1)
    block_path = Path(__file__).resolve().parent / "packaged_block.bat"
    block = block_path.read_text(encoding="utf-8")
    bat = head + block + tail
    (STAGE / "start.bat").write_text(bat, encoding="utf-8", newline="\r\n")


def stage_backend_sources() -> None:
    log("staging backend sources ...")
    shutil.copy2(BACKEND / "source_registry.json", STAGE / "backend" / "source_registry.json")
    cat = BACKEND / "catalog.sqlite"
    if not cat.is_file():
        die("backend/catalog.sqlite missing -- run backend/scripts/export_catalog_sqlite.py first")
    shutil.copy2(cat, STAGE / "backend" / "catalog.sqlite")
    shutil.copy2(BUILD / "req_pins.txt", STAGE / "backend" / "requirements.txt")
    for py in BACKEND.glob("*.py"):
        shutil.copy2(py, STAGE / "backend" / py.name)


def read_env_file() -> dict[str, str]:
    env = {}
    for line in (REPO / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def generate_bpk(user_label: str = "unlabeled", proxy_token: str = "", direct: bool = False) -> None:
    """Bake the .env secrets into one small module whose compiled form carries
    them. Each build generates a random XOR mask; values are stored as
    b64(xor(plaintext)) and the mask separately. Neither the plaintext nor the
    old constant-encodings occur in text or binary, and the b64 blob is
    garbage without this build's mask (NOT crypto -- a debugger on the running
    process still wins; the point is no greppable artifacts).

    LLM access has two modes:
      * proxy (default): the build bakes this user's per-install proxy token
        (MONITOR_PROXY_TOKEN, minted with `npm run token` in monitor/) and the
        DeepSeek key is NOT baked at all -- it lives only on the Vercel proxy,
        so it can never be extracted from a user's PC.
      * direct (--direct): legacy behaviour, bakes DEEPSEEK_API_KEY.

    Usage-monitoring keys (see monitor/ and backend/telemetry.py) are baked the
    same way: MONITOR_URL and MONITOR_INGEST_KEY come from the repo .env,
    MONITOR_USER_LABEL is the --user CLI value baked as this distribution's
    label. Without MONITOR_URL telemetry is a silent no-op, so the keys are
    then deliberately NOT baked at all."""
    env = read_env_file()
    # CLI/process-env value wins over .env; either way it is only baked via the validated path below.
    proxy_token = (proxy_token or env.get("MONITOR_PROXY_TOKEN", "")).strip()
    env.pop("MONITOR_PROXY_TOKEN", None)
    if direct:
        if "DEEPSEEK_API_KEY" not in env:
            die("local .env must contain DEEPSEEK_API_KEY for a --direct build")
        log("WARNING: --direct build: the DeepSeek key WILL be baked into this package.")
    else:
        if not env.get("MONITOR_URL", ""):
            die("proxy build needs MONITOR_URL (the deployed monitor/Vercel URL) in the repo .env")
        if not re.fullmatch(r"cmi_[A-Za-z0-9_-]{20,}", proxy_token or ""):
            die("proxy build needs this user's token: pass --proxy-token cmi_... or set "
                "MONITOR_PROXY_TOKEN in the environment (mint one: cd monitor && "
                "npm run token -- create \"<Name>\" <daily budget USD>). "
                "Use --direct only for an explicit legacy build that ships the DeepSeek key.")
        env.pop("DEEPSEEK_API_KEY", None)  # never ship the real key in proxy mode
        # Telemetry authenticates with the install's own token too, so the shared ingest key (one value
        # that every PC would otherwise carry and could forge events with) is NOT shipped either.
        env.pop("MONITOR_INGEST_KEY", None)
        env["MONITOR_PROXY_TOKEN"] = proxy_token
    # The packaged backend never touches Postgres (it ships catalog.sqlite), so
    # DB credentials are deliberately NOT baked into the distribution at all.
    for k in list(env):
        if k.startswith("DB_") or k == "ASYNC_DB_URL":
            del env[k]

    # Usage monitoring: telemetry is a silent no-op without MONITOR_URL, so if it is
    # missing warn loudly and bake none of the MONITOR_* keys (an ingest key with no
    # endpoint to use it against has no business shipping to an end-user's PC).
    monitor_keys = ("MONITOR_URL", "MONITOR_INGEST_KEY", "MONITOR_USER_LABEL")
    if not env.get("MONITOR_URL", ""):
        log("=" * 64)
        log("WARNING: .env has no MONITOR_URL -- this build will NOT report usage")
        log("to the dashboard (no telemetry will be sent). To enable it, set")
        log("MONITOR_URL and MONITOR_INGEST_KEY in the repo .env and rebuild.")
        log("=" * 64)
        for k in monitor_keys:
            env.pop(k, None)
    else:
        if direct and not env.get("MONITOR_INGEST_KEY", ""):
            log("WARNING: --direct build with MONITOR_URL but no MONITOR_INGEST_KEY -- the")
            log("dashboard will reject this build's events (HTTP 401). Set both in .env.")
        env["MONITOR_USER_LABEL"] = (user_label or "unlabeled")[:100]

    mask = os.urandom(32)
    out = [
        "# GENERATED by packaging/build_package.py -- do not edit or commit.",
        "import base64",
        "",
        f"_MASK = {base64.b64encode(mask).decode('ascii')!r}",
        "_VALUES = {",
    ]
    for k, v in sorted(env.items()):
        xored = bytes(c ^ mask[i % len(mask)] for i, c in enumerate(v.encode("utf-8")))
        out.append(f"    {k!r}: {base64.b64encode(xored).decode('ascii')!r},")
    out += [
        "}",
        "",
        "def get(key: str) -> str:",
        "    try:",
        "        raw = base64.b64decode(_VALUES[key])",
        "    except KeyError:",
        "        raise KeyError(f'no baked value for {key!r}')",
        "    mask = base64.b64decode(_MASK)",
        "    return bytes(b ^ mask[i % len(mask)] for i, b in enumerate(raw)).decode('utf-8')",
        "",
    ]
    (STAGE / "backend" / "bpk.py").write_text("\n".join(out), encoding="utf-8")

    # round-trip self-test straight off the generated file. dont_write_bytecode
    # keeps a __pycache__/bpk.*.pyc out of the stage (it would slip past the
    # extension filters and could even ship a 3.14 pyc otherwise).
    sys.dont_write_bytecode = True
    import importlib.util
    spec = importlib.util.spec_from_file_location("_bpk_selftest", STAGE / "backend" / "bpk.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for k, v in env.items():
        if m.get(k) != v:
            die(f"bpk round-trip failed for {k}")


def patch_config_fallbacks(direct: bool = False) -> None:
    """Rewrite the STAGED config.py (the repo copy is never touched).

    proxy mode (default): the LLM client is pointed at <MONITOR_URL>/api/v1 and
    authenticates with the baked per-install token. Environment variables are
    deliberately NOT consulted for these two values, so a stray DEEPSEEK_API_KEY
    or LLM_BASE_URL on an end-user PC can't redirect or bypass the proxy. The
    client timeout drops from 600 s to 130 s: the proxy answers within 110 s
    (its own upstream timeout) and real calls peak around 48 s.

    direct mode: secrets fall back to the baked bpk module; env vars still win.

    DB_URL stays a plain env lookup either way: the package ships catalog.sqlite
    and bakes no DB credential."""
    cfg = STAGE / "backend" / "config.py"
    text = cfg.read_text(encoding="utf-8")
    text = text.replace(
        "from dotenv import load_dotenv",
        "from dotenv import load_dotenv\n\ntry:\n    import bpk as _bpk\nexcept ImportError:\n    _bpk = None",
    )
    old_key = 'LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")'
    if old_key not in text:
        die("config.py anchors changed -- update patch_config_fallbacks()")
    if direct:
        new_key = 'LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or (_bpk.get("DEEPSEEK_API_KEY") if _bpk else "")'
        text = text.replace(old_key, new_key)
    else:
        old_url = 'LLM_BASE_URL = "https://api.deepseek.com"'
        old_timeout = 'LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "600"))'
        for anchor in (old_url, old_timeout):
            if anchor not in text:
                die("config.py anchors changed -- update patch_config_fallbacks()")
        text = text.replace(old_url, 'LLM_BASE_URL = _bpk.get("MONITOR_URL").rstrip("/") + "/api/v1"')
        text = text.replace(old_key, 'LLM_API_KEY = _bpk.get("MONITOR_PROXY_TOKEN")')
        text = text.replace(old_timeout, 'LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "130"))')
    cfg.write_text(text, encoding="utf-8")


def assert_no_deepseek_key(direct: bool) -> None:
    """Proxy builds must not contain the real DeepSeek key in ANY staged file
    (plaintext, base64 or hex), including the compiled modules. Scans the
    package minus pyruntime (third-party code, thousands of files)."""
    if direct:
        return
    key = read_env_file().get("DEEPSEEK_API_KEY", "")
    if len(key) < 8:
        return
    raw = key.encode()
    needles = {
        "plaintext": raw,
        "base64": base64.b64encode(raw),
        "hex": raw.hex().encode(),
        "reversed": raw[::-1],
    }
    hits = []
    for p in STAGE.rglob("*"):
        if not p.is_file() or "pyruntime" in p.relative_to(STAGE).parts:
            continue
        data = p.read_bytes()
        for form, needle in needles.items():
            if needle in data:
                hits.append(f"{p.relative_to(STAGE)} ({form})")
    if hits:
        die("proxy build still contains the DeepSeek key: " + ", ".join(hits))
    log("no DeepSeek key found in the staged package (proxy build)")


# ---------------------------------------------------------------- compile
def compile_modules(venv_py: Path) -> None:
    """Compile every staged backend .py to a Nuitka .pyd, reusing cached
    artifacts for every module whose staged source hash already has a cache
    entry (pyds/<stem>/<sha256>/) even though the stage was just wiped."""
    modules = sorted(p for p in (STAGE / "backend").glob("*.py"))
    if not modules:
        die("no backend .py staged")
    log(f"compiling {len(modules)} backend modules to native .pyd (first run downloads "
        f"MinGW ~100MB, then compiles; expect 5-25 minutes) ...")
    out_dir = STAGE / "backend"
    for i, mod in enumerate(modules, 1):
        stem = mod.stem
        h = sha256_file(mod)
        cached = PYDS / stem / h
        if cached.is_dir() and any(cached.glob("*.pyd")):
            for pyd in cached.glob("*.pyd"):
                shutil.copy2(pyd, out_dir / pyd.name)
            log(f"  [{i}/{len(modules)}] {stem}: cache hit ({h[:12]})")
            continue
        log(f"  [{i}/{len(modules)}] nuitka --module {stem}")
        r = run([
            venv_py, "-m", "nuitka", "--module", mod,
            "--mingw64", "--assume-yes-for-downloads", "--lto=no",
            "--nofollow-import-to=*",
            f"--output-dir={out_dir}",
            "--remove-output",
        ])
        if r.returncode != 0:
            die(f"nuitka failed on {stem} (exit {r.returncode}) -- see output above")
        # Nuitka writes <stem>[.<abi>].pyd (+ a .pyi stub we never ship). Only
        # files whose name starts "<stem>." belong to THIS module (so a cache
        # for "prompts" cannot swallow "prompts_ollama").
        produced = [p for p in out_dir.glob("*.pyd")
                    if p.name == f"{stem}.pyd" or p.name.startswith(f"{stem}.")]
        if not produced:
            die(f"no .pyd produced for {stem}")
        cached.mkdir(parents=True, exist_ok=True)
        for pyd in produced:
            shutil.copy2(pyd, cached / pyd.name)
        for pyd in produced:
            shutil.copy2(cached / pyd.name, out_dir / pyd.name)
        # keep the cache bounded: keep ONLY the current hash per stem
        for other in (PYDS / stem).iterdir():
            if other.is_dir() and other.name != h:
                shutil.rmtree(other, ignore_errors=True)
    log("compiled modules OK")


def final_sweep() -> None:
    """Nothing except .pyd modules, catalog.sqlite, source_registry.json and
    requirements.txt may remain under backend/ -- no .py, .pyi, .pyc, .env,
    __pycache__, build dirs, or dev junk from any earlier stage."""
    out_dir = STAGE / "backend"
    for p in sorted(out_dir.rglob("*"), reverse=True):
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            continue
        if p.suffix.lower() in BACKEND_ALLOWED_SUFFIX or p.name in BACKEND_ALLOWED_NAMES:
            continue
        p.unlink()
        log(f"  swept {p.name}")
    bad = [p for p in out_dir.rglob("*")
           if p.suffix.lower() in {".py", ".pyi", ".pyc", ".env"}
           or p.name == "__pycache__"]
    if bad:
        die(f"final sweep incomplete: {[str(p) for p in bad]}")


def stage_pyruntime() -> None:
    dest = STAGE / "pyruntime"
    if dest.is_dir():
        shutil.rmtree(dest)
    log("copying bundled Python runtime into package ...")
    shutil.copytree(
        PYRUNTIME, dest,
        ignore=shutil.ignore_patterns("__pycache__", "*.log", "*.pyc"),
    )


# ---------------------------------------------------------------- audit
def audit_stage() -> None:
    log("auditing staged package ...")
    backend = STAGE / "backend"
    mods = len(list(backend.glob("*.pyd")))
    try:
        txt = (STAGE / "HOW TO RUN.txt").read_text(encoding="utf-8")
    except FileNotFoundError:
        txt = ""
    problems = []
    if not txt.strip():
        problems.append("HOW TO RUN.txt missing/empty")
    if not (STAGE / "start.bat").is_file():
        problems.append("start.bat missing")
    else:
        bat = (STAGE / "start.bat").read_text(encoding="utf-8", errors="replace")
        if "pyruntime" not in bat:
            problems.append("packaged start.bat does not reference pyruntime")
        if "if not exist \"%~dp0.env\"" in bat:
            problems.append("packaged start.bat still checks for .env")
        if "--retries 5" not in bat:
            problems.append("packaged start.bat lacks --retries 5")
    if not (STAGE / "frontend" / "index.html").is_file():
        problems.append("frontend/index.html missing")
    for required in ("catalog.sqlite", "source_registry.json", "requirements.txt"):
        if not (backend / required).is_file():
            problems.append(f"backend/{required} missing")
    if mods == 0:
        problems.append("no .pyd modules in backend/")
    stray = sorted(p.relative_to(backend) for p in backend.rglob("*")
                   if p.is_file() and p.suffix.lower() not in {".pyd"}
                   and p.name not in BACKEND_ALLOWED_NAMES)
    if stray:
        problems.append(f"stray backend files: {[str(s) for s in stray]}")
    pyfiles = [p.relative_to(backend) for p in backend.rglob("*")
               if p.is_file() and p.name.lower() == ".env"]
    if pyfiles:
        problems.append(f"backend ships source/stub/env files: {[str(s) for s in pyfiles]}")
    rt = STAGE / "pyruntime"
    if not (rt / "python.exe").is_file() or not (rt / "Lib" / "site-packages" / "pip").is_dir():
        problems.append("pyruntime missing python.exe or pip")
    else:
        files = [f for f in rt.rglob("*") if f.is_file()]
        size = sum(f.stat().st_size for f in files)
        log(f"pyruntime: {len(files)} files, {size / 1e6:.1f} MB (clean embeddable build)")
        if len(files) > 1500:
            problems.append(f"pyruntime suspiciously large: {len(files)} files")
    if (STAGE / ".env").is_file() or (backend / ".env").is_file():
        problems.append(".env present in package")
    if problems:
        die("stage audit failed:\n  " + "\n  ".join(problems))
    log(f"stage audit OK ({mods} compiled modules, requirements.txt present, "
        "HOW TO RUN.txt present, pyruntime clean)")


# ---------------------------------------------------------------- smoke test
def smoke_test(venv_py: Path, direct: bool = False) -> None:
    log("smoke test: importing compiled backend + /health probe ...")
    if direct:
        llm_checks = "assert config.LLM_API_KEY, 'LLM_API_KEY did not resolve'\n"
    else:
        # proxy build: the client must point at the proxy with a cmi_ token, and the
        # baked secrets module must hold NO DeepSeek key (KeyError proves it is absent).
        llm_checks = (
            "assert config.LLM_API_KEY.startswith('cmi_'), 'LLM_API_KEY is not a proxy token'\n"
            "assert config.LLM_BASE_URL.endswith('/api/v1'), config.LLM_BASE_URL\n"
            "assert 'deepseek.com' not in config.LLM_BASE_URL, config.LLM_BASE_URL\n"
            "import bpk\n"
            "try:\n"
            "    bpk.get('DEEPSEEK_API_KEY')\n"
            "    raise SystemExit('bpk still holds DEEPSEEK_API_KEY')\n"
            "except KeyError:\n"
            "    pass\n"
        )
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import main\n"
        "from fastapi.testclient import TestClient\n"
        "r = TestClient(main.app).get('/health')\n"
        "assert r.status_code == 200, r.status_code\n"
        "assert r.json() == {'status': 'ok'}, r.json()\n"
        "import config, reports\n"
        + llm_checks +
        "cat = reports.catalog()\n"
        "assert len(cat) > 100, len(cat)\n"
        "assert reports.categories(), 'categories empty'\n"
        "print(f'smoke OK: health=200, LLM config OK, catalog has {len(cat)} reports')"
    ) % str(STAGE / "backend")
    r = run([venv_py, "-c", code], cwd=str(STAGE),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if r.returncode != 0:
        die("smoke test failed -- inspect output above")


# ---------------------------------------------------------------- zip
def make_zip(user_label: str = "unlabeled") -> Path:
    DIST.mkdir(exist_ok=True)
    day = __import__("datetime").date.today().strftime("%Y%m%d")
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", user_label).strip("_-")[:40] or "unlabeled"
    n = 1
    while (DIST / f"cmi-blog-agent-{day}-{n}-{slug}.zip").exists():
        n += 1
    out = DIST / f"cmi-blog-agent-{day}-{n}-{slug}.zip"
    log(f"zipping {out.name} ...")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(STAGE.rglob("*")):
            if path.is_file():
                z.write(path, str(path.relative_to(STAGE)))
    log(f"zip size: {out.stat().st_size / 1e6:.1f} MB  ->  {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true", help="stage only, don't produce dist zip")
    ap.add_argument("--user", dest="user_label", default="unlabeled", metavar="LABEL",
                    help="End-user label baked into this distribution (who this copy belongs "
                         "to) -- shown on the dashboard and in the zip filename. Pass it for "
                         "every distribution build; 'unlabeled' makes installs hard to tell apart.")
    ap.add_argument("--proxy-token", dest="proxy_token", default=os.environ.get("MONITOR_PROXY_TOKEN", ""),
                    metavar="TOKEN",
                    help="This user's LLM-proxy token (cmi_...; mint with `npm run token` in monitor/). "
                         "Also read from the MONITOR_PROXY_TOKEN environment variable (preferred: keeps "
                         "it out of shell history). Required unless --direct.")
    ap.add_argument("--direct", action="store_true",
                    help="LEGACY: ship the DeepSeek key itself in the package instead of using the "
                         "proxy. Not recommended: the key can be extracted from the user's PC.")
    args = ap.parse_args()

    ensure_pytool()
    ensure_pyruntime()
    venv_py = ensure_build_venv()
    resolve_pins(venv_py)

    reset_stage()
    stage_root()
    stage_backend_sources()
    generate_bpk(args.user_label, args.proxy_token, args.direct)
    patch_config_fallbacks(args.direct)
    stage_pyruntime()
    compile_modules(venv_py)
    final_sweep()
    audit_stage()
    assert_no_deepseek_key(args.direct)
    smoke_test(venv_py, args.direct)

    if args.no_zip:
        log(f"staged at {STAGE} (no zip)")
        return
    if args.user_label == "unlabeled":
        log("WARNING: building a distribution without --user LABEL; installs will show as "
            "'unlabeled' on the dashboard. Pass --user \"Customer Name\" to label this copy.")
    make_zip(args.user_label)
    log("DONE")


if __name__ == "__main__":
    main()
