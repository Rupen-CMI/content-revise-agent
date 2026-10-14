import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse

from ddgs import DDGS
from ddgs.exceptions import DDGSException, TimeoutException

from config import CMI_HOST

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

# Rival research firms (their market sizes contradict CMI's) and press-release wires that mostly
# carry those firms' report announcements.
_BLOCKED_HOSTS = (
    CMI_HOST, "marketsandmarkets.com", "grandviewresearch.com", "fortunebusinessinsights.com",
    "mordorintelligence.com", "precedenceresearch.com", "verifiedmarketresearch.com",
    "alliedmarketresearch.com", "researchandmarkets.com", "imarcgroup.com", "marketresearchfuture.com",
    "transparencymarketresearch.com", "futuremarketinsights.com", "factmr.com", "persistencemarketresearch.com",
    "technavio.com", "expertmarketresearch.com", "databridgemarketresearch.com", "polarismarketresearch.com",
    "skyquestt.com", "straitsresearch.com", "zionmarketresearch.com", "maximizemarketresearch.com",
    "marketresearch.com", "giiresearch.com", "towardsfnb.com", "towardshealthcare.com", "novaoneadvisor.com",
    "gminsights.com", "reportlinker.com", "einpresswire.com", "openpr.com", "globenewswire.com",
    "prnewswire.com", "linkedin.com", "youtube.com", "reddit.com", "quora.com", "facebook.com",
    "instagram.com", "pinterest.com", "tiktok.com",
    # More rival-research/report-PR mills surfaced testing niche B2B markets (chemicals, medical
    # device outsourcing) — same "Market Size, Share, Growth" SEO-report pattern as the firms above.
    "kenresearch.com", "morganreedinsights.com", "markwideresearch.com", "techsciresearch.com",
    "sphericalinsights.com", "procurementresource.com", "syndicatedanalytics.com", "dragonsourcing.com",
    "custommarketinsights.com", "marketdigits.com", "statsmarketresearch.com", "coherentsolutions.com",
    "valuemarketresearch.com", "marketgrowthreports.com", "businessresearchinsights.com",
    "researchnreports.com", "marketinsightsreports.com", "globalgrowthinsights.com",
    "emergenresearch.com", "thebusinessresearchcompany.com",
    # Encyclopedia / dictionary / finance-explainer sites: they answer "what is X", never "what
    # happened in X lately" — a "Wealth - Meaning, Types…" page is not market evidence.
    "wikipedia.org", "grokipedia.com", "wallstreetmojo.com", "investopedia.com", "britannica.com",
    "merriam-webster.com", "dictionary.com", "corporatefinanceinstitute.com",
)

# Titles of definition/how-to/explainer/directory pages — reference material, not a reported
# event. Applied to text (background) hits only; news hits are dated articles about events and
# don't need it. "Market Size, Share, Growth" is the report-PR SEO pattern itself (a page titled
# exactly that is a rival firm's own report landing page even when its own domain isn't blocklisted
# yet — found live on markwideresearch.com, techsciresearch.com, etc.). Also catches product-roundup/listicle pages ("10 Best X Reviewed for 2026", "Top 10 Best X (2026 Review)", "32 Best X Tools") — a buying-guide comparison page, not a reported event, confirmed live: 7 of 19 kept hits for one run were exactly this pattern and all were still correctly rejected by SELECT, but they crowded out the real candidates and wasted the match budget.
_DEFINITIONAL_TITLE_RE = re.compile(
    r"\b(meaning|definitions?|glossary)\b|\bwhat (is|are)\b|\bhow to\b|\b(guide|introduction|intro) to\b"
    r"|\bexplained\b|\bfor beginners\b|\b(types|examples|benefits|advantages) of\b|\bvs\.?\b"
    r"|\bmarket (size|share),?\s*(share,?\s*)?growth\b|\blist of\b|\b(news|articles?) (archive|index|hub)\b"
    r"|^\s*[\w\s&]+\s(articles|news)\s*$"
    r"|\b(top|best)\s+\d+\b|\b\d+\+?\s+(best|tools|apps|platforms|solutions)\b|\(\d{4}\s*review\)",
    re.IGNORECASE,
)

