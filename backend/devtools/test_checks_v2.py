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
            pad(30, [f"What is changing the {MARKET} first shows in its trends, and {ls['L2']} leads the list."]),
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

# the unit of naming is one line of a paragraph: a source named once covers the line's later sentences
inp2.items["W3"]["detail"] = "Usage reaches 2.1 million users."
inp2.items["W5"] = {"id": "W5", "kind": "evidence", "tags": ["context"],
                    "text": "Vendor adoption stands at 8.4 percent of firms.", "detail": "",
                    "source": "Tech Journal", "historical": False, "sections": {"report_context"}}

d = good_draft()
d[2]["paragraphs"][1] = (pad(50, ["Regional sales hit USD 4.5 billion in 2025, the report at Field Notes finds.",
                                  "Usage reaches 2.1 million users in the same report."]) + " " + filler(80))
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: a later sentence of the same line needs no repeat of the source's name",
      by_name(run, "Outside-source").passed, by_name(run, "Outside-source").detail)

d = good_draft()
d[2]["paragraphs"][1] = (pad(50, ["Regional sales hit USD 4.5 billion in 2025, the report at Field Notes finds."])
                         + "\nUsage reaches 2.1 million users on its own line.")
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: the same second sentence on a new line needs the source's name again",
      not by_name(run, "Outside-source").passed, by_name(run, "Outside-source").detail)

d = good_draft()
d[2]["paragraphs"][1] = (pad(50, ["Regional sales hit USD 4.5 billion in 2025, the report at Field Notes finds.",
                                  "Vendor adoption stands at 8.4% of firms."]) + " " + filler(80))
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("attribution: a different outside source's figure in that line still needs its own name",
      not by_name(run, "Outside-source").passed and "Tech Journal" in by_name(run, "Outside-source").detail,
      by_name(run, "Outside-source").detail)

# Two outside facts sharing one token: the sentence belongs to the fact with the most figures in common,
# the weaker half-claim is coincidence and is never reported
inp3 = test_planning.make_inputs()
inp3.items["W6"] = {"id": "W6", "kind": "evidence", "tags": ["context"],
                    "text": "Adoption reaches 9 units among 150 teams.", "detail": "",
                    "source": "Alpha Post", "historical": False, "sections": {"trends"}}
inp3.items["W7"] = {"id": "W7", "kind": "evidence", "tags": ["context"],
                    "text": "Usage stands at 9 units, while 40 firms lag.", "detail": "",
                    "source": "Beta Desk", "historical": False, "sections": {"trends"}}
d = good_draft()
d[2]["paragraphs"][1] = pad(50, ["Adoption reaches 9 units among the tracked firms, while 40 firms lag behind."]) + " " + filler(80)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp3, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
r = by_name(run, "Outside-source")
check("attribution: a token shared by two facts is blamed only on the fact with the most figures in common",
      not r.passed and "Beta Desk" in r.detail and "Alpha Post" not in r.detail, r.detail)

d = good_draft()
d[2]["paragraphs"][1] = (pad(50, ["Usage reaches 9 units, the beta desk finds.",
                                  "Adoption stands at 9 of 150 teams in the same tally."]) + " " + filler(80))
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp3, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
r = by_name(run, "Outside-source")
check("attribution: a fact whose every figure the line's stronger owner does not share keeps its own naming duty",
      not r.passed and "Alpha Post" in r.detail and "Beta Desk" not in r.detail, r.detail)

d = good_draft()
d[2]["paragraphs"][1] = pad(60, ["Adoption reaches 9 units among the tracked firms, and the report at Beta Desk finds ",
                                 "40 firms lag behind."]) + " " + filler(70)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp3, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
r = by_name(run, "Outside-source")
check("attribution: naming the stronger owner's source in the carrying line still clears it",
      r.passed, r.detail)

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

