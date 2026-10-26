import hashlib
import json
import logging
import os
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import urlparse

from ddgs import DDGS
from ddgs.exceptions import DDGSException, TimeoutException

import sources
import config
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
    "gminsights.com", "reportlinker.com", "einpresswire.com", "openpr.com", "linkedin.com", "youtube.com", "reddit.com", "quora.com", "facebook.com",
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
) + tuple(sources.JUNK_HOSTS)

# Title shapes that are never a reported event, whatever the host — including news hits, which the
# regexes below skip. Found in 138 real run logs: finance.yahoo.com alone supplied ~750 hits, mostly
# earnings-call transcripts and stock-pick pieces; rival report ads slipped through on unlisted hosts
# ("Loyalty Management Market worth $23.11 billion by 2032 - Report by MarketsandMarkets").
_JUNK_ANY_TITLE_RE = re.compile(
    r"\bstocks?\b|\bearnings (call|season|preview|report)\b|\bq[1-4]\b.{0,20}\b(earnings|results|scorecard|deep dive)\b"
    r"|\b(call|transcript) highlights\b|\bprice target\b|\bbuy, hold or sell\b|\bzacks\b|\bsaid on its\b"
    r"|\bmarket\b.{0,40}\b(worth|to reach|to hit|projected to|valued at|set to reach)\b.{0,30}\b(billion|million|by 20\d\d)\b"
    r"|\b(report|study) by (marketsandmarkets|grand view|fortune business|mordor|imarc|allied market|"
    r"research and markets|precedence research)\b"
    r"|\bmarket (size|share|forecast|analysis|outlook|report)\b.{0,60}\b20[2-4]\d\b",
    re.IGNORECASE,
)
# Section hubs, home pages, company-profile and event-session pages — "Marketing News | <Site>", "Home
# Page", "Latest ... News", "Company Profile and News". Surfaced by site-restricted text queries, which
# return a site's own landing pages far more often than its articles.
_HUB_TITLE_RE = re.compile(
    r"\b(home ?page|company profile|session details|the home of|coverage of|search results|all (articles|news))\b"
    r"|\blatest\b.{0,40}\bnews\b|\bnews (and|&) (insights|analysis|updates)\b"
    r"|^[^|–]{0,60}\bnews\b\s*[|–-]|\bthe best\b.{0,60}\b(in|for) 20\d\d\b",
    re.IGNORECASE,
)
# A bare company name ("SEI Investments Co.", "Zeta Global Holdings Corp.") is a quote/profile page.
_BARE_COMPANY_TITLE_RE = re.compile(
    r"^[\w .,&'’-]{2,70}\b(corp|corporation|co|inc|ltd|llc|plc|holdings?|group|ag|sa|se|nv)\.?$", re.IGNORECASE
)
# Section/landing pages and profile pages ("Newsroom", "Nasdaq Watch", "United States", "Ross Allardice")
# are 1-3 words; real headlines carrying a fact are longer.
_MIN_TITLE_WORDS = 4

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

# Deterministic cutoff for DDGS NEWS evidence, enforced here before SELECT sees the hit so no LLM
# judges freshness. Six months, not weeks: the articles aren't time-sensitive analysis, and a
# 14-day window was discarding most otherwise-useful evidence. The writer still has to state each
# dated fact's month and year, so an older item is never passed off as "this week's" news.
_MAX_NEWS_AGE = timedelta(days=180)
# Ordinary web-search (non-news) hits are not held to the news window, since they carry only
# a snippet date at best — but a hit with a visible date older than the pipeline's 12-month
# freshness window (freshness.STALE_AFTER) must not reach SELECT either, or a stale event can end
# up cited in the draft (seen live: a July 2025 acquisition cited as a current development).
_MAX_WEB_AGE = timedelta(days=365)
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
# Re-tested live 2026-10-18 (ddgs 9.15.0). "bing" is NOT a registered text engine here: passing it makes
# ddgs silently fan out to every text engine (brave 429 on all calls, grokipedia 502, mojeek 403, plus
# wikipedia/grokipedia title lookups). Single engines are flaky (yahoo alone: plain queries only; startpage,
# mojeek, duckduckgo alone: nothing), but this explicit combination returned 7 hits for both plain and
# site:-restricted queries in 2-4s, without the engines that only fail or add encyclopedia entries.
_TEXT_BACKENDS = "startpage,mojeek,yahoo,duckduckgo"

