"""Offline test for the planning stage's enforcement layer (no network, no real LLM calls).

Run from backend/: python devtools/test_planning.py

Covers planning.validate_plan on hand-built bad plans (too many or too few question headings, stuffed or unknown
keywords, unknown / misplaced / duplicated facts, a bad title, bad snippets, bad sub-segments), the title checks,
planning.compute_budgets (including a very long retained paragraph) and the prompt (no sample titles or headings).
"""
import copy
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_planning")
MARKET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def item(fid, kind, text, sections, tags=(), source="CMI report", historical=False):
    return {"id": fid, "kind": kind, "tags": list(tags), "text": text, "detail": "", "source": source,
            "historical": historical, "sections": set(sections)}


def make_inputs() -> planning.PlanInputs:
    items = {i["id"]: i for i in [
        item("fs.size", "size", "Market size: USD 1.2 billion (2025)", {"report_context"}),
        item("fs.cagr", "cagr", "Growth rate: 13.1%", {"report_context"}),
        item("fs.drivers.0", "drivers", "Digital marketing adoption drives demand.", {"trends", "report_context"}),
        item("fs.restraints.0", "restraints", "Piracy hampers growth.", {"trends"}),
        item("fs.segments.0", "segments", "Segmentation type: Browser Extension, Cloud Based", {"segmentation", "subsegment"}),
        item("fs.segments.1", "segments", "Segmentation application: Data Aggregation, Market Research", {"segmentation", "subsegment"}),
        item("fs.regions.leader", "regions", "Leading region: North America", {"country_us"}, historical=True),
        item("fs.us.0", "us", "A review of widget exports opened in the U.S.", {"country_us"}),
        item("fs.players", "players", "Key players: Acme, Beta", {"key_players"}),
        item("fs.opportunities.0", "opportunities", "Robo advisory opens room to grow.", {"conclusion", "trends"}),
        item("W1", "evidence", "Acme launched a new product.", {"trends", "key_players"}, tags=["trends", "players"], source="News Site"),
        item("W2", "evidence", "Cloud pricing is metered by credit.", {"report_context", "segmentation", "subsegment", "conclusion"}, tags=["context"], source="Other Site"),
    ]}
    questions = [
        {"id": "Q1", "intent": "size", "section": "report_context", "question": "How big is the Zzz Widget Services Market?", "answerable": True},
        {"id": "Q2", "intent": "drivers", "section": "trends", "question": "What drives growth in the Zzz Widget Services Market?", "answerable": True},
        {"id": "Q3", "intent": "segments", "section": "segmentation", "question": "How is the Zzz Widget Services Market divided?", "answerable": True},
        {"id": "Q4", "intent": "region", "section": "country_us", "question": "Which region leads the Zzz Widget Services Market?", "answerable": True},
        {"id": "Q5", "intent": "players", "section": "key_players", "question": "Who are the top companies in the Zzz Widget Services Market?", "answerable": True},
        {"id": "Q6", "intent": "outlook", "section": "conclusion", "question": "What is the outlook for the Zzz Widget Services Market?", "answerable": True},
        {"id": "Q7", "intent": "challenges", "section": "trends", "question": "What challenges does the Zzz Widget Services Market face?", "answerable": False},
    ]
    return planning.PlanInputs(
        market_name=MARKET, intro_angle="A guide to widgets.", points=[{"id": f"P{i}", "text": f"Plain point {i}."} for i in range(1, 7)],
        questions=questions, items=items,
        segments=[{"id": "fs.segments.0", "type": "By Type", "items": ["Browser Extension", "Cloud Based"]},
                  {"id": "fs.segments.1", "type": "By Application", "items": ["Data Aggregation", "Market Research"]}],
        terms=["cloud based", "browser extension", "data aggregation"],
        longtails=[{"id": "L1", "keyword": "cloud based widget services by region", "fact": "fs.regions.leader", "why": "x"},
                   {"id": "L2", "keyword": "widget piracy risk", "fact": "fs.restraints.0", "why": "x"},
                   {"id": "L3", "keyword": "widget us review", "fact": "fs.us.0", "why": "x"}],
        secondary_pool=list(planning._SECONDARY_KEYWORD_SUFFIXES), sponsor_words=84, has_us_facts=True,
    )


