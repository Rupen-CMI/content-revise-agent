import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import checks
import freshness
import llm
import prompts
import reports
import search
import sources
from checks import (
    _FIGURE, MAX_LINK_WORD_OFFSET, CheckResult, _CMI_TITLE_TERMS, _numbers, _stale_event_mentions,
    _title_subject_stems, _verbatim_overlap, cmi_link_placement, run_checks,
)
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
        tag = f"DATED, {h.date.strftime('%Y-%m-%d')}" if h.date else "UNDATED"
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
    # temperature=0.1: this call identifies WHICH market the article is about, not creative writing —
    # sampling variance here previously changed the catalog match's top pick run to run on the exact
    # same article (one run's candidate_markets guess happened to exact-text-match a catalog entry,
    # another run's didn't, swinging the whole ranked-candidate order). Kept low, not 0.0: tested
    # 0.0 against the same article twice and DeepSeek still returned different candidate_markets
    # lists both times, so temperature alone cannot guarantee determinism on this API — 0.1 stays
    # close to that floor without pretending a lower value buys any real extra stability.
    orient = llm.chat_json(prompts.ORIENT_SYSTEM, prompts.ORIENT_USER.format(
        categories=", ".join(reports.categories()),
        title=article.title,
        body=article.body[:_BODY_CHARS],
        sponsor=sponsor.text,
    ), call_name="orient", temperature=0.1, logger=logger)
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


_VALID_SOP_TAGS = {
    "trends", "report_context", "segmentation", "subsegment",
    "country_us", "key_players", "conclusion",
}

# Some models (gpt-oss in testing: 50 non-breaking hyphens and 13 narrow no-break spaces in one
# draft, despite a prompt rule against it) emit hyphen/space look-alikes that print identically but
# are different characters, which silently break keyword matching and copy-paste. Em/en dashes and
# curly quotes are ordinary punctuation and are left alone.
_TYPOGRAPHY_FIXES = str.maketrans({
    "‐": "-", "‑": "-", "‒": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
})


def _plain(text: str) -> str:
    return text.translate(_TYPOGRAPHY_FIXES)

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
    # _market_core() strips the trailing "Market" word (it exists for query-building, where a bare
    # category phrase searches better) — secondary keywords must keep it, or "wealth management
    # platform size" reads as a truncated, ungrammatical half-phrase missing its own noun. Found by
    # inspection: every secondary keyword generated here was missing "market" until this fix.
    core = f"{_market_core(market_name)} market"
    secondary = [(f"{core} {suffix}", section) for suffix, section in _SECONDARY_KEYWORD_SUFFIXES]
    return market_name, secondary


def _secondary_keyword_lines(market_name: str, secondary_kw: list[tuple[str, str]], label: str) -> str:
    """Formats the secondary-keyword list so the TOPIC, not the literal phrase, is the first and
    most prominent thing on each line — found live that presenting these as a quoted, ready-to-use
    phrase ('- "wealth management platform market trends" — use in the X section') made the model
    anchor on the exact string regardless of a separate, correctly-worded rule appearing later in
    the prompt: 7 of 12 keywords came back in their literal static form, back to back, in one real
    draft. Leading with the topic and demoting the exact string to a parenthetical fallback fixes
    the actual cause (what the model sees first and reads as the instruction), not just the symptom.

    IMPORTANT: this must say REORDER, never "in your own words"/"express this idea" — an earlier
    version said exactly that, which told the model it could paraphrase the keyword away entirely
    (found live: "segmentation" became "axes", "size" became "how big"), silently breaking the SEO
    keyword's whole purpose while technically reading as "natural, varied prose". The words
    themselves are the requirement; only their order and surrounding sentence are free to vary."""
    lines = []
    for (suffix, _), (kw, section) in zip(_SECONDARY_KEYWORD_SUFFIXES, secondary_kw):
        lines.append(
            f"- TOPIC: {market_name}'s {suffix} — {label} the {section} section. Build a sentence around the "
            f"keyword's own words (reordered to fit naturally); the static phrase \"{kw}\" is one valid order, but "
            "any order that keeps every one of those same words is equally correct — vary which order you use so "
            "it doesn't read like a template, but never substitute a different word for one of them."
        )
    return "\n".join(lines)


def _llm_search_queries(
    market_name: str, orient: dict, key_players: list[str], logger: logging.Logger,
) -> tuple[list[str], list[str]]:
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
            today=date.today().isoformat(),
            market_name=market_name,
            topic_summary=orient.get("topic_summary", ""),
            bridge_angle=orient.get("bridge_angle", ""),
            key_players=", ".join(key_players) if key_players else "(none known)",
            source_group_menu=sources.pack_menu(),
        ), call_name="search_queries", logger=logger)
        queries = [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]
        groups = [str(g).strip() for g in result.get("source_groups") or [] if str(g).strip() in sources.PACKS]
        if queries:
            return queries, groups
        logger.warning("[search] LLM returned no usable queries, falling back to deterministic templates")
    except llm.LLMError:
        logger.warning("[search] LLM query generation failed, falling back to deterministic templates", exc_info=True)
    return _market_search_queries(market_name), []


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
    queries, groups = (
        _llm_search_queries(market_name, orient, list(key_players), logger)
        if orient is not None
        else (_market_search_queries(market_name), [])
    )
    logger.info(f"[search] queries ({len(queries)}): {queries}")
    # Targeted pass: the same market aimed at trade press / regulators for its industry (plus general
    # business and deal-funding press), on top of whatever the open-web queries surface.
    targeted = sources.targeted_queries(_market_core(market_name), groups)
    logger.info(f"[search] source groups chosen: {groups or '(none — general business + funding only)'}")
    logger.info(f"[search] targeted site queries ({len(targeted)}): {targeted}")
    queries = queries + targeted
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
    _normalize_selection_tags(selection, hits, logger)
    _drop_stale_facts(selection, logger)
    return selection


