"""FastAPI backend for the CMI Blog Revamp Agent.

Serves the JSON API consumed by ``../frontend`` (also served statically from
``/`` when the frontend directory is present):

    POST /upload            multipart .docx  -> { filename, article }
    POST /step1   { filename, article, sponsor_index } -> { token, run_token, orient, candidates }
                  Step 1: identify the market. Orient (LLM) + CMI catalog match only — no web
                  search yet, since the human hasn't confirmed a market to search for.
    POST /step2   { run_token, report_id }   -> { report, hits }
                  Step 2: fetch market evidence online. Runs only after the human confirms a
                  market in Step 1 — web search queries are built from the CONFIRMED report's
                  own name, never the LLM's pre-confirmation guess.
    POST /step3   { run_token }              -> { selection, evidence }
                  Step 3: fetch market report facts. Call 2 (LLM) selects usable evidence from
                  the Step 2 web hits, scoped to the confirmed report.
    POST /step4   { run_token }              -> { title, sections }
                  Step 4: write the report. Call 3 (LLM).
    POST /finalize { run_token }             -> { result }
    GET  /run/{run_token}                    -> full run snapshot, for resuming after a page reload
    GET  /download/{filename}                -> revised .docx
    GET  /health                              -> { status }

Runs are stateful: ``/step1`` creates a run token holding the article,
sponsor paragraph, orient output, and candidates; later steps resume from
that token. The human confirmations (sponsor paragraph, report choice,
continue-after-each-step) live in the frontend — the backend only executes
one gated step per request.

Run with (from this directory)::

    uvicorn main:app --port 8000

Requires the root ``.env`` (``DEEPSEEK_API_KEY`` plus optional ``LLM_*`` settings).
The report catalog comes from ``catalog.sqlite`` alongside the code (built from
Postgres by ``scripts/export_catalog_sqlite.py``) — no live database needed.
"""

import functools
import json
import logging
import os
import re
import secrets
import signal
import sqlite3
import threading
import time
from contextlib import contextmanager, nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path

import sqlite3
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import checks
import config
import demand
import facts
import faqs
import freshness
import llm
import pipeline
import planning
import recheck as recheck_mod
import prompts
import publisher
import reports
import search
import sopcheck
import telemetry
import writer
from docx_io import OUTPUT_DIR, Link, Paragraph, SourceArticle, read_docx
from llm import LLMError
from pipeline import PipelineError
from reports import LiveFetchError
from pipeline_log import new_run_logger

log = logging.getLogger("revamp")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # .docx guest blogs are KBs; 20MB is generous headroom
RUN_TTL_S = 6 * 3600  # run tokens expire after 6h of disuse

_CMI_LINK_RE = re.compile(prompts.CMI_LINK_PATTERN)


# ---------------------------------------------------------------- app

app = FastAPI(title="CMI Blog Revamp Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local UI (file://, live-server, or served below) + API on :8000
    allow_methods=["*"],
    allow_headers=["*"],
)

# Fire-and-forget usage telemetry (see telemetry.py): one app_started per server launch, plus
# a run_completed at the end of every /finalize. Silent no-op unless MONITOR_URL is configured;
# configured from env or the baked bpk module, exactly like the DeepSeek key.
telemetry.emit_app_started()
telemetry.flush_outbox_on_start()


# ---------------------------------------------------------------- auto-shutdown when the browser tab closes
# Active only when start.bat launches the server (CMI_AUTOSHUTDOWN=1), so a developer running
# uvicorn by hand, tests, and CI never get a server that stops itself. The page sends a heartbeat
# every few seconds (and a "closing" beacon on pagehide); once no tab has been seen for a short
# grace period — and no request is mid-flight — the server exits.

_AUTOSHUTDOWN = os.environ.get("CMI_AUTOSHUTDOWN") == "1"
_TAB_TTL_S = 150        # background tabs get timers throttled to ~1/min; a quiet tab is not a closed one
_EMPTY_GRACE_S = 20     # no tabs left: wait this long (covers a page reload) before stopping
_STARTUP_GRACE_S = 180  # the browser never connected after launch
_tabs: dict[str, float] = {}
_tabs_lock = threading.Lock()
_inflight = 0
# Independent of the heartbeat mechanism: ANY real API call (step1-4, finalize, download, ...)
# proves a tab is genuinely open right now, same as a heartbeat would. Added after a real failure
# where the browser's heartbeat POSTs never registered at all (traced to API_BASE pointing at
# "localhost" while the page itself was opened at "127.0.0.1" — a different origin as far as the
# browser's keepalive-fetch handling is concerned) while ordinary step calls kept working the whole
# time, yet the watchdog still shut the server down mid-session because it only trusted the
# heartbeat's own bookkeeping. This gives it a second, independent signal so one broken channel
# can't take the whole safety check down with it.
_last_api_activity = time.time()


@app.middleware("http")
async def _count_inflight(request, call_next):
    global _inflight, _last_api_activity
    _inflight += 1
    _last_api_activity = time.time()
    try:
        return await call_next(request)
    finally:
        _inflight -= 1


@app.post("/app/heartbeat")
def heartbeat(tab: str, closing: int = 0) -> dict:
    with _tabs_lock:
        if closing:
            _tabs.pop(tab, None)
        else:
            _tabs[tab] = time.time()
    return {"ok": True}


class UserNameIn(BaseModel):
    user_name: str


@app.get("/app/user")
def get_user() -> dict:
    """The name this install reports to the usage dashboard; null until the user has entered one
    (the UI blocks on a name prompt at startup while this is null)."""
    return {"user_name": telemetry.user_name() or None}


@app.post("/app/user")
def set_user(body: UserNameIn) -> dict:
    """Set or change the display name. Persists it locally and tells the dashboard (which keeps the
    current name and the one before it)."""
    try:
        name = telemetry.set_user_name(body.user_name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    telemetry.emit_name_updated()
    return {"user_name": name}


def _shutdown() -> None:
    log.info("[autoshutdown] no browser tab connected — shutting the server down")
    signal.raise_signal(signal.SIGINT)  # uvicorn's own handler: graceful stop
    time.sleep(8)
    os._exit(0)  # graceful stop didn't take (e.g. Windows signal delivery) — exit anyway


def _watchdog() -> None:
    started = time.time()
    empty_since: float | None = None
    ever_connected = False
    while True:
        time.sleep(2)
        now = time.time()
        with _tabs_lock:
            for t in [t for t, seen in _tabs.items() if now - seen > _TAB_TTL_S]:
                del _tabs[t]
            has_tabs = bool(_tabs)
        # A real API call within the tab TTL counts the same as a heartbeat — see _last_api_activity
        # above for why this independent signal exists.
        recent_activity = (now - _last_api_activity) <= _TAB_TTL_S
        if has_tabs or recent_activity:
            ever_connected, empty_since = True, None
            continue
        if empty_since is None:
            empty_since = now
        idle_since = empty_since if ever_connected else started
        grace = _EMPTY_GRACE_S if ever_connected else _STARTUP_GRACE_S
        if now - idle_since >= grace and _inflight == 0:
            _shutdown()
            return


if _AUTOSHUTDOWN:
    threading.Thread(target=_watchdog, daemon=True, name="autoshutdown").start()
    log.info("[autoshutdown] enabled — the server stops when the last browser tab closes")


# Most error bodies are a plain string detail, read by the frontend with resp.text(). Pipeline
# failures (_pipeline_failure) pass a dict detail instead, carrying a plain-language message and
# error_code for the UI's error banner — those must leave this server as real JSON, not Python's
# str(dict) repr, or the frontend's JSON parse of it silently fails and falls back to garbage text.
@app.exception_handler(HTTPException)
async def _http_exception_as_text(_, exc: HTTPException) -> Response:
    if isinstance(exc.detail, dict):
        return JSONResponse(exc.detail, status_code=exc.status_code)
    return PlainTextResponse(str(exc.detail), status_code=exc.status_code)


@app.exception_handler(RequestValidationError)
async def _validation_error_as_text(_, exc: RequestValidationError) -> PlainTextResponse:
    msgs = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body') or 'request'}: {e['msg']}"
        for e in exc.errors()
    )
    return PlainTextResponse(f"Invalid request: {msgs}", status_code=400)