# A vendor's own homepage, login page, or product-marketing page — not a reported event. Found
# live once key-player-name queries became common (LLM-generated queries lean on real company
# names much more than the old fixed templates did): a hit naming a key player plus a generic
# category word was being kept even when the page was just that company's own "Sign in to Zoho
# CRM" or "Zoho | Cloud Software Suite for Businesses" — true, on-topic, and utterly useless as a
# citable fact. Applied only to non-news hits; a dated news article's title legitimately mentioning
# "sign in" or similar would be unusual, but non-news (background) hits are exactly where a
# search engine surfaces a brand's own site for a query built around that brand's name.
_VENDOR_PAGE_TITLE_RE = re.compile(
    r"\bsign(ing)?[\s-]?in\b|\blog[\s-]?in\b|\bcreate (an |your )?account\b"
    r"|^\s*[\w\s&.'-]+\s[|–-]\s*(cloud|software|platform|suite|solutions?)\b",
    re.IGNORECASE,
)

_ADULT_TITLE_RE = re.compile(r"\b(porn\w*|xxx|nsfw|hentai)\b", re.IGNORECASE)
_ADULT_HOST_TOKENS = frozenset({
    "porn", "xxx", "xnxx", "xhamster", "xvideos", "hentai", "redtube", "youporn",
    "brazzers", "pornhub", "sexvid", "anysex", "helloporn", "hqporner", "pussyspace",
})
_ADULT_TLDS = frozenset({"xxx", "porn", "sex", "adult"})


def _is_adult(hit) -> bool:
    # Bare "sex"/"sexual" is deliberately not matched: legitimate coverage (e.g. CMI's
    # Sexual Wellness market) must not be filtered.
    labels = (hit.host or "").split(".")
    if labels and labels[-1] in _ADULT_TLDS:
        return True
    if any(tok in label for label in labels for tok in _ADULT_HOST_TOKENS):
        return True
    return bool(_ADULT_TITLE_RE.search(hit.title or ""))


_STOPWORDS = frozenset({
    "market", "markets", "global", "industry", "the", "and", "of", "for", "by", "in", "on", "a", "an",
    "to", "with", "based", "services", "service", "solutions", "solution", "systems", "system", "other",
})

# Deliberate, strict, deterministic policy: DDGS NEWS evidence must be at most 14 days
# old to ever be presented as a "recent development" fact. Enforced entirely here, before
# the SELECT call ever sees the hit, so no LLM is ever asked to judge whether a date
# counts as fresh. The underlying search engines sometimes report coarse, ambiguous
# relative ages (e.g. "2 weeks ago" can mean anywhere from ~10-20 days in practice), which
# can occasionally cause a hit that is actually still fresh to be dropped at this boundary —
# that is an accepted tradeoff: silently admitting a stale fact into a published blog post
# is a worse failure than discarding one borderline-fresh search hit out of dozens collected
# per run.
_MAX_NEWS_AGE = timedelta(days=14)
_PER_QUERY = 8

# Measured success rate per underlying ddgs engine across 63 real run logs (primp response
# codes + "Error in engine" timeouts, tallied directly from backend/logs/*.log):
#   startpage 93%, yahoo(news) 100%, bing 99.5%, grokipedia 100%, wikipedia 99.7%,
#   yahoo(text) 97%  — all kept.
#   brave 0% (429 on literally every one of 525 requests), duckduckgo 37% (403 or timeout on
#   most requests), mojeek 51% (403/timeout on about half) — all cut. These three contributed
#   no usable results while each timeout/retry on them cost up to the full 20s timeout, so they
#   were pure wasted wall-clock time, not just a minor drag.
# ddgs has no "news" engine for grokipedia/wikipedia/startpage, so the news-category backend
# list is narrower than the text-category one. This installed ddgs version also doesn't
# register "bing" as an available TEXT backend (confirmed live: "bing - backends do not exist
# or are disabled" — bing.py exists but isn't wired up for the text category here), even though
# bing_news works fine for the news category.
#
# grokipedia and wikipedia were later cut as well, despite their ~100% success rate: they are
# encyclopedia TITLE lookups (limit=1), so a query like "wealth management platform …" returns the
# dictionary-style "Wealth" entry — reliable, but never evidence of a market development.
# startpage was cut as well: bot-challenged (0 results on 16/16 calls in a 2026-10-13 test),
# so it added only latency.
_NEWS_BACKENDS = "bing,yahoo"
_TEXT_BACKENDS = "yahoo"

# Search providers throttle rapid-fire bursts from one IP (we watched DDG stall every
# query after several runs in quick succession, and logs show Brave 429-ing and DuckDuckGo's
# own endpoint 403-ing on nearly every query even at the old pacing). So: never parallel,
# a wide randomized politeness gap between queries (comfortably under the ~30 req/min figure
# commonly cited as DDG's unofficial throttling threshold — DDG publishes no official number),
# and retries with backoff on timeouts instead of instant zero-results.
_QUERY_TIMEOUT_S = 20
_QUERY_DELAY_S = 3.0  # base gap between queries (+ up to 2.5s jitter)
_INTRA_QUERY_DELAY_S = 1.5  # base gap between the news and text calls of one query (+ jitter)
_QUERY_RETRIES = 2