# the check layer agrees with planning: a plain size/growth topic heading is clean, a section label is not
d = good_draft()
plan2 = copy.deepcopy(PLAN)
plan2["sections"][0]["msq"] = None
plan2["sections"][0]["heading"] = "Zzz Widget Services Market Size and Growth"
d[2]["heading"] = plan2["sections"][0]["heading"]
run = run_v2(TITLE, d, plan=plan2)
check("headings: the plain size/growth fallback for report_context passes",
      by_name(run, HEADING_NAME).passed, by_name(run, HEADING_NAME).detail)

d = good_draft()
plan2 = copy.deepcopy(PLAN)
plan2["sections"][0]["msq"] = None
plan2["sections"][0]["heading"] = "Report Context for the Zzz Widget Services Market"
d[2]["heading"] = plan2["sections"][0]["heading"]
run = run_v2(TITLE, d, plan=plan2)
r = by_name(run, HEADING_NAME)
check("headings: the old report_context label heading fails", not r.passed, r.detail)
check("headings: the label failure carries planning's reason", "section label" in r.detail, r.detail)

# ------------------------------------------------------------------ opening jargon (soft)

JARGON_NAME = "The opening and transition hold no measurements or units"

d = good_draft()
d[0]["paragraphs"][2] = filler(111) + " A page can be 300KB of markup."
run = run_v2(TITLE, d)
r = by_name(run, JARGON_NAME)
check("jargon: 300KB in the opening fails the measurement check", not r.passed, r.detail)
check("jargon: the note names the measurement it holds", "holds the measurement" in r.detail, r.detail)
check("jargon: the measurement check is advisory", "(soft)" in r.name)
d = good_draft()
d[0]["paragraphs"][2] = filler(100) + " A browser script and a parser read each page for the buyer every day."
run = run_v2(TITLE, d)
r = by_name(run, JARGON_NAME)
check("jargon: tech words with no measurement in the opening still pass", r.passed, r.detail)
run = run_v2(TITLE, good_draft())
check("jargon: the good draft's opening and transition are plain",
      by_name(run, JARGON_NAME).passed, by_name(run, JARGON_NAME).detail)
run = run_v2(TITLE, good_draft(),
             sponsor_text=SPONSOR_TEXT + " The retained paragraph can mention kilobytes.")
check("jargon: kilobytes inside the retained sponsor paragraph is exempt",
      by_name(run, JARGON_NAME).passed, by_name(run, JARGON_NAME).detail)

# ------------------------------------------------------------------ active voice (soft)

VOICE_NAME = "Active voice: at most 1 sentence in 6 is passive"

run = run_v2(TITLE, good_draft())
r = by_name(run, VOICE_NAME)
check("voice: the good draft is judged active and the check is advisory", r.passed and "(soft)" in r.name, r.name)

check("voice: an all-active draft gets no notes",
      checks.v2_active_voice_notes([{"heading": "", "sop": ["trends"], "paragraphs": [
          "Vendors sell tools. Buyers compare plans. Demand grows each year."]}]) == [])

_secs_passive = [{"heading": "", "sop": ["trends"], "paragraphs": [
    "The field is driven by demand. The tools are sold by vendors. The report is written by analysts. "
    "The rate is set each year. Buyers compare plans."]}]
notes = checks.v2_active_voice_notes(_secs_passive)
check("voice: a mostly passive draft gets notes, each a truncated sentence",
      len(notes) >= 1 and all(len(n) <= 90 for n in notes) and "is driven" in notes[0], str(notes))

check("voice: whitelisted adjectival participles are not counted as passive",
      checks.v2_active_voice_notes([{"heading": "", "sop": ["trends"], "paragraphs": [
          "Growth is expected to hold. The index is based on exports. The list is limited to five names. "
          "The field is fragmented by design."]}]) == [])

