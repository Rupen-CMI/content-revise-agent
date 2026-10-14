import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import freshness
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
    evidence_citations: list[dict] = field(default_factory=list)
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


# Secondary-keyword suffixes generalized from a real SEO/GEO keyword brief built for one market
# (Smart Appliance Market) — the brief's "<market core> <suffix>" pattern repeats near-identically
# per market, so it's reproduced here as a template rather than hand-written per run. Each suffix
# is mapped to whichever of OUR OWN SOP tags its content actually belongs in (the source brief's
# own section names don't match ours — it assumes a standalone report-landing page with a CTA/
# report-coverage section and a dedicated "challenges" H2, neither of which exists in our SOP).
# "report"/"research" (-> report-coverage/CTA) and "challenges" (-> a dedicated challenges H2) are
# deliberately NOT included: forcing either into our structure would reintroduce the rigid,
# report-template feel the WRITE prompt was already rewritten to move away from.
_SECONDARY_KEYWORD_SUFFIXES: tuple[tuple[str, str], ...] = (
    # Rebalanced from the original brief's mapping, which put 6 of 12 keywords on report_context
    # alone (size/forecast/growth/analysis/CAGR/opportunities/drivers) — a 2-paragraph section with
    # that many keywords chasing it produced visibly stuffed prose in testing on two different
    # markets. Each move below is a content-fit judgment against what that SOP section is actually
    # specified to contain (see the per-section spec below in WRITE_USER), not just load-balancing:
    ("size", "report_context"),       # report_context's own spec: "the market's overall size/growth story"
    ("forecast", "report_context"),   # same sentence as size — the forward-looking half of it
    ("CAGR", "report_context"),       # the numeric form of that same size/growth story
    ("drivers", "report_context"),    # report_context's own spec literally names "drivers, demand factors"
    ("trends", "trends"),             # exact match to the section's own name
    ("growth", "trends"),             # "market growth" is ambiguous between a figure (already covered
                                       # by size/forecast/CAGR above) and a trend's impact — a trend
                                       # paragraph discussing what's fueling growth is a natural home
    ("share", "key_players"),         # share is almost always discussed per-company/competitor
    ("competitive landscape", "key_players"),  # exact match to the section's own spec wording
    ("segmentation", "segmentation"), # exact match to the section's own name
    ("analysis", "segmentation"),     # too generic alone to anchor any one section, but "segmentation
                                       # analysis" is a specific, real phrase — segmentation had only 1
                                       # keyword before, room to take a second
    ("opportunities", "subsegment"),  # growth opportunities are usually discussed AT the sub-segment
                                       # level ("the opportunity in cloud-based deployment is...");
                                       # subsegment had zero keywords mapped before this, and conclusion
                                       # is capped at exactly ONE paragraph so it can only safely hold
                                       # one keyword (outlook) without risking the same crowding
    ("outlook", "conclusion"),        # conclusion's own spec: "synthesize ... what it implies going forward"
)


def _seo_keyword_plan(market_name: str) -> tuple[str, list[tuple[str, str]]]:
    """(primary keyword, [(secondary keyword, target sop section), ...]) for this confirmed
    market. The primary keyword is the market name itself; secondary keywords are generated by
    appending each suffix above to the market's core phrase. Tertiary keywords (loosely related,
    broader terms — e.g. a named sub-technology or the market's own "key players" phrase) are
    deliberately NOT generated here: the source brief hand-picked those per market from real
    keyword-tool data, and guessing them mechanically risks inventing an irrelevant term; WRITE
    is instead told to use one only if something in its own sources already supplies one naturally."""
    core = _market_core(market_name)
    secondary = [(f"{core} {suffix}", section) for suffix, section in _SECONDARY_KEYWORD_SUFFIXES]
    return market_name, secondary


