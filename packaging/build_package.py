"""Builds the distributable zip of the CMI Blog Revamp Agent.

What this produces (dist/cmi-blog-agent-<date>-<n>.zip):
  - backend/ with every .py module compiled to native .pyd extensions (Nuitka,
    compiled for Python 3.12) -- no readable Python source ships
  - one compiled secrets module (backend/bpk) built from the local .env, so the
    DB URL and DEEPSEEK_API_KEY are not in any plaintext file
  - frontend/ untouched, start.bat with the .env check removed
  - pyruntime/ = a full Python 3.12.7 install copied into the package, so
    end-users never download/install Python (start.bat's pyruntime path hits first)

End-user flow is unchanged: extract the zip anywhere, double-click start.bat.

Usage (any Python >= 3.10 on the build machine):

    python packaging/build_package.py          # full build + zip
    python packaging/build_package.py --no-zip # stage only (for inspection)

The whole thing is idempotent; every step resumes where it left off. Heavy
caches live under packaging/_build: the Python installer download, the build
venv (pip deps), and the stage. First run takes a while (pip + compiler
download); reruns are fast.
"""

from __future__ import annotations

import argparse
import base64
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
BUILD = Path(__file__).resolve().parent / "_build"
PYRUNTIME = BUILD / "pyruntime"
BUILD_VENV = BUILD / "venv"
STAGE = BUILD / "stage"
DIST = REPO / "dist"
NUIKTA_OUT = STAGE / "backend"

PY_INSTALLER_URL = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"

# start.bat's same fallback list: if the build machine already has a 3.12
# install that the installer refuses to duplicate (exit 1638), copy THAT
# install into pyruntime -- same file layout, same ABI.
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
    "docs", "dist", "packaging", ".env", "content.html", "out_teen.md",
    ".gitignore", ".claude",
}
ROOT_EXCLUDE_GLOBS = ("*.docx", "*.xlsx", "*.md", "Revised_*")


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    show = not kwargs.pop("quiet", False)
    if show:
        print("  $", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], **kwargs)


def die(msg: str) -> None:
    raise SystemExit(f"[build] FATAL: {msg}")


# ---------------------------------------------------------------- step 1: build Python
def ensure_python(build_tools_only: bool = False) -> None:
    """Pyruntime must contain a Python 3.12 interpreter; both the packages we
    compile and the shipped runtime must be the same minor version (3.12)."""
    if (PYRUNTIME / "python.exe").is_file():
        return
    installer = BUILD / "installers" / PY_INSTALLER_URL.rsplit("/", 1)[-1]
    BUILD.mkdir(parents=True, exist_ok=True)
    if not installer.is_file():
        log(f"downloading {PY_INSTALLER_URL} ...")
        installer.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(PY_INSTALLER_URL, installer)
        log(f"saved {installer} ({installer.stat().st_size / 1e6:.1f} MB)")
    log(f"installing Python 3.12.7 into {PYRUNTIME} ...")
    r = run([
        installer, "/quiet", "InstallAllUsers=0", "PrependPath=0",
        "Include_test=0", "Include_launcher=0", f"TargetDir={PYRUNTIME}",
    ])
    if (PYRUNTIME / "python.exe").is_file():
        log("pyruntime ready")
    elif r.returncode == 1638:
        # 3.12.7 is already installed as a Windows product (another instance
        # registered under the same version) -- the installer refuses to put a
        # second copy anywhere. Reuse the existing per-user install instead.
        src = next((d for d in EXISTING_312_DIRS if (d / "python.exe").is_file()), None)
        if src is None:
            die("installer exit 1638 but no existing Python 3.12 found to reuse")
        log("installer says Python 3.12 already exists on this machine -- "
            f"copying {src} into pyruntime instead ...")
        shutil.copytree(src, PYRUNTIME, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("unins*", "__pycache__", "*.log"))
        # pyvenv-style relative refs don't exist in a base install, but clean
        # build caches keep the shipped copy small.
        for junk in ("LICENSE", "UNINST.EXE"):
            pass
        if not (PYRUNTIME / "python.exe").is_file():
            die("pyruntime copy failed")
        log("pyruntime ready (reused existing install)")
    else:
        die(f"python installer failed (exit {r.returncode})")