d = good_draft()
# The check's threshold is one passive sentence in six over the whole body, and the filler helper itself
# emits many short fragments, so a clearly failing draft needs far more passives than five.
d[2]["paragraphs"].append(
    "The market is driven by steady demand. The tools are sold by regional vendors. "
    "The figures are shown in the report. The plan is made once a year. The papers are held in one file. "
    "The team is led by two owners. The service is run from one office. The rate is set each year. "
    "The names are taken from the list. The awards are given each spring. The traits are found in most firms. "
    "The units are brought in each fall. The roots are known to every buyer. The picks are chosen by the panel. "
    "The hours are spent on review. The notes are kept in one drawer. The wins are counted by the office. "
    "The lines are built from one template. The leads are found by hand. The fees are paid by the buyer. "
    "The goods are bought in pairs. The checks are done each week. The trends are seen in every chart.")
run = run_v2(TITLE, d)
r = by_name(run, VOICE_NAME)
check("voice: a draft with mostly passive sentences fails (soft)", not r.passed, r.detail)
check("voice: the note carries the offending sentences", "The market is driven" in r.detail, r.detail)

# ------------------------------------------------------------------ long-tail noun pile (soft)

PILE_NAME = "No long-tail phrase is pasted as a noun pile"

# a long phrase of nearly all content words, assigned to the subsegment section (its fact sits there)
inp4 = test_planning.make_inputs()
inp4.longtails = inp4.longtails + [
    {"id": "L4", "keyword": "custom cloud based widget services marketplace pricing", "fact": "fs.segments.0", "why": "x"}]
raw4 = test_planning.good_plan()
raw4["sections"][3]["facts"] = ["W2", "fs.segments.0", "fs.segments.1"]   # L4 rests on fs.segments.0
raw4["sections"][3]["longtail"] = "L4"
plan4 = planning.validate_plan(raw4, inp4, test_planning.LOG)
lt_by_sop = {s["sop"]: s["longtail"] for s in plan4["sections"]}
check("pile setup: the plan keeps the placed long-tails with L4 on subsegment",
      sum(1 for s in plan4["sections"] if s.get("longtail")) == 3 and lt_by_sop.get("subsegment") == "L4",
      str(lt_by_sop))

d = good_draft()
d[5]["paragraphs"][0] = pad(75, ["Custom cloud based widget services marketplace pricing decides which tools a team buys."])
run = run_v2(TITLE, d, plan=plan4, inp=inp4, include_disconnected=True)
r = by_name(run, PILE_NAME)
check("pile: a phrase pasted as the pile-of-nouns subject at the head of a sentence is flagged",
      not r.passed, r.detail)
check("pile: the note carries the offending sentence", "pile of nouns" in r.detail and "Custom cloud based" in r.detail, r.detail)
check("pile: the check is advisory and log-only", "(soft)" in r.name, r.name)

d = good_draft()
d[5]["paragraphs"][0] = pad(75, ["Widget teams pay custom prices for the cloud services they use in daily work."])
run = run_v2(TITLE, d, plan=plan4, inp=inp4, include_disconnected=True)
r = by_name(run, PILE_NAME)
check("pile: a normal placement of the same words passes", r.passed, r.detail)

d = good_draft()
run = run_v2(TITLE, d, plan=plan4, inp=inp4, include_disconnected=True)
r = by_name(run, PILE_NAME)
check("pile: the good draft flags nothing", r.passed, r.detail)

notes = checks.longtail_pile_notes([{"heading": "", "sop": ["trends"], "paragraphs": [
    "Custom cloud based widget services marketplace pricing decides which tools a team buys and which features it needs."]}],
    ["custom cloud based widget services marketplace pricing"])
check("pile: the helper alone flags the pile and lists the flagged sentence",
      len(notes) == 1 and "pile of nouns" in notes[0] and "decides which tools" in notes[0], str(notes))
check("pile: the flagged sentence is truncated in the note",
      not any("which features it needs" in n for n in notes), str(notes))
notes = checks.longtail_pile_notes([{"heading": "", "sop": ["trends"], "paragraphs": [
    "Teams pay custom prices for cloud services they use."]}],
    ["custom cloud based widget services marketplace pricing"])
check("pile: the helper alone passes a normal sentence", notes == [], str(notes))

