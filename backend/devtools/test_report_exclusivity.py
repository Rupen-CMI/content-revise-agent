"""Offline test for the report-source exclusive rules (R1-R3): a Step-2 run uses EXACTLY ONE of
(a) a CMI report alone, (b) one agent-suggested market name, (c) the editor's own report links.
Concretely: _clean_markets returns ONE market, find_reports passes exactly that one, /step2 keeps
only the first CMI report id and drops the market pick and pasted links whenever a CMI report was
picked, and _step2_v2 with a chosen CMI report touches NO outside page at all — no competitor
report read, no figure gap search, no figure backing (a missing headline figure simply stays a
gap). No network, no LLM: every heavier step is stubbed to either return a small fixture or raise
with a message naming what must never be reached.

Run from backend/: python devtools/test_report_exclusivity.py
"""
import logging
import re
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import demand  # noqa: E402
import external_reports  # noqa: E402
import facts  # noqa: E402
import keyword_research  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
import publisher  # noqa: E402
import reports  # noqa: E402
import search  # noqa: E402
import us_search  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_report_exclusivity")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

MARKET = "Rose Oil Market"
NAME = "Rose Oil Market Report 2026"
URL = "https://cmi.example/market-insight/rose-oil-market-7211"
FIRST_URL = "https://x.example/a"
SECOND_URL = "https://x.example/b"
HIT_URL = "https://news.example/rose-oil"


def check(name: str, ok: bool, detail: str = "") -> None:
    name = name.replace("—", " - ")
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


@contextmanager
def patched(obj, name, fn):
    orig = getattr(obj, name)
    setattr(obj, name, fn)
    try:
        yield
    finally:
        setattr(obj, name, orig)


def patched_all(*specs):
    """Twelve stubs nest too deep for readability: enter them all on one ExitStack."""
    from contextlib import ExitStack
    stack = ExitStack()
    for obj, name, fn in specs:
        stack.enter_context(patched(obj, name, fn))
    return stack


# --------------------------------------------------------------------------- fixtures

def entry(nid: int, kw: str) -> reports.CatalogEntry:
    return reports.CatalogEntry(news_id=nid, keyword=kw, slug=f"n{nid}", fmt=0, has_summary=True)


def make_run(token: str, candidates: list[reports.CatalogEntry] | None = None) -> main.RunState:
    run = main.RunState(
        filename="upload.docx",
        article=SourceArticle(title="A Blog Title", paragraphs=[]),
        sponsor=Paragraph(index=0, text="Sponsor line."),
        orient={"topic_summary": "the topic", "bridge_angle": "the angle"},
        candidates=candidates or [],
        logger=LOG,
        log_path=Path("devtools/never_written.log"),
    )
    main._RUNS[token] = run
    return run


@contextmanager
def must_never(label: str):
    def boom(*a, **kw):
        raise AssertionError(f"{label} must not run when a CMI report was chosen")
    return boom


def hit(url: str = HIT_URL) -> search.SearchHit:
    return search.SearchHit(title="Rose oil market news", snippet="... rose oil market ...",
                            url=url, source="News Example", date=datetime(2026, 10, 1))


TOKEN = "exclusivity-test-token"


def drop_run() -> None:
    main._RUNS.pop(TOKEN, None)


# ------------------------------------------------------------------ _clean_markets: ONE market

check("clean_markets: of several sent, only the FIRST market is kept",
      main._clean_markets(["Azia Market", "B2 Market", "C3 Market"]) == ["Azia Market"])
check("clean_markets: leading empties are dropped, the first non-empty name wins",
      main._clean_markets(["", "   ", "B2 Market", "C3 Market"]) == ["B2 Market"])
check("clean_markets: the picked name is trimmed",
      main._clean_markets(["  Azia Market  "]) == ["Azia Market"])
check("clean_markets: nothing picked means no market ([])", main._clean_markets([]) == [])
check("clean_markets: a None payload means no market ([])", main._clean_markets(None) == [])
check("clean_markets: whitespace-only strings mean no market ([])",
      main._clean_markets(["", "   ", "\t"]) == [])

