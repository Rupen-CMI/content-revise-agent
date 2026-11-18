"""Primary evidence gathering: Bing News RSS (keyword and site: queries) plus curated publisher RSS
feeds, and a full-text excerpt fetch for the articles that survive filtering. DDGS (search.py) is now
only a fallback — see pipeline.run_search_evidence.

Every network call here is deliberately slow and polite: Bing queries run strictly one at a time with a
wide randomized gap, any one host gets at most one request in flight with a minimum gap between
requests, and repeated Bing failures trip a circuit breaker so the run falls back to DDGS instead of
hammering a throttled endpoint. Sources come from source_registry.json (built and verified by
devtools/source_probe/); a site that blocks automated access (HTTP 403) is skipped, never worked around.
"""
# Deferred annotations: `_Bing.run` annotates `list[search.SearchHit]` AFTER the
# class defines a method also named `search`, which would otherwise resolve the
# annotation to that method (CPython class-body scoping), not the module — an
# import-time AttributeError only on modules importing feeds later in the chain.
from __future__ import annotations
import codecs
import json
import logging
import random
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx
import trafilatura

import search
import us_states
import sections
import sources

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

# --- politeness knobs. Measured Bing News RSS held up with no throttling at ~1-1.8s spacing; these are
# deliberately wider than that. Raise them rather than lower them if anything ever starts failing.
_BING_QUERY_DELAY_S = 1.8        # base gap between Bing queries (+ up to _BING_JITTER_S); measured: no throttling at 1-1.8s
_BING_JITTER_S = 1.0
_BING_RETRY_WAIT_S = 6.0         # wait after a throttled/failed Bing call, multiplied by the attempt number
_BING_RETRIES = 2
_BING_TRIP_AFTER = 3             # consecutive failed queries before Bing is abandoned for this run
_BING_MAX_KEYWORD_QUERIES = 10   # of the LLM's open-web queries (the old chain; the section search spends sections.keyword_query_budget)
_BING_MAX_SITE_QUERIES = sections.BING_SITE_QUERIES   # the three broadest news hosts (see site_queries)
_BING_EARLY_QUERIES = 4          # keyword queries after which the caller may already judge the market 'thin' (see gather)
_BING_MARKET = {"setmkt": "en-US", "cc": "US"}  # the articles have a U.S.-market section
_FEED_WORKERS = 6
_HOST_GAP_S = 2.0                # minimum gap between two requests to the same host (feeds and articles)
_ARTICLE_WORKERS = 6
_ARTICLE_STAGE_TIMEOUT_S = 45    # whole full-text stage; stragglers are dropped, not waited for
_REQUEST_TIMEOUT_S = 15
_MAX_PAGE_BYTES = 2_500_000
_MAX_FETCHES_PER_HOST = 5        # the rest of a site's hits keep their snippet; bounds time and per-site load
_EXCERPT_CHARS = 900
_EXCERPT_MIN_WORDS = 120         # shorter extractions are teasers/paywall stubs, not usable text
_BLOCKING_STATUSES = frozenset({401, 402, 403, 429, 451})

_REGISTRY_PATH = Path(__file__).parent / "source_registry.json"
_BATCH = sources._BATCH


@lru_cache(maxsize=1)
def _registry() -> dict:
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"feeds": [], "bing_hosts": {}, "fulltext_blocked": [], "max_items_per_feed": 60}


class _HostGate:
    """At most one in-flight request per host, and a minimum gap between consecutive requests to it."""

    def __init__(self, gap_s: float):
        self._gap = gap_s
        self._meta = threading.Lock()
        self._locks: dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._last: dict[str, float] = {}

    @contextmanager
    def slot(self, host: str):
        with self._meta:
            lock = self._locks[host]
        with lock:
            wait_s = self._last.get(host, 0.0) + self._gap - time.monotonic()
            if wait_s > 0:
                time.sleep(wait_s + random.uniform(0, 0.5))
            try:
                yield
            finally:
                self._last[host] = time.monotonic()


def _cp1252_fallback(err: UnicodeDecodeError):
    return err.object[err.start:err.end].decode("cp1252", errors="replace"), err.end


codecs.register_error("cp1252_fallback", _cp1252_fallback)


def _parse_feed(content: bytes):
    """feedparser on bytes turns a stray Windows-1252 byte inside a UTF-8 document (Bing does this with
    "(TM)" / "(R)" in press-release headlines) into U+FFFD; decoding those bytes as cp1252 keeps the character."""
    return feedparser.parse(content.decode("utf-8", errors="cp1252_fallback"))


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html or ""))).strip()


def _entry_date(e) -> datetime | None:
    tt = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*tt[:6], tzinfo=timezone.utc) if tt else None