def sec(sop, **kw):
    base = {"sop": sop, "heading": f"Plain heading for {sop}", "msq": None, "point": f"point {sop}", "facts": [],
            "secondary": None, "longtail": None, "terms": [], "snippet": None}
    base.update(kw)
    return base


GOOD_TITLE = "Why Digital Marketing Is Driving the Zzz Widget Services Market"


def good_plan() -> dict:
    return {
        "title": "What Is Driving the Zzz Widget Services Market Right Now", "title_basis": ["fs.drivers.0"],
        "intro": {"points": ["P1", "P2"]}, "transition": {"point": "Why widgets belong to a bigger market."},
        "sections": [
            sec("report_context", msq="Q1", facts=["fs.size", "fs.cagr"], secondary="size"),
            sec("trends", msq="Q2", facts=["fs.drivers.0", "fs.restraints.0", "W1"], secondary="trends", longtail="L2"),
            sec("segmentation", facts=["fs.segments.0", "fs.segments.1"], secondary="segmentation"),
            sec("subsegment", facts=["W2"], sub_segments=[{"split": "By Type", "name": "Cloud Based"}, {"split": "By Application", "name": "Market Research"}]),
            sec("country_us", msq="Q4", facts=["fs.regions.leader", "fs.us.0"], longtail="L1", secondary="share"),
            sec("key_players", facts=["fs.players"], longtail="L3"),
            sec("conclusion", facts=["fs.opportunities.0"], secondary="outlook"),
        ],
    }


def plan_of(raw, inp=None):
    return planning.validate_plan(raw, inp or make_inputs(), LOG)


def S(plan, sop):
    return next(s for s in plan["sections"] if s["sop"] == sop)


# ------------------------------------------------------------------ a good plan passes through unchanged in substance
p = plan_of(good_plan())
check("good: all seven sections in the fixed order", [s["sop"] for s in p["sections"]] == list(planning.ORDER))
check("good: three question headings, set to the exact question text",
      [s["msq"] for s in p["sections"] if s["msq"]] == ["Q1", "Q2", "Q4"] and S(p, "trends")["heading"].startswith("What drives growth"))
check("good: the FAQ is the answerable questions that are not headings, in order", p["faq"] == ["Q3", "Q5", "Q6"], str(p["faq"]))
check("good: the unanswerable question is nowhere", "Q7" not in p["faq"] and all(s["msq"] != "Q7" for s in p["sections"]))
check("good: the title survives", p["title"].startswith("What Is Driving"), p["title"])
check("good: no repairs were needed", p["repairs"] == [] or all("sub-segments" not in r for r in p["repairs"]), str(p["repairs"]))

# ------------------------------------------------------------------ headings
raw = good_plan()
for s in raw["sections"]:
    s["heading"] = "Is this a question?"
