"""Offline test for external_reports.find_candidate_pages and the 2/3-tuple handling of build_external_sheet.
No network, no LLM: the search calls are monkeypatched to return fixed search.SearchHit objects, and both the page
reader and the LLM call are monkeypatched to raise, so any accidental fetch or model call fails the test.

Run from backend/: python devtools/test_external_find.py
"""
import logging
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts  # noqa: E402
import feeds  # noqa: E402
import llm  # noqa: E402
import reports  # noqa: E402
import search  # noqa: E402

import external_reports  # noqa: E402

logging.basicConfig(level=logging.ERROR)

FAILURES: list[str] = []
LOG = logging.getLogger("test_external_find")
TODAY = date(2026, 10, 31)

MARKET_A = "Indoor Plant Market"
MARKET_B = "Mushroom Garden Market"

# Monitors the code under test: anything recorded here would be a fetch or an LLM call.
STATE = {"fetches": [], "llm": [], "fact_sheet_markets": []}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------------------- patched internet / model


def hit(url: str, title: str, snippet: str) -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=url, source="bing", date=datetime(2026, 9, 1))


HITS: dict[str, list[search.SearchHit]] = {
    MARKET_A: [
        hit("https://roots.example/indoor-plant-market", "Indoor Plant Market Grows Steadily",
            "The indoor plant market report sizes the Indoor Plant segment at USD 9 billion."),
        hit("https://growlight.example/indoor-gardening", "Indoor Plant Industry Outlook",
            "Indoor plant sales rise; the market outlook is bright."),
        hit("https://potting.example/plant-market-report", "Plant Market Report",
            "Another indoor plant market report, for the potting bench."),
    ],
    MARKET_B: [
        hit("https://spore.example/mushroom-market", "Mushroom Garden Market Forecast",
            "Mushroom varieties take over the garden market."),
        hit("https://fungal.example/" + "x" * 300, "Fungal Market Press Release",
            "Mushroom garden trade body statement; market analysts said " + "very much " * 40 + "the same."),
    ],
}
# The same page is offered for both markets; it must appear once, attributed to the first market.
HITS[MARKET_B].insert(1, HITS[MARKET_A][0])


def market_of_queries(text: str) -> str | None:
    if "indoor plant" in text:
        return MARKET_A
    if "mushroom garden" in text:
        return MARKET_B
    return None


def fake_bing_search(news_queries, web_queries, logger):
    which = market_of_queries(" ".join([*news_queries, *web_queries]).lower())
    if which is not None and getattr(fake_bing_search, "fail_for", None) == which:
        raise ConnectionError("patches: the search engine is down (simulated)")
    return HITS.get(which, [])


def fake_run_text_searches(queries, logger):
    which = market_of_queries(" ".join(queries).lower())
    return HITS.get(which, [])[1:]  # a slightly different set, as a second engine would give


def fake_fetch_live_page(url, logger=None):
    STATE["fetches"].append(url)
    raise AssertionError(f"no page may be fetched in this test, but {url} was")


def fake_chat_json(*args, **kwargs):
    STATE["llm"].append(args)
    raise AssertionError("no LLM call may happen in this test")


def fake_page_fact_sheet(market_name: str, url: str, text: str, logger, today=None) -> dict:
    """Exactly what the real page_fact_sheet does around the model: validate, then vary. Records its market."""
    STATE["fact_sheet_markets"].append(market_name)
    quote = f"The {market_name} is estimated to be valued at USD 500.0 million in 2025 and is expected to reach " \
            f"USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032"
    if "unusable" in url:
        return facts._empty_fact_sheet()
    raw = {"size": {"value": "USD 500.0 million", "year": "2025", "quote": quote}}
    fs = facts.validate_fact_sheet(raw, text, market_name, logger, today,
                                   outside={"name": external_reports.host_of(url), "url": url})
    external_reports.variance.vary_fact_sheet(fs, market_name)
    return fs


ORIG = {"bing": feeds.bing_search, "text": search.run_text_searches, "fetch": reports.fetch_live_page,
        "llm": llm.chat_json, "pfs": external_reports.page_fact_sheet}
feeds.bing_search = fake_bing_search
search.run_text_searches = fake_run_text_searches
reports.fetch_live_page = fake_fetch_live_page
llm.chat_json = fake_chat_json
external_reports.page_fact_sheet = fake_page_fact_sheet

