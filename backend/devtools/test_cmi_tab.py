"""Offline test for the CMI tab of the Reports step: a picked report id is used, a pasted link that IS a catalog
report routes to that catalog report, a pasted CMI link outside the catalog is read as a CMI page (its figures are
used as the report states them, never varied), a non-CMI link is refused, and the endpoint records the editor's
picks on the run so GET /run can restore them after a reload. No network, no LLM.

Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_cmi_tab.py
"""
import logging
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fastapi  # noqa: E402
import facts  # noqa: E402
import main  # noqa: E402
import reports  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_cmi_tab")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

TOKEN = "cmi-tab-token"


def check(name: str, ok: bool, detail: str = "") -> None:
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
    stack = ExitStack()
    for obj, name, fn in specs:
        stack.enter_context(patched(obj, name, fn))
    return stack


def make_run(candidates=()):
    run = main.RunState(
        filename="upload.docx", article=SourceArticle(title="A Blog Title", paragraphs=[]),
        sponsor=Paragraph(index=0, text="Sponsor line."), orient={"topic_summary": "t", "bridge_angle": "a"},
        candidates=list(candidates), logger=LOG, log_path=Path("devtools/never_written.log"),
    )
    main._RUNS[TOKEN] = run
    return run


def resolve(kind="cmi", report_ids=(), cmi_report_url=""):
    req = main.ReportsFetchRequest(run_token=TOKEN, kind=kind, report_ids=list(report_ids), cmi_report_url=cmi_report_url)
    return main._resolve_report_pick(main._RUNS[TOKEN], req)


def raised_http(fn):
    try:
        fn()
        return None
    except fastapi.HTTPException as e:
        return e


# A real catalog report (the url forms report_url writes) and a CMI link that is not in the catalog.
ENTRY = reports.catalog()[0]
UNLISTED = "https://www.coherentmarketinsights.com/industry-reports/zz-test-unlisted-market-9999"

check("guard: the catalog test link matches a catalog report", reports.match_report_url(ENTRY.url) is not None)
check("guard: the unlisted test link is not a catalog report", reports.match_report_url(UNLISTED) is None)

try:
    make_run(candidates=[ENTRY])

    # ----------------------------------------------------------------------------------- _resolve_report_pick
    picked, urls, markets, cmi_pages = resolve(report_ids=[ENTRY.news_id])
    check("picked report id: the catalog report is the pick, nothing else",
          picked == [ENTRY] and urls == [] and markets == [] and cmi_pages == [], f"{picked} {cmi_pages}")

    picked, urls, markets, cmi_pages = resolve(cmi_report_url=ENTRY.url)
    check("catalog CMI link: it routes to the catalog report, not read as a separate page",
          picked == [ENTRY] and cmi_pages == [], f"{picked} {cmi_pages}")

    picked, urls, markets, cmi_pages = resolve(cmi_report_url=UNLISTED)
    check("unlisted CMI link: read as a CMI page, no catalog pick",
          picked == [] and cmi_pages == [UNLISTED], f"{picked} {cmi_pages}")

    picked, urls, markets, cmi_pages = resolve(report_ids=[ENTRY.news_id], cmi_report_url=UNLISTED)
    check("pick + link: the pick wins and the link is ignored (R1)",
          picked == [ENTRY] and cmi_pages == [], f"{picked} {cmi_pages}")

    e = raised_http(lambda: resolve(cmi_report_url="https://straits.example/translation-service-market"))
    check("non-CMI link: refused (its figures would be treated as a competitor's and adjusted)",
          e is not None and e.status_code == 400 and "coherentmarketinsights.com" in e.detail, str(e))

    e = raised_http(resolve)
    check("nothing picked: refused with what to do instead",
          e is not None and e.status_code == 400 and "Pick a CMI report" in e.detail, str(e))

    e = raised_http(lambda: resolve(report_ids=[424242]))
    check("a report id not matched for this run: refused",
          e is not None and e.status_code == 400, str(e))

    # ------------------------------------------------------------------- fetch: a CMI page is never varied
    def fake_fetch_page(url, market, logger=LOG):
        return reports.Report(news_id=0, name=f"{market} (page)", subject=market, url=url, source="live",
                              sections={"live_report_page": "Unlisted Market page text"}, segment_terms=[])

    with patched_all(
        (reports, "fetch_report_page", fake_fetch_page),
        (facts, "run_fact_sheet", lambda name, r, logger: facts._empty_fact_sheet()),
    ):
        out = main._build_reports(main._RUNS[TOKEN], [], [], cmi_pages=[UNLISTED])
    check("unlisted CMI page: report_mode is cmi", out["report_mode"] == "cmi", str(out["report_mode"]))
    check("unlisted CMI page: figures used exactly as stated — no variance entries",
          out["fact_sheet"]["variance"] == [], str(out["fact_sheet"]["variance"]))
    check("unlisted CMI page: the source is the pasted link, kind cmi",
          out["fact_sheet"]["sources"] == [{"name": out["report"].name, "url": UNLISTED, "kind": "cmi"}],
          str(out["fact_sheet"]["sources"]))
    check("unlisted CMI page: no 3-5% adjustment warning",
          not any("adjusted by 3-5%" in w for w in out["warnings"]), str(out["warnings"]))

    # ------------------------------------------------- /reports/fetch: the picks are recorded for GET /run
    def fake_built(run, chosen, urls, markets=(), url_markets=None, cmi_pages=(), timer=None):
        rep = reports.Report(news_id=chosen[0].news_id if chosen else 0,
                             name=chosen[0].keyword if chosen else "Unlisted Market page",
                             subject="x", url=chosen[0].url if chosen else cmi_pages[0], source="live")
        return {"report": rep, "fact_sheet": facts._empty_fact_sheet(), "report_mode": "cmi", "warnings": []}

    with patched(main, "_build_reports", fake_built):
        payload = main.reports_fetch(main.ReportsFetchRequest(
            run_token=TOKEN, kind="cmi", report_ids=[ENTRY.news_id]))
    run = main._RUNS[TOKEN]
    check("fetch with a picked report id: the payload is the cmi tab's sheet",
          payload["kind"] == "cmi" and payload["report_mode"] == "cmi" and payload["confirmed"] is False, str(payload)[:200])
    check("fetch with a picked report id: recorded on the run for GET /run restore",
          run.picked_report_ids == [ENTRY.news_id] and run.picked_urls == [] and "cmi" in run.report_tabs,
          f"{run.picked_report_ids} {run.picked_urls}")

    with patched(main, "_build_reports", fake_built):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", cmi_report_url=ENTRY.url))
    check("fetch with a catalog CMI link: recorded as the picked report id, no pasted url",
          run.picked_report_ids == [ENTRY.news_id] and run.picked_urls == [], f"{run.picked_report_ids} {run.picked_urls}")

    with patched(main, "_build_reports", fake_built):
        main.reports_fetch(main.ReportsFetchRequest(run_token=TOKEN, kind="cmi", cmi_report_url=UNLISTED))
    check("fetch with an unlisted CMI link: recorded among the picked urls for GET /run restore",
          run.picked_report_ids == [] and run.picked_urls == [UNLISTED], f"{run.picked_report_ids} {run.picked_urls}")
finally:
    main._RUNS.pop(TOKEN, None)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all CMI-tab tests passed")
