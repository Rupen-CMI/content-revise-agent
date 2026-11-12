"""Offline test for what happens to the competitor reports an editor ticks in Step 2: the picker offers only pages the
agent can read (keep_readable), a ticked report on a related market stands in for the picked one with a plain warning
(_use_outside_pages), the other reports of the list are tried when nothing ticked is usable, and a failure says why.
No network, no LLM.

Run from backend/: python devtools/test_ticked_reports.py
"""
import logging
import sys
from contextlib import ExitStack, contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand  # noqa: E402
import evidence_call  # noqa: E402
import external_reports  # noqa: E402
import facts  # noqa: E402
import keyword_research  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
import publisher  # noqa: E402
import search  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_ticked_reports")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False

PICKED = "Human Translation Services Market"
TICKED = "https://straits.example/translation-service-market"
SPARE = "https://mordor.example/translation-services-market"
TOKEN = "ticked-reports-token"


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


# ----------------------------------------------------------------------------------- page_market_name
check("page_market_name: the words up to 'Market'",
      external_reports.page_market_name("Translation Service Market Size, Share, Growth, Analysis, 2034") == "Translation Service Market")
check("page_market_name: a leading 'Global' is dropped",
      external_reports.page_market_name("Global Translation Services Market Size & Forecast") == "Translation Services Market")
check("page_market_name: a title with no market word gives ''",
      external_reports.page_market_name("Human translation is projected to grow") == "")
check("page_market_name: falls back to the page's first line",
      external_reports.page_market_name("", "Zzz Widget Market Report" + chr(10) + "more") == "Zzz Widget Market")

# ----------------------------------------------------------------------------------- keep_readable
cands = [{"url": f"https://h{i}.example/r"} for i in range(5)]
texts = {"https://h0.example/r": "", "https://h1.example/r": "text", "https://h2.example/r": "",
         "https://h3.example/r": "text", "https://h4.example/r": "text"}
with patched(external_reports, "read_page", lambda u, l: texts[u]):
    kept = external_reports.keep_readable(cands, LOG, want=2)
check("keep_readable: unreadable pages are dropped, order kept, capped at `want`",
      [c["url"] for c in kept] == ["https://h1.example/r", "https://h3.example/r"], str(kept))
check("keep_readable: nothing to check gives []", external_reports.keep_readable([], LOG) == [])

# ----------------------------------------------------------------------------------- _use_outside_pages


def fake_build(market_name, pages, logger, today=None):
    """A page states figures only for the market whose name it is scoped to."""
    usable = [p for p in pages if (p[2] if len(p) > 2 else market_name) == "Translation Service Market"]
    sheet = facts._empty_fact_sheet()
    return sheet, [{"name": "straits.example", "url": p[0], "kind": "external"} for p in usable], {p[0]: p[1] for p in usable}


titles = {TICKED: "Translation Service Market Size, Share, Growth, Analysis, 2034"}
with patched(external_reports, "build_external_sheet", fake_build):
    warnings: list[str] = []
    _, sources, texts_ = main._use_outside_pages(PICKED, [(TICKED, "page text", PICKED)], titles, warnings, LOG)
check("related market: a ticked report on 'Translation Service Market' stands in for the picked market",
      [s["url"] for s in sources] == [TICKED] and TICKED in texts_, str(sources))
check("related market: the editor is told, in words, which market the figures really cover",
      any("Translation Service Market" in w and "stand in" in w and PICKED in w for w in warnings), str(warnings))
    # a page that is on the picked market exactly needs no stand-in and no stand-in warning
with patched(external_reports, "build_external_sheet", lambda m, pages, l, today=None: fake_build("Translation Service Market", pages, l)):
    warnings = []
    _, sources, _ = main._use_outside_pages("Translation Service Market", [(TICKED, "t", "Translation Service Market")], titles, warnings, LOG)
check("exact market: used as it is, no stand-in warning",
      len(sources) == 1 and not any("stand in" in w for w in warnings), str(warnings))