def _entry_hit(e, source: str = "") -> search.SearchHit | None:
    link = (e.get("link") or "").strip()
    title = _plain(e.get("title", ""))
    if not link or not title:
        return None
    if urlparse(link).netloc.lower().endswith("bing.com"):
        # Bing wraps each article in a click-tracking link whose url= parameter is the real address.
        link = parse_qs(urlparse(link).query).get("url", [link])[0]
    date = _entry_date(e)
    return search.SearchHit(
        title=title, snippet=_plain(e.get("summary", ""))[:500], url=link,
        source=source or e.get("news_source", "") or "", date=date, is_news=date is not None,
    )


# ------------------------------------------------------------------------------------------- Bing News

# One Bing request at a time with a minimum gap between ANY two of them. The market stage and the claim-backing
# stage run in separate threads; without a shared gate their two Bing streams would interleave unspaced.
_BING_LOCK = threading.Lock()
_bing_last_end = 0.0


@contextmanager
def _bing_turn():
    global _bing_last_end
    with _BING_LOCK:
        wait_s = _bing_last_end + _BING_QUERY_DELAY_S + random.uniform(0, _BING_JITTER_S) - time.monotonic()
        if _bing_last_end and wait_s > 0:
            time.sleep(wait_s)
        try:
            yield
        finally:
            _bing_last_end = time.monotonic()


class _Bing:
    """Sequential Bing News RSS client with retry/backoff and a circuit breaker."""

    def __init__(self, client: httpx.Client, logger: logging.Logger, unquoted_retries: int = sections.UNQUOTED_RETRIES):
        self._client, self._logger = client, logger
        self._failed_in_a_row = 0
        self.disabled = False
        self.unquoted_retries = unquoted_retries   # what is left of this run's allowance (run)
        self.sections_with_hits: set[str] = set()  # sections (or "" for a query with none) that have a raw hit already (run)
        self.queries = 0                           # queries sent, retries included (search)

    def _get(self, query: str, web: bool = False) -> list[search.SearchHit] | None:
        # Bing's web feed is unreliable for automated requests (some queries get pages matching only the first word), so it
        # runs beside Google News, never alone; the callers' relevance filters drop what is off topic.
        url = "https://www.bing.com/search" if web else "https://www.bing.com/news/search"
        kind = "bing_web" if web else "bing_news"
        # Dev-only disk cache (config.SEARCH_CACHE): a hit short-circuits BEFORE the politeness gate
        # and the retry/breaker machinery — a repeated dev run makes no live Bing call for a query
        # it already made within the TTL. Only successes are stored (below), so a cached entry can
        # never represent a throttled/failed request, and the breaker logic stays live-path-only.
        cached = search.cache_lookup(kind, query)
        if cached is not None:
            self._logger.info(f"[cache] {kind} {query!r}: {len(cached)} cached result(s)")
            return cached
        for attempt in range(_BING_RETRIES + 1):
            try:
                with _bing_turn():
                    r = self._client.get(url, params={"q": query, "format": "rss", **_BING_MARKET})
                if r.status_code == 200:
                    hits = [h for e in _parse_feed(r.content).entries if (h := _entry_hit(e))]
                    search.cache_store(kind, query, hits)
                    return hits
                self._logger.warning(f"[bing] {query!r}: HTTP {r.status_code} (attempt {attempt + 1})")
            except httpx.HTTPError as e:
                self._logger.warning(f"[bing] {query!r}: {type(e).__name__} (attempt {attempt + 1})")
            if attempt < _BING_RETRIES:
                time.sleep(_BING_RETRY_WAIT_S * (attempt + 1))
        return None

    def search(self, query: str, web: bool = False) -> list[search.SearchHit]:
        if self.disabled:
            return []
        hits = self._get(query, web)
        self.queries += 1
        if hits is None:
            self._failed_in_a_row += 1
            if self._failed_in_a_row >= _BING_TRIP_AFTER:
                self.disabled = True
                self._logger.warning(f"[bing] {self._failed_in_a_row} failed queries in a row — abandoning Bing for this run")
            return []
        self._failed_in_a_row = 0
        # Every hit carries the query that found it (the query-yield measurement reads it). A page two queries found is
        # two objects; the duplicate merge keeps the first copy, so the first query wins.
        for h in hits:
            if not h.query:
                h.query = query
        self._logger.info(f"[bing{' web' if web else ''}] {query!r} -> {len(hits)} result(s)")
        return hits

    def run(
        self, queries: list[str], sections_for: dict[str, list[str]] | None = None, default: tuple[str, ...] = (),
        on_batch: Callable[[list[search.SearchHit]], None] | None = None,
    ) -> list[search.SearchHit]:
        """Each query in turn. With sections_for ({lower-cased query: [section, ...]}) every hit carries the sections of the
        query that found it (default for a query not in the map). on_batch, if given, is called with this call's hits so far
        after every query, so the caller can start work on them while the remaining queries run."""
        out: list[search.SearchHit] = []
        for i, q in enumerate(queries):
            if self.disabled:
                break
            tag = (sections_for or {}).get(q.lower()) or list(default)
            keys = set(tag) or {""}
            batch = self.search(q)
            if not batch and '"' in q and "site:" not in q and self.unquoted_retries > 0 and not keys & self.sections_with_hits:
                # A quoted phrase plus extra words is the most over-constrained shape there is
                # (15 of 24 Bing keyword queries in one run returned nothing): retry once unquoted,
                # where a multi-word term is an implicit AND rather than a hard requirement.
                # filter_hits re-checks relevance, so widening the query is safe. Each retry costs a query, so it is spent
                # only on a section that has no hit at all yet, and at most sections.UNQUOTED_RETRIES times a run.
                self.unquoted_retries -= 1
                unquoted = " ".join(q.replace('"', "").split())
                self._logger.info(f"[bing] {q!r} gave 0 result(s) and its section has no hit yet; retrying without the quotes: {unquoted!r}")
                batch = self.search(unquoted)
                for h in batch:
                    h.query = q    # the yield measurement counts the retry for the query the plan wrote
            for h in batch:
                h.add_sections(tag)
            if batch:
                self.sections_with_hits |= keys
            out += batch
            if on_batch is not None:
                on_batch(list(out))
            if i < len(queries) - 1:
                time.sleep(_BING_QUERY_DELAY_S + random.uniform(0, _BING_JITTER_S))
        return out