# ------------------------------------------------------------------ find_reports: one market only

try:
    make_run(TOKEN)
    called = []

    def fake_find(markets, exclude_hosts, logger, per_market=6):
        called.append(list(markets))
        return [{"url": "https://no1.com/report", "title": f"{MARKET} Report 2026", "host": "no1.com",
                 "snippet": "the size of it", "market": (markets or [MARKET])[0]}]

    early_calls = []

    def fake_early(run, market):
        early_calls.append(market)
        return {"market": market, "hits": [], "publisher": {}}

    # the evidence search that find_reports starts in the background is stubbed: it must not touch the network
    with patched(external_reports, "find_candidate_pages", fake_find), patched(main, "_early_evidence", fake_early),             patched(external_reports, "read_page", lambda u, l: "readable page text"):
        resp = main.find_reports(main.FindReportsRequest(
            run_token=TOKEN, markets=["  Azia Market ", "B2 Market", "C3 Market"]))
        main.get_run(TOKEN)
        main._get_run(TOKEN).early_future.result(timeout=5)
    check("find_reports: the early evidence search starts for that one market only", early_calls == ["Azia Market"], str(early_calls))
    check("find_reports: exactly ONE market reaches the search even though three were sent",
          called == [[ "Azia Market" ]], str(called))
    check("find_reports: the response's markets list holds that one name only",
          resp["markets"] == ["Azia Market"], str(resp))
    snap = main.get_run(TOKEN)
    check("find_reports: the run records the single picked market",
          snap["picked_markets"] == ["Azia Market"] and len(snap["found_reports"]) == 1, str(snap))
    with patched(external_reports, "find_candidate_pages", fake_find):
        raised = None
        try:
            main.find_reports(main.FindReportsRequest(run_token=TOKEN, markets=["", "   "]))
        except main.HTTPException as e:
            raised = e
    check("find_reports: no market at all is a 400 telling the editor to pick one",
          raised is not None and raised.status_code == 400
          and raised.detail == "Pick one market.", str(raised))
finally:
    drop_run()

# ------------------------------------------------------------------ step2: CMI report used alone

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET), entry(7212, "B2 Market")])
    seen = {}

    def stub_step2_v2(r, picked, urls, **kw):
        seen.update(picked=[c.news_id for c in picked], urls=list(urls),
                    markets=list(kw.get("markets", [])))
        return {}

    with patched(main, "_step2_v2", stub_step2_v2):
        with patched(config, "CMI_CHAIN_V2", True):
            main.step2(main.Step2Request(
                run_token=TOKEN, report_ids=[7211],
                report_urls=[FIRST_URL, "   ", SECOND_URL],
                markets=[MARKET], url_markets={FIRST_URL: MARKET}))
    check("step2: a CMI report picked — _step2_v2 gets urls == [] (links ignored, R3)",
          seen["urls"] == [], str(seen))
    check("step2: a CMI report picked — _step2_v2 gets markets == [] (market ignored, R1)",
          seen["markets"] == [], str(seen))
    check("step2: a CMI report picked — picked_urls on the run stays empty",
          run.picked_urls == [], str(run.picked_urls))
    check("step2: a CMI report picked — the run records only the CMI report id, no market",
          run.picked_report_ids == [7211] and run.picked_markets == [],
          f"{run.picked_report_ids} / {run.picked_markets}")

    # two ids sent: only the FIRST is used — the second is never validated or recorded (so an
    # unknown second id cannot even make the request fail)
    seen.clear()
    with patched(main, "_step2_v2", stub_step2_v2):
        with patched(config, "CMI_CHAIN_V2", True):
            main.step2(main.Step2Request(run_token=TOKEN, report_ids=[7211, 999999]))
    check("step2: two report ids sent — only the first is used (an unknown second id is irrelevant)",
          seen["picked"] == [7211] and run.picked_report_ids == [7211],
          f"{seen} / {run.picked_report_ids}")
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}

    def stub_step2_v2_no_cmi(r, picked, urls, **kw):
        seen.update(picked=[c.news_id for c in picked], urls=list(urls),
                    markets=list(kw.get("markets", [])))
        return {}

    with patched(main, "_step2_v2", stub_step2_v2_no_cmi):
        with patched(config, "CMI_CHAIN_V2", True):
            main.step2(main.Step2Request(
                run_token=TOKEN, report_ids=[], report_urls=[f" {FIRST_URL} ", "", SECOND_URL],
                markets=[f"  {MARKET}  ", "B2 Market"], url_markets={}))
    check("step2: NO CMI id picked — the ONE kept market and the trimmed pasted urls are used",
          seen["picked"] == [] and seen["urls"] == [FIRST_URL, SECOND_URL]
          and seen["markets"] == [MARKET], str(seen))
    check("step2: NO CMI id picked — the run records the same market and urls",
          run.picked_markets == [MARKET] and run.picked_urls == [FIRST_URL, SECOND_URL]
          and run.picked_report_ids == [],
          f"{run.picked_markets} / {run.picked_urls} / {run.picked_report_ids}")