# ---------------------------------------------------------------- step 2: build venv
def ensure_build_venv() -> Path:
    BUILD_VENV.mkdir(parents=True, exist_ok=True)
    venv_py = BUILD_VENV / "Scripts" / "python.exe"
    if not venv_py.is_file():
        log("creating build venv (pip dependencies compile with this) ...")
        run([PYRUNTIME / "python.exe", "-m", "venv", BUILD_VENV], check=True)
    # Runtime deps (minus torch-heavy sentence-transformers, not needed to
    # compile or smoke-test) plus the compiler toolchain.
    reqs = [
        line.split("#", 1)[0].strip()
        for line in (BACKEND / "requirements.txt").read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
        and not line.strip().startswith("sentence-transformers")
        and not line.strip().startswith("psycopg2")
    ]
    marker = BUILD / ".venv_deps_done"
    if not marker.is_file():
        log("installing build venv dependencies ...")
        run([venv_py, "-m", "pip", "install", "--disable-pip-version-check", *reqs], check=True)
        run([venv_py, "-m", "pip", "install", "--disable-pip-version-check",
             "nuitka", "zstandard"], check=True)
        marker.write_text("ok")
    return venv_py


# ---------------------------------------------------------------- stage layout
def reset_stage() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "backend").mkdir(parents=True)


def stage_root() -> None:
    log("staging frontend/ + start.bat ...")
    shutil.copytree(REPO / "frontend", STAGE / "frontend")

    (STAGE / "HOW TO RUN.txt").write_text(
        "CMI Blog Revamp Agent\r\n"
        "=====================\r\n"
        "How to run:\r\n"
        "  1. Extract this whole folder to anywhere on the PC (e.g. Desktop). Keep all files together.\r\n"
        "  2. Double-click  start.bat\r\n"
        "  3. The app opens in your browser at http://127.0.0.1:8000\r\n"
        "\r\n"
        "Notes:\r\n"
        "  - The FIRST run installs some components (needs internet) and can take a few minutes.\r\n"
        "  - Every later run starts in seconds.\r\n"
        "  - Closing the browser tab shuts the app down after a few seconds.\r\n"
        "  - Run start.bat again any time to start a fresh copy.\r\n",
        encoding="utf-8",
    )

    bat = (REPO / "start.bat").read_text(encoding="utf-8", errors="replace")
    marker = (
        'if not exist "%~dp0.env" (\n'
        '    echo WARNING: no .env file found next to this script -- the app needs one\n'
        '    echo ^(DB_URL and LLM settings^) to start. Ask whoever gave you this folder for it.\n'
        '    pause\n'
        '    exit /b 1\n'
        ')\n'
    )
    if marker not in bat:
        die("start.bat .env-warning block not found -- update the marker in this script")
    bat = bat.replace(marker, "")
    (STAGE / "start.bat").write_text(bat, encoding="utf-8")


def stage_backend_sources() -> None:
    log("staging backend sources ...")
    shutil.copy2(BACKEND / "source_registry.json", STAGE / "backend" / "source_registry.json")
    cat = BACKEND / "catalog.sqlite"
    if not cat.is_file():
        die("backend/catalog.sqlite missing -- run backend/scripts/export_catalog_sqlite.py first")
    shutil.copy2(cat, STAGE / "backend" / "catalog.sqlite")
    for py in BACKEND.glob("*.py"):
        shutil.copy2(py, STAGE / "backend" / py.name)