with patched(external_reports, "build_external_sheet", lambda m, pages, l, today=None: (facts._empty_fact_sheet(), [], {})):
    warnings = []
    _, sources, _ = main._use_outside_pages(PICKED, [(TICKED, "t", PICKED)], titles, warnings, LOG)
check("nothing usable: empty result and the page is named in a warning",
      sources == [] and any(TICKED in w for w in warnings), str(warnings))
check("no pages at all: empty result, no model call", main._use_outside_pages(PICKED, [], {}, [], LOG) == ({}, [], {}))

# ----------------------------------------------------------------------------------- _build_reports end to end


def make_run(found):
    run = main.RunState(
        filename="upload.docx", article=SourceArticle(title="A Blog Title", paragraphs=[]),
        sponsor=Paragraph(index=0, text="Sponsor line."), orient={"topic_summary": "t", "bridge_angle": "a"},
        candidates=[], logger=LOG, log_path=Path("devtools/never_written.log"),
    )
    run.found_reports = found
    main._RUNS[TOKEN] = run
    return run


def step2(found, page_text, usable_urls):
    """_step2_v2 with the ticked TICKED url; `page_text` maps url -> text ('' = unreadable); only `usable_urls` give figures."""
    run = make_run(found)

    def build(market_name, pages, logger, today=None):
        usable = [p for p in pages if p[0] in usable_urls]
        return facts._empty_fact_sheet(), [{"name": "x", "url": p[0], "kind": "external"} for p in usable], {p[0]: p[1] for p in usable}

    stubs = [
        (external_reports, "read_page", lambda u, l: page_text.get(u, "")),
        (external_reports, "build_external_sheet", build),
        (facts, "fill_headline_from_web", lambda fs, market, hosts, logger: []),
        (demand, "run_search_demand", lambda m, fs, l, real_queries=(): {"questions": []}),
        (keyword_research, "research", lambda m, l: {"suggestions": [], "pool": [], "ranking_terms": [], "method": ""}),
        (publisher, "run_publisher_points", lambda *a, **k: {"points": []}),
        (pipeline, "prepare_search_queries", lambda *a, **k: (["n"], ["w"], ["u"])),
        (evidence_call, "run_evidence_prep", lambda *a, **k: {"publisher": {"points": []}, "demand": {"questions": []},
                                                              "queries": (["n"], ["w"], ["u"])}),
        (pipeline, "run_search_evidence", lambda *a, **k: []),
    ]
    try:
        with patched_all(*stubs):
            return main._build_reports(run, [], [TICKED], markets=[PICKED], url_markets={TICKED: PICKED})
    finally:
        main._RUNS.pop(TOKEN, None)


found = [{"url": TICKED, "title": "x", "host": "straits.example", "snippet": "", "market": PICKED},
         {"url": SPARE, "title": "Translation Services Market Size", "host": "mordor.example", "snippet": "", "market": PICKED}]

out = step2(found, {TICKED: "ok text", SPARE: "other text"}, usable_urls={TICKED})
check("step2: the ticked report is used when it works, no fallback warning",
      [s["url"] for s in out["fact_sheet"]["sources"]] == [TICKED]
      and not any("could be used" in w for w in out["warnings"]), str(out["warnings"]))

out = step2(found, {TICKED: "", SPARE: "other text"}, usable_urls={SPARE})
check("step2: ticked report unreadable -> the other report of the list is used and the editor is told",
      [s["url"] for s in out["fact_sheet"]["sources"]] == [SPARE]
      and any("None of the reports you ticked could be used" in w and "mordor.example" in w for w in out["warnings"])
      and any(TICKED in w and "could not be read" in w for w in out["warnings"]), str(out["warnings"]))

raised = None
try:
    step2(found, {TICKED: "", SPARE: ""}, usable_urls=set())
except main.PipelineError as e:
    raised = e
check("step2: nothing works anywhere -> the error says WHY (which link, what happened)",
      raised is not None and TICKED in str(raised) and "could not be read" in str(raised), str(raised))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all ticked-report tests passed")