@app.exception_handler(Exception)
async def _unhandled_as_text(_, exc: Exception) -> PlainTextResponse:
    log.critical("UNHANDLED EXCEPTION", exc_info=exc)
    return PlainTextResponse(
        "Something went wrong on the server. The details were written to the log — please try again.",
        status_code=500,
    )


# ---------------------------------------------------------------- run store (one entry per gated run, keyed by token)

@dataclass
class RunState:
    filename: str
    article: SourceArticle
    sponsor: Paragraph
    orient: dict
    candidates: list[reports.CatalogEntry]
    logger: logging.Logger
    log_path: Path
    created_at: float = field(default_factory=time.time)
    # hits stays unset until Step 2 (web search) runs — and that step now only runs AFTER the
    # human confirms a market, searching for THAT market specifically rather than whichever
    # market the LLM guessed first in Step 1 (its guess and the human's pick can differ).
    report: reports.Report | None = None
    hits: list[search.SearchHit] | None = None
    # claim backing pool from Step 2 ({claim id: candidate pages}) — SELECT consumes it in Step 3.
    claim_pool: dict[str, list[search.SearchHit]] | None = None
    # The claim-backing search starts in Step 1, right after the claims are extracted: it depends only on the article, not
    # on the market the human picks, so it runs while the human reads and confirms. Step 2 collects the result.
    claim_future: "Future | None" = None
    # Subheading questions ({"subheading_questions": [{"id": "Q1", "question": "..."}, ...]}) — harvested
    # AND picked synchronously at the very start of Step 2, right after the market is confirmed (it needs
    # the CONFIRMED market name, unlike claim search), so the evidence search for them (question_evidence,
    # below) can start before/alongside run_search_evidence rather than after the questions are already fixed.
    questions: dict | None = None
    # Evidence pool for those questions ({question id: candidate pages}) — SELECT consumes it in Step 3,
    # the same way claim_pool does for claims.
    question_evidence: dict[str, list[search.SearchHit]] | None = None
    # Report claims ([{"id": "RC1", "claim": "...", "section": "trends"|"subsegment"|"country_us"}, ...]) —
    # extracted synchronously right after the report is fetched in Step 2 (it needs the report's own text,
    # so — like questions — it can only happen here, not in Step 1), so the evidence search for them
    # (report_claim_evidence, below) can start before/alongside run_search_evidence.
    report_claims: list[dict] = field(default_factory=list)
    # Evidence pool for those report claims ({claim id: candidate pages}) — SELECT consumes it in Step 3,
    # the same way claim_pool/question_evidence do.
    report_claim_evidence: dict[str, list[search.SearchHit]] | None = None
    # Long-tail commercial keywords ([{"keyword", "intent", "why"}, ...]) — generated in Step 2 from the
    # confirmed report's own segmentation/companies (an LLM call, no web search); the writer (Step 4)
    # integrates at most 3 of them. Empty when generation failed — an enhancement, never a requirement.
    longtails: list[dict] = field(default_factory=list)
    # Step 2's wall-clock timing ({"total_s", "stages": [{"name", "start_s", "seconds"}, ...]}, see StageTimer);
    # shown on the Step 2 card and written to the run log.
    timings: dict | None = None
    selection: dict | None = None
    evidence: str | None = None
    title: str = ""
    sections: list[dict] = field(default_factory=list)
    evidence_citations: list[dict] = field(default_factory=list)
    faq: list[dict] = field(default_factory=list)
    # ---- market-first chain (v2, config.CMI_CHAIN_V2): Step 2's own products and Step 3/4's handoffs.
    # The old fields above stay readable from either chain; finalize picks run_checks_v2 by plan_inputs.
    fact_sheet: dict | None = None          # facts.run_fact_sheet (+ figure backing), Step 2
    gap_pages: list | None = None           # the figure-gap search's candidate pages, Step 2
    demand: dict | None = None              # run_search_demand; questions get their answerability in Step 3
    publisher: dict | None = None           # publisher.run_publisher_points, Step 2
    plan: dict | None = None                # planning.run_plan (validated + repaired), Step 4
    plan_inputs: planning.PlanInputs | None = None   # the planner's world, built at the end of Step 3


_RUNS: dict[str, RunState] = {}

# Background work that must outlive the request that started it (the claim-backing search runs while the human chooses a market).
# 3 workers: claim search, question-evidence search and report-claim-evidence search can all be in flight at once in Step 2.
_BACKGROUND = ThreadPoolExecutor(3, thread_name_prefix="background")


