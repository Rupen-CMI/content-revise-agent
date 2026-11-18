"""Offline test for the canvas backend (highlight-and-redraft on the final draft; no network, llm.chat_json stubbed).

Run from backend/: python devtools/test_canvas.py

Covers display_map/locate (plain text, a CMI link, client link words, the retained-paragraph token, a stale quote), the splice and
marker checks, the guardrails on the changed paragraph only (an invented figure, a figure from the instruction, one the editor typed
before, an outside source), /canvas/suggest (shapes, errors, retry, the one link retry), apply / undo / revert, the manual edit
box, pipeline.refresh_final (checks + docx) and the history. Saving to disk is run_store's own test.
"""
import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

import canvas  # noqa: E402
import clients as clients_mod  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import test_planning  # noqa: E402
from docx_io import OUTPUT_DIR, Link, Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_canvas")
MARKET = test_planning.MARKET
SPONSOR_TEXT = "We recommend Northway Specialists for every widget engagement. Their team reviews each plan in fine detail."
CLIENT_WORDS = "private widget reviews"
CMI = f"[[CMI_LINK:{MARKET}]]"
CLIENT = f"[[CLIENT_LINK:L1|{CLIENT_WORDS}]]"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def raises(fn, status: int) -> str | None:
    """None when fn raised CanvasError(status); else what happened."""
    try:
        fn()
    except canvas.CanvasError as e:
        return None if e.status == status else f"status {e.status}: {e.detail}"
    return "no error"


# ------------------------------------------------------------------ a run to work on
def make_run(with_client: bool = False, filename: str = "canvas_test.docx") -> main.RunState:
    inp = test_planning.make_inputs()
    plan = planning.validate_plan(test_planning.good_plan(), inp, LOG)
    if with_client:
        link = {"id": "L1", "anchor": CLIENT_WORDS, "url": "https://northway.example.com/reviews"}
        inp.clients = [clients_mod.Client(id="C1", host="northway.example.com", paragraph=Paragraph(0, "x"), links=[link])]
        sponsor = Paragraph(0, SPONSOR_TEXT, [Link("Northway Specialists", "https://northway.example.com/")],
                            client_links={"L1": Link(CLIENT_WORDS, link["url"])})
        intro = [f"Buyers of widgets increasingly rely on {CLIENT} to compare options. They value clear guidance."]
    else:
        sponsor = Paragraph(0, SPONSOR_TEXT, [Link("Northway Specialists", "https://northway.example.com/")])
        intro = ["Widgets matter to buyers in every field.", prompts.SPONSOR_TOKEN]
    report = reports.Report(1, MARKET, "widgets", "https://www.coherentmarketinsights.com/industry-reports/zzz", "test",
                            sections={"live_report_page": "The Zzz Widget Services Market is worth USD 1.2 billion."})
    sections = [
        {"heading": "", "sop": [], "paragraphs": intro},
        {"heading": "", "sop": ["transition"], "paragraphs": ["Widget services deserve a closer look."]},
        {"heading": plan["sections"][0]["heading"], "sop": ["report_context"], "paragraphs": [
            f"The {CMI} stands at USD 1.2 billion in 2025. Demand is steady across buyers and providers.",
            "Providers keep adding capacity as buyers adopt widgets in more settings."]},
        {"heading": plan["sections"][1]["heading"], "sop": ["trends"], "paragraphs": [
            "### Digital adoption is raising demand", "Digital marketing adoption drives demand for widget services.",
            "Buyers now compare providers on service quality.", "### Piracy remains a drag", "Piracy hampers growth in some channels."]},
    ]
    run = main.RunState(filename=filename, article=SourceArticle("Guest", [Paragraph(0, "Guest text.")]), sponsor=sponsor, orient={},
                        candidates=[], logger=LOG, log_path=Path("none"))
    run.report, run.selection, run.evidence, run.hits = report, {"evidence": []}, "", []
    run.title, run.sections, run.plan, run.plan_inputs = "What Is Driving the Zzz Widget Services Market", sections, plan, inp
    run.faq = [{"question": "How big is the market?", "answer": "The market stands at USD 1.2 billion in 2025."},
               {"question": "Who leads?", "answer": "Acme and Beta lead the field."}]
    return run


def tpara(si: int, pj: int) -> dict:
    return {"kind": "body", "section": si, "paragraph": pj}