def _drop_stale_facts(selection: dict, logger: logging.Logger) -> None:
    """Remove any selected fact that itself states an event date older than the freshness window.
    A fact can come from a recent hit yet carry an old aside inside its own sentence (seen: a May
    2026 partnership fact that also mentioned a February 2025 deal); the writer then repeats the
    old date and the draft fails the 'dated events from the last 12 months' rule."""
    kept = []
    for e in selection.get("evidence") or []:
        stale = [
            m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(str(e.get("fact", "")))
            if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
        ]
        if stale:
            logger.info(f"[select] dropped {e.get('id')}: its fact states a stale date {stale}")
        else:
            kept.append(e)
    selection["evidence"] = kept


_VALID_USE_FOR = ("trends", "us", "players", "context")


def _normalize_selection_tags(selection: dict, hits: list[search.SearchHit], logger: logging.Logger) -> None:
    """Make every evidence item carry `use_for_all` (a clean list of valid tags) plus `use_for`
    (its first tag, kept as a plain string for anything that reads a single label). Models return
    use_for as a string, a list, or a "a|b" string; unknown tags are dropped. Also enforces the
    one rule the prompt states but a model can ignore: an UNDATED source can never be a "trends"
    fact. Done here in code so the rule holds regardless of which model answered, and logged so a
    model that keeps breaking it stays visible."""
    for e in selection.get("evidence") or []:
        raw = e.get("use_for")
        parts = re.split(r"[|,/+\s]+", raw) if isinstance(raw, str) else [str(x) for x in raw or []]
        tags = []
        for p in parts:
            p = p.strip().lower()
            if p in _VALID_USE_FOR and p not in tags:
                tags.append(p)
        m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
        hit = hits[int(m.group(1)) - 1] if m and 1 <= int(m.group(1)) <= len(hits) else None
        if "trends" in tags and hit is not None and hit.date is None:
            tags.remove("trends")
            logger.info(f"[select] removed 'trends' from {e.get('id')}: source is undated")
        if not tags:
            tags = ["context"]
        e["use_for_all"] = tags
        e["use_for"] = tags[0]


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
        use_for = ", ".join(e.get("use_for_all") or [e.get("use_for", "context")])
        evidence_lines.append(
            f"- ({evidence_id}) [{use_for}] {e.get('fact', '')} (source: {h.source or h.host}, {date})"
        )
    evidence = "\n".join(evidence_lines) or "(none)"
    section(logger, "FINAL EVIDENCE PASSED TO WRITER")
    logger.info(evidence)
    return evidence


def _link_placement_problems(sections: list[dict], sponsor_text: str, market_name: str) -> list[str]:
    """The report link must start within the first 200 words and be the first mention of the market."""
    offset, first_mention, _ = cmi_link_placement(sections, sponsor_text, market_name)
    problems = []
    if offset is not None and offset > MAX_LINK_WORD_OFFSET:
        problems.append(
            f"the CMI link starts at word {offset} of the article — it must be inside the first 100 words, in a "
            "sentence of intro paragraph 1"
        )
    if offset is not None and not first_mention:
        problems.append(
            f'the market name "{market_name}" appears in plain text BEFORE the CMI link — the link itself must be the '
            "first mention of the market's full name"
        )
    return problems


_BARE_SPONSOR_MAX_WORDS = 6  # a real retained sentence runs well past this; a bare link anchor doesn't


def _bare_sponsor_naming_problem(sponsor_text: str, sections: list[dict]) -> str | None:
    """The sponsor paragraph can be just a short linked phrase with no sentence of its own (e.g.
    "Customer data management systems") instead of real prose — WRITE_USER has an explicit hard
    requirement for this case (paragraph 1 must name the phrase's own content words before the
    placeholder), but that is a prompt instruction, not something enforced anywhere, and was found
    live to be missed even when the exact rule text reached the model verbatim. This gives it the
    same deterministic backstop every other hard requirement in this function already has."""
    words = sponsor_text.split()
    if not (1 <= len(words) <= _BARE_SPONSOR_MAX_WORDS):
        return None  # real prose, not a bare anchor — the ordinary lead-in rules already cover it
    intro = next((s for s in sections if not s.get("heading")), None)
    if not intro or not intro["paragraphs"]:
        return None
    lead_in = intro["paragraphs"][0]
    # at least half the anchor's own content words must appear close together in the lead-in — not
    # ALL of them, since the prompt itself allows "customer data" alone to stand in for "customer
    # data management systems" as long as it's specific, not a vaguer stand-in word.
    if not checks.phrase_partially_named(lead_in, sponsor_text):
        return (
            f"the retained paragraph is just a bare linked phrase (\"{sponsor_text}\") with no sentence of its own — "
            "paragraph 1 (the lead-in) must contain a real sentence naming that same specific thing (its own key "
            "words) BEFORE the placeholder, not a vaguer stand-in word like \"the data\" or \"the systems\" that "
            "never actually names it"
        )
    # Naming the concept somewhere in paragraph 1 isn't enough on its own — the bare phrase still
    # has to read as a grammatical continuation of paragraph 1's LAST sentence once substituted in
    # on its own line, or it still lands as an isolated fragment even when the concept was named
    # earlier in the same paragraph (found live: paragraph 1 named "customer data" mid-paragraph,
    # then closed with an unrelated sentence, leaving the bare link stranded on its own anyway).
    if not lead_in.rstrip().endswith(":"):
        return (
            f"the retained paragraph is just a bare linked phrase (\"{sponsor_text}\") with no sentence of its own — "
            "paragraph 1's LAST sentence must end with a direct grammatical lead-up into that phrase (ending in a "
            "colon, e.g. \"...and it starts with getting a handle on your own:\") so the bare phrase reads as that "
            "sentence's own continuation when it lands on its own line right after, not as an isolated fragment"
        )
    return None


