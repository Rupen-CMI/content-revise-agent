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

Requires the root ``.env`` (``DB_URL`` plus optional ``LLM_*`` settings).
"""

import json
import logging
import os
import re
import secrets
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path

import psycopg2
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import checks
import freshness
import llm
import pipeline
import prompts
import reports
import search
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
    selection: dict | None = None
    evidence: str | None = None
    title: str = ""
    sections: list[dict] = field(default_factory=list)
    evidence_citations: list[dict] = field(default_factory=list)


_RUNS: dict[str, RunState] = {}


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
    if isinstance(exc, psycopg2.Error):
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
) -> list[dict]:
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
                segs = _apply_keyword_spans(segs, primary_kw, section_secondary)
            paragraphs.append(segs)
        out.append({"heading": s.get("heading", ""), "paragraphs": paragraphs})
    return out




def _apply_keyword_spans(
    segments: list[dict], primary_kw: str, section_secondary: list[tuple[str, str]],
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
    if primary_kw:
        low_plain, low_phrase = plain.lower(), primary_kw.lower()
        pos = 0
        while (i := low_plain.find(low_phrase, pos)) != -1:
            for k in range(i, i + len(low_phrase)):
                taken[k] = "primary"
            pos = i + len(low_phrase)

    # Pass 2: each section-scoped secondary keyword's suffix word fills in whatever's left, but
    # TRIM at any cell the primary pass already claimed instead of discarding the whole match — a
    # secondary keyword's suffix can legitimately sit right next to the primary keyword's own text.
    for kw, suffix in section_secondary:
        for start, end in checks.secondary_keyword_spans_in_paragraph(plain, primary_kw, suffix):
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
    try:
        orient = pipeline.run_orient(article, sponsor, logger)
        candidates = pipeline.run_match_catalog(orient, logger)
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(logger, "Step 1", e)

    _prune_runs()
    token = secrets.token_hex(16)
    _RUNS[token] = RunState(
        filename=Path(req.filename).name or "upload.docx",
        article=article, sponsor=sponsor, orient=orient,
        candidates=candidates, logger=logger, log_path=log_path,
    )
    logger.info(f"STEP 1 DONE — run token {token[:8]}…, {len(candidates)} candidates")
    return {
        "token": token,
        "run_token": token,
        "orient": orient,
        "candidates": [_candidate_dict(c) for c in candidates],
    }


@app.post("/step2")
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
    try:
        report = pipeline.run_fetch_confirmed_report(chosen, run.logger)
        claims = pipeline.orient_claims(run.orient)
        if claims:
            # Same split as the ungated path: the claim-backing search (text web) runs in its own
            # thread next to the market search (news + feeds), and the request waits once.
            with ThreadPoolExecutor(1) as claim_ex:
                claim_future = claim_ex.submit(
                    pipeline.run_claim_evidence, claims, run.logger,
                )
                hits = pipeline.run_search_evidence(
                    report.name, run.sponsor, run.candidates, run.logger,
                    key_players=reports.key_players(report), orient=run.orient, segments=report.segment_terms,
                )
                run.claim_pool = claim_future.result()
        else:
            hits = pipeline.run_search_evidence(
                report.name, run.sponsor, run.candidates, run.logger,
                key_players=reports.key_players(report), orient=run.orient, segments=report.segment_terms,
            )
    except (PipelineError, LLMError, LiveFetchError) as e:
        raise _pipeline_failure(run.logger, "Step 2", e)
    run.report, run.hits = report, hits
    run.logger.info(f"STEP 2 DONE — report {report.news_id} {report.name}, {len(hits)} hits")
    return {
        "report": _report_dict(report), "hits": [_hit_dict(h) for h in hits],
        "claim_hits": _claim_hits_dict(run.claim_pool),
    }


@app.post("/step3")
def step3(req: Step3Request) -> dict:
    """Step 3: fetch market report facts. Call 2 (LLM) selects usable evidence from the Step 2
    web hits, scoped to the confirmed report."""
    run = _get_run(req.run_token)
    if run.report is None or run.hits is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 2 first.")
    try:
        selection = pipeline.run_select(run.article, run.orient, run.sponsor, run.report, run.hits, run.logger, claim_hits=run.claim_pool)
        evidence = pipeline.run_build_evidence(selection, run.hits, run.logger, orient=run.orient)
    except (PipelineError, LLMError, LookupError) as e:
        raise _pipeline_failure(run.logger, "Step 3", e)
    run.selection, run.evidence = selection, evidence
    run.logger.info(f"STEP 3 DONE — {len(selection.get('evidence') or [])} evidence item(s) selected")
    return {"selection": selection, "evidence": evidence}


@app.post("/step4")
def step4(req: Step4Request) -> dict:
    """Step 4: write the report (Call 3), then run the SEO/readability revision pass (Call 4)."""
    run = _get_run(req.run_token)
    if run.report is None or run.selection is None or run.evidence is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 3 first.")
    try:
        _, title, sections, evidence_citations = pipeline.run_write(
            run.article, run.orient, run.sponsor, run.report, run.evidence, run.logger
        )
        title, sections, evidence_citations = pipeline.run_revise(
            title, sections, evidence_citations, run.report, run.logger, run.evidence, run.sponsor.text
        )
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "Step 4", e)
    run.title, run.sections, run.evidence_citations = title, sections, evidence_citations
    run.logger.info(f"STEP 4 DONE — draft '{title}' ({len(sections)} sections, {len(evidence_citations)} evidence citations)")
    return {"title": title, "sections": _display_sections(sections, run.sponsor, run.report.url)}


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
        "title": run.title,
        "sections": (
            _display_sections(run.sections, run.sponsor, run.report.url if run.report else None)
            if run.sections else []
        ),
    }


@app.post("/finalize")
def finalize(req: FinalizeRequest) -> dict:
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
        )
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "finalize", e)
    # The run is kept alive (not popped) so the final page's "Regenerate draft" button can
    # resend /step4 + /finalize against this same token for a different draft — it's still
    # bounded by the normal RUN_TTL_S/200-run eviction in _prune_runs, same as any other run.
    run.logger.info("LLM COST FOR THIS RUN (logs only — not shown in the UI):")
    for line in llm.run_cost_summary(run.logger):
        run.logger.info(line)
    run.logger.info(f"RUN COMPLETE — {result.output_path}")
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
    primary_kw, secondary_plan = pipeline._seo_keyword_plan(result.report.name)
    secondary_kws = [kw for kw, _ in secondary_plan]
    # Keyword spans are found server-side (checks.secondary_keyword_spans_in_paragraph — same
    # paragraph-scoped, section-scoped matching the pipeline's own coverage check uses) rather
    # than left to the frontend to re-search for the literal phrase, which finds nothing once a
    # keyword has been naturally reworded (e.g. "the size of the market" for "market size") or
    # when the market's name isn't repeated in every sentence that touches one of its keywords.
    return {
        "result": {
            "title": result.title,
            "sections": _display_sections(
                result.sections, result.sponsor, result.report.url, primary_kw, secondary_plan,
            ),
            "report": _report_dict(result.report),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks
            ],
            "evidence_citations": citations_out,
            "primary_keyword": primary_kw,
            "secondary_keywords": secondary_kws,
            "output_filename": result.output_path.name if result.output_path else None,
        }
    }


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
