"""Offline test for FAQ generation's sanitisation and docx-section rendering (no network, no LLM calls).

Run from backend/: python devtools/test_faq.py

Covers: pipeline._normalize_faq (the LLM output sanitiser) and pipeline.faq_sections (the
heading/paragraph shape it's rendered into for the .docx and the frontend) — not the FAQ LLM call
itself (pipeline.run_faq), which needs a real model and is best-effort by design.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------ _normalize_faq
raw = [
    {"question": "What is the CAGR of Zzz Widget Market?", "answer": "Approximately 5.2% between 2026 and 2033."},
    {"question": "what is the cagr of zzz widget market?", "answer": "A duplicate, differently-cased question."},
    {"question": "", "answer": "Dropped: no question."},
    {"question": "Dropped: no answer.", "answer": ""},
    "not a dict",
    {"question": "Which region dominates Zzz Widget Market?", "answer": "Asia Pacific, at about 36.5% share."},
]
out = pipeline._normalize_faq(raw)
check("normalize: non-dict entries dropped", len(out) == 2, str(out))
check("normalize: case/punctuation-only duplicate question collapsed to the first occurrence",
      out[0]["answer"] == "Approximately 5.2% between 2026 and 2033.")
check("normalize: entries missing a question or answer are dropped",
      all(qa["question"] and qa["answer"] for qa in out))

check("normalize: None input gives []", pipeline._normalize_faq(None) == [])

many = [{"question": f"Question {i}?", "answer": f"Answer {i}."} for i in range(pipeline._FAQ_COUNT + 5)]
check("normalize: capped at _FAQ_COUNT", len(pipeline._normalize_faq(many)) == pipeline._FAQ_COUNT)

# ------------------------------------------------------------------ faq_sections
sections = pipeline.faq_sections([
    {"question": "What is Zzz Widget Market?", "answer": "A market for zzz widgets."},
    {"question": "Which players lead it?", "answer": "Acme Corp and Globex Inc."},
])
check("faq_sections: a heading-only intro section comes first", sections[0]["heading"] == "Frequently Asked Questions" and sections[0]["paragraphs"] == [])
check("faq_sections: one section per Q&A, question as heading, answer as its only paragraph",
      sections[1]["heading"] == "What is Zzz Widget Market?" and sections[1]["paragraphs"] == ["A market for zzz widgets."])
check("faq_sections: every FAQ section is tagged sop=['faq']", all(s["sop"] == ["faq"] for s in sections))
check("faq_sections: empty input gives []", pipeline.faq_sections([]) == [])

# ------------------------------------------------------------------ prompt formatting sanity
formatted = prompts.FAQ_USER.format(
    market_name="Zzz Widget Market", report_text="The Zzz Widget Market was valued at USD 1 Bn in 2025.",
    article_text="TITLE: Zzz Widgets Explained\n\nSome article body text.", faq_count=7,
    used_questions="- How big is the Zzz Widget Market?", publisher_text="Some publisher text.", publisher_faq_count=2, page_faq="- How big is the Zzz Widget Market? -> USD 1 Bn.",
)
check("prompt: FAQ_USER formats without a KeyError", isinstance(formatted, str) and "Zzz Widget Market" in formatted)

# ------------------------------------------------------------------ definition questions are dropped
check("definition: 'What is the Zzz Widget Market?' is a definition", pipeline._is_definition_question("What is the Zzz Widget Market?"))
check("definition: 'What is a zzz widget?' is a definition", pipeline._is_definition_question("What is a zzz widget?"))
check("definition: a growth question starting 'What is' is not", not pipeline._is_definition_question("What is the growth outlook for the Zzz Widget Market?"))
check("definition: the CAGR question is not", not pipeline._is_definition_question("What is the compound annual growth rate (CAGR) of the Zzz Widget Market during 2026-2033?"))
check("definition: 'Which region is dominating ...' is not", not pipeline._is_definition_question("Which region is dominating the Zzz Widget Market?"))
_kept = pipeline._normalize_faq([
    {"question": "What is the Zzz Widget Market?", "answer": "A market for widgets."},
    {"question": "Which region is dominating the Zzz Widget Market?", "answer": "North America."},
])
check("normalize: the definition entry is dropped, the real one kept", [q["question"] for q in _kept] == ["Which region is dominating the Zzz Widget Market?"], str(_kept))

# ------------------------------------------------------------------ the report page's own FAQ is not repeated
_page = [
    {"question": "What are the key factors hampering growth of the global zzz widget market?", "answer": "High cost and lack of skilled staff hamper growth."},
    {"question": "Which are the major players operating in the global zzz widget market?", "answer": "Alpha Corp, Beta Ltd, Gamma Inc, Delta SA."},
]
_faq = [
    {"question": "What are the key factors hampering growth of the Zzz Widget Market?", "answer": "Cost and staffing."},       # same question
    {"question": "Who leads the Zzz Widget Market?", "answer": "Alpha Corp, Beta Ltd, Gamma Inc and Delta SA lead it."},    # same answer (company list)
    {"question": "Which deployment mode grows fastest in the Zzz Widget Market?", "answer": "Cloud deployment grows fastest, at 14% a year."},
]
import logging as _lg
_kept = pipeline._drop_page_faq_repeats(_faq, _page, "Zzz Widget Market", _lg.getLogger("t"))
check("page FAQ: a near-identical question is dropped", all("hampering" not in q["question"] for q in _kept), str(_kept))
check("page FAQ: an entry whose answer repeats the page's answer is dropped", all("Who leads" not in q["question"] for q in _kept), str(_kept))
check("page FAQ: a genuinely different question is kept", [q["question"] for q in _kept] == ["Which deployment mode grows fastest in the Zzz Widget Market?"], str(_kept))
check("page FAQ: with no page FAQ nothing is dropped", pipeline._drop_page_faq_repeats(_faq, [], "Zzz Widget Market", _lg.getLogger("t")) == _faq)
import reports as _rp
_html = '<script>{"@type":"Question","name":"How big is the Zzz Market?","acceptedAnswer":{"@type":"Answer","text":"USD 1 Bn &amp; rising."}}</script>'
check("reports.extract_page_faq reads the FAQPage markup", _rp.extract_page_faq(_html) == [{"question": "How big is the Zzz Market?", "answer": "USD 1 Bn & rising."}], str(_rp.extract_page_faq(_html)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all faq tests passed")
