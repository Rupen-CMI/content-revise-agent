"""Offline test for the report-source exclusive rules (R1-R3): a Reports-step fetch uses EXACTLY ONE of
(a) a CMI report alone (the CMI tab), (b) one agent-suggested market name, (c) the editor's own report links
(the competitor tab). Concretely: _clean_markets returns ONE market, find_reports passes exactly that one
(and starts no evidence search), /reports/fetch with kind "cmi" keeps only the first CMI report id and ignores any
market and pasted links, kind "competitor" ignores CMI ids and refuses CMI links, and _build_reports with a chosen
CMI report touches NO outside page at all — no competitor
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

    # no evidence search starts from find_reports any more: evidence is collected once, after the fact sheet is confirmed
    with patched(external_reports, "find_candidate_pages", fake_find), \
            patched(external_reports, "read_page", lambda u, l: "readable page text"):
        resp = main.find_reports(main.FindReportsRequest(
            run_token=TOKEN, markets=["  Azia Market ", "B2 Market", "C3 Market"]))
        main.get_run(TOKEN)
    check("find_reports: no early evidence search exists any more",
          not hasattr(main, "_early_evidence") and not hasattr(main.RunState, "early_future")
          and not any(r.path == "/step2/early_evidence" for r in main.app.routes))
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

# ------------------------------------------------------------------ /reports/fetch routing: CMI report used alone

def fetch_stub(seen):
    def stub_build(r, picked, urls, **kw):
        seen.update(picked=[c.news_id for c in picked], urls=list(urls), markets=list(kw.get("markets", [])),
                    cmi_pages=list(kw.get("cmi_pages", [])))
        return {"report": cmi_report(), "fact_sheet": facts._empty_fact_sheet() | {"sources": [], "variance": []},
                "report_mode": "cmi", "warnings": []}
    return stub_build


def cmi_report() -> reports.Report:
    return reports.Report(
        news_id=7211, name=NAME, subject=MARKET, url=URL, source="cmi.example",
        sections={"live_report_page": "Some market text with no figures at all.\n"},
        segment_terms=["Type"])


try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET), entry(7212, "B2 Market")])
    seen = {}
    with patched(main, "_build_reports", fetch_stub(seen)):
        main.reports_fetch(main.ReportsFetchRequest(
            run_token=TOKEN, kind="cmi", report_ids=[7211], report_urls=[FIRST_URL, "   ", SECOND_URL],
            markets=[MARKET], url_markets={FIRST_URL: MARKET}))
    check("fetch cmi: a CMI report picked - the build gets urls == [] (links ignored, R3)", seen["urls"] == [], str(seen))
    check("fetch cmi: a CMI report picked - the build gets markets == [] (market ignored, R1)", seen["markets"] == [], str(seen))
    check("fetch cmi: picked_urls on the run stays empty", run.picked_urls == [], str(run.picked_urls))
    check("fetch cmi: the run records only the CMI report id, no market",
          run.picked_report_ids == [7211] and run.picked_markets == [], f"{run.picked_report_ids} / {run.picked_markets}")

    # two ids sent: only the FIRST is used - the second is never validated or recorded
    seen.clear()
    with patched(main, "_build_reports", fetch_stub(seen)):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", report_ids=[7211, 999999]))
    check("fetch cmi: two report ids sent - only the first is used (an unknown second id is irrelevant)",
          seen["picked"] == [7211] and run.picked_report_ids == [7211], f"{seen} / {run.picked_report_ids}")

    raised = None
    try:
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", report_ids=[], markets=[MARKET]))
    except main.HTTPException as e:
        raised = e
    check("fetch cmi: nothing picked is a 400 (a market pick does not count on the CMI tab)",
          raised is not None and raised.status_code == 400, str(raised))
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}
    with patched(main, "_build_reports", fetch_stub(seen)):
        main.reports_fetch(main.ReportsFetchRequest(
            run_token=TOKEN, kind="competitor", report_ids=[7211], report_urls=[f" {FIRST_URL} ", "", SECOND_URL],
            markets=[f"  {MARKET}  ", "B2 Market"], url_markets={}, cmi_report_url="https://www.coherentmarketinsights.com/x"))
    check("fetch competitor: the ONE kept market and the FIRST trimmed url are used; CMI ids and link are ignored",
          seen["picked"] == [] and seen["urls"] == [FIRST_URL] and seen["markets"] == [MARKET]
          and seen["cmi_pages"] == [], str(seen))
    check("fetch competitor: the run records the same market and the one url",
          run.picked_markets == [MARKET] and run.picked_urls == [FIRST_URL] and run.picked_report_ids == [],
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
        built = main._build_reports(run, [main._RUNS[TOKEN].candidates[0]], [], markets=[MARKET], url_markets={})
    check("_build_reports CMI: no search demand, publisher, evidence or query planning runs in the Reports step",
          all(flags[k] == 0 for k in ("search_demand", "publisher", "search_queries", "evidence", "us")), str(flags))
    run.report_tabs["cmi"] = built
    main._activate_report(run, "cmi")
    refused = None
    with patched_all(*stubs):
        try:
            main.step2(main.Step2Request(run_token=TOKEN))
        except main.HTTPException as e:
            refused = e
    check("evidence: refused (400) until the fact sheet is confirmed, and nothing was searched",
          refused is not None and refused.status_code == 400 and flags["evidence"] == 0 and flags["fetch"] == 1,
          f"{refused} {flags}")
    main.reports_confirm(main.ReportsConfirmRequest(run_token=TOKEN))
    with patched_all(*stubs):
        out = main.step2(main.Step2Request(run_token=TOKEN))
    check("evidence: with a confirmed sheet it runs and does NOT fetch the report again",
          flags["fetch"] == 1 and flags["evidence"] == 1, str(flags))
    check("_build_reports CMI: the fact sheet comes from the fetcher and NOTHING outside is read",
          all(flags[k] == 0 for k in ("read_page", "find_pages", "build_sheet")), str(flags))
    check("CMI run: no figure gap search, no figure backing, no web fill (R3)",
          flags["gap_search"] == 0 and flags["figure_backing"] == 0 and flags["fill_web"] == 0,
          str(flags))
    check("CMI run: the report is the CMI report and the mode is 'cmi'",
          out["report"]["name"] == NAME and out["report_mode"] == "cmi", str(out["report_mode"]))
    fs = out["fact_sheet"]
    check("CMI run: the fact sheet has NO figure_source (stays unset)",
          (fs.get("figure_source") or None) is None, str(fs.get("figure_source")))
    check("CMI run: all three headline figures simply stay gaps",
          fs["gaps"] == ["size", "forecast", "cagr"], str(fs["gaps"]))
    check("CMI run: the sheet is a pure CMI sheet (no variance, no external source rows)",
          fs["variance"] == [] and fs["sources"] == [{"name": NAME, "url": URL, "kind": "cmi"}],
          str(fs["sources"]) + str(fs["variance"]))
    check("CMI run: no editor warning about outside reports",
          [w for w in out["warnings"] if "news item(s) about this market" not in w] == [], str(out["warnings"]))
    check("CMI run: gap_pages stays [] so Step 3 and the response still work",
          run.gap_pages == [], str(run.gap_pages))
    check("CMI run: the market-news evidence search STILL ran with the report's name",
          flags["evidence"] == 1 and names["evidence"] == [NAME], str(flags) + str(names))
    check("CMI run: the U.S. search, search demand and publisher points still ran",
          flags["us"] == 1 and flags["search_demand"] == 1 and flags["publisher"] == 1, str(flags))
    check("CMI run: the search queries were planned from the CMI report's name",
          flags["search_queries"] == 1 and names["queries"] == [NAME], str(names))
    check("CMI run: the run carries the result (fact sheet, report, mode) for Step 3",
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
            main._build_reports(run, [], [], markets=[f"  {MARKET}  "], url_markets=None)
        except main.PipelineError as e:
            raised = e
    check("_build_reports no-CMI: the search was sent exactly ONE market (never plural)",
          found_markets == [MARKET], str(found_markets))
    check("_build_reports no-CMI: with nothing found the step still fails loudly for the editor",
          raised is not None and "No report could be found" in str(raised), str(raised))
finally:
    drop_run()

# ------------------------------------------- the dedicated CMI report link (its own box, never a competitor's)

CMI_LINK = "https://www.coherentmarketinsights.com/market-insight/rose-oil-market-7211"


def fetch_raises(req):
    try:
        main.reports_fetch(req)
    except main.HTTPException as e:
        return e
    return None


try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    raised = fetch_raises(main.ReportsFetchRequest(run_token=TOKEN, kind="competitor", report_urls=[CMI_LINK]))
    check("fetch competitor: a CMI link pasted among the competitor links is refused (400) and pointed at the CMI report link box",
          raised is not None and raised.status_code == 400
          and "CMI report link box" in str(raised.detail) and CMI_LINK in str(raised.detail), str(raised))
    raised = fetch_raises(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", cmi_report_url="https://reports.example/rose-oil"))
    check("fetch cmi: a non-CMI link in the CMI report link box is refused (400) naming CMI's own site",
          raised is not None and raised.status_code == 400 and "coherentmarketinsights.com" in str(raised.detail), str(raised))
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}
    with patched(main, "_build_reports", fetch_stub(seen)), patched(reports, "match_report_url", lambda u: entry(7211, MARKET)):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", cmi_report_url=CMI_LINK))
    check("fetch cmi: a CMI report link on a catalog page becomes the picked CMI report itself (R3: nothing else)",
          seen["picked"] == [7211] and seen["urls"] == [] and seen["cmi_pages"] == [], str(seen))
    check("fetch cmi: the run records that catalog report id",
          run.picked_report_ids == [7211] and run.picked_markets == [] and run.picked_urls == [],
          f"{run.picked_report_ids} / {run.picked_markets} / {run.picked_urls}")
finally:
    drop_run()

try:
    make_run(TOKEN, candidates=[entry(7211, MARKET)])
    seen = {}
    with patched(main, "_build_reports", fetch_stub(seen)), patched(reports, "match_report_url", lambda u: None):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", cmi_report_url=CMI_LINK))
    check("fetch cmi: a CMI report link outside the catalog is read as a CMI page (cmi_pages, figures never adjusted)",
          seen["picked"] == [] and seen["cmi_pages"] == [CMI_LINK], str(seen))
finally:
    drop_run()

try:
    run = make_run(TOKEN, candidates=[entry(7211, MARKET), entry(7212, "B2 Market")])
    seen = {}
    with patched(main, "_build_reports", fetch_stub(seen)), patched(reports, "match_report_url", lambda u: entry(7212, "B2 Market")):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", report_ids=[7211], cmi_report_url=CMI_LINK))
    check("fetch cmi: a ticked CMI report wins over a pasted CMI report link (the link is ignored, not an error)",
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

