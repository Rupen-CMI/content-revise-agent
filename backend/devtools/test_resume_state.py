"""Offline test for the Step 1 -> Step 2 hand-off: the run's report mode, warnings, the competitor
report candidates /step2/find_reports found, and the editor's own picks (markets, CMI report ids,
pasted urls) survive on the RunState and are returned by GET /run/{run_token} (main.get_run), so the
browser can rehydrate a run after a page reload. No network, no LLM — external_reports.
find_candidate_pages is faked in-process and main._step2_v2 is stubbed, so no page is ever fetched.

Run from backend/: python devtools/test_resume_state.py
"""
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import external_reports  # noqa: E402
import main  # noqa: E402
import reports  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_resume_state")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# --------------------------------------------------------------------------- fixtures

def entry(nid: int, kw: str) -> reports.CatalogEntry:
    return reports.CatalogEntry(news_id=nid, keyword=kw, slug=f"n{nid}", fmt=0, has_summary=True)


def make_run(token: str, candidates: list[reports.CatalogEntry] | None = None) -> main.RunState:
    """A minimal but real RunState, registered under `token` (each section removes it in a finally)."""
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
def patched(obj, name: str, fn):
    orig = getattr(obj, name)
    setattr(obj, name, fn)
    try:
        yield
    finally:
        setattr(obj, name, orig)


def fake_find(markets, exclude_hosts, logger, per_market=6):
    """Two fake competitor-report candidates, one per market; real shape ({url, title, host, snippet, market})."""
    return [
        {"url": "https://no1.com/azia-report", "title": "Azia Market Report 2026", "host": "no1.com",
         "snippet": "the size of it", "market": markets[0]},
        {"url": "https://no2.com/b2-report", "title": "B2 Market Review", "host": "no2.com",
         "snippet": "reviewed here", "market": markets[-1]},
    ]


MESSY = ["  Azia Market  ", "azia market", " B2 Market", "", "C3 Market", "d4 market", "E5 Market"]
CLEAN = ["Azia Market"]   # R1: at most ONE market — the first non-empty entry, trimmed


def never_step2_v2(*args, **kwargs):
    raise AssertionError("_step2_v2 must run with no report fetch behind this test")


TOKEN = "resume-test-token"


def drop_run() -> None:
    main._RUNS.pop(TOKEN, None)


# --------------------------------------------------------------------------- fresh run defaults

try:
    run = make_run(TOKEN)
    snap = main.get_run(TOKEN)
    fresh_keys = ("report_mode", "warnings", "found_reports", "picked_markets",
                  "picked_report_ids", "picked_urls", "sources")
    check("get_run: all seven hand-off keys are present on a fresh run",
          all(k in snap for k in fresh_keys),
          str([k for k in fresh_keys if k not in snap]))
    check("get_run: a fresh run's defaults are empty lists and report_mode 'cmi'",
          snap["report_mode"] == "cmi" and snap["warnings"] == [] and snap["sources"] == []
          and snap["found_reports"] == [] and snap["picked_markets"] == []
          and snap["picked_report_ids"] == [] and snap["picked_urls"] == [],
          str({k: snap[k] for k in fresh_keys}))
    check("get_run: the agent's own market (external_reports.own_market) is surfaced for the merged Market & Reports screen",
          snap["own_market"] == "", str(snap.get("own_market")))
    run.orient = {"topic_summary": "the topic", "bridge_angle": "the angle", "own_market": "Widget Market, By End User"}
    check("get_run: the run's orient own_market comes back bare-named (segmentation qualifier stripped)",
          main.get_run(TOKEN)["own_market"] == "Widget Market", str(main.get_run(TOKEN)["own_market"]))
    # whatever the pipeline writes on the RunState later is read back too
    run.report_mode = "mixed"
    run.warnings = ["Two markets were dropped: too many."]
    again = main.get_run(TOKEN)
    check("get_run: a mutated report_mode and its warnings are read back",
          again["report_mode"] == "mixed"
          and again["warnings"] == ["Two markets were dropped: too many."])
finally:
    drop_run()

# ----------------------------------------------------------------------- /step2/find_reports

