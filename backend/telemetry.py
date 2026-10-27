"""Fire-and-forget usage telemetry to the app owner's dashboard (monitor/, deployed on Vercel).

Sends these event types:
  app_started   once per server launch
  name_updated  the user typed or changed their display name
  run_completed at the end of every /finalize (ok or error), with the run's cost/token
                totals from llm.py's RunCost accumulator

Privacy contract (keep to it):
  - only titles and metadata are sent, NEVER document body text
  - the ingest key is never logged (nothing here is logged at all)
  - the raw IP is not sent; the dashboard derives city-level location from Vercel headers

Configuration (env wins, then the baked bpk module, same pattern as config.py's key):
  MONITOR_URL          e.g. https://<app>.vercel.app        -- unset => everything is a silent no-op
  MONITOR_INGEST_KEY   shared secret for the /api/ingest endpoint
  MONITOR_USER_LABEL   who this install belongs to (baked per distribution at build time)

Delivery: one daemon worker thread consumes a queue; every send is bounded by a 5s
timeout and can never raise or block a request. On failure the event JSON is appended
to a local outbox file (jsonl, capped) and retried on the next successful send or app
start; every event carries a UUID event_id so the dashboard's INSERT ... ON CONFLICT
DO NOTHING makes retries idempotent.
"""

import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Same pattern as config.py for the DeepSeek key: env first, then the baked module.
# In the packaged app bpk.py is compiled next to the other modules; in dev it does not exist.
try:
    import bpk  # type: ignore[import-not-found]
except ImportError:
    bpk = None

load_dotenv(Path(__file__).parent.parent / ".env")

APP_VERSION = os.getenv("MONITOR_APP_VERSION", "1.0.0")
SEND_TIMEOUT_S = 5
OUTBOX_MAX_EVENTS = 500
_TITLE_MAX = 200      # titles only, capped both here and server-side
_LABEL_MAX = 100

_null_logger_path: Path | None = None  # only for type clarity below; unused


def _get(key: str) -> str:
    """Env var first, then the baked bpk module; '' when neither is set (=> no-op)."""
    value = os.getenv(key, "")
    if value:
        return value
    if bpk is not None:
        try:
            return str(bpk.get(key))
        except Exception:  # KeyError for an unbaked key; anything else must not propagate
            return ""
    return ""


def configured() -> bool:
    return bool(_get("MONITOR_URL"))


# ---------------------------------------------------------------- install id (persisted once)

_install_id: str | None = None
_install_lock = threading.Lock()


def _data_dir() -> Path | None:
    """%LOCALAPPDATA%\\cmi-blog-agent — created on demand; None if that is impossible."""
    base = os.getenv("LOCALAPPDATA")
    if not base:
        return None
    try:
        d = Path(base) / "cmi-blog-agent"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except OSError:
        return None


def install_id() -> str:
    """Stable per-install UUID, persisted at %LOCALAPPDATA%\\cmi-blog-agent\\install_id.
    Falls back to an in-memory id if the filesystem won't cooperate (event still works
    for the session; the dashboard just sees a fresh id each launch)."""
    global _install_id
    with _install_lock:
        if _install_id:
            return _install_id
        fresh = str(uuid.uuid4())
        d = _data_dir()
        path = (d / "install_id") if d else None
        if path is not None:
            try:
                if path.is_file():
                    stored = path.read_text(encoding="utf-8").strip()
                    if stored:
                        _install_id = stored
                        return _install_id
                path.write_text(fresh + "\n", encoding="utf-8")
            except OSError:
                pass
        _install_id = _install_id or fresh
        return _install_id


# ---------------------------------------------------------------- display name (typed in by the user)

NAME_MIN = 2
NAME_MAX = 60
_user_name: str | None = None  # None = not read from disk yet; "" = read, nothing set
_name_lock = threading.Lock()


def normalize_name(raw: object) -> str | None:
    """Trim, drop control characters, collapse runs of whitespace. None unless the result is
    NAME_MIN-NAME_MAX characters long (so empty / junk / absurdly long input is rejected)."""
    if not isinstance(raw, str):
        return None
    cleaned = " ".join(raw.split())  # tabs/newlines (a pasted name) become single spaces
    cleaned = " ".join("".join(ch for ch in cleaned if ch.isprintable()).split())  # drop other control chars
    return cleaned if NAME_MIN <= len(cleaned) <= NAME_MAX else None


def _name_path() -> Path | None:
    d = _data_dir()
    return (d / "user_name") if d else None


def user_name() -> str:
    """The name this install reports to the dashboard ('' while the user has not set one yet).
    Persisted next to install_id so it survives restarts; read from disk once per process."""
    global _user_name
    with _name_lock:
        if _user_name is not None:
            return _user_name
        value = ""
        path = _name_path()
        if path is not None:
            try:
                if path.is_file():
                    value = normalize_name(path.read_text(encoding="utf-8")) or ""
            except OSError:
                pass
        _user_name = value
        return value


def set_user_name(raw: object) -> str:
    """Validate, persist and return the normalised name. ValueError on invalid input.
    Does not talk to the network; call emit_name_updated() afterwards to tell the dashboard."""
    global _user_name
    name = normalize_name(raw)
    if name is None:
        raise ValueError(f"enter a name of {NAME_MIN}-{NAME_MAX} characters")
    with _name_lock:
        path = _name_path()
        if path is not None:
            try:
                path.write_text(name + "\n", encoding="utf-8")
            except OSError:
                pass  # still usable for this session; the dashboard gets it via the event
        _user_name = name
    return name