finally:
    drop_run()

# ------------------------------------------------------ _step2_v2: CMI report is the only source

try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    run = main._RUNS[TOKEN]
    flags = {"fetch": 0, "read_page": 0, "find_pages": 0, "build_sheet": 0,
             "gap_search": 0, "figure_backing": 0, "fill_web": 0,
             "search_demand": 0, "publisher": 0, "search_queries": 0, "evidence": 0, "us": 0}
    names = {"evidence": [], "find_pages": [], "queries": []}

    def cmi_report() -> reports.Report:
        return reports.Report(
            news_id=7211, name=NAME, subject=MARKET, url=URL, source="cmi.example",
            sections={"live_report_page": "Some market text with no figures at all.\n"},
            segment_terms=["Type"])

    def fake_sheet() -> dict:
        """What facts.run_fact_sheet would return: a CMI sheet that states NO headline figure."""
        return facts._empty_fact_sheet()

    def fake_fetch_cmi_sheets(chosen, logger, page_urls=(), market=""):
        flags["fetch"] += 1
        return [cmi_report()], [fake_sheet()]

    def fake_read_page(url, logger):
        flags["read_page"] += 1
        return ""

    def fake_find_pages(market_name, exclude_hosts, logger):
        flags["find_pages"] += 1
        names["find_pages"].append(market_name)
        return []

    def fake_build_sheet(market_name, pages, logger, today=None):
        flags["build_sheet"] += 1
        return {}, [], {}

    def fake_gap_search(market_name, exclude_hosts, logger):
        flags["gap_search"] += 1
        return []

    def fake_figure_backing(*a, **kw):
        flags["figure_backing"] += 1
        return facts._empty_fact_sheet()

    def fake_fill_web(fs, market, hosts, logger):
        flags["fill_web"] += 1
        return []

    def fake_search_demand(market_name, fs, logger, real_queries=()):
        flags["search_demand"] += 1
        return {"questions": [{"id": "Q1", "question": "How fast is the market growing?"}]}

    def fake_publisher_point(*a, **kw):
        flags["publisher"] += 1
        return {"points": []}

    def fake_search_queries(market_name, players, logger, segment_terms=None):
        flags["search_queries"] += 1
        names["queries"].append(market_name)
        return (["news query"], ["web query"], ["us query"])

    def fake_search_evidence(market_name, sponsor, candidates, logger, **kw):
        flags["evidence"] += 1
        names["evidence"].append(market_name)
        return [hit()]

    def fake_us_search(market_name, sponsor_hosts, players, logger, queries):
        flags["us"] += 1
        return [hit("https://us.example/rose-oil")]

    stubs = [
        (main, "_fetch_cmi_sheets", fake_fetch_cmi_sheets),
        (external_reports, "read_page", fake_read_page),
        (external_reports, "find_pages", fake_find_pages),
        (external_reports, "build_external_sheet", fake_build_sheet),
        (facts, "run_figure_gap_search", fake_gap_search),
        (facts, "run_figure_backing", fake_figure_backing),
        (facts, "fill_headline_from_web", fake_fill_web),
        (demand, "run_search_demand", fake_search_demand),
        (keyword_research, "research", lambda m, l: {"suggestions": [], "pool": [], "ranking_terms": [], "method": ""}),
        (publisher, "run_publisher_points", fake_publisher_point),
        (pipeline, "prepare_search_queries", fake_search_queries),
        (pipeline, "run_search_evidence", fake_search_evidence),
        (us_search, "run_us_search", fake_us_search),
    ]
    with patched_all(*stubs):
        out = main._step2_v2(run, [main._RUNS[TOKEN].candidates[0]], [],
                             markets=[MARKET], url_markets={})
    check("_step2_v2 CMI: the fact sheet comes from the fetcher and NOTHING outside is read",
          all(flags[k] == 0 for k in ("read_page", "find_pages", "build_sheet")), str(flags))
    check("_step2_v2 CMI: no figure gap search, no figure backing, no web fill (R3)",
          flags["gap_search"] == 0 and flags["figure_backing"] == 0 and flags["fill_web"] == 0,
          str(flags))
    check("_step2_v2 CMI: the report is the CMI report and the mode is 'cmi'",
          out["report"]["name"] == NAME and out["report_mode"] == "cmi", str(out["report_mode"]))
    fs = out["fact_sheet"]
    check("_step2_v2 CMI: the fact sheet has NO figure_source (stays unset)",
          (fs.get("figure_source") or None) is None, str(fs.get("figure_source")))
    check("_step2_v2 CMI: all three headline figures simply stay gaps",
          fs["gaps"] == ["size", "forecast", "cagr"], str(fs["gaps"]))
    check("_step2_v2 CMI: the sheet is a pure CMI sheet (no variance, no external source rows)",
          fs["variance"] == [] and fs["sources"] == [{"name": NAME, "url": URL, "kind": "cmi"}],
          str(fs["sources"]) + str(fs["variance"]))
    check("_step2_v2 CMI: no editor warning about outside reports",
          out["warnings"] == [], str(out["warnings"]))
    check("_step2_v2 CMI: gap_pages stays [] so Step 3 and the response still work",
          run.gap_pages == [], str(run.gap_pages))
    check("_step2_v2 CMI: the market-news evidence search STILL ran with the report's name",
          flags["evidence"] == 1 and names["evidence"] == [NAME], str(flags) + str(names))
    check("_step2_v2 CMI: the U.S. search, search demand and publisher points still ran",
          flags["us"] == 1 and flags["search_demand"] == 1 and flags["publisher"] == 1, str(flags))
    check("_step2_v2 CMI: the search queries were planned from the CMI report's name",
          flags["search_queries"] == 1 and names["queries"] == [NAME], str(names))
    check("_step2_v2 CMI: the run carries the result (fact sheet, report, mode) for Step 3",
          run.report is not None and run.report.url == URL and run.fact_sheet is fs
          and run.report_mode == "cmi", "run state")
