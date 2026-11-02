"""Keyword research from what people really type.

The agent asks Google's and Bing's search boxes what follows the market's name (their autocomplete lists the phrases real
users type, most popular first), then ranks every phrase by how high and how often it appeared and keeps only the phrases
that name THIS market. The ranked phrases feed two things:
  * the secondary-keyword pool (the words that follow "<market> market": size, share, growth rate ...), ordered by demand,
  * the long-tail searches the demand step chooses from (it is told to prefer these real phrases).
Nothing here is a model's guess about volume: the signal is the rank and the agreement of two engines. A paid volume source
(Semrush, Ahrefs, DataForSEO) would add exact numbers; `Suggestion.volume` is reserved for it and stays None today.

Best-effort: every request that fails is skipped and logged, and a run with no suggestions falls back to the fixed pool.
"""
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from pipeline import _SECONDARY_KEYWORD_SUFFIXES, _market_core
from pipeline_log import section

_GOOGLE = "https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=us&q={q}"
_BING = "https://api.bing.com/osjson.aspx?query={q}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}
_ENDINGS = ("", "size", "share", "growth", "trends", "forecast", "analysis", "report", "key players", "segmentation",
            "opportunities", "drivers", "outlook", "revenue", "by region", "in the us")
_LETTERS = "abcdefghijklmnopqrstuvwxyz"
_WORKERS = 4
_TIMEOUT = 6
_PAUSE = 0.05

# What a phrase after "<market> market" is about, mapped to the section that answers it. First match wins.
_SECTION_RULES = (
    (re.compile(r"\b(?:us|usa|u\.s|united states|america|american)\b", re.I), "country_us"),
    (re.compile(r"\b(?:size|value|valuation|revenue|forecast|cagr|projection|worth|statistics)\b", re.I), "report_context"),
    (re.compile(r"\b(?:share|players|companies|competitors|competitive|landscape|leaders|vendors|manufacturers|top)\b", re.I), "key_players"),
    (re.compile(r"\b(?:segment|segments|segmentation|type|types|application|applications|by)\b", re.I), "segmentation"),
    (re.compile(r"\b(?:trends?|innovation|technology|ai|digital|shift)\b", re.I), "trends"),
    (re.compile(r"\b(?:growth|drivers?|demand|opportunit\w+|potential)\b", re.I), "trends"),
    (re.compile(r"\b(?:outlook|future|analysis|overview|report|research)\b", re.I), "conclusion"),
)
# Words in a phrase's tail that carry no topic: years, file types, brand-of-report words, places other than the U.S.
_TAIL_NOISE = re.compile(r"^(?:20\d\d|19\d\d|pdf|ppt|free|download|sample|2024-2030|2025-2032|global|worldwide|the|a|an|of|in|and|for|by)$", re.I)


@dataclass
class Suggestion:
    query: str
    score: float = 0.0
    engines: set = field(default_factory=set)
    hits: int = 0
    volume: int | None = None      # reserved for a paid volume source

    def as_dict(self) -> dict:
        return {"query": self.query, "score": round(self.score, 2), "engines": sorted(self.engines), "hits": self.hits, "volume": self.volume}


