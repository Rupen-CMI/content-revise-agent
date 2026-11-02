"""Offline test for the candidate-quality helpers in external_reports: clean_title, looks_stale, is_research_host
and rank_candidates, plus an end-to-end find_candidate_pages run that shows them together (stale dropped, one per
host, research firms first, titles cleaned). No network: both search calls are monkeypatched to return fixed
search.SearchHit objects, and the page reader records rather than fetches.

Run from backend/: python devtools/test_candidate_quality.py
"""
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feeds  # noqa: E402
import search  # noqa: E402

import external_reports  # noqa: E402

logging.basicConfig(level=logging.ERROR)

FAILURES: list[str] = []
LOG = logging.getLogger("test_candidate_quality")
TODAY = date(2026, 10, 31)

MORDOR_GARBLE = ("Wealth Management Platform Market Size, Industry Report ...Wealth Management Platform Market Size, "
                 "Share | Growth, 2034Wealth Management Platform Market Size, Share, Growth ...")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------------------- clean_title


def hit(url: str, title: str, snippet: str) -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=url, source="s", date=None)


# ------------------------------------------------------------------------------- clean_title

clean = ("Indoor Plant Market Grows Steadily Through 2032, Grand View Research Says")
check("title: a short clean title is returned unchanged",
      external_reports.clean_title(clean, "Indoor Plant Market") == clean)
check("title: a repeated 3-word phrase is cut at the start of the second occurrence (case-insensitive)",
      external_reports.clean_title("Wealth Management Platform Report Wealth Management Platform Outlook")
      == "Wealth Management Platform Report",
      external_reports.clean_title("Wealth Management Platform Report Wealth Management Platform Outlook"))
check("title: the market's own name repeated is cut even when the phrase check needs it",
      external_reports.clean_title("Wealth Management Platform Market Report — Wealth Management Platform Market Outlook",
                                   "Wealth Management Platform Market")
      == "Wealth Management Platform Market Report",
      external_reports.clean_title("Wealth Management Platform Market Report — Wealth Management Platform Market Outlook",
                                   "Wealth Management Platform Market"))
garbled = external_reports.clean_title(MORDOR_GARBLE, "Wealth Management Platform Market")
check("title: the real garbled Mordor title is cut to the first clean phrase",
      garbled == "Wealth Management Platform Market Size, Industry Report", garbled)
check("title: the garbled title no longer carries repeated phrases or ellipses",
      "..." not in garbled and "…" not in garbled and "2034" not in garbled
      and garbled.lower().count("wealth management platform market") == 1, garbled)
check("title: the garbled title is short enough", len(garbled) <= 120, str(len(garbled)))
check("title: trailing separators and ellipses are stripped (pipe, dash, colon)",
      external_reports.clean_title("Market Size Report |") == "Market Size Report"
      and external_reports.clean_title("Market Size Report ...") == "Market Size Report"
      and external_reports.clean_title("Market Size Report -") == "Market Size Report"
      and external_reports.clean_title("Market Size Report:") == "Market Size Report")
check("title: a lone trailing & is stripped, an & between words is kept",
      external_reports.clean_title("Market Size Report &") == "Market Size Report"
      and external_reports.clean_title("Market Size Report & Forecasts |") == "Market Size Report & Forecasts")
long_title = " ".join(["Word%02d" % i for i in range(40)])
long_clean = external_reports.clean_title(long_title, "", max_chars=120)
check("title: a long title is cut at a word boundary and closed with ONE ellipsis character",
      long_clean == " ".join(["Word%02d" % i for i in range(17)]) + "…"
      and long_clean.count("…") == 1 and len(long_clean) <= 120, long_clean)

# ------------------------------------------------------------------------------- looks_stale

check("stale: a 2021 page against a 2026 today is stale",
      external_reports.looks_stale("Wealth Management Platform Market Report 2021", "", TODAY))