def _segmentation_word_dropped_problem(sections: list[dict]) -> str | None:
    """A prompt instruction explicitly banning "axis"/"axes"/"breakdown" as stand-ins for
    "segment"/"segmentation" in the segmentation section's lead-in sentence was found live to not
    hold reliably even when the exact forbidden words were named in the prompt the model received
    verbatim (the model used "Coherent Market Insights splits this market along three axes" anyway).
    Same lesson as every other prompt-only rule this session: a hard requirement needs a
    deterministic backstop, not just a clear ask. Checks the segmentation section's own lead-in
    paragraph (its first paragraph, before the bulleted lines) for the real word "segment" or
    "segmentation" — if it's missing AND a banned stand-in word is present, that's the failure."""
    seg_section = next((s for s in sections if "segmentation" in s.get("sop", [])), None)
    if not seg_section or not seg_section["paragraphs"]:
        return None
    lead_in = seg_section["paragraphs"][0].split("\n", 1)[0].lower()  # lead-in sentence only, not the bullets
    has_real_word = bool(re.search(r"\bsegment(s|ation|ed)?\b", lead_in))
    banned_stand_ins = [w for w in ("axis", "axes", "breakdown") if re.search(rf"\b{w}\b", lead_in)]
    if not has_real_word and banned_stand_ins:
        return (
            f"the segmentation section's lead-in sentence uses {', '.join(repr(w) for w in banned_stand_ins)} "
            "instead of the word \"segment\"/\"segmentation\" — rewrite that one sentence so it uses the real word "
            "\"segment\" or \"segmentation\" (e.g. \"Coherent Market Insights breaks the market into three "
            "segments:\"), not a stand-in word, since this is where that secondary keyword's own words belong"
        )
    return None


_MAX_LITERAL_SECONDARY_KEYWORDS = 2  # see _repeated_keyword_shape_problem


def _repeated_keyword_shape_problem(full_text: str, secondary_kw: list[tuple[str, str]]) -> str | None:
    """Flags the specific failure the secondary-keyword REORDER rule exists to prevent: several
    secondary keywords all appearing in their exact static word ORDER ("<market> market trends",
    "<market> market size", "<market> market drivers", ...), which reads as a robotic, repeated
    "<market> market <word>" shape no matter how well-written each individual sentence is. The
    rule asks the model to reorder these words into a different sentence shape — NOT to paraphrase
    them into different words, which was tried first and caused secondary-keyword coverage to drop
    to 0/12 on a real draft (the model swapped "size" for "how big", "segmentation" for "axes",
    etc. — technically varied, but the keyword's own actual words were gone, defeating the SEO
    purpose a secondary keyword exists for in the first place). One or two literal uses of the
    original word order are fine (the rule allows keeping the exact order when it's already the
    smoothest phrasing); three or more identical "<market> market <word>" shapes back to back is
    the stuffed pattern this check exists to catch."""
    low = full_text.lower()
    literal_hits = [kw for kw, _ in secondary_kw if kw.lower() in low]
    if len(literal_hits) > _MAX_LITERAL_SECONDARY_KEYWORDS:
        return (
            f"{len(literal_hits)} secondary keywords appear as their exact static word ORDER, back to back in the "
            f"same \"<market> market <word>\" shape — {', '.join(repr(k) for k in literal_hits)}. REORDER most of "
            "these into a different sentence shape per the SECONDARY KEYWORDS rule above — every one of the "
            "keyword's own words must still appear (e.g. \"the size of the market\" keeps every word of \"market "
            "size\", just reordered), never swapped for a synonym or paraphrase (\"how big the market has become\" "
            "is wrong — it drops the actual word \"size\") — "
            f"so no more than {_MAX_LITERAL_SECONDARY_KEYWORDS} keep the exact original word order"
        )
    return None