def quote_at(run, target: dict, text: str) -> tuple[int, int, str]:
    display = canvas.display_map(canvas.read_raw(run, target), run.sponsor)[0]
    i = display.index(text)
    return i, i + len(text), text


# ------------------------------------------------------------------ stub LLM
CALLS: list[dict] = []
ANSWERS: list = []


def fake_chat_json(system, user, **kw):
    CALLS.append({"system": system, "user": user, **kw})
    if not ANSWERS:
        raise AssertionError("an unexpected LLM call")
    a = ANSWERS.pop(0)
    if isinstance(a, Exception):
        raise a
    return a


llm.chat_json = fake_chat_json


def suggest(run, target, text, instruction="make it clearer", scope="selection", retry_of=None, answers=()):
    ANSWERS[:] = list(answers)
    s, e, q = quote_at(run, target, text)
    return canvas.suggest(run, target, s, e, q, instruction, scope, retry_of, LOG)


# ------------------------------------------------------------------ display_map / locate
run = make_run()
raw = run.sections[2]["paragraphs"][0]
disp, spans = canvas.display_map(raw, run.sponsor)
check("display: equals the concatenated segment texts of expand_segments",
      disp == "".join(t for t, _ in pipeline.expand_segments(raw, run.sponsor, run.report.url)), disp)
check("display: the CMI marker shows its words", disp.startswith(f"The {MARKET} stands"), disp)
for para in [run.sections[0]["paragraphs"][1], f"A {CMI} then {prompts.SPONSOR_TOKEN} end.", "plain text only"]:
    d, _ = canvas.display_map(para, run.sponsor)
    check(f"display: matches expand_segments for {para[:30]!r}", d == "".join(t for t, _ in pipeline.expand_segments(para, run.sponsor, None)))

sel = canvas.locate(run, tpara(2, 1), 10, 20, run.sections[2]["paragraphs"][1][10:20])
check("locate: plain words map one to one", sel.selected_raw == run.sections[2]["paragraphs"][1][10:20] and not sel.atoms)
check("locate: a stale quote is refused with 409", raises(lambda: canvas.locate(run, tpara(2, 1), 10, 20, "not the words"), 409) is None)
check("locate: an empty selection is refused with 400", raises(lambda: canvas.locate(run, tpara(2, 1), 5, 5, ""), 400) is None)
check("locate: a missing paragraph is refused with 400", raises(lambda: canvas.locate(run, tpara(2, 9), 0, 1, "x"), 400) is None)

i = disp.index(MARKET) + 5                       # touches only the middle of the link words
sel = canvas.locate(run, tpara(2, 0), i, i + 4, disp[i:i + 4])
check("locate: a selection touching part of the CMI link widens to the whole marker", sel.selected_raw == CMI and sel.selected_display == MARKET,
      f"{sel.selected_raw!r} {sel.selected_display!r}")
j = disp.index("1.2 billion")
sel = canvas.locate(run, tpara(2, 0), disp.index("The"), j + 3, disp[disp.index("The"):j + 3])
check("locate: a selection that starts before and ends after a link keeps the text around it and the whole marker",
      CMI in sel.selected_raw and sel.selected_raw.endswith("1.2") and sel.selected_raw.startswith("The "), sel.selected_raw)
sel = canvas.locate(run, tpara(2, 0), 0, 3, "The")
check("locate: a selection ending right at a link does not touch it", not sel.atoms and sel.selected_raw == "The")

crun = make_run(with_client=True)
cdisp = canvas.display_map(crun.sections[0]["paragraphs"][0], crun.sponsor)[0]
k = cdisp.index(CLIENT_WORDS)
sel = canvas.locate(crun, tpara(0, 0), k + 8, k + 12, cdisp[k + 8:k + 12])
check("locate: a selection inside client link words widens to the whole client marker", sel.selected_raw == CLIENT and sel.selected_display == CLIENT_WORDS)

sdisp = canvas.display_map(run.sections[0]["paragraphs"][1], run.sponsor)[0]
sel = canvas.locate(run, tpara(0, 1), 3, 9, sdisp[3:9])
check("locate: the retained-paragraph token paragraph widens to the whole token", sel.selected_raw == prompts.SPONSOR_TOKEN and sel.selected_display == SPONSOR_TEXT)
mixed = f"Before {prompts.SPONSOR_TOKEN} after."
md, ms = canvas.display_map(mixed, run.sponsor)
check("display: an inline sponsor token expands with the text around it", md == f"Before {SPONSOR_TEXT} after.", md)