# ------------------------------------------------------------------------------------------ Google News
# Bing's WEB search feed now answers automated requests with pages that match only the query's first word, so it is
# not used any more. Google News RSS is free, answers keyword queries properly, and indexes rival research firms'
# report pages as well as news, which is what the web-style queries (competitor reports, figure gap, claim backing)
# need. Its <link> is a news.google.com redirect that must be decoded to the real address; that costs two requests,
# so a hit first carries a provisional URL on its real host (the <source url> names it) and is decoded only after the
# cheap title/host filters (resolve_hits).

_GNEWS_RSS_URL = "https://news.google.com/rss/search"
# The /rss/articles/ page carries the same signature and timestamp as /articles/, which Google redirects to its "sorry" captcha page.
_GNEWS_ARTICLE_URL = "https://news.google.com/rss/articles/{gid}?hl=en-US&gl=US&ceid=US:en"
_GNEWS_BATCH_URL = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
_GNEWS_PARAMS = {"hl": "en-US", "gl": "US", "ceid": "US:en"}
_GNEWS_QUERY_DELAY_S = 1.5       # base gap between Google News queries (+ up to _BING_JITTER_S)
_GNEWS_RETRY_WAIT_S = 5.0
_GNEWS_RETRIES = 1
_GNEWS_TRIP_AFTER = 3            # consecutive failed queries before Google News is abandoned for this run
_GNEWS_DECODE_GAP_S = 0.6        # minimum gap between two redirect-decoding requests
_GNEWS_DECODE_WORKERS = 3
_GNEWS_MARK = "#gnews-"          # a provisional URL is "https://<host>/#gnews-<article id>"
_GNEWS_SIG_RE = re.compile(r'data-n-a-sg="([^"]+)"')
_GNEWS_TS_RE = re.compile(r'data-n-a-ts="([^"]+)"')

_GNEWS_LOCK = threading.Lock()
_gnews_last_end = 0.0
_GNEWS_DECODE_GATE = _HostGate(_GNEWS_DECODE_GAP_S)
_GNEWS_DECODED: dict[str, str] = {}   # article id -> real URL, for the life of the process ("" = could not be decoded)


@contextmanager
def _gnews_turn():
    """One Google News search at a time with a minimum gap between any two (the claim, question and market stages run in threads)."""
    global _gnews_last_end
    with _GNEWS_LOCK:
        wait_s = _gnews_last_end + _GNEWS_QUERY_DELAY_S + random.uniform(0, _BING_JITTER_S) - time.monotonic()
        if _gnews_last_end and wait_s > 0:
            time.sleep(wait_s)
        try:
            yield
        finally:
            _gnews_last_end = time.monotonic()


def _strip_publisher(title: str, source: str) -> str:
    """Google News titles end with " - Publisher Name"; the publisher is not part of the headline."""
    title = title.strip()
    if source:
        suffix = f" - {source.strip()}"
        if title.lower().endswith(suffix.lower()):
            return title[:-len(suffix)].rstrip()
    return title


def _gnews_hit(e) -> search.SearchHit | None:
    link = (e.get("link") or "").strip()
    if "/articles/" not in link:
        return None
    gid = link.split("/articles/")[1].split("?")[0]
    src = e.get("source") or {}
    src_name = _plain(src.get("title", "")) if hasattr(src, "get") else ""
    src_host = urlparse(src.get("href", "") if hasattr(src, "get") else "").netloc.lower()
    title = _strip_publisher(_plain(e.get("title", "")), src_name)
    if not gid or not src_host or not title:
        return None
    snippet = _plain(e.get("summary", ""))
    # Google's description is the headline and publisher again, which says nothing a snippet should.
    snippet = "" if snippet.lower().startswith(title.lower()[:40]) else snippet[:500]
    date = _entry_date(e)
    return search.SearchHit(
        title=title, snippet=snippet, url=f"https://{src_host}/{_GNEWS_MARK}{gid}",
        source=src_name, date=date, is_news=date is not None,
    )


