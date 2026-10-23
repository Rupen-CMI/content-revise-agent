"""Offline test for the v2 check layer (restructure plan §3.9 — no network, no real LLM calls).

Run from backend/: python devtools/test_checks_v2.py

Covers checks.run_checks_v2 on a hand-built good draft (every kept and new check passes) and hand-built
bad drafts that each break exactly the check under test: sponsor token count/placement, CMI link
placement, the plan's structure, secondary keyword rules, block order/shares, number tracing, outside
attribution, placeholder shapes, FAQ = MSQs, SOP specifics (trends, sub-segments, U.S. length),
recency and title problems.
"""
import copy
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402

import test_planning  # noqa: E402

FAILURES: list[str] = []
MARKET = test_planning.MARKET
SPONSOR_TEXT = ("We recommend Northway Specialists for every widget engagement. Their team reviews each plan "
                "in fine detail. Their work spans many markets. Ask for a full review before you commit.")

def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)

INP = test_planning.make_inputs()
PLAN = planning.validate_plan(test_planning.good_plan(), INP, test_planning.LOG)
plan_sec = {s["sop"]: s for s in PLAN["sections"]}

# ------------------------------------------------------------------ builders

_FILL_SENT = "The piece looks at what the firms do next and where they go."
_FILL_WORDS = _FILL_SENT.split()


def filler(n: int) -> str:
    """Exactly n filler words, no digits and none of the plan's assigned keyword words."""
    words = []
    i = 0
    while len(words) < n:
        words.append(_FILL_WORDS[i % len(_FILL_WORDS)])
        i += 1
    t = " ".join(words)
    if not t.endswith("."):
        t += "."
    return t


def pad(words_target: int, sentences: list[str]) -> str:
    t = " ".join(sentences)
    have = len(t.split())
    if have > words_target:
        raise ValueError(f"sentence craft overruns {words_target} words: {have}")
    return (t + " " + filler(words_target - have)).strip()


def good_draft() -> list[dict]:
    """A deliberate draft for PLAN: intro (link + sponsor + filler), transition, plan's market
    sections in order with the plan's headings, every keyword/key/long-tail placed by hand."""
    ls = {lt["id"]: lt["keyword"] for lt in INP.longtails}
    return [
        {"heading": "", "sop": [], "paragraphs": [
            pad(26, [f"Start at the source: [[CMI_LINK:{MARKET}]], where this guide gathers its findings."]),
            prompts.SPONSOR_TOKEN,
            filler(118),
        ]},
        {"heading": "", "sop": ["transition"], "paragraphs": [
            pad(80, ["Why widgets belong to a bigger market is the thread this review follows next."]),
        ]},
        {"heading": plan_sec["report_context"]["heading"], "sop": ["report_context"], "paragraphs": [
            pad(60, [f"The market size of the {MARKET} stands at USD 1.2 billion in 2025.",
                     f"Its growth rate runs 13.1% CAGR a year."]),
            filler(140),
        ]},
        {"heading": plan_sec["trends"]["heading"], "sop": ["trends"], "paragraphs": [
            pad(30, [f"What is changing the {MARKET} first shows in its trends."]),
            "\n".join(f"- {filler(57)}" for _ in range(3)),
        ]},
        {"heading": plan_sec["segmentation"]["heading"], "sop": ["segmentation"], "paragraphs": [
            pad(50, [f"How the {MARKET} is divided: a segmentation into Browser Extension and Cloud Based types."]),
            pad(50, ["Data Aggregation and Market Research applications follow the same split in that field."]),
        ]},
        {"heading": plan_sec["subsegment"]["heading"], "sop": ["subsegment"], "paragraphs": [
            pad(75, [f"Cloud Based tools form one part of the {MARKET} worth a closer look."]),
            pad(75, ["Data Aggregation services form the other part worth a closer look."]),
        ]},
        {"heading": plan_sec["country_us"]["heading"], "sop": ["country_us"], "paragraphs": [
            pad(70, [f"North America leads the {MARKET}, and cloud based widget services by region is where that shows.",
                     f"A review of widget exports opened in the U.S."]),
            pad(110, [f"The United States carries the biggest share of the {MARKET} today."]),
        ]},
        {"heading": plan_sec["key_players"]["heading"], "sop": ["key_players"], "paragraphs": [
            pad(60, [f"Acme and Beta lead the {MARKET}. CMI analysts expect steady gains for both."]),
            pad(50, ["Widget us review pages list the same two names most often."]),
        ]},
        {"heading": plan_sec["conclusion"]["heading"], "sop": ["conclusion"], "paragraphs": [
            pad(30, [f"The outlook for the {MARKET} stays positive."]),
            filler(62),
        ]},
    ]