try:
    # ---------------------------------------------------------------------------- find_candidate_pages
    found = external_reports.find_candidate_pages([MARKET_A, MARKET_B], ["sponsor.example"], LOG)
    urls = [c["url"] for c in found]
    check("find: candidates come back in market order (A's before B's)",
          urls[:2] == ["https://roots.example/indoor-plant-market", "https://growlight.example/indoor-gardening"],
          str(urls))
    check("find: every candidate carries the market it was found for",
          {c["market"] for c in found} == {MARKET_A, MARKET_B}
          and all(c["market"] == MARKET_A for c in found if c["url"].endswith("indoor-gardening")),
          str([(c["url"], c["market"]) for c in found]))
    check("find: a url offered for two markets is deduplicated to one candidate",
          urls.count("https://roots.example/indoor-plant-market") == 1, str(urls))
    long_snippet = next(c["snippet"] for c in found if "Fungal" in str(c["title"]))
    check("find: snippets are capped at 240 characters", len(long_snippet) == 240, str(len(long_snippet)))
    check("find: every candidate carries a host without www", all(c["host"] and "www." not in c["host"] for c in found)
          and "roots.example" in {c["host"] for c in found}, str({c["host"] for c in found}))
    check("find: no page was fetched and no LLM call happened", not STATE["fetches"] and not STATE["llm"])

    few = external_reports.find_candidate_pages([MARKET_A], ["sponsor.example"], LOG, per_market=1)
    check("find: per_market caps how many candidates one market returns", len(few) == 1, str(len(few)))

    STATE["fetches"].clear()
    fake_bing_search.fail_for = MARKET_B
    broken = external_reports.find_candidate_pages([MARKET_B, MARKET_A], ["sponsor.example"], LOG)
    check("find: a market whose search fails is skipped, the others still come back",
          [c["market"] for c in broken] == [MARKET_A] * 3, str(broken))
    check("find: the failed market's search still fetched nothing, no exception raised",
          not STATE["fetches"] and not STATE["llm"])
    del fake_bing_search.fail_for

    # ---------------------------------------------------------------------------- build_external_sheet
    STATE["llm"].clear()

    def text_for(market: str) -> str:
        return ("The " + market + " is estimated to be valued at USD 500.0 million in 2025 and is expected to reach "
                "USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032. Extra context sentence.")

    # 2-tuples: the market_name argument scopes every page.
    STATE["fact_sheet_markets"].clear()
    merged, used, texts = external_reports.build_external_sheet(
        MARKET_A, [("https://pages.example/a", text_for(MARKET_A))], LOG, today=TODAY)
    check("sheet: a (url, text) page is scoped to the market_name argument", STATE["fact_sheet_markets"] == [MARKET_A],
          str(STATE["fact_sheet_markets"]))
    check("sheet: the merged sheet keeps the page's figure and names the source external",
          bool(merged["size"]) and used[0]["kind"] == "external" and used[0]["url"] == "https://pages.example/a",
          f"{merged['size']} / {used}")
    check("sheet: the returned texts map holds the page's text", texts == {"https://pages.example/a": text_for(MARKET_A)})

    # 3-tuples: each page is scoped to the market in its third element.
    STATE["fact_sheet_markets"].clear()
    merged3, used3, texts3 = external_reports.build_external_sheet(
        MARKET_A,
        [("https://pages.example/b", text_for(MARKET_B), MARKET_B),
         ("https://pages.example/unusable", "nothing here", MARKET_B),
         ("https://pages.example/c", text_for(MARKET_A), "")],
        LOG, today=TODAY)
    check("sheet: a page's own market wins over the default (empty third element falls back)",
          sorted(STATE["fact_sheet_markets"]) == sorted([MARKET_B, MARKET_B, MARKET_A]), str(STATE["fact_sheet_markets"]))
    check("sheet: a page that states nothing usable is left out of sources and texts",
          "https://pages.example/unusable" not in texts3 and len(used3) == 2, f"{used3} / {texts3}")
    check("sheet: no LLM call happened while building the sheets", not STATE["llm"])

finally:
    feeds.bing_search = ORIG["bing"]
    search.run_text_searches = ORIG["text"]
    reports.fetch_live_page = ORIG["fetch"]
    llm.chat_json = ORIG["llm"]
    external_reports.page_fact_sheet = ORIG["pfs"]

# ---------------------------------------------------------------------------- what counts as a report page
check("candidates: an encyclopedia page is never a report", not external_reports.looks_like_report("en.wikipedia.org", "Internet market history", "market"))
check("candidates: a page with no market wording is not a report", not external_reports.looks_like_report("site.example", "A history of the web", "hypertext and links"))
check("candidates: a market report page is", external_reports.looks_like_report("rival.example", "Widget Market Size and Forecast", ""))

# a page with segments but no headline figure for THIS market (a neighbouring market's page) is not used at all
def segments_only(market_name, url, text, logger, today=None):
    fs = facts._empty_fact_sheet()
    fs["segments"] = [{"id": "fs.segments.0", "type": "By Type", "items": ["A", "B"], "figures": {}, "dominant": None}]
    fs["drivers"] = [{"id": "fs.drivers.0", "text": "Demand grows.", "quote": "q", "historical": False}]
    fs["external"] = {"name": "x", "url": url}
    return fs

external_reports.page_fact_sheet = segments_only
try:
    merged_none, used_none, texts_none = external_reports.build_external_sheet(MARKET_A, [("https://n.example/page", "text")], logging.getLogger("t"))
finally:
    external_reports.page_fact_sheet = fake_page_fact_sheet
check("scope: a page that states no headline figure for the market contributes nothing, not even its segments",
      used_none == [] and texts_none == {} and merged_none["segments"] == [], f"{used_none} {merged_none['segments']}")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all external find tests passed")