def _gnews_id(url: str) -> str:
    return url.split(_GNEWS_MARK, 1)[1] if _GNEWS_MARK in url else ""


def _parse_batchexecute(text: str) -> str:
    """The real article URL in a batchexecute answer, or '' when the answer has none."""
    try:
        return str(json.loads(json.loads(text.split("\n\n")[1])[0][2])[1])
    except (IndexError, KeyError, TypeError, ValueError):
        return ""


def _decode_gnews(client: httpx.Client, gid: str) -> str:
    """The real URL behind a Google News article id: the article page hands out a signature and timestamp, which the
    batchexecute endpoint exchanges for the address. '' when it cannot be decoded. Never raises."""
    if gid in _GNEWS_DECODED:
        return _GNEWS_DECODED[gid]
    url = ""
    try:
        with _GNEWS_DECODE_GATE.slot("news.google.com"):
            page = client.get(_GNEWS_ARTICLE_URL.format(gid=gid))
        sig, ts = _GNEWS_SIG_RE.search(page.text), _GNEWS_TS_RE.search(page.text)
        if page.status_code == 200 and sig and ts:
            inner = (
                '["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
                f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{gid}",{ts.group(1)},"{sig.group(1)}"]'
            )
            req = [[["Fbv4je", inner, None, "generic"]]]
            with _GNEWS_DECODE_GATE.slot("news.google.com"):
                r = client.post(
                    _GNEWS_BATCH_URL, data={"f.req": json.dumps(req)},
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                )
            if r.status_code == 200:
                url = _parse_batchexecute(r.text)
    except Exception:
        url = ""
    if url:
        _GNEWS_DECODED[gid] = url   # a failure is not remembered: it may be a passing block
    return url


def resolve_hits(hits: list[search.SearchHit], logger: logging.Logger = _null_logger) -> list[search.SearchHit]:
    """Replace the provisional URL of every Google News hit with the real one (a few in parallel, politely). A hit that cannot
    be decoded is dropped; so is one whose real page is a second copy of another hit's. Hits from other sources pass through.
    Call it on the hits that survived the cheap title/host filters, not on a whole raw batch."""
    pending = [h for h in hits if _GNEWS_MARK in h.url]
    if pending:
        with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client, \
                ThreadPoolExecutor(_GNEWS_DECODE_WORKERS) as ex:
            real = list(ex.map(lambda h: _decode_gnews(client, _gnews_id(h.url)), pending))
        for h, url in zip(pending, real):
            h.url = url
        logger.info(f"[gnews] decoded {sum(1 for u in real if u)} of {len(pending)} link(s)")
    out, seen = [], set()
    for h in hits:
        if h.url and h.url not in seen and _GNEWS_MARK not in h.url:
            seen.add(h.url)
            out.append(h)
    return out


class _GoogleNews:
    """Sequential Google News RSS client with retry/backoff and a circuit breaker; hits carry provisional URLs (resolve_hits)."""

    def __init__(self, client: httpx.Client, logger: logging.Logger):
        self._client, self._logger = client, logger
        self._failed_in_a_row = 0
        self.disabled = False

    def _get(self, query: str) -> list[search.SearchHit] | None:
        cached = search.cache_lookup("gnews", query)
        if cached is not None:
            self._logger.info(f"[cache] gnews {query!r}: {len(cached)} cached result(s)")
            return cached
        for attempt in range(_GNEWS_RETRIES + 1):
            try:
                with _gnews_turn():
                    r = self._client.get(_GNEWS_RSS_URL, params={"q": query, **_GNEWS_PARAMS})
                if r.status_code == 200:
                    hits = [h for e in _parse_feed(r.content).entries if (h := _gnews_hit(e))]
                    search.cache_store("gnews", query, hits)
                    return hits
                self._logger.warning(f"[gnews] {query!r}: HTTP {r.status_code} (attempt {attempt + 1})")
            except httpx.HTTPError as e:
                self._logger.warning(f"[gnews] {query!r}: {type(e).__name__} (attempt {attempt + 1})")
            if attempt < _GNEWS_RETRIES:
                time.sleep(_GNEWS_RETRY_WAIT_S * (attempt + 1))
        return None

    def search(self, query: str) -> list[search.SearchHit]:
        if self.disabled:
            return []
        hits = self._get(query)
        if hits is None:
            self._failed_in_a_row += 1
            if self._failed_in_a_row >= _GNEWS_TRIP_AFTER:
                self.disabled = True
                self._logger.warning(f"[gnews] {self._failed_in_a_row} failed queries in a row — abandoning Google News for this run")
            return []
        self._failed_in_a_row = 0
        for h in hits:
            h.query = query
        self._logger.info(f"[gnews] {query!r} -> {len(hits)} result(s)")
        return hits