def _write_problems(
    sections: list[dict], report: reports.Report, original_body: str, report_text: str, sponsor_text: str = "",
    secondary_kw: list[tuple[str, str]] = (),
) -> list[str]:
    """Hard-requirement violations in a CALL 3 draft, phrased as instructions for a retry."""
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    full = "\n".join(paragraphs)
    problems = []
    repeated_shape_problem = _repeated_keyword_shape_problem(full, secondary_kw)
    if repeated_shape_problem:
        problems.append(repeated_shape_problem)
    links = re.findall(prompts.CMI_LINK_PATTERN, full)
    if len(links) != 1:
        problems.append(
            f"the [[CMI_LINK:{report.name}]] citation appears {len(links)} times — it must appear exactly once, inline "
            "in a sentence of intro paragraph 1 (as the first mention of the market, within the first 100 words)"
        )
    elif links[0].strip() != report.name.strip():
        problems.append(f"the CMI_LINK anchor is {links[0]!r} but must be exactly {report.name!r}")
    else:
        problems.extend(_link_placement_problems(sections, sponsor_text, report.name))
    if full.count(prompts.SPONSOR_TOKEN) != 1 or not any(p.strip() == prompts.SPONSOR_TOKEN for p in paragraphs):
        problems.append(
            f"the placeholder {prompts.SPONSOR_TOKEN} must be its own paragraph, exactly once (paragraph 2 of the intro)"
        )
    bare_sponsor_problem = _bare_sponsor_naming_problem(sponsor_text, sections)
    if bare_sponsor_problem:
        problems.append(bare_sponsor_problem)
    segmentation_word_problem = _segmentation_word_dropped_problem(sections)
    if segmentation_word_problem:
        problems.append(segmentation_word_problem)
    present = {t for s in sections for t in s["sop"]}
    missing = sorted(_VALID_SOP_TAGS - present)
    if missing:
        problems.append(
            f"these required sections are missing: {', '.join(missing)} — every requirement needs its own section "
            "tagged in \"sop\""
        )
    elif "conclusion" not in sections[-1]["sop"]:
        problems.append('the conclusion section must be the last section')
    stale = _stale_event_mentions(sections)
    if stale:
        problems.append(
            f"it cites events dated more than 12 months ago ({'; '.join(stale)}) — leave those events out entirely or "
            "describe the point without presenting an old event as recent"
        )
    words = sum(len(p.split()) for p in paragraphs if p.strip() != prompts.SPONSOR_TOKEN)
    # Soft target is 1,400-1,700; only a far miss is worth a retry.
    if words > 1950 or words < 1200:
        longest = sorted(sections, key=lambda s: -sum(len(p.split()) for p in s["paragraphs"]))[:4]
        problems.append(
            f"the length is {words} words but the target is 1,400-1,700"
            + (
                " — tighten every paragraph that is over its stated range, especially in: "
                + "; ".join(f'"{s["heading"] or "intro"}" ({sum(len(p.split()) for p in s["paragraphs"])} words)' for s in longest)
                if words > 1950 else " — add the missing substance to the paragraphs that are under their stated ranges"
            )
        )
    sources = [("original article", original_body)] + ([("CMI report", report_text)] if report_text else [])
    pct, examples = _verbatim_overlap(paragraphs, sources, report_name=report.name)
    if pct > 5.0:
        problems.append(
            f"{pct:.0f}% of the words are copied in runs of 10 or more words from the original article or the CMI "
            "report — rewrite the affected paragraphs from scratch with genuinely different sentence structure "
            "(not synonym swaps); the affected paragraphs include: " + " | ".join(examples)
        )
    return problems


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
    secondary_kw_lines = _secondary_keyword_lines(report.name, secondary_kw, "use in")
    sponsor_words = len(re.findall(r"\b\w[\w'-]*\b", sponsor.text))
    max_own_words = max(0, 1700 - sponsor_words)
    logger.info(f"[write] word budget: sponsor={sponsor_words}, own writing max={max_own_words}")
    user_prompt = prompts.WRITE_USER.format(
        sponsor_words=sponsor_words,
        max_own_words=max_own_words,
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
    )

    # One retry, with the specific problems appended, when the first draft breaks a hard
    # requirement the final checks would fail it on anyway (seen in testing: a draft with no CMI
    # link at all and 17% of its words copied from the sources). A retry that errors out falls back
    # to the first draft rather than failing the run.
    feedback = ""
    first_attempt = None
    for attempt in (1, 2):
        try:
            written = llm.chat_json(
                prompts.WRITE_SYSTEM, user_prompt + feedback, temperature=0.5,
                call_name="write" if attempt == 1 else "write-retry", logger=logger,
            )
        except llm.LLMError:
            if first_attempt is None:
                raise
            logger.warning("[write] retry failed, keeping the first draft", exc_info=True)
            written, sections, title = first_attempt
            break

        sections = [
            {
                "heading": str(s.get("heading") or ""),
                "sop": [str(t) for t in s.get("sop") or [] if str(t) in _VALID_SOP_TAGS],
                "paragraphs": [_plain(str(p)) for p in s.get("paragraphs") or [] if str(p).strip()],
            }
            for s in written.get("sections") or []
        ]
        title = _plain(str(written.get("title") or ""))
        if not sections:
            if first_attempt is None:
                raise PipelineError("Call 3 returned no article sections")
            written, sections, title = first_attempt
            break

        problems = _write_problems(sections, report, article.body, report_text, sponsor.text, secondary_kw)
        if not problems or attempt == 2:
            if problems:
                logger.warning(f"[write] draft still has problems after the retry: {'; '.join(problems)}")
            break
        logger.warning(f"[write] first draft has problems ({'; '.join(problems)}) — retrying once with that feedback")
        first_attempt = (written, sections, title)
        feedback = (
            "\n\nYOUR PREVIOUS DRAFT WAS REJECTED FOR THESE PROBLEMS: " + "; ".join(problems) + ". Write the whole "
            "article again from scratch, in the same JSON shape, fixing exactly these problems while still meeting "
            "every other requirement above."
        )

    # Only trust a self-reported citation if its paragraph text is a VERBATIM match against a
    # real paragraph in the draft — the model could misreport (summarize rather than quote, or
    # cite a paragraph it then edited before finalizing), so this is validated against ground
    # truth rather than taken on faith. An unmatched citation is dropped, not corrected, since
    # there's no reliable way to know which real paragraph it actually meant.
    real_paragraphs = {p for s in sections for p in s["paragraphs"]}
    evidence_citations = []
    for c in written.get("evidence_citations") or []:
        para = _plain(str(c.get("paragraph") or ""))
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


@dataclass
class _KeywordStats:
    stuffed: list[tuple[str, int, list[str], int]]  # (section label, paragraph number, secondary kws found, primary count)
    used: set[str]  # distinct secondary keywords present anywhere
    primary_total: int
    words: int


def _keyword_suffix(keyword: str) -> str:
    """Recovers a secondary keyword's own suffix word(s) (e.g. "drivers", "competitive landscape")
    from its full string ("<market core> market <suffix>") by matching against the known suffix
    list — used so usage-checking can require the suffix and the market name to both appear
    somewhere in a paragraph without needing them clustered together (see
    checks.secondary_keyword_in_paragraph)."""
    low = keyword.lower()
    for suffix, _ in _SECONDARY_KEYWORD_SUFFIXES:
        if low.endswith(suffix.lower()):
            return suffix
    return keyword  # shouldn't happen for a keyword this function actually generated


