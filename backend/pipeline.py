import logging
import re
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import llm
import prompts
import reports
import search
from checks import CheckResult, run_checks
from docx_io import Paragraph, SourceArticle, output_path_for, write_docx
from pipeline_log import new_run_logger, section

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_BODY_CHARS = 12000

Segment = tuple[str, str | None]  # (text, hyperlink url or None)


class PipelineError(RuntimeError):
    pass


@dataclass
class RevampResult:
    title: str
    sections: list[dict]
    sponsor: Paragraph
    report: reports.Report
    candidates: list[reports.CatalogEntry]
    orient: dict
    selection: dict
    hits: list[search.SearchHit]
    checks: list[CheckResult]
    output_path: Path | None = None

    def segments(self, paragraph: str) -> list[Segment]:
        """Expand placeholders: the retained paragraph with its original links, and the CMI report link."""
        if paragraph.strip() == prompts.SPONSOR_TOKEN:
            return sponsor_segments(self.sponsor)
        out: list[Segment] = []
        pos = 0
        for m in re.finditer(prompts.CMI_LINK_PATTERN, paragraph):
            out.append((paragraph[pos:m.start()], None))
            out.append((m.group(1), self.report.url))
            pos = m.end()
        out.append((paragraph[pos:], None))
        return [s for s in out if s[0]]


def sponsor_segments(sponsor: Paragraph) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    for link in sponsor.links:
        i = sponsor.text.find(link.anchor, pos) if link.anchor else -1
        if i < 0:
            continue
        out.append((sponsor.text[pos:i], None))
        out.append((link.anchor, link.url))
        pos = i + len(link.anchor)
    out.append((sponsor.text[pos:], None))
    return [s for s in out if s[0]]