def _llm_search_queries(
    market_name: str, orient: dict, key_players: list[str], logger: logging.Logger,
) -> list[str]:
    """LLM-generated search queries, seeded with the confirmed market name, the article's own
    topic/angle, and real company names pulled from the CMI report — lets query phrasing adapt
    per-market instead of blindly applying the same fixed event-word templates to every market
    (the fixed templates underperformed badly on category names made of generic words, e.g.
    "digital marketing software" rarely appears verbatim in real coverage; a company-name-led
    query like "<real vendor> acquisition" finds genuine news a bare category-name query cannot).
    Falls back to the deterministic templates on any LLM failure — search must never hard-fail
    the whole pipeline over a transient model error."""
    try:
        result = llm.chat_json(prompts.SEARCH_QUERIES_SYSTEM, prompts.SEARCH_QUERIES_USER.format(
            market_name=market_name,
            topic_summary=orient.get("topic_summary", ""),
            bridge_angle=orient.get("bridge_angle", ""),
            key_players=", ".join(key_players) if key_players else "(none known)",
        ), call_name="search_queries", logger=logger)
        queries = [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]
        if queries:
            return queries
        logger.warning("[search] LLM returned no usable queries, falling back to deterministic templates")
    except llm.LLMError:
        logger.warning("[search] LLM query generation failed, falling back to deterministic templates", exc_info=True)
    return _market_search_queries(market_name)


def run_search_evidence(
    market_name: str, sponsor: Paragraph, candidates: list[reports.CatalogEntry], logger: logging.Logger,
    progress: Callable[[str], None] = lambda _: None, key_players: list[str] = (),
    orient: dict | None = None,
) -> list[search.SearchHit]:
    """Web search for supporting evidence, run AFTER the human has confirmed the market —
    queries are built from the confirmed market name itself, so this never searches for a
    market the human ultimately rejected."""
    section(logger, "WEB SEARCH")
    logger.info(f"[search] key players for {market_name!r}: {list(key_players) or '(none)'}")
    queries = (
        _llm_search_queries(market_name, orient, list(key_players), logger)
        if orient is not None
        else _market_search_queries(market_name)
    )
    logger.info(f"[search] queries ({len(queries)}): {queries}")
    progress(f"Searching the web for {market_name} ({len(queries)} queries)")
    sponsor_hosts = tuple(_host(link.url) for link in sponsor.links if link.is_external)
    # Gate on the CONFIRMED market name only. candidates[] are OTHER markets the human did not
    # pick — mixing their tokens in here (e.g. "Network Automation", "Logistics Automation") let
    # hits about those rejected markets pass the topical filter on borrowed vocabulary, directly
    # contradicting this function's own job of never surfacing evidence for a rejected market.
    raw_hits = search.run_searches(queries, logger=logger)
    hits = search.filter_hits(raw_hits, [market_name], sponsor_hosts, logger=logger, key_players=key_players)
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
        evidence_id = e["id"].strip().upper()
        evidence_lines.append(
            f"- ({evidence_id}) [{e.get('use_for', 'context')}] {e.get('fact', '')} (source: {h.source or h.host}, {date})"
        )
    evidence = "\n".join(evidence_lines) or "(none)"
    section(logger, "FINAL EVIDENCE PASSED TO WRITER")
    logger.info(evidence)
    return evidence


