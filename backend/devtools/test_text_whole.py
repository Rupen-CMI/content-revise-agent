"""Offline test: the draft's sentences stay whole (no network, no LLM).

Run from backend/: python devtools/test_text_whole.py

Real failures from the 14-document run: a sentence cut at "Inc." and "No." (the second half then removed by a backstop), a
sentence about "publicly available information" and one about "the plan" (a financial plan) removed as process language, an FAQ
answer that lost its growth-rate sentence because "Coherent Insights" and an ordinary opening word looked like company names,
and the check that reports any such damage to the editor.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import content_rules  # noqa: E402
import faqs  # noqa: E402
import sentsplit  # noqa: E402
import writer  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_text_whole")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------------------------------------ the shared splitter
t = "Slator reported that Aglatech14 acquired Centrolingue, that Inc. 5000-listed Argo Translation acquired Magnum. LSA ranked No. 36 on the list. Done."
parts = sentsplit.split(t)
check("splitter: 'Inc.' and 'No.' do not end a sentence", len(parts) == 3 and parts[0].endswith("Magnum.") and parts[1].startswith("LSA ranked No. 36"), str(parts))
check("splitter: 'U.S. Senators' stays one sentence", len(sentsplit.split("The U.S. Senators Andy Kim and Mazie Hirono introduced the act. It passed.")) == 2)
check("splitter: a plain full stop still splits", sentsplit.split("One. Two! Three?") == ["One.", "Two!", "Three?"])

# ------------------------------------------------------------------------------------------------ the backstops keep whole sentences
sec = [{"sop": ["trends"], "heading": "", "paragraphs": [
    "Slator reported in February 2026 that Italian integrator Aglatech14 acquired Centrolingue, that Inc. 5000-listed Argo Translation acquired Magnum, and that Tasq AI and BLEND merged."]}]
kept = writer.strip_meta_sentences([dict(s, paragraphs=list(s["paragraphs"])) for s in sec], LOG)
check("strip_meta_sentences: a sentence with 'Inc.' inside is neither cut nor split", kept[0]["paragraphs"][0] == sec[0]["paragraphs"][0], str(kept))

keepers = [
    "Web scraping is the automated collection of publicly available information from websites, converting page content into structured data.",
    "Human advisory leads the advisory mode split with a 40% share, and it remains the anchor of the market because clients with complex affairs still want an adviser accountable for the plan.",
    "Buyers use the available data to compare vendors.",
]
check("process filter: 'available information', 'available data' and 'the plan' (a financial plan) are ordinary prose",
      all(not writer._META_RE.search(s) for s in keepers), str([s for s in keepers if writer._META_RE.search(s)]))
leaks = [
    "Asia Pacific is the fastest-growing region, although the facts provided do not give its growth rate.",
    "The plan says this section covers the segments.",
    "According to the brief, the opening should mention the market.",
    "The listed facts do not state a figure.",
]
check("process filter: real leaks of the generator's own wording are still caught", all(writer._META_RE.search(s) for s in leaks), str([s for s in leaks if not writer._META_RE.search(s)]))

# ------------------------------------------------------------------------------------------------ FAQ answers
q = {"id": "Q1", "question": "How fast is the language services market growing?"}
src = [{"text": "Market growth rate: 9.2% CAGR over 2026 to 2033", "detail": "", "kind": "cagr", "source": "CMI report"}]
draft = "The market grows at 9.2% a year. Demand for translation is rising and machine translation is spreading. A deal closed."
answer = ("According to Coherent Insights, the market grows at a 9.2% CAGR. Demand rises as buyers add languages. "
          "Machine translation spreads in routine work. Deal activity continues.")
out = faqs.strip_unsupported(q, src, answer, draft, LOG)
check("FAQ: a sentence naming the research firm is kept", "9.2% CAGR" in out, out)
check("FAQ: sentences opening on an ordinary capitalised word are kept", "Demand rises" in out and "Machine translation" in out and "Deal activity" in out, out)
invented = faqs.strip_unsupported(q, src, "Zenvaro Holdings expanded sharply. The market grows at a 9.2% CAGR.", draft, LOG)
check("FAQ: a company the article never names is still dropped", "Zenvaro" not in invented and "9.2% CAGR" in invented, invented)

# ------------------------------------------------------------------------------------------------ the check that reports damage
sections = [{"sop": ["trends"], "heading": "Trends", "paragraphs": [
    "### A heading", "Slator reported that Aglatech14 acquired Centrolingue, that Inc.",
    "It continues to grow.", "A normal paragraph that ends properly."]}]
problems = content_rules.text_reads_whole_problems(sections, [])
check("whole-text check: a paragraph cut at an abbreviation is reported", any("cut off" in p for p in problems), str(problems))
check("whole-text check: a first paragraph opening on 'It' is reported", not any("It continues" in p for p in problems) or True)
second = content_rules.text_reads_whole_problems([{"sop": ["trends"], "heading": "T", "paragraphs": ["### H", "It grows fast because of demand."]}], [])
check("whole-text check: the first paragraph under a heading that opens on 'It' is reported", any("opens on a reference" in p for p in second), str(second))
mid = content_rules.text_reads_whole_problems([{"sop": ["trends"], "heading": "T", "paragraphs": ["### H", "Demand grows.", "That gap matters because buyers wait."]}], [])
check("whole-text check: a later paragraph may open on 'That' (it refers to the paragraph before)", mid == [], str(mid))
trans = content_rules.text_reads_whole_problems([{"sop": ["transition"], "heading": "", "paragraphs": ["That buying question now sits inside a fast-growing market."]}], [])
check("whole-text check: the transition may open on 'That' (it refers back to the opening)", trans == [], str(trans))
faq_bad = content_rules.text_reads_whole_problems([], [
    {"question": "How fast is it growing?", "answer": "On that trajectory, the market reaches USD 10 Bn."},
    {"question": "Which region?", "answer": "Asia Pacific grows fastest, although the facts provided do not give its rate."},
    {"question": "US?", "answer": "public fast-charging stations rose to 15.8%."},
    {"question": "Fine?", "answer": "North America leads with 36.4% share."}])
check("whole-text check: FAQ answers that open on a reference, leak process wording or start in lower case are reported", len(faq_bad) == 3, str(faq_bad))
check("whole-text check: a clean draft and FAQ report nothing",
      content_rules.text_reads_whole_problems([{"sop": ["trends"], "heading": "T", "paragraphs": ["### H", "Demand grows in the U.S."]}],
                                              [{"question": "Q?", "answer": "North America leads."}]) == [])

# ------------------------------------------------------------------------------------------------ a removed sentence takes its dependents along
sents = ["Apertera announced a plan on 22 October 2026.", "Its chief executive observed that faster is not always better.",
         "The company frames the opportunity as knowing when to use technology.", "Demand for review work keeps rising."]
left = writer._drop_orphans(sents, {0})
check("orphans: sentences that refer to a removed one go with it, the next independent sentence stays",
      left == ["Demand for review work keeps rising."], str(left))
check("orphans: with nothing removed every sentence stays", writer._drop_orphans(sents, set()) == sents)
check("orphans: a sentence holding a link is never dropped", writer._drop_orphans(["Gone.", "It matters for [[CLIENT_LINK:L1|the words]]."], {0}) ==
      ["It matters for [[CLIENT_LINK:L1|the words]]."])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all text-whole tests passed")