def _core_words(market_name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", _market_core(market_name).lower()) if w]


def _fetch(url: str, logger: logging.Logger) -> list[str]:
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        time.sleep(_PAUSE)
        return [str(x) for x in data[1]] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
    except Exception as e:  # network, status, bad JSON: this one request contributes nothing
        logger.info(f"[keywords] {url.split('?')[0].split('/')[2]} request failed: {type(e).__name__}")
        return []


def _names_market(phrase: str, core_words: list[str]) -> bool:
    """A phrase counts only when it holds every word of the market's core (a stem match, so plurals pass)."""
    tokens = re.findall(r"[a-z0-9]+", phrase.lower())
    return bool(core_words) and all(any(t.startswith(w[:5]) or w.startswith(t[:5]) for t in tokens if len(t) > 2) for w in core_words)


def collect_suggestions(market_name: str, logger: logging.Logger, limit: int = 40) -> list[Suggestion]:
    """Ranked real search phrases for the market, best first. A phrase scores 1/(rank+1) each time an engine lists it for a
    seed (letter-by-letter seeds count half), so a phrase both engines list near the top for several seeds ranks first."""
    section(logger, "KEYWORD RESEARCH (real searches)")
    core = _market_core(market_name)
    core_words = _core_words(market_name)
    # A niche market's full phrase ("... market size") rarely autocompletes, but the shorter head phrases do, so both are asked.
    seeds = ([(f"{core} market {e}".strip(), 1.0) for e in _ENDINGS] + [(f"{core} market {c}", 0.5) for c in _LETTERS]
             + [(core, 1.0), (f"{core} industry", 1.0)] + [(f"{core} {c}", 0.5) for c in _LETTERS])
    jobs = [(engine, tmpl.format(q=quote(seed)), seed, weight)
            for seed, weight in seeds for engine, tmpl in (("google", _GOOGLE), ("bing", _BING))]

    def run(job):
        engine, url, seed, weight = job
        return engine, seed, weight, _fetch(url, logger)

    found: dict[str, Suggestion] = {}
    ok = 0
    with ThreadPoolExecutor(_WORKERS) as ex:
        for engine, seed, weight, phrases in ex.map(run, jobs):
            ok += bool(phrases)
            for rank, raw in enumerate(phrases):
                phrase = " ".join(raw.lower().split())
                if phrase == seed or not _names_market(phrase, core_words):
                    continue
                s = found.setdefault(phrase, Suggestion(phrase))
                s.score += weight / (rank + 1)
                s.engines.add(engine)
                s.hits += 1
    for s in found.values():
        if len(s.engines) > 1:
            s.score *= 1.5            # two engines agree
    ranked = sorted(found.values(), key=lambda s: (-s.score, s.query))[:limit]
    logger.info(f"[keywords] {ok}/{len(jobs)} requests answered; {len(found)} phrase(s) name the market; top: "
                + "; ".join(f"{s.query} ({s.score:.1f})" for s in ranked[:8]))
    return ranked


def tail_of(phrase: str, market_name: str) -> str:
    """What follows '<market core> market' in a phrase, trimmed to topic words ('wealth management platform market size 2026' -> 'size')."""
    core = _market_core(market_name).lower()
    low = " ".join(phrase.lower().split())
    i = low.find(core)
    if i < 0:                       # the phrase names the market in other words: no tail to read
        return ""
    rest = low[i + len(core):]
    words = [w for w in re.findall(r"[a-z0-9&-]+", rest) if w not in ("market", "markets", "industry")]
    words = [w for w in words if not _TAIL_NOISE.match(w)]
    return " ".join(words[:3])


# The words a secondary keyword's tail may be made of: the forms a market keyword takes ("<market> size", "<market> share").
_TAIL_VOCAB = frozenset({
    "size", "share", "forecast", "growth", "rate", "trends", "trend", "analysis", "report", "outlook", "key", "players", "player",
    "segmentation", "segments", "opportunities", "drivers", "cagr", "revenue", "by", "region", "competitive", "landscape",
    "demand", "technology", "applications", "types", "companies", "providers", "vendors", "top", "future", "overview", "statistics",
})


def section_for(tail: str) -> str | None:
    for rx, sec in _SECTION_RULES:
        if rx.search(tail):
            return sec
    return None


def build_pool(suggestions: list[Suggestion], market_name: str, logger: logging.Logger) -> list[dict]:
    """The secondary-keyword pool, most searched first: [{key, section, score, example, source}]. A key is the words that
    follow '<market> market' (the form the writer and the checks already use). Real phrases come first, in score order, one
    row per key; the fixed list then fills any key not already present, so the planner always has its usual choices."""
    rows: dict[str, dict] = {}
    for s in suggestions:
        tail = tail_of(s.query, market_name)
        sec = section_for(tail) if tail else None
        if not tail or sec is None or sec == "country_us" or not all(w in _TAIL_VOCAB for w in tail.split()):
            continue
        row = rows.setdefault(tail, {"key": tail, "section": sec, "score": 0.0, "example": s.query, "source": "search"})
        row["score"] = round(row["score"] + s.score, 2)
    pool = sorted(rows.values(), key=lambda r: -r["score"])
    present = {r["key"].lower() for r in pool}
    for key, sec in _SECONDARY_KEYWORD_SUFFIXES:
        if key.lower() not in present:
            pool.append({"key": key, "section": sec, "score": 0.0, "example": "", "source": "standard"})
    logger.info("[keywords] pool: " + "; ".join(f"{r['key']} -> {r['section']} ({r['score']}, {r['source']})" for r in pool[:14]))
    return pool


_SERP_TERMS = ("market size", "size", "share", "forecast", "growth", "trends", "analysis", "report", "outlook", "key players",
               "segmentation", "opportunities", "drivers", "cagr", "revenue", "by region", "competitive landscape")


def serp_terms(market_name: str, logger: logging.Logger) -> dict[str, float]:
    """Which keyword forms the top-ranking pages for the market target: the titles of Bing's web results for '<market> market'
    and '<market> market size share forecast', scored by how many of the first ten carry each form (rank-weighted). A form that
    most ranking titles carry is the one the market's search results reward."""
    import feeds
    core = _market_core(market_name)
    try:
        hits = feeds.bing_search([], [f"{core} market", f"{core} market size share forecast"], logger)
    except Exception:
        logger.info("[keywords] the ranking-page lookup failed", exc_info=True)
        return {}
    scores: dict[str, float] = {}
    for rank, h in enumerate(hits[:20]):
        title = (h.title or "").lower()
        if not _names_market(title, _core_words(market_name)):
            continue
        for term in _SERP_TERMS:
            if re.search(r"\b" + re.escape(term) + r"\b", title):
                scores[term] = scores.get(term, 0.0) + 1.0 / (rank % 10 + 1)
    logger.info("[keywords] ranking pages carry: " + "; ".join(f"{k} ({v:.1f})" for k, v in sorted(scores.items(), key=lambda kv: -kv[1])[:8]))
    return scores


def research(market_name: str, logger: logging.Logger) -> dict:
    """{suggestions: [...], pool: [...], method: str}: everything the keyword choices rest on, for the planner and the screen."""
    try:
        suggestions = collect_suggestions(market_name, logger)
    except Exception:
        logger.warning("[keywords] the keyword research failed; the standard pool is used", exc_info=True)
        suggestions = []
    pool = build_pool(suggestions, market_name, logger)
    serp = serp_terms(market_name, logger)
    if serp:                                   # forms the ranking pages target lift their keys; "market size" is the key "size"
        for row in pool:
            bonus = max((v for k, v in serp.items() if k.split()[-1] == row["key"].lower().split()[-1]), default=0.0)
            if bonus:
                row["score"] = round(row["score"] + 2 * bonus, 2)
                row["source"] = "search" if row["source"] == "search" else "ranking pages"
        pool.sort(key=lambda r: -r["score"])
    return {
        "suggestions": [s.as_dict() for s in suggestions],
        "ranking_terms": [{"term": k, "score": round(v, 2)} for k, v in sorted(serp.items(), key=lambda kv: -kv[1])],
        "pool": pool,
        "method": ("Google and Bing were asked what people type after the market's name (ranked by how high and how often a phrase "
                   "appeared, and whether both engines list it), and the titles of the pages that rank for the market were read to see "
                   "which keyword forms they target. Only phrases that name this market are kept."),
    }