# Search providers throttle rapid-fire bursts from one IP (we watched DDG stall every
# query after several runs in quick succession, and logs show Brave 429-ing and DuckDuckGo's
# own endpoint 403-ing on nearly every query even at the old pacing). So: never parallel,
# a wide randomized politeness gap between queries (comfortably under the ~30 req/min figure
# commonly cited as DDG's unofficial throttling threshold — DDG publishes no official number),
# and retries with backoff on timeouts instead of instant zero-results.
_QUERY_TIMEOUT_S = 20
_QUERY_DELAY_S = 4.0  # base gap between queries (+ up to 2.5s jitter)
_INTRA_QUERY_DELAY_S = 2.0  # base gap between the news and text calls of one query (+ jitter)
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
    # Query-relevant paragraphs from the article page itself (feeds.attach_excerpts), when the page was
    # readable. Empty for hits that only have a headline and snippet.
    excerpt: str = ""
    # "claim" for pages retrieved to back one claim of the original article (claim_id), else "market".
    scope: str = "market"
    claim_id: str = ""

    @property
    def host(self) -> str:
        return urlparse(self.url).netloc.lower().removeprefix("www.")


# --------------------------------------------------------------- dev-only disk cache (config.SEARCH_CACHE)
# See config.SEARCH_CACHE — enabled only via CMI_SEARCH_CACHE=1, for iterating on dev runs of the
# same market without re-hitting rate-limited engines. One tiny JSON file per (kind, query) under
# config.SEARCH_CACHE_DIR; live fetch paths are untouched when disabled. scope/claim_id/excerpt are
# deliberately NOT stored: they are set by the caller AFTER the search, so a cached hit must arrive
# blank and get its caller-side annotations exactly at a fresh hit would.
def _cache_path(kind: str, query: str):
    h = hashlib.sha256(f"{kind}|{query}".encode("utf-8")).hexdigest()[:24]
    return config.SEARCH_CACHE_DIR / f"{h}.json"


def _hit_to_cache(h: SearchHit) -> dict:
    return {"title": h.title, "snippet": h.snippet, "url": h.url, "source": h.source,
            "date": h.date.isoformat() if h.date else None, "is_news": h.is_news}


def _hit_from_cache(d: dict) -> SearchHit:
    date = None
    if d.get("date"):
        try:
            date = datetime.fromisoformat(d["date"])
        except ValueError:
            pass
    return SearchHit(d.get("title", ""), d.get("snippet", ""), d.get("url", ""), d.get("source", ""), date, bool(d.get("is_news")))


def cache_lookup(kind: str, query: str) -> list[SearchHit] | None:
    """The cached results for one (kind, query), or None on a miss/expiry/corruption (which callers
    treat exactly like needing a live fetch). Never raises."""
    if not config.SEARCH_CACHE:
        return None
    try:
        blob = json.loads(_cache_path(kind, query).read_text(encoding="utf-8"))
        if time.time() - blob["t"] <= config.SEARCH_CACHE_TTL_S:
            return [_hit_from_cache(h) for h in blob.get("hits") or []]
    except OSError:
        pass  # absent cache behaves like a miss
    except Exception:
        logging.getLogger("revamp.cachecorrupt").debug("corrupt cache entry ignored", exc_info=True)
    return None


