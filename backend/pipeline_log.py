import itertools
import logging
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"

_root_handlers_installed = False
_run_id_counter = itertools.count(1)


def _install_root_handlers(path: Path) -> None:
    """Attach handlers to the ROOT logger so every module's logging.getLogger(__name__) call
    (DB errors, search/HTTP warnings, etc.) lands in the same file/terminal stream without
    every call site having to be handed a logger explicitly."""
    global _root_handlers_installed
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")

    file_handler = logging.FileHandler(path, encoding="utf-8")
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    if not _root_handlers_installed:
        # On Windows, sys.stdout's default encoding is the console codepage (cp1252 etc.), not
        # UTF-8 — any LLM-generated character outside that codepage (non-breaking hyphens, curly
        # quotes, em-dashes) raises UnicodeEncodeError from inside logging's own emit(), which is
        # NOT caught by logging's normal error handling and kills the whole process. Reconfigure
        # to replace unencodable characters instead of crashing; the UTF-8 file handler above
        # already has the exact text regardless.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(errors="replace")
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(fmt)
        root.addHandler(stream_handler)
        _install_crash_hooks()
        _root_handlers_installed = True


def _install_crash_hooks() -> None:
    """Catch what logging.Logger normally can't: uncaught exceptions on the main thread,
    on any background thread, and inside Streamlit's own callback machinery. Without this,
    a bug on a user's laptop prints a traceback to a terminal they may have already closed
    and nothing reaches the log file at all."""
    crash_log = logging.getLogger("revamp.crash")

    def log_excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        crash_log.critical("UNCAUGHT EXCEPTION (main thread)", exc_info=(exc_type, exc_value, exc_tb))

    def thread_excepthook(args: threading.ExceptHookArgs):
        crash_log.critical(
            f"UNCAUGHT EXCEPTION (thread {args.thread.name if args.thread else '?'})",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = log_excepthook
    threading.excepthook = thread_excepthook


def new_run_logger(label: str = "run") -> tuple[logging.Logger, Path]:
    """One timestamped log file per pipeline run; every module's logger writes to it via root.

    logging.getLogger("revamp") always returns the SAME Logger instance across every call — it is
    NOT a fresh object per run. A first attempt stamped a run id onto that shared instance as an
    attribute, but a second new_run_logger() call (e.g. the next run starting) overwrote it before
    the first run's later LLM calls read it back, silently merging two runs' costs into one
    tracker (caught live: a 2-call run only recorded its first call, and a second run's real call
    vanished entirely). A LoggerAdapter is a genuinely distinct object per call — unlike the base
    Logger, two adapters never alias each other — while still delegating to the one real Logger
    for actual writing, so every existing `logger.info(...)` call site keeps working unchanged.
    """
    LOG_DIR.mkdir(exist_ok=True)
    path = LOG_DIR / f"{datetime.now():%Y%m%d_%H%M%S}_{label}.log"
    _install_root_handlers(path)
    base = logging.getLogger("revamp")
    adapter = logging.LoggerAdapter(base, {"revamp_run_id": next(_run_id_counter)})
    return adapter, path


def section(logger: logging.Logger, title: str) -> None:
    logger.info("")
    logger.info("=" * 20 + f" {title} " + "=" * 20)


def log_exceptions(logger: logging.Logger, where: str):
    """Context manager: log any exception raised in `where` with full traceback, then re-raise
    so the caller (Streamlit's error boundary, or the CLI) still sees and can act on the failure."""

    class _Ctx:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, exc_tb):
            if exc_type is not None:
                logger.error(
                    f"EXCEPTION in {where}: {exc_type.__name__}: {exc_value}\n"
                    + "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
                )
            return False

    return _Ctx()