# ------------------------------------------------------------------ loose keyword matcher (checks.keyword_words_in_sentence)

PHRASE = "zorb widget pricing models"       # content stems: zorb, widget, pric(ing), model(s)
SPREAD_SENT = "Pricing models for the widget range were set by the zorb desk."
spans = checks.keyword_words_in_sentence(SPREAD_SENT, PHRASE)
check("loose: reordered content words spread across one sentence are found, one span each",
      spans is not None and sorted(SPREAD_SENT[s:e].lower() for s, e in spans) ==
      ["models", "pricing", "widget", "zorb"], str(spans))
check("loose: a sentence missing one content word returns None",
      checks.keyword_words_in_sentence("Pricing models for the widget range were set by the desk.", PHRASE) is None)
check("loose: words sitting in two different sentences return None",
      checks.keyword_words_in_sentence("Pricing models for the widget range sank. The zorb desk took over.", PHRASE) is None)
check("loose: stem folding matches the singular for the phrase's plural",
      checks.keyword_words_in_sentence("One zorb widget pricing model stands out.", PHRASE) is not None)
check("loose: a synonym still does not count",
      checks.keyword_words_in_sentence("Gizmo widget pricing models lead the list.", PHRASE) is None)
TIGHT_SENT = "zorb widget pricing models lead the list."
tight_spans = checks.keyword_words_in_sentence(TIGHT_SENT, PHRASE)
check("loose: a tight light-glued stretch still wins and is returned as per-word spans",
      tight_spans is not None and sorted(TIGHT_SENT[s:e].lower() for s, e in tight_spans) ==
      ["models", "pricing", "widget", "zorb"], str(tight_spans))

# ------------------------------------------------------------------ derivational stem fold (checks.word_stem)

STEM_GROUP = ("segmentation", "segments", "segmented", "segment")
check("word_stem: the -ation noun, its verb, participle and base reduce to one stem",
      {checks.word_stem(w) for w in STEM_GROUP} == {"segment"},
      str({w: checks.word_stem(w) for w in STEM_GROUP}))
check("word_stem: lowercased and whitespace-trimmed",
      checks.word_stem("  Segments ") == "segment")
check("word_stem: the plural folds, the adjective stands alone (equivalence only where the algorithm yields it)",
      checks.word_stem("regions") == "region" and checks.word_stem("regional") == "regional"
      and checks.word_stem("region") == "region")
STEM_PHRASE = "zorb widget pricing segmentation"
check("stem fold: an -ation keyword word is satisfied by a sentence using the verb form",
      checks.keyword_words_in_sentence("The zorb desk segments widget pricing.", STEM_PHRASE) is not None)
check("stem fold: an -ation keyword word is satisfied by a sentence using the participle form",
      checks.keyword_words_in_sentence("The zorb desk segmented widget pricing.", STEM_PHRASE) is not None)
check("stem fold: the -ation keyword word's own noun form still matches as before",
      checks.keyword_words_in_sentence("Pricing and widget segmentation on the zorb desk.", STEM_PHRASE) is not None)
check("stem fold: unrelated words sharing a prefix are not matched (market/marked)",
      checks.keyword_words_in_sentence("The zorb marked pricing widget models.", "zorb widget market pricing models") is None)
check("stem fold: unrelated words sharing a prefix are not matched (regional/regions)",
      checks.keyword_words_in_sentence("Widget pricing across the zorb regions.", "regional widget pricing") is None)

# The derivational fold is a switch: the keyword-stuffing count needs the exact word shapes, so it runs
# the same matcher with use_stems=False while the used-in-its-section tests keep the fold on by default.
check("stem fold off: a keyword word matched only through the fold no longer matches",
      checks.keyword_words_in_sentence("The zorb desk segments widget pricing.", STEM_PHRASE, use_stems=False) is None)
check("stem fold off: the -ation keyword word's own noun form still matches",
      checks.keyword_words_in_sentence(
          "Pricing and widget segmentation on the zorb desk.", STEM_PHRASE, use_stems=False) is not None)
