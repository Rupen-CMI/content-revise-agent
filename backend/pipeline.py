import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import checks
import config
import feeds
import freshness
import intent
import llm
import prompts
import reports
import search
import sources
from checks import (
    _FIGURE, MAX_LINK_WORD_OFFSET, CMI_OPINIONS_MAX, CMI_OPINIONS_MIN, CheckResult, count_cmi_opinions, _CMI_TITLE_TERMS, _numbers, _stale_event_mentions,
    _title_subject_stems, _verbatim_overlap, cmi_link_placement, long_paragraph_problems, LONG_PARAGRAPH_CHECK_ENABLED, question_heading_problems, run_checks,
)
from docx_io import Paragraph, SourceArticle, output_path_for, write_docx
from pipeline_log import new_run_logger, section

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_BODY_CHARS = 12000

Segment = tuple[str, str | None]  # (text, hyperlink url or None)

# A retained paragraph that carries no sentence punctuation at all and is only a handful of words is
# a BARE LINKED PHRASE (e.g. "Customer data management systems") rather than prose: it has to be
# woven into one of the writer's own sentences instead of landing alone on its own line. A real
# retained sentence runs well past this bound.
_BARE_SPONSOR_MAX_WORDS = 8
_SENTENCE_END_RE = re.compile(r"[.!?][\"'”’)\]]*(?:\s|$)")
_WORD_RE = re.compile(r"\b\w[\w'-]*\b")


def sponsor_mode(text: str) -> str:
    """Deterministic rendering/placement mode for the retained paragraph: 'phrase' when it has no
    sentence-ending punctuation (., ! or ? possibly followed by a closing quote/bracket) and at most
    _BARE_SPONSOR_MAX_WORDS words, otherwise 'prose'."""
    stripped = (text or "").strip()
    if not stripped:
        return "prose"
    if _SENTENCE_END_RE.search(stripped):
        return "prose"
    return "phrase" if len(_WORD_RE.findall(stripped)) <= _BARE_SPONSOR_MAX_WORDS else "prose"


def _word_count(text: str) -> int:
    """Words a paragraph is worth when the placeholder is inline: its own words, with the token
    itself never counted as a word."""
    return len(_WORD_RE.findall(text.replace(prompts.SPONSOR_TOKEN, " ")))


def _sponsor_blank(text: str) -> str:
    """The paragraph with any placeholder removed — the token is not real writing, so it must never
    contribute words, keyword matches, figures or verbatim overlap."""
    return text.replace(prompts.SPONSOR_TOKEN, " ")


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
    faq: list[dict] = field(default_factory=list)
    output_path: Path | None = None

    def segments(self, paragraph: str) -> list[Segment]:
        """Expand placeholders: the retained paragraph with its original links, and the CMI report link."""
        return expand_segments(paragraph, self.sponsor, self.report.url)


def _cmi_link_segments(text: str, report_url: str | None) -> list[Segment]:
    out: list[Segment] = []
    pos = 0
    for m in re.finditer(prompts.CMI_LINK_PATTERN, text):
        out.append((text[pos:m.start()], None))
        out.append((m.group(1), report_url))
        pos = m.end()
    out.append((text[pos:], None))
    return [s for s in out if s[0]]


def expand_segments(paragraph: str, sponsor: Paragraph, report_url: str | None) -> list[Segment]:
    """Expand a written paragraph into (text, url) segments: the sponsor placeholder ANYWHERE in the
    paragraph becomes the retained text with its original links (exact characters, original
    capitalisation), and each [[CMI_LINK:...]] becomes the report URL. A placeholder that is the
    whole paragraph still renders exactly as it did before inline placement was supported."""
    if paragraph.strip() == prompts.SPONSOR_TOKEN:
        return sponsor_segments(sponsor)
    out: list[Segment] = []
    parts = paragraph.split(prompts.SPONSOR_TOKEN)
    for i, part in enumerate(parts):
        if part:
            out.extend(_cmi_link_segments(part, report_url))
        if i < len(parts) - 1:
            out.extend(sponsor_segments(sponsor))
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
        body = h.excerpt or h.snippet[:300]
        lines.append(f"[W{i}] {h.title} | {h.source or h.host} | {tag}\n      {body}")
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


# The originals' claims list is capped here too: the publisher block is now a single short
# section (about 20% of the post, see _INTRO_TOTAL_WORDS and PUBLISHER_USER), so more than 3
# unbacked claims would have nowhere to go and would only pad the claim-selection pool with noise.
_MAX_ORIENT_CLAIMS = 3


def _normalize_claims(raw, sponsor_text: str, logger: logging.Logger) -> list[dict]:
    """Sanitise ORIENT's claims_to_back: dicts only, a claim short enough to actually read, none of
    them the retained paragraph's own content (that paragraph is fixed and its wording is never
    rewritten, so searching for its backing would be wasted — and its text must not drift), ids
    renumbered C1..Cn sequentially (a model that miscounts must not desync the C-id the writer and
    the checks use), at most 2 search queries each. Bad entries are dropped with a log, never
    fatal — claim backing is an enhancement, not a requirement the run may fail on."""
    sponsor_low = " ".join((sponsor_text or "").split()).lower()
    out: list[dict] = []
    for c in raw if isinstance(raw, list) else []:
        if len(out) >= _MAX_ORIENT_CLAIMS:
            break
        if not isinstance(c, dict):
            continue
        claim = re.sub(r"\s+", " ", str(c.get("claim") or "")).strip()
        if len(claim.split()) < 4:
            logger.warning(f"[orient] dropped malformed claim entry {c.get('id')!r}: {claim!r}")
            continue
        if claim.lower() in sponsor_low:
            logger.info(f"[orient] claim dropped (it is the retained paragraph's own text): {claim[:90]}")
            continue
        queries = [re.sub(r"\s+", " ", str(q)).strip() for q in (c.get("queries") or [])]
        out.append({
            "id": f"C{len(out) + 1}",
            "claim": claim,
            "kind": str(c.get("kind") or "").strip().lower() or "claim",
            "why": re.sub(r"\s+", " ", str(c.get("why") or "")).strip(),
            "queries": [q for q in queries if q][:2],
        })
    if out:
        logger.info(f"[orient] claims to back ({len(out)}): " + "; ".join(f'{c["id"]}={c["claim"][:60]}' for c in out))
    else:
        logger.info("[orient] no unverifiable claims extracted")
    return out


def orient_claims(orient: dict | None) -> list[dict]:
    """The step-1 claims list, safe to read from anywhere (main.py, replay tools): empty when the
    orient call predated claim extraction, returned nothing, or returned the wrong shape."""
    if not config.SEARCH_CLAIMS_ENABLED or not isinstance(orient, dict):
        return []
    return [
        c for c in orient.get("claims_to_back") or []
        if isinstance(c, dict) and re.sub(r"\s+", " ", str(c.get("claim") or "")).strip()
    ]


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
    if config.SEARCH_CLAIMS_ENABLED:
        orient["claims_to_back"] = _normalize_claims(orient.get("claims_to_back"), sponsor.text, logger)
    else:
        logger.info("[orient] claim extraction disabled (config.SEARCH_CLAIMS_ENABLED) — ignoring any claims_to_back the model returned")
        orient["claims_to_back"] = []
    # Long-tail candidates ride this call (they are generated for the TOP guess's market, so if the
    # human confirms that same market the run pays no extra call; see resolve_longtails). Stored RAW
    # here — like claims, they are sanitised later, at Step 2 against the CONFIRMED market, because
    # the only fact this call misses is which candidate the human actually picks.
    raw_longtails = orient.get("longtail_candidates")
    orient["longtail_candidates"] = [
        t for t in (raw_longtails if isinstance(raw_longtails, list) else [])
        if isinstance(t, dict) and str(t.get("keyword") or "").strip()
    ][:12]
    names = [n for n in orient.get("candidate_markets") or [] if isinstance(n, str)]
    keywords = [k for k in orient.get("scope_keywords") or [] if isinstance(k, str)]
    orient["longtail_market"] = names[0] if names else ""
    if orient["longtail_candidates"]:
        logger.info(f"[orient] long-tail candidates for {orient['longtail_market']!r}: "
                    + "; ".join(f'{t["keyword"]} [{t.get("intent", "?")}]' for t in orient["longtail_candidates"]))
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
    "country_us", "key_players", "conclusion", "transition",
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
    # alone (size/forecast/growth/analysis/CAGR/opportunities/drivers) — one short section with
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
        core = kw[: kw.lower().rindex(" market ")] if " market " in kw.lower() else market_name
        lines.append(
            f'- "{suffix}" ({label} the {section} section). Required words, all present in the sentence: "{suffix}", '
            f'"{core}" and "market". Write it in a reordered form, for example "the {suffix} of the {core} market" or '
            f'"the {core} market\'s {suffix}", whichever fits the sentence.'
        )
    return "\n".join(lines)


# ---------------------------------------------------------------- long-tail keywords (commercial/research intent SEO)
# A small set of long-tail search phrases (1-3 actually used by the writer out of a generated
# pool), intended stocks the user asked for: commercial/review, niche/B2B, research/B2B,
# commercial/pricing and vendor comparison. Unlike the primary/secondary keyword plan above these
# are NOT derivable from the market name alone ("wealth management for doctors" has no suffix
# template), so an LLM proposes market-specific candidates from the confirmed report's own
# segmentation and named companies, and the writer integrates a few where they genuinely fit.
_LONGTAIL_COUNT = 8          # candidates generated; the writer is capped at 3 (see MARKET_USER)
_LONGTAIL_MIN_WORDS = 3
_LONGTAIL_MAX_WORDS = 8
_LONGTAIL_INTENTS = ("commercial_review", "niche_b2b", "research_b2b", "commercial_pricing", "vendor_comparison")


def _normalize_longtails(raw, market_name: str, logger: logging.Logger) -> list[dict]:
    """Sanitise the long-tail generator's output: real phrases only (3-8 words, no placeholder
    brackets that would print literally in prose), intents mapped to the five clusters the user
    asked for, duplicates collapsed, and exact echoes of the market's own name dropped (the
    primary keyword's job is that phrase verbatim — a candidate that only restates it is wasted
    pool space). Bad entries are dropped with a log, never fatal: long-tail integration is an
    enhancement the run must never fail on."""
    market_low = " ".join(market_name.lower().split())
    core_low = _market_core(market_name)
    echoes = {market_low, core_low, f"{core_low} market"}
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if len(out) >= _LONGTAIL_COUNT:
            break
        if not isinstance(item, dict):
            continue
        keyword = re.sub(r"\s+", " ", str(item.get("keyword") or "")).strip()
        if any(ch in keyword for ch in "[]<>{}|"):
            logger.info(f"[long-tail] dropped (placeholder bracket): {keyword!r}")
            continue
        n_words = len(_WORD_RE.findall(keyword))
        if not _LONGTAIL_MIN_WORDS <= n_words <= _LONGTAIL_MAX_WORDS:
            logger.info(f"[long-tail] dropped ({n_words} words, need {_LONGTAIL_MIN_WORDS}-{_LONGTAIL_MAX_WORDS}): {keyword!r}")
            continue
        intent = str(item.get("intent") or "").strip().lower()
        if intent not in _LONGTAIL_INTENTS:
            logger.info(f"[long-tail] dropped (unknown intent {intent!r}): {keyword!r}")
            continue
        norm = " ".join(keyword.lower().split())
        if norm in seen:
            continue
        if norm in echoes:
            logger.info(f"[long-tail] dropped (restates the market name itself): {keyword!r}")
            continue
        # The market name plus one generic word ("<market> market size") is the primary/secondary keyword
        # plan's job, not a long-tail phrase: it names no sub-segment, audience, region or technology.
        words = set(_WORD_RE.findall(norm))
        if words & {"market", "industry", "sector"} and len(words - set(_WORD_RE.findall(core_low)) - {"market", "industry", "sector"}) <= 1:
            logger.info(f"[long-tail] dropped (the market name plus one generic word): {keyword!r}")
            continue
        seen.add(norm)
        out.append({
            "keyword": keyword, "intent": intent,
            "why": re.sub(r"\s+", " ", str(item.get("why") or "")).strip(),
        })
    if out:
        logger.info(f"[long-tail] kept {len(out)} candidate(s): " +
                    "; ".join(f'{t["keyword"]} [{t["intent"]}]' for t in out))
    else:
        logger.info("[long-tail] no usable candidates generated")
    return out


def run_longtail_keywords(
    market_name: str, report: reports.Report, logger: logging.Logger, orient: dict | None = None,
) -> list[dict]:
    """A CALL run in Step 2 (after the report is confirmed, alongside the report-claims extraction
    — see main.py): asks the model for long-tail search phrases across the five commercial-intent
    clusters, grounded in the report's own segmentation and named companies so the phrases name
    real sub-segments/audiences of THIS market. Best-effort like every enhancement: a generation
    failure returns [] and the writer simply works without long-tail keywords."""
    section(logger, "CALL: LONG-TAIL KEYWORDS")
    try:
        result = llm.chat_json(prompts.LONGTAIL_SYSTEM, prompts.LONGTAIL_USER.format(
            market_name=market_name,
            topic_summary=str((orient or {}).get("topic_summary") or "(not available)"),
            segments="\n".join(f"- {t}" for t in list(report.segment_terms)[:18]) or "(none listed)",
            key_players=", ".join(reports.key_players(report)) or "(none known)",
            count=_LONGTAIL_COUNT,
        ), call_name="longtail_keywords", logger=logger)
    except llm.LLMError:
        logger.warning("[long-tail] generation failed; the draft will use no long-tail keywords", exc_info=True)
        return []
    return _normalize_longtails(result.get("long_tail_keywords"), market_name, logger)


def _format_longtail_lines(longtails: list[dict]) -> str:
    """The long-tail candidates handed to the writer (and to the revise pass): one line per
    phrase, its intent cluster and the reader intent behind it."""
    if not longtails:
        return "(none this run — write the post with the primary/secondary keywords only)"
    lines = []
    for t in longtails:
        line = f'- "{t["keyword"]}" [intent: {t["intent"]}]'
        if t.get("why"):
            line += f" — reader intent: {t['why']}"
        lines.append(line)
    return "\n".join(lines)


def _longtail_usage(title: str, sections: list[dict], longtails: list[dict]) -> list[dict]:
    """{keyword, intent, used} — whether each generated phrase is used in the finished draft's body.
    The writer may REORDER a phrase's words to fit a sentence (see the WRITE prompt's LONG-TAIL rule), so
    this is the concept match checks.longtail_spans (every content word present, close together), the same
    one the UI highlighting and the SOP check use — a literal substring test would miss every reordered use.
    Used for the finalize log line and the UI result; the 2-3 target is a soft SOP check, never a retry."""
    body = "\n".join(" ".join(p if isinstance(p, str) else str(p) for p in s.get("paragraphs", [])) for s in sections)
    return [{**t, "used": bool(checks.longtail_spans(body, t["keyword"]))} for t in longtails]


def longtail_candidates_from_orient(orient: dict | None, market_name: str, logger: logging.Logger) -> list[dict]:
    """The Step 1 long-tail candidates, safe to read from Step 2 — mirrors orient_claims()'s pattern:
    the raw list stored on orient (generated for THAT call's top-guess market) is sanitised here
    against the CONFIRMED market and filtered for relevance. A candidate sharing no word with the
    confirmed market's core names a different market — that happens when the human overrode the
    model's default pick, which the caller (resolve_longtails) detects and handles by regenerating."""
    if not isinstance(orient, dict) or not isinstance(orient.get("longtail_candidates"), list):
        return []
    out = _normalize_longtails(orient["longtail_candidates"], market_name, logger)
    core_words = set(_WORD_RE.findall(_market_core(market_name)))
    frame_words = {"market", "industry", "sector"}
    kept: list[dict] = []
    for t in out:
        words = set(_WORD_RE.findall(t["keyword"].lower()))
        if words & core_words or words & frame_words:
            kept.append(t)
        else:
            logger.info(f"[long-tail] Step 1 candidate dropped (names a different market): {t['keyword']!r}")
    if len(kept) != len(out):
        logger.info(f"[long-tail] kept {len(kept)}/{len(out)} Step 1 candidate(s) relevant to the confirmed market {market_name!r}")
    return kept