def _host(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _brand_terms(sponsor: Paragraph) -> list[str]:
    terms = []
    for link in sponsor.links:
        if link.is_external:
            name = _host(link.url).split(".")[0]
            terms += [name, name.replace("-", " ")]
    return list(dict.fromkeys(terms))


def _format_hits(hits: list[search.SearchHit]) -> str:
    lines = []
    for i, h in enumerate(hits, 1):
        if h.is_news:
            date = h.date.strftime("%Y-%m-%d") if h.date else "undated"
            tag = f"NEWS, {date}"
        else:
            tag = "BACKGROUND, undated"
        lines.append(f"[W{i}] {h.title} | {h.source or h.host} | {tag}\n      {h.snippet[:300]}")
    return "\n".join(lines) or "(no results)"


def _report_text(report: reports.Report) -> str:
    return report.sections["live_report_page"]


# ---------------------------------------------------------------- stepwise API (used by the Streamlit UI for gated runs)
# Each step logs exactly as the monolithic run does; _revamp_inner below is now
# just these steps chained together, so CLI / non-gated behaviour is unchanged.

def run_orient(article: SourceArticle, sponsor: Paragraph, logger: logging.Logger) -> dict:
    """CALL 1: identify the market and plan research. Returns the parsed JSON."""
    section(logger, "CALL 1: ORIENT")
    orient = llm.chat_json(prompts.ORIENT_SYSTEM, prompts.ORIENT_USER.format(
        categories=", ".join(reports.categories()),
        title=article.title,
        body=article.body[:_BODY_CHARS],
        sponsor=sponsor.text,
    ), call_name="orient", logger=logger)
    names = [n for n in orient.get("candidate_markets") or [] if isinstance(n, str)]
    keywords = [k for k in orient.get("scope_keywords") or [] if isinstance(k, str)]
    if not names and not keywords:
        raise PipelineError("Call 1 returned no candidate markets or scope keywords")
    return orient


def _orient_names(orient: dict) -> tuple[list[str], list[str]]:
    keywords = [k for k in orient.get("scope_keywords") or [] if isinstance(k, str)]
    names = [n for n in orient.get("candidate_markets") or [] if isinstance(n, str)]
    return keywords, names


def run_match_catalog(
    orient: dict, logger: logging.Logger,
    progress: Callable[[str], None] = lambda _: None,
) -> list[reports.CatalogEntry]:
    """CMI report catalog matching only — no web search. Fast (DB + embeddings, no network
    calls to third parties), so Step 1 can return to the human for market confirmation without
    first paying for a full web-search pass against a market they haven't picked yet."""
    keywords, names = _orient_names(orient)
    if not names and not keywords:
        raise PipelineError("Call 1 returned no candidate markets or scope keywords")
    section(logger, "CATALOG MATCH")
    progress("Matching candidate markets against the CMI report catalog")
    logger.info(f"scope keywords from LLM: {keywords}")
    logger.info(f"candidate market names from LLM: {names}")

    # Rank-ordered merge: the LLM returns candidate_markets most-fitting-first, so matches
    # for guess #1 outrank matches for guess #2, and so on. Within one guess, near-exact
    # text matches come before semantic neighbors; the unranked scope-keyword pool goes last
    # as its own source. (Previously every guess pooled into one list, so a lower guess's
    # exact title overlap — e.g. CRM at guess #2 — outranked the top guess's neighborhood.)
    PER_GUESS = 3
    text_groups = reports.match_markets_grouped(names, per_name=PER_GUESS) if names else []
    for i, group in enumerate(text_groups):
        logger.info(f"text-match guess #{i + 1} {names[i]!r}: "
                    + ", ".join(f"{e.news_id}:{e.keyword} ({s:.2f})" for s, e in group))
    try:
        name_groups = reports.match_markets_semantic_grouped(names, per_name=PER_GUESS) if names else []
        for i, group in enumerate(name_groups):
            logger.info(f"name semantic-match guess #{i + 1}: "
                        + ", ".join(f"{e.news_id}:{e.keyword} ({s:.3f})" for s, e in group))
    except Exception:
        logger.warning("name semantic match failed", exc_info=True)
        name_groups = [[] for _ in names]
    try:
        keyword_matches = reports.match_markets_semantic(keywords, limit=12) if keywords else []
        logger.info("keyword semantic-match reports: " + ", ".join(f"{c.news_id}:{c.keyword}" for c in keyword_matches))
    except Exception:
        logger.warning("keyword semantic match failed", exc_info=True)
        keyword_matches = []

    guess_buckets: list[list[reports.CatalogEntry]] = []
    for text_group, name_group in zip_longest(text_groups, name_groups, fillvalue=[]):
        guess_buckets.append([e for _, e in text_group] + [e for _, e in name_group])
    # Round-robin across [guess #1, guess #2, …, keywords] so every guess's best pick
    # precedes every guess's second pick, while the keyword pool keeps representation.
    sources: list[list[reports.CatalogEntry]] = guess_buckets + ([keyword_matches] if keyword_matches else [])
    seen: set[int] = set()
    candidates: list[reports.CatalogEntry] = []
    for i in range(max((len(s) for s in sources), default=0)):
        for s in sources:
            if i < len(s) and s[i].news_id not in seen:
                seen.add(s[i].news_id)
                candidates.append(s[i])
    candidates = candidates[:25]
    logger.info("ranked candidates: " + ", ".join(f"{c.news_id}:{c.keyword}" for c in candidates))

    if not candidates:
        raise PipelineError(f"No CMI report matched any of: {', '.join(names + keywords)}")
    return candidates


def _market_search_queries(market_name: str) -> list[str]:
    """Deterministic queries built directly from the HUMAN-CONFIRMED market name — not the
    LLM's pre-confirmation guess, which may differ if a human picked a different candidate
    than the model's top pick. No LLM call needed; this is template substitution.

    Each query targets ONE specific angle and is phrased the way a market analyst searches
    (naming a concrete kind of event — funding, launch, adoption, regulation), not the way a
    marketer searches ("guide", "best tools") — that generic phrasing reliably surfaces
    buyer's-guide/listicle pages that filter_hits rejects anyway, wasting the request."""
    return [
        f"{market_name} market trend 2026 analyst",
        f"{market_name} adoption drivers",
        f"{market_name} vendor product launch OR acquisition OR funding",
        f"{market_name} United States market developments",
        f"{market_name} regulatory OR compliance development",
        f"{market_name} industry announcement 2026",
    ]


def run_search_evidence(
    market_name: str, sponsor: Paragraph, candidates: list[reports.CatalogEntry], logger: logging.Logger,
    progress: Callable[[str], None] = lambda _: None,
) -> list[search.SearchHit]:
    """Web search for supporting evidence, run AFTER the human has confirmed the market —
    queries are built from the confirmed market name itself, so this never searches for a
    market the human ultimately rejected."""
    section(logger, "WEB SEARCH")
    queries = _market_search_queries(market_name)
    progress(f"Searching the web for {market_name} ({len(queries)} queries)")
    sponsor_hosts = tuple(_host(link.url) for link in sponsor.links if link.is_external)
    gate_phrases = [market_name] + [c.keyword for c in candidates[:5]]
    raw_hits = search.run_searches(queries, logger=logger)
    hits = search.filter_hits(raw_hits, gate_phrases, sponsor_hosts, logger=logger)
    return hits


def run_select(
    article: SourceArticle, orient: dict, sponsor: Paragraph,
    report: reports.Report, hits: list[search.SearchHit],
    logger: logging.Logger,
) -> dict:
    """CALL 2: select evidence for the human-confirmed report. Returns the parsed JSON.
    Report choice is no longer this call's job — the human picked the market/report in Step 1
    (see run_fetch_confirmed_report), so this call only judges which search hits are usable."""
    section(logger, "CALL 2: SELECT EVIDENCE")
    selection = llm.chat_json(prompts.SELECT_SYSTEM, prompts.SELECT_USER.format(
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        bridge_angle=orient.get("bridge_angle", ""),
        sponsor=sponsor.text,
        report_name=report.name,
        hits=_format_hits(hits),
    ), call_name="select", logger=logger)
    return selection


def run_fetch_confirmed_report(chosen: reports.CatalogEntry, logger: logging.Logger) -> reports.Report:
    """Fetch the report the human confirmed in Step 1 — a direct lookup, no LLM involved."""
    report = reports.fetch_report(chosen.news_id, logger=logger)
    logger.info(f"confirmed report: {report.news_id} {report.name} ({report.source}) {report.url}")
    return report


def run_build_evidence(
    selection: dict, hits: list[search.SearchHit], logger: logging.Logger,
) -> str:
    """Build the evidence block passed to the writer from Call 2 output + search hits."""
    evidence_lines = []
    for e in selection.get("evidence") or []:
        m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
        if not m or not 1 <= int(m.group(1)) <= len(hits):
            logger.info(f"evidence item dropped (bad id {e.get('id')!r}): {e}")
            continue
        h = hits[int(m.group(1)) - 1]
        date = h.date.strftime("%Y-%m") if h.date else "undated"
        evidence_lines.append(f"- [{e.get('use_for', 'context')}] {e.get('fact', '')} (source: {h.source or h.host}, {date})")
    evidence = "\n".join(evidence_lines) or "(none)"
    section(logger, "FINAL EVIDENCE PASSED TO WRITER")
    logger.info(evidence)
    return evidence


def run_write(
    article: SourceArticle, orient: dict, sponsor: Paragraph,
    report: reports.Report, evidence: str, logger: logging.Logger,
) -> tuple[dict, str, list[dict]]:
    """CALL 3: write the article. Returns (raw_json, title, sections)."""
    section(logger, "CALL 3: WRITE")
    report_text = _report_text(report)
    written = llm.chat_json(prompts.WRITE_SYSTEM, prompts.WRITE_USER.format(
        sop=prompts.SOP,
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        bridge_angle=orient.get("bridge_angle", ""),
        original_body=article.body[:_BODY_CHARS],
        sponsor=sponsor.text,
        sponsor_token=prompts.SPONSOR_TOKEN,
        report_name=report.name,
        report_text=report_text,
        evidence=evidence,
    ), temperature=0.5, call_name="write", logger=logger)

    sections = [
        {
            "heading": str(s.get("heading") or ""),
            "sop": [str(t) for t in s.get("sop") or []],
            "paragraphs": [str(p) for p in s.get("paragraphs") or [] if str(p).strip()],
        }
        for s in written.get("sections") or []
    ]
    title = str(written.get("title") or "")
    if not sections:
        raise PipelineError("Call 3 returned no article sections")
    return written, title, sections


def run_finalize(
    article: SourceArticle, sponsor: Paragraph, report: reports.Report,
    candidates: list[reports.CatalogEntry], orient: dict, selection: dict,
    hits: list[search.SearchHit],
    title: str, sections: list[dict], evidence: str,
    original_filename: str, logger: logging.Logger,
) -> RevampResult:
    """SOP checks + write .docx. Returns the finished RevampResult."""
    section(logger, "SOP CHECKS")
    report_text = _report_text(report)
    checks = run_checks(
        title, sections, f"{report_text}\n{evidence}\n{sponsor.text}", _brand_terms(sponsor),
        sponsor.text, article.body, report.name, selection, hits,
    )
    for c in checks:
        logger.info(("PASS " if c.passed else "FAIL ") + c.name + (f" - {c.detail}" if c.detail else ""))

    result = RevampResult(title, sections, sponsor, report, candidates, orient, selection, hits, checks)
    output_path = output_path_for(original_filename)
    write_docx(output_path, title, sections, result.segments)
    result.output_path = output_path
    logger.info(f"OUTPUT FILE: {output_path}")
    return result


def revamp(
    article: SourceArticle, sponsor: Paragraph, original_filename: str,
    progress: Callable[[str], None] = lambda _: None, logger: logging.Logger | None = None,
) -> RevampResult:
    """Runs the pipeline; any failure (network, database, LLM, bad model output) is logged with
    a full traceback to the per-run log file before being re-raised, so a failure on an end user's
    laptop always leaves a diagnosable trail even though no one is watching the terminal live.
    On success, writes the revamped article to output/Revised_<original_filename>.docx."""
    log_path = None
    if logger is None:
        logger, log_path = new_run_logger()
        progress(f"Logging this run to {log_path}")
    try:
        return _revamp_inner(article, sponsor, original_filename, progress, logger)
    except Exception:
        logger.critical(f"PIPELINE FAILED — see traceback below (log file: {log_path})", exc_info=True)
        raise


def _revamp_inner(
    article: SourceArticle, sponsor: Paragraph, original_filename: str,
    progress: Callable[[str], None], logger: logging.Logger,
) -> RevampResult:
    logger.info(f"ARTICLE TITLE: {article.title}")
    logger.info(f"SPONSOR PARAGRAPH: {sponsor.text}")
    logger.info(f"SPONSOR LINKS: {[(l.anchor, l.url) for l in sponsor.links]}")

    progress("Step 1: identifying the market and planning research")
    orient = run_orient(article, sponsor, logger)

    progress("Matching candidate markets against the CMI report catalog")
    candidates = run_match_catalog(orient, logger, progress)

    # No human available to confirm a market in this non-interactive path (used by the CLI/test
    # script) — take the top-ranked candidate, same as the UI's default radio selection.
    progress("Fetching the CMI report")
    report = run_fetch_confirmed_report(candidates[0], logger)

    progress("Searching the web for supporting evidence")
    hits = run_search_evidence(candidates[0].keyword, sponsor, candidates, logger, progress)

    progress("Step 2: selecting evidence")
    selection = run_select(article, orient, sponsor, report, hits, logger)
    evidence = run_build_evidence(selection, hits, logger)

    progress("Step 3: writing the article")
    written, title, sections = run_write(article, orient, sponsor, report, evidence, logger)

    progress("Checking the draft against the SOP")
    result = run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, original_filename, logger,
    )

    progress("Writing the revised article to output/")
    return result