def _keyword_stats(sections: list[dict], primary_kw: str, secondary_kws: list[str]) -> _KeywordStats:
    """Deterministic scan of a draft for SEO keyword usage. A paragraph is 'stuffed' when it holds
    two or more distinct secondary keywords, or the primary keyword twice or more. The sponsor
    placeholder paragraph is skipped (it is replaced by the guest's own text later).

    A secondary keyword's words may be REORDERED but not swapped for synonyms (see WRITE_USER's
    SECONDARY KEYWORDS rule) — but requiring the market name's own words and the suffix's own words
    to sit within one narrow window (checks.keyword_used) was found too strict for real analysis
    writing: a paragraph often names the market once, then spends several sentences on its
    drivers/share/competitive landscape without repeating the market name in each one — natural
    variation, not a miss. checks.secondary_keyword_in_paragraph instead requires the market's words
    and the suffix's words to each appear SOMEWHERE in the paragraph, not clustered together. The
    primary keyword keeps literal matching: that one has its own, separate "never truncate" rule."""
    stuffed, used, primary_total, words = [], set(), 0, 0
    for s in sections:
        label = s["heading"] or "intro"
        for n, p in enumerate(s["paragraphs"], 1):
            if p.strip() == prompts.SPONSOR_TOKEN:
                continue
            low = _plain(p).lower()
            words += len(p.split())
            found = [
                k for k in secondary_kws
                if checks.secondary_keyword_in_paragraph(low, primary_kw, _keyword_suffix(k))
            ]
            primary_count = low.count(primary_kw.lower())
            used.update(found)
            primary_total += primary_count
            if len(found) >= 2 or primary_count >= 2:
                stuffed.append((label, n, found, primary_count))
    return _KeywordStats(stuffed, used, primary_total, words)


def _stuffing_report(stuffed: list[tuple[str, int, list[str], int]]) -> str:
    if not stuffed:
        return "(none found — leave all keyword usage exactly as it is)"
    lines = []
    for label, n, found, primary_count in stuffed:
        problems = []
        if len(found) >= 2:
            problems.append("secondary keywords " + ", ".join(f'"{k}"' for k in found) + " (keep only ONE)")
        if primary_count >= 2:
            problems.append(f"the primary keyword {primary_count} times (keep only ONE)")
        lines.append(f'- Section "{label}", paragraph {n}: contains ' + " and ".join(problems))
    return "\n".join(lines)


_MIN_PRIMARY_USES = 3


