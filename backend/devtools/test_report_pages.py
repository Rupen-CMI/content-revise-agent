"""Offline test: a market-report page is never market evidence (no network, no LLM).

Run from backend/: python devtools/test_report_pages.py

The CMI report is fetched on its own, so another firm's report listing (a "Market Size, Share, Forecast" page) must not appear among the
evidence the web search gathers. The titles below are real ones that slipped past the old filter, which only knew a list of sites and
report titles carrying a year. Headlines that report something that happened, and trusted trade press, stay.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import search  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_report_pages")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def hit(title: str, host: str, news: bool = False, snippet: str = "") -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=f"https://{host}/a/b", source=host,
                            date=datetime.now(timezone.utc) if news else None, is_news=news)


# ---- titles that are report listings (real ones from run logs)
REPORTS = [
    ("Vitamin C Derivatives Market Size, Share, Trends & Growth ...", "allmarketanalysis.com", False),
    ("Vitamin C Derivatives Market Size, Share & Trends Analysis ...", "allmarketanalysis.com", False),
    ("Global Vitamin C Derivatives Market Research Report ...", "dirmarketresearch.com", False),
    ("Vitamin C and Derivatives Market 2026 - PW Consulting", "pmarketresearch.com", False),
    ("Language Services Market (2026 - 2033)", "futuremarketreport.com", False),
    ("Language Services Market Research Report 2034", "marketintelo.com", False),
    ("United States (US) Language Services Market (2025-2031 ...", "6wresearch.com", False),
    ("Wealth Management Platform Market Forecast by 2034", "someunlistedsite.example", False),
    ("Language Services Market Size Report", "somenewsdesk.example", True),
    ("Language Services Market Outlook: growth through 2031", "somenewsdesk.example", True),
]
for title, host, news in REPORTS:
    reason = search.report_page_reason(hit(title, host, news))
    check(f"report page dropped: {title[:58]!r} ({host})", reason is not None, str(reason))

# ---- a host named like a report mill is dropped whatever the title says
for host in ("dirmarketresearch.com", "allmarketanalysis.com", "futuremarketreport.com", "marketintelo.com", "pmarketresearch.com"):
    check(f"report-mill host dropped on any title: {host}", search.report_page_reason(hit("Weekly note on vitamin c derivatives and what suppliers do", host)) == "a market-report site")

# ---- things that must stay: reporting something that happened, ordinary sites, trusted trade press
KEEP = [
    ("Nevis closes a USD 70 million Series B round for wealth platform", "fintechglobal.example", True),
    ("Temenos completes its purchase of additiv in July", "msn.example", True),
    ("Providers adopt AI interpreters across hospital networks", "healthcareit.example", True),
    ("Marketing teams adopt AI translation for campaigns", "marketingweek.example", True),
    ("Google launches an AI translator for market research teams", "techblog.example", False),
    ("Why supermarkets are testing receipt scanning", "supermarketnews.example", True),
    ("MarketWatch: bank adopts new translation service", "marketwatch.com", True),
    ("Investors weigh the wealth management platform market after the merger", "fiercefinance.example", True),
]
for title, host, news in KEEP:
    check(f"kept: {title[:58]!r}", search.report_page_reason(hit(title, host, news)) is None, str(search.report_page_reason(hit(title, host, news))))

# ---- inside filter_hits the report pages are gone and the news stays
hits = [hit(t, h, n, snippet="vitamin c derivatives suppliers") for t, h, n in REPORTS[:6]] + [
    hit("Vitamin C derivatives supplier opens a new plant in Germany", "chemnews.example", True, "vitamin c derivatives supplier opens a new plant"),
]
kept = search.filter_hits(hits, ["vitamin c derivatives market"], logger=LOG)
check("filter_hits: no market-report page reaches the evidence list", all(search.report_page_reason(h) is None for h in kept), str([h.title for h in kept]))
check("filter_hits: the real news item is kept", any("new plant in Germany" in h.title for h in kept), str([h.title for h in kept]))
claim_kept = search.filter_claim_hits(
    [hit("Vitamin C Derivatives Market Size, Share, Trends & Growth ...", "allmarketanalysis.com", False, "vitamin c derivatives stability claim")],
    "vitamin c derivatives stay stable in a finished product", logger=LOG)
check("filter_claim_hits: a report page never backs a claim either", claim_kept == [])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all report-page tests passed")