def longtail_seeds_match(orient: dict | None, market_name: str) -> bool:
    """True when the market the orient call seeded its long-tail candidates for is (in bare-core
    terms) the market the human actually confirmed — i.e. the Step 1 candidates can be reused and
    Step 2 needs no generation call. An unseeded orient (old runs / generation skipped) is False."""
    seeded = _market_core(str((orient or {}).get("longtail_market") or ""))
    return bool(seeded) and seeded == _market_core(market_name)


def resolve_longtails(market_name: str, report: reports.Report, orient: dict | None, logger: logging.Logger) -> list[dict]:
    """THE long-tail entry point both run paths (stepwise /step2 and the monolith) use. The candidates are
    generated HERE, in Step 2, from the confirmed report's own segments and companies AND the article's subject
    (run_longtail_keywords): the pool has to sit where the article and the report overlap, or the writer has
    nothing it can honestly place a phrase against. Step 1's orient call also proposes candidates, for the
    market alone; that list is kept as the FALLBACK when this generation returns nothing, and is only reused
    when the human confirmed the market it was seeded for. Best-effort like every enhancement: any failure
    leaves the run's long-tails empty rather than failing the step."""
    try:
        generated = run_longtail_keywords(market_name, report, logger, orient)
        if generated:
            return generated
        if longtail_seeds_match(orient, market_name):
            logger.info("[long-tail] generation gave nothing — falling back to the Step 1 candidates")
            return longtail_candidates_from_orient(orient, market_name, logger)
        return []
    except Exception:
        logger.warning("[long-tail] resolution failed; the draft will use no long-tail keywords", exc_info=True)
        return []


def _llm_search_queries(
    market_name: str, key_players: list[str], logger: logging.Logger, segments: list[str] = (),
) -> tuple[list[str], list[str], list[str]]:
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
            key_players=", ".join(key_players) if key_players else "(none known)",
            segments=", ".join(segments[:14]) if segments else "(none listed)",
            source_group_menu=sources.pack_menu(),
        ), call_name="search_queries", logger=logger)
        queries = [str(q).strip() for q in result.get("queries") or [] if str(q).strip()]
        groups = [str(g).strip() for g in result.get("source_groups") or [] if str(g).strip() in sources.PACKS]
        topic_terms = list(dict.fromkeys(
            re.sub(r"\s+", " ", str(t)).strip().lower() for t in result.get("topic_terms") or [] if str(t).strip()
        ))[:20]
        if queries:
            return queries, groups, topic_terms
        logger.warning("[search] LLM returned no usable queries, falling back to deterministic templates")
    except llm.LLMError:
        logger.warning("[search] LLM query generation failed, falling back to deterministic templates", exc_info=True)
    return _market_search_queries(market_name), [], []


def prepare_search_queries(
    market_name: str, key_players: list[str], logger: logging.Logger, segments: list[str] = (),
) -> tuple[list[str], list[str], list[str]]:
    """The query-generation half of run_search_evidence, callable on its own thread: main.py's
    Step 2 runs it concurrently with the question harvest and the report-claim extraction (none of
    the three needs another's output). Returns exactly what run_search_evidence reads when handed
    it back — (queries, source_groups, topic_terms)."""
    return _llm_search_queries(market_name, list(key_players), logger, list(segments))