def cache_store(kind: str, query: str, hits: list[SearchHit]) -> None:
    """Record one successful engine answer to disk. Best-effort: any cache bookkeeping failure is
    swallowed — search must never break because the cache could not write. Atomic via temp+replace
    (concurrent writes to the same key come from the parallel evidence threads)."""
    if not config.SEARCH_CACHE:
        return
    try:
        path = _cache_path(kind, query)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}{threading.get_ident()}")
        tmp.write_text(json.dumps(
            {"t": time.time(), "hits": [_hit_to_cache(h) for h in hits]}, ensure_ascii=False,
        ), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


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
    # DDGS only offers d/w/m/y, so "y" is the narrowest limit that covers the 6-month window;
    # _MAX_NEWS_AGE then trims it to 180 days. Without any limit the engines return
    # relevance-ranked news from any time and most per-query slots go to years-old items.
    return [
        SearchHit(r["title"], r.get("body", ""), r["url"], r.get("source", ""), _parse_date(r.get("date")), is_news=True)
        for r in d.news(query, max_results=_PER_QUERY, backend=_NEWS_BACKENDS, timelimit="y")
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
    shared function and discarded the news hits already collected, wiping out every query.

    With the dev-only cache on (config.SEARCH_CACHE), the successful result of each (kind, query)
    is reused instead of re-hitting DDGS; the retry loop stays outside the cache, so a transient
    engine failure inside fn is never recorded as an empty answer."""
    for attempt in range(_QUERY_RETRIES + 1):
        try:
            cached = cache_lookup(kind, query)
            if cached is not None:
                logger.info(f"[cache] {kind} {query!r}: {len(cached)} cached result(s)")
                return cached
            hits = fn(d, query)
            cache_store(kind, query, hits)
            return hits
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


# DDGS politeness is per REQUEST, per kind. News calls go to the news engines (bing, yahoo) and text calls to the text
# engines (startpage, mojeek, yahoo, duckduckgo), so the two kinds can run side by side without either engine seeing more
# than one request per gap; within a kind, any two requests, from the market fallback or the claim stage, are spaced by
# the gap. (The old single lock, held for a whole run, made the claim stage wait for the entire market fallback and made
# every query do its news call, a pause, then its text call, one after the other.)
_DDGS_LOCKS = {"news": threading.Lock(), "text": threading.Lock()}
_ddgs_last_end = {"news": 0.0, "text": 0.0}


@contextmanager
def _ddgs_gate(kind: str):
    with _DDGS_LOCKS[kind]:
        wait = _ddgs_last_end[kind] + _QUERY_DELAY_S + random.uniform(0, 2.5) - time.monotonic()
        if _ddgs_last_end[kind] and wait > 0:
            time.sleep(wait)
        try:
            yield
        finally:
            _ddgs_last_end[kind] = time.monotonic()


def _lane(kind: str, fn, queries: list[str], logger: logging.Logger) -> list[SearchHit]:
    """One kind of DDGS call (news or text) for every query, in order, each request through that kind's gate."""
    out: list[SearchHit] = []
    with DDGS(timeout=_QUERY_TIMEOUT_S) as d:
        for q in queries:
            with _ddgs_gate(kind):
                batch = _attempt(fn, d, q, kind, logger)
            logger.info(f"[search] {kind} {q!r}: {len(batch)} raw result(s)")
            out += batch
    return out


def run_searches(queries: list[str], logger: logging.Logger = _null_logger) -> list[SearchHit]:
    """DDGS news and text searches for every query. The two kinds run concurrently, each one query at a time with a
    politeness gap, so the wall time is that of the slower kind, not the sum of both."""
    with ThreadPoolExecutor(2) as ex:
        news_f = ex.submit(_lane, "news", _news, queries, logger)
        text_f = ex.submit(_lane, "text", lambda d, q: _text(d, q, logger), queries, logger)
        news, text = news_f.result(), text_f.result()
    logger.info(f"[search] DDGS: {len(news)} news + {len(text)} text raw result(s) from {len(queries)} queries")
    return news + text


def run_text_searches(queries: list[str], logger: logging.Logger = _null_logger) -> list[SearchHit]:
    """Text-only DDGS queries (no news call): used for CLAIM BACKING, whose useful sources are explainers,
    encyclopedias and reference pages — kinds of pages that never appear in a news index. Same per-request text gate as
    the market fallback's text lane."""
    hits: list[SearchHit] = []
    with DDGS(timeout=_QUERY_TIMEOUT_S) as d:
        for q in queries:
            with _ddgs_gate("text"):
                batch = _attempt(lambda d, q: _text(d, q, logger), d, q, "text", logger)
            logger.info(f"[claim-search] query {q!r} -> {len(batch)} raw result(s)")
            hits += batch
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


def _word_or_compound(token: str, hit_tokens: set[str]) -> bool:
    """The market word as a whole word, or at the start of a short compound built on it ("wealth" -> "wealthtech").
    Whole-word matching alone dropped "Leading Wealthtech Platform for Independent Advisors" for a wealth-platform market."""
    return token in hit_tokens or (len(token) >= 5 and any(
        w.startswith(token) and len(w) - len(token) <= 5 for w in hit_tokens
    ))


def stem_hit(word: str, stem: str) -> bool:
    """A word matches a market-identity stem as a whole word, or by prefix only when the stem is long enough to
    identify something ("wealth" -> "wealthtech"). A 3-letter stem such as "web" must not match by prefix: it
    matches "website" in every press release's boilerplate and "web shells" in every security story."""
    return word == stem or (len(stem) >= 5 and word.startswith(stem))


def compile_topic_terms(terms: list[str]) -> list[re.Pattern]:
    """Whole-word/phrase patterns (plural allowed) for the topic vocabulary the query-writing step produced."""
    out = []
    for t in terms:
        t = re.sub(r"\s+", " ", str(t).strip().lower())
        if len(t) >= 3:
            out.append(re.compile(r"\b" + re.escape(t) + r"s?\b", re.IGNORECASE))
    return out


def _head_matched(head: str, hit_tokens: set[str]) -> bool:
    if head in hit_tokens:
        return True
    return head in _TECH_HEAD_SYNONYMS and bool(_TECH_HEAD_SYNONYMS & hit_tokens)


def _player_patterns(key_players: list[str]) -> list[tuple[str, re.Pattern]]:
    return [(p, re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE)) for p in key_players if p]


def _is_key_player_own_site(host: str, key_players: list[str]) -> str | None:
    """A host named after one of the market's key players ("temenos.com", "investor.fisglobal.com",
    "broadridge.com") is that company's own site — product pages, docs, investor relations — which is
    its marketing, not a third-party reported fact. Generic: driven by the report's own key players,
    so it needs no per-vendor list. Returns the matched player, or None."""
    label = host.removeprefix("www.").split(".")
    label = label[-3] if len(label) >= 3 and label[-2] in ("co", "com", "org", "net", "gov") else (label[-2] if len(label) >= 2 else label[0])
    label = re.sub(r"[^a-z0-9]", "", label)
    for player in key_players:
        full = re.sub(r"[^a-z0-9]", "", player.lower())
        first = re.sub(r"[^a-z0-9]", "", (player.split() or [""])[0].lower())
        for cand in {full, first}:
            short, long_ = sorted((cand, label), key=len)
            if len(short) >= 4 and long_.startswith(short):
                return player
    return None


def filter_hits(
    hits: list[SearchHit], phrases: list[str], blocked_hosts: tuple[str, ...] = (),
    limit: int = 30, logger: logging.Logger = _null_logger,
    key_players: list[str] = (), max_per_host: int = 0, sector_hosts: frozenset[str] = frozenset(),
    topic_terms: list[str] = (),
) -> list[SearchHit]:
    """Dedup, drop blocked/stale sources, and keep hits sharing vocabulary with the market.
    topic_terms is the market's own vocabulary as the query-writing step produced it (technologies, activities,
    what vendors and buyers do): a dated news item that uses one is on-topic even when its headline lacks the
    market NAME, and a sector-source item that uses none is not kept on the strength of its host alone.
    max_per_host (0 = unlimited) keeps one prolific site from filling the whole shortlist.
    sector_hosts are the trade-press sites of the market's own industry: a dated news item from one is on-topic by
    SOURCE, so it reaches SELECT (the LLM judge) even when its headline lacks the market's words
    ("SEI taps IBM for agentic AI growth" in a wealth-platform run). Measured live: the strict word gate dropped about
    ten such relevant items per run."""
    tokens, full_phrases = gate_terms(phrases)
    freq = _catalog_token_freq()
    gates = [(p, g) for p in phrases if (g := _gate_for_phrase(p, freq)) is not None]
    for p, (head, required, generic, _) in gates:
        logger.info(f"[filter] gate {p!r}: head={head!r} required={required} generic={sorted(generic)}")
    logger.info(f"[filter] gate phrases ({len(full_phrases)}): {full_phrases}")
    logger.info(f"[filter] key players ({len(key_players)}): {list(key_players)}")
    player_res = _player_patterns(list(key_players))
    topic_res = compile_topic_terms(list(topic_terms))
    logger.info(f"[filter] topic terms ({len(topic_res)}): {list(topic_terms)}")
    cutoff = datetime.now(timezone.utc) - _MAX_NEWS_AGE
    web_cutoff = datetime.now(timezone.utc) - _MAX_WEB_AGE
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
        own_site = _is_key_player_own_site(hit.host, list(key_players))
        if own_site and not sources.is_trusted(hit.host):
            dropped += 1
            logger.info(f"[filter] DROP (company's own site: {own_site}) [{hit.host}] {hit.title!r}")
            continue
        title_words = len(hit.title.split())
        if title_words < _MIN_TITLE_WORDS:
            dropped += 1
            logger.info(f"[filter] DROP (landing/profile page, {title_words}-word title) [{hit.host}] {hit.title!r}")
            continue
        if (_JUNK_ANY_TITLE_RE.search(hit.title) or _HUB_TITLE_RE.search(hit.title)
                or (title_words <= 5 and _BARE_COMPANY_TITLE_RE.match(hit.title))):
            dropped += 1
            logger.info(f"[filter] DROP (stock/earnings/report-ad/quote page) [{hit.host}] {hit.title!r}")
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
        if hit.date and not hit.is_news and hit.date < web_cutoff:
            dropped += 1
            logger.info(f"[filter] DROP (stale web result, {hit.date:%Y-%m-%d}): {hit.title!r}")
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
            _head_matched(head, hit_tokens) and all(_word_or_compound(t, hit_tokens) for t in required)
            for _, (head, required, _, _) in gates
        )
        named_player = next((p for p, rx in player_res if rx.search(text)), None)
        # A trusted source already vouches that this is real reporting, so the topical bar drops from
        # "market identity AND head noun, as whole words" to "any market-identity word, matched by
        # prefix". That is what lets "Wealthtech" / "Martech" / "Marketers" count for wealth / marketing,
        # and lets a hit with no head noun ("Adhesion Wealth Launches Custom Model Solutions") through.
        # SELECT still judges relevance afterwards.
        topic_hit = next((rx.pattern for rx in topic_res if rx.search(text)), None)
        # With a topic vocabulary, one market-identity word is not enough on its own either: a market named by a
        # generic word plus a specific one ("web" + "scraping") would otherwise pass every story that merely
        # contains the generic word ("web shells", "web apps"). The item must also use the market's head noun
        # or one of its topic terms.
        trusted_ok = hit.is_news and sources.is_trusted(hit.host) and any(
            any(stem_hit(w, t) for w in hit_tokens for t in (required or [head]))
            and (not topic_res or topic_hit is not None or _head_matched(head, hit_tokens))
            for _, (head, required, _, _) in gates
        )
        # A key-player name plus a generic category word is too weak a bar on its own — it is
        # exactly the shape of that company's OWN homepage/product page ("Zoho | Cloud Software
        # Suite", "Sign in to Zoho CRM"), true and on-topic but not a reported event or citable
        # fact. Found live once LLM-generated queries started leaning on real company names much
        # more than the old fixed templates did. Require the hit to also be a dated NEWS result —
        # a background (undated) page naming a vendor is its own marketing material far more often
        # than it is a substantive third-party fact about that vendor.
        player_ok = hit.is_news and named_player is not None and (
            sources.is_trusted(hit.host) or any(
                any(t in hit_tokens for t in ([head] if not non_head else non_head))
                for _, (head, _, _, non_head) in gates
            )
        )
        # A sector-source item needs a topical signal beyond its host once the topic vocabulary exists: the trade press
        # of a broad industry (security, enterprise IT) publishes mostly stories that have nothing to do with this
        # market (patch notices, earnings dates, breaches). Without a vocabulary (the LLM step failed) the old
        # host-only rule stands.
        sector_ok = hit.is_news and any(hit.host == h or hit.host.endswith("." + h) for h in sector_hosts) and (
            topic_hit is not None or not topic_res
        )
        topic_ok = hit.is_news and topic_hit is not None
        rules = "|".join(
            r for ok, r in (
                (matched_phrase, "phrase"), (token_ok, "tokens"), (player_ok, "key_player"), (trusted_ok, "trusted_source"),
                (sector_ok, "sector_source"), (topic_ok, "topic_terms"),
            ) if ok
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
    final: list[SearchHit] = []
    per_host: dict[str, int] = {}
    for _, hit in scored:
        if len(final) >= limit:
            break
        if max_per_host and per_host.get(hit.host, 0) >= max_per_host:
            continue
        per_host[hit.host] = per_host.get(hit.host, 0) + 1
        final.append(hit)
    logger.info(f"[filter] kept {kept}, dropped {dropped}, returning top {len(final)} of {kept}")
    return final


# ------------------------------------------------------------------------------- claim backing
# Claim-backing searches judge a DIFFERENT question than market news does: does any outside page
# support, qualify, or contradict one factual claim made in the original article? That changes the
# filter profile in four deliberate ways (each one reuses filter_hits's reasoning, inverted for the
# different purpose — see filter_claim_hits for the per-rule notes):
#   - no recency cutoff at all: "commission-free apps are regulated like brokers" is backed just as
#     well by a 2021 regulator explainer as by this month's news, and stale-dating a BACKGROUND
#     claim's source is wrong, not cautious;
#   - encyclopedia/dictionary/explainer hosts (wikipedia, investopedia, …) are ALLOWED here — for
#     market news they are useless, but for backing a definition/practice/comparison claim they are
#     exactly the useful kind, so they must not sit in the blocked list;
#   - DEFINITIONAL_TITLE_RE does not apply: a "What is X?" page is the prize here, not junk —
#     still dropped: adult, PR wires/social/content farms, vendor login/landing pages, section
#     hubs, bare company quote pages, and the market-report-SEO ad pattern (a page titled
#     "<Market> Size, Share, Growth…" advertises a rival report; it is not claim backing);
#   - the topical bar is overlap with THE CLAIM's own vocabulary (not the market's), verified
#     token-wise, because these hits were retrieved BY the claim's queries — the gate only has to
#     catch a stray irrelevant result from the same engine, not re-derive the market.

# Encyclopedia / dictionary / reference hosts the market filter bans (they answer "what is X",
# never "what happened in X") — re-listed so filter_claim_hits can un-block them.
_ENC_REFS = frozenset({
    "wikipedia.org", "grokipedia.com", "wallstreetmojo.com", "investopedia.com", "britannica.com",
    "merriam-webster.com", "dictionary.com", "corporatefinanceinstitute.com",
})
# The big software vendors' own sites are blocked for MARKET evidence (a vendor blog is not reporting) but they are
# the primary source for exactly the claims this stage backs: what a product category is ("What is marketing
# automation?"), what a named company offers, how a named product works. Vendor login/landing pages are still
# caught by _VENDOR_PAGE_TITLE_RE; whether a page SUPPORTS the claim is SELECT's stance call, not the host list's.
_CLAIM_ALLOWED_VENDORS = frozenset({
    "microsoft.com", "learn.microsoft.com", "oracle.com", "ibm.com", "adobe.com", "business.adobe.com",
    "salesforce.com", "hubspot.com", "academy.hubspot.com", "blog.hubspot.com", "knowledge.hubspot.com",
    "aws.amazon.com", "g2.com", "learn.g2.com",
})
_CLAIM_BLOCKED_HOSTS = tuple(h for h in _BLOCKED_HOSTS if h not in _ENC_REFS and h not in _CLAIM_ALLOWED_VENDORS)

# A 2-word title ("Robinhood") is a ticker/quote page; for claim backing even a short page title
# naming the claim's subject can be fine, so the floor sits between filter_hits's 4 and 0.
_MIN_CLAIM_TITLE_WORDS = 3

# Distinctive claim words that must survive into a hit's text for the hit to be judged related.
# Two tokens minimum: one token is almost always a category word the claim shares with half the web
# (different token sets per claim keep this honest), two is the loosest bar that still discriminates.
_CLAIM_OVERLAP_MIN = 2
_CLAIM_MAX_PER_HOST = 2


def _site_of(host: str) -> str:
    """The site a host belongs to ("blog.converted.in" -> "converted.in", "news.bbc.co.uk" -> "bbc.co.uk")."""
    parts = host.split(".")
    keep = 3 if len(parts) >= 3 and len(parts[-1]) == 2 and len(parts[-2]) <= 3 else 2
    return ".".join(parts[-keep:])


def filter_claim_hits(
    hits: list[SearchHit], claim: str, logger: logging.Logger = _null_logger, limit: int = 6,
) -> list[SearchHit]:
    """The claim-backing filter profile (see the block comment above for how and why it differs
    from filter_hits). Ranks by overlap with the claim's own vocabulary; `limit` caps the handful
    handed to SELECT per claim, which only ever picks at most two."""
    claim_tokens = _content_tokens(claim)
    logger.info(f"[filter:claim] gate ({len(claim_tokens)} tokens): {sorted(claim_tokens)}")
    seen: set[str] = set()
    scored: list[tuple[int, SearchHit]] = []
    dropped = 0
    for hit in hits:
        if _is_adult(hit) or not hit.title or not hit.url:
            dropped += 1
            continue
        if any(hit.host == b or hit.host.endswith("." + b) for b in _CLAIM_BLOCKED_HOSTS):
            dropped += 1
            logger.info(f"[filter:claim] DROP (blocked host {hit.host}): {hit.title!r}")
            continue
        title_words = len(hit.title.split())
        if (
            title_words < _MIN_CLAIM_TITLE_WORDS
            or _HUB_TITLE_RE.search(hit.title)
            or _JUNK_ANY_TITLE_RE.search(hit.title)
            or (title_words <= 5 and _BARE_COMPANY_TITLE_RE.match(hit.title))
            or (not hit.is_news and _VENDOR_PAGE_TITLE_RE.search(hit.title))
        ):
            dropped += 1
            logger.info(f"[filter:claim] DROP (junk shape) [{hit.host}] {hit.title!r}")
            continue
        fingerprint = hashlib.sha256(re.sub(r"\W+", " ", hit.title.lower()).strip().encode()).hexdigest()
        if fingerprint in seen or hit.url in seen:
            dropped += 1
            continue
        seen.update((fingerprint, hit.url))
        overlap = claim_tokens & _content_tokens(f"{hit.title} {hit.snippet} {hit.excerpt}")
        if len(overlap) < _CLAIM_OVERLAP_MIN:
            dropped += 1
            logger.info(f"[filter:claim] DROP (no claim overlap) [{hit.host}] {hit.title!r}")
            continue
        scored.append((len(overlap), hit))
    scored.sort(key=lambda x: x[0], reverse=True)
    # Independent sources back a claim; one site's five pages on the same topic are one opinion. Bing's web feed
    # in particular fills a result list with a single site's pages, so each host supplies at most two.
    per_host: dict[str, int] = {}
    ranked: list[tuple[int, SearchHit]] = []
    for n, h in scored:
        site = _site_of(h.host)
        if per_host.get(site, 0) >= _CLAIM_MAX_PER_HOST:
            continue
        per_host[site] = per_host.get(site, 0) + 1
        ranked.append((n, h))
    final = [h for _, h in ranked[:limit]]
    logger.info(
        f"[filter:claim] kept {len(scored)}, dropped {dropped}, returning top {len(final)}; "
        + "; ".join(f"{n}:{h.host}" for n, h in ranked[:limit])
    )
    return final