GOOD_FAQ = [
    {"id": "Q3", "intent": "segments", "question": next(q["question"] for q in INP.questions if q["id"] == "Q3"),
     "answer": f"The {MARKET} divides into Browser Extension and Cloud Based types."},
    {"id": "Q5", "intent": "players", "question": next(q["question"] for q in INP.questions if q["id"] == "Q5"),
     "answer": f"Acme and Beta lead the {MARKET} among its main companies."},
    {"id": "Q6", "intent": "outlook", "question": next(q["question"] for q in INP.questions if q["id"] == "Q6"),
     "answer": "Robo advisory opens room to grow going forward."},
]

REPORT_TEXT = "Unrelated report wording. " * 120


def run_v2(title, sections, **kw) -> list[checks.CheckResult]:
    base = dict(plan=PLAN, inp=INP, faq=GOOD_FAQ, sponsor_brand_terms=["Northway Specialists"],
                sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
    base.update(kw)
    return checks.run_checks_v2(title, sections, **base)


def by_name(results, name):
    return next(r for r in results if name.lower() in r.name.lower())


# ------------------------------------------------------------------ good draft: everything passes

TITLE = PLAN["title"]
good = run_v2(TITLE, good_draft())
for r in good:
    check(f"good: {r.name}", r.passed, r.detail)
check("good: length lands in the band", by_name(good, "Length").passed, by_name(good, "Length").detail)

# ------------------------------------------------------------------ sponsor paragraph

d = good_draft()
d[0]["paragraphs"][1] = f"Mentioned inline, {prompts.SPONSOR_TOKEN}, written into a sentence."
r = by_name(run_v2(TITLE, d), "Retained backlink")
check("sponsor: an inline token still counts as placed once", r.passed, r.detail)

d = good_draft()
d.append({"heading": "Extra", "sop": ["key_players"], "paragraphs": [prompts.SPONSOR_TOKEN + " again."]})
r = by_name(run_v2(TITLE, d), "Retained backlink")
check("sponsor: a second token fails", not r.passed, r.detail)

d = good_draft()
d[1]["paragraphs"].append(prompts.SPONSOR_TOKEN)
r = by_name(run_v2(TITLE, d), "Retained paragraph sits")
check("sponsor: the token outside the intro fails the slot check", not r.passed, r.detail)

d = good_draft()
d[0]["paragraphs"][1] = SPONSOR_TEXT     # the paragraph written out where the token should sit
r = by_name(run_v2(TITLE, d), "Retained backlink")
check("sponsor: the retained paragraph written out instead of the token fails", not r.passed, r.detail)

# ------------------------------------------------------------------ CMI link

d = good_draft()
d[0]["paragraphs"][0] = pad(26, ["Start at the source of this market story, where this guide gathers its findings."])
d[2]["paragraphs"][1] = filler(134) + f" See [[CMI_LINK:{MARKET}]] for the full report."
r = by_name(run_v2(TITLE, d), "CMI link sits")
check("cmi link: a late link fails the first-200-words check", not r.passed, r.detail)

d = good_draft()
d[0]["paragraphs"][0] = pad(26, [f"{MARKET} appears in plain words first, then the report: [[CMI_LINK:{MARKET}]]."])
r = by_name(run_v2(TITLE, d), "first mention")
check("cmi link: a plain-text market mention before the link fails", not r.passed, r.detail)

d = good_draft()
d[0]["paragraphs"][0] = f"Start at [[CMI_LINK:Some Other Market]] here: {MARKET}."
r = by_name(run_v2(TITLE, d), "anchor names")
check("cmi link: the wrong anchor market fails", not r.passed, r.detail)

# ------------------------------------------------------------------ structure

d = good_draft()
bad_title = "The Growth Story of the Zzz Widget Services Market at 13% Now"
run = run_v2(bad_title, d)
check("structure: a different title fails the structure check",
      not by_name(run, "Draft structure").passed)
check("structure: a title with a figure fails planning's title checks",
      not by_name(run, "Title passes").passed, by_name(run, "Title passes").detail)

d = good_draft()
d[0]["paragraphs"][2] = filler(600)          # intro 4x its share
d.pop(2)                                     # and report_context missing
run = run_v2(TITLE, d)
check("structure: a left-out market section fails", not by_name(run, "Draft structure").passed,
      by_name(run, "Draft structure").detail)

d = good_draft()
d[2], d[3] = d[3], d[2]
r1 = "Draft structure"
run = run_v2(TITLE, d)
check("structure: swapped market sections fail", not by_name(run, r1).passed)

d = good_draft()
d[2]["heading"] = "How big is it?"
run = run_v2(TITLE, d)
check("structure: a changed heading fails", not by_name(run, r1).passed)

# ------------------------------------------------------------------ block order

d = good_draft()
extra = {"heading": "", "sop": [], "paragraphs": [filler(60)]}
order = [d[0], d[1], d[2]] + [extra] + d[3:]
run = run_v2(TITLE, order)
check("block order: a publisher paragraph returning after market sections fails",
      not by_name(run, "Block order").passed, by_name(run, "Block order").detail)

d = good_draft()
run = run_v2(TITLE, d)
check("block order: the good draft keeps intro -> transition -> market",
      by_name(run, "Block order").passed)

# ------------------------------------------------------------------ block shares

d = good_draft()
d[0]["paragraphs"][2] = filler(420)
run = run_v2(TITLE, d)
check("block shares: a 400-word intro breaks the 20% cap",
      not by_name(run, "Block shares").passed, by_name(run, "Block shares").detail)

d = good_draft()
d[1]["paragraphs"][0] = filler(210)
run = run_v2(TITLE, d)
check("block shares: a 210-word transition breaks the band",
      not by_name(run, "Block shares").passed, by_name(run, "Block shares").detail)

run = run_v2(TITLE, good_draft())
check("block shares: the good draft sits in all three bands", by_name(run, "Block shares").passed,
      by_name(run, "Block shares").detail)

# ------------------------------------------------------------------ keywords

d = good_draft()
d[3]["paragraphs"][0] = pad(30, [f"What is changing the {MARKET} first shows in its trends and its size."])
run = run_v2(TITLE, d)
check("keywords: two assigned keywords in one paragraph pass the hard check but are noted (soft)",
      by_name(run, "Secondary keywords").passed and not by_name(run, "No paragraph carries two").passed,
      by_name(run, "Secondary keywords").detail)

d = good_draft()
d[3]["paragraphs"][0] = pad(30, [f"What is changing the {MARKET} first shows in its trends, its size and its segmentation."])
run = run_v2(TITLE, d)
check("keywords: three assigned keywords piled into one paragraph fail (stuffing)",
      not by_name(run, "Secondary keywords").passed, by_name(run, "Secondary keywords").detail)

d = good_draft()
d[2]["paragraphs"][0] = pad(60, [f"The {MARKET} totals USD 1.2 billion in 2025.", "Its growth rate runs 13.1% CAGR a year."])
run = run_v2(TITLE, d)
check("keywords: a section missing its keyword fails",
      not by_name(run, "Secondary keywords").passed, by_name(run, "Secondary keywords").detail)

# ------------------------------------------------------------------ number trace

d = good_draft()
d[2]["paragraphs"][1] = filler(100) + " Other trackers put it at 7.3 million devices."
run = run_v2(TITLE, d)
check("trace: an unbacked 7.3 fails", not by_name(run, "Every number").passed,
      by_name(run, "Every number").detail)

d = good_draft()
d[-1]["faq"] = None
run = run_v2(TITLE, d, faq=[])
check("trace: the good draft traces every number", by_name(run, "Every number").passed)

# ------------------------------------------------------------------ attribution

NEWS = {"id": "W3", "kind": "evidence", "tags": ["context"], "text": "Regional sales hit USD 4.5 billion in 2025.",
        "detail": "", "source": "Field Notes"}
inp2 = test_planning.make_inputs()
inp2.items["W3"] = NEWS

d = good_draft()
d[2]["paragraphs"][1] = pad(50, ["Regional sales hit USD 4.5 billion in 2025, per the counted filings."]) + " " + filler(80)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: an outside figure with no source named fails",
      not by_name(run, "Outside-source").passed, by_name(run, "Outside-source").detail)