class StageTimer:
    """Wall-clock timer for one pipeline step's stages (Step 2's evidence collection). Stages overlap —
    several run on their own threads at once — so each is timed from its own start to its own end,
    with its start offset from the step's start; the step's total is real elapsed time, NOT the sum.
    Every finished stage is logged as it ends, so the run log shows where the time went live."""

    def __init__(self, logger: logging.Logger, label: str) -> None:
        self._logger, self._label = logger, label
        self._t0 = time.perf_counter()
        self._lock = threading.Lock()
        self._stages: list[dict] = []

    @contextmanager
    def stage(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            seconds = time.perf_counter() - start
            with self._lock:
                self._stages.append({"name": name, "start_s": round(start - self._t0, 1), "seconds": round(seconds, 1)})
            self._logger.info(f"[timing] {self._label} / {name}: {seconds:.1f}s (started at +{start - self._t0:.1f}s)")

    def summary(self) -> dict:
        total = round(time.perf_counter() - self._t0, 1)
        with self._lock:
            stages = sorted(self._stages, key=lambda s: s["start_s"])
        self._logger.info(
            f"[timing] {self._label} TOTAL: {total:.1f}s — " + "; ".join(f'{s["name"]} {s["seconds"]}s' for s in stages)
        )
        return {"total_s": total, "stages": stages}


def _start_claim_search(orient: dict, logger: logging.Logger) -> "Future | None":
    claims = pipeline.orient_claims(orient)
    if not claims:
        return None

    def work() -> dict:
        try:
            return pipeline.run_claim_evidence(claims, logger)
        except Exception:  # claim backing is best-effort: a failure here must never fail the run
            logger.warning("[claim-search] failed; the draft will treat every claim as unbacked", exc_info=True)
            return {}

    return _BACKGROUND.submit(work)


def _harvest_and_select_questions(market_name: str, orient: dict, report: reports.Report, logger: logging.Logger) -> dict:
    """Draft the final subheading questions (the model writes them from the report; the autocomplete
    harvest they used to be picked from is disconnected — see pipeline.run_draft_questions), right after the market is
    confirmed and before the market web search starts — so the questions are already fixed by the
    time _start_question_evidence_search below runs, and that evidence search can overlap the
    general market search instead of running after it. Best-effort: a failure here must never fail
    the run, since the question pool is an enhancement, not a requirement."""
    try:
        return pipeline.run_draft_questions(market_name, orient, report, logger)
    except Exception:
        logger.warning("[intent] question harvest/selection failed; no subheading questions will be offered", exc_info=True)
        return {"subheading_questions": []}


def _start_question_evidence_search(questions: list[dict], logger: logging.Logger, timer: "StageTimer | None" = None) -> "Future":
    def work() -> dict:
        try:
            with (timer.stage("Question evidence search") if timer else nullcontext()):
                return pipeline.run_question_evidence(questions, logger)
        except Exception:  # best-effort, like claim search: a failure here must never fail the run
            logger.warning("[question-search] failed; chosen questions will be answered with no outside evidence", exc_info=True)
            return {}

    return _BACKGROUND.submit(work)


def _extract_report_claims(market_name: str, report: reports.Report, logger: logging.Logger) -> list[dict]:
    """Extract the report's own trends/subsegment/country_us claims SYNCHRONOUSLY, right after the
    report is fetched and before the market web search starts — so the claims are already fixed by
    the time _start_report_claim_evidence_search below runs, and that evidence search can overlap
    the general market search instead of running after it. Best-effort: a failure here must never
    fail the run, since this evidence is an enhancement, not a requirement."""
    try:
        return pipeline.run_report_claims(market_name, report, logger)
    except Exception:
        logger.warning("[report-claims] extraction failed; no report-claim evidence will be gathered", exc_info=True)
        return []


def _start_report_claim_evidence_search(claims: list[dict], logger: logging.Logger, timer: "StageTimer | None" = None) -> "Future":
    def work() -> dict:
        try:
            with (timer.stage("Report-claim evidence search") if timer else nullcontext()):
                return pipeline.run_report_claim_evidence(claims, logger)
        except Exception:  # best-effort, like claim search: a failure here must never fail the run
            logger.warning("[report-claim-search] failed; report claims will be used with no outside corroboration", exc_info=True)
            return {}

    return _BACKGROUND.submit(work)


def _extract_longtails(market_name: str, report: reports.Report, orient: dict, logger: logging.Logger) -> list[dict]:
    """Resolve the long-tail keyword candidates for the CONFIRMED market (see
    pipeline.resolve_longtails): reuse the Step 1 orient candidates when the human confirmed the
    model's default pick, regenerate (one small call) when they picked a different report.
    Best-effort: a failure here just means no long-tail keywords this run."""
    return pipeline.resolve_longtails(market_name, report, orient, logger)


def _prune_runs() -> None:
    now = time.time()
    for token in [t for t, r in _RUNS.items() if now - r.created_at > RUN_TTL_S]:
        _RUNS.pop(token, None)
    while len(_RUNS) > 200:  # hard cap: drop the oldest
        _RUNS.pop(min(_RUNS, key=lambda t: _RUNS[t].created_at), None)


def _get_run(token: str | None) -> RunState:
    if not token:
        raise HTTPException(400, "Missing run_token — start from Step 1 first.")
    run = _RUNS.get(token)
    if run is None:
        raise HTTPException(404, "Unknown or expired run. Please start a new run from Step 1.")
    return run


def _classify_failure(exc: Exception) -> tuple[str, str]:
    """(error_code, plain-language message) for a pipeline failure — shown to the end user
    in the UI, who isn't expected to understand stack traces or library exception text.
    The technical str(exc) is shown alongside it (collapsed) for them to copy into a bug report."""
    if isinstance(exc, LiveFetchError):
        return "live_fetch_failed", (
            "Couldn't load the live report page on coherentmarketinsights.com — it may be "
            "temporarily down or blocking automated requests. Try again in a minute, or pick a "
            "different report."
        )
    if isinstance(exc, LLMError):
        if "timed out" in str(exc):
            return "llm_timeout", "The writing model took too long to respond. Please try this step again."
        return "llm_error", "The writing model returned an error or an unusable response. Please try this step again."
    if isinstance(exc, sqlite3.Error):
        return "database_error", "Couldn't reach the report database. Please try again shortly."
    if isinstance(exc, PipelineError):
        if "No CMI report matched" in str(exc):
            return "no_report_match", (
                "No CMI report in the catalog matches this article's market closely enough. "
                "This article may need a report added to the catalog, or a different sponsor "
                "paragraph/market to be considered."
            )
        return "pipeline_error", "The pipeline couldn't produce a usable result at this step. Please try again."
    return "unknown_error", "Something went wrong on this step."


def _pipeline_failure(logger: logging.Logger, where: str, exc: Exception) -> HTTPException:
    logger.critical(f"PIPELINE FAILED at {where}", exc_info=exc)
    error_code, message = _classify_failure(exc)
    return HTTPException(500, {
        "error_code": error_code,
        "message": message,
        "where": where,
        "technical": str(exc) or repr(exc),
    })


def _draft_word_count(sections: list[dict] | None) -> int:
    """Words in a draft's paragraphs (same counting the pipeline's own checks use)."""
    return sum(pipeline._word_count(p) for s in (sections or []) for p in (s.get("paragraphs") or []))


def _emit_run_completed(run: RunState, run_token: str, status: str, started: float,
                        title: str, sections: list[dict] | None,
                        totals: dict | None = None) -> None:
    """One telemetry run_completed for /finalize. Fire-and-forget (telemetry.send never
    raises or blocks); the totals snapshot must have been taken BEFORE run_cost_summary()
    popped the tracker, so it's passed in for the success path. Like run_progress it carries only
    what was spent since the previous report (llm.run_cost_delta), because the dashboard sums them."""
    if totals is None:
        totals = llm.run_cost_delta(run.logger)  # error path: tracker is still in place
    try:
        telemetry.emit_run_completed(
            run_id=run_token,
            title=title or run.title or run.filename,
            word_count=_draft_word_count(sections) if sections is not None else None,
            status=status,
            duration_s=round(time.time() - started, 1),
            cost_usd=totals.get("cost_usd"),
            prompt_tokens=totals.get("prompt_tokens", 0),
            completion_tokens=totals.get("completion_tokens", 0),
            cached_tokens=totals.get("cached_tokens", 0),
        )
    except Exception:
        pass


def _emit_progress(logger: logging.Logger, run_id: str, title: str, step: str, started: float,
                   only_if_spent: bool = False) -> None:
    """Tell the dashboard a pipeline step ended and what it cost (only the LLM calls made since the
    last report). Called for failed steps too: a step that errors out has still spent money
    (only_if_spent skips the report for a step that failed without making a single call, e.g. one
    rejected by validation). Telemetry must never affect the pipeline, so nothing in here may raise."""
    try:
        if not telemetry.configured():
            return
        totals = llm.run_cost_delta(logger)
        if only_if_spent and not totals.get("calls"):
            return
        telemetry.emit_run_progress(
            run_id=run_id,
            title=title,
            step=step,
            duration_s=round(time.time() - started, 1),
            cost_usd=totals.get("cost_usd"),
            prompt_tokens=totals.get("prompt_tokens", 0),
            completion_tokens=totals.get("completion_tokens", 0),
            cached_tokens=totals.get("cached_tokens", 0),
        )
    except Exception:
        pass


def _reports_step(step: str):
    """Decorator for the /stepN endpoints (the run already exists): when the step returns OR raises,
    send a run_progress event with the cost it incurred. A run abandoned after step 2 is therefore on
    the dashboard, with its real cost, instead of only appearing if /finalize is ever reached."""
    def decorate(fn):
        @functools.wraps(fn)
        def wrapper(req):
            succeeded = False
            try:
                result = fn(req)
                succeeded = True
                return result
            finally:
                token = getattr(req, "run_token", None) or ""
                run = _RUNS.get(token)
                if run is not None:
                    _emit_progress(run.logger, token, run.title or run.article.title or run.filename, step,
                                   run.created_at, only_if_spent=not succeeded)
        return wrapper
    return decorate


# ---------------------------------------------------------------- serialization (frontend shapes; camelCase where the UI expects it)

def _link_dict(link: Link) -> dict:
    return {"anchor": link.anchor, "url": link.url, "isExternal": link.is_external}


def _paragraph_dict(p: Paragraph) -> dict:
    return {"index": p.index, "text": p.text, "links": [_link_dict(l) for l in p.links]}


def _article_dict(article: SourceArticle) -> dict:
    return {
        "title": article.title,
        "paragraphs": [_paragraph_dict(p) for p in article.paragraphs],
    }


def _candidate_dict(c: reports.CatalogEntry) -> dict:
    return {"news_id": c.news_id, "keyword": c.keyword, "url": c.url}


def _hit_dict(h: search.SearchHit) -> dict:
    return {
        "title": h.title,
        "snippet": h.snippet,
        "url": h.url,
        "source": h.source,
        "date": h.date.isoformat() if h.date else None,
        "is_news": h.is_news,
        "excerpt": h.excerpt,
        "scope": h.scope,
        "claim_id": h.claim_id,
    }


def _claim_hits_dict(pool: dict[str, list[search.SearchHit]] | None) -> dict:
    """{claim id: [candidate pages the claim-backing search kept]} for the UI (Step 2 and a reloaded run)."""
    return {
        cid: [
            {"title": h.title, "url": h.url, "source": h.source or h.host, "snippet": h.snippet,
             "date": h.date.isoformat() if h.date else None}
            for h in hits
        ]
        for cid, hits in (pool or {}).items()
    }


def _report_dict(r: reports.Report) -> dict:
    return {
        "news_id": r.news_id, "name": r.name, "url": r.url,
        # The same text the writer actually reads (run_write's _report_text pulls this same key) —
        # shown in Step 3 so a human can check what the draft will be grounded in, not just the
        # report's name/link. Previously fetched and used but never sent to the frontend at all.
        "text": r.sections.get("live_report_page", ""),
    }


def _segment_dicts(text: str, sponsor: Paragraph, report_url: str | None) -> list[dict]:
    """Expand a written paragraph's placeholders into the same (text, url) segments
    ``RevampResult.segments()`` uses for the .docx — the JSON UI renders real hyperlinks too,
    so it needs the URLs, not flattened plain text. Delegated to pipeline.expand_segments so the
    preview, the docx and the checks cannot disagree about where an inline placeholder resolves."""
    return [{"text": t, "url": u} for t, u in pipeline.expand_segments(text, sponsor, report_url)]


def _display_sections(
    sections: list[dict], sponsor: Paragraph, report_url: str | None,
    primary_kw: str | None = None, secondary_plan: list[tuple[str, str]] = (),
    longtails: list[str] = (), counts: dict | None = None,
    tertiary_terms: list[str] = (),
) -> list[dict]:
    """`counts`, when given, is filled with the keyword use counts that the highlighting itself found
    ({"primary": n, "secondary": {kw: n}, "longtail": {phrase: n}, "tertiary": {term: n}}) — the SAME
    spans the reader sees highlighted, so the legend at the top of the page can never disagree with
    the marks in the text. `tertiary_terms` (the market-first chain's related search terms) are marked
    as a literal word-group; the old chain leaves it empty."""
    out = []
    for s in sections:
        # Only the secondary keywords TAGGED FOR this section are eligible to highlight in it — a
        # keyword's words showing up by coincidence in an unrelated section (or the market name
        # appearing without that keyword's own suffix nearby) is not a confirmed use of it, just a
        # false positive. See _apply_keyword_spans for how each one is matched within this section.
        section_secondary = [(kw, pipeline._keyword_suffix(kw)) for kw, sec in secondary_plan
                              if sec in s.get("sop", [])]
        paragraphs = []
        for p in s.get("paragraphs", []):
            segs = _segment_dicts(p, sponsor, report_url)
            if primary_kw:
                segs = _apply_keyword_spans(segs, primary_kw, section_secondary, longtails, counts,
                                            tertiary_terms=tertiary_terms)
            paragraphs.append(segs)
        out.append({"heading": s.get("heading", ""), "paragraphs": paragraphs})
    return out




def _apply_keyword_spans(
    segments: list[dict], primary_kw: str, section_secondary: list[tuple[str, str]],
    longtails: list[str] = (), counts: dict | None = None,
    tertiary_terms: list[str] = (),
) -> list[dict]:
    """Highlights the primary keyword's exact text, plus each SECTION-SCOPED secondary keyword's
    suffix word (only the keywords actually tagged for the section this paragraph belongs to —
    see _display_sections). A secondary keyword's words may be reordered and split across a
    sentence (see WRITE_USER's REORDER rule), so a tight literal-phrase match finds almost nothing
    real; checks.secondary_keyword_spans_in_paragraph instead requires the market's own words and
    the suffix's own words to each appear SOMEWHERE in the paragraph, which matches how the
    backend's own coverage check judges a secondary keyword as used (see pipeline._keyword_stats) —
    the highlight should show the reader the same thing the pipeline itself credited, not a
    stricter standard that silently finds nothing on genuinely well-written paragraphs."""
    plain = "".join(s["text"] for s in segments)

    # Pass 1: stake out the primary keyword's own EXACT, literal occurrences first (a plain
    # case-insensitive substring search, not the concept matcher) — these are always correct and
    # always win, since the primary keyword is never reworded and its exact text is the one thing
    # every CMI_LINK anchor and every full market-name mention actually is.
    taken: list[str | None] = [None] * len(plain)
    low_plain = plain.lower()
    if counts is not None:
        counts.setdefault("primary", 0)
        counts.setdefault("secondary", {})
        counts.setdefault("longtail", {})
    if primary_kw:
        low_phrase = primary_kw.lower()
        pos = 0
        while (i := low_plain.find(low_phrase, pos)) != -1:
            for k in range(i, i + len(low_phrase)):
                taken[k] = "primary"
            pos = i + len(low_phrase)
            if counts is not None:
                counts["primary"] += 1

    # Pass 1b: long-tail phrases, literal, in every section. Staked out BEFORE the secondary keywords
    # so a phrase that happens to contain a secondary keyword's suffix word stays one long-tail mark
    # (the secondary pass trims around it); the primary keyword's own text still wins on any overlap.
    # When no tight light-glued stretch exists in the paragraph, the loose per-word matcher is the
    # fallback: a phrase is a topic to cover, so each content word's own span is marked (label
    # unchanged) and the phrase counts as one use, not one per word.
    for phrase in longtails:
        marks = checks.longtail_spans(plain, phrase)
        if marks:
            for start, end in marks:
                free = [k for k in range(start, end) if taken[k] is None]
                if not free:
                    continue  # entirely inside the primary keyword's text — not a separate use
                for k in free:
                    taken[k] = "longtail"
                if counts is not None:
                    counts["longtail"][phrase] = counts["longtail"].get(phrase, 0) + 1
            continue
        loose = checks.keyword_words_in_sentence(plain, phrase)
        counted = False
        for start, end in loose or []:
            free = [k for k in range(start, end) if taken[k] is None]
            if not free:
                continue
            for k in free:
                taken[k] = "longtail"
            if counts is not None and not counted:
                counts["longtail"][phrase] = counts["longtail"].get(phrase, 0) + 1
            counted = True

    # Pass 1c: the market-first chain's tertiary (related) search terms, marked as a plain
    # case-insensitive word-group fill — a tertiary term is a loosely related phrase the writer
    # used naturally, never restated verbatim from the plan, so the concept matcher used for
    # secondary keywords above (§ market words + suffix words) has no suffix to anchor one.
    for term in tertiary_terms:
        if counts is not None:
            counts.setdefault("tertiary", {})
        low_term = term.lower()
        pos = 0
        while (i := low_plain.find(low_term, pos)) != -1:
            if all(taken[k] is None for k in range(i, i + len(low_term))):
                for k in range(i, i + len(low_term)):
                    taken[k] = "tertiary"
                if counts is not None:
                    counts["tertiary"][term] = counts["tertiary"].get(term, 0) + 1
            pos = i + len(low_term)

    # Pass 2: each section-scoped secondary keyword's suffix word fills in whatever's left, but
    # TRIM at any cell the primary pass already claimed instead of discarding the whole match — a
    # secondary keyword's suffix can legitimately sit right next to the primary keyword's own text.
    # When the strict one-stretch match finds nothing, the loose per-word matcher is the fallback
    # (checks.secondary_keyword_loose_spans, same market-anchor gate): each content word's own span
    # is marked with the same label, and the keyword counts as one use, not one per word.
    for kw, suffix in section_secondary:
        spans = checks.secondary_keyword_spans_in_paragraph(plain, primary_kw, suffix)
        loose = False
        if not spans:
            spans = checks.secondary_keyword_loose_spans(plain, primary_kw, suffix)
            loose = bool(spans)
        counted = False
        for start, end in spans:
            if all(taken[k] is not None for k in range(start, end)):
                continue  # wholly covered by the primary/long-tail marks — nothing of it is shown, so it is not counted
            if counts is not None and not (loose and counted):
                counts["secondary"][kw] = counts["secondary"].get(kw, 0) + 1
            counted = True
            i = start
            while i < end:
                if taken[i] is not None:
                    i += 1
                    continue
                j = i
                while j < end and taken[j] is None:
                    j += 1
                for k in range(i, j):
                    taken[k] = "secondary"
                i = j

    out: list[dict] = []
    pos = 0
    for seg in segments:
        seg_len = len(seg["text"])
        seg_start = pos
        i = 0
        while i < seg_len:
            label = taken[seg_start + i]
            j = i + 1
            while j < seg_len and taken[seg_start + j] == label:
                j += 1
            piece = {"text": seg["text"][i:j], "url": seg.get("url")}
            if label:
                piece["kw"] = label
            out.append(piece)
            i = j
        pos += seg_len
    return out


# ---------------------------------------------------------------- request models

class LinkIn(BaseModel):
    anchor: str = ""
    url: str


class ParagraphIn(BaseModel):
    index: int
    text: str
    links: list[LinkIn] = Field(default_factory=list)


class ArticleIn(BaseModel):
    title: str
    paragraphs: list[ParagraphIn]


class Step1Request(BaseModel):
    filename: str = "upload.docx"
    article: ArticleIn
    sponsor_index: int | None = None


class Step2Request(BaseModel):
    run_token: str | None = None
    report_id: int | None = None


class Step3Request(BaseModel):
    run_token: str | None = None


class Step4Request(BaseModel):
    run_token: str | None = None


class FinalizeRequest(BaseModel):
    run_token: str | None = None


def _rebuild_article(data: ArticleIn) -> SourceArticle:
    return SourceArticle(
        title=data.title,
        paragraphs=[
            Paragraph(
                index=p.index,
                text=p.text,
                links=[Link(anchor=li.anchor or "", url=li.url) for li in p.links],
            )
            for p in data.paragraphs
        ],
    )


# ---------------------------------------------------------------- routes

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/dev/seed-run")
def dev_seed_run() -> dict:
    """DEV ONLY — loads devtools/fixture.json (captured by capture_fixture.py) into a real
    RunState, pre-populated through Step 3 (report + hits + selection already set), so the
    real frontend can resume straight into Step 4 without re-running orient/catalog-match/
    web-search/select or paying for their LLM/DDGS calls. Lets you iterate on WRITE_SYSTEM/
    WRITE_USER through the actual UI instead of only via devtools/replay_write.py."""
    fixture_path = Path(__file__).parent / "devtools" / "fixture.json"
    if not fixture_path.is_file():
        raise HTTPException(404, "No fixture found — run devtools/capture_fixture.py first.")
    fixture = json.loads(fixture_path.read_text())

    article = _rebuild_article(ArticleIn(**fixture["article"]))
    sponsor = next(p for p in article.paragraphs if p.index == fixture["sponsor"]["index"])
    candidates = [reports.CatalogEntry(**c) for c in fixture["candidates"]]
    report = reports.Report(**fixture["report"])
    report.sections["live_report_page"] = freshness.strip_stale_developments(report.sections["live_report_page"])[0]

    def _hit_from(d: dict) -> search.SearchHit:
        d = dict(d)
        d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
        return search.SearchHit(**d)

    hits = [_hit_from(h) for h in fixture["hits"]]

    logger, log_path = new_run_logger(label="dev-seed")
    logger.info(f"[dev-seed] loaded fixture: report={report.name!r}, {len(hits)} hits")

    _prune_runs()
    token = secrets.token_hex(16)
    _RUNS[token] = RunState(
        filename=fixture.get("original_filename", "upload.docx"),
        article=article, sponsor=sponsor, orient=fixture["orient"],
        candidates=candidates, logger=logger, log_path=log_path,
        report=report, hits=hits, selection=fixture["selection"], evidence=fixture["evidence"],
    )
    logger.info(f"[dev-seed] run token {token[:8]}… ready, ff to Step 4")
    return {"run_token": token, "nav": "step4"}


@app.post("/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    name = file.filename or "upload.docx"
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a Word document (.docx).")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File too large — please upload a .docx under 20MB.")
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    try:
        article = read_docx(BytesIO(raw))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"filename": Path(name).name, "article": _article_dict(article)}


