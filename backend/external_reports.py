"""Outside reports: the market's figures when no CMI report fits the blog.

The editor either pastes report links of their own or ticks nothing, in which case the agent searches the internet for
reports on the blog's OWN market (never a broader or neighbouring one). Each page is read into a fact sheet with the same
quote-by-quote verification as a CMI report, except that every headline quote must name the market in full. The sheets
are merged (merge_fact_sheets) and every figure is moved by variance.vary_fact_sheet before anyone sees it.

Best-effort per page: a page that cannot be read or yields no figure for this market is skipped and logged.
"""
import logging
import time
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urlparse

import facts
import feeds
import llm
import prompts
import reports
import search
import variance
from config import is_cmi_url
from pipeline import _bare_market_names, _market_core
from pipeline_log import section

_PAGE_CHARS = 40_000
_MAX_PAGES = 4
_WORKERS = 4
_CANDIDATE_WORKERS = 4
_SNIPPET_CHARS = 240


def own_market(orient: dict) -> str:
    """The market the article itself is about, at its own level. Falls back to the first candidate market."""
    own = _bare_market_names([orient.get("own_market")]) if orient.get("own_market") else []
    names = own or _bare_market_names(orient.get("candidate_markets"))
    return names[0] if names else ""


def host_of(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


# Pages that can never be a market report: encyclopedias, Q&A and social sites, video, dictionaries.
_NOT_REPORT_HOSTS = (
    "wikipedia.org", "wiktionary.org", "britannica.com", "quora.com", "reddit.com", "youtube.com", "facebook.com",
    "linkedin.com", "x.com", "twitter.com", "medium.com", "investopedia.com", "dictionary.com", "merriam-webster.com",
)
# A report page's title or snippet carries at least one of these.
_REPORT_CUE_RE = re.compile(r"market|industry|report|forecast|size|share|outlook|analysis|cagr|growth", re.I)


def looks_like_report(host: str, title: str, snippet: str) -> bool:
    """A candidate for 'competitor report on this market': not an encyclopedia or social page, and its title or snippet
    talks about a market, its size, forecast or growth."""
    if any(host == h or host.endswith("." + h) for h in _NOT_REPORT_HOSTS):
        return False
    return bool(_REPORT_CUE_RE.search(f"{title} {snippet}"))


# Research-firm report hosts rank first among candidates: a page from one of these is almost always a proper market
# report, while the rest is a mixed bag of trade press, vendor posts and news (coherentmarketinsights.com is absent on
# purpose: CMI's own site is excluded at the figure filter, so it can never reach the candidates).
RESEARCH_HOSTS = (
    "grandviewresearch.com", "marketsandmarkets.com", "mordorintelligence.com", "fortunebusinessinsights.com",
    "gminsights.com", "precedenceresearch.com", "researchandmarkets.com", "emergenresearch.com",
    "verifiedmarketreports.com", "alliedmarketresearch.com", "futuremarketinsights.com", "intelmarketresearch.com",
    "businessresearchinsights.com", "polarismarketresearch.com", "straitsresearch.com", "marketresearchfuture.com",
    "imarcgroup.com", "technavio.com", "statista.com", "expertmarketresearch.com", "maximizemarketresearch.com",
    "databridgemarketresearch.com", "reportsanddata.com", "skyquestt.com", "factmr.com",
    "persistencemarketresearch.com",
)


def is_research_host(host: str) -> bool:
    """True when `host` itself, or any subdomain of it, is one of the research-firm hosts."""
    host = (host or "").lower().removeprefix("www.")
    return any(host == h or host.endswith("." + h) for h in RESEARCH_HOSTS)


def _repeat_run_cut(title: str) -> int | None:
    """The char offset at which a repeated run of 3+ words (case-insensitive, comparison on word characters only)
    starts its second occurrence, or None when the title carries no such repeat."""
    tokens = [(m.group(0).lower(), m.start()) for m in re.finditer(r"[A-Za-z0-9]+", title)]
    words = [w for w, _ in tokens]
    spans = [s for _, s in tokens]
    for j in range(3, len(words) - 2):  # j: candidate start of the second occurrence
        for length in range(3, len(words) - j + 1):
            head = words[j:j + length]
            if any(words[i:i + length] == head for i in range(0, j - length + 1)):
                return spans[j]
    return None


_TRAILING_JUNK = ("…", "...", "|", "-", "–", "—", ":", "&")


def clean_title(title: str, market: str = "", max_chars: int = 120) -> str:
    """A tidy short title for the editor: whitespace collapsed; a 3+ word phrase (the market's own name, usually)
    that appears twice is cut at the START of the second occurrence; trailing separators/ellipses are stripped; a
    remainder longer than max_chars is cut at the last word boundary and closed with a single '…'. A short clean
    title comes back unchanged."""
    title = " ".join((title or "").split())
    if not title:
        return ""
    cut = _repeat_run_cut(title)
    if market:
        low, market_low = title.lower(), market.lower()
        first = low.find(market_low)
        second = low.find(market_low, first + len(market_low)) if first >= 0 else -1
        if second >= 0 and (cut is None or second < cut):
            cut = second
    if cut is not None:
        title = title[:cut]
    while True:
        title = title.rstrip()
        junk = next((j for j in _TRAILING_JUNK if title.endswith(j)), None)
        if junk is None:
            break
        title = title[:-len(junk)]
    if len(title) > max_chars:
        boundary = title.rfind(" ", 0, max_chars)
        title = (title[:boundary] if boundary > 0 else title[:max_chars]).rstrip() + "…"
    return title


def looks_stale(title: str, snippet: str, today: date | None = None, max_age_years: int = 2) -> bool:
    """True when the title or snippet mentions years and the NEWEST one is more than max_age_years in the past ('2034
    forecast' on a 2021 page). A page mentioning no year at all is not called stale on that basis."""
    years = facts._years(f"{title or ''} {snippet or ''}")
    if not years:
        return False
    return max(years) < (today or date.today()).year - max_age_years


def rank_candidates(hits, per_market: int) -> list:
    """The candidate pages kept for the editor: stale pages (their newest mentioned year is too old) are dropped, at
    most ONE page per host survives (the first seen), research-firm hosts come first and everything else after, each
    group in its original order, capped at per_market. Pure: returns a new list, nothing logged or fetched."""
    fresh, seen_hosts = [], set()
    for h in hits:
        if h.host in seen_hosts or looks_stale(h.title, h.snippet):
            continue
        seen_hosts.add(h.host)
        fresh.append(h)
    research = [h for h in fresh if is_research_host(h.host)]
    return (research + [h for h in fresh if not is_research_host(h.host)])[:max(0, per_market)]


def search_queries(market_name: str) -> tuple[list[str], list[str]]:
    """(news-style, web-style) queries for pages that state the market's size, growth, segments and players."""
    core = _market_core(market_name)
    return (
        [f"{core} market report size CAGR"],
        [f"{core} market size share forecast", f"{core} market CAGR segments key players",
         f"{core} market report press release"],
    )


# A page read once (to check it in the picker) is not fetched again when Step 2 reads it a minute later.
_PAGE_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_SECONDS = 1800


def read_page(url: str, logger: logging.Logger) -> str:
    """The readable text of a page, or '' when it cannot be fetched. Never raises."""
    cached = _PAGE_CACHE.get(url)
    if cached and time.time() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    try:
        text = reports.fetch_live_page(url, logger)[:_PAGE_CHARS]
    except Exception as e:  # LiveFetchError and anything a page can throw
        logger.info(f"[external] {url} could not be read: {e}")
        return ""
    _PAGE_CACHE[url] = (time.time(), text)
    return text


def keep_readable(candidates: list[dict], logger: logging.Logger, want: int = 6) -> list[dict]:
    """The candidates whose page the agent can actually read, in order, at most `want`. A search result can point at a page
    that only shows a short preview (or blocks the agent); offering it would let the editor tick a report that fails later."""
    if not candidates:
        return []
    with ThreadPoolExecutor(_WORKERS) as ex:
        texts = list(ex.map(lambda c: read_page(c["url"], logger), candidates))
    readable = [c for c, t in zip(candidates, texts) if t]
    logger.info(f"[external] {len(readable)} of {len(candidates)} candidate page(s) can be read; offering {min(want, len(readable))}")
    return readable[:max(0, want)]


_MARKET_HEAD_RE = re.compile(r"^(.*?\bmarkets?)", re.I)


def page_market_name(title: str, text: str = "") -> str:
    """The market a report page is about, from its title (else its first line): the words up to the first 'Market', minus a
    leading 'Global'. '' when none can be read."""
    for src in (title, (text or "").strip().split(chr(10), 1)[0]):
        s = re.sub(r"^(global|worldwide)\s+", "", (src or "").strip(), flags=re.I)
        m = _MARKET_HEAD_RE.match(s)
        if m and 2 <= len(m.group(1).split()) <= 8:
            return m.group(1).strip()
    return ""


def check_cmi_url(url: str, market_name: str, logger: logging.Logger) -> dict:
    """The early look at a pasted link on CMI's own site: it is the CMI report, used as if the editor had picked it
    (its figures are CMI's own, so nothing is adjusted and the report is linked). A report in the catalog is named; a
    CMI page outside the catalog must at least be readable."""
    entry = reports.match_report_url(url)
    if entry is not None:
        return {"ok": True, "cmi": True, "title": entry.keyword,
                "message": f"This is CMI's own report, {entry.keyword}. It will be used as the CMI report: its figures are not adjusted and it is linked in the article."}
    text = read_page(url, logger)
    if not text:
        return {"ok": False, "cmi": True, "title": "", "message": "This CMI page could not be read. Try another link."}
    return {"ok": True, "cmi": True, "title": text.strip().split("\n", 1)[0][:120],
            "message": "This is a CMI page that is not in the report list. It will be read as a CMI report: its figures are not adjusted."}


def check_url(url: str, market_name: str, logger: logging.Logger, cmi: bool = False) -> dict:
    """Early feedback for a link the editor pasted: can the page be read, and does it look like a report on this market?
    {ok, message, title}. A deterministic look (no model): the market's words, a 'market' word and a figure. cmi=True
    comes from the dedicated CMI report link box, which takes only pages on CMI's own site."""
    url = (url or "").strip()
    if not re.match(r"^https?://", url, re.I):
        return {"ok": False, "message": "That does not look like a web link. It should start with http:// or https://.", "title": ""}
    if is_cmi_url(url):
        if not cmi:
            return {"ok": False, "title": "",
                    "message": "This is a CMI report link. Paste it in the CMI report link box above, not among the competitor links."}
        return check_cmi_url(url, market_name, logger)
    if cmi:
        return {"ok": False, "title": "",
                "message": "This is not a CMI page. The CMI report link box takes only coherentmarketinsights.com report pages."}
    text = read_page(url, logger)
    if not text:
        return {"ok": False, "message": "The page could not be read (it may be paywalled, blocked or down). Try another link.", "title": ""}
    core = facts._core_tokens(market_name)
    words = {facts._stem(w) for w in re.findall(r"[a-z0-9]+", text.lower())}
    missing = [t for t in core if t not in words]
    title = text.strip().split("\n", 1)[0][:120]
    if missing:
        return {"ok": False, "title": title, "message": f"The page was read but does not mention {', '.join(missing)}. It may be about a different market."}
    if not re.search(r"\d", text) or "market" not in text.lower():
        return {"ok": False, "title": title, "message": "The page was read but carries no market figures."}
    return {"ok": True, "title": title, "message": "Readable and about this market. Its figures will be used, adjusted by a few percent."}


def find_pages(market_name: str, exclude_hosts, logger: logging.Logger) -> list[tuple[str, str]]:
    """(url, text) of readable pages that may report this market, found by web search, best first."""
    section(logger, "OUTSIDE REPORT SEARCH")
    news_q, web_q = search_queries(market_name)
    try:
        raw = feeds.bing_search(news_q, web_q, logger)
        raw += search.run_text_searches(web_q[:2], logger)
    except Exception:
        logger.warning("[external] the outside report search failed", exc_info=True)
        return []
    kept = rank_candidates(
        [h for h in facts.filter_figure_hits(raw, market_name, exclude_hosts, logger, limit=_MAX_PAGES + 6)
         if looks_like_report(h.host, h.title, h.snippet)], _MAX_PAGES + 2)
    with ThreadPoolExecutor(_WORKERS) as ex:
        texts = list(ex.map(lambda h: read_page(h.url, logger), kept))
    pages = [(h.url, t) for h, t in zip(kept, texts) if t]
    logger.info(f"[external] {len(pages)} of {len(kept)} candidate page(s) could be read")
    return pages[:_MAX_PAGES]


def find_candidate_pages(
    markets: list[str], exclude_hosts, logger: logging.Logger, per_market: int = 6,
) -> list[dict]:
    """Step 2 pre-pass: what the web search offers for each market the editor picked, WITHOUT reading any page (the
    editor first ticks which candidates to use). Same searches and the same figure filter as find_pages, one worker per
    market (at most 4). Returns {url, title, host, snippet, market} per candidate, deduplicated by url, in market order;
    a market whose search fails is skipped and logged."""
    section(logger, "OUTSIDE REPORT CANDIDATES")

    def for_market(market: str) -> list[dict]:
        news_q, web_q = search_queries(market)
        try:
            raw = feeds.bing_search(news_q, web_q, logger)
            raw += search.run_text_searches(web_q[:2], logger)
        except Exception:
            logger.warning(f"[external] the outside report search for {market!r} failed", exc_info=True)
            return []
        kept = rank_candidates(
            [h for h in facts.filter_figure_hits(raw, market, exclude_hosts, logger, limit=per_market + 6)
             if looks_like_report(h.host, h.title, h.snippet)], per_market)
        return [
            {"url": h.url, "title": clean_title(h.title, market), "host": h.host,
             "snippet": h.snippet[:_SNIPPET_CHARS], "market": market}
            for h in kept
        ]

    names = [m for m in markets if m]
    with ThreadPoolExecutor(min(len(names), _CANDIDATE_WORKERS) or 1) as ex:
        per_market_hits = list(ex.map(for_market, names))
    out, seen = [], set()
    for hits in per_market_hits:
        for c in hits:
            if c["url"] and c["url"] not in seen:
                seen.add(c["url"])
                out.append(c)
    logger.info(f"[external] {len(out)} candidate page(s) for {len(names)} market(s), nothing fetched yet")
    return out


def page_fact_sheet(market_name: str, url: str, text: str, logger: logging.Logger, today: date | None = None) -> dict:
    """One page read into a verified fact sheet whose figures are already moved by the variance. Empty headline when the
    page does not state the market's own figures; never raises."""
    if is_cmi_url(url):
        # A CMI page is read as the CMI report, never as an outside one: its figures are CMI's own and must not be varied.
        logger.warning(f"[external] {url} is a CMI page; it is not read as an outside report")
        return facts._empty_fact_sheet()
    outside = {"name": host_of(url), "url": url}
    try:
        raw = llm.chat_json(
            prompts.FACT_SHEET_SYSTEM, prompts.FACT_SHEET_USER.format(market_name=market_name, report_text=text),
            temperature=0.1, call_name="fact_sheet_external", logger=logger,
        )
    except llm.LLMError:
        logger.warning(f"[external] the fact sheet call failed for {url}", exc_info=True)
        raw = {}
    fs = facts.validate_fact_sheet(raw, text, market_name, logger, today, outside=outside)
    variance.vary_fact_sheet(fs, market_name)
    return fs


def build_external_sheet(
    market_name: str, pages: list[tuple[str, str]] | list[tuple[str, str, str]], logger: logging.Logger,
    today: date | None = None,
) -> tuple[dict, list[dict], dict[str, str]]:
    """Read every page in parallel and merge what they state. Returns (merged fact sheet, sources used, {url: page text}
    of those sources). The sheet's gaps list whatever no page stated for this market; pages that stated nothing usable
    are left out of the sources.

    Each page is `(url, text)` scoped to market_name, or `(url, text, market)` scoped to that page's own market (the
    market the editor picked that the page was found for)."""
    section(logger, "CALL: FACT SHEET (OUTSIDE REPORTS)")
    cmi_pages = [p[0] for p in pages if is_cmi_url(p[0])]
    if cmi_pages:
        logger.warning(f"[external] CMI page(s) left out of the outside reports (they are CMI's own, never varied): {cmi_pages}")
        pages = [p for p in pages if not is_cmi_url(p[0])]

    def market_of(page) -> str:
        return page[2] if len(page) > 2 and page[2] else market_name

    with ThreadPoolExecutor(_WORKERS) as ex:
        sheets = list(ex.map(lambda p: page_fact_sheet(market_of(p), p[0], p[1], logger, today), pages))
    usable = []
    for page, fs in zip(pages, sheets):
        url, page_market = page[0], market_of(page)
        # A page is trusted only when it states at least one HEADLINE figure for this market in the market's own words
        # (the strict scope check). A page about a neighbouring or broader market states none, and then nothing else
        # on it (segments, statements, players) may be used either: it would be about another market.
        headline = len([k for k in facts.HEADLINE_KEYS if fs.get(k)])
        if headline:
            usable.append((url, fs))
        else:
            logger.info(f"[external] {url} states no figure for {page_market!r} itself, so none of it is used")
    sources = [{"name": host_of(u), "url": u, "kind": "external"} for u, _ in usable]
    merged = facts.merge_fact_sheets([fs for _, fs in usable], sources)
    texts = {p[0]: p[1] for p in pages}
    logger.info(f"[external] merged {len(usable)} page(s); gaps: {merged['gaps'] or 'none'}; {len(merged['variance'])} figure(s) varied")
    return merged, sources, {u: texts[u] for u, _ in usable}