def run_search_evidence(
    market_name: str, sponsor: Paragraph, candidates: list[reports.CatalogEntry], logger: logging.Logger,
    progress: Callable[[str], None] = lambda _: None, key_players: list[str] = (),
    orient: dict | None = None, segments: list[str] = (),
    search_queries: tuple[list[str], list[str], list[str]] | None = None,
) -> list[search.SearchHit]:
    """Web search for supporting evidence, run AFTER the human has confirmed the market —
    queries are built from the confirmed market name itself, so this never searches for a
    market the human ultimately rejected.

    search_queries: pre-generated (queries, groups, topic_terms) from prepare_search_queries, so
    the stepwise API can overlap that LLM call with the other Step 2 prep. None (CLI / callers that
    did not parallelize) generates them here as before."""
    section(logger, "WEB SEARCH")
    logger.info(f"[search] key players for {market_name!r}: {list(key_players) or '(none)'}")
    queries, groups, topic_terms = (
        search_queries
        if search_queries is not None
        else (
            _llm_search_queries(market_name, list(key_players), logger, list(segments))
            if orient is not None
            else (_market_search_queries(market_name), [], [])
        )
    )
    logger.info(f"[search] queries ({len(queries)}): {queries}")
    logger.info(f"[search] source groups chosen: {groups or '(none — general business + funding only)'}")
    core = _market_core(market_name)
    sponsor_hosts = tuple(_host(link.url) for link in sponsor.links if link.is_external)

    def filtered(raw: list[search.SearchHit]) -> list[search.SearchHit]:
        # Gate on the CONFIRMED market name only. candidates[] are OTHER markets the human did not
        # pick — mixing their tokens in here (e.g. "Network Automation", "Logistics Automation") let
        # hits about those rejected markets pass the topical filter on borrowed vocabulary, directly
        # contradicting this function's own job of never surfacing evidence for a rejected market.
        return search.filter_hits(
            raw, [market_name], sponsor_hosts, limit=config.SEARCH_KEEP_HITS, logger=logger, key_players=key_players,
            max_per_host=config.SEARCH_MAX_PER_HOST, sector_hosts=feeds.sector_hosts(groups), topic_terms=topic_terms,
        )

    # FALLBACK: DDGS, which is rate-sensitive and slow (about 9s per query), runs only when the primary stage came up
    # short (or when config says always/never). It uses the LLM's own queries only: the site: queries it used to repeat
    # are the ones Bing already ran, and a DDGS text search on them returns site landing pages. The slow part overlaps
    # with the rest of the primary stage: as soon as Bing's first few keyword queries are done and clearly thin, DDGS starts in a
    # background thread while the site: queries and the publisher feeds are still running.
    mode = config.SEARCH_DDGS_MODE
    if mode not in ("fallback", "always", "off"):
        logger.warning(f"[search] unknown SEARCH_DDGS_MODE {mode!r}; DDGS not run")
    ddgs_queries = queries[:config.SEARCH_DDGS_MAX_QUERIES]
    ddgs_pool = ThreadPoolExecutor(1)
    ddgs_future = None

    def after_keywords(bing_hits: list[search.SearchHit]) -> None:
        nonlocal ddgs_future
        early = len(filtered(bing_hits))
        if mode == "always" or (mode == "fallback" and early < max(1, config.SEARCH_PRIMARY_MIN_HITS // 4)):
            logger.info(f"[search] DDGS fallback starts early ({'configured to always run' if mode == 'always' else f'the first Bing keyword queries kept only {early}'}): {len(ddgs_queries)} queries, overlapping the rest of the primary stage")
            progress(f"Primary sources thin — adding web search in parallel ({len(ddgs_queries)} queries)")
            ddgs_future = ddgs_pool.submit(search.run_searches, ddgs_queries, logger)

    # PRIMARY: Bing News RSS (keyword + site: queries) and the curated publisher feeds.
    progress(f"Gathering evidence for {market_name} (Bing News + publisher feeds)")
    raw_hits = feeds.gather(market_name, core, queries, groups, list(key_players), logger, topic_terms, after_keywords)
    hits = filtered(raw_hits)
    logger.info(f"[search] primary stage kept {len(hits)} article(s)")
    if ddgs_future is not None:
        hits = filtered(raw_hits + ddgs_future.result())
    elif mode == "fallback" and len(hits) < config.SEARCH_PRIMARY_MIN_HITS:
        logger.info(f"[search] DDGS fallback (primary stage kept only {len(hits)} < {config.SEARCH_PRIMARY_MIN_HITS}): {len(ddgs_queries)} queries")
        progress(f"Primary sources thin ({len(hits)} articles) — adding web search ({len(ddgs_queries)} queries)")
        hits = filtered(raw_hits + search.run_searches(ddgs_queries, logger=logger))
    ddgs_pool.shutdown(wait=False)

    # Full-text pass over what survived, so SELECT and the writer see article text, not just a snippet.
    progress(f"Reading {len(hits)} candidate articles")
    feeds.attach_excerpts(hits, market_name, logger)
    logger.info(f"[search] {sum(1 for h in hits if h.excerpt)} of {len(hits)} article(s) have full-text excerpts")
    return hits


_CLAIM_BPOOL_KEEP = 6  # pages kept per claim for SELECT (which picks at most two)
_CLAIM_POOL_GOOD = 2   # kept hits at which a claim's first query no longer needs its second


def run_claim_evidence(
    claims: list[dict], logger: logging.Logger, progress: Callable[[str], None] = lambda _: None,
) -> dict[str, list[search.SearchHit]]:
    """Web search for backing of the claims ORIENT extracted from the original article — Bing web search
    with DDGS text search as the fallback (the sources are explainers/encyclopedias/vendor reference
    pages, which news indexes don't carry; see feeds.ClaimWebSearch and search.run_text_searches). Returns {claim_id: [hits]}.
    Callers run this CONCURRENTLY with run_search_evidence (its own thread): different source
    profile, different rate-limit budget, and both stages are network-bound.

    Per claim: the first search query from Step 1; a second one only if the first was thin (kept
    hits below _CLAIM_POOL_GOOD). A claim with no queries from Step 1 gets a deterministic fallback
    built from its own distinctive words, in article order, unquoted — right-shaped for a topic
    search, not a phrase-match search."""
    section(logger, "CLAIM BACKING SEARCH")
    pool: dict[str, list[search.SearchHit]] = {}
    if not claims:
        return pool
    total = 0
    bing_web = feeds.ClaimWebSearch(logger)
    for c in claims:
        queries = list(c.get("queries") or [])
        if not queries:  # Step 1 gave none: the claim's own distinctive words, in article order, unquoted
            words: list[str] = []
            for w in re.findall(r"[A-Za-z0-9']+", c["claim"]):
                w = w.strip("'.,").lower()
                if len(w) > 2 and w not in search._STOPWORDS and w not in words:
                    words.append(w)
            queries = [" ".join(words[:8])] if words else []
        # Two channels per query, polite and sequential: Bing web search first (no rate-limit trouble at our pace),
        # DDGS text search only when the claim is still thin. Hits accumulate across queries and channels and are
        # filtered together, so a claim ends with the best of everything found, not the last query's batch.
        raw: list[search.SearchHit] = []
        kept: list[search.SearchHit] = []
        # Two channels per query, polite and sequential: Bing web search first (no rate-limit trouble at our pace),
        # DDGS text search only when the claim is still thin. Hits accumulate across queries and channels and are
        # filtered together, so a claim ends with the best of everything found, not the last query's batch.
        # DDGS no longer fires after EVERY thin Bing batch, though: one thin batch is usually the
        # query's scope, not the claim's absence of coverage on the web. The fallback waits for two
        # consecutive thin queries — or the claim's LAST query, so a single-query claim still gets
        # exactly one DDGS attempt. At most one DDGS call per claim, and every such call queues
        # behind all other streams' text-lane calls in search's shared gate, so on claim-heavy runs
        # this roughly halves the wait those claims take end to end.
        ddgs_used = False
        thin_streak = 0
        for qi, q in enumerate(queries):
            raw += bing_web.search(q)
            kept = search.filter_claim_hits(raw, c["claim"], logger, limit=_CLAIM_BPOOL_KEEP)
            if len(kept) >= _CLAIM_POOL_GOOD:
                break
            thin_streak += 1
            if ddgs_used or not (thin_streak >= 2 or qi == len(queries) - 1):
                continue
            ddgs_used = True
            raw += search.run_text_searches([q], logger)
            kept = search.filter_claim_hits(raw, c["claim"], logger, limit=_CLAIM_BPOOL_KEEP)
            if len(kept) >= _CLAIM_POOL_GOOD:
                break
        if not kept:
            logger.info(f"[claim-search] {c['id']} ({c['claim'][:70]!r}): nothing usable found")
        for h in kept:
            h.scope, h.claim_id = "claim", c["id"]
        feeds.attach_excerpts(kept, c["claim"], logger, focus=c["claim"])
        pool[c["id"]] = kept
        total += len(kept)
    bing_web.close()
    backed_pool = sum(1 for hits in pool.values() if hits)
    progress(
        f"Claim backing search: {total} candidate page(s) across {backed_pool}/{len(claims)} claim(s)"
    )
    logger.info(f"[claim-search] pool: {backed_pool}/{len(claims)} claim(s) have candidates, {total} page(s) total")
    return pool


_SUBHEADING_QUESTION_COUNT = 3  # up to three of the article's sections may be headed by a question


def run_harvest_questions(
    market_name: str, logger: logging.Logger, progress: Callable[[str], None] = lambda _: None,
) -> dict:
    """Harvest real autocomplete completions for the confirmed market (see intent.harvest_questions),
    run CONCURRENTLY with run_search_evidence the same way run_claim_evidence is — independent network
    calls, so overlapping them costs no extra wall-clock. Best-effort: a harvesting failure must never
    fail the run, since the question pool is an enhancement, not a requirement."""
    section(logger, "QUESTION HARVEST")
    try:
        pool = intent.harvest_questions(market_name, _market_core(market_name), logger)
    except Exception:
        logger.warning("[intent] harvesting failed; no subheading questions will be offered", exc_info=True)
        pool = {"subheading_candidates": []}
    progress(f"Harvested {len(pool['subheading_candidates'])} subheading question candidate(s)")
    return pool


def _format_question_candidates(candidates: list[str]) -> str:
    if not candidates:
        return "(none harvested)"
    return "\n".join(f"- {q}" for q in candidates)


# Sections whose fixed job answers a common market question; subsegment (two per-sub-segment analyses) and
# conclusion (the closing take) have no such question.
_QUESTION_ELIGIBLE_SECTIONS = ("report_context", "trends", "segmentation", "country_us", "key_players")


def run_select_questions(
    market_name: str, orient: dict, question_pool: dict | None, logger: logging.Logger,
) -> dict:
    """CALL: pick the final subheading questions from the harvested pool, each assigned to ONE of
    the article's existing market sections (trends, country_us, key_players) whose heading and
    opening it will replace — never a new section. Returns {"subheading_questions": [...]}, empty
    when nothing was harvested — the writer still works fine without them (see prompts.MARKET_USER).
    The FAQ block is unrelated to this pool; it is generated separately at write time (see
    run_faq), grounded in the finished article and the CMI report."""
    pool = question_pool or {"subheading_candidates": []}
    if not pool.get("subheading_candidates"):
        logger.info("[intent] no harvested candidates — skipping question selection")
        return {"subheading_questions": []}
    section(logger, "CALL: SELECT QUESTIONS")
    try:
        result = llm.chat_json(prompts.INTENT_SYSTEM, prompts.INTENT_USER.format(
            market_name=market_name,
            topic_summary=orient.get("topic_summary", ""),
            subheading_candidates=_format_question_candidates(pool.get("subheading_candidates") or []),
            subheading_count=_SUBHEADING_QUESTION_COUNT,
        ), call_name="select_questions", logger=logger)
    except llm.LLMError:
        logger.warning("[intent] question selection call failed; no questions will be offered", exc_info=True)
        return {"subheading_questions": []}
    return _normalize_subheading_questions(result, logger)


def _normalize_subheading_questions(result: dict, logger: logging.Logger, market_name: str = "") -> dict:
    """Sanitise the model's subheading_questions (shared by the autocomplete-selection call and the
    LLM-drafted call): each needs text and a valid section, at most one per section, at most
    _SUBHEADING_QUESTION_COUNT in all. With market_name given, a drafted question must also be a real
    question phrased about the MARKET (intent._is_market_question) — a vague or product-level draft
    is dropped, never kept."""
    questions: list[dict] = []
    seen_sections: set[str] = set()
    for item in result.get("subheading_questions") or []:
        if not isinstance(item, dict):
            continue
        q = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
        sec = str(item.get("section") or "").strip().lower()
        if not q or sec not in _QUESTION_ELIGIBLE_SECTIONS:
            logger.info(f"[intent] dropped question (missing text or invalid section {sec!r}): {item}")
            continue
        if sec in seen_sections:
            logger.info(f"[intent] dropped question (section {sec!r} already assigned): {item}")
            continue
        if market_name and not (
            4 <= len(q.split()) <= 18 and q.endswith("?") and intent._is_market_question(q, _market_core(market_name))
        ):
            logger.info(f"[intent] dropped drafted question (not a market-shaped question): {q!r}")
            continue
        seen_sections.add(sec)
        questions.append({"id": f"Q{len(questions) + 1}", "question": q, "section": sec})
        if len(questions) >= _SUBHEADING_QUESTION_COUNT:
            break
    logger.info(f"[intent] selected {len(questions)} subheading question(s): "
                + "; ".join(f'{q["id"]}[{q["section"]}]={q["question"][:70]}' for q in questions))
    return {"subheading_questions": questions}


def run_draft_questions(market_name: str, orient: dict, report: reports.Report, logger: logging.Logger) -> dict:
    """CALL: the model itself drafts the 1-2 most-searched, market-level questions that will head
    existing sections (trends / country_us / key_players), grounded in the CMI report. This replaces
    the autocomplete harvest + selection as the question source: autocomplete returned almost nothing
    market-shaped for B2B markets, and what it did return was vague product-level questions
    (run_harvest_questions / run_select_questions are kept but no longer connected). Best-effort: a
    failure leaves the sections with their ordinary headings."""
    section(logger, "CALL: DRAFT SUBHEADING QUESTIONS")
    try:
        result = llm.chat_json(prompts.QUESTION_DRAFT_SYSTEM, prompts.QUESTION_DRAFT_USER.format(
            market_name=market_name,
            core=_market_core(market_name),
            topic_summary=orient.get("topic_summary", ""),
            report_text=_report_text(report),
            subheading_count=_SUBHEADING_QUESTION_COUNT,
        ), call_name="draft_questions", logger=logger)
    except llm.LLMError:
        logger.warning("[intent] question drafting call failed; no questions will be offered", exc_info=True)
        return {"subheading_questions": []}
    return _normalize_subheading_questions(result, logger, market_name)


_QUESTION_BPOOL_KEEP = 6  # pages kept per question for SELECT (which picks at most two)
_QUESTION_POOL_GOOD = 2   # kept hits at which a question's search no longer needs the DDGS fallback


def run_question_evidence(
    questions: list[dict], logger: logging.Logger, progress: Callable[[str], None] = lambda _: None,
) -> dict[str, list[search.SearchHit]]:
    """Web search for evidence that directly answers each chosen subheading question — the same
    machinery (Bing web search, DDGS text-search fallback) and the same best-effort policy as
    run_claim_evidence, since an explainer/analysis page answering a question is the same shape of
    source as one backing a claim. Runs CONCURRENTLY with run_search_evidence (see main.py's Step 2,
    which starts this right after the questions are chosen, before the general market web search):
    different source profile, different rate-limit budget, both stages are network-bound."""
    section(logger, "QUESTION BACKING SEARCH")
    pool: dict[str, list[search.SearchHit]] = {}
    if not questions:
        return pool
    total = 0
    bing_web = feeds.ClaimWebSearch(logger)
    for q in questions:
        qid, qtext = q["id"], q["question"]
        if q.get("section") == "report_context":
            # That section answers with the CMI report's own size/growth figures; outside pages for such a
            # question would carry rival market-sizing numbers (the policy of _drop_market_sizing_facts).
            logger.info(f"[question-search] {qid} ({qtext[:70]!r}): answered from the CMI report, no outside search")
            continue
        query = re.sub(r"[?!.]+\s*$", "", qtext).strip()
        raw = bing_web.search(query)
        kept = search.filter_claim_hits(raw, qtext, logger, limit=_QUESTION_BPOOL_KEEP)
        if len(kept) < _QUESTION_POOL_GOOD:
            raw += search.run_text_searches([query], logger)
            kept = search.filter_claim_hits(raw, qtext, logger, limit=_QUESTION_BPOOL_KEEP)
        if not kept:
            logger.info(f"[question-search] {qid} ({qtext[:70]!r}): nothing usable found")
        for h in kept:
            h.scope, h.claim_id = "question", qid
        feeds.attach_excerpts(kept, qtext, logger, focus=qtext)
        pool[qid] = kept
        total += len(kept)
    bing_web.close()
    backed = sum(1 for hits in pool.values() if hits)
    progress(f"Question backing search: {total} candidate page(s) across {backed}/{len(questions)} question(s)")
    logger.info(f"[question-search] pool: {backed}/{len(questions)} question(s) have candidates, {total} page(s) total")
    return pool


_REPORT_CLAIM_COUNT = 8  # up to ~3 per area (trends, subsegment, country_us); see REPORT_CLAIMS_USER.
# Raised from 5 (2 per area): these three areas need the most outside substance, so more extractable
# claims means more of the report-claim backing search's budget is spent where the writer needs it —
# the market stream's volume was cut back at the same time (SEARCH_KEEP_HITS/SEARCH_MAX_SELECTED).
_REPORT_CLAIM_SECTIONS = ("trends", "subsegment", "country_us")
# Where each report claim's backing folds into run_build_evidence's existing MARKET EVIDENCE groups
# (see _EVIDENCE_GROUPS): subsegment has no dedicated group of its own, so it joins "context", the
# same group sub-segment facts already route to ("SEGMENTS, SUB-SEGMENTS, GROWTH DRIVERS...").
_REPORT_CLAIM_SECTION_TO_GROUP = {"trends": "trends", "country_us": "us", "subsegment": "context"}


def run_report_claims(market_name: str, report: reports.Report, logger: logging.Logger) -> list[dict]:
    """A CALL run right after the report is fetched (Step 2, before the general market web search —
    see main.py): extracts specific, checkable statements the CMI REPORT ITSELF makes about trends,
    a sub-segment, or the U.S. market, each a candidate for independent outside corroboration. This
    is the report's own equivalent of ORIENT's (disconnected) claims_to_back — same shape, same
    best-effort policy — but it targets the sections that need the most substance (trends,
    subsegment, country_us), not the publisher block, and it can only run once the report is known,
    so it happens here rather than in Step 1."""
    section(logger, "CALL: REPORT CLAIMS")
    try:
        result = llm.chat_json(prompts.REPORT_CLAIMS_SYSTEM, prompts.REPORT_CLAIMS_USER.format(
            market_name=market_name,
            report_text=_report_text(report),
            claim_count=_REPORT_CLAIM_COUNT,
        ), call_name="report_claims", logger=logger)
    except llm.LLMError:
        logger.warning("[report-claims] extraction call failed; no report-claim evidence will be gathered", exc_info=True)
        return []
    claims: list[dict] = []
    for item in result.get("report_claims") or []:
        if not isinstance(item, dict):
            continue
        claim = re.sub(r"\s+", " ", str(item.get("claim") or "")).strip()
        sec = str(item.get("section") or "").strip().lower()
        if len(claim.split()) < 4 or sec not in _REPORT_CLAIM_SECTIONS:
            logger.info(f"[report-claims] dropped malformed entry (missing text or invalid section {sec!r}): {item}")
            continue
        queries = [re.sub(r"\s+", " ", str(q)).strip() for q in (item.get("queries") or [])]
        claims.append({
            "id": f"RC{len(claims) + 1}", "claim": claim, "section": sec,
            "queries": [q for q in queries if q][:2],
        })
        if len(claims) >= _REPORT_CLAIM_COUNT:
            break
    if claims:
        logger.info(f"[report-claims] extracted {len(claims)}: "
                    + "; ".join(f'{c["id"]}[{c["section"]}]={c["claim"][:60]}' for c in claims))
    else:
        logger.info("[report-claims] no specific, checkable report claims extracted")
    return claims


_REPORT_CLAIM_BPOOL_KEEP = 8  # pages kept per report claim for SELECT (which picks at most three)
_REPORT_CLAIM_POOL_GOOD = 2   # kept hits at which a report claim's search no longer needs the DDGS fallback


def run_report_claim_evidence(
    claims: list[dict], logger: logging.Logger, progress: Callable[[str], None] = lambda _: None,
) -> dict[str, list[search.SearchHit]]:
    """Web search for independent corroboration of each extracted report claim — the same machinery
    (Bing web search, DDGS text-search fallback) and the same best-effort policy as
    run_claim_evidence/run_question_evidence. Runs CONCURRENTLY with run_search_evidence (see
    main.py's Step 2, which starts this right after the report claims are extracted, before the
    general market web search): different source profile, both stages are network-bound."""
    section(logger, "REPORT CLAIM BACKING SEARCH")
    pool: dict[str, list[search.SearchHit]] = {}
    if not claims:
        return pool
    total = 0
    bing_web = feeds.ClaimWebSearch(logger)
    for c in claims:
        cid, ctext = c["id"], c["claim"]
        queries = list(c.get("queries") or []) or [ctext]
        raw: list[search.SearchHit] = []
        kept: list[search.SearchHit] = []
        # Same thin-streak DDGS policy as run_claim_evidence (see it there for the rationale): the
        # fallback fires once per claim at most — after two consecutive thin Bing queries, or the
        # claim's last query (two-query claims here are common, so both shapes skip one DDGS call).
        ddgs_used = False
        thin_streak = 0
        for qi, q in enumerate(queries):
            raw += bing_web.search(q)
            kept = search.filter_claim_hits(raw, ctext, logger, limit=_REPORT_CLAIM_BPOOL_KEEP)
            if len(kept) >= _REPORT_CLAIM_POOL_GOOD:
                break
            thin_streak += 1
            if ddgs_used or not (thin_streak >= 2 or qi == len(queries) - 1):
                continue
            ddgs_used = True
            raw += search.run_text_searches([q], logger)
            kept = search.filter_claim_hits(raw, ctext, logger, limit=_REPORT_CLAIM_BPOOL_KEEP)
            if len(kept) >= _REPORT_CLAIM_POOL_GOOD:
                break
        if not kept:
            logger.info(f"[report-claim-search] {cid} ({ctext[:70]!r}): nothing usable found")
        for h in kept:
            h.scope, h.claim_id = "report_claim", cid
        feeds.attach_excerpts(kept, ctext, logger, focus=ctext)
        pool[cid] = kept
        total += len(kept)
    bing_web.close()
    backed = sum(1 for hits in pool.values() if hits)
    progress(f"Report claim backing search: {total} candidate page(s) across {backed}/{len(claims)} claim(s)")
    logger.info(f"[report-claim-search] pool: {backed}/{len(claims)} claim(s) have candidates, {total} page(s) total")
    return pool


def _format_claims_for_select(claims: list[dict]) -> str:
    """The claims list handed to SELECT (and, via run_build_evidence, to the writer): one line per
    claim, its id, kind and text — the text must carry the claim's own distinctive names so SELECT
    can judge a pool hit's stance against exactly what the article asserted."""
    if not claims:
        return "(none — the Step 1 call extracted no unverifiable claims from this article)"
    lines = []
    for c in claims:
        line = f'- {c["id"]} [{c.get("kind") or "claim"}] "{c["claim"]}"'
        if c.get("why"):
            line += f" — needs backing because: {c['why']}"
        lines.append(line)
    return "\n".join(lines)


def _flatten_claim_pool(
    claims: list[dict], pool: dict[str, list[search.SearchHit]],
) -> list[tuple[str, str, search.SearchHit]]:
    """(P-number, claim id, hit) in deterministic claim order — P-numbers are sequential across all
    claims (P1, P2, …) and are the ids SELECT is told to cite. Empty when there are no claims, so
    callers can distinguish 'no claims' from 'claims but no results'."""
    if not claims:
        return []
    out: list[tuple[str, str, search.SearchHit]] = []
    for c in claims:
        for h in (pool or {}).get(c["id"]) or []:
            out.append((f"P{len(out) + 1}", c["id"], h))
    return out


def _format_pool_for_select(pool: list[tuple[str, str, search.SearchHit]], claims: list[dict]) -> str:
    if claims and not pool:
        return "(none — the claim searches found nothing usable for any claim)"
    lines = []
    for pid, cid, h in pool:
        tag = f"DATED, {h.date.strftime('%Y-%m-%d')}" if h.date else "UNDATED"
        body = h.excerpt or h.snippet[:300]
        lines.append(f"[{pid}] (for: {cid}) {h.title} | {h.source or h.host} | {tag}\n      {body}")
    return "\n".join(lines) or "(no claims were extracted, so there are no claim results)"


def _format_questions_for_select(questions: list[dict]) -> str:
    """The chosen subheading questions handed to SELECT: one line per question, its id, its
    assigned section and its text — mirrors _format_claims_for_select."""
    if not questions:
        return "(none — no subheading questions were chosen for this market)"
    return "\n".join(f'- {q["id"]} [replaces the {q.get("section", "?")} heading] "{q["question"]}"' for q in questions)


def _flatten_question_pool(
    questions: list[dict], pool: dict[str, list[search.SearchHit]],
) -> list[tuple[str, str, search.SearchHit]]:
    """(PQ-number, question id, hit) in deterministic question order — mirrors _flatten_claim_pool,
    with a "PQ" prefix so these ids never collide with the claim pool's "P" ids in the same prompt."""
    if not questions:
        return []
    out: list[tuple[str, str, search.SearchHit]] = []
    for q in questions:
        for h in (pool or {}).get(q["id"]) or []:
            out.append((f"PQ{len(out) + 1}", q["id"], h))
    return out


def _format_question_pool_for_select(pool: list[tuple[str, str, search.SearchHit]], questions: list[dict]) -> str:
    if questions and not pool:
        return "(none — the question searches found nothing usable for any question)"
    lines = []
    for pid, qid, h in pool:
        tag = f"DATED, {h.date.strftime('%Y-%m-%d')}" if h.date else "UNDATED"
        body = h.excerpt or h.snippet[:300]
        lines.append(f"[{pid}] (for: {qid}) {h.title} | {h.source or h.host} | {tag}\n      {body}")
    return "\n".join(lines) or "(no subheading questions were chosen, so there are no question results)"


def _format_report_claims_for_select(claims: list[dict]) -> str:
    """The extracted report claims handed to SELECT: one line per claim, its id, its assigned
    section and its text — mirrors _format_claims_for_select."""
    if not claims:
        return "(none — no specific, checkable report claims were extracted for this market)"
    return "\n".join(f'- {c["id"]} [{c["section"]}] "{c["claim"]}"' for c in claims)


def _flatten_report_claim_pool(
    claims: list[dict], pool: dict[str, list[search.SearchHit]],
) -> list[tuple[str, str, search.SearchHit]]:
    """(PR-number, claim id, hit) in deterministic claim order — mirrors _flatten_question_pool,
    with a "PR" prefix so these ids never collide with the claim pool's "P" or the question pool's
    "PQ" ids in the same prompt."""
    if not claims:
        return []
    out: list[tuple[str, str, search.SearchHit]] = []
    for c in claims:
        for h in (pool or {}).get(c["id"]) or []:
            out.append((f"PR{len(out) + 1}", c["id"], h))
    return out


def _format_report_claim_pool_for_select(pool: list[tuple[str, str, search.SearchHit]], claims: list[dict]) -> str:
    if claims and not pool:
        return "(none — the report-claim searches found nothing usable for any claim)"
    lines = []
    for pid, cid, h in pool:
        tag = f"DATED, {h.date.strftime('%Y-%m-%d')}" if h.date else "UNDATED"
        body = h.excerpt or h.snippet[:300]
        lines.append(f"[{pid}] (for: {cid}) {h.title} | {h.source or h.host} | {tag}\n      {body}")
    return "\n".join(lines) or "(no report claims were extracted, so there are no report-claim results)"


def run_select(
    article: SourceArticle, orient: dict, sponsor: Paragraph,
    report: reports.Report, hits: list[search.SearchHit],
    logger: logging.Logger, claim_hits: dict[str, list[search.SearchHit]] | None = None,
    questions: list[dict] = (), question_hits: dict[str, list[search.SearchHit]] | None = None,
    report_claims: list[dict] = (), report_claim_hits: dict[str, list[search.SearchHit]] | None = None,
    headline_figures: dict | None = None,
) -> dict:
    """CALL 2: select evidence for the human-confirmed report. Returns the parsed JSON.
    Report choice is no longer this call's job — the human picked the market/report in Step 1
    (see run_fetch_confirmed_report), so this call only judges which search hits are usable.

    Claim backing (when Step 1 extracted claims and the caller searched for them, claim_hits maps
    claim id -> candidate pages): SELECT additionally picks at most 2 pages per claim and tags each
    supports/qualifies/contradicts; without a pool the claims still reach SELECT and the writer so
    unbacked ones get softened, not asserted bare.

    Question backing (when Step 2 chose subheading questions and searched for them, question_hits
    maps question id -> candidate pages, the same way claim_hits does): SELECT additionally picks at
    most 2 pages per question — no stance, since a question isn't a claim being judged true or false,
    just a fact worth citing in its direct answer.

    Report claim backing (when Step 2 extracted report claims and searched for them, report_claim_hits
    maps claim id -> candidate pages): SELECT additionally picks at most 3 pages per claim that
    independently corroborate it — same no-stance shape as question backing, since this is about
    reinforcing what the CMI report already states, never judging or contradicting it.

    headline_figures (optional; pipeline._headline_figures(run.fact_sheet)): the fact sheet's own
    size/forecast/CAGR values. When given, the post-SELECT backstop _drop_market_sizing_facts also
    drops an item whose fact names a market and carries nothing but restatements of those figures,
    so a second source for the headline numbers cannot reach the draft even in words the regexes miss."""
    section(logger, "CALL 2: SELECT EVIDENCE")
    claims = orient_claims(orient)
    claim_flat = _flatten_claim_pool(claims, claim_hits or {})
    questions = list(questions or [])
    question_flat = _flatten_question_pool(questions, question_hits or {})
    report_claims = list(report_claims or [])
    report_claim_flat = _flatten_report_claim_pool(report_claims, report_claim_hits or {})
    selection = llm.chat_json(prompts.SELECT_SYSTEM, prompts.SELECT_USER.format(
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        bridge_angle=orient.get("bridge_angle", ""),
        sponsor=sponsor.text,
        report_name=report.name,
        hits=_format_hits(hits),
        claims_block=_format_claims_for_select(claims),
        claim_pool=_format_pool_for_select(claim_flat, claims),
        questions_block=_format_questions_for_select(questions),
        question_pool=_format_question_pool_for_select(question_flat, questions),
        report_claims_block=_format_report_claims_for_select(report_claims),
        report_claim_pool=_format_report_claim_pool_for_select(report_claim_flat, report_claims),
        max_selected=config.SEARCH_MAX_SELECTED,
    ), call_name="select", logger=logger, max_tokens=8000)
    _normalize_selection_tags(selection, hits, logger)
    _strip_players_tag(selection, logger)
    _drop_stale_facts(selection, logger)
    _drop_market_sizing_facts(selection, logger, headline_figures)
    _verify_selection_figures(selection, hits, logger)
    _normalize_claim_backing(selection, claims, claim_flat, logger)
    _normalize_question_backing(selection, questions, question_flat, logger)
    _normalize_report_claim_backing(selection, report_claims, report_claim_flat, logger)
    return selection


_FIGURE_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _figures(text: str) -> set[str]:
    """Digit strings in `text` with commas dropped. Single digits and plain years are ignored: they turn up in
    ordinary prose everywhere (a publish date the model correctly states is not in the article's own text)."""
    out = set()
    for m in _FIGURE_RE.findall(text or ""):
        n = m.replace(",", "").rstrip(".")
        if len(n) >= 2 and not re.fullmatch(r"20[1-3]\d", n):
            out.add(n)
    return out


def _verify_selection_figures(selection: dict, hits: list[search.SearchHit], logger: logging.Logger) -> None:
    """SELECT's fact/detail are LLM-written; a figure that is nowhere in the source's own text is an invented number,
    which must never reach the writer. An item whose FACT carries one is dropped; an unsupported DETAIL is blanked."""
    kept = []
    for e in selection.get("evidence") or []:
        m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
        if not m or not 1 <= int(m.group(1)) <= len(hits):
            kept.append(e)  # a bad id is dropped by run_build_evidence, which logs it
            continue
        h = hits[int(m.group(1)) - 1]
        source = f"{h.title} {h.snippet} {h.excerpt}".replace(",", "")
        bad_fact = sorted(n for n in _figures(str(e.get("fact", ""))) if n not in source)
        if bad_fact:
            logger.warning(f"[select] dropped {e.get('id')}: its fact states {bad_fact}, which is not in the source text")
            continue
        detail = str(e.get("detail") or "").strip()
        bad_detail = sorted(n for n in _figures(detail) if n not in source)
        if bad_detail:
            logger.warning(f"[select] blanked the detail of {e.get('id')}: it states {bad_detail}, not in the source text")
            detail = ""
        e["detail"] = detail
        kept.append(e)
    selection["evidence"] = kept


# A claim's backing is only an enhancement: each entry must cite a pool id that exists, carry a
# valid stance, and state only figures that appear in that pool hit's own text (verified below, the
# same way market-evidence figures are) — never guessed at. At most two entries per claim; the
# claim-id comes from the pool tag itself, so a mislabelled entry lands under the claim it was
# actually searched for.
_CLAIM_STANCES = frozenset({"supports", "qualifies", "contradicts"})


def _fresh_detail(detail: str, pid: str, logger: logging.Logger) -> str:
    """A backing item's detail is the opening of the source page; blank it when it states a stale date."""
    stale = [
        m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(detail)
        if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
    ]
    if stale:
        logger.info(f"[select] blanked the detail of claim backing {pid}: it states a stale date {stale}")
        return ""
    return detail


def _normalize_claim_backing(
    selection: dict, claims: list[dict], pool: list[tuple[str, str, search.SearchHit]], logger: logging.Logger,
) -> None:
    """Sanitise SELECT's claim_backing, the same way _verify_selection_figures polices the market
    evidence: every entry must cite a pool id that exists (an id SELECT invented is dropped, never
    guessed at), carry a valid stance, and state only figures that appear in that pool hit's own
    text. At most two entries per claim; the claim-id comes from the pool tag itself, so a
    mislabelled entry lands under the claim it was actually searched for. Writes the cleaned list
    back to selection["claim_backing"] with source/excerpt strings attached for run_build_evidence."""
    claims_ids = {c["id"] for c in claims}
    pool_by_pid = {pid: (cid, h) for pid, cid, h in pool}
    per_claim: dict[str, int] = {}
    kept: list[dict] = []
    for e in selection.get("claim_backing") or []:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("id") or "").strip().upper()
        stance = str(e.get("stance") or "").strip().lower()
        if pid not in pool_by_pid:
            logger.info(f"[select] dropped claim backing entry (unknown pool id {pid!r}): {e}")
            continue
        pid_cid, hit = pool_by_pid[pid]
        # trust the pool tag over whichever claim id SELECT typed
        cid = pid_cid if pid_cid in claims_ids else (str(e.get("claim_id") or "").strip().upper() or pid_cid)
        if cid not in claims_ids:
            logger.info(f"[select] dropped claim backing entry (unresolvable claim id) [pid {pid}]: {e}")
            continue
        if stance not in _CLAIM_STANCES:
            logger.info(f"[select] dropped claim backing {pid} (bad stance {stance!r})")
            continue
        fact = re.sub(r"\s+", " ", str(e.get("fact") or "")).strip()
        if not fact:
            fact = " ".join((hit.snippet or "").split())[:220]
            logger.info(f"[select] backing {pid} had no fact, substituted first words of the snippet")
        source = f"{hit.title} {hit.snippet} {hit.excerpt}".replace(",", "")
        bad = sorted(n for n in _figures(fact) if n not in source)
        if bad:
            logger.warning(f"[select] dropped claim backing {pid}: its fact states {bad}, which is not in the source text")
            continue
        # Same freshness policy as the market evidence (_drop_stale_facts): the draft may not cite a dated event older than
        # the freshness window, whichever stream it came from. The claim search has no recency cutoff on purpose (a
        # background explainer need not be recent), so the date check has to happen here, on what SELECT wrote.
        stale = [
            m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(fact)
            if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
        ]
        if stale:
            logger.info(f"[select] dropped claim backing {pid}: its fact states a stale date {stale}")
            continue
        per_claim[cid] = per_claim.get(cid, 0) + 1
        if per_claim[cid] > 2:
            logger.info(f"[select] {cid} already has 2 backing entries — dropped {pid}")
            continue
        kept.append({
            "claim_id": cid, "id": pid, "stance": stance, "fact": fact,
            "source": hit.source or hit.host, "url": hit.url,
            "detail": _fresh_detail(" ".join((hit.excerpt or "").split())[:320], pid, logger),
        })
    selection["claim_backing"] = kept
    if claims:
        logger.info(
            f"[select] claim backing: {len(kept)} entry(ies) across {len(per_claim)} of {len(claims_ids)} claim(s) "
            f"({', '.join(sorted(per_claim)) or 'none backed'})"
        )


def _normalize_question_backing(
    selection: dict, questions: list[dict], pool: list[tuple[str, str, search.SearchHit]], logger: logging.Logger,
) -> None:
    """Sanitise SELECT's question_backing — mirrors _normalize_claim_backing exactly, minus the
    stance field (a question isn't a claim being judged true/false, just a fact worth citing in its
    direct answer). Writes the cleaned list back to selection["question_backing"] with
    source/excerpt strings attached for run_build_evidence."""
    question_ids = {q["id"] for q in questions}
    pool_by_pid = {pid: (qid, h) for pid, qid, h in pool}
    per_question: dict[str, int] = {}
    kept: list[dict] = []
    for e in selection.get("question_backing") or []:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("id") or "").strip().upper()
        if pid not in pool_by_pid:
            logger.info(f"[select] dropped question backing entry (unknown pool id {pid!r}): {e}")
            continue
        pid_qid, hit = pool_by_pid[pid]
        # trust the pool tag over whichever question id SELECT typed
        qid = pid_qid if pid_qid in question_ids else (str(e.get("question_id") or "").strip().upper() or pid_qid)
        if qid not in question_ids:
            logger.info(f"[select] dropped question backing entry (unresolvable question id) [pid {pid}]: {e}")
            continue
        fact = re.sub(r"\s+", " ", str(e.get("fact") or "")).strip()
        if not fact:
            fact = " ".join((hit.snippet or "").split())[:220]
            logger.info(f"[select] question backing {pid} had no fact, substituted first words of the snippet")
        source = f"{hit.title} {hit.snippet} {hit.excerpt}".replace(",", "")
        bad = sorted(n for n in _figures(fact) if n not in source)
        if bad:
            logger.warning(f"[select] dropped question backing {pid}: its fact states {bad}, which is not in the source text")
            continue
        stale = [
            m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(fact)
            if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
        ]
        if stale:
            logger.info(f"[select] dropped question backing {pid}: its fact states a stale date {stale}")
            continue
        per_question[qid] = per_question.get(qid, 0) + 1
        if per_question[qid] > 2:
            logger.info(f"[select] {qid} already has 2 backing entries — dropped {pid}")
            continue
        kept.append({
            "question_id": qid, "id": pid, "fact": fact,
            "source": hit.source or hit.host, "url": hit.url,
            "detail": _fresh_detail(" ".join((hit.excerpt or "").split())[:320], pid, logger),
        })
    selection["question_backing"] = kept
    if questions:
        logger.info(
            f"[select] question backing: {len(kept)} entry(ies) across {len(per_question)} of {len(question_ids)} question(s) "
            f"({', '.join(sorted(per_question)) or 'none backed'})"
        )