def run_write(
    article: SourceArticle, orient: dict, sponsor: Paragraph,
    report: reports.Report, evidence: str, logger: logging.Logger,
) -> tuple[dict, str, list[dict], list[dict]]:
    """CALL 3: write the article. Returns (raw_json, title, sections, evidence_citations)."""
    section(logger, "CALL 3: WRITE")
    report_text = _report_text(report)
    body_text, removed = freshness.strip_stale_developments(article.body)
    if removed:
        logger.info(f"[reports] dropped {len(removed)} stale dated development(s) older than {freshness.STALE_AFTER.days} days")
        for line in removed:
            logger.info(f"[reports]   stale: {line[:160]}")
    primary_kw, secondary_kw = _seo_keyword_plan(report.name)
    logger.info(f"[seo] primary keyword: {primary_kw!r}")
    logger.info(f"[seo] secondary keywords ({len(secondary_kw)}): {secondary_kw}")
    secondary_kw_lines = "\n".join(f'- "{kw}" — use in the {section} section' for kw, section in secondary_kw)
    written = llm.chat_json(prompts.WRITE_SYSTEM, prompts.WRITE_USER.format(
        sop=prompts.SOP,
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        bridge_angle=orient.get("bridge_angle", ""),
        original_body=body_text[:_BODY_CHARS],
        sponsor=sponsor.text,
        sponsor_token=prompts.SPONSOR_TOKEN,
        report_name=report.name,
        report_text=report_text,
        cmi_keywords=", ".join(prompts.CMI_KEYWORDS),
        primary_keyword=primary_kw,
        secondary_keywords=secondary_kw_lines,
        evidence=evidence,
        today=date.today().isoformat(),
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

    # Only trust a self-reported citation if its paragraph text is a VERBATIM match against a
    # real paragraph in the draft — the model could misreport (summarize rather than quote, or
    # cite a paragraph it then edited before finalizing), so this is validated against ground
    # truth rather than taken on faith. An unmatched citation is dropped, not corrected, since
    # there's no reliable way to know which real paragraph it actually meant.
    real_paragraphs = {p for s in sections for p in s["paragraphs"]}
    evidence_citations = []
    for c in written.get("evidence_citations") or []:
        para = str(c.get("paragraph") or "")
        eid = str(c.get("id") or "").strip().upper()
        if eid and para in real_paragraphs:
            evidence_citations.append({"id": eid, "paragraph": para})
        else:
            logger.info(f"[write] dropped unverifiable evidence citation: {c}")

    return written, title, sections, evidence_citations


def _render_draft_for_revise(title: str, sections: list[dict]) -> str:
    """Plain-text rendering of the draft for CALL 4's prompt — heading, sop tags, and paragraphs
    per section, in the same order WRITE produced them."""
    lines = [f"TITLE: {title}", ""]
    for s in sections:
        heading = s["heading"] or "(unheaded intro)"
        tags = ", ".join(s["sop"]) or "untagged"
        lines.append(f"### {heading} [sop: {tags}]")
        lines.extend(s["paragraphs"])
        lines.append("")
    return "\n".join(lines)


def run_revise(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, logger: logging.Logger,
) -> tuple[str, list[dict], list[dict]]:
    """CALL 4: dedicated SEO-destuffing + readability editing pass over the already-approved
    draft from CALL 3. Facts/structure are frozen; only prose quality changes. Falls back to the
    original CALL 3 draft unchanged on any failure or structural-invariant violation — this call
    is purely an enhancement, never a requirement, so it must never be allowed to break a draft
    that was already good."""
    section(logger, "CALL 4: REVISE (SEO + readability)")
    primary_kw, secondary_kw = _seo_keyword_plan(report.name)
    secondary_kw_lines = "\n".join(f'- "{kw}" — tagged for the {sec} section' for kw, sec in secondary_kw)
    try:
        revised = llm.chat_json(prompts.REVISE_SYSTEM, prompts.REVISE_USER.format(
            draft_title=title,
            draft_body=_render_draft_for_revise(title, sections),
            sop=prompts.SOP,
            primary_keyword=primary_kw,
            secondary_keywords=secondary_kw_lines,
            sponsor_token=prompts.SPONSOR_TOKEN,
            report_name=report.name,
        ), temperature=0.3, call_name="revise", logger=logger)
    except llm.LLMError:
        logger.warning("[revise] CALL 4 failed, keeping the CALL 3 draft unchanged", exc_info=True)
        return title, sections, evidence_citations

    new_sections = [
        {
            "heading": str(s.get("heading") or ""),
            "sop": [str(t) for t in s.get("sop") or []],
            "paragraphs": [str(p) for p in s.get("paragraphs") or [] if str(p).strip()],
        }
        for s in revised.get("sections") or []
    ]
    new_title = str(revised.get("title") or "") or title

    # Validate the structural invariants this call was told never to touch. Any violation means
    # the revise pass produced something worse than doing nothing — discard it and keep CALL 3's
    # draft rather than ship a broken structure for the sake of cleaner prose.
    full = "\n".join(p for s in new_sections for p in s["paragraphs"])
    same_section_count = len(new_sections) == len(sections)
    same_sop_tags = same_section_count and all(
        new_sections[i]["sop"] == sections[i]["sop"] for i in range(len(sections))
    )
    sponsor_ok = full.count(prompts.SPONSOR_TOKEN) == 1
    cmi_links = re.findall(prompts.CMI_LINK_PATTERN, full)
    cmi_link_ok = len(cmi_links) == 1 and cmi_links[0].strip() == report.name.strip()

    if not new_sections or not (same_section_count and same_sop_tags and sponsor_ok and cmi_link_ok):
        logger.warning(
            "[revise] CALL 4 output failed structural validation "
            f"(sections={same_section_count}, sop_tags={same_sop_tags}, sponsor={sponsor_ok}, cmi_link={cmi_link_ok}) "
            "— keeping the CALL 3 draft unchanged"
        )
        return title, sections, evidence_citations

    # Citations are only kept if their exact paragraph text survived the rewrite verbatim —
    # anything the revise pass touched loses its highlight rather than risk mis-attributing a
    # citation to the wrong (now different) paragraph. This undercounts slightly in exchange for
    # never showing a false highlight.
    new_paragraphs = {p for s in new_sections for p in s["paragraphs"]}
    kept_citations = [c for c in evidence_citations if c["paragraph"] in new_paragraphs]
    if len(kept_citations) < len(evidence_citations):
        logger.info(
            f"[revise] {len(evidence_citations) - len(kept_citations)} evidence citation(s) lost "
            "their highlight because their paragraph was rewritten by CALL 4"
        )

    logger.info(f"[revise] CALL 4 accepted: {len(new_sections)} sections, {len(kept_citations)}/{len(evidence_citations)} citations retained")
    return new_title, new_sections, kept_citations


def run_finalize(
    article: SourceArticle, sponsor: Paragraph, report: reports.Report,
    candidates: list[reports.CatalogEntry], orient: dict, selection: dict,
    hits: list[search.SearchHit],
    title: str, sections: list[dict], evidence: str,
    original_filename: str, logger: logging.Logger,
    evidence_citations: list[dict] = (),
) -> RevampResult:
    """SOP checks + write .docx. Returns the finished RevampResult."""
    section(logger, "SOP CHECKS")
    report_text = _report_text(report)
    primary_kw, secondary_kw = _seo_keyword_plan(report.name)
    checks = run_checks(
        title, sections, f"{report_text}\n{evidence}\n{sponsor.text}", _brand_terms(sponsor),
        sponsor.text, article.body, report.name, selection, hits, report_text=report_text,
        primary_keyword=primary_kw, secondary_keywords=[kw for kw, _ in secondary_kw],
    )
    for c in checks:
        logger.info(("PASS " if c.passed else "FAIL ") + c.name + (f" - {c.detail}" if c.detail else ""))

    result = RevampResult(
        title, sections, sponsor, report, candidates, orient, selection, hits, checks,
        evidence_citations=list(evidence_citations),
    )
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
    hits = run_search_evidence(
        candidates[0].keyword, sponsor, candidates, logger, progress,
        key_players=reports.key_players(report), orient=orient,
    )

    progress("Step 2: selecting evidence")
    selection = run_select(article, orient, sponsor, report, hits, logger)
    evidence = run_build_evidence(selection, hits, logger)

    progress("Step 3: writing the article")
    written, title, sections, evidence_citations = run_write(article, orient, sponsor, report, evidence, logger)

    progress("Step 4: SEO + readability revision pass")
    title, sections, evidence_citations = run_revise(title, sections, evidence_citations, report, logger)

    progress("Checking the draft against the SOP")
    result = run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, original_filename, logger,
        evidence_citations=evidence_citations,
    )

    progress("Writing the revised article to output/")
    return result