d = good_draft()
d[2]["paragraphs"][1] = pad(60, ["Regional sales hit USD 4.5 billion in 2025, the report at Field Notes finds."]) + " " + filler(70)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: naming the source clears it", by_name(run, "Outside-source").passed,
      by_name(run, "Outside-source").detail)

d = good_draft()
d[2]["paragraphs"][0] = pad(60, [f"CMI puts regional sales at USD 4.5 billion in 2025.",
                                 f"The market size of the {MARKET} stands at USD 1.2 billion."])
d[2]["paragraphs"][1] = filler(140)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: crediting CMI for an outside figure fails",
      not by_name(run, "Outside-source").passed, by_name(run, "Outside-source").detail)

# ------------------------------------------------------------------ placeholders

d = good_draft()
d[-1]["paragraphs"][1] = filler(30) + " The report quotes USD 2025 as a stated rate and rose by % to a <Percentage> shape."
run = run_v2(TITLE, d)
check("placeholders: sample shapes left in prose fail", not by_name(run, "placeholder").passed,
      by_name(run, "placeholder").detail)

run = run_v2(TITLE, good_draft())
check("placeholders: the good draft carries none", by_name(run, "placeholder").passed)

# ------------------------------------------------------------------ FAQ

d = good_draft()
extra_faq = GOOD_FAQ + [{"id": "Q9", "intent": "size", "question": "Why buy widgets today?",
                         "answer": "The market grew 4.4% last year."}]