@dataclass
class SearchHit:
    title: str
    snippet: str
    url: str
    source: str
    date: datetime | None
    # True for d.news() results (carry a real, verifiable date) vs d.text() results (ddgs returns
    # no date field for these at all — see _ddgs below). Only is_news hits can ever be cited as
    # "ongoing trend" evidence; text hits are background/definitional context only.
    is_news: bool = False

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")


def _parse_date(raw) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw) if "T" in raw or "-" in raw[:5] else parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


_REL_UNITS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
_ABS_DATE_RE = re.compile(r"^\s*([A-Za-z]{3,9})\.? (\d{1,2}), (\d{4})\s*[·\-–—]")
_REL_DATE_RE = re.compile(r"^\s*(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago\s*[·\-–—]", re.IGNORECASE)


def _snippet_date(snippet: str) -> datetime | None:
    """Text results carry no date field, but search engines often prefix the snippet with one
    ("Mar 3, 2025 · …", "3 days ago · …"). Only a date at the very START of the snippet counts —
    a date mentioned mid-sentence is about the content, not when the page was published."""
    m = _ABS_DATE_RE.match(snippet or "")
    if m:
        try:
            return datetime.strptime(f"{m.group(1)[:3]} {m.group(2)} {m.group(3)}", "%b %d %Y").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    m = _REL_DATE_RE.match(snippet or "")
    if m:
        return datetime.now(timezone.utc) - timedelta(days=int(m.group(1)) * _REL_UNITS[m.group(2).lower()])
    return None


def _news(d: DDGS, query: str) -> list[SearchHit]:
    # timelimit="m": ask the engines for the past month. Without it the event-style queries return
    # relevance-ranked news from any time (46 of 48 hits in one live test were months old and were
    # then discarded by the 21-day cutoff), so the per-query slots were spent on stale items.
    # The engines only loosely honor it, which is why the strict 21-day filter still runs after.
    return [
        SearchHit(r["title"], r.get("body", ""), r["url"], r.get("source", ""), _parse_date(r.get("date")), is_news=True)
        for r in d.news(query, max_results=_PER_QUERY, backend=_NEWS_BACKENDS, timelimit="m")
    ]


def _text(d: DDGS, query: str, logger: logging.Logger = _null_logger) -> list[SearchHit]:
    hits = [
        SearchHit(r["title"], r.get("body", ""), r["href"], "", _snippet_date(r.get("body", "")), is_news=False)
        for r in d.text(query, max_results=_PER_QUERY, backend=_TEXT_BACKENDS)
    ]
    query_tokens = _content_tokens(query)
    if hits and query_tokens and not any(
        query_tokens & _content_tokens(f"{hit.title} {hit.snippet}") for hit in hits
    ):
        logger.warning(f"[search] text {query!r}: engine returned {len(hits)} result(s) unrelated to the query — batch discarded")
        return []
    return hits


def _attempt(fn, d: DDGS, query: str, kind: str, logger: logging.Logger) -> list[SearchHit]:
    """One search call (news OR text) with timeout retries. Never raises: the two calls for a
    query must fail independently — previously a text-search "No results found." escaped from the
    shared function and discarded the news hits already collected, wiping out every query."""
    for attempt in range(_QUERY_RETRIES + 1):
        try:
            return fn(d, query)
        except TimeoutException as e:
            if attempt < _QUERY_RETRIES:
                wait = 3.0 * (attempt + 1)
                logger.warning(f"[search] {kind} {query!r} timed out (attempt {attempt + 1}); retrying in {wait:.0f}s")
                time.sleep(wait)
            else:
                logger.warning(f"[search] {kind} {query!r} failed ({type(e).__name__}: {e}) — continuing with 0 results", exc_info=True)
                return []
        except DDGSException as e:
            if "no results" in str(e).lower():  # an empty answer, not a fault
                logger.info(f"[search] {kind} {query!r} -> no results")
            else:
                logger.warning(f"[search] {kind} {query!r} failed ({type(e).__name__}: {e}) — continuing with 0 results", exc_info=True)
            return []
        except Exception as e:  # one failed provider/query must not sink the run; search is best-effort
            logger.warning(f"[search] {kind} {query!r} failed ({type(e).__name__}: {e}) — continuing with 0 results", exc_info=True)
            return []
    return []