@app.post("/sop-check")
async def sop_check(file: UploadFile = File(...)) -> dict:
    """Checks a finished draft against the SOP rules that can be judged from its own text. Standalone:
    it touches no run state and makes no LLM or network call."""
    name = file.filename or "draft.docx"
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a Word document (.docx).")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File too large — please upload a .docx under 20MB.")
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    try:
        result = sopcheck.check_draft(raw)
    except Exception as e:
        logging.getLogger(__name__).error(f"sop-check could not read {name}: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(400, "This file could not be read as a Word document (.docx). "
                                 "If it came from Google Docs or an older .doc file, re-save it as .docx and try again.")
    return {"filename": Path(name).name, **result}


@app.post("/step1")
def step1(req: Step1Request) -> dict:
    """Step 1: identify the market. Orient (LLM) + catalog match only — no web search yet, since
    the human hasn't confirmed a market to search for. Fast: no DDGS calls on this request."""
    article = _rebuild_article(req.article)
    if req.sponsor_index is None:
        raise HTTPException(400, "No sponsor paragraph selected.")
    sponsor = next((p for p in article.paragraphs if p.index == req.sponsor_index), None)
    if sponsor is None:
        raise HTTPException(400, "Selected sponsor paragraph was not found in the article.")
    if not any(li.is_external for li in sponsor.links):
        raise HTTPException(400, "Selected paragraph has no outside link — pick the one with the paid backlink.")

    logger, log_path = new_run_logger()
    logger.info(f"ARTICLE TITLE: {article.title}")
    logger.info(f"SPONSOR PARAGRAPH: {sponsor.text}")
    started = time.time()
    title = article.title or Path(req.filename).name or "upload.docx"
    try:
        orient = pipeline.run_orient(article, sponsor, logger)
        claim_future = _start_claim_search(orient, logger)
        candidates = pipeline.run_match_catalog(orient, logger)
    except (PipelineError, LLMError) as e:
        # no run token exists yet (it is minted below, after the LLM work), but the calls that did
        # happen were paid for, so report them under their own id
        _emit_progress(logger, secrets.token_hex(16), title, "step1", started)
        raise _pipeline_failure(logger, "Step 1", e)

    _prune_runs()
    token = secrets.token_hex(16)
    _RUNS[token] = RunState(
        filename=Path(req.filename).name or "upload.docx",
        article=article, sponsor=sponsor, orient=orient,
        candidates=candidates, logger=logger, log_path=log_path, claim_future=claim_future,
    )
    logger.info(f"STEP 1 DONE — run token {token[:8]}…, {len(candidates)} candidates")
    _emit_progress(logger, token, title, "step1", started)
    return {
        "token": token,
        "run_token": token,
        "orient": orient,
        "candidates": [_candidate_dict(c) for c in candidates],
    }


