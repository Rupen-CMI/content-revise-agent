"""Offline test for the FAQ stage (MSQs answered from the plan's facts; no network, no real LLM calls).

Run from backend/: python devtools/test_faq_msq.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import faqs  # noqa: E402
import llm  # noqa: E402
import planning  # noqa: E402
import test_planning  # noqa: E402,E402
from test_planning import check, make_inputs  # noqa: E402

LOG = logging.getLogger("test_faq_msq")
LOG.setLevel(logging.CRITICAL)

inp = make_inputs()
plan = test_planning.plan_of(test_planning.good_plan())

# ------------------------------------------------------------------ sources per question
size_srcs = faqs.sources_for_question(next(q for q in inp.questions if q["id"] == "Q1"), inp)
check("sources: the size question sees the size fact, not a driver or the evidence list",
      {s["id"] for s in size_srcs} == {"fs.size"}, str({s["id"] for s in size_srcs}))
trends_srcs = faqs.sources_for_question(next(q for q in inp.questions if q["id"] == "Q2"), inp)
check("sources: the drivers question sees drivers facts only, not the player list or the evidence pool",
      {s["id"] for s in trends_srcs} == {"fs.drivers.0"}, str({s["id"] for s in trends_srcs}))
players_srcs = faqs.sources_for_question(next(q for q in inp.questions if q["id"] == "Q5"), inp)
check("sources: the players question sees the player list only, not the player-tagged news",
      {s["id"] for s in players_srcs} == {"fs.players"}, str({s["id"] for s in players_srcs}))
check("sources: no evidence item (a launch, a dated news line) reaches any question's facts",
      all(s["kind"] != "evidence" for q in inp.questions for s in faqs.sources_for_question(q, inp)))
check("sources: the evidence items carry no 'detail' line into a question block",
      faqs._question_block(1, next(q for q in inp.questions if q["id"] == "Q5"), players_srcs).count("detail:") == 0)

# ------------------------------------------------------------------ prompt
text = faqs.build_prompt(plan, inp, "2026-10-23")
check("prompt: every planned faq question appears verbatim",
      all(q["question"] in text for q in inp.questions if q["id"] in plan["faq"]))
q3 = next(q for q in inp.questions if q["id"] == "Q3")
q5 = next(q for q in inp.questions if q["id"] == "Q5")
block3 = text[text.index(q3["question"]):text.index(q5["question"])]
check("prompt: a question block carries only that question's own facts",
      "Segmentation type" in block3 and "Market size" not in block3 and "Key players" not in block3)

# ------------------------------------------------------------------ stripping unsupported sentences
by_id = {q["id"]: q for q in inp.questions}
srcs = faqs.sources_for_question(by_id["Q1"], inp)
good = "The Zzz Widget Services Market is put at USD 1.2 billion in 2025 according to the report."
ans = faqs.strip_unsupported(by_id["Q1"], srcs, good + " It grows at 17.39% a year. Nothing is wrong here.", "no draft", LOG)
check("strip: the supported sentence is kept and the unsupported figure sentence is dropped", ans == good, str(ans))

named = "Acme and Beta are established vendors here. Sprout Social also sells widgets in the market."
ans2 = faqs.strip_unsupported(by_id["Q5"], faqs.sources_for_question(by_id["Q5"], inp), named, "no draft", LOG)
check("strip: company details not in the facts go", ans2.startswith("Acme and Beta"), str(ans2))

draft_figs = "The draft says the market is USD 1.2 billion."
ans3 = faqs.strip_unsupported(by_id["Q1"], srcs, "The market is about USD 1.2 billion in the draft.", draft_figs, LOG)
check("strip: a figure the draft already states is consistent and kept", ans3.endswith("in the draft."), str(ans3))

# ------------------------------------------------------------------ normalize
raw = [
    {"question": by_id["Q3"]["question"], "answer": "The report splits it by Browser Extension and Cloud Based. It also mentions GE Healthcare."},
    {"question": by_id["Q5"]["question"], "answer": "Acme and Beta lead the report's list of players."},
    {"question": by_id["Q7"]["question"], "answer": "unanswerable by test data"},
    {"question": by_id["Q1"]["question"], "answer": "not planned for the faq"},
    {"question": "not the question text", "answer": "orphaned"},
    {"question": by_id["Q6"]["question"], "answer": ""},
]
draft = "no draft"
out = faqs.normalize_faq(raw, plan, inp, draft, LOG)
# all three are planned answerable faq questions here (good_plan heads the other ones); Q6 came back empty
check("normalize: answers come back in plan order for the planned answerable questions only",
      [e["id"] for e in out] == ["Q3", "Q5"], str([e["id"] for e in out]))
check("normalize: an unanswerable question is dropped", all(e["id"] != "Q7" for e in out))
check("normalize: a question with no answer text is dropped", all(e["id"] != "Q6" for e in out))
check("normalize: an unsupported company name line is removed inside an answer",
      "GE" not in out[0]["answer"], str(out[0]["answer"]))
check("normalize: the question text is verbatim the MSQ", out[0]["question"] == by_id["Q3"]["question"])
check("normalize: entries carry the MSQ id and intent", out[0]["intent"] == "segments" and out[1]["id"] == "Q5")

# ------------------------------------------------------------------ pointing words in the answer
raw_pt = [
    {"question": by_id["Q3"]["question"], "answer": "That growth comes from digital marketing demand."},
    {"question": by_id["Q5"]["question"], "answer": "That is because demand grows."},
]
draft_pt = "That growth is what the draft is about."
out_pt = faqs.normalize_faq(raw_pt, plan, inp, draft_pt, LOG)
check("normalize: a leading 'That + noun' in an answer becomes 'The + noun'",
      len(out_pt) == 2 and out_pt[0]["answer"].startswith("The growth comes from digital marketing demand."),
      str([e["answer"] for e in out_pt]))
check("normalize: a bare pointing word in an answer ('That is ...') stays as it is",
      len(out_pt) == 2 and out_pt[1]["answer"] == "That is because demand grows.", str([e["answer"] for e in out_pt]))

# ------------------------------------------------------------------ bare pointing sentence in the answer joined with ', which'
raw_j = [
    {"question": by_id["Q3"]["question"], "answer": "Demand keeps rising across regions. That makes the field more competitive."},
]
draft_j = "Demand grows every year. That is what the draft carries."
out_j = faqs.normalize_faq(raw_j, plan, inp, draft_j, LOG)
check("normalize: a bare pointing sentence in an answer is joined to the sentence before with ', which'",
      len(out_j) == 1 and out_j[0]["answer"] == "Demand keeps rising across regions, which makes the field more competitive.",
      str([e.get("answer") for e in out_j]))

# ------------------------------------------------------------------ reader-narration filler tail in the answer
raw_f = [
    {"question": by_id["Q3"]["question"],
     "answer": "The report splits the market into Browser Extension and Cloud Based types, so readers can see "
               "which kinds of buyers the figures cover."},
]
out_f = faqs.normalize_faq(raw_f, plan, inp, "no draft", LOG)
check("normalize: an answer's filler tail that narrates what the reader will see is removed",
      len(out_f) == 1 and out_f[0]["answer"] == "The report splits the market into Browser Extension and Cloud Based types.",
      str([e.get("answer") for e in out_f]))

# ------------------------------------------------------------------ the length and dated-example backstop
trimmer = lambda a: faqs._trim_answer(a, LOG)
check("trim: at most the first two sentences of an answer are kept",
      trimmer("The first sentence holds. A second one follows. A third never prints.") == "The first sentence holds. A second one follows.")
check("trim: a sentence carrying a month-and-year date is dropped",
      trimmer("Growth runs at 13.1% a year. In March 2026, demand for robo advisory opened up.") == "Growth runs at 13.1% a year.")
check("trim: a sentence carrying a dated quarter is dropped too",
      trimmer("Growth runs at 13.1% a year. In 2025 Q3, demand for robo advisory opened up.") == "Growth runs at 13.1% a year.")
long_two = ("The answer starts here and then carries on with " + "filler " * 30
            + ". A second sentence runs on with " + "more " * 30 + "words.")
first = long_two[:long_two.index(". A second") + 1]
check("trim: an answer over 70 words is cut at the last full sentence within 70 words", trimmer(long_two) == first,
      str(trimmer(long_two)[:80]))
check("trim: an answer within the band is left unchanged", trimmer("One sentence only here.") == "One sentence only here.")

raw_trim = [{"question": by_id["Q6"]["question"],
             "answer": "The outlook is positive as growth runs at 13.1% a year. Robo advisory opens room to grow. "
                       "Overall the picture stays positive for the years ahead."}]
out_trim = faqs.normalize_faq(raw_trim, plan, inp, "no draft", LOG)
check("normalize: an answer is kept to its first two sentences",
      len(out_trim) == 1 and out_trim[0]["answer"] == "The outlook is positive as growth runs at 13.1% a year. "
                                                     "Robo advisory opens room to grow.",
      str([e.get("answer") for e in out_trim]))

raw_dated = [{"question": by_id["Q6"]["question"],
              "answer": "Growth runs at 13.1% a year, so the outlook stays positive. "
                        "In March 2026, demand for robo advisory keeps opening room to grow."}]
draft_dated = "In March 2026, demand for robo advisory keeps opening room to grow."
out_dated = faqs.normalize_faq(raw_dated, plan, inp, draft_dated, LOG)
check("normalize: a dated-example sentence in an answer is dropped, its supported sentence stays",
      len(out_dated) == 1 and out_dated[0]["answer"] == "Growth runs at 13.1% a year, so the outlook stays positive.",
      str([e.get("answer") for e in out_dated]))

# ------------------------------------------------------------------ the call (LLM stubbed)
orig = llm.chat_json
llm.chat_json = lambda *a, **k: {"faq": raw}
try:
    sections = [{"heading": "", "sop": [], "paragraphs": ["draft text"]}]
    got = faqs.run_faq(plan, inp, sections, LOG, today="2026-10-23")
    check("call: the run returns the normalized entries", [e["id"] for e in got] == [e["id"] for e in out])
finally:
    llm.chat_json = orig

llm.chat_json = lambda *a, **k: (_ for _ in ()).throw(llm.LLMError("down"))
try:
    sections = [{"heading": "", "sop": [], "paragraphs": ["draft text"]}]
    got = faqs.run_faq(plan, inp, sections, LOG)
    check("call: a failing FAQ call leaves no FAQ block and never fails", got == [])
finally:
    llm.chat_json = orig

# ------------------------------------------------------------------ the answer prompt's rules
import prompts  # noqa: E402
_fp = prompts.FAQ_MSQ_USER
check("prompt: an answer is one or two sentences of 40 to 60 words, with no company, launch or news wording",
      "One or two sentences, 40 to 60 words in all" in _fp
      and "No company names, product launches, dated examples or news" in _fp)
check("prompt: the output template asks for 1-2 sentences of 40-60 words", "<1-2 sentences, 40-60 words>" in _fp)
check("prompt: no rule asks an FAQ answer to name a non-CMI source and month", "names that source" not in _fp)
check("prompt: the sentence-length rule is rendered for the FAQ", "average about 18 words" in _fp)
check("prompt: one piece of evidence appears in one answer only", "One piece of evidence, one answer" in _fp and "never carry the same fact in different words" in _fp)
check("prompt: an answer about a loosely related fact is left empty, not filled", "loosely related" in _fp and "leave its answer empty" in _fp)
check("prompt: no 'according to ...' and no research firm's name in an answer", 'Never write "according to Coherent Market Insights"' in _fp)
check("prompt: every answer stands alone and opens on its subject, never a pointing word", "never opens on a pointing word" in _fp)
check("prompt: a rate is never labelled with a word of the model's own", "double-digit-adjacent" in _fp)
check("planner prompt: evidence is counted per piece, not per source", "not about each source" in prompts.PLAN_USER and "assigned to exactly one section" in prompts.PLAN_USER)
check("write prompt: one piece of evidence, one place in the whole post", "One piece of evidence, one place." in prompts.WRITE_USER)

# ------------------------------------------------------------------ the checks: answer length
import checks  # noqa: E402
no_sections: list[dict] = []
short = "The outlook stays positive as growth runs at 13.1% a year."
probs = checks.v2_faq_problems([{"id": "Q6", "question": by_id["Q6"]["question"],
                                 "answer": " ".join(["word"] * 65)}], plan, inp, no_sections)
check("check: an answer over 60 words is reported", any("runs 65 words" in p for p in probs), str(probs))
three = short + " Robo advisory opens room to grow. Overall the picture stays positive."
probs = checks.v2_faq_problems([{"id": "Q6", "question": by_id["Q6"]["question"], "answer": three}], plan, inp, no_sections)
check("check: an answer of three sentences is reported", any("holds 3 sentences" in p for p in probs), str(probs))
probs = checks.v2_faq_problems([{"id": "Q6", "question": by_id["Q6"]["question"], "answer": short}], plan, inp, no_sections)
check("check: an answer within the length band reports nothing", probs == [], str(probs))

print()
if test_planning.FAILURES:
    print(f"{len(test_planning.FAILURES)} FAILURE(S): {test_planning.FAILURES}")
    sys.exit(1)
print("all faq tests passed")