try:
    make_run(TOKEN)
    with patched(external_reports, "find_candidate_pages", fake_find), patched(external_reports, "read_page", lambda u, l: "readable page text"):
        with patched(main, "_step2_v2", never_step2_v2):
            resp = main.find_reports(main.FindReportsRequest(run_token=TOKEN, markets=MESSY))
    check("find_reports: the response shape is unchanged (markets + the found candidates)",
          resp["markets"] == CLEAN and len(resp["reports"]) == 2, str(resp)[:120])
    snap = main.get_run(TOKEN)
    check("find_reports: get_run returns the found candidates verbatim",
          snap["found_reports"] == resp["reports"], str(snap["found_reports"])[:120])
    check("find_reports: get_run returns the single cleaned market "
          "(trimmed, case-duplicates dropped, empty dropped, first kept)",
          snap["picked_markets"] == CLEAN, str(snap["picked_markets"]))
    # a second search overwrites the first — the run keeps only the latest picker state
    with patched(external_reports, "find_candidate_pages", lambda m, h, l, per_market=6: []):
        main.find_reports(main.FindReportsRequest(run_token=TOKEN, markets=["Only One Market"]))
    snap = main.get_run(TOKEN)
    check("find_reports: a later search replaces the candidates and the markets",
          snap["found_reports"] == [] and snap["picked_markets"] == ["Only One Market"],
          str(snap["found_reports"]) + str(snap["picked_markets"]))
finally:
    drop_run()

# ----------------------------------------------------------------------- /reports/fetch recording

try:
    run = make_run(TOKEN, candidates=[entry(1, "Azia Market"), entry(2, "B2 Market"), entry(3, "C3 Market")])
    run.found_reports = fake_find(["Azia Market"], [], LOG)
    seen = {}

    def stub_build(r, picked, urls, **kw):
        seen.update(picked=[c.news_id for c in picked], urls=list(urls),
                    markets=list(kw.get("markets", [])))
        return {"report": reports.Report(news_id=2, name="B2 Market", subject="B2 Market", url="", source="", sections={}),
                "fact_sheet": {"gaps": [], "sources": []}, "report_mode": "cmi", "warnings": []}

    with patched(main, "_build_reports", stub_build):
        out = main.reports_fetch(main.ReportsFetchRequest(
            run_token=TOKEN, kind="cmi", report_ids=[2, 1],
            report_urls=[" https://own.com/a ", "   ", "https://own.com/b "],
            markets=MESSY, url_markets={}))
    # R2/R3: a CMI report was chosen, so it is used alone - only its first id, no market, no links.
    check("fetch: a CMI report chosen - the build receives only its FIRST id and no urls/markets",
          out["kind"] == "cmi" and seen["picked"] == [2]
          and seen["urls"] == [] and seen["markets"] == [], str(seen))
    snap = main.get_run(TOKEN)
    check("fetch: get_run returns only the first confirmed CMI report id",
          snap["picked_report_ids"] == [2], str(snap["picked_report_ids"]))
    check("fetch: with a CMI report chosen, the pasted urls are dropped from the run's picks too",
          snap["picked_urls"] == [], str(snap["picked_urls"]))
    check("fetch: with a CMI report chosen, the market pick is dropped from the run's picks too",
          snap["picked_markets"] == [], str(snap["picked_markets"]))
    check("fetch: the earlier found_reports are NOT cleared by fetching",
          len(snap["found_reports"]) == 2, str(snap["found_reports"])[:120])
finally:
    drop_run()

# ------------------------------------------------------- /reports/fetch recording on the competitor tab

try:
    run = make_run(TOKEN, candidates=[entry(1, "Azia Market"), entry(2, "B2 Market")])
    seen = {}

    def stub_build_no_cmi(r, picked, urls, **kw):
        seen.update(picked=[c.news_id for c in picked], urls=list(urls),
                    markets=list(kw.get("markets", [])))
        return {"report": reports.Report(news_id=0, name="Azia Market", subject="Azia Market", url="", source="", sections={}),
                "fact_sheet": {"gaps": [], "sources": []}, "report_mode": "external", "warnings": []}

    with patched(main, "_build_reports", stub_build_no_cmi):
        main.reports_fetch(main.ReportsFetchRequest(
            run_token=TOKEN, kind="competitor", report_ids=[2],
            report_urls=[" https://own.com/a ", "   ", "https://own.com/b "],
            markets=MESSY, url_markets={}))
    snap = main.get_run(TOKEN)
    # competitor tab: the single picked market and the trimmed pasted urls are kept; CMI ids are ignored
    check("fetch: competitor tab - the build receives no picked report but the market and the one (first) url",
          seen["picked"] == [] and seen["urls"] == ["https://own.com/a"]
          and seen["markets"] == CLEAN, str(seen))
    check("fetch: competitor tab - get_run keeps the url pick and the single market pick",
          snap["picked_urls"] == ["https://own.com/a"]
          and snap["picked_markets"] == CLEAN and snap["picked_report_ids"] == [],
          str(snap["picked_urls"]) + str(snap["picked_markets"]) + str(snap["picked_report_ids"]))