check("stem fold off: the participle form no longer matches",
      checks.keyword_words_in_sentence("The zorb desk segmented widget pricing.", STEM_PHRASE, use_stems=False) is None)
check("secondary loose: with the fold off, the sentence's verb form no longer satisfies the -ation keyword",
      checks.secondary_keyword_in_paragraph(
          "The Zzz Widget Services Market keeps changing. "
          "Vendors segment zorb widget pricing by tier.",
          MARKET, "zorb widget pricing segmentation", use_stems=False) is False)

# Stuffing counts exact words only: an inflected keyword word ("segments" for "segmentation") counted
# through the fold would stuff-count a paragraph that merely uses the topic's plural, so a paragraph
# with two exact keyword uses plus one fold-only use passes the hard check (a soft 2-keyword note is fine).
d = good_draft()
d[3]["paragraphs"][0] = pad(30, [f"What is changing the {MARKET} first shows in its trends, its size "
                                 "and how its segments divide the field."])
run = run_v2(TITLE, d)
check("keywords: a fold-only keyword word does not tip a paragraph into stuffing",
      by_name(run, "Secondary keywords").passed, by_name(run, "Secondary keywords").detail)

# ------------------------------------------------------------------ secondary keyword: loose verdict keeps the anchor rule

check("secondary loose: a spread use inside one sentence passes while the market name anchors the paragraph",
      checks.secondary_keyword_in_paragraph(
          "The Zzz Widget Services Market keeps changing. "
          "Vendors price zorb widgets so many services keep pricing parts apart.",
          MARKET, "zorb widget services pricing") is True)
check("secondary loose: with the market anchor absent from the paragraph the keyword still fails",
      checks.secondary_keyword_in_paragraph(
          "Vendors price zorb widgets so many services keep pricing parts apart.",
          MARKET, "zorb widget services pricing") is False)
check("secondary loose: a suffix -ation word is satisfied by the sentence's verb form (word_stem fold)",
      checks.secondary_keyword_in_paragraph(
          "The Zzz Widget Services Market keeps changing. "
          "Vendors segment zorb widget pricing by tier.",
          MARKET, "zorb widget pricing segmentation") is True)

# ------------------------------------------------------------------ loose long-tail matching in run_checks_v2

# A plan whose sub-segment section carries a phrase whose content words are written spread, reordered
# and inflected across one sentence — no light-glued stretch anywhere, so only the loose matcher can
# credit it. The plan keeps three assigned long-tails (validate_plan drops the fourth).
LT_SPREAD_SENT = "Vendors price zorb widgets so many services keep pricing parts apart."
check("long-tail loose: the tight matcher finds no use in the spread sentence (the old behaviour)",
      checks.longtail_spans(LT_SPREAD_SENT, "zorb widget services pricing") == [])
inp5 = test_planning.make_inputs()
inp5.longtails = inp5.longtails + [
    {"id": "L5", "keyword": "zorb widget services pricing", "fact": "fs.segments.0", "why": "x"}]
raw5 = test_planning.good_plan()
raw5["sections"][3]["facts"] = ["W2", "fs.segments.0", "fs.segments.1"]   # L5 rests on fs.segments.0
raw5["sections"][3]["longtail"] = "L5"      # the sub-segment section, where its fact sits
plan5 = planning.validate_plan(raw5, inp5, test_planning.LOG)
d = good_draft()
d[5]["paragraphs"][0] = pad(75, [LT_SPREAD_SENT])
run = run_v2(TITLE, d, plan=plan5, inp=inp5, include_disconnected=True)
r = by_name(run, "Every assigned long-tail")
check("long-tail loose: the spread, reordered use counts for its own section",
      r.passed and "zorb" not in r.detail, r.detail)
r = by_name(run, "Uses 2-3 long-tail")
check("long-tail loose: the spread use is counted in the used tally",
      r.passed and "zorb widget services pricing" in r.detail, r.detail)