check("stale: a title+snippet mentioning no year at all is not stale",
      not external_reports.looks_stale("Wealth Management Platform Market Report", "the market keeps growing", TODAY))
check("stale: mixed years count by the NEWEST one",
      not external_reports.looks_stale("Report covering 2019 to 2024", "outlook through 2025", TODAY)
      and external_reports.looks_stale("2021 report (base year 2019 data)", "", TODAY))
check("stale: the boundary year today.year - max_age_years is NOT stale",
      not external_reports.looks_stale("Market Size 2024", "", TODAY))
check("stale: one year further back already is",
      external_reports.looks_stale("Market Size 2023", "", TODAY))
check("stale: a year only in the snippet counts too",
      external_reports.looks_stale("A clean title with no digits", "coverage as reported in 2021", TODAY))
check("stale: a future year is never stale",
      not external_reports.looks_stale("Wealth Management Platform Market Global Forecast to 2032", "", TODAY))

# ------------------------------------------------------------------------------- is_research_host

check("host: research-firm hosts match exactly and on subdomains (www included)",
      external_reports.is_research_host("grandviewresearch.com")
      and external_reports.is_research_host("reports.grandviewresearch.com")
      and external_reports.is_research_host("www.mordorintelligence.com"))
check("host: a host that merely CONTAINS a research name is not a research host",
      not external_reports.is_research_host("notgrandviewresearch.com")
      and not external_reports.is_research_host("grandviewresearch.com.evil.example"))
check("host: coherentmarketinsights.com is NOT a research host here (CMI's own site is excluded elsewhere)",
      not external_reports.is_research_host("coherentmarketinsights.com"))
check("host: an ordinary site is not a research host", not external_reports.is_research_host("dailypress.example"))

# ------------------------------------------------------------------------------- rank_candidates

check("rank: a stale hit is dropped", external_reports.rank_candidates(
    [hit("https://a.example/x", "X Market Report 2021", "the market as it stood")], 6) == [])
check("rank: at most one hit per host survives (the first seen)",
      [h.url for h in external_reports.rank_candidates(
          [hit("https://a.example/1", "One Market Report", ""), hit("https://a.example/2", "Another Market Report", ""),
           hit("https://b.example/1", "Bee Market Report", "")], 6)]
      == ["https://a.example/1", "https://b.example/1"])
check("rank: research-firm hosts come first, the rest after, each group in original order",
      [h.host for h in external_reports.rank_candidates(
          [hit("https://news.example/1", "First Market News", ""), hit("https://www.grandviewresearch.com/r", "Gvr Market Report", ""),
           hit("https://trades.example/2", "Second Market News", ""), hit("https://mordorintelligence.com/r", "Mordor Market Report", "")], 6)]
      == ["grandviewresearch.com", "mordorintelligence.com", "news.example", "trades.example"],
      str([h.host for h in external_reports.rank_candidates(
          [hit("https://news.example/1", "First Market News", ""), hit("https://www.grandviewresearch.com/r", "Gvr Market Report", ""),
           hit("https://trades.example/2", "Second Market News", ""), hit("https://mordorintelligence.com/r", "Mordor Market Report", "")], 6)]))
check("rank: per_market caps the kept list",
      len(external_reports.rank_candidates(
          [hit(f"https://s{i}.example/x", f"Market {i} Report", "") for i in range(4)], 2)) == 2)
check("rank: a dropped stale hit does not consume one of the per_market slots",
      [h.url for h in external_reports.rank_candidates(
          [hit("https://old.example/x", "Old Market Report 2021", ""), hit("https://m.example/1", "First Market Report", ""),
           hit("https://m.example/2", "Dup Market Report", ""), hit("https://n.example/x", "Second Market Report", "")], 2)]
      == ["https://m.example/1", "https://n.example/x"])

# ------------------------------------------------------------------------------- end to end

