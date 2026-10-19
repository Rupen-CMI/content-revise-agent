"""Primary evidence gathering: Bing News RSS (keyword and site: queries) plus curated publisher RSS
feeds, and a full-text excerpt fetch for the articles that survive filtering. DDGS (search.py) is now
only a fallback — see pipeline.run_search_evidence.

Every network call here is deliberately slow and polite: Bing queries run strictly one at a time with a
wide randomized gap, any one host gets at most one request in flight with a minimum gap between
requests, and repeated Bing failures trip a circuit breaker so the run falls back to DDGS instead of
hammering a throttled endpoint. Sources come from source_registry.json (built and verified by
devtools/source_probe/); a site that blocks automated access (HTTP 403) is skipped, never worked around.
"""
import codecs
import json
import logging
import random
import re
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, wait
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
import sources

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}

# --- politeness knobs. Measured Bing News RSS held up with no throttling at ~1-1.8s spacing; these are
# deliberately wider than that. Raise them rather than lower them if anything ever starts failing.
_BING_QUERY_DELAY_S = 2.5        # base gap between Bing queries (+ up to _BING_JITTER_S)
_BING_JITTER_S = 1.5
_BING_RETRY_WAIT_S = 6.0         # wait after a throttled/failed Bing call, multiplied by the attempt number
_BING_RETRIES = 2
_BING_TRIP_AFTER = 3             # consecutive failed queries before Bing is abandoned for this run
_BING_MAX_KEYWORD_QUERIES = 10   # of the LLM's open-web queries
_BING_MAX_SITE_QUERIES = 9
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

class _Bing:
    """Sequential Bing News RSS client with retry/backoff and a circuit breaker."""

    def __init__(self, client: httpx.Client, logger: logging.Logger):
        self._client, self._logger = client, logger
        self._failed_in_a_row = 0
        self.disabled = False

    def _get(self, query: str) -> list[search.SearchHit] | None:
        for attempt in range(_BING_RETRIES + 1):
            try:
                r = self._client.get(
                    "https://www.bing.com/news/search", params={"q": query, "format": "rss", **_BING_MARKET}
                )
                if r.status_code == 200:
                    return [h for e in _parse_feed(r.content).entries if (h := _entry_hit(e))]
                self._logger.warning(f"[bing] {query!r}: HTTP {r.status_code} (attempt {attempt + 1})")
            except httpx.HTTPError as e:
                self._logger.warning(f"[bing] {query!r}: {type(e).__name__} (attempt {attempt + 1})")
            if attempt < _BING_RETRIES:
                time.sleep(_BING_RETRY_WAIT_S * (attempt + 1))
        return None

    def search(self, query: str) -> list[search.SearchHit]:
        if self.disabled:
            return []
        hits = self._get(query)
        if hits is None:
            self._failed_in_a_row += 1
            if self._failed_in_a_row >= _BING_TRIP_AFTER:
                self.disabled = True
                self._logger.warning(f"[bing] {self._failed_in_a_row} failed queries in a row — abandoning Bing for this run")
            return []
        self._failed_in_a_row = 0
        self._logger.info(f"[bing] {query!r} -> {len(hits)} result(s)")
        return hits

    def run(self, queries: list[str]) -> list[search.SearchHit]:
        out: list[search.SearchHit] = []
        for i, q in enumerate(queries):
            if self.disabled:
                break
            out += self.search(q)
            if i < len(queries) - 1:
                time.sleep(_BING_QUERY_DELAY_S + random.uniform(0, _BING_JITTER_S))
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

def _topical_filter(phrases: list[str], key_players: list[str]):
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

    def ok(hit: search.SearchHit) -> bool:
        text = f"{hit.title} {hit.snippet}"
        words = search._content_tokens(text)
        return any(w.startswith(s) for w in words for s in stems) or any(rx.search(text) for _, rx in players)

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
    logger: logging.Logger = _null_logger,
) -> list[search.SearchHit]:
    """Bing keyword queries + Bing site: queries + the relevant publisher feeds. Returns raw (unfiltered)
    hits, possibly empty; the caller filters them and decides whether the DDGS fallback is needed."""
    feed_defs = feeds_for(group_ids)
    bing_queries = queries[:_BING_MAX_KEYWORD_QUERIES] + site_queries(core, group_ids)
    logger.info(f"[gather] {len(feed_defs)} feed(s), {len(bing_queries)} Bing query(ies) for groups {group_ids or '(none)'}")
    keep = _topical_filter([market_name], key_players)
    gate = _HostGate(_HOST_GAP_S)
    with ThreadPoolExecutor(1) as ex:
        rss_future = ex.submit(_fetch_feeds, feed_defs, keep, gate, logger)
        with httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True) as client:
            bing = _Bing(client, logger)
            bing_hits = bing.run(bing_queries)
        rss_hits = rss_future.result()
    logger.info(f"[gather] Bing {len(bing_hits)} + RSS {len(rss_hits)} raw hit(s){' (Bing was abandoned)' if bing.disabled else ''}")
    return bing_hits + rss_hits


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


def attach_excerpts(
    hits: list[search.SearchHit], market_name: str, logger: logging.Logger = _null_logger,
) -> None:
    """Fetch each hit's article page (politely) and store an excerpt on hit.excerpt. A hit whose page can't
    be read keeps only its headline and snippet. Never raises."""
    if not hits:
        return
    blocked = set(_registry()["fulltext_blocked"])
    freq = search._catalog_token_freq()
    g = search._gate_for_phrase(market_name, freq)
    stems = set(g[1] or [g[0]]) if g else set()
    gate = _HostGate(_HOST_GAP_S)
    runtime_blocked: set[str] = set()
    stats = defaultdict(int)

    def one(h: search.SearchHit) -> None:
        host = h.host
        if host in blocked or host in runtime_blocked or h.url.lower().split("?")[0].endswith((".pdf", ".xlsx", ".zip")):
            stats["skipped"] += 1
            return
        try:
            with gate.slot(host):
                if host in runtime_blocked:
                    stats["skipped"] += 1
                    return
                r = client.get(h.url)
            if r.status_code in _BLOCKING_STATUSES:
                runtime_blocked.add(host)
                stats["blocked"] += 1
                return
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                stats["failed"] += 1
                return
            text = trafilatura.extract(r.text[:_MAX_PAGE_BYTES], include_comments=False, include_tables=False) or ""
            if len(text.split()) < _EXCERPT_MIN_WORDS:
                stats["thin"] += 1
                return
            h.excerpt = _best_excerpt(text, stems)
            stats["ok"] += 1
        except Exception:
            stats["failed"] += 1

    client = httpx.Client(headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S, follow_redirects=True)
    ex = ThreadPoolExecutor(_ARTICLE_WORKERS)
    per_host: dict[str, int] = defaultdict(int)
    to_fetch = []
    for h in hits:  # hits arrive best-first, so a site's strongest articles are the ones that get read
        per_host[h.host] += 1
        if per_host[h.host] <= _MAX_FETCHES_PER_HOST:
            to_fetch.append(h)
    try:
        futures = [ex.submit(one, h) for h in to_fetch]
        done, pending = wait(futures, timeout=_ARTICLE_STAGE_TIMEOUT_S)
        if pending:
            logger.warning(f"[fulltext] {len(pending)} fetch(es) still running after {_ARTICLE_STAGE_TIMEOUT_S}s — dropped")
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        client.close()
    logger.info(f"[fulltext] {len(to_fetch)} of {len(hits)} hit(s) fetched: {dict(stats)}")