run = run_v2(TITLE, d, faq=extra_faq)
check("faq: an unplanned question fails", not by_name(run, "FAQ questions").passed,
      by_name(run, "FAQ questions").detail)

d = good_draft()
faq2 = [dict(GOOD_FAQ[0])]
faq2 += [{"id": "Q5", "intent": "players", "question": next(q["question"] for q in INP.questions if q["id"] == "Q5"),
          "answer": "Acme and Beta lead the field, which grew 9.9% overall."}]
run = run_v2(TITLE, d, faq=faq2)
check("faq: an unbacked figure in an answer fails", not by_name(run, "FAQ questions").passed,
      by_name(run, "FAQ questions").detail)

# ------------------------------------------------------------------ SOP specifics: trends, sub-segments, U.S.

d = good_draft()
d[3]["paragraphs"].pop()
run = run_v2(TITLE, d)
check("trends: only 2 trend items fail", not by_name(run, "Three trends").passed)

d = good_draft()
d[4]["paragraphs"] = d[4]["paragraphs"][:1]
d[5] = {**d[5], "paragraphs": d[5]["paragraphs"][:1]}
run = run_v2(TITLE, d)
check("trends/subsegment: a one-paragraph subsegment section fails",
      not by_name(run, "Two sub-segment").passed, by_name(run, "Two sub-segment").detail)

d = good_draft()
d[6]["paragraphs"] = [pad(10, ["Short."])]
run = run_v2(TITLE, d)
check("us: a 10-word U.S. section fails", not by_name(run, "U.S. analysis").passed,
      by_name(run, "U.S. analysis").detail)

d = good_draft()
d[6]["paragraphs"] = [filler(240)]
run = run_v2(TITLE, d)
check("us: a 240-word U.S. section fails", not by_name(run, "U.S. analysis").passed,
      by_name(run, "U.S. analysis").detail)

# ------------------------------------------------------------------ recency + trends-from-dated-sources

d = good_draft()
# W1 maps to hits[0]: an undated first hit that the selection calls a trends fact fails
hits = [types.SimpleNamespace(date=None), types.SimpleNamespace(date="2026-03-01")]
selection = {"evidence": [{"id": "W1", "use_for_all": ["trends"]}]}
run = run_v2(TITLE, d, selection=selection, hits=hits)
check("recency: an undated trends source fails", not by_name(run, 'dated results').passed,
      by_name(run, 'dated results').detail)

hits2 = [types.SimpleNamespace(date="2026-03-01"), types.SimpleNamespace(date="2026-01-01")]
run = run_v2(TITLE, d, selection=selection, hits=hits2)
check("recency: dated trends sources pass", by_name(run, 'dated results').passed)

run = run_v2(TITLE, d, selection=selection, hits=hits)
check("recency: the good draft carries no stale event", by_name(run, "Dated events").passed)

# ------------------------------------------------------------------ guest brand

d = good_draft()
d[0]["paragraphs"][2] = filler(100) + " Northway Specialists recommend more Northway Specialists always."
run = run_v2(TITLE, d)
check("brand: a guest-brand leak outside the retained paragraph fails",
      not by_name(run, "Guest brand").passed, by_name(run, "Guest brand").detail)

# ------------------------------------------------------------------ Flesch below the floor

dense_sent = "Unaccountably understanding particular individuals characterize developmental considerations."
score = checks._flesch_reading_ease(" ".join([dense_sent] * 15))
check("flesch: dense graduate-level prose measures below the Flesch floor", score is not None and score < checks._V2_FLESCH_MIN,
      f"score {score if score is None else round(score)}")

# ------------------------------------------------------------------ vague-reference openers