# ---------------------------------------------------------------- outbox (offline resilience)

def _outbox_path() -> Path | None:
    d = _data_dir()
    return (d / "outbox.jsonl") if d else None


def _outbox_append(event: dict) -> None:
    path = _outbox_path()
    if path is None:
        return
    try:
        lines = []
        if path.is_file():
            lines = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        lines.append(json.dumps(event, ensure_ascii=False))
        # keep the most recent OUTBOX_MAX_EVENTS events
        path.write_text("\n".join(lines[-OUTBOX_MAX_EVENTS:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def _outbox_flush() -> None:
    """Retry every queued event; drop the ones the server accepted (idempotent anyway) and
    keep the rest for next time. Never raises."""
    path = _outbox_path()
    if path is None or not path.is_file():
        return
    try:
        pending = [ln for ln in path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    except OSError:
        return
    remaining = []
    for line in pending:
        try:
            event = json.loads(line)
        except ValueError:
            continue  # corrupt line — drop it, it will never validate server-side
        if not isinstance(event, dict) or not _post(event):
            remaining.append(line)
    try:
        if remaining:
            path.write_text("\n".join(remaining[-OUTBOX_MAX_EVENTS:]) + "\n", encoding="utf-8")
        else:
            path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- delivery

def _auth_headers() -> dict:
    """Prefer this install's own proxy token (the dashboard then takes the user label from the token
    and can revoke it); fall back to the legacy shared ingest key used by --direct builds."""
    headers = {"Content-Type": "application/json"}
    token = _get("MONITOR_PROXY_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        headers["X-Ingest-Key"] = _get("MONITOR_INGEST_KEY")
    return headers


def _post(event: dict) -> bool:
    """One POST to the dashboard. Returns True on 2xx; never raises."""
    url = _get("MONITOR_URL")
    if not url:
        return False
    try:
        req = urllib.request.Request(
            url.rstrip("/") + "/api/ingest",
            data=json.dumps(event, ensure_ascii=False).encode("utf-8"),
            headers=_auth_headers(),
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=SEND_TIMEOUT_S) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError, Exception):  # noqa: B014 - fire-and-forget
        return False


_queue: "queue.SimpleQueue[dict]" = queue.SimpleQueue()
_worker_lock = threading.Lock()
_worker_started = False


def _worker() -> None:
    while True:
        event = _queue.get()
        try:
            if event == {}:
                _outbox_flush()  # start-of-run marker: retry whatever queued up offline
            elif _post(event):
                _outbox_flush()  # delivery works again: catch up on anything queued offline
            else:
                _outbox_append(event)
        except Exception:
            pass  # a telemetry bug must never take the app down


def _ensure_worker() -> None:
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name="telemetry").start()
            _worker_started = True


def send(event: dict) -> None:
    """Queue an event for delivery. Never raises, never blocks (queue put is O(1)),
    and is a silent no-op unless MONITOR_URL is configured."""
    try:
        if not configured():
            return
        _ensure_worker()
        _queue.put(event)
    except Exception:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- event builders (the only API main.py uses)

def _base(kind: str) -> dict:
    """Fields every event carries. user_name rides on all of them so the dashboard heals itself even
    if a name_updated event was lost; the server only applies a name when its event is the newest."""
    return {
        "type": kind,
        "event_id": str(uuid.uuid4()),
        "install_id": install_id(),
        "user_label": _get("MONITOR_USER_LABEL")[:_LABEL_MAX],
        "user_name": user_name(),
        "app_version": APP_VERSION[:40],
        "ts": _now_iso(),
    }


def emit_app_started() -> None:
    send(_base("app_started"))


def emit_name_updated() -> None:
    """Tell the dashboard this install's name was just set or changed (it keeps current + previous)."""
    send(_base("name_updated"))


def emit_run_completed(
    run_id: str,
    title: str,
    word_count: int | None,
    status: str,
    duration_s: float | None,
    cost_usd: float | None,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int,
) -> None:
    send({
        **_base("run_completed"),
        "run_id": str(run_id)[:64],
        "title": (title or "")[:_TITLE_MAX],
        "word_count": word_count,
        "status": status if status in ("ok", "error") else "error",
        "duration_s": duration_s,
        "cost_usd": cost_usd,
        "prompt_tokens": int(prompt_tokens or 0),
        "completion_tokens": int(completion_tokens or 0),
        "cached_tokens": int(cached_tokens or 0),
    })


# Queue a start-of-run flush: retry anything that queued up while offline. The worker's first
# real delivery also triggers a flush, so this matters mostly for the app_started failure case.
def flush_outbox_on_start() -> None:
    try:
        if configured():
            _ensure_worker()
            _queue.put({})  # flush-only marker (never sent — see _worker)
    except Exception:
        pass


def _reset_for_tests() -> None:
    """Test-only: clear the cached install id and worker state."""
    global _install_id, _worker_started, _user_name
    with _install_lock:
        _install_id = None
    with _name_lock:
        _user_name = None
    with _worker_lock:
        _worker_started = False
