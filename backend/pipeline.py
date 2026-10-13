import logging
import re
from dataclasses import dataclass
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
            tag = f"BACKGROUND, {h.date.strftime('%Y-%m-%d')}" if h.date else "BACKGROUND, undated"
        lines.append(f"[W{i}] {h.title} | {h.source or h.host} | {tag}\n      {h.snippet[:300]}")
    return "\n".join(lines) or "(no results)"


def _report_text(report: reports.Report) -> str:
    return report.sections["live_report_page"]


# ---------------------------------------------------------------- stepwise API (used by the Streamlit UI for gated runs)
# Each step logs exactly as the monolithic run does; _revamp_inner below is now
# just these steps chained together, so CLI / non-gated behaviour is unchanged.

_SEGMENT_SUFFIX_RE = re.compile(r",\s*by\s+", re.IGNORECASE)


def _bare_market_names(names) -> list[str]:
    """Strip ", By <Segment> (...)" report-title suffixes and dedupe, keeping order. The catalog
    matches on bare report names ("Wealth Management Platform Market"); the extra segmentation
    words dilute the word-overlap score so the exact catalog report stops matching at all."""
    out: list[str] = []
    seen: set[str] = set()
    for n in names or []:
        if not isinstance(n, str):
            continue
        bare = _SEGMENT_SUFFIX_RE.split(n, maxsplit=1)[0].strip()
        if bare and bare.lower() not in seen:
            seen.add(bare.lower())
            out.append(bare)
    return out


def run_orient(article: SourceArticle, sponsor: Paragraph, logger: logging.Logger) -> dict:
    """CALL 1: identify the market and plan research. Returns the parsed JSON."""
    section(logger, "CALL 1: ORIENT")
    orient = llm.chat_json(prompts.ORIENT_SYSTEM, prompts.ORIENT_USER.format(
        categories=", ".join(reports.categories()),
        title=article.title,
        body=article.body[:_BODY_CHARS],
        sponsor=sponsor.text,
    ), call_name="orient", logger=logger)
    orient["candidate_markets"] = _bare_market_names(orient.get("candidate_markets"))
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

    # Score-ranked merge, not positional round-robin. The LLM returns candidate_markets
    # most-fitting-first, so earlier guesses get a small rank bonus — but it must stay small:
    # a real production failure was Healthcare Automation Market (0.33 text-match, a 3-way TIE
    # with no real tiebreak, for guess #1 "Marketing Automation Market" — no catalog report by
    # that bare name exists) ranking #1 purely by guess-position, even though guess #2's "Marketing
    # Software Market" matched Digital Marketing Software Market at 1.00 text / 0.92 semantic — an
    # unrelated market won ahead of a near-exact one because position, not score, decided rank.
    PER_GUESS = 3
    GUESS_RANK_BONUS = 0.05  # per guess position back from #1 — enough to prefer guess #1 between
    # otherwise-comparable matches, never enough for a weak match to outrank a strong one two or
    # three guesses later (a real near-exact 1.00/0.92 match beats a bare-threshold 0.33 one even
    # at guess #7, since 7 * 0.05 = 0.35 < 0.67, the gap between those two real scores).
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

    # (score + rank bonus, entry) for every text/semantic hit across every guess position, plus
    # the unranked keyword pool at a fixed modest score (it was never a head-to-head match against
    # one candidate_markets guess, so it can't be scored on the same scale — kept as a supplementary
    # source, not allowed to outrank an actual scored match).
    scored_candidates: list[tuple[float, reports.CatalogEntry]] = []
    for i, group in enumerate(text_groups):
        bonus = (len(text_groups) - i) * GUESS_RANK_BONUS
        scored_candidates += [(s + bonus, e) for s, e in group]
    for i, group in enumerate(name_groups):
        bonus = (len(name_groups) - i) * GUESS_RANK_BONUS
        scored_candidates += [(s + bonus, e) for s, e in group]
    scored_candidates += [(0.01, e) for e in keyword_matches]

    scored_candidates.sort(key=lambda x: x[0], reverse=True)
    seen: set[int] = set()
    candidates: list[reports.CatalogEntry] = []
    for _, e in scored_candidates:
        if e.news_id not in seen:
            seen.add(e.news_id)
            candidates.append(e)
    candidates = candidates[:25]
    logger.info("ranked candidates: " + ", ".join(f"{c.news_id}:{c.keyword}" for c in candidates))

    if not candidates:
        raise PipelineError(f"No CMI report matched any of: {', '.join(names + keywords)}")
    return candidates