finally:
    drop_run()

# --------------------------------------- _step2_v2 without a CMI report: the market-first fallback

try:
    make_run(TOKEN)
    run = main._RUNS[TOKEN]
    found_markets: list[str] = []

    def fake_find_pages_empty(market_name, exclude_hosts, logger):
        found_markets.append(market_name)
        return []

    def fake_fill_empty(fs, market, hosts, logger):
        return []

    with patched(external_reports, "find_pages", fake_find_pages_empty), \
            patched(external_reports, "read_page", must_never("external_reports.read_page")), \
            patched(facts, "fill_headline_from_web", fake_fill_empty):
        raised = None
        try:
            main._step2_v2(run, [], [], markets=[f"  {MARKET}  "], url_markets=None)
        except main.HTTPException as e:
            raised = e
    check("_step2_v2 no-CMI: the search was sent exactly ONE market (never plural)",
          found_markets == [MARKET], str(found_markets))
    check("_step2_v2 no-CMI: with nothing found the step still fails loudly for the editor",
          raised is not None and raised.status_code == 500 and "No report could be found" in str(raised.detail),
          str(raised))
finally:
    drop_run()

# ------------- _step2_v2 with an agent-picked market: the early evidence search is reused, not repeated

from concurrent.futures import Future  # noqa: E402