def _step2_v2(run: RunState, chosen: reports.CatalogEntry) -> dict:
    """Step 2 on the market-first chain (plan §3 diagram): fetch the report, build the fact sheet (with
    the figure gap search + figure backing when size/forecast/CAGR is missing), then — in parallel —
    search demand and the publisher points, and the LLM's search queries feeding the market web search.
    The three prep streams are independent (only demand needs the fact sheet, which finishes first), so
    they overlap the way the old chain's Step 2 prep did; the web search is still the long pole."""
    run.logger.info("STEP 2 (market-first chain)")
    timer = StageTimer(run.logger, "Step 2")
    try:
        with timer.stage("Fetch CMI report"):
            report = pipeline.run_fetch_confirmed_report(chosen, run.logger)
        with timer.stage("Fact sheet"):
            fs = facts.run_fact_sheet(report.name, report, run.logger)
        gap_pages: list[search.SearchHit] = []
        if fs["gaps"]:
            with timer.stage("Figure gap search"):
                hosts = [
                    search.urlparse(l.url).netloc.lower().removeprefix("www.")
                    for l in run.sponsor.links if l.is_external
                ]
                gap_pages = facts.run_figure_gap_search(report.name, hosts, run.logger)
            if gap_pages:
                with timer.stage("Figure backing"):
                    fs = facts.run_figure_backing(report.name, fs, gap_pages, hosts, run.logger)
        prep = ThreadPoolExecutor(max_workers=3, thread_name_prefix="step2-v2")
        try:
            def timed(name: str, fn, *args):
                with timer.stage(name):
                    return fn(*args)

            demand_f = prep.submit(timed, "Search demand", demand.run_search_demand, report.name, fs, run.logger)
            publisher_f = prep.submit(
                timed, "Publisher points", publisher.run_publisher_points, run.article, run.sponsor, report.name, run.logger)
            search_queries_f = prep.submit(
                timed, "Plan market search queries", pipeline.prepare_search_queries, report.name,
                list(reports.key_players(report)), run.logger, list(report.segment_terms),
            )
            prepared_queries = search_queries_f.result()
        finally:
            prep.shutdown(wait=False)
        with timer.stage("Market web search"):
            hits = pipeline.run_search_evidence(
                report.name, run.sponsor, run.candidates, run.logger,
                key_players=reports.key_players(report), orient=run.orient, segments=report.segment_terms,
                search_queries=prepared_queries,
            )
        demand_result, publisher_result = demand_f.result(), publisher_f.result()
    except (PipelineError, LLMError, LiveFetchError) as e:
        raise _pipeline_failure(run.logger, "Step 2", e)
    run.report, run.hits = report, hits
    run.fact_sheet, run.gap_pages, run.demand, run.publisher = fs, gap_pages, demand_result, publisher_result
    run.timings = timer.summary()
    run.logger.info(
        f"STEP 2 DONE in {run.timings['total_s']}s — report {report.name}, {len(hits)} hits, "
        f"{len(demand_result.get('questions') or [])} MSQ(s), fact sheet gaps: {fs['gaps'] or 'none'}"
    )
    return {
        "report": _report_dict(report), "hits": [_hit_dict(h) for h in hits],
        "fact_sheet": fs,
        "demand": demand_result,
        "publisher": publisher_result,
        "timings": run.timings,
    }