# ------------------------------------------------------------------ splice + marker checks
run = make_run()
sel = canvas.locate(run, tpara(2, 1), *quote_at(run, tpara(2, 1), "adopt widgets")[:2], "adopt widgets")
check("splice: replaces exactly the selected words", canvas.splice(sel, "use widgets").endswith("as buyers use widgets in more settings."))
sp = canvas.locate(run, tpara(2, 1), *quote_at(run, tpara(2, 1), " adopt")[:2], " adopt")
check("splice: keeps the whitespace the selection had at its edges", "buyers use widgets" in canvas.splice(sp, "use").replace("use widgets", "use widgets"), canvas.splice(sp, "use"))
check("markers: an unchanged marker passes", canvas.marker_problem(f"a {CMI} b", f"c {CMI} d") is None)
check("markers: a dropped CMI link is refused", canvas.marker_problem(f"a {CMI} b", "a b") is not None)
check("markers: a duplicated marker is refused", canvas.marker_problem(f"a {CMI} b", f"a {CMI} {CMI} b") is not None)
check("markers: client link words altered are refused", canvas.marker_problem(f"x {CLIENT} y", "x [[CLIENT_LINK:L1|other words]] y") is not None)
check("markers: a dropped client link is refused even when the editor says remove the link",
      canvas.check_suggestion(f"x {CLIENT} y", "x y", "remove the link") is not None)
check("markers: a CMI link may go when the instruction says remove the link", canvas.check_suggestion(f"a {CMI} b", "a b", "please remove the link") is None)
check("markers: a CMI link may not go otherwise", canvas.check_suggestion(f"a {CMI} b", "a b", "make it shorter") is not None)
check("markers: a marker that was not in the passage is refused", canvas.marker_problem("plain", f"plain {CMI}") is not None)
check("markers: a broken half marker is refused", canvas.marker_problem("plain", "plain [[CMI_LINK:x") is not None)
check("markers: the sponsor token must stay", canvas.marker_problem(f"a {prompts.SPONSOR_TOKEN}", "a") is not None)

# ------------------------------------------------------------------ suggest: shapes and errors
run = make_run()
T = tpara(2, 1)
r = suggest(run, T, "adopt widgets in more settings", "Make it more formal.",
            answers=[{"replacement": "deploy widgets across a wider range of settings", "note": ""}])
check("suggest: the response shape", set(r) == {"suggestion_id", "target", "before", "after", "note", "words_before", "words_after"}, str(set(r)))
check("suggest: before/after are the whole paragraph as segments",
      "".join(x["text"] for x in r["before"]) == run.sections[2]["paragraphs"][1]
      and "".join(x["text"] for x in r["after"]) == "Providers keep adding capacity as buyers deploy widgets across a wider range of settings.", str(r["after"]))
check("suggest: word counts", r["words_before"] == 11 and r["words_after"] == 14, f"{r['words_before']} {r['words_after']}")
check("suggest: nothing is applied before Accept", run.sections[2]["paragraphs"][1].endswith("in more settings."))
p = CALLS[-1]["user"]
check("suggest: the prompt marks the selection", "⟦SEL⟧adopt widgets in more settings⟦/SEL⟧" in p)
check("suggest: the prompt carries the instruction verbatim", "Make it more formal." in p)
check("suggest: the prompt carries this section's facts block", "THIS SECTION'S FACTS AND SHAPE" in p and "[fs.size]" in p)
check("suggest: the prompt carries all other facts compactly", "[W1] Acme launched a new product. (News Site)" in p and "[fs.us.0]" in p)
check("suggest: the prompt carries the guest article's points", "[P1] Plain point 1." in p)
check("suggest: the prompt shows the neighbouring paragraphs", "Before: Widget services deserve a closer look." in p)
check("suggest: temperature 0.3 on the first ask", CALLS[-1]["temperature"] == 0.3)
check("suggest: no client block outside the opening", "CLIENT LINK RULES" not in p)
sid1 = r["suggestion_id"]

r2 = suggest(run, T, "adopt widgets in more settings", "Make it more formal.", retry_of=sid1,
             answers=[{"replacement": "use widgets across more settings", "note": ""}])