def early_case(players):
    """Run _step2_v2 for a market pick whose early evidence is already in; returns (flags, hits urls, publisher)."""
    make_run(TOKEN)
    r = main._RUNS[TOKEN]
    done = Future()
    done.set_result({"market": MARKET, "hits": [hit("https://early.example/one")], "publisher": {"points": ["early point"]}})
    r.early_future, r.early_market = done, MARKET
    flags = {"publisher": 0, "us": 0, "evidence": 0, "queries": 0, "demand": 0}

    def ext_sheet(market_name, pages, logger, today=None):
        sheet = facts._empty_fact_sheet()
        sheet["players"] = list(players)
        return sheet, [{"name": "Outside", "url": FIRST_URL, "kind": "external"}], {FIRST_URL: "text"}

    def count(key, ret):
        def fn(*a, **kw):
            flags[key] += 1
            return ret
        return fn

    stubs = [
        (external_reports, "read_page", lambda url, logger: "page text"),
        (external_reports, "build_external_sheet", ext_sheet),
        (facts, "fill_headline_from_web", lambda fs, market, hosts, logger: []),
        (demand, "run_search_demand", count("demand", {"questions": []})),
        (keyword_research, "research", lambda m, l: {"suggestions": [], "pool": [], "ranking_terms": [], "method": ""}),
        (publisher, "run_publisher_points", count("publisher", {"points": ["late point"]})),
        (pipeline, "prepare_search_queries", count("queries", (["n"], ["w"], ["u"]))),
        (pipeline, "run_search_evidence", count("evidence", [hit("https://late.example/two")])),
        (us_search, "run_us_search", count("us", [hit("https://us.example/three")])),
    ]
    try:
        with patched_all(*stubs):
            out = main._step2_v2(r, [], [FIRST_URL], markets=[MARKET], url_markets={FIRST_URL: MARKET})
        return flags, [h["url"] for h in out["hits"]], out["publisher"]
    finally:
        drop_run()


flags, urls_, pub = early_case(players=[])
check("early evidence: nothing to top up, so no second web search, no U.S. search, no second publisher call",
      flags["evidence"] == 0 and flags["us"] == 0 and flags["publisher"] == 0 and flags["queries"] == 0, str(flags))
check("early evidence: the early hits and publisher points are what Step 2 carries; demand still runs",
      urls_ == ["https://early.example/one"] and pub == {"points": ["early point"]} and flags["demand"] == 1,
      f"{urls_} {pub} {flags}")
flags, urls_, pub = early_case(players=["Acme"])
check("early evidence: a picked report that names players gets ONE top-up search merged after the early hits (no U.S. repeat)",
      flags["evidence"] == 1 and flags["us"] == 0 and flags["publisher"] == 0
      and urls_ == ["https://early.example/one", "https://late.example/two"], f"{flags} {urls_}")

# ------------------------------------------- step2: the dedicated CMI report link (its own box, never a competitor's)

CMI_LINK = "https://www.coherentmarketinsights.com/market-insight/rose-oil-market-7211"


def stub_step2_v2(r, picked, urls, **kw):
    seen.update(picked=[c.news_id for c in picked], urls=list(urls),
                markets=list(kw.get("markets", [])), cmi_pages=list(kw.get("cmi_pages", [])))
    return {}


try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    raised = None
    try:
        main.step2(main.Step2Request(run_token=TOKEN, report_ids=[], report_urls=[CMI_LINK], markets=[]))
    except main.HTTPException as e:
        raised = e
    check("step2: a CMI link pasted among the competitor links is refused (400) and pointed at the CMI report link box",
          raised is not None and raised.status_code == 400
          and "CMI report link box" in str(raised.detail) and CMI_LINK in str(raised.detail), str(raised))