def _safe(d: DDGS, query: str, logger: logging.Logger = _null_logger) -> list[SearchHit]:
    news = _attempt(_news, d, query, "news", logger)
    time.sleep(_INTRA_QUERY_DELAY_S + random.uniform(0, 0.6))
    text = _attempt(lambda d, q: _text(d, q, logger), d, query, "text", logger)
    logger.info(f"[search] query {query!r}: {len(news)} news + {len(text)} text")
    return news + text


def run_searches(queries: list[str], logger: logging.Logger = _null_logger) -> list[SearchHit]:
    """Runs queries one at a time, in order, with a politeness gap between them —
    never more than one request to DDGS in flight, and never a rapid-fire burst.
    Reuses one DDGS session across the whole run (opening a fresh client per query was an
    unnecessary extra per-query cost with no benefit, and some DDGS backends track sessions)."""
    hits: list[SearchHit] = []
    with DDGS(timeout=_QUERY_TIMEOUT_S) as d:
        for i, q in enumerate(queries):
            batch = _safe(d, q, logger=logger)
            logger.info(f"[search] query {q!r} -> {len(batch)} raw result(s)")
            hits += batch
            if i < len(queries) - 1:
                time.sleep(_QUERY_DELAY_S + random.uniform(0, 2.5))
    return hits


def _content_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def gate_terms(phrases: list[str]) -> tuple[set[str], list[str]]:
    """Single-word and full-phrase vocabulary derived from curated market/segment names."""
    tokens: set[str] = set()
    for p in phrases:
        tokens |= _content_tokens(p)
    full = [p.lower() for p in phrases if len(p.split()) >= 2]
    return tokens, full


# Category words real coverage swaps for the market name's head noun ("software" ->
# "platform", "martech", "agentic", …). A hit naming any of these still names the same kind
# of thing, so they count as the head matching.
_TECH_HEAD_SYNONYMS = frozenset({
    "software", "platform", "platforms", "saas", "app", "apps", "application",
    "applications", "tool", "tools", "technology", "technologies", "tech",
    "martech", "adtech", "cloud", "automation", "agentic",
})

# A market-name modifier seen in this share of catalog keywords or more is too common to
# identify one market, so the gate doesn't require it. Kept at 0.5%: at 1% "digital"
# (71 of 9,564 catalog names, 0.74%) counted as specific and became required, which
# dropped real market news phrased as e.g. "agentic marketing" or "marketing platform".
_GENERIC_DF_THRESHOLD = 0.005


def _ordered_market_tokens(phrase: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", phrase.lower())
    while words and words[0] == "global":
        words.pop(0)
    while words and words[-1] in ("market", "markets"):
        words.pop()
    return [w for w in words if len(w) > 2 and w not in _STOPWORDS]


@lru_cache(maxsize=1)
def _catalog_token_freq() -> dict[str, float]:
    """Share of catalog report keywords containing each word. A market-name word found across
    many catalog keywords ("management", "digital") can't discriminate one market from another,
    so the gate doesn't require it; a rare word ("propionate", "tourette") is the market's
    actual identity and is always required."""
    import reports
    entries = reports.catalog()
    if not entries:
        return {}
    df: dict[str, int] = {}
    for e in entries:
        for w in {w for w in re.findall(r"[a-z0-9]+", (e.keyword or "").lower())
                  if len(w) > 2 and w not in _STOPWORDS}:
            df[w] = df.get(w, 0) + 1
    return {w: c / len(entries) for w, c in df.items()}


def _gate_for_phrase(phrase: str, freq: dict[str, float]) -> tuple[str, list[str], set[str], list[str]] | None:
    """(head, required, generic, non_head) for one market phrase. English noun phrases put the
    head noun last ("digital marketing SOFTWARE"); the modifiers before it are required only
    when rare enough in the catalog to actually identify this market."""
    toks = _ordered_market_tokens(phrase)
    if not toks:
        return None
    head, non_head = toks[-1], toks[:-1]
    generic = {t for t in non_head if freq.get(t, 0.0) >= _GENERIC_DF_THRESHOLD}
    required = [t for t in non_head if t not in generic] or list(non_head)
    return head, required, generic, non_head


def _head_matched(head: str, hit_tokens: set[str]) -> bool:
    if head in hit_tokens:
        return True
    return head in _TECH_HEAD_SYNONYMS and bool(_TECH_HEAD_SYNONYMS & hit_tokens)


def _player_patterns(key_players: list[str]) -> list[tuple[str, re.Pattern]]:
    return [(p, re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE)) for p in key_players if p]


