"""On-disk store for finished runs (one pickle per run token, under ``runs/``).

A run lives in ``main._RUNS`` only: it is gone when the server restarts and it expires after
``main.RUN_TTL_S``. A finished draft, though, is still worth editing — the canvas editor calls
``main._persist_run`` after every edit, and ``main._get_run`` rebuilds the run from here when the
token is no longer in memory, so an editor can reopen yesterday's run and keep working on it.

Only the fields worth keeping are written (``FIELDS``): the article, the sponsor paragraph, the
report facts, the plan, the draft itself. The logger, its log file, background futures and timers
are per-process and never saved.
"""

import logging
import os
import pickle
import re
import tempfile
import time
from pathlib import Path

log = logging.getLogger("revamp.run_store")

RUNS_DIR = Path(__file__).parent / "runs"

# The RunState fields worth saving — everything another step (or the canvas editor) reads back,
# minus the per-process bits: logger, log_path, created_at, claim_future, step2_timer.
FIELDS: tuple[str, ...] = (
    "article", "sponsor", "report", "candidates", "orient", "plan", "plan_inputs",
    "selection", "hits", "evidence", "fact_sheet", "clients", "filename", "title",
    "sections", "faq", "evidence_citations", "longtails", "report_mode", "report_kind",
)

# The canvas editor's state. It may not exist on RunState yet, so it is saved by name only when
# the run actually has it (see values()).
OPTIONAL_FIELDS: tuple[str, ...] = ("canvas",)

_TOKEN_RE = re.compile(r"[0-9a-f]{16,64}")


def _path(token: str) -> Path | None:
    """``runs/<token>.pkl``, or None for anything that isn't a plain hex token (never build a path
    from an unvalidated string: a token such as ``../x`` would escape the directory)."""
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        return None
    return RUNS_DIR / f"{token}.pkl"


def values(run) -> dict:
    """The saveable fields present on `run` — missing ones are simply left out (defaults then
    apply again on restore)."""
    data = {}
    for name in FIELDS + OPTIONAL_FIELDS:
        if hasattr(run, name):
            data[name] = getattr(run, name)
    return data


def save(token: str, run) -> bool:
    """Pickle the run's saveable fields to ``runs/<token>.pkl``, atomically. Never raises: a failed
    save only costs the restart/expiry restore, and the run in memory is untouched."""
    path = _path(token)
    if path is None:
        log.warning(f"[run_store] refusing to save run with an invalid token {token!r}")
        return False
    logger = getattr(run, "logger", None) or log
    tmp_path = None
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        data = values(run)
        with tempfile.NamedTemporaryFile(
            dir=RUNS_DIR, prefix=f".{token}.", suffix=".tmp", delete=False
        ) as fh:
            tmp_path = Path(fh.name)
            pickle.dump(data, fh, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp_path, path)
        return True
    except Exception:
        logger.warning(f"[run_store] could not save run {token[:8]}…", exc_info=True)
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
        return False


def load(token: str) -> dict | None:
    """The saved fields for `token`, or None when the token is invalid, the file is gone, or it
    cannot be read (a pickle from an older code version, a half-written file, ...)."""
    path = _path(token)
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as fh:
            data = pickle.load(fh)
    except Exception:
        log.warning(f"[run_store] could not read the saved run {token[:8]}…", exc_info=True)
        return None
    return data if isinstance(data, dict) else None


def cleanup(max_age_days: int = 14) -> int:
    """Delete saved runs older than `max_age_days`; returns how many files went."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        files = list(RUNS_DIR.glob("*.pkl"))
    except OSError:
        return 0
    for path in files:
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        log.info(f"[run_store] cleaned up {removed} saved run(s) older than {max_age_days} days")
    return removed