class ClaimWebSearch:
    """Web-style search for claim backing: explainer, reference and vendor-documentation pages that a news index rarely
    carries. One instance per run, on Google News (free; Bing's web feed no longer answers). It indexes news and
    publishers' pages, not the whole web, so callers must relevance-filter (search.filter_claim_hits does). Each search
    returns hits with their real URLs (decoded after dropping what the title and host cannot use)."""

    def __init__(self, logger: logging.Logger):
        self._logger = logger
        self._client = httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True)
        self._gnews = _GoogleNews(self._client, logger)
        self._bing = _Bing(self._client, logger)

    def search(self, query: str, resolve: bool = True, max_resolved: int = 8) -> list[search.SearchHit]:
        hits = _dedupe(self._bing.search(query, web=True) + self._gnews.search(query))
        return resolve_hits(hits[:max_resolved], self._logger) if resolve else hits

    @property
    def disabled(self) -> bool:
        return self._gnews.disabled and self._bing.disabled

    def close(self) -> None:
        self._client.close()


def _dedupe(hits: list[search.SearchHit]) -> list[search.SearchHit]:
    """First copy of each URL, in order (Bing web and Google News often return the same report page)."""
    seen: set[str] = set()
    return [h for h in hits if not (h.url in seen or seen.add(h.url))]


def web_search(
    queries: list[str], logger: logging.Logger = _null_logger, resolve: bool = True, max_resolved: int = 8,
) -> list[search.SearchHit]:
    """Web-style queries on Bing web and Google News, one polite client. With resolve=False the hits keep provisional URLs on their real
    hosts (enough for host and title filters); call resolve_hits on the survivors. Returns raw, unfiltered hits."""
    out: list[search.SearchHit] = []
    with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
        gnews, bing = _GoogleNews(client, logger), _Bing(client, logger)
        for q in queries:
            out += bing.search(q, web=True) + gnews.search(q)
    out = _dedupe(out)
    return resolve_hits(out[:max_resolved * max(1, len(queries))], logger) if resolve else out


def bing_search(
    news_queries: list[str], web_queries: list[str], logger: logging.Logger = _null_logger,
    sections_for: dict[str, list[str]] | None = None, resolve: bool = True,
) -> list[search.SearchHit]:
    """Bing News queries, then the web-style queries on Google News (see web_search; Bing's web feed is no longer used). Used
    by the U.S. data search, which wants news AND statistics pages, and by the section top-up (sections_for tags each hit
    with the sections of the query that found it). Returns raw, unfiltered hits; resolve=False leaves Google News hits with
    provisional URLs (resolve_hits)."""
    out: list[search.SearchHit] = []
    if news_queries:
        with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            out = _Bing(client, logger).run(news_queries, sections_for)
    if web_queries:
        out += web_search(web_queries, logger, resolve=resolve)
    return out


def site_queries(core: str, group_ids: list[str]) -> list[str]:
    """Bing `site:` queries for sites that have no RSS feed of their own: the general business and deal
    press always, plus — per chosen industry group, best-first — that group's feed-less trade-press sites
    and regulators that Bing is known to index."""
    core = core.strip()
    queries = [
        sources._site_query(core, sources.GENERAL_BUSINESS_HOSTS[:_BATCH]),
        sources._site_query(core, sources.GENERAL_BUSINESS_HOSTS[_BATCH:2 * _BATCH]),
        sources._site_query(core, sources.DEAL_FUNDING_HOSTS, "funding"),
    ]
    bing_hosts = _registry()["bing_hosts"]
    for pid in [i for i in dict.fromkeys(group_ids) if i in sources.PACKS][:3]:
        pack = sources.PACKS[pid]
        hosts = [h for h in pack["trade"] + pack["regulators"] if h in bing_hosts]
        for i in range(0, min(len(hosts), 2 * _BATCH), _BATCH):
            queries.append(sources._site_query(core, hosts[i:i + _BATCH]))
    return queries[:_BING_MAX_SITE_QUERIES]


# ---------------------------------------------------------------------------------------------- RSS

def _topical_filter(phrases: list[str], key_players: list[str], topic_terms: list[str] = ()):
    """Cheap pre-filter for feed items so only plausibly relevant ones reach filter_hits (which applies the
    real topical gate): the item must mention one of the market's identifying words (prefix match, so
    'wealthtech' counts for 'wealth') or a key player."""
    freq = search._catalog_token_freq()
    stems: set[str] = set()
    for p in phrases:
        g = search._gate_for_phrase(p, freq)
        if g:
            head, required, _, _ = g
            stems |= set(required or [head])
    players = search._player_patterns(list(key_players))
    topics = search.compile_topic_terms(list(topic_terms))

    def ok(hit: search.SearchHit) -> bool:
        text = f"{hit.title} {hit.snippet}"
        words = search._content_tokens(text)
        return (
            any(search.stem_hit(w, s) for w in words for s in stems)
            or any(rx.search(text) for _, rx in players)
            or any(rx.search(text) for _, rx in topics)
        )

    return ok