def filter_hits(
    hits: list[SearchHit], phrases: list[str], blocked_hosts: tuple[str, ...] = (),
    limit: int = 30, logger: logging.Logger = _null_logger,
    key_players: list[str] = (),
) -> list[SearchHit]:
    """Dedup, drop blocked/stale sources, and keep hits sharing vocabulary with the market."""
    tokens, full_phrases = gate_terms(phrases)
    freq = _catalog_token_freq()
    gates = [(p, g) for p in phrases if (g := _gate_for_phrase(p, freq)) is not None]
    for p, (head, required, generic, _) in gates:
        logger.info(f"[filter] gate {p!r}: head={head!r} required={required} generic={sorted(generic)}")
    logger.info(f"[filter] gate phrases ({len(full_phrases)}): {full_phrases}")
    logger.info(f"[filter] key players ({len(key_players)}): {list(key_players)}")
    player_res = _player_patterns(list(key_players))
    cutoff = datetime.now(timezone.utc) - _MAX_NEWS_AGE
    blocked = _BLOCKED_HOSTS + tuple(h.lower() for h in blocked_hosts)

    seen: set[str] = set()
    scored: list[tuple[int, SearchHit]] = []
    kept = dropped = 0
    for hit in hits:
        if _is_adult(hit):
            dropped += 1
            logger.info("[filter] DROP (adult content)")
            continue
        if not hit.title or not hit.url:
            dropped += 1
            logger.info("[filter] DROP (missing title/url)")
            continue
        if any(hit.host == b or hit.host.endswith("." + b) for b in blocked):
            dropped += 1
            logger.info(f"[filter] DROP (blocked host {hit.host}): {hit.title!r}")
            continue
        if not hit.is_news and _DEFINITIONAL_TITLE_RE.search(hit.title):
            dropped += 1
            logger.info(f"[filter] DROP (definition/explainer page) [{hit.host}] {hit.title!r}")
            continue
        if not hit.is_news and _VENDOR_PAGE_TITLE_RE.search(hit.title):
            dropped += 1
            logger.info(f"[filter] DROP (vendor homepage/login page) [{hit.host}] {hit.title!r}")
            continue
        if hit.date and hit.is_news and hit.date < cutoff:
            dropped += 1
            logger.info(f"[filter] DROP (stale, {hit.date:%Y-%m-%d}): {hit.title!r}")
            continue
        fingerprint = hashlib.sha256(re.sub(r"\W+", " ", hit.title.lower()).strip().encode()).hexdigest()
        if fingerprint in seen or hit.url in seen:
            dropped += 1
            logger.info(f"[filter] DROP (duplicate): {hit.title!r}")
            continue
        seen.update((fingerprint, hit.url))

        text = f"{hit.title} {hit.snippet}".lower()
        matched_tokens = tokens & _content_tokens(text)
        matched_phrase = next((p for p in full_phrases if p in text), None)
        hit_tokens = _content_tokens(text)
        token_ok = any(
            _head_matched(head, hit_tokens) and all(t in hit_tokens for t in required)
            for _, (head, required, _, _) in gates
        )
        named_player = next((p for p, rx in player_res if rx.search(text)), None)
        # A key-player name plus a generic category word is too weak a bar on its own — it is
        # exactly the shape of that company's OWN homepage/product page ("Zoho | Cloud Software
        # Suite", "Sign in to Zoho CRM"), true and on-topic but not a reported event or citable
        # fact. Found live once LLM-generated queries started leaning on real company names much
        # more than the old fixed templates did. Require the hit to also be a dated NEWS result —
        # a background (undated) page naming a vendor is its own marketing material far more often
        # than it is a substantive third-party fact about that vendor.
        player_ok = hit.is_news and named_player is not None and any(
            any(t in hit_tokens for t in ([head] if not non_head else non_head))
            for _, (head, _, _, non_head) in gates
        )
        rules = "|".join(
            r for ok, r in ((matched_phrase, "phrase"), (token_ok, "tokens"), (player_ok, "key_player")) if ok
        )
        if rules:
            kept += 1
            logger.info(
                f"[filter] KEEP (rule={rules}, tokens={sorted(matched_tokens)}, phrase={matched_phrase!r}"
                + (f", player={named_player!r}" if player_ok else "") + f") "
                f"[{hit.host}] {hit.title!r}"
            )
            scored.append((len(matched_tokens), hit))
        else:
            dropped += 1
            logger.info(f"[filter] DROP (no topical overlap) [{hit.host}] {hit.title!r}")

    scored.sort(key=lambda x: (x[0], x[1].date or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    final = [hit for _, hit in scored[:limit]]
    logger.info(f"[filter] kept {kept}, dropped {dropped}, returning top {len(final)} of {kept}")
    return final