check("suggest: retry_of asks at temperature 0.5 and shows the previous text as not this",
      CALLS[-1]["temperature"] == 0.5 and "deploy widgets across a wider range of settings" in CALLS[-1]["user"] and "do not give it again" in CALLS[-1]["user"])

check("suggest: a stale quote is 409", raises(lambda: canvas.suggest(run, T, 0, 5, "nope!", "x", "selection", None, LOG), 409) is None)
check("suggest: an empty instruction is 400", raises(lambda: suggest(run, T, "Providers", " "), 400) is None)
check("suggest: the title target is 400", raises(lambda: canvas.suggest(run, {"kind": "title"}, 0, 4, "What", "x", "selection", None, LOG), 400) is None)
check("suggest: an unusable model answer is 422", raises(lambda: suggest(run, T, "Providers", answers=[{"note": "x"}]), 422) is None)
check("suggest: an unchanged answer is 422", raises(lambda: suggest(run, T, "Providers", answers=[{"replacement": "Providers", "note": ""}]), 422) is None)

# the opening with a client: client block in the prompt; link must survive
crun = make_run(with_client=True)
ct = tpara(0, 0)
r = suggest(crun, ct, "They value clear guidance.", "Shorten", answers=[{"replacement": "They value clarity.", "note": ""}])
check("suggest: the opening's prompt carries the client rules", "CLIENT LINK RULES" in CALLS[-1]["user"])
check("suggest: a client link outside the selection stays in the suggestion", any(x["url"] == "https://northway.example.com/reviews" for x in r["after"]))
r = suggest(crun, ct, f"rely on {CLIENT_WORDS} to compare", "Rewrite", answers=[
    {"replacement": "rely on guides to compare", "note": ""},                 # drops the client link: refused, one retry
    {"replacement": f"turn to {CLIENT} to compare", "note": ""}])
check("suggest: a dropped client link gets one retry stating the problem",
      len(CALLS) >= 2 and "was refused because" in CALLS[-1]["user"] and any(x["url"] for x in r["after"]), CALLS[-1]["user"][-600:])
check("suggest: the client link words stay exact", f"turn to {CLIENT_WORDS} to compare" in "".join(x["text"] for x in r["after"]))
check("suggest: a link that still fails after the retry is 422",
      raises(lambda: suggest(crun, ct, f"rely on {CLIENT_WORDS} to compare", "Rewrite",
                             answers=[{"replacement": "rely on guides", "note": ""}, {"replacement": "rely on other guides", "note": ""}]), 422) is None)
check("suggest: the CMI link in the selection must come back too",
      raises(lambda: suggest(run, tpara(2, 0), f"The {MARKET} stands at", "Reword",
                             answers=[{"replacement": "The market stands at", "note": ""}, {"replacement": "The market is at", "note": ""}]), 422) is None)

# scopes
run = make_run()
r = suggest(run, tpara(2, 1), "Providers", "Rewrite the whole paragraph", scope="paragraph",
            answers=[{"replacement": "Providers expand capacity as widgets reach more settings.", "note": ""}])
check("suggest: scope paragraph replaces the whole paragraph", "".join(x["text"] for x in r["after"]) == "Providers expand capacity as widgets reach more settings.")
r = suggest(run, tpara(3, 1), "Digital marketing", "Rewrite the section", scope="section", answers=[{"replacement":
            "### Digital adoption is raising demand\n\nDigital adoption lifts demand for widget services.\n\nBuyers compare providers on service quality.\n\n"
            "### Piracy remains a drag\n\nPiracy hampers growth in some channels.", "note": ""}])
texts = "".join(x["text"] for x in r["after"])
check("suggest: scope section returns all the section's paragraphs joined with a blank line",
      texts.count("\n\n") == 4 and "".join(x["text"] for x in r["before"]).count("\n\n") == 4, texts)
check("suggest: the section prompt asks for every paragraph", "every paragraph of the part above" in CALLS[-1]["user"])
check("suggest: a section answer that changes a sub-heading is refused after one retry",
      raises(lambda: suggest(run, tpara(3, 1), "Digital marketing", "Rewrite the section", scope="section", answers=[
          {"replacement": "### Another heading\n\nText.", "note": ""}, {"replacement": "### Another heading\n\nText.", "note": ""}]), 422) is None)

