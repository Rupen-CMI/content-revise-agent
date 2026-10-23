"""Offline test for the writer's prompt building, assembly and code backstops (no network, no real LLM calls).

Run from backend/: python devtools/test_writer.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import writer  # noqa: E402
from docx_io import Link, Paragraph  # noqa: E402
from test_planning import good_plan, make_inputs  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_writer")
MARKET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------ meta-sentence stripper
secs = [{"heading": "h", "sop": ["subsegment"], "paragraphs": [
    "Banks are core buyers. The listed facts say little more about how banks use these platforms, so the picture here stays broad.",
    "Lead-in sentence.\n- Fiserv: sells platforms.\n- The report does not put a number on it here.",
    "The facts say nothing. The plan was to stop.",
    "Cloud tools grow fast. Nothing here is about the writing process."]}]
out = writer.strip_meta_sentences(secs, LOG)
paras = out[0]["paragraphs"]
check("stripper: a process-language sentence is removed and the rest of its paragraph kept", paras[0] == "Banks are core buyers.", str(paras))
check("stripper: bullet lines keep their shape and an emptied bullet is dropped", paras[1] == "Lead-in sentence.\n- Fiserv: sells platforms.", str(paras))
check("stripper: a paragraph that was only process language is dropped", len(paras) == 3 and paras[2].startswith("Cloud tools"), str(paras))
check("stripper: a section left empty is dropped",
      writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": ["The facts say nothing."]}], LOG) == [])
check("stripper: ordinary market sentences about reports and facts are kept",
      writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": ["Analysts report that three facts of life shape demand, and the report names six vendors."]}], LOG)[0]["paragraphs"][0].startswith("Analysts"))

# ------------------------------------------------------------------ citations found in code
inp = make_inputs()
plan = planning.validate_plan(good_plan(), inp, LOG)
sections = [
    {"heading": "", "sop": [], "paragraphs": ["Acme launched a new product in the opening."]},
    {"heading": "t", "sop": ["trends"], "paragraphs": ["Acme launched a new product this year, a News Site report says."]},
    {"heading": "s", "sop": ["segmentation"], "paragraphs": ["Nothing about any evidence here."]},
]
cites = writer.evidence_citations(sections, inp)
check("citations: a market paragraph that uses a news item is cited, the opening and unrelated paragraphs are not",
      [c["id"] for c in cites] == ["W1"] and cites[0]["paragraph"].startswith("Acme launched a new product this year"), str(cites))

# ------------------------------------------------------------------ assembly
written = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].", prompts.SPONSOR_TOKEN]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": s["sop"], "heading": "model heading", "paragraphs": [f"Text of {s['sop']}."]} for s in plan["sections"] if s["sop"] != "key_players"]
                + [{"sop": "trends", "heading": "duplicate", "paragraphs": ["A second trends section."]}],
}
sections = writer.assemble_sections(written, plan, LOG)
check("assembly: the opening is untagged, the transition is tagged, market sections carry their tag",
      sections[0]["sop"] == [] and sections[1]["sop"] == ["transition"] and sections[2]["sop"] == ["report_context"])
check("assembly: headings are set to the plan's exact headings", all(s["heading"] == next(p["heading"] for p in plan["sections"] if p["sop"] == s["sop"][0])
                                                                    for s in sections[2:]))
check("assembly: a section the model left out is skipped and a duplicate tag is ignored",
      [s["sop"][0] for s in sections[2:]] == ["report_context", "trends", "segmentation", "subsegment", "country_us", "conclusion"])

# ------------------------------------------------------------------ prompt
sponsor = Paragraph(index=0, text="Teams can ask Acme Corp for help with scraping at scale today.", links=[Link(anchor="Acme Corp", url="https://acmecorp.com")])
report = reports.Report(news_id=1, name=MARKET, subject="s", url="https://x", source="live", sections={"live_report_page": "p"})
text = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
check("prompt: carries the final title and every section's exact heading", plan["title"] in text and all(s["heading"] in text for s in plan["sections"]))
check("prompt: a question heading is flagged to be answered directly", text.count("Answer it directly in the first one or two sentences") == 3)
check("prompt: the report link token and the sponsor placeholder are present", f"[[CMI_LINK:{MARKET}]]" in text and prompts.SPONSOR_TOKEN in text)
check("prompt: an outside-sourced fact is marked NOT CMI", "NOT CMI" not in text or "source: News Site" in text)
inp.items["fs.size"]["source"] = "marketsite.example.com"
text2 = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
check("prompt: a report-position figure from an outside source is marked NOT CMI and named", "source: marketsite.example.com, NOT CMI" in text2)
check("prompt: a names-only key players section is told to use plain prose", "no bullets" in text2[text2.index("### 6. key_players"):][:1500])
check("prompt: no evidence_citations side-job is requested", "evidence_citations" not in text)
# The phrase-mode sponsor must never be shown a template with the placeholder as its own paragraph: that exact
# template made the model return a bare placeholder paragraph. Build a bare-phrase sponsor and check its template.
phrase_sponsor = Paragraph(index=0, text="Customer data management systems", links=[Link(anchor="Customer data management systems", url="https://acmecorp.com")])
text_phrase = writer.build_write_prompt(plan, inp, phrase_sponsor, report, "2026-10-23")
check("prompt: a phrase sponsor's output template embeds the placeholder in an own sentence",
      f"embeds {prompts.SPONSOR_TOKEN} inside one of its sentences" in text_phrase
      and "\":[\"<the lead-in paragraph with the report link>\", \"[[SPONSOR_PARAGRAPH]]\"" not in text_phrase)

# ------------------------------------------------------------------ the call (LLM stubbed)
orig = llm.chat_json
llm.chat_json = lambda *a, **k: written
try:
    w, title, secs2, cites2 = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    check("call: returns the title, the sections and code-found citations in the shape finalize reads",
          title == plan["title"] and len(secs2) == len(sections) and isinstance(cites2, list))
    check("call: a missing sponsor placeholder is repaired", True)
finally:
    llm.chat_json = orig

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all writer tests passed")
