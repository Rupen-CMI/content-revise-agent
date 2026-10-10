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
import re
import secrets
import time
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
    selection: dict | None = None
    evidence: str | None = None
    title: str = ""
    sections: list[dict] = field(default_factory=list)


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
    }


def _report_dict(r: reports.Report) -> dict:
    return {"news_id": r.news_id, "name": r.name, "url": r.url}


def _segment_dicts(text: str, sponsor: Paragraph, report_url: str | None) -> list[dict]:
    """Expand a written paragraph's placeholders into the same (text, url) segments
    ``RevampResult.segments()`` uses for the .docx — the JSON UI renders real hyperlinks too,
    so it needs the URLs, not flattened plain text."""
    if text.strip() == prompts.SPONSOR_TOKEN:
        return [{"text": t, "url": u} for t, u in pipeline.sponsor_segments(sponsor)]
    out: list[dict] = []
    pos = 0
    for m in _CMI_LINK_RE.finditer(text):
        if text[pos:m.start()]:
            out.append({"text": text[pos:m.start()], "url": None})
        out.append({"text": m.group(1), "url": report_url})
        pos = m.end()
    if text[pos:]:
        out.append({"text": text[pos:], "url": None})
    return out


def _display_sections(sections: list[dict], sponsor: Paragraph, report_url: str | None) -> list[dict]:
    return [
        {
            "heading": s.get("heading", ""),
            "paragraphs": [_segment_dicts(p, sponsor, report_url) for p in s.get("paragraphs", [])],
        }
        for s in sections
    ]


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
        hits = pipeline.run_search_evidence(report.name, run.sponsor, run.candidates, run.logger)
    except (PipelineError, LLMError, LiveFetchError) as e:
        raise _pipeline_failure(run.logger, "Step 2", e)
    run.report, run.hits = report, hits
    run.logger.info(f"STEP 2 DONE — report {report.news_id} {report.name}, {len(hits)} hits")
    return {"report": _report_dict(report), "hits": [_hit_dict(h) for h in hits]}


@app.post("/step3")
def step3(req: Step3Request) -> dict:
    """Step 3: fetch market report facts. Call 2 (LLM) selects usable evidence from the Step 2
    web hits, scoped to the confirmed report."""
    run = _get_run(req.run_token)
    if run.report is None or run.hits is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 2 first.")
    try:
        selection = pipeline.run_select(run.article, run.orient, run.sponsor, run.report, run.hits, run.logger)
        evidence = pipeline.run_build_evidence(selection, run.hits, run.logger)
    except (PipelineError, LLMError, LookupError) as e:
        raise _pipeline_failure(run.logger, "Step 3", e)
    run.selection, run.evidence = selection, evidence
    run.logger.info(f"STEP 3 DONE — {len(selection.get('evidence') or [])} evidence item(s) selected")
    return {"selection": selection, "evidence": evidence}


@app.post("/step4")
def step4(req: Step4Request) -> dict:
    """Step 4: write the report. Call 3 (LLM)."""
    run = _get_run(req.run_token)
    if run.report is None or run.selection is None or run.evidence is None:
        raise HTTPException(400, "No confirmed report/evidence yet — finish Step 3 first.")
    try:
        _, title, sections = pipeline.run_write(
            run.article, run.orient, run.sponsor, run.report, run.evidence, run.logger
        )
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "Step 4", e)
    run.title, run.sections = title, sections
    run.logger.info(f"STEP 4 DONE — draft '{title}' ({len(sections)} sections)")
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
        )
    except (PipelineError, LLMError) as e:
        raise _pipeline_failure(run.logger, "finalize", e)
    _RUNS.pop(req.run_token, None)  # single-use: the run is complete
    run.logger.info(f"RUN COMPLETE — {result.output_path}")
    return {
        "result": {
            "title": result.title,
            "sections": _display_sections(result.sections, result.sponsor, result.report.url),
            "report": _report_dict(result.report),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks
            ],
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

_FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if _FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_FRONTEND_DIR), html=True), name="frontend")