@app.post("/step2")
@_reports_step("step2")
def step2(req: Step2Request) -> dict:
    """Step 2: fetch market evidence online. Runs ONLY after the human confirms a market —
    the web search queries are built from the CONFIRMED report's own name, never the LLM's
    pre-confirmation guess (which can be a different market than what the human picks)."""
    run = _get_run(req.run_token)
    if req.report_id is None:
        raise HTTPException(400, "No report selected — confirm a market first.")
    chosen = next((c for c in run.candidates if c.news_id == req.report_id), None)
    if chosen is None:
        raise HTTPException(400, f"Report {req.report_id} is not one of the matched reports for this run.")
    # Market-first chain (docs/restructure_plan.md §3): fact sheet (+ figure gap backing), search demand
    # and publisher points replace the old Step 2 prep (subheading questions, report claims, longtails —
    # those keep running unchanged behind the flag off, see _step2_v2's docstring).
    if config.CMI_CHAIN_V2:
        return _step2_v2(run, chosen)
    timer = StageTimer(run.logger, "Step 2")
    try:
        with timer.stage("Fetch CMI report"):
            report = pipeline.run_fetch_confirmed_report(chosen, run.logger)
        # The claim-backing search has been running since Step 1 (see RunState.claim_future); start it now only if it
        # never started (a run restored without it).
        if run.claim_future is None:
            run.claim_future = _start_claim_search(run.orient, run.logger)
        # The three prep steps below used to run strictly one after another, starting each one's
        # evidence search as it finished — which serialized three LLM calls (~30-50s of wall time).
        # None of them needs another's output, so they now run in parallel; each evidence search
        # still starts only once its own prep has produced the thing it searches for, and the web
        # search consumes the pre-generated queries (search_queries=), so the whole chain keeps its
        # original data dependencies. DeepSeek handles concurrent calls; no shared rate gate exists
        # in llm.py, so the parallel calls genuinely overlap.
        prep = ThreadPoolExecutor(max_workers=4, thread_name_prefix="step2-prep")
        try:
            def timed(name: str, fn, *args):
                with timer.stage(name):
                    return fn(*args)

            questions_f = prep.submit(timed, "Draft subheading questions", _harvest_and_select_questions, report.name, run.orient, report, run.logger)
            report_claims_f = prep.submit(timed, "Extract report claims", _extract_report_claims, report.name, report, run.logger)
            search_queries_f = prep.submit(
                timed, "Plan market search queries", pipeline.prepare_search_queries, report.name,
                list(reports.key_players(report)), run.logger, list(report.segment_terms),
            )
            # Long-tail keyword candidates are generated on their own thread in this same parallel prep, from the
            # confirmed report's segments/companies and the article's subject (pipeline.resolve_longtails); nothing
            # below waits on them until the writer, so they cost no extra wall time.
            longtails_f = prep.submit(
                timed, "Generate long-tail keywords", _extract_longtails, report.name, report, run.orient, run.logger,
            )
            # Subheading questions are harvested AND picked before the evidence search below starts, so the
            # evidence search for them can run concurrently with it rather than only after the questions are known.
            run.longtails = longtails_f.result()
            run.questions = questions_f.result()
            question_future = _start_question_evidence_search(run.questions.get("subheading_questions") or [], run.logger, timer)
            # Report claims are extracted the same way and at the same point as subheading questions — the
            # report's own text is only available now, and fixing the claims before the market search starts
            # lets their evidence search run concurrently with it instead of only after.
            run.report_claims = report_claims_f.result()
            report_claim_future = _start_report_claim_evidence_search(run.report_claims, run.logger, timer)
            prepared_queries = search_queries_f.result()
        finally:
            prep.shutdown(wait=False)
        with timer.stage("Market web search"):
            hits = pipeline.run_search_evidence(
                report.name, run.sponsor, run.candidates, run.logger,
                key_players=reports.key_players(report), orient=run.orient, segments=report.segment_terms,
                search_queries=prepared_queries,
            )
        run.claim_pool = run.claim_future.result() if run.claim_future is not None else {}
        run.question_evidence = question_future.result()
        run.report_claim_evidence = report_claim_future.result()
    except (PipelineError, LLMError, LiveFetchError) as e:
        raise _pipeline_failure(run.logger, "Step 2", e)
    run.report, run.hits = report, hits
    run.timings = timer.summary()
    run.logger.info(
        f"STEP 2 DONE in {run.timings['total_s']}s — report {report.news_id} {report.name}, {len(hits)} hits, "
        f"{len(run.questions.get('subheading_questions') or [])} subheading question(s) chosen, "
        f"{len(run.report_claims)} report claim(s) extracted"
    )
    return {
        "report": _report_dict(report), "hits": [_hit_dict(h) for h in hits],
        "claim_hits": _claim_hits_dict(run.claim_pool),
        "questions": run.questions,
        "report_claims": run.report_claims,
        "long_tail_keywords": run.longtails,
        "timings": run.timings,
    }


def _step3_v2(run: RunState) -> dict:
    """Step 3 on the market-first chain (plan §3 diagram): SELECT vets the market news with no
    question/claim backing (those chains are disconnected behind the old flag), and afterwards code
    marks every MSQ answerable / unanswerable and assembles the planner's world (`run.plan_inputs`).
    The same post-processing the devtools build_chain.load_chain applies to its fixtures."""
    run.logger.info("STEP 3 (market-first chain)")
    try:
        selection = pipeline.run_select(run.article, run.orient, run.sponsor, run.report, run.hits, run.logger,
                                        headline_figures=pipeline._headline_figures(run.fact_sheet))
        evidence = pipeline.run_build_evidence(selection, run.hits, run.logger, orient=run.orient)
        evidence_items = [it for it in pipeline._parse_evidence(evidence) if it.get("group") != "article"]
        questions = demand.mark_answerability(
            [dict(q) for q in (run.demand or {}).get("questions") or []],
            run.fact_sheet, evidence_items, run.logger)
        run.demand = {**run.demand, "questions": questions}
        run.plan_inputs = planning.build_inputs(
            run.report.name, run.fact_sheet, evidence_items, run.demand, run.publisher,
            pipeline._word_count(run.sponsor.text))
        # The market-first chain's long-tail phrases come from Step 2's search demand (run.demand's
        # long_tail records), and the planner shards them onto sections via plan_inputs; expose the
        # same list as run.longtails (the old chain sets it in Step 2) so finalize's usage check, the
        # keyword-highlighting pass and the UI all see the phrases the writer was actually told to use.
        run.longtails = [dict(lt) for lt in run.plan_inputs.longtails]
    except (PipelineError, LLMError, LookupError) as e:
        raise _pipeline_failure(run.logger, "Step 3", e)
    run.selection, run.evidence = selection, evidence
    msqs = run.demand.get("questions") if run.demand else []
    run.logger.info(
        f"STEP 3 DONE — {len(selection.get('evidence') or [])} evidence item(s), "
        f"{sum(1 for q in msqs or [] if q.get('answerable'))}/{len(msqs or [])} MSQ(s) answerable")
    # long_tail_keywords rides along for the UI (renderStep3Panel reads state.run.longtails);
    # the old chain's Step 2 response already sent it, this chain populates run.longtails here.
    return {"selection": selection, "evidence": evidence, "demand": run.demand,
            "long_tail_keywords": run.longtails}