def _fetch_feeds(feed_defs: list[dict], keep, gate: _HostGate, logger: logging.Logger) -> list[search.SearchHit]:
    cap = _registry().get("max_items_per_feed", 60)

    def one(f: dict) -> list[search.SearchHit]:
        try:
            with gate.slot(urlparse(f["url"]).netloc):
                r = client.get(f["url"])
            if r.status_code != 200:
                logger.warning(f"[rss] {f['name']}: HTTP {r.status_code}")
                return []
            hits = [h for e in _parse_feed(r.content).entries[:cap] if (h := _entry_hit(e, f["name"]))]
            return [h for h in hits if keep(h)]
        except Exception as e:  # one dead feed must never sink the run
            logger.warning(f"[rss] {f['name']}: {type(e).__name__}")
            return []

    with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client, \
            ThreadPoolExecutor(_FEED_WORKERS) as ex:
        results = list(ex.map(one, feed_defs))
    hits = [h for batch in results for h in batch]
    logger.info(f"[rss] {len(feed_defs)} feed(s) -> {len(hits)} topically plausible item(s)")
    return hits


# A company's own blog is that company's marketing, not trade press (search.py drops key players' own sites for the same reason).
_VENDOR_BLOG_HOSTS = frozenset({"aws.amazon.com", "blog.google", "blogs.microsoft.com", "blogs.nvidia.com", "openai.com"})


def sector_hosts(group_ids: list[str]) -> frozenset[str]:
    """Trade-press sites of the chosen industry groups (curated packs, their feeds, and the feed-less sites Bing
    indexes). Deliberately NOT regulators/central banks/EU bodies (their feeds carry everything they publish, e.g.
    "A Geological Service for Europe") and not the general business press: those still need their words to match."""
    chosen = {g for g in group_ids if g in sources.PACKS}
    official = {h for p in sources.PACKS.values() for h in p["regulators"]}

    def trade_press(host: str) -> bool:
        return host not in official and host not in _VENDOR_BLOG_HOSTS and not any(host.endswith(sfx) for sfx in sources.TRUSTED_SUFFIXES) and not host.endswith(".europa.eu")

    hosts = {h for g in chosen for h in sources.PACKS[g]["trade"]}
    reg = _registry()
    hosts |= {f["host"] for f in reg["feeds"] if not f["general"] and chosen & set(f["groups"])}
    hosts |= {h for h, v in reg["bing_hosts"].items() if chosen & set(v["groups"])}
    return frozenset(h for h in hosts if trade_press(h))


def feeds_for(group_ids: list[str]) -> list[dict]:
    chosen = set(group_ids)
    return [f for f in _registry()["feeds"] if f["general"] or chosen & set(f["groups"])]


# --------------------------------------------------------------------------------------------- gather

def gather(
    market_name: str, core: str, queries: list[str], group_ids: list[str], key_players: list[str],
    logger: logging.Logger = _null_logger, topic_terms: list[str] = (),
    after_keywords: "Callable[[list[search.SearchHit]], None] | None" = None,
    query_sections: dict[str, list[str]] | None = None, max_keyword_queries: int = _BING_MAX_KEYWORD_QUERIES,
    on_hits: "Callable[[list[search.SearchHit]], None] | None" = None,
) -> list[search.SearchHit]:
    """Bing keyword queries + Bing site: queries + the relevant publisher feeds. Returns raw (unfiltered)
    hits, possibly empty; the caller filters them and decides whether the DDGS fallback is needed.
    after_keywords, if given, is called with the Bing hits after the first few keyword queries, before the rest of
    them and the site: queries run, so the caller can start slow work (the DDGS fallback) that overlaps with the rest.
    query_sections ({lower-cased query: [section, ...]}, the Evidence step's section search): each keyword hit carries the
    sections of the query that found it; the site: queries and the publisher feeds are not about one section, so their
    hits carry "market".
    on_hits, if given, is called after EVERY Bing query with all the Bing hits so far, so the caller can start reading the
    pages of the hits that look usable while the remaining (strictly sequential) queries run."""
    feed_defs = feeds_for(group_ids)
    keyword_queries = queries[:max_keyword_queries]
    general = ("market",) if query_sections is not None else ()
    site_qs = site_queries(core, group_ids)
    logger.info(f"[gather] {len(feed_defs)} feed(s), {len(keyword_queries) + len(site_qs)} Bing query(ies) for groups {group_ids or '(none)'}")
    keep = _topical_filter([market_name], key_players, topic_terms)
    gate = _HostGate(_HOST_GAP_S)
    with ThreadPoolExecutor(1) as ex:
        rss_future = ex.submit(_fetch_feeds, feed_defs, keep, gate, logger)
        bing_started = time.monotonic()
        with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            bing = _Bing(client, logger)
            bing_hits = bing.run(keyword_queries[:_BING_EARLY_QUERIES], query_sections, general,
                                 on_batch=(lambda batch: on_hits(batch)) if on_hits else None)
            if after_keywords is not None:
                after_keywords(list(bing_hits))
            earlier = list(bing_hits)
            bing_hits += bing.run(keyword_queries[_BING_EARLY_QUERIES:] + site_qs, query_sections, general,
                                  on_batch=(lambda batch: on_hits(earlier + batch)) if on_hits else None)
        logger.info(f"[timing] Bing queries: {bing.queries} in {time.monotonic() - bing_started:.1f}s")
        bing_hits += _us_gnews_hits(keyword_queries, query_sections, logger)
        rss_hits = rss_future.result()
        for h in rss_hits:
            h.add_sections(general)
    logger.info(f"[gather] Bing {len(bing_hits)} + RSS {len(rss_hits)} raw hit(s){' (Bing was abandoned)' if bing.disabled else ''}")
    return bing_hits + rss_hits


