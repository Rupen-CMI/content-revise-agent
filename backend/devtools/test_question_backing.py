"""Offline test for subheading-question evidence backing and heading placement (no network, no LLM calls).

Run from backend/: python devtools/test_question_backing.py

Covers: SELECT's question_backing sanitising (pipeline._normalize_question_backing, mirroring
_normalize_claim_backing minus the stance field), the question pool flattening/formatting helpers,
the SUBHEADING QUESTIONS / QUESTION EVIDENCE groups in run_build_evidence, the evidence-block
round-trip parser (pipeline._parse_question_assignments), and the deterministic heading check
(checks.question_heading_problems) — not the actual question-evidence web search
(pipeline.run_question_evidence) or the LLM selection call (pipeline.run_select_questions), which
need the network/a model and are best-effort by design.
"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import search  # noqa: E402

LOG = logging.getLogger("test_question_backing")
LOG.addHandler(logging.NullHandler())
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def hit(title: str, url: str, snippet: str = "", date=None) -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=url, source="", date=date)


questions = [
    {"id": "Q1", "question": "What are the top challenges in Zzz Widget adoption?", "section": "trends"},
    {"id": "Q2", "question": "Which regions lead Zzz Widget Market growth?", "section": "country_us"},
]

# ------------------------------------------------------------------ _format_questions_for_select / _flatten_question_pool
check("format: empty question list gives a placeholder, not an empty string",
      "none" in pipeline._format_questions_for_select([]).lower())
block = pipeline._format_questions_for_select(questions)
check("format: both question ids appear in the questions block", "Q1" in block and "Q2" in block)

pool_hits = [
    hit("Zzz Widget adoption challenges explained", "https://a.example.org/1", "Integration cost is the top barrier"),
    hit("More on Zzz Widget adoption", "https://b.example.org/2", "A second explainer"),
    hit("Regional Zzz Widget growth report", "https://c.example.org/3", "Asia Pacific leads adoption with 36.5 percent share"),
]
for h_ in pool_hits:
    h_.source = h_.host
flat = pipeline._flatten_question_pool(questions, {"Q1": pool_hits[:2], "Q2": pool_hits[2:]})
check("flatten: PQ-numbers are sequential across all questions",
      [pid for pid, _, _ in flat] == ["PQ1", "PQ2", "PQ3"], str(flat))
check("flatten: each entry keeps its own question id",
      [qid for _, qid, _ in flat] == ["Q1", "Q1", "Q2"], str(flat))
check("flatten: no questions gives []", pipeline._flatten_question_pool([], {"Q1": pool_hits}) == [])

pool_block = pipeline._format_question_pool_for_select(flat, questions)
check("format: the pool block tags each entry with the question it answers", "(for: Q1)" in pool_block and "(for: Q2)" in pool_block)
check("format: questions with no pool at all gives the 'none found' placeholder",
      "none" in pipeline._format_question_pool_for_select([], questions).lower())

# ------------------------------------------------------------------ SELECT question_backing sanitising
selection = {"question_backing": [
    {"question_id": "Q1", "id": "PQ1", "fact": "Integration cost is the top barrier to Zzz Widget adoption."},
    {"question_id": "Q1", "id": "PQ2", "fact": "A second, real entry for the same question."},
    {"question_id": "Q1", "id": "PQ9", "fact": "Invented pool id."},
    {"question_id": "Q2", "id": "PQ3", "fact": "Asia Pacific leads adoption with 36.5% market share."},
    {"question_id": "Q2", "id": "PQ3", "fact": "A figure (55) that is not in the source text."},
    "not a dict",
]}
pipeline._normalize_question_backing(selection, questions, flat, LOG)
kept = selection["question_backing"]
kept_ids = [(b["id"], b["question_id"]) for b in kept]
check("backing: at most two entries per question", sum(1 for b in kept if b["question_id"] == "Q1") <= 2, str(kept_ids))
check("backing: an invented pool id is dropped", all(b["id"] != "PQ9" for b in kept))
check("backing: an entry carries no 'stance' field (questions aren't judged true/false)", all("stance" not in b for b in kept))
check("backing: the real Q1 entries are kept", {b["id"] for b in kept if b["question_id"] == "Q1"} == {"PQ1", "PQ2"}, str(kept_ids))
check("backing: an invented figure (55) not in the source is dropped, the real one (36.5) is kept",
      any("36.5" in b["fact"] for b in kept) and all("55" not in b["fact"] for b in kept), str(kept_ids))

# freshness parity: question backing obeys the same stale-date rule as claim/market evidence
old_hit = hit("Old Zzz Widget regional report", "https://old.example.org/r", "Asia Pacific led as of 1 July 2020")
old_hit.source = old_hit.host
sel_f = {"question_backing": [
    {"question_id": "Q2", "id": "PQ1", "fact": "Asia Pacific led the Zzz Widget Market as of 1 July 2020."},
]}
pipeline._normalize_question_backing(sel_f, questions, [("PQ1", "Q2", old_hit)], LOG)
check("freshness: a backing fact stating a stale date is dropped", sel_f["question_backing"] == [], str(sel_f["question_backing"]))

# ------------------------------------------------------------------ run_build_evidence: SUBHEADING QUESTIONS / QUESTION EVIDENCE
built = pipeline.run_build_evidence(
    {"evidence": [], "question_backing": [
        {"question_id": "Q1", "id": "PQ1", "fact": "Integration cost is the top barrier.", "source": "a.example.org", "url": "https://a.example.org/1", "detail": ""},
    ]},
    [], LOG, orient=None, questions=questions,
)
check("build: SUBHEADING QUESTIONS group present", "### SUBHEADING QUESTIONS" in built)
check("build: both question ids and texts appear", "Q1" in built and "Q2" in built and "top challenges" in built.lower())
check("build: QUESTION EVIDENCE group present with the backed question's fact", "### QUESTION EVIDENCE" in built and "Integration cost" in built)
check("build: the unbacked question (Q2) is flagged as having no outside evidence", "Q2" in built.split("### QUESTION EVIDENCE")[0] and "no outside evidence" in built)

no_questions_built = pipeline.run_build_evidence({"evidence": []}, [], LOG, orient=None, questions=[])
check("build: no questions means no SUBHEADING QUESTIONS group", "SUBHEADING QUESTIONS" not in no_questions_built)
check("build: each question line names the section it replaces", "replaces the trends heading" in built and "replaces the country_us heading" in built)

# ------------------------------------------------------------------ _parse_question_assignments (evidence-block round trip)
parsed = pipeline._parse_question_assignments(built)
check("parse: both questions round-trip with their id, section and text",
      {(p["id"], p["section"]) for p in parsed} == {("Q1", "trends"), ("Q2", "country_us")}, str(parsed))
check("parse: no SUBHEADING QUESTIONS block gives []", pipeline._parse_question_assignments(no_questions_built) == [])

# ------------------------------------------------------------------ checks.question_heading_problems
good_sections = [
    {"heading": "What Are the Top Challenges in Zzz Widget Adoption?", "sop": ["trends"], "paragraphs": ["..."]},
    {"heading": "Which Regions Lead Zzz Widget Market Growth?", "sop": ["country_us"], "paragraphs": ["..."]},
]
check("heading check: a section correctly headed with its assigned question passes",
      checks.question_heading_problems(good_sections, questions) == [])

no_question_mark = [
    {"heading": "Top Challenges in Zzz Widget Adoption", "sop": ["trends"], "paragraphs": ["..."]},
    {"heading": "Which Regions Lead Zzz Widget Market Growth?", "sop": ["country_us"], "paragraphs": ["..."]},
]
problems = checks.question_heading_problems(no_question_mark, questions)
check("heading check: a heading not ending in '?' is flagged", any("trends" in p and "does not end" in p for p in problems), str(problems))

ordinary_heading = [
    {"heading": "Market Trends and Outlook", "sop": ["trends"], "paragraphs": ["..."]},
    {"heading": "Which Regions Lead Zzz Widget Market Growth?", "sop": ["country_us"], "paragraphs": ["..."]},
]
problems = checks.question_heading_problems(ordinary_heading, questions)
check("heading check: an ordinary descriptive heading (not ending in '?') is flagged", any("trends" in p for p in problems), str(problems))

unrelated_question_mark = [
    {"heading": "Is Zzz Widget Right for Your Team?", "sop": ["trends"], "paragraphs": ["..."]},
    {"heading": "Which Regions Lead Zzz Widget Market Growth?", "sop": ["country_us"], "paragraphs": ["..."]},
]
problems = checks.question_heading_problems(unrelated_question_mark, questions)
check("heading check: a '?' heading sharing too little wording with the assigned question is still flagged",
      any("too little wording" in p for p in problems), str(problems))

check("heading check: no questions at all gives no problems", checks.question_heading_problems(good_sections, []) == [])

# ------------------------------------------------------------------ prompt formatting sanity
formatted = prompts.SELECT_USER.format_map({
    "title": "x", "topic_summary": "x", "bridge_angle": "x", "sponsor": "x", "report_name": "x",
    "hits": "x", "claims_block": "x", "claim_pool": "x", "questions_block": block, "question_pool": pool_block,
    "report_claims_block": "x", "report_claim_pool": "x", "max_selected": 10,
})
check("prompt: SELECT_USER formats with the new question fields and contains them", "Q1" in formatted and "Q2" in formatted)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all question-backing tests passed")