@app.post("/step3")
@_reports_step("step3")
def step3(req: Step3Request) -> dict:
    """Step 3: fetch market report facts. Call 2 (LLM) selects usable evidence from the Step 2
    web hits, scoped to the confirmed report."""
    run = _get_run(req.run_token)
    if run.report is None or run.hits is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 2 first.")
    if config.CMI_CHAIN_V2:
        return _step3_v2(run)
    # The subheading questions and report claims were already chosen/extracted in Step 2 (see
    # run.questions, run.report_claims); SELECT here only judges which of the evidence gathered for
    # them (run.question_evidence, run.report_claim_evidence) is usable.
    subheading_questions = (run.questions or {}).get("subheading_questions") or []
    try:
        selection = pipeline.run_select(
            run.article, run.orient, run.sponsor, run.report, run.hits, run.logger,
            claim_hits=run.claim_pool, questions=subheading_questions, question_hits=run.question_evidence,
            report_claims=run.report_claims, report_claim_hits=run.report_claim_evidence,
        )
        evidence = pipeline.run_build_evidence(selection, run.hits, run.logger, orient=run.orient, questions=subheading_questions)
    except (PipelineError, LLMError, LookupError) as e:
        raise _pipeline_failure(run.logger, "Step 3", e)
    run.selection, run.evidence = selection, evidence
    run.logger.info(f"STEP 3 DONE — {len(selection.get('evidence') or [])} evidence item(s) selected, "
                     f"{len(selection.get('question_backing') or [])} question backing item(s), "
                     f"{len(selection.get('report_claim_backing') or [])} report claim backing item(s)")
    return {"selection": selection, "evidence": evidence, "questions": run.questions}


def _step4_chain(run: RunState) -> dict:
    """Step 4 on the market-first chain (plan §3 diagram): PLAN (one LLM call, validated and repaired in
    code) ──> WRITE ──> FAQ ANSWERS. Every regeneration runs the full plan call again — there is no
    quality retry anywhere in the chain, so a new draft is a new plan."""
    try:
        plan = planning.run_plan(run.plan_inputs, run.logger)
        run.plan = plan
        _, title, sections, evidence_citations = writer.run_write_v2(
            plan, run.plan_inputs, run.sponsor, run.report, run.logger)
        faq = faqs.run_faq(plan, run.plan_inputs, sections, run.logger)
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "Step 4", e)
    run.title, run.sections, run.evidence_citations, run.faq = title, sections, evidence_citations, faq
    run.logger.info(f"STEP 4 DONE — draft {title!r} ({len(sections)} sections, {len(faq)} MSQ FAQ entries"
                    + (f"; plan repairs: {'; '.join(plan.get('repairs'))}" if plan.get("repairs") else "") + ")")
    return {"title": title, "sections": _display_sections(sections, run.sponsor, run.report.url), "faq": faq}


@app.post("/step4")
@_reports_step("step4")
def step4(req: Step4Request) -> dict:
    """Step 4: write the report (Call 3), then run the SEO/readability revision pass (Call 4)."""
    run = _get_run(req.run_token)
    if run.report is None or run.selection is None or run.evidence is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 3 first.")
    if config.CMI_CHAIN_V2:
        return _step4_chain(run)
    try:
        _, title, sections, evidence_citations = pipeline.run_write(
            run.article, run.orient, run.sponsor, run.report, run.evidence, run.logger, longtails=run.longtails
        )
        title, sections, evidence_citations = pipeline.run_revise(
            title, sections, evidence_citations, run.report, run.logger, run.evidence, run.sponsor.text,
            longtails=run.longtails,
        )
        faq = pipeline.run_faq(run.report.name, run.report, title, sections, run.logger, sponsor_text=run.sponsor.text)
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "Step 4", e)
    run.title, run.sections, run.evidence_citations, run.faq = title, sections, evidence_citations, faq
    run.logger.info(f"STEP 4 DONE — draft '{title}' ({len(sections)} sections, {len(evidence_citations)} evidence citations, {len(faq)} FAQ entries)")
    return {"title": title, "sections": _display_sections(sections, run.sponsor, run.report.url), "faq": faq}


@app.get("/run/{run_token}")
def get_run(run_token: str) -> dict:
    """Full snapshot of a run, for the frontend to rehydrate itself after a page reload.

    Returns everything a fresh client needs to rebuild ``state`` without re-running any
    pipeline step: the article, the confirmed sponsor paragraph, and whichever of
    orient/select/write has completed so far.
    """
    run = _get_run(run_token)
    return {
        "run_token": run_token,
        "filename": run.filename,
        "article": _article_dict(run.article),
        "sponsor_index": run.sponsor.index,
        "orient": run.orient,
        "candidates": [_candidate_dict(c) for c in run.candidates],
        "hits": [_hit_dict(h) for h in run.hits] if run.hits is not None else [],
        "claim_hits": _claim_hits_dict(run.claim_pool),
        "report": _report_dict(run.report) if run.report else None,
        "selection": run.selection,
        "questions": run.questions,
        "demand": run.demand,
        "fact_sheet": run.fact_sheet,
        "publisher": run.publisher,
        "plan": run.plan,
        "long_tail_keywords": run.longtails,
        "timings": run.timings,
        "title": run.title,
        "sections": (
            _display_sections(run.sections, run.sponsor, run.report.url if run.report else None)
            if run.sections else []
        ),
        "faq": run.faq,
    }