def _min_secondary_uses(total: int) -> int:
    """The floor the 'Secondary keywords: genuine effort made' check in checks.py enforces."""
    return max(1, total // 3)


_MAX_TITLE_CHARS = 78  # the prompt asks for 40-75; a little slack before a rewrite is demanded

_PRIMARY_TARGET_ORDER =("report_context", "key_players", "conclusion", "segmentation", "country_us", "trends", "subsegment")


def _unsupported_figures(sections: list[dict], source_text: str) -> list[tuple[str, int, str]]:
    """(section label, paragraph number, figure text) for every market figure in the draft whose
    number appears nowhere in the report/evidence text — the same test the final 'All figures
    traceable' check applies. Typical cause: a statistic the guest author wrote in the original
    article that the writer carried over."""
    known = _numbers(source_text)
    out = []
    for s in sections:
        for pn, p in enumerate(s["paragraphs"], 1):
            if p.strip() == prompts.SPONSOR_TOKEN:
                continue
            for m in _FIGURE.finditer(p):
                if float(m.group(1).replace(",", "")) not in known:
                    out.append((s["heading"] or "intro", pn, m.group(0)))
    return out


def _coverage_report(
    title: str, sections: list[dict], stats: _KeywordStats, primary_kw: str, secondary_kw: list[tuple[str, str]],
    unsupported: list[tuple[str, int, str]] = (),
) -> str:
    """What the draft is MISSING, as concrete edits for CALL 4: code picks the paragraph for each
    missing keyword, because a vague "work some in somewhere" instruction was ignored every time
    while a named paragraph gets acted on (just like the stuffing list). Thresholds mirror the final
    checks, so a draft that clears them gets '(none)' and is left alone."""
    secondary_names = [kw for kw, _ in secondary_kw]
    cands = []
    for si, s in enumerate(sections):
        for pn, p in enumerate(s["paragraphs"], 1):
            # The intro is off limits: the report link lives there and must stay the first mention
            # of the market name, so no plain-text mention may be added ahead of it.
            if p.strip() == prompts.SPONSOR_TOKEN or si == 0:
                continue
            low = _plain(p).lower()
            cands.append({
                "key": (si, pn), "label": s["heading"] or "intro", "tags": s["sop"],
                "has_secondary": any(checks.keyword_used(low, k) for k in secondary_names),
                "has_primary": primary_kw.lower() in low,
            })
    taken: set[tuple[int, int]] = set()
    lines = [
        f'- Section "{label}", paragraph {pn}: the figure "{fig}" is not supported by the CMI report or the evidence. '
        "Remove that number and state the point without it, keeping the sentence grammatical."
        for label, pn, fig in unsupported
    ]

    if (
        not any(t in re.findall(r"[a-z]+", title.lower()) for t in _CMI_TITLE_TERMS)
        or primary_kw.lower() not in title.lower() or len(title) > _MAX_TITLE_CHARS
    ):
        lines.append(
            f'- TITLE: rewrite the title so it contains the exact phrase "{primary_kw}" AND at least one CMI term (for '
            'example "trends", "growth drivers", "competitive landscape", "market intelligence", "market forecast", '
            '"analysis" or "insights"), and staying 40-75 characters (it is currently '
            f"{len(title)}). NEVER drop the title's subject — the words that name the original article's specific topic, "
            "i.e. everything except the market name and generic filler; if it is too long, shorten the CMI term or the "
            "filler words (for example drop \"'s Growth Drivers\" for \"Trends\") but keep the subject words."
        )

    need_secondary = _min_secondary_uses(len(secondary_kw)) - len(stats.used)
    if need_secondary > 0:
        for kw, sec in [(k, s) for k, s in secondary_kw if k not in stats.used][:need_secondary]:
            free = [c for c in cands if c["key"] not in taken and not c["has_secondary"] and c["tags"]]
            pick = next((c for c in free if sec in c["tags"]), None) or (free[0] if free else None)
            if pick:
                taken.add(pick["key"])
                lines.append(
                    f'- Section "{pick["label"]}", paragraph {pick["key"][1]}: reword one sentence so it naturally '
                    f'expresses "{kw}" — reorder or reword it to fit the sentence (e.g. "the size of the market" '
                    "instead of \"market size\"), the exact words don't matter as long as the meaning is there."
                )

    need_primary = _MIN_PRIMARY_USES - stats.primary_total
    if need_primary > 0:
        picked_sections: set[int] = set()
        for tag in (*_PRIMARY_TARGET_ORDER, None):
            for c in cands:
                if len(picked_sections) >= need_primary:
                    break
                in_tag = (not c["tags"]) if tag is None else (tag in c["tags"])
                if in_tag and not c["has_primary"] and c["key"][0] not in picked_sections:
                    picked_sections.add(c["key"][0])
                    lines.append(
                        f'- Section "{c["label"]}", paragraph {c["key"][1]}: name the market with the exact phrase '
                        f'"{primary_kw}" once (for example in place of "the market" or a shortened name for it).'
                    )
    return "\n".join(lines) or "(none — coverage is sufficient; do not add any keywords)"


def enforce_report_link(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, sponsor_text: str, logger: logging.Logger,
) -> tuple[str, list[dict], list[dict]]:
    """Guarantee, in code, that the FIRST mention of the market name in the article is the one
    hyperlink back to the CMI report, and that it starts within the first 200 words. The writer is
    asked to do this and retried once, but a model can still miss; this runs on every run and makes
    the outcome independent of the model:
      1. every existing [[CMI_LINK:...]] token is reduced to plain text,
      2. the first mention of the market name (any casing) that starts within the first 200 words
         becomes the single link, written with the report's exact name as its anchor,
      3. if there is no such mention, one short sentence naming the market as the link is added at
         the end of intro paragraph 1.
    Evidence citations follow any paragraph this rewrites, so their highlights stay attached."""
    if not sections or not report.name.strip():
        return title, sections, evidence_citations
    name = report.name.strip()
    name_re = re.compile(r"\s+".join(re.escape(w) for w in name.split()), re.IGNORECASE)
    token = f"[[CMI_LINK:{name}]]"
    sponsor_words = len(sponsor_text.split())

    paras = [
        [re.sub(prompts.CMI_LINK_PATTERN, lambda m: m.group(1), p) for p in s["paragraphs"]]
        for s in sections
    ]

    target = None  # (section index, paragraph index, match) of the first mention, if early enough
    words_before = 0
    found_any = False
    for si, plist in enumerate(paras):
        for pi, p in enumerate(plist):
            if p.strip() == prompts.SPONSOR_TOKEN:
                words_before += sponsor_words
                continue
            m = name_re.search(p)
            if m:
                found_any = True
                if words_before + len(p[:m.start()].split()) <= MAX_LINK_WORD_OFFSET:
                    target = (si, pi, m)
                break
            words_before += len(p.split())
        if found_any:
            break

    how = ""
    if target is not None:
        si, pi, m = target
        paras[si][pi] = paras[si][pi][:m.start()] + token + paras[si][pi][m.end():]
        how = "linked the first mention"
    else:
        first = next((i for i, p in enumerate(paras[0]) if p.strip() != prompts.SPONSOR_TOKEN), None)
        if first is None:
            return title, sections, evidence_citations
        sentence = f"Coherent Market Insights follows this space in its {token} research."
        p = paras[0][first]
        paras[0][first] = (
            f"{sentence} {p}" if len(p.split()) > MAX_LINK_WORD_OFFSET - 20 else f"{p.rstrip()} {sentence}"
        )
        how = "no mention within the first 200 words, so added a linking sentence to intro paragraph 1"

    rewritten = {
        old: new
        for s, plist in zip(sections, paras)
        for old, new in zip(s["paragraphs"], plist) if old != new
    }
    if not rewritten:
        return title, sections, evidence_citations
    new_sections = [{**s, "paragraphs": plist} for s, plist in zip(sections, paras)]
    new_citations = [{**c, "paragraph": rewritten.get(c["paragraph"], c["paragraph"])} for c in evidence_citations]
    logger.info(f"[link] {how}; {len(rewritten)} paragraph(s) rewritten")
    return title, new_sections, new_citations


_DISABLE_CALL_4 = False


def run_revise(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, logger: logging.Logger, evidence: str = "", sponsor_text: str = "",
) -> tuple[str, list[dict], list[dict]]:
    """CALL 4 (see _revise_pass), always followed by the deterministic report-link enforcement so
    the link placement holds whichever way the revise pass ended (accepted, rejected, or failed)."""
    if _DISABLE_CALL_4:
        logger.warning("[revise] CALL 4 disabled (_DISABLE_CALL_4) — shipping the unrevised CALL 3 draft")
        return title, sections, evidence_citations
    title, sections, evidence_citations = _revise_pass(
        title, sections, evidence_citations, report, logger, evidence, sponsor_text
    )
    return enforce_report_link(title, sections, evidence_citations, report, sponsor_text, logger)


def _revise_pass(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, logger: logging.Logger, evidence: str = "", sponsor_text: str = "",
) -> tuple[str, list[dict], list[dict]]:
    """CALL 4: dedicated SEO-destuffing + readability editing pass over the already-approved
    draft from CALL 3. Facts/structure are frozen; only prose quality changes. Falls back to the
    original CALL 3 draft unchanged on any failure or structural-invariant violation — this call
    is purely an enhancement, never a requirement, so it must never be allowed to break a draft
    that was already good."""
    section(logger, "CALL 4: REVISE (SEO + readability)")
    primary_kw, secondary_kw = _seo_keyword_plan(report.name)
    secondary_names = [kw for kw, _ in secondary_kw]
    secondary_kw_lines = _secondary_keyword_lines(report.name, secondary_kw, "tagged for")

    before = _keyword_stats(sections, primary_kw, secondary_names)
    link_pos_before = cmi_link_placement(sections, sponsor_text, report.name)[2]
    link_problems_before = len(_link_placement_problems(sections, sponsor_text, report.name))
    source_text = f"{_report_text(report)}\n{evidence}"
    unsupported_before = _unsupported_figures(sections, source_text)
    if unsupported_before:
        logger.info(f"[revise] unsupported figure(s) in the CALL 3 draft: {unsupported_before}")
    logger.info(
        f"[revise] scan of the CALL 3 draft: {len(before.stuffed)} stuffed paragraph(s), "
        f"{len(before.used)}/{len(secondary_names)} secondary keywords used, "
        f"primary x{before.primary_total}, {before.words} words"
    )
    base_prompt = prompts.REVISE_USER.format(
        draft_title=title,
        draft_body=_render_draft_for_revise(title, sections),
        sop=prompts.SOP,
        primary_keyword=primary_kw,
        secondary_keywords=secondary_kw_lines,
        stuffing_report=_stuffing_report(before.stuffed),
        coverage_report=_coverage_report(title, sections, before, primary_kw, secondary_kw, unsupported_before),
        sponsor_token=prompts.SPONSOR_TOKEN,
        report_name=report.name,
    )

    # Allowed to retry ONCE, with the specific reasons the first answer fell short appended: seen in
    # testing, a first attempt that left a flagged figure in place or returned the draft unchanged
    # usually fixes it when told exactly what is still wrong. After the second attempt anything
    # still wrong means the CALL 3 draft is kept unchanged.
    feedback = ""
    for attempt in (1, 2):
        try:
            revised = llm.chat_json(
                prompts.REVISE_SYSTEM, base_prompt + feedback, temperature=0.3,
                call_name="revise" if attempt == 1 else "revise-retry", logger=logger,
            )
        except llm.LLMError:
            logger.warning("[revise] CALL 4 failed, keeping the CALL 3 draft unchanged", exc_info=True)
            return title, sections, evidence_citations

        new_sections = [
            {
                "heading": str(s.get("heading") or ""),
                "sop": [str(t) for t in s.get("sop") or [] if str(t) in _VALID_SOP_TAGS],
                "paragraphs": [_plain(str(p)) for p in s.get("paragraphs") or [] if str(p).strip()],
            }
            for s in revised.get("sections") or []
        ]
        new_title = _plain(str(revised.get("title") or "")) or title

        # Structural invariants this call was told never to touch. Any violation means the revise
        # pass produced something worse than doing nothing.
        full = "\n".join(p for s in new_sections for p in s["paragraphs"])
        same_section_count = len(new_sections) == len(sections)
        same_sop_tags = same_section_count and all(
            new_sections[i]["sop"] == sections[i]["sop"] for i in range(len(sections))
        )
        sponsor_ok = full.count(prompts.SPONSOR_TOKEN) == 1
        cmi_links = re.findall(prompts.CMI_LINK_PATTERN, full)
        cmi_link_ok = len(cmi_links) == 1 and cmi_links[0].strip() == report.name.strip()
        # The link must stay in the paragraph it was in and not drift later or lose first-mention status.
        link_placed_ok = cmi_link_ok and (
            cmi_link_placement(new_sections, sponsor_text, report.name)[2] == link_pos_before
            and len(_link_placement_problems(new_sections, sponsor_text, report.name)) <= link_problems_before
        )

        problems: list[str] = []
        after = unsupported_after = None
        if not new_sections or not (same_section_count and same_sop_tags and sponsor_ok and link_placed_ok):
            problems.append(
                f"the structure changed (same section count={same_section_count}, same sop tags={same_sop_tags}, "
                f"sponsor placeholder exactly once={sponsor_ok}, CMI link exactly once, correct, in the same paragraph "
                f"and still the first mention of the market={link_placed_ok}) — keep every section, sop tag, the "
                "placeholder and the CMI link exactly as in the draft"
            )
        else:
            # Quality gates: only worth keeping if it fixed what it was asked to without damaging
            # what was already fine (seen: a model returning the draft word for word, and one that
            # stripped every secondary keyword and cut ~8% of the words).
            after = _keyword_stats(new_sections, primary_kw, secondary_names)
            unsupported_after = _unsupported_figures(new_sections, source_text)
            # Removing keywords is expected only inside the stuffed paragraphs (down to one each); +1 slack.
            allowed_drop = sum(max(len(ks) - 1, 0) for _, _, ks, _ in before.stuffed) + 1
            shrink_floor = 1400 if before.words > 1700 else int(before.words * 0.95)
            if before.stuffed and len(after.stuffed) >= len(before.stuffed):
                problems.append(
                    f"keyword stuffing was not reduced ({len(before.stuffed)} -> {len(after.stuffed)} paragraphs) — "
                    "fix every paragraph listed in the stuffing scan"
                )
            if not before.stuffed and after.stuffed:
                problems.append(f"you introduced keyword stuffing in {len(after.stuffed)} paragraph(s)")
            if len(unsupported_after) > len(unsupported_before):
                problems.append(f"you introduced unsupported figures {[f for _, _, f in unsupported_after]}")
            if before.used and not after.used:
                problems.append("you removed every secondary keyword — only remove those in stuffed paragraphs")
            if len(after.used) < len(before.used) - allowed_drop:
                problems.append(
                    f"secondary keyword coverage collapsed ({len(before.used)} -> {len(after.used)}) — "
                    "leave keywords alone in paragraphs the scan did not list"
                )
            if after.primary_total < min(before.primary_total, 2):
                problems.append(f"the primary keyword was dropped ({before.primary_total} -> {after.primary_total} uses)")
            if after.words < shrink_floor:
                problems.append(
                    f"the draft shrank too much ({before.words} -> {after.words} words; keep it at least {shrink_floor})"
                )

        # An unsupported figure the scan asked to be removed but is still there is not a rejection
        # (keeping it either way is no worse than the CALL 3 draft) but is worth one more attempt.
        unresolved = []
        if not problems and unsupported_after:
            unresolved.append(
                "these unsupported figures are still in the text and must be removed (say the point without the "
                f"number): {[(label, pn, f) for label, pn, f in unsupported_after]}"
            )
        if not problems:
            def _title_ok(t: str) -> bool:
                return (
                    primary_kw.lower() in t.lower() and len(t) <= _MAX_TITLE_CHARS
                    and any(w in _CMI_TITLE_TERMS for w in re.findall(r"[a-z]+", t.lower()))
                )

            if not _title_ok(title) and not _title_ok(new_title):
                unresolved.append(
                    f'the title must contain the exact phrase "{primary_kw}" and a CMI term such as "trends", '
                    '"growth drivers", "competitive landscape", "market intelligence", "analysis" or "insights", and '
                    f"be at most {_MAX_TITLE_CHARS} characters — apply the TITLE edit"
                )
            if before.primary_total < _MIN_PRIMARY_USES and after.primary_total < _MIN_PRIMARY_USES:
                unresolved.append(
                    f"the primary keyword still appears only {after.primary_total} time(s) — make the primary keyword "
                    "edits listed in the coverage scan"
                )
            min_secondary = _min_secondary_uses(len(secondary_names))
            if len(before.used) < min_secondary and len(after.used) < min_secondary:
                unresolved.append(
                    f"only {len(after.used)} secondary keywords are used, below the minimum of {min_secondary} — make "
                    "the secondary keyword edits listed in the coverage scan"
                )

        if not problems and not unresolved:
            break
        reasons = "; ".join(problems + unresolved)
        if attempt == 1:
            logger.warning(f"[revise] CALL 4 attempt 1 fell short ({reasons}) — retrying once with that feedback")
            feedback = (
                "\n\nYOUR PREVIOUS ANSWER FELL SHORT: " + reasons + ". Produce the full revised JSON again, "
                "correcting exactly these things and applying every required edit listed above."
            )
            continue
        if problems:
            logger.warning(f"[revise] CALL 4 rejected on quality ({reasons}) — keeping the CALL 3 draft unchanged")
            return title, sections, evidence_citations
        logger.warning(f"[revise] CALL 4 accepted with unresolved items ({reasons})")

    # Never let the revise pass trade the title's subject for brevity or keywords: a title that no
    # longer shares any subject word with the draft's own title is the generic "<Market>: Trends and
    # Insights" failure, so the draft's title is kept instead.
    old_subject = _title_subject_stems(title, report.name)
    if old_subject and not (old_subject & _title_subject_stems(new_title, report.name)):
        logger.warning(f"[revise] revised title {new_title!r} lost the subject of {title!r} — keeping the draft's title")
        new_title = title

    # A citation is keyed by its paragraph's text, which the rewrite has usually changed. Section
    # and paragraph counts are unchanged only when the structure survived intact, so in that case
    # each citation follows its paragraph to the same position; otherwise (a paragraph was merged
    # or split) fall back to keeping only citations whose exact text is still present, never
    # risking a highlight on the wrong paragraph.
    same_shape = all(len(a["paragraphs"]) == len(b["paragraphs"]) for a, b in zip(sections, new_sections))
    if same_shape:
        position = {p: (si, pi) for si, s in enumerate(sections) for pi, p in enumerate(s["paragraphs"])}
        kept_citations = [
            {"id": c["id"], "paragraph": new_sections[position[c["paragraph"]][0]]["paragraphs"][position[c["paragraph"]][1]]}
            for c in evidence_citations if c["paragraph"] in position
        ]
    else:
        new_paragraphs = {p for s in new_sections for p in s["paragraphs"]}
        kept_citations = [c for c in evidence_citations if c["paragraph"] in new_paragraphs]
        logger.info(
            f"[revise] paragraph counts changed, so only {len(kept_citations)}/{len(evidence_citations)} "
            "evidence citation(s) could be kept"
        )

    logger.info(
        f"[revise] CALL 4 accepted (attempt {attempt}): {len(new_sections)} sections, "
        f"stuffed paragraphs {len(before.stuffed)} -> {len(after.stuffed)}, "
        f"secondary keywords {len(before.used)} -> {len(after.used)}, primary x{before.primary_total} -> x{after.primary_total}, "
        f"unsupported figures {len(unsupported_before)} -> {len(unsupported_after)}, words {before.words} -> {after.words}, "
        f"{len(kept_citations)}/{len(evidence_citations)} citations retained"
    )
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
        original_title=article.title,
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
    title, sections, evidence_citations = run_revise(
        title, sections, evidence_citations, report, logger, evidence, sponsor.text
    )

    progress("Checking the draft against the SOP")
    result = run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, original_filename, logger,
        evidence_citations=evidence_citations,
    )

    progress("Writing the revised article to output/")
    return result
