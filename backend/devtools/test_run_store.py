"""A finished run survives a restart or its TTL: run_store pickles the saveable fields, main._get_run
rebuilds the run from them with a fresh logger, and stale saves are cleaned up. No network, no LLM."""
import logging
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
import run_store  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES = []
TOKEN = "0123456789abcdef0123456789abcdef"

# Keep the test's pickles out of the real backend/runs/.
_TMP = tempfile.TemporaryDirectory()
run_store.RUNS_DIR = Path(_TMP.name)


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        FAILURES.append(name)


SECTIONS = [{"heading": "Where the market stands", "paragraphs": ["The market grew in 2025."]}]
FAQ = [{"question": "Who leads the market?", "answer": "PostPilot and Lob."}]

main._RUNS[TOKEN] = main.RunState(
    filename="upload.docx",
    article=SourceArticle(title="A Blog Title", paragraphs=[Paragraph(index=0, text="Body.")]),
    sponsor=Paragraph(index=1, text="Sponsor line."),
    orient={"topic_summary": "t", "bridge_angle": "a", "own_market": "Direct Mail Retargeting Market"},
    candidates=[], logger=logging.getLogger("test"), log_path=Path("devtools/never_written.log"),
)
main._RUNS[TOKEN].title = "A Written Title"
main._RUNS[TOKEN].sections = SECTIONS
main._RUNS[TOKEN].faq = FAQ

check("save: _persist_run writes the run and says so", main._persist_run(TOKEN, main._RUNS[TOKEN])
      and (run_store.RUNS_DIR / f"{TOKEN}.pkl").is_file())

main._RUNS.pop(TOKEN, None)
run = main._get_run(TOKEN)
check("restore: a run gone from memory comes back from disk",
      isinstance(run, main.RunState) and main._RUNS.get(TOKEN) is run, type(run).__name__)
check("restore: the draft is intact (title, sections, faq)",
      run.title == "A Written Title" and run.sections == SECTIONS and run.faq == FAQ,
      f"{run.title!r} {run.sections!r} {run.faq!r}")
check("restore: the article, sponsor and filename are intact",
      run.article.title == "A Blog Title" and run.article.paragraphs[0].text == "Body."
      and run.sponsor.text == "Sponsor line." and run.filename == "upload.docx")
check("restore: the run got a fresh logger, not a pickled one",
      run.logger is not None and run.log_path is not None
      and not str(run.log_path).endswith(".pkl"), str(run.log_path))


def _unknown_404s() -> bool:
    try:
        main._get_run("f" * 32)
        return False
    except Exception as e:  # fastapi.HTTPException(404)
        return getattr(e, "status_code", None) == 404


check("restore: a token with nothing on disk 404s", _unknown_404s())

# canvas may not exist on RunState yet (another agent is adding it): when it does, it must round-trip.
run.canvas = {"blocks": [{"id": "b1", "text": "edited"}]}
main._persist_run(TOKEN, run)
main._RUNS.pop(TOKEN, None)
check("restore: the canvas editor's state comes back when the run has it",
      getattr(main._get_run(TOKEN), "canvas", None) == {"blocks": [{"id": "b1", "text": "edited"}]})

check("save: a traversal token is refused, nothing is written",
      main._persist_run("../x", main._RUNS[TOKEN]) is False and run_store.load("../x") is None
      and not (run_store.RUNS_DIR.parent / "x.pkl").exists())
check("save: a non-hex token is refused", run_store.save("own-sheet-token", main._RUNS[TOKEN]) is False)
check("load: a token with nothing saved returns None", run_store.load("f" * 32) is None)

old = run_store.RUNS_DIR / ("ab" * 16 + ".pkl")
old.write_bytes(b"old run")
long_ago = time.time() - 20 * 86400
os.utime(old, (long_ago, long_ago))
fresh = run_store.RUNS_DIR / f"{TOKEN}.pkl"
check("cleanup: an old save goes, a fresh one stays",
      run_store.cleanup() == 1 and not old.exists() and fresh.is_file())

_TMP.cleanup()
main._RUNS.pop(TOKEN, None)
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all run_store tests passed")