def _normalize_report_claim_backing(
    selection: dict, claims: list[dict], pool: list[tuple[str, str, search.SearchHit]], logger: logging.Logger,
) -> None:
    """Sanitise SELECT's report_claim_backing — mirrors _normalize_question_backing (no stance: this
    corroborates the report's own statement, it never judges or contradicts it), plus one extra
    rule market/question backing don't need: a fact that is really market sizing/forecast (a rival
    figure that could contradict the report's own) is rejected, the same policy
    _drop_market_sizing_facts applies to ordinary market evidence. Writes the cleaned list back to
    selection["report_claim_backing"], each entry carrying its claim's own "section" (trends,
    subsegment or country_us) so run_build_evidence can route it into the matching evidence group."""
    claims_by_id = {c["id"]: c for c in claims}
    pool_by_pid = {pid: (cid, h) for pid, cid, h in pool}
    per_claim: dict[str, int] = {}
    kept: list[dict] = []
    for e in selection.get("report_claim_backing") or []:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("id") or "").strip().upper()
        if pid not in pool_by_pid:
            logger.info(f"[select] dropped report claim backing entry (unknown pool id {pid!r}): {e}")
            continue
        pid_cid, hit = pool_by_pid[pid]
        cid = pid_cid if pid_cid in claims_by_id else (str(e.get("claim_id") or "").strip().upper() or pid_cid)
        if cid not in claims_by_id:
            logger.info(f"[select] dropped report claim backing entry (unresolvable claim id) [pid {pid}]: {e}")
            continue
        fact = re.sub(r"\s+", " ", str(e.get("fact") or "")).strip()
        if not fact:
            fact = " ".join((hit.snippet or "").split())[:220]
            logger.info(f"[select] report claim backing {pid} had no fact, substituted first words of the snippet")
        if _is_market_sizing(fact):
            logger.info(f"[select] dropped report claim backing {pid}: it states market sizing/forecast ({fact[:80]!r})")
            continue
        source = f"{hit.title} {hit.snippet} {hit.excerpt}".replace(",", "")
        bad = sorted(n for n in _figures(fact) if n not in source)
        if bad:
            logger.warning(f"[select] dropped report claim backing {pid}: its fact states {bad}, which is not in the source text")
            continue
        stale = [
            m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(fact)
            if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
        ]
        if stale:
            logger.info(f"[select] dropped report claim backing {pid}: its fact states a stale date {stale}")
            continue
        per_claim[cid] = per_claim.get(cid, 0) + 1
        if per_claim[cid] > 3:  # report claims get 3 (up from the 2 article claims/questions get): these
            # entries ARE the substance for the report's trends/subsegment/country_us sections
            logger.info(f"[select] {cid} already has 3 backing entries — dropped {pid}")
            continue
        kept.append({
            "claim_id": cid, "id": pid, "section": claims_by_id[cid]["section"], "fact": fact,
            "source": hit.source or hit.host, "url": hit.url,
            "detail": _fresh_detail(" ".join((hit.excerpt or "").split())[:320], pid, logger),
        })
    selection["report_claim_backing"] = kept
    if claims:
        logger.info(
            f"[select] report claim backing: {len(kept)} entry(ies) across {len(per_claim)} of {len(claims_by_id)} claim(s) "
            f"({', '.join(sorted(per_claim)) or 'none backed'})"
        )


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
            continue
        stale_detail = [
            m.group(0) for m in freshness.INLINE_EVENT_DATE_RE.finditer(str(e.get("detail", "")))
            if (d := freshness.event_date(m)) is not None and freshness.is_stale(d)
        ]
        if stale_detail:
            logger.info(f"[select] blanked the detail of {e.get('id')}: it states a stale date {stale_detail}")
            e["detail"] = ""
        kept.append(e)
    selection["evidence"] = kept