d = good_draft()
run = run_v2(TITLE, d, plan=plan5, inp=inp5, include_disconnected=True)
r = by_name(run, "Every assigned long-tail")
check("long-tail loose: with the phrase's words absent the own-section check still fails",
      not r.passed and "zorb widget services pricing" in r.detail, r.detail)

# ------------------------------------------------------------------ noun pile: runs of a keyword's own words (soft)

notes = checks.longtail_pile_notes([{"heading": "", "sop": ["subsegment"], "paragraphs": [
    "Acme runs custom cloud based widget services marketplace pricing setups for buyers."]}],
    ["custom cloud based widget services marketplace pricing"])
check("pile extended: five-plus consecutive keyword words mid-sentence are flagged as a pile run",
      len(notes) == 1 and "pile of nouns" in notes[0] and "run together" in notes[0] and "Acme runs" in notes[0],
      str(notes))
notes = checks.longtail_pile_notes([{"heading": "", "sop": ["subsegment"], "paragraphs": [
    "Vendors charge custom prices so cloud widgets spread marketplace pricing costs apart."]}],
    ["custom cloud based widget services marketplace pricing"])
check("pile extended: the same words spread across the clause raise no pile note", notes == [], str(notes))

# ------------------------------------------------------------------ disconnected soft checks (owner's instruction)

# Five soft checks are DISCONNECTED from the default results on the owner's instruction: the code that
# builds them stays in checks.py, and include_disconnected=True is the only way to get them back.

DISC_NAMES = {
    "Uses 2-3 long-tail keywords (soft)",
    "Every assigned long-tail is used in its own section (soft)",
    "No long-tail phrase is pasted as a noun pile (soft)",
    "No sentence over 30 words (soft)",
    "Readability Flesch >= 50 (soft)",
}

check("disconnected: the set holds exactly five names", len(checks.V2_DISCONNECTED_CHECKS) == 5,
      str(checks.V2_DISCONNECTED_CHECKS))
check("disconnected: the set holds exactly the five disconnected names",
      set(checks.V2_DISCONNECTED_CHECKS) == DISC_NAMES, str(sorted(checks.V2_DISCONNECTED_CHECKS)))
check("disconnected: the set entries derive from the constants that build the names",
      f"Uses {checks.LONGTAIL_MIN_USED}-{checks.LONGTAIL_MAX_USED} long-tail keywords (soft)" in checks.V2_DISCONNECTED_CHECKS
      and f"No sentence over {checks._V2_SENTENCE_MAX_WORDS} words (soft)" in checks.V2_DISCONNECTED_CHECKS
      and f"Readability Flesch >= {checks._V2_FLESCH_MIN} (soft)" in checks.V2_DISCONNECTED_CHECKS)

d = good_draft()
default = run_v2(TITLE, d)
default_names = [r.name for r in default]
check("disconnected: by default none of the five names is in the results",
      not [n for n in default_names if n in DISC_NAMES],
      str([n for n in default_names if n in DISC_NAMES]))
check("disconnected: the active-voice soft check still runs by default",
      any("Active voice" in n for n in default_names))
check("disconnected: hard checks still run by default",
      any(n.startswith("Draft structure matches") for n in default_names))
check("disconnected: other soft checks still run by default (length band, tertiary terms)",
      any(n == "Length is 1,200-1,500 words (soft)" for n in default_names))

flagged = run_v2(TITLE, d, include_disconnected=True)
flagged_names = [r.name for r in flagged]
check("disconnected: with the flag all five soft checks (that apply to the draft) are present",
      not [n for n in DISC_NAMES if n not in flagged_names],
      str([n for n in DISC_NAMES if n not in flagged_names]))
check("disconnected: adding the flag loses nothing else (default == flagged minus the five)",
      default_names == [n for n in flagged_names if n not in DISC_NAMES],
      str([n for n in flagged_names if n not in DISC_NAMES and n not in default_names]))

# ------------------------------------------------------------------ summary

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all v2 check tests passed")