_US_GNEWS_RESOLVE = 8   # U.S.-query hits whose links are decoded; the rest of the batch is dropped
_US_THIN_HEADLINES = 3  # fewer U.S.-naming headlines than this and the state-level queries run too
_US_WORDS_RE = re.compile(r'"|\bU\.S\.|\bUSA\b|\bUS\b|(?i:\bunited states\b)|\bAmerican\b')


def us_state_queries(us_queries: list[str]) -> list[str]:
    """State-level queries for the U.S. section when U.S.-wide news is thin: the shortest U.S. query without its U.S. words,
    then a few large states joined by OR (Google News reads OR)."""
    bases = [" ".join(_US_WORDS_RE.sub(" ", q).split()) for q in us_queries]
    base = min((b for b in bases if b), key=len, default="")
    return [f"{base} {' OR '.join(group)}" for group in us_states.QUERY_STATE_GROUPS] if base else []


def _us_gnews_hits(queries: list[str], query_sections: dict[str, list[str]] | None, logger: logging.Logger) -> list[search.SearchHit]:
    """The U.S. section's queries on Google News as well: Bing News answers them with a hit or none (a niche market has little
    U.S.-labelled news there), Google News with dozens. Hits that name the U.S. in their headline come first, then the first
    _US_GNEWS_RESOLVE of them get real links. Best-effort: a failure here must not lose the Bing and feed hits."""
    us_qs = [q for q in queries if "us" in ((query_sections or {}).get(q.lower()) or [])]
    if not us_qs:
        return []
    try:
        with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            gnews = _GoogleNews(client, logger)
            hits: list[search.SearchHit] = []
            for q in us_qs:
                for h in gnews.search(q):
                    h.add_sections(query_sections[q.lower()])
                    hits.append(h)
        names_us = re.compile(r"\bU\.S\.|\bUS\b|\bUSA\b|(?i:united states)|\bAmerican\b")
        resolve = _US_GNEWS_RESOLVE
        if sum(1 for h in hits if names_us.search(f"{h.title} {h.snippet}")) < _US_THIN_HEADLINES:
            # U.S.-wide news is thin: ask about the largest states too (a state's own development is U.S. evidence)
            tags = query_sections[us_qs[0].lower()]
            state_qs = us_state_queries(us_qs)
            for q in state_qs:
                for h in gnews.search(q):
                    h.add_sections(tags)
                    hits.append(h)
            resolve += 4
            logger.info(f"[us-search] few U.S.-wide headlines; state-level queries added: {state_qs}")
        # stable: the engine's order within each group; a headline naming the U.S. or one of its states first
        hits.sort(key=lambda h: not (names_us.search(f"{h.title} {h.snippet}") or us_states.STATE_RE.search(f"{h.title} {h.snippet}")))
        return resolve_hits(hits[:resolve], logger)
    except Exception:
        logger.warning("[gnews] the U.S. queries failed", exc_info=True)
        return []


# ----------------------------------------------------------------------------------------- full text

def _best_excerpt(text: str, stems: set[str]) -> str:
    """The article's opening paragraph plus the paragraphs that mention the market and carry figures,
    in original order, capped at _EXCERPT_CHARS. Deterministic — no LLM involved."""
    paras = [p.strip() for p in text.split("\n") if len(p.split()) >= 12]
    if not paras:
        return ""

    def score(p: str) -> int:
        words = search._content_tokens(p)
        return 2 * sum(1 for w in words if any(w.startswith(s) for s in stems)) + len(re.findall(r"\d", p)) // 2

    chosen = {0}
    budget = _EXCERPT_CHARS - len(paras[0])
    for i in sorted(range(1, len(paras)), key=lambda i: -score(paras[i])):
        if budget < 150:
            break
        if score(paras[i]) > 0:
            chosen.add(i)
            budget -= len(paras[i])
    out = " ".join(paras[i] for i in sorted(chosen))
    return out[:_EXCERPT_CHARS].rsplit(" ", 1)[0] if len(out) > _EXCERPT_CHARS else out