VAGUE_NAME = "Every sentence names what it refers to"

d = good_draft()
d[2]["paragraphs"][0] += " This shows how fast the market grows."
run = run_v2(TITLE, d)
r = by_name(run, VAGUE_NAME)
check("vague: 'This shows how fast the market grows.' fails", not r.passed, r.detail)
check("vague: the detail names the pointing-word pattern", "opens with a pointing word" in r.detail, r.detail)

d = good_draft()
d[4]["paragraphs"][1] = "That is why buyers pay less."
run = run_v2(TITLE, d)
check("vague: 'That is why buyers pay less.' fails", not by_name(run, VAGUE_NAME).passed,
      by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[5]["paragraphs"][1] = "It means buyers pay less."
run = run_v2(TITLE, d)
check("vague: 'It means buyers pay less.' fails (bare pronoun)", not by_name(run, VAGUE_NAME).passed,
      by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[6]["paragraphs"][1] = "It is the largest region today."
run = run_v2(TITLE, d)
check("vague: 'It is the largest region today.' states its subject and passes",
      by_name(run, VAGUE_NAME).passed, by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[4]["paragraphs"][1] = d[4]["paragraphs"][1] + "\n- This keeps costs low."
run = run_v2(TITLE, d)
r = by_name(run, VAGUE_NAME)
check("vague: a bullet line opening with This fails", not r.passed, r.detail)

run = run_v2(TITLE, good_draft(),
             sponsor_text="This approach keeps the stack simple for every team that uses it.")
check("vague: the retained sponsor paragraph is exempt from the pointing-word check",
      by_name(run, VAGUE_NAME).passed, by_name(run, VAGUE_NAME).detail)

d = good_draft()
faq2 = copy.deepcopy(GOOD_FAQ)
faq2[0]["answer"] = "That is because the market grows."
run = run_v2(TITLE, d, faq=faq2)
r = by_name(run, VAGUE_NAME)
check("vague: an FAQ answer opening with That fails", not r.passed, r.detail)
check("vague: the FAQ hit is labelled with its question", "FAQ answer to" in r.detail, r.detail)

# ------------------------------------------------------------------ heading shape

HEADING_NAME = "Headings: question headings are planned MSQs"

d = good_draft()
plan2 = copy.deepcopy(PLAN)
plan2["sections"][2]["heading"] = "Where the market stands"          # segmentation: not an MSQ section
d[4]["heading"] = plan2["sections"][2]["heading"]
run = run_v2(TITLE, d, plan=plan2)
r = by_name(run, HEADING_NAME)
check("headings: a non-question heading that names no SOP topic fails", not r.passed, r.detail)
check("headings: the detail carries planning's reason", "not a clean topic heading" in r.detail, r.detail)

d = good_draft()
plan2 = copy.deepcopy(PLAN)
plan2["sections"][2]["heading"] = "What is changing the market?"     # a question where no MSQ was planned
d[4]["heading"] = plan2["sections"][2]["heading"]
run = run_v2(TITLE, d, plan=plan2)
r = by_name(run, HEADING_NAME)
check("headings: a question heading on a non-MSQ section fails", not r.passed, r.detail)
check("headings: the detail says why (it is a question)", "it is a question" in r.detail, r.detail)

run = run_v2(TITLE, good_draft())
check("headings: the good draft's headings are planned MSQs / clean topics",
      by_name(run, HEADING_NAME).passed, by_name(run, HEADING_NAME).detail)

# ------------------------------------------------------------------ opening jargon (soft)

JARGON_NAME = "The opening and transition use no technical terms"

d = good_draft()
d[0]["paragraphs"][2] = filler(111) + " A page can be 300KB of markup."
run = run_v2(TITLE, d)
r = by_name(run, JARGON_NAME)
check("jargon: 300KB in the opening fails the technical-terms check", not r.passed, r.detail)
check("jargon: the technical-terms check is advisory", "(soft)" in r.name)
run = run_v2(TITLE, good_draft())
check("jargon: the good draft's opening and transition are plain",
      by_name(run, JARGON_NAME).passed, by_name(run, JARGON_NAME).detail)
run = run_v2(TITLE, good_draft(),
             sponsor_text=SPONSOR_TEXT + " The retained paragraph can mention kilobytes.")
check("jargon: kilobytes inside the retained sponsor paragraph is exempt",
      by_name(run, JARGON_NAME).passed, by_name(run, JARGON_NAME).detail)

# ------------------------------------------------------------------ summary

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all v2 check tests passed")