MARKET = "Wealth Management Platform Market"
HITS = [
    hit("https://dailypress.example/wealth-markets",
        "Economy Brief: Wealth Management Platform Market Seen Expanding",
        "The wealth management platform market keeps attracting advisory firms, analysts reported this month."),
    hit("https://www.mordorintelligence.com/industry-reports/wealth-management-platform-market",
        "Wealth Management Platform Market Size, Share, Growth | Mordor Intelligence",
        "The wealth management platform market is segmented by component and region."),
    hit("https://www.grandviewresearch.com/industry-analysis/wealth-management-platform-market",
        MORDOR_GARBLE,
        "Grand View Research's wealth management platform market analysis."),
    hit("https://www.grandviewresearch.com/horoscope/wealth",
        "Grand View Research Wealth Management Platform Market Overview",
        "The second grandviewresearch page for the wealth management platform market."),
    hit("https://www.marketsandmarkets.com/Market-Reports/wealth-management-platform-market.html",
        "Wealth Management Platform Market Global Forecast to 2032 by Component",
        "MarketsandMarkets segments the wealth management platform market by offering."),
    hit("https://legacynews.example/wealth-market-2021",
        "Wealth Management Platform Market Report from 2021",
        "Coverage of the wealth management platform market as it stood years ago."),
    hit("https://en.wikipedia.org/wiki/Wealth_management",
        "Wealth management",
        "Wealth management is an investment advisory service combining portfolio management."),
    hit("https://fintechweekly.example/wealth-platforms",
        "Wealth Management Platform Market Roundup",
        "Trade coverage of the wealth management platform market, vendors and wins."),
]

STATE = {"bing": [], "text": []}


def fake_bing_search(news_queries, web_queries, logger):
    STATE["bing"].append([*news_queries, *web_queries])
    return list(HITS)


def fake_run_text_searches(queries, logger):
    STATE["text"].append(list(queries))
    return [HITS[5], HITS[2]]  # urls already returned by bing: must be deduplicated by url


ORIG = (feeds.bing_search, search.run_text_searches)
feeds.bing_search = fake_bing_search
search.run_text_searches = fake_run_text_searches
try:
    candidates = external_reports.find_candidate_pages([MARKET], ["sponsor.example"], LOG, per_market=6)
finally:
    feeds.bing_search, search.run_text_searches = ORIG

urls = [c["url"] for c in candidates]
hosts = [c["host"] for c in candidates]
check("e2e: both patched search engines were queried", bool(STATE["bing"]) and bool(STATE["text"]))
check("e2e: research-firm candidates come first, then the rest, original order inside each group",
      hosts == ["mordorintelligence.com", "grandviewresearch.com", "marketsandmarkets.com",
                "dailypress.example", "fintechweekly.example"], str(hosts))
check("e2e: the stale page, the wikipedia page and the same-host duplicate are gone",
      len(candidates) == 5 and all("legacynews" not in u and "wikipedia" not in u for u in urls), str(urls))
check("e2e: the garbled grandviewresearch title is cleaned in the candidate it feeds the editor",
      candidates[1]["title"] == "Wealth Management Platform Market Size, Industry Report", str(candidates[1]["title"]))
check("e2e: every offered title is short and free of search-engine ellipses",
      all(len(c["title"]) <= 120 and "…" not in c["title"] and "..." not in c["title"] for c in candidates),
      str([c["title"] for c in candidates]))
check("e2e: every candidate carries the market it was found for and a clean snippet",
      all(c["market"] == MARKET and c["snippet"] for c in candidates))

ORIG = (feeds.bing_search, search.run_text_searches)
feeds.bing_search = fake_bing_search
search.run_text_searches = fake_run_text_searches
try:
    few = external_reports.find_candidate_pages([MARKET], ["sponsor.example"], LOG, per_market=2)
finally:
    feeds.bing_search, search.run_text_searches = ORIG
check("e2e: per_market caps how many candidates come back", len(few) == 2, str(len(few)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all candidate quality tests passed")