# Market sizing belongs to the CMI report alone. SELECT is told to reject it (prompts.SELECT_USER, test 3); this is the
# backstop for a model that keeps one anyway: a fact that gives the size/forecast/growth of a market or industry, or that
# attributes anything to a rival market-research firm, would put a second, contradicting figure in the draft. The last
# two alternatives catch the sentence reading the other way round: first a sizing verb (estimates, values, puts,
# forecasts, projects, pegs, sizes) and only then the market name and the currency amount, plus a bare market noun
# followed straight by at and an amount with no sizing verb at all.
_RIVAL_RESEARCH_RE = re.compile(
    r"\b(mordor intelligence|marketsandmarkets|markets and markets|grand view research|research and markets|technavio|"
    r"fortune business insights|precedence research|allied market research|imarc|verified market research|"
    r"transparency market research|future market insights|persistence market research|market research future|"
    r"polaris market research|straits research|expert market research|data bridge market research)\b", re.IGNORECASE)
_MARKET_SIZING_RE = re.compile(
    r"\bcagr\b|\bcompound annual growth\b|\b(market|industry) size\b"
    r"|\b(market|industry|sector|segment)\b[^.;]{0,90}\b(valued|worth|sized|projected|forecast(ed)?|"
    r"expected to (reach|grow|hit|double)|will (reach|grow|add|double|hit)|estimated (at|to)|to reach|to grow)\b"
    r"|\b(estimates?|estimated|values?|valued|puts|sized?|sizes|forecasts?|projects?|projected|pegs?)\b[^.;]{0,60}"
    r"\b(market|industry|sector|segment)\s+(?:at|for|near|to|reaching)\s+"
    r"(?:(?:US)?\$|£|€|usd|eur|gbp|dollars?)?\s*\d"
    r"|\b(market|industry|sector)\s+(?:approximately\s+|about\s+|around\s+|roughly\s+)?at\s+"
    r"(?:(?:US)?\$|£|€|usd|eur|gbp|dollars?)?\s*\d",
    re.IGNORECASE)


def _is_market_sizing(text: str) -> bool:
    return bool(_RIVAL_RESEARCH_RE.search(text) or _MARKET_SIZING_RE.search(text))


def _drop_market_sizing_facts(
    selection: dict, logger: logging.Logger, headline_figures: dict | None = None,
) -> None:
    """Remove selected market facts that state a market's size/forecast/growth or cite a rival research firm; blank a
    kept item's `detail` when it does. When `headline_figures` is given (the fact sheet's own size/forecast/CAGR values,
    see _headline_figures), a fact that names a market and carries nothing but restatements of those very figures is
    dropped too: whoever says it, a second figure for the same market is banned. Claim backing is exempt: it backs the
    article's own claims, not market sizing."""
    kept = []
    for e in selection.get("evidence") or []:
        fact = str(e.get("fact", ""))
        if _is_market_sizing(fact):
            logger.info(f"[select] dropped {e.get('id')}: market sizing/forecast or a rival research firm ({fact[:80]!r})")
            continue
        if _restates_headline_figures(fact, headline_figures):
            logger.info(f"[select] dropped {e.get('id')}: every figure it states is one of the fact sheet's own "
                        f"headline figures ({fact[:80]!r})")
            continue
        if _is_market_sizing(str(e.get("detail", ""))):
            logger.info(f"[select] blanked the detail of {e.get('id')}: it states market sizing or cites a rival research firm")
            e["detail"] = ""
        kept.append(e)
    selection["evidence"] = kept


_MARKET_NOUN_RE = re.compile(r"\b(market|industry|sector|segment)\b", re.IGNORECASE)


def _headline_figures(fs: dict | None) -> dict | None:
    """The fact sheet's headline numbers (facts.HEADLINE_KEYS: size, forecast, cagr) each normalised the way _figures
    normalises any fact text, plus their float values so 4.59 and 4.590 still compare equal. None when there is no fact
    sheet or no headline figure survived it, so callers can pass it straight through to _drop_market_sizing_facts."""
    if not fs:
        return None
    raw: set[str] = set()
    for key in ("size", "forecast", "cagr"):
        entry = fs.get(key)
        if isinstance(entry, dict):
            raw |= _figures(str(entry.get("value", "")))
    floats: set[float] = set()
    for r in raw:
        try:
            floats.add(float(r))
        except ValueError:
            pass
    return {"raw": raw, "floats": floats} if (raw or floats) else None


def _restates_headline_figures(fact: str, headline: dict | None) -> bool:
    """True when the fact names a market AND every figure it carries (years never count — see _figures) is one of the
    fact sheet's own headline figures: numeric content that only re-states what the report itself already states."""
    if not headline or not _MARKET_NOUN_RE.search(fact):
        return False
    figs = _figures(fact)
    if not figs:
        return False
    for n in figs:
        if n in headline["raw"]:
            continue
        try:
            matched = float(n) in headline["floats"]
        except ValueError:
            matched = False
        if not matched:
            return False
    return True


# The key-players section shows companies acting in the market, not what was said about them. A recognition (an award,
# a ranking, an honour, a certification) or a personnel change reads as concrete, named-company news and keeps slipping
# through SELECT, so this pair of word lists is the deterministic backstop — deliberately narrow and case-insensitive,
# with every match rounded to whole words.
_RECOGNITION_RE = re.compile(
    r"\b(awards?|awarded|recognitions?|ranked|ranking|named among|honou?rs?|honou?red|trophy|of the year|"
    r"certifications?|certified)\b", re.IGNORECASE)
_PERSONNEL_RE = re.compile(
    r"\b(chief (executive|financial|technology|information|operating) officer|ceo|cfo|cto|cio|coo|"
    r"appointed|appoints|hired|hires|joins as|steps down|stepping down|resigns|departure|leaves|"
    r"leaving|executives leaving)\b", re.IGNORECASE)


def _strip_players_tag(selection: dict, logger: logging.Logger) -> None:
    """Remove an item's `players` tag when its fact is a recognition or a personnel change (a recognition is what
    someone else said about the company, a personnel change is inside the company's walls — neither shows how a
    company changes the market). The item survives under any other tag it carries; it is deleted only when `players`
    was its one and only tag, so it could never have been used anywhere else."""
    kept = []
    for e in list(selection.get("evidence") or []):
        fact = re.sub(r"\s+", " ", str(e.get("fact", "")))
        tags = list(e.get("use_for_all") or [])
        if "players" in tags and (_RECOGNITION_RE.search(fact) or _PERSONNEL_RE.search(fact)):
            rest = [t for t in tags if t != "players"]
            if rest:
                e["use_for_all"] = rest
                e["use_for"] = rest[0]
                logger.info(f"[select] removed 'players' from {e.get('id')}: its fact is a recognition or a personnel "
                            f"change, not the company's own move in the market ({fact[:80]!r})")
            else:
                logger.info(f"[select] dropped {e.get('id')}: its fact is a recognition or a personnel change and "
                            f"'players' was its only tag ({fact[:80]!r})")
                continue
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


# Evidence reaches the writer grouped by the part of the article each item mainly serves (its FIRST use_for
# tag); a flat list with every item tagged for every section left the writer nothing to route by.
_EVIDENCE_GROUPS = (
    ("trends", "MARKET TRENDS (back each of the three trends with at least one of these, two where space allows)"),
    ("us", "THE U.S. PARAGRAPH"),
    ("players", "KEY PLAYERS (each entry about one company explains that company's own move in the market and what it changes for buyers and its rivals)"),
    ("context", "SEGMENTS, SUB-SEGMENTS, GROWTH DRIVERS AND THE BRIDGE (the conclusion adds nothing new)"),
)


def run_build_evidence(
    selection: dict, hits: list[search.SearchHit], logger: logging.Logger, orient: dict | None = None,
    questions: list[dict] = (),
) -> str:
    """Build the evidence block passed to the writer from Call 2 output + search hits. Groups:
    ARTICLE EVIDENCE (backing for claims extracted from the original article, one entry per claim id)
    followed by that claims list with each claim's status; SUBHEADING QUESTIONS (the chosen questions
    themselves, each with whatever QUESTION EVIDENCE backs its direct answer); then the MARKET
    EVIDENCE groups. Questions reach here ready for a future write-call to turn into sections (see
    pipeline.run_select_questions/run_question_evidence) — this function only assembles the text."""
    backing = [b for b in selection.get("claim_backing") or [] if isinstance(b, dict)]
    claims = orient_claims(orient)
    questions = list(questions or [])
    question_backing = [b for b in selection.get("question_backing") or [] if isinstance(b, dict)]
    parts: list[str] = []
    if backing or claims:
        backed_ids = {b["claim_id"] for b in backing}
        if backing:
            parts.append("### ARTICLE EVIDENCE (backs, qualifies or corrects the original article's own claims; publisher block only)")
            for b in backing:
                parts.append(f"- ({b['claim_id']}) [{b['stance']}] {b['fact']} (source: {b.get('source') or 'web'})")
                if str(b.get("detail") or "").strip():
                    parts.append(f"    DETAIL: {str(b['detail']).strip()}")
        if claims:
            parts.append("### CLAIMS FROM THE ORIGINAL ARTICLE (handle each one under the CLAIMS rules, in the publisher block)")
            for c in claims:
                line = f'* {c["id"]} [{c.get("kind") or "claim"}] "{c["claim"]}"'
                if c["id"] in backed_ids:
                    line += " — BACKED: apply CLAIMS rule 1, 2 or 3, whichever matches the stance of its ARTICLE EVIDENCE item(s) above, naming the source in the sentence"
                else:
                    line += " — NO OUTSIDE BACKING FOUND: apply CLAIMS rule 4 (hedged and attributed if it is a general statement; dropped if it is a superlative or a verdict about a named product); never state it as fact"
                parts.append(line)
    if questions:
        parts.append(
            "### SUBHEADING QUESTIONS (real, commonly-searched questions about this market — each REPLACES the "
            "heading and opening 1-3 lines of one of the sections named below, it does not add a new section; "
            "ground the answer in the QUESTION EVIDENCE below where one exists, otherwise in the CMI report and "
            "this article's own content)"
        )
        backed_q_ids = {b["question_id"] for b in question_backing}
        for q in questions:
            line = f'* {q["id"]} [replaces the {q.get("section", "?")} heading] "{q["question"]}"'
            if q["id"] not in backed_q_ids:
                line += " — no outside evidence found for this one; answer from the CMI report/article content only"
            parts.append(line)
        if question_backing:
            parts.append("### QUESTION EVIDENCE (grounds a subheading question's direct answer with a real outside fact)")
            for b in question_backing:
                parts.append(f"- ({b['question_id']}) {b['fact']} (source: {b.get('source') or 'web'})")
                if str(b.get("detail") or "").strip():
                    parts.append(f"    DETAIL: {str(b['detail']).strip()}")
    grouped: dict[str, list[str]] = {key: [] for key, _ in _EVIDENCE_GROUPS}
    # Report-claim backing (independent corroboration of a specific statement the CMI REPORT ITSELF
    # makes — see run_report_claims/run_report_claim_evidence) folds directly into the SAME market
    # evidence groups the writer already reads, routed by the claim's own section, so these items are
    # simply more MARKET EVIDENCE to the writer — just evidence specifically aimed at reinforcing the
    # report's own trends/sub-segment/U.S. claims, which is where the user asked for more substance.
    for b in [b for b in selection.get("report_claim_backing") or [] if isinstance(b, dict)]:
        group = _REPORT_CLAIM_SECTION_TO_GROUP.get(b.get("section"), "context")
        line = f"- ({b['claim_id']}) [{group}] {b['fact']} (source: {b.get('source') or 'web'})"
        if str(b.get("detail") or "").strip():
            line += f"\n    DETAIL: {str(b['detail']).strip()}"
        grouped[group].append(line)
    for e in selection.get("evidence") or []:
        m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
        if not m or not 1 <= int(m.group(1)) <= len(hits):
            logger.info(f"evidence item dropped (bad id {e.get('id')!r}): {e}")
            continue
        h = hits[int(m.group(1)) - 1]
        if h.date and freshness.is_stale(h.date.date()):
            # Last gate before the writer: the draft may not cite anything older than the freshness window, and the
            # source's own date is what the evidence label shows the writer, whatever earlier filter let the item by.
            logger.info(f"evidence item dropped (source dated {h.date:%Y-%m-%d}, older than the freshness window): {e.get('id')}")
            continue
        date = h.date.strftime("%Y-%m") if h.date else "undated"
        evidence_id = e["id"].strip().upper()
        tags = e.get("use_for_all") or [e.get("use_for", "context")]
        line = f"- ({evidence_id}) [{', '.join(tags)}] {e.get('fact', '')} (source: {h.source or h.host}, {date})"
        if str(e.get("detail") or "").strip():
            line += f"\n    DETAIL: {str(e['detail']).strip()}"
        grouped[tags[0] if tags[0] in grouped else "context"].append(line)
    for key, label in _EVIDENCE_GROUPS:
        if grouped[key]:
            parts.append(f"### MARKET EVIDENCE FOR {label}")
            parts.extend(grouped[key])
    evidence = "\n".join(parts) or "(none)"
    section(logger, "FINAL EVIDENCE PASSED TO WRITER")
    logger.info(evidence)
    return evidence


_EVIDENCE_LINE_RE = re.compile(r"^- \(([A-Z]+\d+)\) \[([^\]]*)\] (.*) \(source: (.*)\)\s*$")


def _parse_evidence(evidence: str) -> list[dict]:
    """Inverse of run_build_evidence: [{id, tags, group, fact, detail, source}]. Parsing the block (rather than threading
    the structured list through every caller) keeps the string interface that saved fixtures and the stepwise API use.
    ARTICLE EVIDENCE lines are identified by their C-number id and get group "article"; market lines keep the live
    use_for tag's group."""
    items: list[dict] = []
    for line in (evidence or "").splitlines():
        m = _EVIDENCE_LINE_RE.match(line)
        if m:
            tags = [t.strip() for t in m.group(2).split(",") if t.strip()]
            is_article = m.group(1).startswith("C")
            items.append({"id": m.group(1), "tags": tags,
                          "group": "article" if is_article else (tags[0] if tags else "context"),
                          "fact": m.group(3).strip(), "detail": "", "source": m.group(4).strip()})
        elif items and line.strip().startswith("DETAIL:"):
            items[-1]["detail"] = line.strip()[len("DETAIL:"):].strip()
    return items


_QUESTION_ASSIGNMENT_RE = re.compile(r'^\* (Q\d+) \[replaces the (\w+) heading\] "(.+?)"', re.MULTILINE)


def _parse_question_assignments(evidence: str) -> list[dict]:
    """Inverse of run_build_evidence's SUBHEADING QUESTIONS lines: [{id, section, question}] — lets
    _write_problems run checks.question_heading_problems without threading the structured questions
    list through run_write's own signature, the same string-interface choice _parse_evidence makes."""
    return [
        {"id": m.group(1), "section": m.group(2), "question": m.group(3)}
        for m in _QUESTION_ASSIGNMENT_RE.finditer(evidence or "")
    ]


def _min_evidence_total(n_items: int) -> int:
    """How many distinct MARKET evidence items a draft must use: about 45% of what SELECT vetted, never fewer
    than 6 nor more than 10 (the claim-backing half of the run now also asks the writer for ARTICLE EVIDENCE in
    the publisher block, so a higher market cap crowds both into the same article; a ~1,500-word piece carries
    10 market items alongside them), and never more than exists."""
    return min(n_items, max(6, min(10, round(n_items * 0.45)))) if n_items else 0


# (SOP tag, evidence group it draws on, distinct items the section should use, label for the feedback)
_SECTION_EVIDENCE_NEEDS = (
    ("trends", "trends", 3, "market-trends"),
    ("country_us", "us", 2, "U.S. analysis"),
    ("key_players", "players", 3, "key-players"),
)


_MONTH_WORDS = frozenset({
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
})


def _strict_signature(fact: str) -> set[str]:
    """The names and figures that identify ONE evidence item. Starts from checks._fact_signature_tokens but drops what
    appears in almost any draft and so proves nothing: years, month names, and very short words."""
    return {
        t for t in checks._fact_signature_tokens(fact)
        if t not in _MONTH_WORDS and not re.fullmatch(r"20[1-3]\d,?", t) and (len(t) >= 4 or any(c.isdigit() for c in t))
    }