finally:
    drop_run()

# ------------------------------------------------------------- the report's own covered market and host

try:
    # a stand-in read: the page states no figure for the picked market, but does for the market its title names
    calls = []

    def fake_build(market, pages, logger):
        calls.append([m for _, _, m in pages])
        if len(calls) == 1:
            return {}, [], {}
        return {"gaps": []}, [{"url": pages[0][0], "name": "p", "kind": "external"}], {pages[0][0]: "text"}

    warns: list[str] = []
    covered: list[str] = []
    page = ("https://finance.yahoo.com/r", "page text", "Human Translation Services Market")
    with patched(external_reports, "build_external_sheet", fake_build), \
            patched(external_reports, "page_market_name", lambda title, text: "Machine Translation Market"):
        main._use_outside_pages("Human Translation Services Market", [page], {}, warns, LOG, covered)
    check("outside pages: the related market that stands in is reported to the caller",
          covered == ["Machine Translation Market"] and "related" in warns[0], str(covered) + str(warns))

    make_run(TOKEN)

    def stub_build_standin(r, picked, urls, **kw):
        return {"report": reports.Report(news_id=0, name="Human Translation Services Market", subject="x", url="", source="", sections={}),
                "fact_sheet": {"gaps": [], "sources": []}, "report_mode": "external", "warnings": [],
                "covers_market": "Machine Translation Market", "host": "finance.yahoo.com"}

    with patched(main, "_build_reports", stub_build_standin):
        out = main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="competitor", markets=["Human Translation Services Market"]))
    check("fetch: the response names the market the report covers and its host",
          out["report"]["covers_market"] == "Machine Translation Market" and out["report"]["host"] == "finance.yahoo.com"
          and out["report"]["name"] == "Human Translation Services Market", str(out["report"])[:200])
    tab = main.get_run(TOKEN)["report_tabs"]["competitor"]["report"]
    check("fetch: get_run keeps them per tab for a reload",
          tab["covers_market"] == "Machine Translation Market" and tab["host"] == "finance.yahoo.com", str(tab))
finally:
    drop_run()

# ------------------------------------------------------------------- the CMI screen's pick (before any fetch)

try:
    make_run(TOKEN)
    check("cmi_choice: a fresh run has none", main.get_run(TOKEN)["cmi_choice"] == {}, str(main.get_run(TOKEN)["cmi_choice"]))
    main.reports_cmi_choice(main.CmiChoiceRequest(run_token=TOKEN, report_id=7))
    check("cmi_choice: a picked report comes back from get_run",
          main.get_run(TOKEN)["cmi_choice"] == {"report_id": 7, "cmi_report_url": ""}, str(main.get_run(TOKEN)["cmi_choice"]))
    main.reports_cmi_choice(main.CmiChoiceRequest(run_token=TOKEN, report_id=7, cmi_report_url="  https://www.coherentmi.com/x  "))
    check("cmi_choice: a pasted link clears the pick (and is trimmed)",
          main.get_run(TOKEN)["cmi_choice"] == {"report_id": None, "cmi_report_url": "https://www.coherentmi.com/x"},
          str(main.get_run(TOKEN)["cmi_choice"]))
    main.reports_cmi_choice(main.CmiChoiceRequest(run_token=TOKEN, report_id=3))
    check("cmi_choice: a pick clears the link",
          main.get_run(TOKEN)["cmi_choice"] == {"report_id": 3, "cmi_report_url": ""}, str(main.get_run(TOKEN)["cmi_choice"]))
finally:
    drop_run()

# ------------------------------------------------------------------- fact_sheet sources

try:
    run = make_run(TOKEN)
    run.fact_sheet = {"markets": {}, "sources": [{"url": "https://src.com/1", "title": "S1"}]}
    snap = main.get_run(TOKEN)
    check("fact_sheet sources: get_run surfaces them from the fact sheet",
          snap["sources"] == [{"url": "https://src.com/1", "title": "S1"}], str(snap["sources"]))
    run.fact_sheet = None
    snap = main.get_run(TOKEN)
    check("fact_sheet sources: a run with no fact sheet yet returns an empty list",
          snap["sources"] == [], str(snap["sources"]))
finally:
    drop_run()

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all resume-state tests passed")
