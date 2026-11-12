"""The U.S. data search: the article's U.S. section has no other region to lean on, so it gets a search of its own.

Runs beside the general market web search in Step 2 and returns pages about the market in the United States (news and
statistics pages, found with several query styles). Every page kept must name the U.S. in its own text; the hits join the
general pool, are shown to SELECT marked as found by the U.S. search, and SELECT tags the real U.S. findings "us".
Best-effort like every search stage: a failure returns nothing and the planner flags the section for the editor.
"""
import logging
import re

import config
import feeds
import search
from pipeline import _market_core
from pipeline_log import section

_US_TEXT_RE = re.compile(r"\bU\.S\.|\bUS\b|\bUSA\b|(?i:united states)|\bAmerican\b")
_KEEP = 10
_MIN_BEFORE_FALLBACK = 3


def us_news_queries(market_name: str) -> list[str]:
    core = _market_core(market_name)
    # adoption, policy, company investment and news wording: size-style phrasings ("market growth", "statistics", "market share") bring
    # back research firms' own report pages, which the search filters drop, and leave no U.S. page
    return [
        f'"{core}" United States',
        f"{core} U.S. adoption demand",
        f"{core} U.S. federal policy",
        f"{core} U.S. companies investment",
        f"{core} U.S. expansion announced",
    ]


def us_web_queries(market_name: str) -> list[str]:
    core = _market_core(market_name)
    return [
        f"{core} United States adoption policy",
        f"{core} United States companies investment expansion",
        f"{core} U.S. regulation demand adoption",
    ]


def names_the_us(hit: search.SearchHit) -> bool:
    return bool(_US_TEXT_RE.search(f"{hit.title} {hit.snippet} {hit.excerpt}"))


def run_us_search(
    market_name: str, sponsor_hosts: tuple[str, ...], key_players, logger: logging.Logger, topic_terms: list[str] = (),
) -> list[search.SearchHit]:
    """Pages about this market in the United States, each naming the U.S. in its own text, marked scope "us"."""
    section(logger, "U.S. DATA SEARCH")
    try:
        raw = feeds.bing_search(us_news_queries(market_name), us_web_queries(market_name), logger)

        def keep(hits: list[search.SearchHit]) -> list[search.SearchHit]:
            kept = search.filter_hits(
                hits, [market_name], sponsor_hosts, limit=_KEEP * 2, logger=logger, key_players=key_players,
                max_per_host=config.SEARCH_MAX_PER_HOST, topic_terms=list(topic_terms),
            )
            # pages that already name the U.S. take the cap first; a news item that does not yet (its excerpt is fetched
            # below) only fills what is left, so a pool of unrelated news can never crowd the U.S. pages out
            named = [h for h in kept if names_the_us(h)]
            return (named + [h for h in kept if h.is_news and not names_the_us(h)])[:_KEEP]

        kept = keep(raw)
        us_named = sum(1 for h in kept if names_the_us(h))
        if us_named < _MIN_BEFORE_FALLBACK:
            logger.info(f"[us-search] only {us_named} page(s) from Bing name the U.S.; adding the text-search engines")
            raw += search.run_text_searches(us_web_queries(market_name)[:2], logger)
            kept = keep(raw)
    except Exception:
        logger.warning("[us-search] the U.S. search failed; the section relies on the report's own U.S. facts", exc_info=True)
        return []
    feeds.attach_excerpts(kept, market_name, logger)
    kept = [h for h in kept if names_the_us(h)]
    for h in kept:
        h.scope = "us"
        h.add_sections(["us"])
    logger.info(f"[us-search] kept {len(kept)} page(s) that name the U.S.: " + "; ".join(h.host for h in kept))
    return kept


def merge_hits(general: list[search.SearchHit], us_hits: list[search.SearchHit]) -> list[search.SearchHit]:
    """The general pool followed by the U.S. pages it does not already hold. A page both searches found keeps the U.S.
    mark, so SELECT sees it as a U.S. candidate."""
    by_url = {h.url: h for h in general}
    extra: list[search.SearchHit] = []
    for h in us_hits:
        if h.url in by_url:
            by_url[h.url].scope = "us"
            by_url[h.url].add_sections(["us"])
        else:
            extra.append(h)
    return list(general) + extra