# Capitalised words that name no one in particular; a match on one of these alone proves nothing.
# Generic English words and universal company-name structure/suffix words only — no industry nouns,
# since a matching helper cannot know which industry a given run is about.
_GENERIC_NAME_WORDS = frozenset({
    "management", "capital", "group", "global", "partners", "services", "solutions", "holdings",
    "market", "markets", "company", "companies", "life", "line", "first", "new", "marketing",
    "business", "industry", "research",
})


_ENTITY_RE = re.compile(r"\b[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*\b")


def _fact_entities(fact: str) -> list[tuple[list[str], str]]:
    """Named things in a fact: (specific words, whole phrase) for each capitalised run that names someone in
    particular. Generic words (Group, Global), month names and sentence-opening filler are not "specific"."""
    out = []
    for run in _ENTITY_RE.findall(fact):
        words = [w.strip(".'-") for w in run.split()]
        specific = [
            w.lower() for w in words
            if len(w) >= 3 and w.lower() not in _GENERIC_NAME_WORDS and w.lower() not in _MONTH_WORDS
            and w.lower() not in checks._FACT_GENERIC_CAPS
        ]
        if specific:
            out.append((specific, " ".join(w.lower() for w in words)))
    return out


def _evidence_used_ids(text: str, items: list[dict]) -> set[str]:
    """Ids of evidence items that really appear in `text`. An item is used when one of its named entities does (a
    single-word name as a word; a longer name as its whole phrase, or all of its specific words when there are two
    or more), or when at least two of its figures do. A lone common word that happens to sit inside a name
    ("Group" in "Acme Group") must NOT count: the first version of this matcher did, and reported 16 of 36
    items used when a hand read of the draft found 9."""
    tokens, low = checks._market_tokens(text), text.lower()
    used = set()
    for it in items:
        hit = False
        for specific, phrase in _fact_entities(it["fact"]):
            if len(phrase.split()) == 1:
                hit = specific[0] in tokens
            elif len(specific) >= 2:
                hit = phrase in low or all(w in tokens for w in specific)
            else:
                hit = phrase in low
            if hit:
                break
        if not hit:
            figures = {t for t in _strict_signature(it["fact"]) if any(c.isdigit() for c in t)}
            hit = sum(1 for t in figures if t in tokens or t in low) >= 2
        if hit:
            used.add(it["id"])
    return used


def _evidence_problems(sections: list[dict], items: list[dict], min_total: int) -> list[str]:
    """Deterministic backstop for the evidence-backing rule (see _evidence_used_ids for what counts as "used"). The
    prompt states every minimum; a prompt-only rule was ignored (5 of 33 items cited in testing).
    ARTICLE EVIDENCE items (group "article", C-numbered) are judged separately from the market ones: the market
    minimum only counts market items, while the claim list gets (a) a lower aggregate floor in the publisher block
    and (b) a per-claim requirement — every backed claim's backing must actually be worked into the publisher's
    own sections, or a retry instruction names it."""
    market_items = [it for it in items if it.get("group") != "article"]
    article_items = [it for it in items if it.get("group") == "article"]
    items = market_items
    if not items and not article_items:
        return []

    def used_in(text: str) -> set[str]:
        return _evidence_used_ids(text, items)

    def hint(candidates: list[dict], limit: int = 5) -> str:
        return "; ".join(f"({c['id']}) {c['fact'][:80]}" for c in candidates[:limit])

    body = [p for s in sections for p in s["paragraphs"] if _word_count(p)]
    used_all = used_in("\n".join(_sponsor_blank(p) for p in body))
    problems: list[str] = []
    if items and len(used_all) < min_total:
        unused = [it for it in items if it["id"] not in used_all]
        problems.append(
            f"only {len(used_all)} of the {len(items)} market evidence items are actually used in the article but at least "
            f"{min_total} must be — work more of them into the sections they belong to, with their specifics, for "
            f"example: {hint(unused)}"
        )
    for tag, group, need, label in _SECTION_EVIDENCE_NEEDS:
        group_items = [it for it in items if it["group"] == group]
        need = min(need, len(group_items))
        text = "\n".join(_sponsor_blank(p) for s in sections if tag in s["sop"] for p in s["paragraphs"])
        have = used_in(text)
        if len(have) < need:
            unused = [it for it in group_items if it["id"] not in have]
            problems.append(
                f"the {label} section uses {len(have)} evidence item(s) but needs at least {need}, each backing a "
                f"specific claim with its named source, date and concrete detail — use from: {hint(unused)}"
            )
    trend_paras = [
        p for s in sections if "trends" in s["sop"] for p in s["paragraphs"]
        if _word_count(p) >= 40
    ]
    bare = [p for p in trend_paras if not used_in(_sponsor_blank(p))]
    if bare and any(it["group"] == "trends" for it in items):
        problems.append(
            f"{len(bare)} market-trend paragraph(s) rest on no evidence item at all (a trend must be backed by a named "
            "company, source or figure, not asserted): " + " | ".join(f'"{" ".join(p.split()[:9])}..."' for p in bare[:3])
        )
    if article_items:
        publisher_sections = [s for s in sections if _section_class(s) == "P"]
        publisher_text = "\n".join(_sponsor_blank(p) for s in publisher_sections for p in s["paragraphs"])
        used_a = _evidence_used_ids(publisher_text, article_items)
        claim_ids = sorted({it["id"] for it in article_items})
        min_article = min(3, len(claim_ids))
        if len(used_a) < min_article:
            unused = [it for it in article_items if it["id"] not in used_a]
            problems.append(
                f"the publisher's own sections (the unheaded intro and the three untagged ones) use "
                f"{len(used_a)} of the ARTICLE EVIDENCE items backing the original article's claims, but at least "
                f"{min_article} must be worked into them there, each with its named source and what it establishes — "
                f"use from: {hint(unused)}"
            )
        for cid in claim_ids:
            if cid not in used_a:
                its = [it for it in article_items if it["id"] == cid]
                problems.append(
                    f"claim ({cid}) has backing ({its[0]['fact'][:110]} — source: {its[0].get('source') or 'web'}) but "
                    "no publisher section actually uses it — work this claim, corrected or qualified as the stance "
                    "requires, and its named source into the intro or one of the untagged publisher sections"
                )
    return problems


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


def _sponsor_intro(sections: list[dict]) -> dict | None:
    """The unheaded intro section (the first section the writer produces)."""
    return next((s for s in sections if not (s.get("heading") or "").strip()), None)


def _sponsor_location(sections: list[dict]) -> tuple[dict | None, int | None, str, str]:
    """(intro section, index of the intro paragraph holding the placeholder, text before it, text
    after it). The placeholder may be a paragraph of its own or sit inline in a writer's sentence."""
    intro = _sponsor_intro(sections)
    if intro is None:
        return None, None, "", ""
    for i, p in enumerate(intro["paragraphs"]):
        if prompts.SPONSOR_TOKEN in p:
            cut = p.index(prompts.SPONSOR_TOKEN)
            return intro, i, p[:cut], p[cut + len(prompts.SPONSOR_TOKEN):]
    return intro, None, "", ""


def _sponsor_location_problem(sections: list[dict]) -> str | None:
    """Missing or duplicated placeholder, or a placeholder outside the intro. This is exactly the
    set of failures the deterministic fallback in run_write repairs."""
    full = "\n".join(p for s in sections for p in s["paragraphs"])
    count = full.count(prompts.SPONSOR_TOKEN)
    if count != 1:
        return (
            f"the retained paragraph placeholder {prompts.SPONSOR_TOKEN} must appear exactly once in the whole "
            f"article, completely unchanged — it appears {count} time(s)"
        )
    intro, pi, _, _ = _sponsor_location(sections)
    if not intro or pi is None:
        return (
            f"the retained paragraph placeholder {prompts.SPONSOR_TOKEN} must appear inside the unheaded intro "
            "section (the first section), not outside it or in a headed section"
        )
    return None


