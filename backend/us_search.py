"""The U.S. section's share of the market web search: the article's U.S. section has no other region to lean on, so it gets
queries of its own (sections.US_QUERIES of them, inside the run's one Bing budget; no separate U.S. search runs).

The queries are built here; the general search (pipeline.run_search_evidence) runs them with every other section's, and each
hit they find carries the section "us". After the pages are read, every page found for "us" must name the U.S. in its own
text: it is then marked scope "us" (SELECT sees it as found by the U.S. search and tags the real U.S. findings "us"); a page
that does not loses the "us" section, and is dropped when it had no other.
"""
import re

import search

_US_TEXT_RE = re.compile(r"\bU\.S\.|\bUS\b|\bUSA\b|(?i:united states)|\bAmerican\b")


def us_news_queries(core: str) -> list[str]:
    """The U.S. section's Bing queries for a market's core noun, best first (the budget takes the first sections.US_QUERIES)."""
    # adoption, policy, company investment and news wording: size-style phrasings ("market growth", "statistics", "market share") bring
    # back research firms' own report pages, which the search filters drop, and leave no U.S. page
    return [
        f'"{core}" United States',
        f"{core} U.S. adoption demand",
        f"{core} U.S. companies investment",
        f"{core} U.S. federal policy",
        f"{core} U.S. expansion announced",
    ]


def names_the_us(hit: search.SearchHit) -> bool:
    return bool(_US_TEXT_RE.search(f"{hit.title} {hit.snippet} {hit.excerpt}"))


def mark_us_hits(hits: list[search.SearchHit]) -> list[search.SearchHit]:
    """The hits with the U.S. section settled (call it once the pages are read): a hit found for "us" that names the U.S. is
    marked scope "us"; one that does not loses the "us" section and is dropped when that was its only one."""
    out: list[search.SearchHit] = []
    for h in hits:
        if "us" in h.sections:
            if names_the_us(h):
                h.scope = "us"
            else:
                h.sections = [s for s in h.sections if s != "us"]
                if not h.sections:
                    continue
        out.append(h)
    return out
