import hashlib
import logging
import random
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

from ddgs import DDGS
from ddgs.exceptions import TimeoutException

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
)

_STOPWORDS = frozenset({
    "market", "markets", "global", "industry", "the", "and", "of", "for", "by", "in", "on", "a", "an",
    "to", "with", "based", "services", "service", "solutions", "solution", "systems", "system", "other",
})

# "Ongoing trend" evidence must be genuinely current, not merely "not ancient" — a 2-year window
# let through anything short of actually stale. Only d.news() hits carry a real date at all (see
# SearchHit.is_news), so this cutoff only ever applies to those; d.text() hits have no date field
# in ddgs's response at all and are handled separately as background-only, never trend evidence.
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
_NEWS_BACKENDS = "bing,yahoo"
_TEXT_BACKENDS = "yahoo,grokipedia,wikipedia,startpage"

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


def _ddgs(d: DDGS, query: str) -> list[SearchHit]:
    hits = []
    for r in d.news(query, max_results=_PER_QUERY, backend=_NEWS_BACKENDS):
        hits.append(SearchHit(
            r["title"], r.get("body", ""), r["url"], r.get("source", ""), _parse_date(r.get("date")), is_news=True,
        ))
    time.sleep(_INTRA_QUERY_DELAY_S + random.uniform(0, 0.6))
    for r in d.text(query, max_results=_PER_QUERY, backend=_TEXT_BACKENDS):
        hits.append(SearchHit(r["title"], r.get("body", ""), r["href"], "", None, is_news=False))
    return hits


def _safe(d: DDGS, query: str, logger: logging.Logger = _null_logger) -> list[SearchHit]:
    for attempt in range(_QUERY_RETRIES + 1):
        try:
            return _ddgs(d, query)
        except TimeoutException as e:
            if attempt < _QUERY_RETRIES:
                wait = 3.0 * (attempt + 1)
                logger.warning(f"[search] query {query!r} timed out (attempt {attempt + 1}); retrying in {wait:.0f}s")
                time.sleep(wait)
            else:
                logger.warning(f"[search] query {query!r} failed ({type(e).__name__}: {e}) — continuing with 0 results", exc_info=True)
                return []
        except Exception as e:  # one failed provider/query must not sink the run; search is best-effort
            logger.warning(f"[search] query {query!r} failed ({type(e).__name__}: {e}) — continuing with 0 results", exc_info=True)
            return []


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


def filter_hits(
    hits: list[SearchHit], phrases: list[str], blocked_hosts: tuple[str, ...] = (),
    limit: int = 30, logger: logging.Logger = _null_logger,
) -> list[SearchHit]:
    """Dedup, drop blocked/stale sources, and keep hits sharing vocabulary with the market."""
    tokens, full_phrases = gate_terms(phrases)
    logger.info(f"[filter] gate tokens ({len(tokens)}): {sorted(tokens)}")
    logger.info(f"[filter] gate phrases ({len(full_phrases)}): {full_phrases}")
    cutoff = datetime.now(timezone.utc) - _MAX_NEWS_AGE
    blocked = _BLOCKED_HOSTS + tuple(h.lower() for h in blocked_hosts)

    seen: set[str] = set()
    scored: list[tuple[int, SearchHit]] = []
    kept = dropped = 0
    for hit in hits:
        if not hit.title or not hit.url:
            dropped += 1
            logger.info("[filter] DROP (missing title/url)")
            continue
        if any(hit.host == b or hit.host.endswith("." + b) for b in blocked):
            dropped += 1
            logger.info(f"[filter] DROP (blocked host {hit.host}): {hit.title!r}")
            continue
        if hit.date and hit.date < cutoff:
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
        if matched_tokens and len(matched_tokens) >= 2 or matched_phrase:
            kept += 1
            logger.info(
                f"[filter] KEEP (tokens={sorted(matched_tokens)}, phrase={matched_phrase!r}) "
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