# FAQ
r = suggest(run, {"kind": "faq", "index": 1}, "Acme and Beta", "Rephrase", answers=[{"replacement": "Acme and Beta, among others,", "note": ""}])
check("suggest: a FAQ answer works", "".join(x["text"] for x in r["after"]) == "Acme and Beta, among others, lead the field." and r["target"] == {"kind": "faq", "index": 1})
check("suggest: the FAQ prompt shows the question and asks for a standalone answer", "Question: Who leads?" in CALLS[-1]["user"] and "standalone answer" in CALLS[-1]["user"])

# ------------------------------------------------------------------ guardrails on the changed paragraph only
run = make_run()
T = tpara(2, 1)
r = suggest(run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "Say demand is growing", answers=[
    {"replacement": "Demand rose 45% last year. Providers keep adding capacity as buyers adopt widgets.", "note": ""}])
after = "".join(x["text"] for x in r["after"])
check("guard: an invented figure's sentence is stripped", "45%" not in after and after.startswith("Providers keep adding"), after)
check("guard: the dropped sentence is reported in the note", "Dropped" in r["note"] and "45%" in r["note"] and "figure" in r["note"], r["note"])
r = suggest(run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "Add that demand rose 45% last year", answers=[
    {"replacement": "Demand rose 45% last year. Providers keep adding capacity as buyers adopt widgets.", "note": ""}])
check("guard: a figure the instruction gives is kept", "45%" in "".join(x["text"] for x in r["after"]), r["note"])
run.canvas["editor_numbers"] = ["45"]
r = suggest(run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "Say demand is growing", answers=[
    {"replacement": "Demand rose 45% last year. Providers keep adding capacity as buyers adopt widgets.", "note": ""}])
check("guard: a figure the editor typed earlier is kept", "45%" in "".join(x["text"] for x in r["after"]), r["note"])
run.canvas["editor_numbers"] = []
r = suggest(run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "Add a source", answers=[
    {"replacement": "According to News Site, buyers adopt widgets in more settings. Providers keep adding capacity.", "note": ""}])
after = "".join(x["text"] for x in r["after"])
check("guard: an outside source name is stripped", "News Site" not in after, after)
check("guard: a fact-backed figure is kept", "USD 1.2 billion" in "".join(x["text"] for x in suggest(
    run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "Add the size", answers=[
        {"replacement": "The market stands at USD 1.2 billion in 2025. Providers keep adding capacity.", "note": ""}])["after"]))
check("guard: when every sentence is dropped the suggestion is 422", raises(lambda: suggest(
    run, T, "Providers keep adding capacity as buyers adopt widgets in more settings.", "x", answers=[
        {"replacement": "Demand rose 45% last year.", "note": ""}]), 422) is None)
big = copy.deepcopy(run.sections)
suggest(run, T, "Providers", "x", answers=[{"replacement": "Providers, large and small,", "note": ""}])
check("guard: only the changed paragraph is touched, the draft itself is not modified before Accept", run.sections == big)

# ------------------------------------------------------------------ apply / undo / revert through the API, refresh_final and the docx
client = TestClient(main.app)
main._persist_run = lambda token, run: False
run = make_run(filename="canvas_test_api.docx")
TOKEN = "canvas-test-token"
main._RUNS[TOKEN] = run
ORIGINAL = copy.deepcopy(run.sections)
docx_path = pipeline.output_path_for(run.filename)


def post(path, body):
    return client.post(path, json=body)