def generate_bpk() -> None:
    """Bake the .env secrets into one small module whose compiled form carries
    them. Each build generates a random XOR mask; values are stored as
    b64(xor(plaintext)) and the mask separately. Neither the plaintext nor the
    old constant-encodings occur in text or binary, and the b64 blob is
    garbage without this build's mask (NOT crypto -- a debugger on the running
    process still wins; the point is no greppable artifacts)."""
    env = {}
    for line in (REPO / ".env").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip('"').strip("'")
    if "DEEPSEEK_API_KEY" not in env:
        die("local .env must contain DEEPSEEK_API_KEY")
    # The packaged backend never touches Postgres (it ships catalog.sqlite), so
    # DB credentials are deliberately NOT baked into the distribution at all.
    for k in list(env):
        if k.startswith("DB_") or k == "ASYNC_DB_URL":
            del env[k]

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

    # round-trip self-test straight off the generated file
    import importlib.util
    spec = importlib.util.spec_from_file_location("_bpk_selftest", STAGE / "backend" / "bpk.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for k, v in env.items():
        if m.get(k) != v:
            die(f"bpk round-trip failed for {k}")


def patch_config_fallbacks() -> None:
    """Secrets fall back to the baked bpk module; env vars still win so a user
    who REALLY wants to override remains able (and dev keeps working). DB_URL
    stays a plain env lookup: the package ships catalog.sqlite and bakes no DB
    credential."""
    cfg = STAGE / "backend" / "config.py"
    text = cfg.read_text(encoding="utf-8")
    text = text.replace(
        "from dotenv import load_dotenv",
        "from dotenv import load_dotenv\n\ntry:\n    import bpk as _bpk\nexcept ImportError:\n    _bpk = None",
    )
    old_key = 'LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")'
    new_key = 'LLM_API_KEY = os.getenv("DEEPSEEK_API_KEY", "") or (_bpk.get("DEEPSEEK_API_KEY") if _bpk else "")'
    if old_key not in text:
        die("config.py anchors changed -- update patch_config_fallbacks()")
    text = text.replace(old_key, new_key)
    cfg.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------- compile
def compile_modules(venv_py: Path) -> None:
    modules = sorted(p for p in (STAGE / "backend").glob("*.py"))
    if not modules:
        die("no backend .py staged")
    log(f"compiling {len(modules)} backend modules to native .pyd (first run downloads "
        f"MinGW ~100MB, then compiles; expect 5-25 minutes) ...")
    out_dir = STAGE / "backend"
    for i, mod in enumerate(modules, 1):
        stem = mod.stem
        if any(out_dir.glob(f"{stem}.*win*.pyd")) or (out_dir / f"{stem}.pyd").is_file():
            log(f"  [{i}/{len(modules)}] {stem} already compiled, skipping")
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
            die(f"nuitka failed on {stem.name} (exit {r.returncode}) -- see output above")
    # nuitka writes <name>.cp312-win_amd64.pyd (or <name>.pyd); confirm all exist
    for mod in modules:
        stem = mod.stem
        if not any(out_dir.glob(f"{stem}*.pyd")):
            die(f"no .pyd produced for {stem}")
    # compiled: drop every readable source, dev fixture junk already excluded.
    # IMPORTANT: also drop Nuitka's generated .pyi stubs -- they mirror module
    # constants verbatim (baked secrets in bpk.pyi, full prompt text in
    # prompts.pyi), so shipping them would leak the secrets.
    leftovers = sorted(out_dir.glob("*.py")) + sorted(out_dir.glob("*.pyi"))
    build_tmp = out_dir / "build"  # nuitka keeps intermediate dirs sometimes
    for py in leftovers:
        py.unlink()
    if build_tmp.exists():
        shutil.rmtree(build_tmp, ignore_errors=True)
    if any(out_dir.glob("*.pyi")):
        die("stage still contains .pyi stubs after cleanup")
    log(f"compiled modules OK; {len(leftovers)} source/stub files removed from package")


def stage_pyruntime() -> None:
    dest = STAGE / "pyruntime"
    if dest.is_dir():
        return
    log("copying bundled Python runtime into package ...")
    shutil.copytree(PYRUNTIME, dest, ignore=shutil.ignore_patterns("unins*", "*.log"))


# ---------------------------------------------------------------- smoke test
def smoke_test(venv_py: Path) -> None:
    log("smoke test: importing compiled backend + /health probe ...")
    code = (
        "import sys; sys.path.insert(0, r'%s')\n"
        "import main\n"
        "from fastapi.testclient import TestClient\n"
        "r = TestClient(main.app).get('/health')\n"
        "assert r.status_code == 200, r.status_code\n"
        "assert r.json() == {'status': 'ok'}, r.json()\n"
        "import config, reports\n"
        "assert config.LLM_API_KEY, 'LLM_API_KEY did not resolve'\n"
        "cat = reports.catalog()\n"
        "assert len(cat) > 100, len(cat)\n"
        "assert reports.categories(), 'categories empty'\n"
        "print(f'smoke OK: health=200, LLM_API_KEY resolves, catalog has {len(cat)} reports')"
    ) % str(STAGE / "backend")
    r = run([venv_py, "-c", code], cwd=str(STAGE))
    if r.returncode != 0:
        die("smoke test failed -- inspect output above")


# ---------------------------------------------------------------- zip
def make_zip() -> Path:
    DIST.mkdir(exist_ok=True)
    day = __import__("datetime").date.today().strftime("%Y%m%d")
    n = 1
    while (DIST / f"cmi-blog-agent-{day}-{n}.zip").exists():
        n += 1
    out = DIST / f"cmi-blog-agent-{day}-{n}.zip"
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
    args = ap.parse_args()

    ensure_python()
    venv_py = ensure_build_venv()

    if not (STAGE / "frontend").is_dir() or not any((STAGE / "backend").glob("*.py")):
        reset_stage()
        stage_root()
        stage_backend_sources()
        generate_bpk()
        patch_config_fallbacks()
    stage_pyruntime()
    compile_modules(venv_py)
    smoke_test(venv_py)

    if args.no_zip:
        log(f"staged at {STAGE} (no zip)")
        return
    make_zip()
    log("DONE")


if __name__ == "__main__":
    main()