def _market_search_queries(market_name: str) -> list[str]:
    """Deterministic queries built directly from the HUMAN-CONFIRMED market name — not the
    LLM's pre-confirmation guess, which may differ if a human picked a different candidate
    than the model's top pick. No LLM call needed; this is template substitution.

    Every query is a NEWS search for the market's own subject, as one exact quoted phrase, plus
    ONE kind of company event (launch, acquisition, funding round, partnership, regulation, U.S.
    expansion). Rules that keep out the junk earlier versions pulled in:
      - the phrase is quoted, so the subject is REQUIRED. Unquoted, a news engine happily returns
        anything matching the event word;
      - no "OR": engines treat each OR branch as an independent search, so "… launches OR unveils
        new product OR service" matched any article containing "service" — a live test returned
        sports and celebrity news once a recency limit was applied;
      - the trailing "Market" is dropped ("<X> Market … trend/analyst" is how research firms title
        their own report PR, and lets an engine do a topic lookup on the first word of the name);
      - no explainer words ("trend", "adoption drivers", "overview"): they invite definitions and
        how-to pages, which are not evidence that anything happened.

    The six event-type queries (launch/acquisition/funding/partnership/regulation/expansion) assume
    a consumer-tech-shaped market with vendors doing those things — a real failure found live:
    for a niche B2B/chemical/pharma-treatment market (e.g. "Calcium Propionate", "Tourette Syndrome
    Treatment") almost none of those six phrases match any real news at all, since "funding round"
    or "launch" in that phrasing is simply not how coverage of that market is written. A 7th,
    deliberately generic query (no event word, just "news") is added as a catch-all so a market
    with no fundable/launchable events still has a chance at SOMETHING concrete and recent, rather
    than zero queries ever returning a hit.

    An 8th query drops the quotes entirely (still no "OR" — unquoted multi-word terms are an
    implicit AND, not the independent-branch problem quoting was added to fix). Compound
    category names built from individually-common words ("digital marketing software") rarely
    appear as that exact 3-word string in real coverage, which tends to paraphrase ("digital
    marketing platform", "martech") or just name a company — so the quoted queries above starved
    this kind of market down to a handful of raw hits. The unquoted query recovers that volume;
    relevance is now enforced downstream by filter_hits's stricter all-tokens gate, not by the
    query's own precision, so widening the query here is safe rather than reopening the old bug."""
    phrase = f'"{_market_core(market_name)}"'
    return [
        f"{phrase} launch",
        f"{phrase} acquisition",
        f"{phrase} funding round",
        f"{phrase} partnership",
        f"{phrase} regulation",
        f"{phrase} U.S. expansion",
        f"{phrase} news",
        f"{_market_core(market_name)} news",
    ]


def _market_core(market_name: str) -> str:
    """'Global Wealth Management Platform Market' -> 'wealth management platform'.
    A trailing "(ACRONYM)" is dropped too — news rarely repeats it, and inside an exact phrase
    it would make the quoted match fail."""
    core = re.sub(r"\s+markets?\s*$", "", market_name.strip(), flags=re.IGNORECASE)
    core = re.sub(r"^global\s+", "", core, flags=re.IGNORECASE)
    core = re.sub(r"\s*\([^)]*\)", "", core)
    return core.strip().lower() or market_name.strip()


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
    # Gate on the CONFIRMED market name only. candidates[] are OTHER markets the human did not
    # pick — mixing their tokens in here (e.g. "Network Automation", "Logistics Automation") let
    # hits about those rejected markets pass the topical filter on borrowed vocabulary, directly
    # contradicting this function's own job of never surfacing evidence for a rejected market.
    raw_hits = search.run_searches(queries, logger=logger)
    hits = search.filter_hits(raw_hits, [market_name], sponsor_hosts, logger=logger)
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
        cmi_keywords=", ".join(prompts.CMI_KEYWORDS),
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
