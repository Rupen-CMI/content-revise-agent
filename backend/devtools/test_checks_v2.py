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
import content_rules  # noqa: E402
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


def trend_block(n: int) -> list[str]:
    """One trend: a sub-heading entry, then one paragraph of about 100 words (the content team's spec) that carries a year."""
    return [f"### Trend {n} Is Raising Demand For Widget Services",
            pad(110, [f"In 2026 a named study reported change {'ABC'[n - 1]} in widget demand."])]


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
            *[p for n in (1, 2, 3) for p in trend_block(n)],
        ]},
        {"heading": plan_sec["segmentation"]["heading"], "sop": ["segmentation"], "paragraphs": [
            pad(50, [f"How the {MARKET} is divided: a segmentation into Browser Extension and Cloud Based types."]),
            pad(50, ["Data Aggregation and Market Research applications follow the same split in that field."]),
        ]},
        {"heading": plan_sec["subsegment"]["heading"], "sop": ["subsegment"], "paragraphs": [
            "### Cloud Based Tools Lead On Easy Deployment",
            pad(95, [f"Cloud Based tools form one part of the {MARKET} worth a closer look."]),
            "### Market Research Grows With Research Demand",
            pad(95, ["Market Research services form the other part worth a closer look."]),
        ]},
        {"heading": plan_sec["country_us"]["heading"], "sop": ["country_us"], "paragraphs": [
            pad(70, [f"The U.S. leads the {MARKET}, and cloud based widget services by region is where that shows.",
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
    if r.name == "No point is made twice across sections":
        continue    # the builders pad every section with the same filler sentence; the check has its own tests
    if r.name == checks.V2_US_EVIDENCE_CHECK_NAME:
        continue    # this plan carries one U.S. fact, so the thin-evidence warning is expected; tested below
    check(f"good: {r.name}", r.passed, r.detail)
check("good: length lands in the band", by_name(good, "Length").passed, by_name(good, "Length").detail)

# ------------------------------------------------------------------ the U.S. evidence warning
thin = by_name(good, "thin U.S. evidence")
check("us evidence: one U.S. fact is thin: the check warns and says the section is written from the report's own facts",
      not thin.passed and "report's own facts" in thin.detail, thin.detail)
enough_inp = test_planning.us_inputs(2)
enough_raw = test_planning.good_plan()
enough_raw["sections"][1]["facts"] = ["fs.drivers.0", "W1"]
enough_raw["sections"][4]["facts"] = []
enough_plan = planning.validate_plan(enough_raw, enough_inp, test_planning.LOG)
enough = run_v2(TITLE, good_draft(), plan=enough_plan, inp=enough_inp)
check("us evidence: with two U.S. facts the check passes", by_name(enough, "thin U.S. evidence").passed)
none_inp = test_planning.us_inputs(0, with_report_facts=False)
none_plan = planning.validate_plan(enough_raw, none_inp, test_planning.LOG)
none = run_v2(TITLE, good_draft(), plan=none_plan, inp=none_inp)
check("us evidence: with no U.S. fact and no report fact it still warns, and never calls the section an editor note",
      not by_name(none, "thin U.S. evidence").passed and "editor note" not in by_name(none, "thin U.S. evidence").detail,
      by_name(none, "thin U.S. evidence").detail)

# ------------------------------------------------------------------ one piece of news evidence is cited once
EVIDENCE_CHECK = "Each piece of news evidence is cited in one section and in one trend only"
check("evidence reuse: a draft that cites each news item once passes", by_name(good, "cited in one section").passed)
d = good_draft()
trends_sec = next(s for s in d if s["sop"] == ["trends"])
trends_sec["paragraphs"][2] = "Acme launched a new product. " + trends_sec["paragraphs"][2]
players_sec = next(s for s in d if s["sop"] == ["key_players"])
players_sec["paragraphs"][1] = "Acme launched a new product. " + players_sec["paragraphs"][1]
res = run_v2(TITLE, d)
check("evidence reuse: the same news item cited in a trend and in the key-players section fails the check and names it",
      not by_name(res, "cited in one section").passed and "W1 is cited in trend 1 and again in key_players" in by_name(res, "cited in one section").detail,
      by_name(res, "cited in one section").detail)
d = good_draft()
trends_sec = next(s for s in d if s["sop"] == ["trends"])
trends_sec["paragraphs"][2] = "Acme launched a new product. " + trends_sec["paragraphs"][2]
trends_sec["paragraphs"][4] = "Acme launched a new product. " + trends_sec["paragraphs"][4]
res = run_v2(TITLE, d)
check("evidence reuse: the same news item cited in two trend blocks fails the check",
      not by_name(res, "cited in one section").passed and "trend 1 and again in trend 2" in by_name(res, "cited in one section").detail,
      by_name(res, "cited in one section").detail)

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

check("keywords: the secondary-keyword checks are no longer part of the SOP checks",
      not any(r.name.startswith(("Secondary keywords", "No paragraph carries two")) for r in run_v2(TITLE, good_draft())))

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

# ------------------------------------------------------------------ no outside source is named (CMI is the only one)

NEWS = {"id": "W3", "kind": "evidence", "tags": ["context"], "text": "Regional sales hit USD 4.5 billion in 2025.",
        "detail": "", "source": "Field Notes"}
inp2 = test_planning.make_inputs()
inp2.items["W3"] = NEWS


def source_check(inp, text, faq=GOOD_FAQ):
    d = good_draft()
    d[2]["paragraphs"][1] = pad(50, [text]) + " " + filler(80)
    run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp, faq=faq, sponsor_brand_terms=[],
                               sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
    return by_name(run, "No outside source")


r = source_check(inp2, "Regional sales hit USD 4.5 billion in 2025, per Field Notes.")
check("source: a trailing 'per <source>' fails and names the source", not r.passed and "Field Notes" in r.detail, r.detail)
r = source_check(inp2, "According to Field Notes, regional sales hit USD 4.5 billion in 2025.")
check("source: a leading 'According to <source>' fails", not r.passed, r.detail)
r = source_check(inp2, "Regional sales hit USD 4.5 billion in 2025, the report at Field Notes finds.")
check("source: a listed source's own name written bare fails too", not r.passed, r.detail)
r = source_check(inp2, "Regional sales hit USD 4.5 billion in 2025.")
check("source: the same figure stated plainly passes", r.passed, r.detail)
r = source_check(inp2, "Regional sales hit USD 4.5 billion in 2025, a Grand View Research estimate.")
check("source: a research-firm-shaped name fails with no fact naming it", not r.passed, r.detail)
r = source_check(inp2, "A survey by Forrester found that 40% of retail firms adopt the tools.")
check("source: 'a survey by <name>' fails", not r.passed and "Forrester" in r.detail, r.detail)
r = source_check(inp2, "Gartner estimates that demand rises, and the segment grows.")
check("source: a subject-verb citation of an outside name fails", not r.passed and "Gartner" in r.detail, r.detail)
r = source_check(inp2, "CMI says regional demand rises as buyers consolidate vendors.")
check("source: CMI itself may be named", r.passed, r.detail)
r = source_check(inp2, "Coherent Market Insights estimates that regional demand rises as buyers consolidate vendors.")
check("source: Coherent Market Insights is not a research-firm shape that trips it", r.passed, r.detail)
r = source_check(inp2, "Researchers found that buyers consolidate vendors as demand rises.")
check("source: a generic 'Researchers found' names no source", r.passed, r.detail)

# a company that is the ACTOR of its own evidence item is never a cited source; only attribution contexts count
inp_actor = test_planning.make_inputs()
inp_actor.items["W8"] = {"id": "W8", "kind": "evidence", "tags": ["context"],
                         "text": "Adobe forecast that its own traffic data would show holiday retail visits up 7 percent.",
                         "detail": "", "source": "Adobe Newsroom, 2026-11", "historical": False, "sections": {"trends"}}
r = source_check(inp_actor, "Adobe forecast that its own traffic data would show holiday retail visits up 7% this season.")
check("source: the actor of its own evidence item (source 'Adobe Newsroom', actor Adobe) does not trip it", r.passed, r.detail)
r = source_check(inp_actor, "Holiday retail visits rise 7% this season, per Adobe Newsroom.")
check("source: the publication name 'Adobe Newsroom' in an attribution still fails", not r.passed, r.detail)
r = source_check(inp_actor, "According to Adobe, holiday retail visits rise 7% this season.")
check("source: 'according to <the actor>' on the actor's own figure passes", r.passed, r.detail)

# a listed key player that merely ends in Analytics / Insights is a product, not a research firm
inp_players = test_planning.make_inputs()
inp_players.items["fs.players"] = {"id": "fs.players", "kind": "players", "tags": [], "source": "CMI report", "historical": False,
                                   "text": "Key players include Google Analytics, Sprinklr Insights and Brand Watchers.",
                                   "detail": "", "sections": {"key_players"}}
r = source_check(inp_players, "Google Analytics and Sprinklr Insights compete for the same marketing budgets.")
check("source: a listed player named like a research firm (Sprinklr Insights) is not a cited source", r.passed, r.detail)
r = source_check(test_planning.make_inputs(), "Sprinklr Insights compete for the same marketing budgets.")
check("source: the same name with no fact sheet entry for it is a research-firm shape", not r.passed, r.detail)

# a domain-style source
inp_dom = test_planning.make_inputs()
inp_dom.items["W8"] = {"id": "W8", "kind": "evidence", "tags": ["context"],
                       "text": "Tool adoption reaches 6.6 percent of tracked firms.", "detail": "",
                       "source": "zz8020.com, 2026-01", "historical": False, "sections": {"report_context"}}
r = source_check(inp_dom, "Tool adoption reaches 6.6% of tracked firms, the count at zz8020.com finds.")
check("source: a domain-style source named in a sentence fails", not r.passed and "zz8020.com" in r.detail, r.detail)
r = source_check(inp_dom, "Tool adoption reaches 6.6% of tracked firms this year.")
check("source: the plain figure passes", r.passed, r.detail)

# the FAQ is held to the same rule
r = source_check(inp2, "Regional sales hit USD 4.5 billion in 2025.",
                 faq=[{**GOOD_FAQ[0], "answer": GOOD_FAQ[0]["answer"] + " This is per Field Notes."}] + list(GOOD_FAQ[1:]))
check("source: an FAQ answer that names a source fails", not r.passed and "FAQ" in r.detail, r.detail)

# crediting CMI for an outside figure
d = good_draft()
d[2]["paragraphs"][0] = pad(60, ["CMI puts regional sales at USD 4.5 billion in 2025.",
                                 f"The market size of the {MARKET} stands at USD 1.2 billion."])
d[2]["paragraphs"][1] = filler(140)
run = checks.run_checks_v2(TITLE, d, plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("credit: crediting CMI for an outside figure fails",
      not by_name(run, "never credited to CMI").passed, by_name(run, "never credited to CMI").detail)
run = checks.run_checks_v2(TITLE, good_draft(), plan=PLAN, inp=inp2, faq=GOOD_FAQ, sponsor_brand_terms=[],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
check("credit: the good draft credits nothing outside to CMI", by_name(run, "never credited to CMI").passed)

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

check("trends: the good draft's three sub-headed trends of one paragraph each pass",
      by_name(run_v2(TITLE, good_draft()), "Trends: three sub-headed").passed)

d = good_draft()
d[3]["paragraphs"].pop()
run = run_v2(TITLE, d)
check("trends: a trend with no paragraph fails", not by_name(run, "Trends: three sub-headed").passed,
      by_name(run, "Trends: three sub-headed").detail)

d = good_draft()
d[3]["paragraphs"] = [p for p in d[3]["paragraphs"] if p != "### Trend 3 Is Raising Demand For Widget Services"]
run = run_v2(TITLE, d)
check("trends: a missing sub-heading fails (two trends, one of two paragraphs)", not by_name(run, "Trends: three sub-headed").passed,
      by_name(run, "Trends: three sub-headed").detail)

d = good_draft()
d[3]["paragraphs"][2] = filler(110)
run = run_v2(TITLE, d)
check("trends: a trend whose first paragraph has no figure or year passes (that rule left the SOP on 2026-11-18)",
      by_name(run, "Trends: three sub-headed").passed, by_name(run, "Trends: three sub-headed").detail)

SUB_SHAPE = "Sub-segments: two, each under its own heading"
SPEC_LEN = "Trend and sub-segment paragraphs about 100 words"
check("sub-segments: the good draft's two sub-headed sub-segments of one paragraph each pass",
      by_name(run_v2(TITLE, good_draft()), SUB_SHAPE).passed, by_name(run_v2(TITLE, good_draft()), SUB_SHAPE).detail)
d = good_draft()
d[5]["paragraphs"] = [p for p in d[5]["paragraphs"] if not p.startswith("### ")]
check("sub-segments: paragraphs without their own headings fail", not by_name(run_v2(TITLE, d), SUB_SHAPE).passed)
check("spec length: ~100-word trend and sub-segment paragraphs pass", by_name(run_v2(TITLE, good_draft()), SPEC_LEN).passed,
      by_name(run_v2(TITLE, good_draft()), SPEC_LEN).detail)
d = good_draft()
d[3]["paragraphs"][2] = filler(40)
check("spec length: a 40-word trend paragraph is noted (soft)", not by_name(run_v2(TITLE, d), SPEC_LEN).passed
      and "40 words" in by_name(run_v2(TITLE, d), SPEC_LEN).detail, by_name(run_v2(TITLE, d), SPEC_LEN).detail)

check("trends: the sub-headings are not counted as prose (word counts and sentence checks read the sections without them)",
      all(not p.startswith("### ") for s in checks.subheads.strip_subheads(good_draft()) for p in s["paragraphs"]))

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

# The pointing-word check is disconnected from the default results (like the soft checks below), so
# this section asks for the disconnected checks too.
def run_v2_all(title, sections, **kw):
    return run_v2(title, sections, include_disconnected=True, **kw)


VAGUE_NAME = "Every sentence names what it refers to"

d = good_draft()
d[2]["paragraphs"][0] += " This shows how fast the market grows."
run = run_v2_all(TITLE, d)
r = by_name(run, VAGUE_NAME)
check("vague: 'This shows how fast the market grows.' fails", not r.passed, r.detail)
check("vague: the detail names the pointing-word pattern", "opens with a pointing word" in r.detail, r.detail)

d = good_draft()
d[4]["paragraphs"][1] = "That is why buyers pay less."
run = run_v2_all(TITLE, d)
check("vague: 'That is why buyers pay less.' fails", not by_name(run, VAGUE_NAME).passed,
      by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[5]["paragraphs"][1] = "It means buyers pay less."
run = run_v2_all(TITLE, d)
check("vague: 'It means buyers pay less.' fails (bare pronoun)", not by_name(run, VAGUE_NAME).passed,
      by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[6]["paragraphs"][1] = "It is the largest region today."
run = run_v2_all(TITLE, d)
check("vague: 'It is the largest region today.' states its subject and passes",
      by_name(run, VAGUE_NAME).passed, by_name(run, VAGUE_NAME).detail)

d = good_draft()
d[4]["paragraphs"][1] = d[4]["paragraphs"][1] + "\n- This keeps costs low."
run = run_v2_all(TITLE, d)
r = by_name(run, VAGUE_NAME)
check("vague: a bullet line opening with This fails", not r.passed, r.detail)

run = run_v2_all(TITLE, good_draft(),
             sponsor_text="This approach keeps the stack simple for every team that uses it.")
check("vague: the retained sponsor paragraph is exempt from the pointing-word check",
      by_name(run, VAGUE_NAME).passed, by_name(run, VAGUE_NAME).detail)

d = good_draft()
faq2 = copy.deepcopy(GOOD_FAQ)
faq2[0]["answer"] = "That is because the market grows."
run = run_v2_all(TITLE, d, faq=faq2)
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
d[2]["paragraphs"] += [d[2]["paragraphs"][-1]] * 2    # the trends now carry nine filler paragraphs: keep the passive share well above 1 in 6
run = run_v2(TITLE, d)
r = by_name(run, VOICE_NAME)
check("voice: a draft with mostly passive sentences fails (soft)", not r.passed, r.detail)
check("voice: the note carries the offending sentences", "The market is driven" in r.detail, r.detail)

# ------------------------------------------------------------------ long-tail noun pile (soft)

PILE_NAME = "No long-tail phrase is pasted as a noun pile"

# a long phrase of nearly all content words, assigned to the subsegment section (its fact sits there)
inp4 = test_planning.make_inputs()
inp4.longtails = inp4.longtails + [
    {"id": "L4", "keyword": "custom cloud based widget services marketplace pricing", "fact": "fs.segments.0.dominant", "why": "x"}]
inp4.items["fs.segments.0.dominant"] = test_planning.item("fs.segments.0.dominant", "segments", "Dominant category in By Type: Cloud Based (the report states it leads this split)", {"subsegment"})
raw4 = test_planning.good_plan()
raw4["sections"][3]["facts"] = ["W2", "fs.segments.0.dominant"]   # L4 rests on the leader fact (a split's category list is stated in the segmentation section only)
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
    {"id": "L5", "keyword": "zorb widget services pricing", "fact": "fs.segments.0.dominant", "why": "x"}]
inp5.items["fs.segments.0.dominant"] = test_planning.item("fs.segments.0.dominant", "segments", "Dominant category in By Type: Cloud Based (the report states it leads this split)", {"subsegment"})
raw5 = test_planning.good_plan()
raw5["sections"][3]["facts"] = ["W2", "fs.segments.0.dominant"]   # L5 rests on the leader fact (a split's category list is stated in the segmentation section only)
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
    checks.V2_VAGUE_CHECK_NAME,
    "Uses 2-3 long-tail keywords (soft)",
    "Every assigned long-tail is used in its own section (soft)",
    "No long-tail phrase is pasted as a noun pile (soft)",
    "No sentence over 30 words (soft)",
    "Readability Flesch >= 50 (soft)",
}

check("disconnected: the set holds exactly six names", len(checks.V2_DISCONNECTED_CHECKS) == 6,
      str(checks.V2_DISCONNECTED_CHECKS))
check("disconnected: the set holds exactly the six disconnected names",
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
      any(n == "Length is 1,300-1,700 words (soft)" for n in default_names))

flagged = run_v2(TITLE, d, include_disconnected=True)
flagged_names = [r.name for r in flagged]
check("disconnected: with the flag all five soft checks (that apply to the draft) are present",
      not [n for n in DISC_NAMES if n not in flagged_names],
      str([n for n in DISC_NAMES if n not in flagged_names]))
check("disconnected: adding the flag loses nothing else (default == flagged minus the five)",
      default_names == [n for n in flagged_names if n not in DISC_NAMES],
      str([n for n in flagged_names if n not in DISC_NAMES and n not in default_names]))

# ------------------------------------------------------------------ outside report: no link, a loud warning

INP_OUT = test_planning.make_inputs()
INP_OUT.link_report, INP_OUT.report_mode = False, "external"
INP_OUT.outside_audit = [{"field": "size", "original": "USD 500.0 million", "shown": "USD 521.0 million", "source": "rival.example", "url": "https://rival.example/r"}]
nolink_draft = good_draft()
nolink_draft[0]["paragraphs"][0] = pad(26, [f"Start at the source: {MARKET}, where this guide gathers its findings."])
out = checks.run_checks_v2(TITLE, nolink_draft, PLAN, INP_OUT, faq=GOOD_FAQ, sponsor_brand_terms=["Northway Specialists"],
                           sponsor_text=SPONSOR_TEXT, report_text=REPORT_TEXT, report_name=MARKET)
out_names = [r.name for r in out]
check("outside: no CMI link check runs when no report is linked", not any("CMI link" in n or "CMI report linked" in n for n in out_names), str(out_names))
check("outside: the SOP checks carry no competitor-report warning (the Step 2 box already shows it)", not any(r.name.startswith("WARNING") for r in out))

# ------------------------------------------------------------------ the report's main figures are all in the article
fig_inp = test_planning.make_inputs()
fig_inp.items["fs.forecast"] = test_planning.item("fs.forecast", "forecast", "Market forecast: USD 3.4 billion (2032)", {"report_context"})
fig_inp.items["fs.segments.0.i1"] = test_planning.item("fs.segments.0.i1", "segment_item", "Cloud Based (By Type): 48.2%", {"subsegment", "segmentation"})
fig_inp.segments = [
    {"id": "fs.segments.0", "type": "By Type", "items": ["Browser Extension", "Cloud Based", "Others"], "dominant": {"name": "Cloud Based", "basis": "stated"}},
    {"id": "fs.segments.1", "type": "By Industry", "items": ["Engineering & Manufacturing", "Media"]},
]


def fig_sections(ctx: str, seg: str, sub: str = "") -> list[dict]:
    return [{"heading": "h", "sop": ["report_context"], "paragraphs": [ctx]},
            {"heading": "h", "sop": ["segmentation"], "paragraphs": [seg]},
            {"heading": "h", "sop": ["subsegment"], "paragraphs": [sub or "Nothing else."]}]


all_in = fig_sections("The market was worth USD 1.2 billion in 2025, is forecast at USD 3.4 billion by 2032 and grows at 13.1% a year.",
                      "The market splits by type into browser extension, cloud based and other tools, and by industry into engineering and "
                      "manufacturing and media.",
                      "Cloud based tools hold 48.2% of revenue and lead the split, while engineering and manufacturing "
                      "is the other part analysed.")
check("report figures: size, forecast, growth rate, categories and the share all stated -> no problem",
      checks.v2_report_figure_problems(all_in, fig_inp) == [], str(checks.v2_report_figure_problems(all_in, fig_inp)))
no_forecast = fig_sections("The market was worth USD 1.2 billion in 2025 and grows at 13.1% a year.",
                           all_in[1]["paragraphs"][0], all_in[2]["paragraphs"][0])
probs = checks.v2_report_figure_problems(no_forecast, fig_inp)
check("report figures: a forecast left out of the article is named", any("3.4" in p and "forecast" in p.lower() for p in probs), str(probs))
no_share = fig_sections(all_in[0]["paragraphs"][0], all_in[1]["paragraphs"][0], "Cloud based tools lead the split.")
probs = checks.v2_report_figure_problems(no_share, fig_inp)
check("report figures: a segment share left out of the article is named", any("48.2" in p for p in probs), str(probs))
no_cat = fig_sections(all_in[0]["paragraphs"][0], "The market splits by type into browser extension and cloud based tools.",
                      "Cloud based tools hold 48.2% of revenue and lead the split.")
probs = checks.v2_report_figure_problems(no_cat, fig_inp)
check("report figures: a split's category left out is named, 'Others' is not required",
      any("Engineering & Manufacturing" in p for p in probs) and not any("Others" in p for p in probs), str(probs))
year_only = fig_sections("The market is forecast at USD 3.4 billion and was worth USD 1.2 billion, growing at 13.1% a year.",
                         all_in[1]["paragraphs"][0], all_in[2]["paragraphs"][0])
check("report figures: a year is not a figure (the year of a figure may be left out)",
      checks.v2_report_figure_problems(year_only, fig_inp) == [], str(checks.v2_report_figure_problems(year_only, fig_inp)))
check("report figures: the check reports as a failed row in the full check list",
      any(not r.passed and r.name.startswith("Report figures stated") for r in run_v2("T", no_forecast + [], faq=[])),
      "")

fig_inp.segments = fig_inp.segments + [{"id": "fs.segments.2", "type": "By Application", "items": ["Media", "Others (Banking and Finance, Non-profit and Government Organizations)"]}]
check("report figures: a parenthesis inside a category, and 'Others', are not required in the article",
      not any("Others" in p or "Banking" in p for p in checks.v2_report_figure_problems(all_in, fig_inp)),
      str(checks.v2_report_figure_problems(all_in, fig_inp)))

# the analysed share belongs to the sub-segment section, and a split the article does not analyse needs no figure at all
fig_inp.items["fs.segments.2.i0"] = test_planning.item("fs.segments.2.i0", "segment_item", "Media (By Application): 31.0%", {"subsegment"})
check("report figures: a non-analysed split's share is not required anywhere",
      not any("31.0" in p for p in checks.v2_report_figure_problems(all_in, fig_inp)),
      str(checks.v2_report_figure_problems(all_in, fig_inp)))
share_elsewhere = fig_sections(all_in[0]["paragraphs"][0],
                               all_in[1]["paragraphs"][0] + " Cloud based tools hold 48.2% of revenue.",
                               "Cloud based tools lead the split, while engineering and manufacturing is the other part analysed.")
probs = checks.v2_report_figure_problems(share_elsewhere, fig_inp)
check("report figures: the analysed sub-segment's share is required in its own section, not merely anywhere",
      any("48.2" in p and "sub-segment section" in p for p in probs), str(probs))

# the leading and fastest-growing regions belong to the market-size section
fig_inp.items["fs.regions.leader"] = test_planning.item(
    "fs.regions.leader", "regions", "Leading region: North America (42.6% share in 2026)", {"report_context"})
probs = checks.v2_report_figure_problems(all_in, fig_inp)
check("report figures: a region left out of the market-size section is named",
      any("North America" in p and "market-size section" in p for p in probs), str(probs))
with_region = fig_sections(all_in[0]["paragraphs"][0] + " North America leads with 42.6% of revenue.",
                           all_in[1]["paragraphs"][0], all_in[2]["paragraphs"][0])
check("report figures: the leading region stated in the market-size section passes",
      not any("North America" in p for p in checks.v2_report_figure_problems(with_region, fig_inp)),
      str(checks.v2_report_figure_problems(with_region, fig_inp)))

# ------------------------------------------------------------------ the market's name is in the first 100 words (both modes)
_lead = " ".join(["Professionals who adjust single pages so that engines can read them need background first."] * 8)   # 88 words, no name
_named = lambda *paras: [{"heading": "", "sop": [], "paragraphs": list(paras)}]
check("name offset: a name in the first sentence is at word 0..",
      checks.market_name_offset(_named(f"The {MARKET} grows. " + _lead), "", MARKET) == 1)
check("name offset: a name after a long lead-in is beyond 100 words (the SEO draft's failure)",
      (checks.market_name_offset(_named(_lead, _lead, f"The {MARKET} grows."), "", MARKET) or 0) > checks.MARKET_NAME_WORDS)
check("name offset: a name that never appears gives None", checks.market_name_offset(_named(_lead), "", MARKET) is None)
check("name offset: a CMI link counts as the name at its own position",
      checks.market_name_offset(_named(f"Teams study the [[CMI_LINK:{MARKET}]] closely."), "", MARKET) == 3)
check("name offset: the retained paragraph counts at its real length when it sits before the name",
      checks.market_name_offset(_named(prompts.SPONSOR_TOKEN, f"The {MARKET} grows."), "one two three four five", MARKET) == 6)
check("name offset: a heading is not read", checks.market_name_offset(
    [{"heading": MARKET, "sop": ["trends"], "paragraphs": ["Nothing named here."]}], "", MARKET) is None)

# no report is cited when an outside report stood in (CMI's report is not the source of those figures)
for phrase, expect in [("CMI's report values the market at USD 80 billion.", True), ("The report states growth is steady.", True),
                       ("According to the research, demand rises.", True), ("Demand rises as firms adopt new tools.", False),
                       ("Gartner reports that adoption rose in 2025.", False)]:
    hit = bool(checks._REPORT_CITED_RE.search(phrase))
    check(f"report cited: {phrase!r} -> {expect}", hit == expect)


# ------------------------------------------------------------------ verbatim overlap: taxonomy masking

_TAXONOMY = ["Aptitude Tests", "Personality Tests", "Skill Tests", "Behavioral Assessments",
             "Direct Assessment", "Analog Assessment"]
_REPORT_LINE = ("The report divides the market by assessment type: " + ", ".join(_TAXONOMY)
                + " and Others; the split is the report's own.")
_LIST_PARAGRAPH = ("Assessment Type spans " + ", ".join(_TAXONOMY)
                   + " and Others, and Aptitude Tests lead that split in the article.")
check("overlap: the report's own taxonomy list is required wording, not a copied line",
      checks._verbatim_overlap([_LIST_PARAGRAPH], [("CMI report", _REPORT_LINE)],
                               ignore_terms=_TAXONOMY)[0] == 0.0)
check("overlap: without the taxonomy masked the same list would count as copied",
      checks._verbatim_overlap([_LIST_PARAGRAPH], [("CMI report", _REPORT_LINE)])[0] > 0.0)

_PROSE_COPY = ("Advancements in technology such as online platforms, remote assessment tools and data analytics have "
               "transformed the global assessment services market.")
check("overlap: copied prose still counts with a taxonomy supplied",
      checks._verbatim_overlap([_PROSE_COPY], [("CMI report", _REPORT_LINE + " " + _PROSE_COPY)],
                               ignore_terms=_TAXONOMY)[0] > 5.0)
check("copied_runs: reports the copied run and nothing shorter",
      checks.copied_runs(_PROSE_COPY, [("CMI report", _REPORT_LINE + " " + _PROSE_COPY)],
                         ignore_terms=_TAXONOMY) == [" ".join(checks.re.findall(r"\w+", _PROSE_COPY.lower()))])


# ------------------------------------------------------------------ repeated point: the sub-segment's own category name

_SUB_SECTIONS = [
    {"heading": "Segments", "sop": ["segmentation"],
     "paragraphs": ["By mode, the report distinguishes online delivery from offline delivery, and online leads."]},
    {"heading": "Sub-segments", "sop": ["subsegment"],
     "paragraphs": ["Online delivery leads the mode split of Zzz Widget Services Market, at 55.9% in 2026, and the "
                    "share reflects how clients prefer to reach providers."]},
]
check("repetition: a sub-segment naming its own category and split is not a repeated point",
      content_rules.repeated_point_problems(
          _SUB_SECTIONS, market_name="Zzz Widget Services Market", own_terms=["Online", "BFSI"]) == [])
check("repetition: without the category exemption the same pair is flagged",
      content_rules.repeated_point_problems(
          _SUB_SECTIONS, market_name="Zzz Widget Services Market") != [])

# a genuine repeat that happens to name the category: the category must not count as overlap, but must not shrink the
# sentence below the min-stems gate either
_CATEGORY_REPEAT = [
    {"heading": "Segments", "sop": ["segmentation"],
     "paragraphs": ["Online delivery expands through smartphone penetration and doorstep convenience."]},
    {"heading": "Conclusion", "sop": ["conclusion"],
     "paragraphs": ["Online delivery grows on smartphone penetration and doorstep convenience."]},
]
check("repetition: a real repeat that names the category is still flagged with own terms",
      len(content_rules.repeated_point_problems(_CATEGORY_REPEAT, own_terms=["Online Delivery"])) == 1,
      str(content_rules.repeated_point_problems(_CATEGORY_REPEAT, own_terms=["Online Delivery"])))
check("repetition: the same pair is flagged without own terms",
      len(content_rules.repeated_point_problems(_CATEGORY_REPEAT)) == 1)


# ------------------------------------------------------------------ summary

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all v2 check tests passed")