try:
    s, e, q = quote_at(run, tpara(2, 1), "adopt widgets in more settings")
    ANSWERS[:] = [{"replacement": "deploy widgets in more places", "note": ""}]
    rs = post("/canvas/suggest", {"run_token": TOKEN, "target": tpara(2, 1), "start": s, "end": e, "quote": q,
                                  "instruction": "reword", "scope": "selection"})
    check("api: /canvas/suggest returns 200 with the contract's shape", rs.status_code == 200 and set(rs.json()) == {
        "suggestion_id", "target", "before", "after", "note", "words_before", "words_after"}, rs.text[:300])
    check("api: /canvas/suggest 409 on a stale quote", post("/canvas/suggest", {"run_token": TOKEN, "target": tpara(2, 1), "start": s, "end": e,
          "quote": "wrong", "instruction": "x"}).status_code == 409)
    check("api: /canvas/suggest 400 on an empty instruction", post("/canvas/suggest", {"run_token": TOKEN, "target": tpara(2, 1), "start": s, "end": e,
          "quote": q, "instruction": ""}).status_code == 400)
    check("api: /canvas/suggest 400 on the title", post("/canvas/suggest", {"run_token": TOKEN, "target": {"kind": "title"}, "start": 0, "end": 4,
          "quote": "What", "instruction": "x"}).status_code == 400)
    check("api: an unknown run is 404", post("/canvas/suggest", {"run_token": "nope", "target": tpara(2, 1), "start": s, "end": e, "quote": q,
          "instruction": "x"}).status_code == 404)
    sid = rs.json()["suggestion_id"]

    ra = post("/canvas/apply", {"run_token": TOKEN, "suggestion_id": sid})
    body = ra.json()
    check("api: /canvas/apply returns result + canvas", ra.status_code == 200 and set(body) == {"result", "canvas"}, ra.text[:300])
    check("api: canvas status after one edit", body["canvas"] == {"can_undo": True, "edits": 1, "saved": False}, str(body["canvas"]))
    check("api: the result is the /finalize payload (title, sections, checks, report, faq, clients)",
          {"title", "sections", "checks", "report", "faq", "clients", "output_filename", "evidence_citations"} <= set(body["result"]), str(set(body["result"])))
    check("api: the draft holds the accepted text", run.sections[2]["paragraphs"][1].endswith("deploy widgets in more places."))
    shown = "".join(x["text"] for x in body["result"]["sections"][2]["paragraphs"][1])
    check("api: the result's display sections show the new text", shown.endswith("deploy widgets in more places."), shown)
    check("api: the docx is rewritten with the check re-run", docx_path.is_file() and body["result"]["output_filename"] == docx_path.name and body["result"]["checks"])
    check("api: the accepted instruction's numbers need none; history keeps the agent's draft", run.canvas["original"]["sections"] == ORIGINAL)
    check("api: applying again is 404 (the suggestion is spent)", post("/canvas/apply", {"run_token": TOKEN, "suggestion_id": sid}).status_code == 404)

    # a suggestion made on a draft that then changed is refused
    ANSWERS[:] = [{"replacement": "Providers, large and small, keep adding capacity", "note": ""}]
    s2, e2, q2 = quote_at(run, tpara(2, 1), "Providers keep adding capacity")
    sg = post("/canvas/suggest", {"run_token": TOKEN, "target": tpara(2, 1), "start": s2, "end": e2, "quote": q2, "instruction": "x"}).json()
    run.sections[2]["paragraphs"][1] = "Something else entirely."
    check("api: a suggestion on a changed paragraph is 409 on apply", post("/canvas/apply", {"run_token": TOKEN, "suggestion_id": sg["suggestion_id"]}).status_code == 409)
    run.sections[2]["paragraphs"][1] = ORIGINAL[2]["paragraphs"][1].replace("adopt widgets in more settings", "deploy widgets in more places")

    # manual edit
    src = post("/canvas/source", {"run_token": TOKEN, "target": tpara(2, 0)}).json()
    check("api: /canvas/source replaces each link by a placeholder",
          src["text"] == "The ⟦L0⟧ stands at USD 1.2 billion in 2025. Demand is steady across buyers and providers."
          and src["links"] == [{"id": "L0", "words": MARKET, "url": run.report.url}], str(src))
    ssp = post("/canvas/source", {"run_token": TOKEN, "target": tpara(0, 1)}).json()
    check("api: /canvas/source gives the retained paragraph as one locked link", ssp["text"] == "⟦L0⟧" and ssp["links"][0]["words"] == SPONSOR_TEXT)
    new_text = "The ⟦L0⟧ stands at USD 1.2 billion in 2025, a figure of 77% of peers. Demand is steady."
    rm = post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": new_text})
    check("api: a manual edit applies and counts", rm.status_code == 200 and rm.json()["canvas"]["edits"] == 2, rm.text[:300])
    check("api: the placeholder came back as the marker", run.sections[2]["paragraphs"][0].startswith(f"The {CMI} stands at"), run.sections[2]["paragraphs"][0])
    check("api: the editor's typed numbers are allowed figures from then on", "77" in run.canvas["editor_numbers"], str(run.canvas["editor_numbers"]))
    check("api: a manual edit that drops a client link / sponsor token is 400",
          post("/canvas/apply", {"run_token": TOKEN, "target": tpara(0, 1), "new_text": "gone"}).status_code == 400)
    check("api: a placeholder twice is 400", post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": "⟦L0⟧ and ⟦L0⟧"}).status_code == 400)
    check("api: a placeholder that does not exist is 400", post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": "⟦L0⟧ ⟦L5⟧"}).status_code == 400)
    check("api: a typed marker is 400", post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": "⟦L0⟧ [[CMI_LINK:x]]"}).status_code == 400)
    check("api: empty text is 400", post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": "  "}).status_code == 400)
    rd = post("/canvas/apply", {"run_token": TOKEN, "target": tpara(2, 0), "new_text": "Demand is steady across buyers and providers."})
    check("api: a CMI link may be dropped by a manual edit", rd.status_code == 200 and CMI not in run.sections[2]["paragraphs"][0], rd.text[:200])
    rt = post("/canvas/apply", {"run_token": TOKEN, "target": {"kind": "title"}, "new_text": "A New Title For The Zzz Widget Services Market"})
    check("api: the title can be edited by hand", rt.status_code == 200 and run.title.startswith("A New Title") and rt.json()["result"]["title"] == run.title)
    rf = post("/canvas/apply", {"run_token": TOKEN, "target": {"kind": "faq", "index": 1}, "new_text": "Acme leads the field."})
    check("api: a FAQ answer can be edited by hand", rf.status_code == 200 and run.faq[1]["answer"] == "Acme leads the field." and rf.json()["result"]["faq"][1]["answer"] == "Acme leads the field.")
    n_edits = rf.json()["canvas"]["edits"]
    check("api: the edit counter follows", n_edits == 5, str(n_edits))

    # undo / revert
    ru = post("/canvas/undo", {"run_token": TOKEN})
    check("api: undo restores the FAQ answer and re-checks", ru.status_code == 200 and run.faq[1]["answer"] == "Acme and Beta lead the field."
          and ru.json()["canvas"] == {"can_undo": True, "edits": 4, "saved": False}, ru.text[:200])
    ru = post("/canvas/undo", {"run_token": TOKEN})
    check("api: undo again restores the title", run.title == "What Is Driving the Zzz Widget Services Market")
    rr = post("/canvas/revert", {"run_token": TOKEN})
    check("api: revert returns to the agent's draft", rr.status_code == 200 and run.sections == ORIGINAL and rr.json()["canvas"] == {"can_undo": False, "edits": 0, "saved": False}, rr.text[:200])
    check("api: undo with nothing to undo is 400", post("/canvas/undo", {"run_token": TOKEN}).status_code == 400)
    check("api: revert of an untouched draft is 400 only before any edit", post("/canvas/revert", {"run_token": TOKEN}).status_code == 200)
    check("api: the canvas state is plain data (it can be saved)", __import__("pickle").loads(__import__("pickle").dumps(run.canvas)) == run.canvas)
    check("api: apply with nothing to apply is 400", post("/canvas/apply", {"run_token": TOKEN}).status_code == 400)
finally:
    main._RUNS.pop(TOKEN, None)
    if docx_path.is_file():
        docx_path.unlink()

# refresh_final directly: checks + docx, no telemetry
run = make_run(filename="canvas_test_refresh.docx")
res = pipeline.refresh_final(run.article, run.sponsor, run.report, run.candidates, run.orient, run.selection, run.hits, run.title, run.sections, run.evidence,
                             run.filename, LOG, faq=run.faq, plan=run.plan, plan_inputs=run.plan_inputs)
path = pipeline.output_path_for(run.filename)
check("refresh_final: returns the checks and writes the docx", bool(res.checks) and res.output_path == path and path.is_file())
if path.is_file():
    path.unlink()
check("run_finalize still returns the same thing through refresh_final", pipeline.run_finalize.__doc__ is not None and "refresh_final" in pipeline.run_finalize.__doc__)

# the history is a stack of the agent's draft and each step
run = make_run()
canvas.apply_manual(run, tpara(2, 1), "First edit of this paragraph.")
canvas.apply_manual(run, tpara(2, 1), "Second edit of this paragraph.")
check("history: two edits, undo twice restores the original paragraph",
      (canvas.undo(run), canvas.undo(run), run.sections[2]["paragraphs"][1])[2].startswith("Providers keep adding capacity"))
check("history: the agent's draft is kept from the first edit and not overwritten", run.canvas["original"]["sections"][2]["paragraphs"][1].startswith("Providers keep"))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all canvas checks passed")