for s, qid in zip(raw["sections"], ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", None]):
    s["msq"] = qid
p = plan_of(raw)
check("headings: five or more question headings are cut to three", sum(1 for s in p["sections"] if s["msq"]) == 3, str([s["msq"] for s in p["sections"]]))
check("headings: no non-question section keeps a question mark",
      all(not s["heading"].endswith("?") for s in p["sections"] if not s["msq"]), str([s["heading"] for s in p["sections"]]))
check("headings: a demoted question moves to the FAQ", all(q in p["faq"] for q in ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6"] if q not in [s["msq"] for s in p["sections"]]))

raw = good_plan()
for s in raw["sections"]:
    s["msq"] = None
p = plan_of(raw)
check("headings: with none chosen, two are promoted", sum(1 for s in p["sections"] if s["msq"]) == 2, str([s["msq"] for s in p["sections"]]))

raw = good_plan()
raw["sections"][0]["msq"] = "Q2"      # Q2 can only head trends
raw["sections"][1]["msq"] = "Q7"      # unanswerable
raw["sections"][4]["msq"] = "Q99"     # unknown
p = plan_of(raw)
check("headings: a question in a section it cannot head, an unanswerable one and an unknown one are never kept",
      S(p, "report_context")["msq"] != "Q2" and all(s["msq"] not in ("Q7", "Q99") for s in p["sections"]))

raw = good_plan()
raw["sections"][2]["heading"] = "Segmentation"
raw["sections"][5]["heading"] = "What drives growth in the Zzz Widget Services Market?"
p = plan_of(raw)
check("headings: a bare label and a copied question text are replaced by plain headings",
      S(p, "segmentation")["heading"] != "Segmentation" and not S(p, "key_players")["heading"].endswith("?"))

raw = good_plan()
raw["sections"][2]["heading"] = raw["sections"][3]["heading"] = "Same heading"
p = plan_of(raw)
check("headings: duplicates are made unique", len({s["heading"].lower() for s in p["sections"]}) == 7)

# ------------------------------------------------------------------ facts
raw = good_plan()
raw["sections"][1]["facts"] = ["fs.drivers.0", "fs.nope", "fs.size", "fs.players", "W1"]
raw["sections"][5]["facts"] = ["fs.players", "W1"]
p = plan_of(raw)
check("facts: unknown ids, headline figures outside report_context and facts in the wrong section are dropped",
      S(p, "trends")["facts"] == ["fs.drivers.0", "W1"], str(S(p, "trends")["facts"]))
check("facts: a fact used twice stays in the first section only", "W1" not in S(p, "key_players")["facts"] and "fs.players" in S(p, "key_players")["facts"])

raw = good_plan()
raw["sections"][0]["facts"] = ["fs.size", "fs.cagr", "fs.drivers.0", "W2", "fs.opportunities.0"]
p = plan_of(raw)
check("facts: at most four per section", len(S(p, "report_context")["facts"]) <= 4, str(S(p, "report_context")["facts"]))

raw = good_plan()
raw["sections"][2]["facts"] = ["fs.segments.0", "fs.segments.1"]
raw["sections"][3]["facts"] = ["fs.segments.0", "W2"]
p = plan_of(raw)
check("facts: a segment split may serve both segmentation and the sub-segment analysis", "fs.segments.0" in S(p, "subsegment")["facts"])

raw = good_plan()
raw["sections"][5]["facts"] = []
raw["sections"][4]["facts"] = []
p = plan_of(raw)
check("lengths: a section without facts is short; a U.S. section without U.S. or regional facts is short too",
      S(p, "key_players")["length"] == "short" and S(p, "country_us")["length"] == "short")
raw = good_plan()
raw["sections"][4]["facts"] = ["fs.regions.leader"]
inp = make_inputs()
inp.has_us_facts = False
raw = good_plan()
check("lengths: key players with only the list of names is short; with a detail from the news it is normal",
      S(plan_of(raw), "key_players")["length"] == "short")
raw = good_plan()
raw["sections"][5]["facts"] = ["fs.players", "W1"]
raw["sections"][1]["facts"] = ["fs.drivers.0", "fs.restraints.0"]
check("lengths: ... normal once a news detail backs a company", S(plan_of(raw), "key_players")["length"] == "normal")
check("lengths: a U.S. section with only regional facts and no U.S. facts is qualitative", S(plan_of(raw, inp), "country_us")["length"] == "qualitative")

# ------------------------------------------------------------------ keywords
raw = good_plan()
for s, key in zip(raw["sections"], ["size", "forecast", "CAGR", "drivers", "trends", "growth", "share"]):
    s["secondary"] = key
p = plan_of(raw)
check("keywords: at most six secondary keywords in all", sum(1 for s in p["sections"] if s["secondary"]) <= 6)
raw = good_plan()
raw["sections"][0]["secondary"] = raw["sections"][1]["secondary"] = "size"
raw["sections"][2]["secondary"] = "made up"
p = plan_of(raw)
check("keywords: a repeated or unknown secondary keyword is dropped", S(p, "trends")["secondary"] != "size" and S(p, "segmentation")["secondary"] != "made up")
raw = good_plan()
for s in raw["sections"]:
    s["secondary"] = None
p = plan_of(raw)
check("keywords: with fewer than four, the pool's own sections fill the gap", sum(1 for s in p["sections"] if s["secondary"]) >= 4, str([s["secondary"] for s in p["sections"]]))

raw = good_plan()
for s, lid in zip(raw["sections"], ["L1", "L2", "L3", "L1", "L2", "L3", "L1"]):
    s["longtail"] = lid
p = plan_of(raw)
check("keywords: three long-tails at most, each once", 2 <= sum(1 for s in p["sections"] if s["longtail"]) <= 3 and
      len({s["longtail"] for s in p["sections"] if s["longtail"]}) == sum(1 for s in p["sections"] if s["longtail"]))
raw = good_plan()
for s in raw["sections"]:
    s["longtail"] = None
p = plan_of(raw)
check("keywords: with none chosen, long-tails are added where their own fact sits", sum(1 for s in p["sections"] if s["longtail"]) >= 2)
raw = good_plan()
raw["sections"][1]["terms"] = ["Cloud Based", "made up term", "browser extension", "data aggregation", "cloud based"]
p = plan_of(raw)
check("keywords: related terms are limited to the pool, unique and at most three a section",
      S(p, "trends")["terms"] == ["cloud based", "browser extension", "data aggregation"], str(S(p, "trends")["terms"]))

# ------------------------------------------------------------------ guest points
raw = good_plan()
for s, pid in zip(raw["sections"], ["P3", "P4", "P5", "P6", "P3", "P4", "P5"]):
    s["snippet"] = pid
p = plan_of(raw)
check("snippets: at most three, none in the conclusion, none repeating the opening",
      sum(1 for s in p["sections"] if s["snippet"]) <= 3 and S(p, "conclusion")["snippet"] is None)
raw = good_plan()
raw["sections"][1]["snippet"] = "P1"          # P1 already opens the article
p = plan_of(raw)
check("snippets: a guest point that already opens the article is not reused", S(p, "trends")["snippet"] is None)
raw = good_plan()
raw["intro"] = {"points": ["P9"]}
p = plan_of(raw)
check("intro: unknown guest points are dropped and the opening is topped up to two", len(p["intro"]["points"]) == 2)

# ------------------------------------------------------------------ sub-segments
raw = good_plan()
raw["sections"][3]["sub_segments"] = [{"split": "By Type", "name": "Browser Extension"}, {"split": "By Type", "name": "Cloud Based"}]
p = plan_of(raw)
splits = [x["split"] for x in S(p, "subsegment")["sub_segments"]]
check("sub-segments: two from the same split are replaced by two from different splits", len(set(splits)) == 2, str(S(p, "subsegment")["sub_segments"]))
raw = good_plan()
raw["sections"][3]["sub_segments"] = [{"split": "Segmentation type", "name": "cloud based"}, {"split": "segmentation application", "name": "Market Research"}]
check("sub-segments: split names as the fact list prints them ('Segmentation type') are accepted",
      [x["name"] for x in S(plan_of(raw), "subsegment")["sub_segments"]] == ["Cloud Based", "Market Research"])
raw = good_plan()
raw["sections"][3]["sub_segments"] = [{"split": "By Colour", "name": "Red"}]
check("sub-segments: invented splits fall back to the report's own", len(S(plan_of(raw), "subsegment")["sub_segments"]) == 2)

# ------------------------------------------------------------------ title
known = set(make_inputs().items)
def tp(t, basis=("fs.drivers.0",)):
    return planning.title_problems(t, MARKET, list(basis), known)[0]
check("title: a good title has no hard problems", tp(GOOD_TITLE) == [], str(tp(GOOD_TITLE)))
check("title: a label tail is rejected", bool(tp("Zzz Widget Services Market and Its Many Parts: Key Trends")))
check("title: a two-subject join is rejected", bool(tp("Python Scraping Stacks and the Zzz Widget Services Market Today")))
check("title: a title without the primary keyword is rejected", bool(tp("What Is Driving the Widget Industry Right Now This Year")))
check("title: a title that starts the keyword late is rejected",
      bool(tp("A very long introduction before we ever mention the Zzz Widget Services Market at all")))
check("title: no backing fact is rejected", bool(tp(GOOD_TITLE, ())))
check("title: far too short is rejected", bool(tp("Zzz Widget Services Market")))
check("title: a figure or year in the title is rejected", bool(tp("Zzz Widget Services Market Nears a Billion Dollars by 2034")))
raw = good_plan()
raw["title"] = "Python Scraping Stacks and the Zzz Widget Services Market: Key Trends"
p = plan_of(raw)
check("title: a bad planned title is replaced by a well-formed fallback",
      p["title"] != raw["title"] and MARKET in p["title"] and not planning._LABEL_TAIL_RE.search(p["title"]), p["title"])

# ------------------------------------------------------------------ odd input
p = plan_of(None)
check("robustness: a missing plan still yields all seven sections with plain headings and a title",
      [s["sop"] for s in p["sections"]] == list(planning.ORDER) and p["title"] and all(s["heading"] for s in p["sections"]))
check("robustness: a missing plan still promotes two question headings", sum(1 for s in p["sections"] if s["msq"]) == 2)
p = plan_of({"sections": "nope", "title": 5})
check("robustness: nonsense input is survived", len(p["sections"]) == 7)

# ------------------------------------------------------------------ budgets
b = planning.compute_budgets(84)
check("budgets: the intro share is under its cap, the transition inside its band",
      b["intro_total"] / b["target"] <= planning.INTRO_CAP and 0.05 <= b["transition"] / b["target"] <= 0.10, str(b))
total = b["intro_total"] + b["transition"] + b["market_total"]
check("budgets: the blocks add up to the target within rounding", abs(total - b["target"]) <= 8, f"{total} vs {b['target']}")
check("budgets: the market block is at least 70%", b["market_total"] / total >= 0.70, str(b["market_total"] / total))
check("budgets: the U.S. section stays inside the SOP's 100-200 words", 100 <= b["sections"]["country_us"] <= 200, str(b["sections"]))
check("budgets: the intro's own words are its budget minus the retained paragraph", b["intro_own"] == b["intro_total"] - 84)
big = planning.compute_budgets(260)
check("budgets: a very long retained paragraph lengthens the article, within the ceiling, and keeps the intro cap",
      big["target"] > planning.TARGET_WORDS and big["target"] <= planning.MAX_WORDS and big["intro_total"] / big["target"] <= planning.INTRO_CAP and big["notes"], str(big))
thin = planning.compute_budgets(84, {"trends": "short", "key_players": "short"})
check("budgets: a thin section gets fewer words than a normal one", thin["sections"]["trends"] < b["sections"]["trends"])
plan = plan_of(good_plan())
check("budgets: the plan carries the code-computed words per block",
      plan["intro"]["words"] == plan["budgets"]["intro_total"] and all(s["words"] > 0 for s in plan["sections"]))

# ------------------------------------------------------------------ the call (LLM stubbed) and the prompt
orig = llm.chat_json
llm.chat_json = lambda *a, **k: copy.deepcopy(good_plan())
try:
    check("call: the stubbed planner answer is validated end to end", planning.run_plan(make_inputs(), LOG)["title"].startswith("What Is Driving"))
finally:
    llm.chat_json = orig

text = planning.build_prompt(make_inputs())
check("prompt: lists the answerable questions only", "Q1 [size; can head report_context]" in text and "Q7" not in text)
check("prompt: lists report facts and evidence with their ids", "fs.size" in text and "W1 [suits:" in text)
check("prompt: marks a historical fact", "historical: give its year" in text)
bad = [l for l in prompts.PLAN_USER.splitlines() if "Key Trends" in l or "Zzz" in l]
check("prompt: contains no sample titles or placeholder markets", not bad, str(bad))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all planning tests passed")