class ExcerptReader:
    """Reads the article pages of hits (politely) and stores an excerpt on hit.excerpt. Pages can be read as the hits arrive
    (start), while the sequential Bing queries are still running, and `finish` then reads whatever was not started and waits
    for the stragglers. Every host keeps its one-request-at-a-time gap (_HostGate) whichever way a page is started. A hit whose
    page can't be read keeps only its headline and snippet. Never raises."""

    def __init__(self, market_name: str, logger: logging.Logger = _null_logger, focus: str = ""):
        self._logger = logger
        self._blocked = set(_registry()["fulltext_blocked"])
        g = search._gate_for_phrase(market_name, search._catalog_token_freq())
        self._stems = set(g[1] or [g[0]]) if g else set()
        if focus:  # claim backing: pick the paragraphs that talk about the claim itself, not about a market
            self._stems = search._content_tokens(focus)
        self._gate = _HostGate(_HOST_GAP_S)
        self._runtime_blocked: set[str] = set()
        self._stats = defaultdict(int)
        self._client = httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True)
        self._pool = ThreadPoolExecutor(_ARTICLE_WORKERS)
        self._futures: dict[str, Future] = {}
        self._started_per_host: dict[str, int] = defaultdict(int)

    def _one(self, h: search.SearchHit) -> None:
        host = h.host
        if host in self._blocked or host in self._runtime_blocked or h.url.lower().split("?")[0].endswith((".pdf", ".xlsx", ".zip")):
            self._stats["skipped"] += 1
            return
        try:
            with self._gate.slot(host):
                if host in self._runtime_blocked:
                    self._stats["skipped"] += 1
                    return
                r = self._client.get(h.url)
            if r.status_code in _BLOCKING_STATUSES:
                self._runtime_blocked.add(host)
                self._stats["blocked"] += 1
                return
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                self._stats["failed"] += 1
                return
            text = trafilatura.extract(r.text[:_MAX_PAGE_BYTES], include_comments=False, include_tables=False) or ""
            if len(text.split()) < _EXCERPT_MIN_WORDS:
                self._stats["thin"] += 1
                return
            h.excerpt = _best_excerpt(text, self._stems)
            self._stats["ok"] += 1
        except Exception:
            self._stats["failed"] += 1

    def _start(self, h: search.SearchHit) -> Future:
        if h.url not in self._futures:
            self._started_per_host[h.host] += 1
            self._futures[h.url] = self._pool.submit(self._one, h)
        return self._futures[h.url]

    def start(self, hits: list[search.SearchHit]) -> None:
        """Start reading these hits' pages in the background (a page already started is not read twice). At most
        _MAX_FETCHES_PER_HOST pages of one site are started this way; hits arrive best-first."""
        for h in hits:
            if h.url not in self._futures and self._started_per_host[h.host] < _MAX_FETCHES_PER_HOST:
                self._start(h)

    def finish(self, hits: list[search.SearchHit]) -> None:
        """Read what is left of the final hits (at most _MAX_FETCHES_PER_HOST per site, the best-first ones), wait up to
        _ARTICLE_STAGE_TIMEOUT_S for the readers, then stop them. Pages started early for a hit that did not make the final
        list are simply not used."""
        per_host: dict[str, int] = defaultdict(int)
        to_fetch = []
        for h in hits:  # hits arrive best-first, so a site's strongest articles are the ones that get read
            per_host[h.host] += 1
            if per_host[h.host] <= _MAX_FETCHES_PER_HOST:
                to_fetch.append(h)
        try:
            futures = [self._start(h) for h in to_fetch]
            _, pending = wait(futures, timeout=_ARTICLE_STAGE_TIMEOUT_S)
            if pending:
                self._logger.warning(f"[fulltext] {len(pending)} fetch(es) still running after {_ARTICLE_STAGE_TIMEOUT_S}s — dropped")
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._client.close()
        self._logger.info(f"[fulltext] {len(to_fetch)} of {len(hits)} hit(s) fetched: {dict(self._stats)}")

    def close(self) -> None:
        """Stop the readers without waiting (the search failed before finish)."""
        self._pool.shutdown(wait=False, cancel_futures=True)
        self._client.close()


def attach_excerpts(
    hits: list[search.SearchHit], market_name: str, logger: logging.Logger = _null_logger, focus: str = "",
) -> None:
    """Fetch each hit's article page (politely) and store an excerpt on hit.excerpt. A hit whose page can't
    be read keeps only its headline and snippet. Never raises."""
    if not hits:
        return
    ExcerptReader(market_name, logger, focus).finish(hits)