def _starts_sentence(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped:
        return True
    return stripped[0].isupper() or stripped[0] in "\"'‘“([0123456789"


def _sponsor_problems(sections: list[dict], sponsor_text: str) -> list[str]:
    """Mode-aware placement rules for the retained paragraph's placeholder, phrased as retry
    instructions. The placeholder must appear exactly once, inside the intro. A BARE PHRASE sponsor
    has no sentence of its own, so it must sit inside one of the writer's sentences with real
    writing on both sides; PROSE may be its own paragraph or sit between the writer's sentences,
    but never mid-sentence. The substituted text itself is never to be written by the model."""
    location_problem = _sponsor_location_problem(sections)
    if location_problem:
        return [location_problem]
    intro, pi, before, after = _sponsor_location(sections)
    mode = sponsor_mode(sponsor_text)
    full = "\n".join(p for s in sections for p in s["paragraphs"])
    problems: list[str] = []
    if mode == "phrase":
        before_words, after_words = _word_count(before), _word_count(after)
        if before_words < 12 or after_words < 6:
            problems.append(
                f'the retained text is a bare linked phrase ("{sponsor_text}") with no sentence of its own, so the '
                f"placeholder {prompts.SPONSOR_TOKEN} must sit INSIDE one of your own sentences in intro paragraph 2 — "
                f"write at least 12 of your own words before it (currently {before_words}) and at least 6 after it "
                f"(currently {after_words}) in that same paragraph, as a noun phrase the sentence grammatically needs. "
                "Never put it alone on its own line, at the very start of the paragraph, or at its very end"
            )
        normalised = " ".join(sponsor_text.split()).lower()
        if normalised and normalised in " ".join(full.split()).lower():
            problems.append(
                f'the retained phrase "{sponsor_text}" also appears written out in the article outside the placeholder — '
                "never repeat or paraphrase it yourself anywhere; it is inserted automatically at the placeholder, "
                "exactly as written"
            )
    else:
        embedded = intro["paragraphs"][pi].strip() != prompts.SPONSOR_TOKEN
        before_ok = not before.strip() or bool(re.search(r"[.!?][\"'”’)\]]*\s*$", before))
        if embedded and not (before_ok and _starts_sentence(after)):
            problems.append(
                f"the retained paragraph placeholder {prompts.SPONSOR_TOKEN} sits mid-sentence — when it is not its "
                "own paragraph it must sit BETWEEN your own sentences: the text before it must end with . ! ? (or the "
                "placeholder must start the paragraph) and the text after it must start a new complete sentence"
            )
    return problems


def _force_sponsor_placeholder(sections: list[dict]) -> list[dict]:
    """Last-resort repair used when a draft still has no valid placement after the retry: strip any
    placeholder occurrences and insert one as the intro's second paragraph (the old behaviour), so
    the paying guest's backlink can never be lost."""
    for s in sections:
        cleaned = []
        for p in s.get("paragraphs") or []:
            if prompts.SPONSOR_TOKEN in p:
                p = re.sub(r"[ \t]{2,}", " ", p.replace(prompts.SPONSOR_TOKEN, " ")).strip()
            if p:
                cleaned.append(p)
        s["paragraphs"] = cleaned
    if not sections:
        sections.append({"heading": "", "sop": [], "paragraphs": [prompts.SPONSOR_TOKEN]})
        return sections
    intro = _sponsor_intro(sections) or sections[0]
    intro["paragraphs"].insert(min(1, len(intro["paragraphs"])), prompts.SPONSOR_TOKEN)
    return sections


_INTRO_TOTAL_WORDS = 150  # intro target INCLUDING the retained paragraph's real words — shrunk from 210 along with
# the rest of the publisher block when its share of the post dropped to ~20% (see PUBLISHER_USER's WORD TARGETS)


def _intro_budget(mode: str, sponsor_words: int) -> str:
    """Per-paragraph intro word targets computed from the retained text's real length, so the intro always adds up
    to _INTRO_TOTAL_WORDS: a static "45-60 words each" was wrong for a 4-word phrase (intro came out ~105 words
    against a ~170 budget) and for a long prose paragraph alike."""
    own = max(80, _INTRO_TOTAL_WORDS - sponsor_words)
    if mode == "phrase":
        p2 = min(90, max(50, own - 90))
        total = 45 + p2 + 45 + sponsor_words
        return (
            f"Paragraph 1 (lead-in): 2-3 sentences, about 45 words. Paragraph 2 (your own paragraph that holds the retained "
            f"phrase inside one of its sentences): 3-4 sentences, about {p2} words of your own. Paragraph 3 (pivot): 2-3 "
            f"sentences, about 45 words. The intro comes to about {total} words once the retained phrase's own "
            f"{sponsor_words} words are counted."
        )
    each = min(55, max(30, round(own / 2)))
    total = 2 * each + sponsor_words
    return (
        f"Paragraph 1 (lead-in): 2-3 sentences, about {each} words. Paragraph 2: the retained paragraph's slot "
        f"({sponsor_words} real words, which are not counted as yours; if you embed it inside a paragraph of your own, that "
        f"paragraph's own words add to the intro). Paragraph 3 (pivot): 2-3 sentences, about {each} words. The intro comes to "
        f"about {total} words once the retained paragraph is counted."
    )


def _sponsor_rule(mode: str, sponsor_text: str) -> str:
    """The short, mode-specific placement instruction injected into prompts.WRITE_USER. Every
    illustrative example is generalised (no real market or product names)."""
    if mode == "phrase":
        return (
            f'The retained text is a BARE LINKED PHRASE, not a paragraph: "{sponsor_text}". Write intro paragraph 2 '
            f"as a paragraph of your own in which the placeholder {prompts.SPONSOR_TOKEN} appears exactly ONCE, "
            "INSIDE one of your sentences, as a noun phrase that sentence grammatically needs, with your own words "
            "before it and your own words after it in that same paragraph (illustrative shape only, not real "
            'content: "...and that review starts with [[SPONSOR_PARAGRAPH]] a team already runs, because ..."). '
            "The phrase is inserted exactly as written, including its capitalisation, so choose a sentence shape in "
            "which it reads naturally. Never repeat or paraphrase the phrase yourself anywhere else in the article."
        )
    return (
        "The retained text is PROSE (one or more whole sentences). Place the placeholder "
        f"{prompts.SPONSOR_TOKEN} either as its own paragraph (paragraph 2 of the intro, the placeholder alone and "
        "nothing else) or BETWEEN your own sentences inside intro paragraph 2 — never mid-sentence. If you embed it, "
        "write a complete sentence before it and start a new complete sentence after it, so the substituted prose "
        'reads as its own sentences in the flow (illustrative shape only: "Opening sentence. '
        '[[SPONSOR_PARAGRAPH]] Closing sentence.").'
    )


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


def _section_class(section: dict) -> str:
    """P (publisher) for the unheaded intro and every section with an empty `sop` list; M (market) for
    every section carrying a SOP tag — the conclusion counts as market."""
    return "M" if section.get("sop") else "P"


def _layout_share(sections: list[dict], sponsor_text: str = "") -> tuple[int, int]:
    """(publisher words, total words), counting the sponsor placeholder as the retained paragraph's
    real words because those real words belong to the publisher's side of the article."""
    sponsor_words = len(_WORD_RE.findall(sponsor_text)) if sponsor_text else 0
    publisher = total = 0
    for s in sections:
        for p in s.get("paragraphs") or []:
            words = _word_count(p) + (sponsor_words if prompts.SPONSOR_TOKEN in p else 0)
            total += words
            if _section_class(s) == "P":
                publisher += words
    return publisher, total


def _layout_problems(sections: list[dict], sponsor_text: str = "") -> list[str]:
    """Deterministic backstop for the layout the WRITE prompt asks for: the unheaded intro, then the
    publisher's ONE section, then ONE transition (report_context, as the bridge) into the market
    block, with the conclusion last. The prompt-only version of this rule produced badly interleaved
    drafts (about five subject changes), so the hard parts are enforced here and fed back as log-only
    feedback (no retry is run). Publisher share target (~20%) and market block size (~900-1,100 words)
    match PUBLISHER_USER / MARKET_USER's WORD TARGETS and checks.run_checks's soft layout checks —
    keep all three in step if the budget changes again."""
    if not sections:
        return []
    classes = [_section_class(s) for s in sections]
    switches = sum(1 for a, b in zip(classes, classes[1:]) if a != b)
    order = " | ".join(
        f"{(s.get('heading') or '').strip() or 'intro'} {c}" for s, c in zip(sections, classes)
    )
    problems: list[str] = []
    if switches > 2:
        problems.append(
            f"the article changes subject {switches} times in this order: {order} — it must change subject only once, "
            "from ONE publisher block into ONE market block. Keep the intro and the publisher's one section consecutive "
            "(no market section between them), then put every market section, starting with report_context, after it"
        )
    publisher_sections = [s for s, c in zip(sections, classes) if c == "P" and (s.get("heading") or "").strip()]
    if len(publisher_sections) != 1:
        problems.append(
            f"the publisher block has {len(publisher_sections)} headed section(s) but must have exactly 1, covering the "
            "article's 2-3 most important points"
        )
    first_market = next((s for s in sections if s.get("sop")), None)
    if first_market is not None and "report_context" not in first_market["sop"]:
        problems.append(
            f'the first market section is "{first_market.get("heading") or "untitled"}" but report_context must be '
            "the first market section and act as the bridge: it opens by turning from the publisher's subject to the "
            "market that subject lives in, then gives that market's size and direction"
        )
    publisher, total = _layout_share(sections, sponsor_text)
    market = total - publisher
    if total:
        share = publisher / total * 100
        if share < 10:
            add = max(0, round(total * 0.20) - publisher)
            problems.append(
                f"publisher content is only {share:.0f}% of the article ({publisher} of {total} words) but the soft "
                f"target is about 20% (240-300 words including the retained paragraph). Add about {add} words to the "
                "intro and the one publisher section, developing their own points rather than padding"
            )
        elif share > 30:
            cut = max(0, publisher - round(total * 0.20))
            problems.append(
                f"publisher content is {share:.0f}% of the article ({publisher} of {total} words) but the soft target "
                f"is about 20% — cut about {cut} words from the intro and the one publisher section, mostly restatement"
            )
        if market > 1150:
            cut = market - 1000
            problems.append(
                f"the market block is {market} words but the soft target is about 900-1,100 (never above 1,150) — cut "
                f"about {cut} words, mostly whole sentences from the longest market paragraphs"
            )
    return problems


def _write_problems(
    sections: list[dict], report: reports.Report, original_body: str, report_text: str, sponsor_text: str = "",
    secondary_kw: list[tuple[str, str]] = (), evidence_items: list[dict] = (), min_evidence: int = 0,
    questions: list[dict] = (),
) -> list[str]:
    """Hard-requirement violations in a CALL 3 draft, phrased as instructions for a retry."""
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    full = "\n".join(paragraphs)
    problems = []
    problems.extend(_layout_problems(sections, sponsor_text))
    if LONG_PARAGRAPH_CHECK_ENABLED:
        problems.extend(long_paragraph_problems(sections))
    problems.extend(question_heading_problems(sections, questions))
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
    problems.extend(_sponsor_problems(sections, sponsor_text))
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
    opinions = count_cmi_opinions(paragraphs)
    if not CMI_OPINIONS_MIN <= opinions <= CMI_OPINIONS_MAX:
        problems.append(
            f"it has {opinions} sentence(s) giving a labelled CMI analyst opinion; the post carries {CMI_OPINIONS_MIN} to "
            f"{CMI_OPINIONS_MAX} (\"A CMI analyst expects…\", placed in trends, subsegment, key_players, country_us or conclusion)"
        )
    stale = _stale_event_mentions(sections)
    if stale:
        problems.append(
            f"it cites events dated more than 12 months ago ({'; '.join(stale)}) — leave those events out entirely or "
            "describe the point without presenting an old event as recent"
        )
    words = sum(_word_count(p) for p in paragraphs)
    # Soft target is 1,200-1,500; only a clear miss is logged (no retry is run). The floor sits a little under the
    # target for the same reason the old 1,400-1,700 band did: the evidence-backing rule needs room to develop what
    # it cites, so a draft right at the floor with no space for that is already a real problem worth flagging.
    if words > 1700 or words < 1100:
        longest = sorted(sections, key=lambda s: -sum(len(p.split()) for p in s["paragraphs"]))[:4]
        problems.append(
            f"the length is {words} words but the target is 1,200-1,500"
            + (
                " — tighten every paragraph that is over its stated range, especially in: "
                + "; ".join(f'"{s["heading"] or "intro"}" ({sum(len(p.split()) for p in s["paragraphs"])} words)' for s in longest)
                if words > 1700 else " — add the missing substance to the paragraphs that are under their stated ranges"
            )
        )
    problems.extend(_evidence_problems(sections, list(evidence_items), min_evidence))
    # Only the CMI report is a copy risk (CMI repeating its own text); the publisher's article is edited, so its wording
    # may be reused.
    sources = [("CMI report", report_text)] if report_text else []
    pct, examples = _verbatim_overlap(paragraphs, sources, report_name=report.name) if sources else (0.0, [])
    if pct > 5.0:
        problems.append(
            f"{pct:.0f}% of the words are copied in runs of 10 or more words from the original article or the CMI "
            "report — rewrite the affected paragraphs from scratch with genuinely different sentence structure "
            "(not synonym swaps); the affected paragraphs include: " + " | ".join(examples)
        )
    return problems


def _split_evidence(evidence: str) -> tuple[str, str]:
    """(article part, market part) of the evidence block: ARTICLE EVIDENCE plus the claims list go to the publisher
    call, MARKET EVIDENCE to the market call. Blocks from before claim backing existed (no ARTICLE/CLAIMS header)
    are all market evidence."""
    lines = (evidence or "").splitlines()
    has_article = any(l.startswith("### ARTICLE EVIDENCE") or l.startswith("### CLAIMS FROM") for l in lines)
    if not has_article:
        return "(none: no claims were extracted from the original article)", evidence or "(none)"
    idx = next((i for i, l in enumerate(lines) if l.startswith("### MARKET EVIDENCE")), len(lines))
    article = "\n".join(lines[:idx]).strip() or "(none: no claims were extracted from the original article)"
    market = "\n".join(lines[idx:]).strip() or "(none)"
    return article, market


def _render_part1(sections: list[dict], sponsor: Paragraph) -> str:
    """The publisher block as the market call reads it: headings and paragraphs, the retained paragraph's REAL text
    substituted for its placeholder and the CMI link shown as the plain market name."""
    lines: list[str] = []
    for s in sections:
        if (s.get("heading") or "").strip():
            lines.append(f"## {s['heading'].strip()}")
        for p in s["paragraphs"]:
            lines.append(re.sub(prompts.CMI_LINK_PATTERN, r"\1", p.replace(prompts.SPONSOR_TOKEN, sponsor.text)))
        lines.append("")
    return "\n".join(lines).strip()


def _split_fused_paragraphs(text: str) -> list[str]:
    """A paragraph string may hold real line breaks only for a lead-in followed by "- " bullet lines (trends,
    segmentation). Any other line break is the model putting two paragraphs into one array entry (seen: both sub-segment
    analyses in one string, so the draft had one paragraph where the SOP needs two). Split there; bullet lines stay
    attached to the text above them. Nothing is rewritten, only the array shape is restored."""
    out: list[str] = []
    for line in str(text).split("\n"):
        if not line.strip():
            continue
        if out and line.lstrip().startswith(("- ", "• ")):
            out[-1] += "\n" + line.strip()
        else:
            out.append(line.strip())
    return out


def _drop_repeated_tags(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """The market block has exactly one section per SOP tag. A model sometimes writes the whole list twice, or adds a
    second conclusion; keep the first section of each tag, in order, and log what was dropped. Nothing is rewritten."""
    seen: set[str] = set()
    kept = []
    for s in sections:
        tags = set(s["sop"])
        if tags and tags <= seen:
            logger.warning(f"[write] dropped a repeated {sorted(tags)} section ({s['heading']!r}): the market block has one section per tag")
            continue
        seen |= tags
        kept.append(s)
    return kept


def _paragraph_text(entry) -> str:
    """A "paragraphs" entry is a string, or an object {"paragraph": "..."} when the prompt asks the writer to name what
    each paragraph is about (the sub-segment analyses: segment, sub-segment, paragraph)."""
    if isinstance(entry, dict):
        return str(entry.get("paragraph") or entry.get("text") or "")
    return str(entry)


def _clean_sections(raw_sections, force_untagged: bool = False) -> list[dict]:
    out = []
    for s in raw_sections or []:
        if not isinstance(s, dict):
            continue
        paragraphs = [
            _plain(q) for p in s.get("paragraphs") or [] if _paragraph_text(p).strip()
            for q in _split_fused_paragraphs(_paragraph_text(p))
        ]
        if not paragraphs:  # a section with no text is dropped
            continue
        out.append({
            "heading": str(s.get("heading") or ""),
            "sop": [] if force_untagged else [str(t) for t in s.get("sop") or [] if str(t) in _VALID_SOP_TAGS],
            "paragraphs": paragraphs,
        })
    return out


def run_write(
    article: SourceArticle, orient: dict, sponsor: Paragraph,
    report: reports.Report, evidence: str, logger: logging.Logger,
    longtails: list[dict] = (),
) -> tuple[dict, str, list[dict], list[dict]]:
    """CALL 3: write the article in two LLM calls, each with one job. 3A edits the publisher's own article into an
    intro plus three sections (claims backed from ARTICLE EVIDENCE) and is told it is Part 1, so it does not conclude.
    3B reads that finished text and writes the market block, the bridge from it and the title. A single call that
    had to do both sometimes stopped after the publisher block (about 1 draft in 5 in testing); with one job per call
    that cannot happen. `longtails` (from run_longtail_keywords, empty when generation failed) is a small pool of
    commercial-intent search phrases 3B may integrate up to three of. There is no quality retry: problems the
    deterministic checks find are logged, not re-asked.
    Returns (raw_json, title, sections, evidence_citations)."""
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
    longtail_lines = _format_longtail_lines(list(longtails))
    logger.info(f"[seo] long-tail candidates: {list(longtails) or '(none)'}")
    sponsor_words = len(re.findall(r"\b\w[\w'-]*\b", sponsor.text))
    mode = sponsor_mode(sponsor.text)
    logger.info(f"[write] sponsor mode: {mode} ({sponsor_words} word(s): {sponsor.text[:80]!r})")
    evidence_items = _parse_evidence(evidence)
    question_assignments = _parse_question_assignments(evidence)
    article_ids = {it["id"] for it in evidence_items if it.get("group") == "article"}
    market_count = sum(1 for it in evidence_items if it.get("group") != "article")
    min_evidence = _min_evidence_total(market_count)
    # Capped at 2, not 3: the publisher block is now a single ~110-140 word section (see
    # PUBLISHER_USER), which has room for at most a couple of backed claims alongside its own points.
    article_min = min(2, len(article_ids)) if article_ids else 0
    logger.info(
        f"[write] evidence: {market_count} market item(s), at least {min_evidence} must be used; "
        f"{len(article_ids)} claim(s) with backing, publisher block must use at least {article_min} of them"
    )
    article_evidence, market_evidence = _split_evidence(evidence)
    today = date.today().isoformat()

    # ---- 3A: the publisher block (intro + three sections)
    backed_ids = re.findall(r"^\* (C\d+) \[[^\]]*\].*— BACKED", article_evidence, re.MULTILINE)
    pub_prompt = prompts.PUBLISHER_USER.format(
        backed_claims=", ".join(backed_ids) if backed_ids else "none this time",
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        original_body=body_text[:_BODY_CHARS],
        sponsor=sponsor.text,
        report_name=report.name,
        evidence=article_evidence,
        intro_budget=_intro_budget(mode, sponsor_words),
        sponsor_words=sponsor_words,
        sponsor_token=prompts.SPONSOR_TOKEN,
        sponsor_rule=_sponsor_rule(mode, sponsor.text),
        article_min=article_min,
        today=today,
    )
    written_a = llm.chat_json(
        prompts.PUBLISHER_SYSTEM, pub_prompt, temperature=0.3, call_name="write-publisher", logger=logger, max_tokens=6000,
    )
    pub_sections = _clean_sections(written_a.get("sections"), force_untagged=True)
    if not pub_sections:
        raise PipelineError("Call 3A returned no publisher sections")
    pub_words = sum(_word_count(p) for s in pub_sections for p in s["paragraphs"]) + sponsor_words
    logger.info(
        f"[write] 3A done: {len(pub_sections)} section(s), about {pub_words} words including the retained paragraph"
    )

    # ---- 3B: the market block, the bridge from the publisher text, and the title
    market_prompt = prompts.MARKET_USER.format(
        sop=prompts.SOP,
        title=article.title,
        topic_summary=orient.get("topic_summary", ""),
        bridge_angle=orient.get("bridge_angle", ""),
        report_name=report.name,
        part1=_render_part1(pub_sections, sponsor),
        part1_words=pub_words,
        today=today,
        report_text=report_text,
        key_players=", ".join(reports.key_players(report)) or "(none listed: use the companies the report text names)",
        cmi_keywords=", ".join(prompts.CMI_KEYWORDS),
        primary_keyword=primary_kw,
        secondary_keywords=secondary_kw_lines,
        longtail_keywords=longtail_lines,
        evidence=market_evidence,
        min_evidence=min_evidence,
    )
    written_b = llm.chat_json(
        prompts.MARKET_SYSTEM, market_prompt, temperature=0.3, call_name="write-market", logger=logger, max_tokens=6000,
    )
    plan = [e for e in written_b.get("long_tail_plan") or [] if isinstance(e, dict)]
    logger.info("[long-tail] the writer's plan: " + (
        "; ".join(f'{e.get("phrase")!r} -> {e.get("section")} (evidence {e.get("evidence")}) stub: {e.get("stub")!r}' for e in plan) or "(none)"
    ))
    market_sections = _drop_repeated_tags(_clean_sections(written_b.get("sections")), logger)
    if not market_sections:
        raise PipelineError("Call 3B returned no market sections")
    title = _plain(str(written_b.get("title") or ""))
    sections = pub_sections + market_sections
    written = {"title": title, "sections": sections,
               "evidence_citations": list(written_a.get("evidence_citations") or []) + list(written_b.get("evidence_citations") or [])}

    problems = _write_problems(
        sections, report, article.body, report_text, sponsor.text, secondary_kw, evidence_items, min_evidence,
        questions=question_assignments,
    )
    if problems:
        logger.warning(f"[write] draft problems found by the deterministic checks (no retry is run): {'; '.join(problems)}")
    else:
        logger.info("[write] the draft passed every deterministic write check")

    # Deterministic last resort: whatever the model did, the paying guest's backlink must never be
    # lost. If the placeholder is missing, duplicated, or outside the intro, insert it as its own
    # paragraph at intro position 2 (the pre-inline-token behaviour) and carry on.
    location_problem = _sponsor_location_problem(sections)
    if location_problem:
        logger.warning(f"[write] {location_problem} — inserting the placeholder as intro paragraph 2 so the link is not lost")
        sections = _force_sponsor_placeholder(sections)

    # Only trust a self-reported citation if its paragraph text is a VERBATIM match against a real
    # paragraph in the draft; an unmatched citation is dropped, not corrected.
    real_paragraphs = {p for s in sections for p in s["paragraphs"]}
    evidence_citations = []
    for c in written.get("evidence_citations") or []:
        if not isinstance(c, dict):
            continue
        para = _plain(str(c.get("paragraph") or ""))
        eid = str(c.get("id") or "").strip().upper()
        if eid and para in real_paragraphs:
            evidence_citations.append({"id": eid, "paragraph": para})
        else:
            logger.info(f"[write] dropped unverifiable evidence citation: {c}")

    return written, title, sections, evidence_citations


_PUBLISHER_FAQ_COUNT = 2  # one or two questions about the guest article's own subject, after the market ones
_FAQ_COUNT = 5  # market questions (CMI's own site publishes 7 in all for the house-style example this prompt was built from);
# fewer can come back when a category (e.g. CAGR, a single dominant region) has no grounded figure —
# see FAQ_USER's "drop rather than invent" rule.


def _publisher_text(sections: list[dict], sponsor_text: str) -> str:
    """The publisher's own content as it stands in the finished article: every UNTAGGED section (the intro and the
    publisher sections carry no sop tag; the market sections all do), with the retained sponsor paragraph put back in
    place of its placeholder and the CMI-link placeholders reduced to their anchor words."""
    chunks = []
    for s in sections:
        if s.get("sop"):
            continue
        paras = [
            re.sub(r"\[\[CMI_LINK:(.*?)\]\]", r"\1", str(p).replace(prompts.SPONSOR_TOKEN, sponsor_text))
            for p in s.get("paragraphs", [])
        ]
        body = "\n".join(p for p in paras if p.strip())
        if body:
            chunks.append(f"{s.get('heading') or '(intro)'}\n{body}")
    return "\n\n".join(chunks)


def run_faq(
    market_name: str, report: reports.Report, title: str, sections: list[dict], logger: logging.Logger,
    sponsor_text: str = "",
) -> list[dict]:
    """A CALL run AFTER the article itself is written (not parallelized with WRITE): house-style FAQs
    (growth estimate, key players, restraints, drivers, CAGR, dominant region, segments —
    the same categories CMI's own site already publishes for other markets) are each answered ONLY
    from the CMI report text and the article's own finished content, so nothing in the FAQ can
    contradict or outrun what the article itself says. Best-effort: a generation failure just means
    no FAQ block, logged, never fatal — the FAQ is an addition to the article, not a requirement."""
    section(logger, "CALL: FAQ")
    report_text = _report_text(report)
    article_text = _render_draft_for_revise(title, sections)
    try:
        result = llm.chat_json(prompts.FAQ_SYSTEM, prompts.FAQ_USER.format(
            market_name=market_name,
            report_text=report_text,
            article_text=article_text[:_BODY_CHARS],
            faq_count=_FAQ_COUNT,
            publisher_faq_count=_PUBLISHER_FAQ_COUNT,
            publisher_text=_publisher_text(sections, sponsor_text)[:_BODY_CHARS] or "(none)",
            used_questions="\n".join(f"- {s['heading']}" for s in sections if str(s.get("heading") or "").strip().endswith("?"))
            or "(none — no section heading is a question)",
            page_faq="\n".join(f"- {qa['question']} -> {qa['answer']}" for qa in report.page_faq)
            or "(none — the report page has no FAQ)",
        ), call_name="faq", logger=logger)
    except llm.LLMError:
        logger.warning("[faq] generation call failed; the draft will carry no FAQ block", exc_info=True)
        return []
    market_faq = _drop_page_faq_repeats(_normalize_faq(result.get("faq")), report.page_faq, market_name, logger)
    # Publisher FAQ: questions about the guest article's own subject, answered from its content. A definition or a
    # how-to is fine here (they are the point), and the report page's FAQ cannot overlap them, so neither market-only
    # filter applies. They follow the market entries.
    publisher_faq = _normalize_faq(result.get("publisher_faq"), limit=_PUBLISHER_FAQ_COUNT, allow_definitions=True)
    logger.info(f"[faq] generated {len(market_faq)} market + {len(publisher_faq)} publisher FAQ entries")
    return market_faq + publisher_faq


_FAQ_REPEAT_STOPWORDS = frozenset({
    "the", "and", "for", "are", "was", "were", "with", "that", "this", "from", "which", "what", "who", "how", "does",
    "will", "its", "their", "global", "market", "across", "world", "during", "into", "has", "have", "been", "being",
})


def _faq_tokens(text: str, market_name: str) -> set[str]:
    skip = set(re.findall(r"[a-z0-9]+", market_name.lower())) | _FAQ_REPEAT_STOPWORDS
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in skip}


def _drop_page_faq_repeats(faq: list[dict], page_faq: list[dict], market_name: str, logger: logging.Logger) -> list[dict]:
    """Deterministic backstop for FAQ_USER's DIFFER FROM THE REPORT PAGE rule: drops an entry whose question, or whose
    answer, is essentially one the report page's own FAQ already carries (overlap coefficient of content words >= 0.7
    for a question, >= 0.6 for an answer). The prompt does the semantic work; this only catches near-verbatim
    repeats, such as the same restraints sentence or the same company list."""
    if not page_faq:
        return faq
    kept = []
    for qa in faq:
        q_tok, a_tok = _faq_tokens(qa["question"], market_name), _faq_tokens(qa["answer"], market_name)
        repeat = None
        for page in page_faq:
            pq, pa = _faq_tokens(page["question"], market_name), _faq_tokens(page["answer"], market_name)
            if q_tok and pq and len(q_tok & pq) / min(len(q_tok), len(pq)) >= 0.7:
                repeat = page["question"]
            elif a_tok and pa and len(a_tok & pa) / min(len(a_tok), len(pa)) >= 0.6:
                repeat = page["question"]
            if repeat:
                break
        if repeat:
            logger.info(f"[faq] dropped {qa['question']!r}: it repeats the report page's FAQ entry {repeat!r}")
            continue
        kept.append(qa)
    return kept


_FAQ_MARKET_ANGLE_WORDS = frozenset({
    "growth", "growing", "driving", "drivers", "hampering", "restraining", "restraints", "cagr", "size", "share",
    "factors", "trends", "players", "forecast", "estimate", "estimates", "expected", "projected", "rate", "value",
    "valued", "segment", "segments", "region", "regions", "challenges", "opportunities", "leading", "dominating",
})


def _is_definition_question(question: str) -> bool:
    """A short "What is/are/does <the market or its subject>?" question with no market angle in it
    (growth, drivers, share, ...) — a definition, which the FAQ must not carry (FAQ_USER rule 4)."""
    words = re.findall(r"[a-z0-9']+", question.lower())
    return (
        len(words) <= 9 and words[:2] in (["what", "is"], ["what", "are"], ["what", "does"])
        and not any(w in _FAQ_MARKET_ANGLE_WORDS for w in words)
    )


def _normalize_faq(raw: list | None, limit: int | None = None, allow_definitions: bool = False) -> list[dict]:
    """Sanitise the FAQ call's raw output: dicts only, both fields non-empty, duplicate questions
    (by normalised text) collapsed to the first occurrence, capped at _FAQ_COUNT. Pulled out of
    run_faq so the sanitisation logic is testable without an LLM call."""
    faq: list[dict] = []
    seen: set[str] = set()
    for qa in raw or []:
        if not isinstance(qa, dict):
            continue
        q = re.sub(r"\s+", " ", str(qa.get("question") or "")).strip()
        a = re.sub(r"\s+", " ", str(qa.get("answer") or "")).strip()
        if not q or not a:
            continue
        if not allow_definitions and _is_definition_question(q):
            continue
        key = re.sub(r"[^a-z0-9 ]", "", q.lower()).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        faq.append({"question": q, "answer": a})
    return faq[:limit if limit is not None else _FAQ_COUNT]


def faq_sections(faq: list[dict]) -> list[dict]:
    """FAQ Q&A pairs rendered as extra docx/display sections, tagged sop=["faq"] — kept OUT of the
    `sections` list every length/word-count/SOP check runs against, so the FAQ block sits outside
    the article's word limit entirely, by construction, not by a special case inside those checks."""
    if not faq:
        return []
    out = [{"heading": "Frequently Asked Questions", "sop": ["faq"], "paragraphs": []}]
    for qa in faq:
        q, a = str(qa.get("question") or "").strip(), str(qa.get("answer") or "").strip()
        if q and a:
            out.append({"heading": q, "sop": ["faq"], "paragraphs": [a]})
    return out


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
            if not _word_count(p):
                continue
            low = _plain(_sponsor_blank(p)).lower()
            words += _word_count(p)
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
            if not _word_count(p):
                continue
            for m in _FIGURE.finditer(_sponsor_blank(p)):
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
            if not _word_count(p) or si == 0:
                continue
            low = _plain(_sponsor_blank(p)).lower()
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
    # An inline placeholder stands for the retained paragraph's real words at that point in the
    # sentence, so the word offset must count it as sponsor_words, not as the one token word.
    sponsor_extra = max(sponsor_words - 1, 0)

    def _widened(text: str) -> int:
        return len(text.split()) + sponsor_extra * text.count(prompts.SPONSOR_TOKEN)

    paras = [
        [re.sub(prompts.CMI_LINK_PATTERN, lambda m: m.group(1), p) for p in s["paragraphs"]]
        for s in sections
    ]

    target = None  # (section index, paragraph index, match) of the first mention, if early enough
    words_before = 0
    found_any = False
    for si, plist in enumerate(paras):
        for pi, p in enumerate(plist):
            if not _word_count(p):
                words_before += sponsor_words
                continue
            m = name_re.search(p)
            if m:
                found_any = True
                if words_before + _widened(p[:m.start()]) <= MAX_LINK_WORD_OFFSET:
                    target = (si, pi, m)
                break
            words_before += _widened(p)
        if found_any:
            break

    how = ""
    if target is not None:
        si, pi, m = target
        paras[si][pi] = paras[si][pi][:m.start()] + token + paras[si][pi][m.end():]
        how = "linked the first mention"
    else:
        first = next((i for i, p in enumerate(paras[0]) if _word_count(p)), None)
        if first is None:
            return title, sections, evidence_citations
        sentence = f"Coherent Market Insights follows this space in its {token} research."
        p = paras[0][first]
        paras[0][first] = (
            f"{sentence} {p}" if _widened(p) > MAX_LINK_WORD_OFFSET - 20 else f"{p.rstrip()} {sentence}"
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


_DISABLE_CALL_4 = True


def run_revise(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, logger: logging.Logger, evidence: str = "", sponsor_text: str = "",
    longtails: list[dict] = (),
) -> tuple[str, list[dict], list[dict]]:
    """CALL 4 (see _revise_pass), always followed by the deterministic report-link enforcement so
    the link placement holds whichever way the revise pass ended (accepted, rejected, or failed)."""
    if _DISABLE_CALL_4:
        logger.warning("[revise] CALL 4 disabled (_DISABLE_CALL_4) — shipping the unrevised CALL 3 draft")
        return title, sections, evidence_citations
    title, sections, evidence_citations = _revise_pass(
        title, sections, evidence_citations, report, logger, evidence, sponsor_text, longtails
    )
    return enforce_report_link(title, sections, evidence_citations, report, sponsor_text, logger)


def _revise_pass(
    title: str, sections: list[dict], evidence_citations: list[dict],
    report: reports.Report, logger: logging.Logger, evidence: str = "", sponsor_text: str = "",
    longtails: list[dict] = (),
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
        longtail_keywords=_format_longtail_lines(list(longtails)),
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
        sponsor_ok = full.count(prompts.SPONSOR_TOKEN) == 1 and _sponsor_location_problem(new_sections) is None
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
    faq: list[dict] = (),
    longtails: list[dict] = (),
    plan: dict | None = None, plan_inputs=None,
) -> RevampResult:
    """SOP checks + write .docx. Returns the finished RevampResult. `faq` (from run_faq) is never
    passed to a length/word-count/SOP check whose body is the article itself — the market-first
    chain's run_checks_v2 DOES read the FAQ (plan §3.8: FAQ figures must trace), but no article
    length or share check ever counts FAQ words (they sit outside the article entirely). It is
    appended to the .docx output after the article's own sections (see faq_sections)."""
    section(logger, "SOP CHECKS")
    report_text = _report_text(report)
    primary_kw, secondary_kw = _seo_keyword_plan(report.name)
    # The market-first chain hands the finalized draft its validated plan and planner inputs; seen
    # here, those pick checks.run_checks_v2 (the old chain's draft never has them). The v2 checks
    # keep the checks §3.9 keeps and add the plan/structure/block ones; nothing below branches further.
    if plan is not None and plan_inputs is not None:
        check_results = checks.run_checks_v2(
            title, sections, plan, plan_inputs, faq=faq, sponsor_brand_terms=_brand_terms(sponsor),
            sponsor_text=sponsor.text, report_text=report_text, report_name=report.name,
            selection=selection, hits=hits,
        )
    else:
        check_results = run_checks(
            title, sections, f"{report_text}\n{evidence}\n{sponsor.text}", _brand_terms(sponsor),
            sponsor.text, article.body, report.name, selection, hits, report_text=report_text,
            primary_keyword=primary_kw, secondary_keywords=[kw for kw, _ in secondary_kw],
            original_title=article.title, questions=_parse_question_assignments(evidence),
            longtails=[t["keyword"] for t in longtails],
        )
    for c in check_results:
        logger.info(("PASS " if c.passed else "FAIL ") + c.name + (f" - {c.detail}" if c.detail else ""))

    result = RevampResult(
        title, sections, sponsor, report, candidates, orient, selection, hits, check_results,
        evidence_citations=list(evidence_citations), faq=list(faq),
    )
    output_path = output_path_for(original_filename)
    write_docx(output_path, title, sections + faq_sections(result.faq), result.segments)
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

    # Subheading questions are harvested AND picked now, right after the market is confirmed (same
    # timing as the stepwise API's Step 2) — fixed before the evidence search below starts, so the
    # question-evidence search for them can run concurrently with it instead of only after.
    evidence_t0 = time.perf_counter()
    progress("Drafting subheading questions for this market")
    questions = run_draft_questions(report.name, orient, report, logger).get("subheading_questions") or []

    # Long-tail commercial-keyword candidates: resolved at the same point as the stepwise API's
    # Step 2 (the monolith always confirms the top candidate, so this nearly always reuses the
    # orient candidates with no generation call). Best-effort, like them.
    progress("Resolving long-tail keyword candidates")
    longtails = resolve_longtails(report.name, report, orient, logger)

    # Report claims are extracted now (the report is known) and their evidence search is started
    # before the market search below — the same ordering as the stepwise API's Step 2.
    progress("Extracting the report's own claims to back")
    report_claims = run_report_claims(report.name, report, logger)

    progress("Searching the web for supporting evidence")
    claims = orient_claims(orient)
    with ThreadPoolExecutor(3) as pool:
        # Claim backing, question backing and report-claim backing each run in their own thread,
        # concurrently with the market stage: different sources (general web explainers vs news
        # feeds), all four stages are network-bound, so the run waits once for whichever finishes
        # last, not for their sum.
        claim_future = pool.submit(run_claim_evidence, claims, logger, progress) if claims else None
        question_future = pool.submit(run_question_evidence, questions, logger, progress)
        report_claim_future = (
            pool.submit(run_report_claim_evidence, report_claims, logger, progress) if report_claims else None
        )
        hits = run_search_evidence(
            candidates[0].keyword, sponsor, candidates, logger, progress,
            key_players=reports.key_players(report), orient=orient, segments=report.segment_terms,
        )
        claim_pool = claim_future.result() if claim_future is not None else {}
        question_evidence = question_future.result()
        report_claim_evidence = report_claim_future.result() if report_claim_future is not None else {}

    logger.info(f"[timing] evidence collection (CLI path) TOTAL: {time.perf_counter() - evidence_t0:.1f}s")
    progress("Step 2: selecting evidence")
    selection = run_select(
        article, orient, sponsor, report, hits, logger,
        claim_hits=claim_pool, questions=questions, question_hits=question_evidence,
        report_claims=report_claims, report_claim_hits=report_claim_evidence,
    )
    evidence = run_build_evidence(selection, hits, logger, orient=orient, questions=questions)

    progress("Step 3: writing the article")
    written, title, sections, evidence_citations = run_write(
        article, orient, sponsor, report, evidence, logger, longtails=longtails
    )

    progress("Step 4: SEO + readability revision pass")
    title, sections, evidence_citations = run_revise(
        title, sections, evidence_citations, report, logger, evidence, sponsor.text, longtails=longtails
    )

    progress("Writing the FAQ block")
    faq = run_faq(report.name, report, title, sections, logger, sponsor_text=sponsor.text)

    progress("Checking the draft against the SOP")
    result = run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, original_filename, logger,
        evidence_citations=evidence_citations, faq=faq, longtails=longtails,
    )

    progress("Writing the revised article to output/")
    return result