def _keyword_view(run: RunState, report: reports.Report, sponsor: Paragraph, title: str, sections: list[dict]) -> dict:
    """The keyword side of a draft's display: its sections with the keyword spans marked, the keyword lists
    and the use counts the highlighting itself found. Shared by the final screen and the recheck of a
    humanized draft, so the two can never count a keyword differently."""
    primary_kw, secondary_plan = pipeline._seo_keyword_plan(report.name)
    secondary_kws = [kw for kw, _ in secondary_plan]
    # Market-first chain extras: the search demand's related (tertiary) terms are highlighted and
    # counted the same way, and the plan's MSQ headings are reported so the final view can mark them.
    tertiary_terms = [t for t in (getattr(run.plan_inputs, "terms", []) or [])] if run.plan_inputs is not None else []
    msq_headings = [ps["heading"] for ps in ((run.plan or {}).get("sections") or []) if ps.get("msq")]
    # Whether each generated long-tail phrase survived into the finished draft — a literal,
    # whitespace-normalised match, since WRITE freezes a phrase's word order. Reported to the UI
    # and logged; an unused candidate is fine by design (the writer was told to use at most 3 and
    # only where they genuinely fit), so this is visibility, never a failure gate.
    longtail_usage = pipeline._longtail_usage(title, sections, run.longtails)
    used = [t for t in longtail_usage if t["used"]]
    run.logger.info(
        f"[long-tail] the final draft uses {len(used)}/{len(longtail_usage)} candidate phrase(s): "
        + (", ".join(t["keyword"] for t in used) or "(none)")
    )
    # Keyword spans are found server-side (checks.secondary_keyword_spans_in_paragraph — same
    # paragraph-scoped, section-scoped matching the pipeline's own coverage check uses) rather
    # than left to the frontend to re-search for the literal phrase, which finds nothing once a
    # keyword has been naturally reworded (e.g. "the size of the market" for "market size") or
    # when the market's name isn't repeated in every sentence that touches one of its keywords.
    keyword_counts: dict = {}
    display_sections = _display_sections(
        sections, sponsor, report.url, primary_kw, secondary_plan,
        longtails=[t["keyword"] for t in longtail_usage], counts=keyword_counts,
        tertiary_terms=tertiary_terms,
    )
    tertiary_total = sum(keyword_counts.get("tertiary", {}).values())
    tertiary_used = len(keyword_counts.get("tertiary", {}))
    run.logger.info(
        f"[keywords] highlighted uses — primary x{keyword_counts.get('primary', 0)}, "
        f"secondary {sum(keyword_counts.get('secondary', {}).values())}x "
        f"({len(keyword_counts.get('secondary', {}))}/{len(secondary_kws)} keywords), "
        f"tertiary {tertiary_total}x ({tertiary_used}/{len(tertiary_terms)} terms), "
        f"long-tail {sum(keyword_counts.get('longtail', {}).values())}x "
        f"({len(keyword_counts.get('longtail', {}))}/{len(longtail_usage)} phrases)"
    )
    if msq_headings:
        run.logger.info("[plan] MSQ headings: " + "; ".join(msq_headings))
    return {
        "sections": display_sections,
        "primary_keyword": primary_kw,
        "secondary_keywords": secondary_kws,
        "tertiary_terms": tertiary_terms,
        "msq_headings": msq_headings,
        "keyword_counts": keyword_counts,
        "long_tail_keywords": [
            {"keyword": t["keyword"], "intent": t["intent"], "used": t["used"]}
            for t in longtail_usage
        ],
    }


@app.post("/finalize")
def finalize(req: FinalizeRequest) -> dict:
    t0 = time.time()
    run = _get_run(req.run_token)
    if not run.title or not run.sections:
        raise HTTPException(400, "No draft yet — finish Step 4 first.")
    assert run.report is not None and run.selection is not None and run.evidence is not None and run.hits is not None
    try:
        result = pipeline.run_finalize(
            run.article, run.sponsor, run.report,
            run.candidates, run.orient, run.selection, run.hits,
            run.title, run.sections, run.evidence,
            run.filename, run.logger,
            evidence_citations=run.evidence_citations,
            faq=run.faq, longtails=run.longtails,
            plan=run.plan, plan_inputs=run.plan_inputs,
        )
    except (PipelineError, LLMError) as e:
        _emit_run_completed(run, req.run_token, "error", t0, run.title or run.filename, run.sections)
        llm.run_cost_summary(run.logger)  # also frees the tracker on the error path (see run_cost_totals)
        raise _pipeline_failure(run.logger, "finalize", e)
    # The run is kept alive (not popped) so the final page's "Regenerate draft" button can
    # resend /step4 + /finalize against this same token for a different draft — it's still
    # bounded by the normal RUN_TTL_S/200-run eviction in _prune_runs, same as any other run.
    run_totals = llm.run_cost_delta(run.logger)  # what finalize added; taken BEFORE run_cost_summary() pops the tracker (telemetry, below)
    run.logger.info("LLM COST FOR THIS RUN (logs only — not shown in the UI):")
    for line in llm.run_cost_summary(run.logger):
        run.logger.info(line)
    run.logger.info(f"RUN COMPLETE — {result.output_path}")
    # Telemetry (fire-and-forget, telemetry.py): the totals snapshot was taken from the tracker
    # BEFORE run_cost_summary() popped it, so the dashboard sees the same per-run numbers as the log.
    _emit_run_completed(run, req.run_token, "ok", t0, result.title, result.sections, run_totals)
    # Map each citation's evidence ID back to its original fact text for a human-readable label
    # in the UI (e.g. a hover tooltip) — the citation itself only carries the ID + paragraph.
    facts_by_id = {str(e.get("id", "")).strip().upper(): e.get("fact", "") for e in (run.selection.get("evidence") or [])}
    # Article-evidence citations carry their CLAIM id (C1, C2, ...); one claim can have up to two backing items.
    for b in run.selection.get("claim_backing") or []:
        cid = str(b.get("claim_id", "")).strip().upper()
        line = f"[{b.get('stance', '')}] {b.get('fact', '')} ({b.get('source', '')})"
        facts_by_id[cid] = f"{facts_by_id[cid]} | {line}" if facts_by_id.get(cid) else line
    citations_out = [
        {"id": c["id"], "paragraph": c["paragraph"], "fact": facts_by_id.get(c["id"], "")}
        for c in result.evidence_citations
    ]
    view = _keyword_view(run, result.report, result.sponsor, result.title, result.sections)
    return {
        "result": {
            "title": result.title,
            **view,
            "report": _report_dict(result.report),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks
            ],
            "evidence_citations": citations_out,
            "output_filename": result.output_path.name if result.output_path else None,
            "faq": result.faq,
        }
    }


@app.post("/recheck")
async def recheck(run_token: str, file: UploadFile = File(...)) -> dict:
    """Rechecks a humanized draft of a finished run: the .docx is mapped back onto the run's own draft
    shape (recheck.parse), then the run's SOP checks and keyword highlighting are applied to it with
    everything the run knows — plan, fact sheet, evidence, sponsor, report. No LLM or network call."""
    run = _get_run(run_token)
    if run.plan is None or run.plan_inputs is None or not run.title or not run.sections:
        raise HTTPException(400, "Rechecking needs a finished run — finish this run's draft first.")
    assert run.report is not None and run.selection is not None and run.hits is not None
    name = file.filename or "draft.docx"
    if not name.lower().endswith(".docx"):
        raise HTTPException(400, "Please upload a Word document (.docx).")
    raw = await file.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "File too large — please upload a .docx under 20MB.")
    if not raw:
        raise HTTPException(400, "Uploaded file is empty.")
    try:
        parsed = recheck_mod.parse(raw, run.title, run.sections, run.faq, run.sponsor.text)
    except Exception as e:
        run.logger.error(f"recheck could not read {name}: {type(e).__name__}: {e}", exc_info=True)
        raise HTTPException(400, "This file could not be read as a Word document (.docx). "
                                 "If it came from Google Docs or an older .doc file, re-save it as .docx and try again.")
    check_results = checks.run_checks_v2(
        parsed["title"], parsed["sections"], run.plan, run.plan_inputs, faq=parsed["faq"],
        sponsor_brand_terms=pipeline._brand_terms(run.sponsor), sponsor_text=run.sponsor.text,
        report_text=pipeline._report_text(run.report), report_name=run.report.name,
        selection=run.selection, hits=run.hits, humanized=True,
    )
    run.logger.info(f"[recheck] humanized draft {name!r}: "
                    f"{sum(1 for c in check_results if c.passed)}/{len(check_results)} checks pass")
    view = _keyword_view(run, run.report, run.sponsor, parsed["title"], parsed["sections"])
    return {"result": {
        "title": parsed["title"],
        **view,
        "report": _report_dict(run.report),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in check_results],
        "faq": parsed["faq"],
        "notes": parsed["notes"],
        "filename": Path(name).name,
    }}


@app.get("/download/{filename}")
def download(filename: str):
    if Path(filename).name != filename or not filename.lower().endswith(".docx"):
        raise HTTPException(400, "Invalid download filename.")
    path = (OUTPUT_DIR.resolve() / Path(filename).name)
    if OUTPUT_DIR.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "File not found. It may have been cleaned up — re-run the pipeline.")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=path.name,
    )


# ---------------------------------------------------------------- serve the frontend UI (added last so API routes win)

class _RevalidatingStaticFiles(StaticFiles):
    """Without a Cache-Control header browsers cache index.html/app.js heuristically (from
    Last-Modified) and keep showing the old UI after the files are updated, until a hard refresh.
    "no-cache" makes them revalidate on every load — a cheap 304 when nothing changed."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", _RevalidatingStaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