finally:
    drop_run()

try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    raised = None
    try:
        main.step2(main.Step2Request(run_token=TOKEN, report_ids=[], report_urls=[],
                                     cmi_report_url="https://reports.example/rose-oil"))
    except main.HTTPException as e:
        raised = e
    check("step2: a non-CMI link in the CMI report link box is refused (400) naming CMI's own site",
          raised is not None and raised.status_code == 400
          and "coherentmarketinsights.com" in str(raised.detail), str(raised))
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}
    with patched(main, "_step2_v2", stub_step2_v2), patched(config, "CMI_CHAIN_V2", True), \
            patched(reports, "match_report_url", lambda u: entry(7211, MARKET)):
        main.step2(main.Step2Request(run_token=TOKEN, report_ids=[], report_urls=[], cmi_report_url=CMI_LINK))
    check("step2: a CMI report link on a catalog page becomes the picked CMI report itself (R3: nothing else)",
          seen["picked"] == [7211] and seen["urls"] == [] and seen["cmi_pages"] == [], str(seen))
    check("step2: the run records that catalog report id",
          run.picked_report_ids == [7211] and run.picked_markets == [] and run.picked_urls == [],
          f"{run.picked_report_ids} / {run.picked_markets} / {run.picked_urls}")
finally:
    drop_run()

try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}
    with patched(main, "_step2_v2", stub_step2_v2), patched(config, "CMI_CHAIN_V2", True), \
            patched(reports, "match_report_url", lambda u: None):
        main.step2(main.Step2Request(run_token=TOKEN, report_ids=[], report_urls=[], cmi_report_url=CMI_LINK))
    check("step2: a CMI report link outside the catalog is read as a CMI page (cmi_pages, figures never adjusted)",
          seen["picked"] == [] and seen["cmi_pages"] == [CMI_LINK], str(seen))
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET), entry(7212, "B2 Market")])
    seen = {}
    with patched(main, "_step2_v2", stub_step2_v2), patched(config, "CMI_CHAIN_V2", True), \
            patched(reports, "match_report_url", lambda u: entry(7212, "B2 Market")):
        main.step2(main.Step2Request(run_token=TOKEN, report_ids=[7211], report_urls=[], cmi_report_url=CMI_LINK))
    check("step2: a ticked CMI report wins over a pasted CMI report link (the link is ignored, not an error)",
          seen["picked"] == [7211] and seen["cmi_pages"] == [] and run.picked_report_ids == [7211], str(seen))
finally:
    drop_run()

# ------------------------------------------------------------------ check_url: CMI box vs competitor rows

check("check_url cmi=False: a CMI link is refused and pointed at the CMI report link box",
      (lambda r: not r["ok"] and "CMI report link box" in r["message"] and r["title"] == "")(
          external_reports.check_url(CMI_LINK, MARKET, LOG)))
with patched(reports, "match_report_url", lambda u: entry(7211, MARKET)):
    check("check_url cmi=True: a CMI link still goes through the CMI check (the catalog report is named)",
          (lambda r: r["ok"] and r.get("cmi") is True and MARKET in r["title"])(
              external_reports.check_url(CMI_LINK, MARKET, LOG, cmi=True)))
check("check_url cmi=True: a non-CMI link is refused (the CMI box takes only pages on CMI's own site)",
      (lambda r: not r["ok"] and "coherentmarketinsights.com" in r["message"] and r["title"] == "")(
          external_reports.check_url("https://reports.example/rose-oil", MARKET, LOG, cmi=True)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all report-exclusivity tests passed")


def patched_d_all(*specs):
    """Nested context managers are deep enough to be unreadable when there are twelve stubs."""
    from contextlib import ExitStack
    stack = ExitStack()
    for obj, name, fn in specs:
        stack.enter_context(patched(obj, name, fn))
    return stack
