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

import facts as facts_mod  # noqa: E402
import llm  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_planning")
MARKET = "Zzz Widget Services Market"
HEADINGS = {
    "report_context": "Zzz Widget Services Market Size and Growth",
    "trends": "Trends in the Zzz Widget Services Market",
    "segmentation": "Zzz Widget Services Market Segmentation",
    "subsegment": "Sub-Segment Analysis of the Zzz Widget Services Market",
    "country_us": "U.S. Analysis of the Zzz Widget Services Market",
    "key_players": "Key Players in the Zzz Widget Services Market",
    "conclusion": "Conclusion and Outlook for the Zzz Widget Services Market",
}


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
                   {"id": "L2", "keyword": "widget piracy watch", "fact": "fs.restraints.0", "why": "x"},
                   {"id": "L3", "keyword": "widget us review", "fact": "fs.us.0", "why": "x"}],
        secondary_pool=list(planning._SECONDARY_KEYWORD_SUFFIXES), sponsor_words=84, has_us_facts=True,
    )


def sec(sop, **kw):
    base = {"sop": sop, "heading": HEADINGS[sop], "msq": None, "point": f"point {sop}", "facts": [],
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

# the cap trim never drops the size/growth question from report_context (demand.INTENT_SECTION maps it there and nowhere else)
raw = good_plan()
for s, qid in zip(raw["sections"], ["Q1", "Q2", "Q3", "Q4", "Q5", None, None]):
    s["msq"] = qid
raw["sections"][0]["facts"] = ["fs.size"]        # report_context ranks last, so the old trim dropped its question
p = plan_of(raw)
check("cap trim: five question headings are cut to three", sum(1 for s in p["sections"] if s["msq"]) == 3,
      str([s["msq"] for s in p["sections"]]))
check("cap trim: the size question stays on report_context even when that section ranks last",
      S(p, "report_context")["msq"] == "Q1", str([s["msq"] for s in p["sections"]]))
check("cap trim: the drops come from the other sections, lowest ranked first",
      S(p, "key_players")["msq"] is None and S(p, "country_us")["msq"] is None
      and S(p, "trends")["msq"] == "Q2" and S(p, "segmentation")["msq"] == "Q3",
      str([s["msq"] for s in p["sections"]]))
check("cap trim: the dropped questions move to the FAQ", all(q in p["faq"] for q in ["Q4", "Q5"]), str(p["faq"]))

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
check("headings: a bare label and a copied question text are replaced by clean topic headings",
      S(p, "segmentation")["heading"] == HEADINGS["segmentation"] and S(p, "key_players")["heading"] == HEADINGS["key_players"],
      str([S(p, "segmentation")["heading"], S(p, "key_players")["heading"]]))

raw = good_plan()
raw["sections"][2]["heading"] = raw["sections"][3]["heading"] = "Sub-Segments of the Zzz Widget Services Market"   # valid for both topic regexes
p = plan_of(raw)
check("headings: duplicates are made unique", len({s["heading"].lower() for s in p["sections"]}) == 7)

raw = good_plan()
raw["sections"][1]["msq"] = None      # trends loses its question heading, so its own heading is checked
raw["sections"][1]["heading"] = "Where the market stands"
p = plan_of(raw)
check("headings: a topic-less heading is replaced by the section's fallback topic heading",
      S(p, "trends")["heading"] == HEADINGS["trends"], S(p, "trends")["heading"])

raw = good_plan()
raw["sections"][4]["msq"] = None      # country_us loses its question heading, so its own heading is checked
raw["sections"][4]["heading"] = "North America leads while Asia Pacific grows fastest"
p = plan_of(raw)
check("headings: a finding-sentence heading is replaced by the section's fallback topic heading",
      S(p, "country_us")["heading"] == HEADINGS["country_us"], S(p, "country_us")["heading"])

raw = good_plan()
raw["sections"][2]["heading"] = "How the Zzz Widget Services Market is divided"
p = plan_of(raw)
check("headings: an interrogative heading without a question mark is replaced too",
      S(p, "segmentation")["heading"] == HEADINGS["segmentation"], S(p, "segmentation")["heading"])

raw = good_plan()
raw["sections"][1]["msq"] = None
raw["sections"][1]["heading"] = "Trends"
p = plan_of(raw)
check("headings: a heading that does not name the market is replaced by the fallback topic heading",
      S(p, "trends")["heading"] == HEADINGS["trends"], S(p, "trends")["heading"])

p = plan_of(good_plan())
check("headings: a clean topic heading is kept unchanged and logs no heading repair",
      S(p, "key_players")["heading"] == HEADINGS["key_players"] and not any("heading" in r for r in p["repairs"]),
      str([S(p, "key_players")["heading"], p["repairs"]]))

raw = good_plan()
for s, qid in zip(raw["sections"], ["Q1", "Q2", "Q3", "Q4", None, None, None]):
    s["msq"] = qid
p = plan_of(raw)
check("headings: an MSQ section keeps the exact question text",
      S(p, "trends")["heading"] == "What drives growth in the Zzz Widget Services Market?", S(p, "trends")["heading"])
check("headings: a demoted question leaves a clean topic heading, not the question",
      S(p, "country_us")["msq"] is None and S(p, "country_us")["heading"] == HEADINGS["country_us"],
      str([S(p, "country_us")["msq"], S(p, "country_us")["heading"]]))

check("fallbacks: every fallback topic heading passes its own heading_problem check",
      all(planning.heading_problem(text.format(market=MARKET), sop, MARKET) is None
          for sop, text in planning._FALLBACK_HEADINGS.items()),
      str({s: planning.heading_problem(text.format(market=MARKET), s, MARKET) for s, text in planning._FALLBACK_HEADINGS.items()}))
check("heading_problem: each failure kind has its own reason",
      planning.heading_problem("", "trends", MARKET) == "it is empty"
      and planning.heading_problem("What is changing?", "trends", MARKET) == "it is a question"
      and planning.heading_problem("Trends in the Zzz Widget Services Market by 2026", "trends", MARKET) == "it holds a figure"
      and planning.heading_problem("A heading with far too many words in it to pass the limit test", "trends", MARKET) == "it is too long",
      str([planning.heading_problem("", "trends", MARKET),
           planning.heading_problem("What is changing?", "trends", MARKET),
           planning.heading_problem("Trends in the Zzz Widget Services Market by 2026", "trends", MARKET),
           planning.heading_problem("A heading with far too many words in it to pass the limit test", "trends", MARKET)]))
check("heading_problem: the old report_context label fallback heading is rejected, by design",
      planning.heading_problem("Report Context for the Zzz Widget Services Market", "report_context", MARKET)
      == "it is a section label, not a topic phrase",
      str(planning.heading_problem("Report Context for the Zzz Widget Services Market", "report_context", MARKET)))
check("heading_problem: other report-context label wordings are rejected too",
      all(planning.heading_problem(h, "report_context", MARKET) is not None
          for h in ["Market Intelligence Report Context", "Market Intelligence: " + MARKET]),
      str([planning.heading_problem(h, "report_context", MARKET)
           for h in ["Market Intelligence Report Context", "Market Intelligence: " + MARKET]]))
check("heading_problem: topic words about size and growth are what a report_context heading must name",
      planning.heading_problem("Zzz Widget Services Market Size and Growth", "report_context", MARKET) is None
      and planning.heading_problem("Growth in the Zzz Widget Services Market", "report_context", MARKET) is None
      and planning.heading_problem("Overview of the Zzz Widget Services Market", "report_context", MARKET) is not None
      and planning.heading_problem("Context for the Zzz Widget Services Market", "report_context", MARKET) is not None,
      str([planning.heading_problem("Zzz Widget Services Market Size and Growth", "report_context", MARKET),
           planning.heading_problem("Growth in the Zzz Widget Services Market", "report_context", MARKET),
           planning.heading_problem("Overview of the Zzz Widget Services Market", "report_context", MARKET),
           planning.heading_problem("Context for the Zzz Widget Services Market", "report_context", MARKET)]))
raw = good_plan()
raw["sections"][0]["msq"] = None
raw["sections"][0]["heading"] = "Report Context for the Zzz Widget Services Market"
p = plan_of(raw)
check("headings: the label-style report_context heading is replaced by the plain topic fallback",
      S(p, "report_context")["msq"] is None and S(p, "report_context")["heading"] == HEADINGS["report_context"],
      str([S(p, "report_context")["msq"], S(p, "report_context")["heading"]]))

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
check("facts: at most six in the opening section, the headline figures always among them",
      len(S(p, "report_context")["facts"]) <= 6 and {"fs.size", "fs.cagr"} <= set(S(p, "report_context")["facts"]),
      str(S(p, "report_context")["facts"]))

raw = good_plan()
raw["sections"][2]["facts"] = ["fs.segments.0", "fs.segments.1"]
raw["sections"][3]["facts"] = ["fs.segments.0", "W2"]
p = plan_of(raw)
check("facts: a split's category list is stated once, in the segmentation section only (one piece of evidence, one place)",
      "fs.segments.0" in S(p, "segmentation")["facts"] and "fs.segments.0" not in S(p, "subsegment")["facts"],
      str((S(p, "segmentation")["facts"], S(p, "subsegment")["facts"])))
# (the U.S. section is the one place a report fact may repeat: with thin U.S. evidence it borrows the report's own facts)
every = [f for sec in p["sections"] if sec["sop"] != "country_us" for f in sec["facts"]]
check("facts: no fact id appears in two sections of the plan", len(every) == len(set(every)), str([f for f in set(every) if every.count(f) > 1]))

raw = good_plan()
raw["sections"][5]["facts"] = []
raw["sections"][4]["facts"] = []
p = plan_of(raw)
check("lengths: a section without facts is short; the U.S. section is handed every U.S. fact there is, then the report's own (one U.S. fact is thin)",
      S(p, "key_players")["length"] == "short" and S(p, "country_us")["facts"][0] == "fs.us.0" and S(p, "country_us")["length"] == "us_from_report",
      str((S(p, "country_us")["facts"], S(p, "country_us")["length"])))
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
inp = make_inputs()
for fid in [f for f, it in inp.items.items() if it["kind"] in ("us", "drivers", "opportunities", "segments")]:
    del inp.items[fid]
inp.longtails = [lt for lt in inp.longtails if lt["fact"] != "fs.us.0"]
check("lengths: a U.S. section with no U.S. fact and no report fact at all is flagged for the editor, never filled with another region",
      S(plan_of(raw, inp), "country_us")["length"] == "us_missing")
check("facts: a regional fact cannot sit in the U.S. section", "country_us" not in planning._FACT_SECTIONS["regions"])
check("facts: general trend evidence cannot sit in the U.S. section",
      "country_us" not in planning._EVIDENCE_TAG_SECTIONS["trends"] and "country_us" not in planning._EVIDENCE_TAG_SECTIONS["context"])

# ------------------------------------------------------------------ the U.S. section: enough U.S. facts, thin ones, none
def us_inputs(n_us: int, with_report_facts: bool = True) -> planning.PlanInputs:
    """make_inputs() with exactly n_us U.S. facts (fs.us.0 and an evidence item tagged us), and optionally no report facts of the
    kinds the thin-evidence fallback draws on."""
    inp = make_inputs()
    for fid in ("fs.us.0", "fs.regions.leader"):
        inp.items.pop(fid, None)
    inp.longtails = [lt for lt in inp.longtails if lt["fact"] in inp.items]
    us_items = [item("fs.us.0", "us", "A review of widget exports opened in the U.S.", {"country_us"}),
                item("W3", "evidence", "A U.S. agency funded a widget pilot.", {"country_us"}, tags=["us"], source="US News")]
    for it in us_items[:n_us]:
        inp.items[it["id"]] = it
    if not with_report_facts:
        for fid in [f for f, it in inp.items.items() if it["kind"] in ("drivers", "opportunities", "segments")]:
            del inp.items[fid]
    return inp


def us_plan(n_us, with_report_facts=True, trends_facts=("fs.drivers.0", "W1")):
    raw = good_plan()
    raw["sections"][1]["facts"] = list(trends_facts)
    raw["sections"][4]["facts"] = []
    return plan_of(raw, us_inputs(n_us, with_report_facts))


p = us_plan(2)
check("U.S. enough: with two U.S. facts the section rests on them alone, as written before",
      S(p, "country_us")["facts"] == ["fs.us.0", "W3"] and S(p, "country_us")["length"] == "normal" and S(p, "country_us")["us_source"] == "news",
      str((S(p, "country_us")["facts"], S(p, "country_us")["length"])))
every = [f for sec in p["sections"] for f in sec["facts"]]
check("U.S. enough: no fact is stated in two sections", len(every) == len(set(every)), str([f for f in set(every) if every.count(f) > 1]))

p = us_plan(1)
us = S(p, "country_us")
check("U.S. thin (one fact): the section is written from the report's own facts, its U.S. statement first",
      us["length"] == "us_from_report" and us["us_source"] == "report" and us["facts"][0] == "fs.us.0" and "fs.drivers.0" in us["facts"],
      str((us["facts"], us["length"])))
check("U.S. thin: the report's facts follow in kind order (us, drivers, opportunities, segments) up to the section cap",
      us["facts"] == ["fs.us.0", "fs.drivers.0", "fs.opportunities.0"] and len(us["facts"]) <= planning._FACTS_MAX["country_us"],
      str(us["facts"]))
check("U.S. thin: a fact borrowed by the U.S. section stays where it was (trends keep their driver)",
      "fs.drivers.0" in S(p, "trends")["facts"], str(S(p, "trends")["facts"]))
check("U.S. thin: the plan says so in its repairs", any("fewer than 2 U.S. facts exist" in r for r in p["repairs"]), str(p["repairs"]))
check("U.S. thin: the budget stays the section's own (no editor-note size)", S(p, "country_us")["words"] >= 100, str(S(p, "country_us")["words"]))

p = us_plan(0)
check("U.S. none: also written from the report's own facts, no editor note",
      S(p, "country_us")["length"] == "us_from_report" and S(p, "country_us")["facts"][0] == "fs.drivers.0", str(S(p, "country_us")["facts"]))

p = us_plan(0, with_report_facts=False)
check("U.S. none and no report fact: the editor-note mode, with a note-sized budget",
      S(p, "country_us")["length"] == "us_missing" and S(p, "country_us")["words"] <= 30, str((S(p, "country_us")["length"], S(p, "country_us")["words"])))

# evidence tagged us always goes to the U.S. section and never to a trend
inp = us_inputs(2)
raw = good_plan()
raw["sections"][1]["facts"] = ["fs.drivers.0", "W1", "W3"]
raw["sections"][1]["trend_groups"] = [{"trend": "a", "facts": ["fs.drivers.0"]}, {"trend": "b", "facts": ["W1"]}, {"trend": "c", "facts": ["W3"]}]
raw["sections"][4]["facts"] = ["fs.us.0"]
p = plan_of(raw, inp)
check("U.S. tag: evidence tagged us is dropped from the trends (and their groups) and lands in the U.S. section",
      "W3" not in S(p, "trends")["facts"] and all("W3" not in g["facts"] for g in S(p, "trends")["trend_groups"])
      and "W3" in S(p, "country_us")["facts"], str((S(p, "trends")["facts"], S(p, "country_us")["facts"])))
check("U.S. tag: the skip is logged", any("W3" in r and "U.S." in r for r in p["repairs"]), str(p["repairs"]))

built = planning.build_inputs(
    MARKET, {**facts_mod._empty_fact_sheet(), "us": [{"id": "fs.us.0", "text": "A U.S. statement.", "historical": False}]},
    [{"id": "W9", "fact": "A U.S. firm expanded.", "tags": ["us", "trends"], "source": "S"},
     {"id": "W8", "fact": "A firm launched.", "tags": ["trends"], "source": "S"}],
    {}, {}, 80)
check("U.S. tag: build_inputs lets evidence tagged us sit in the U.S. section only, however else it was tagged",
      built.items["W9"]["sections"] == {"country_us"} and "country_us" not in built.items["W8"]["sections"], str(built.items["W9"]["sections"]))
check("U.S. tag: has_us_facts is set from the U.S. facts", built.has_us_facts)

# the PLAN prompt carries the rule
check("U.S. tag: the plan prompt says a U.S.-tagged fact belongs to the U.S. section and to no trend",
      "belongs to the U.S. section and to no trend" in prompts.PLAN_USER)

# ------------------------------------------------------------------ segmentation and sub-segments explain why a category leads
def seg_inputs() -> planning.PlanInputs:
    """Three splits, each with a stated leader; a reason (segment_drivers) for the leader of By Type and of By End User."""
    inp = make_inputs()
    inp.segments = [
        {"id": "fs.segments.0", "type": "By Type", "items": ["Browser Extension", "Cloud Based"], "figures": {}, "dominant": {"name": "Cloud Based", "basis": "stated"}},
        {"id": "fs.segments.1", "type": "By Application", "items": ["Data Aggregation", "Market Research"], "figures": {}, "dominant": {"name": "Data Aggregation", "basis": "stated"}},
        {"id": "fs.segments.2", "type": "By End User", "items": ["Retail", "Banking"], "figures": {}, "dominant": {"name": "Retail", "basis": "stated"}},
    ]
    for n, (split, leader) in enumerate([("By Type", "Cloud Based"), ("By Application", "Data Aggregation"), ("By End User", "Retail")]):
        inp.items[f"fs.segments.{n}"] = item(f"fs.segments.{n}", "segments", f"Segmentation {split}: x, y", {"segmentation", "subsegment"})
        inp.items[f"fs.segments.{n}.dominant"] = item(f"fs.segments.{n}.dominant", "segments", f"Dominant category in {split}: {leader}", {"segmentation", "subsegment"})
    for fid, split, name in [("fs.segment_drivers.0", "By Type", "Cloud Based"), ("fs.segment_drivers.1", "By End User", "Retail")]:
        it = item(fid, "segment_drivers", f"Why {name} stands out in {split}: remote teams want it.", planning._FACT_SECTIONS["segment_drivers"])
        it.update({"split": split, "name": name})
        inp.items[fid] = it
    return inp


seg_raw = good_plan()
seg_raw["sections"][2]["facts"] = ["fs.segments.0", "fs.segments.1", "fs.segments.2"]
seg_raw["sections"][3]["facts"] = []
seg_raw["sections"][3]["sub_segments"] = [{"split": "By Type", "name": "Cloud Based"}, {"split": "By Application", "name": "Data Aggregation"}]
p = plan_of(seg_raw, seg_inputs())
seg_facts, sub_facts = S(p, "segmentation")["facts"], S(p, "subsegment")["facts"]
check("segment reasons: the reason for a picked sub-segment goes to the sub-segment section", "fs.segment_drivers.0" in sub_facts, str(sub_facts))
check("segment reasons: ... and is not repeated in segmentation", "fs.segment_drivers.0" not in seg_facts, str(seg_facts))
check("segment reasons: the reason for the leader of a split no sub-segment covers goes to segmentation",
      "fs.segment_drivers.1" in seg_facts and "fs.segment_drivers.1" not in sub_facts, str((seg_facts, sub_facts)))
check("segment reasons: a split's leader line stays in segmentation even when its leader is a picked sub-segment",
      {"fs.segments.0.dominant", "fs.segments.1.dominant", "fs.segments.2.dominant"} <= set(seg_facts)
      and {"fs.segments.0.dominant", "fs.segments.1.dominant"} <= set(sub_facts), str((seg_facts, sub_facts)))
check("segment reasons: a split's category list is still stated in segmentation only",
      all(f in seg_facts for f in ("fs.segments.0", "fs.segments.1", "fs.segments.2")) and not [f for f in sub_facts if f in ("fs.segments.0", "fs.segments.1", "fs.segments.2")])
check("segment reasons: drivers may now sit in segmentation and sub-segment as well as trends",
      {"segmentation", "subsegment", "trends"} <= planning._FACT_SECTIONS["drivers"]
      and planning._FACT_SECTIONS["segment_drivers"] == {"segmentation", "subsegment"})
seg_raw["sections"][2]["facts"] += ["fs.drivers.0"]
seg_raw["sections"][1]["facts"] = ["W1"]
seg_in = seg_inputs()
seg_in.items["fs.drivers.0"]["sections"] = set(planning._FACT_SECTIONS["drivers"])      # as build_inputs sets it from the kind
p = plan_of(seg_raw, seg_in)
check("segment reasons: a general driver the planner gives the segmentation section is accepted there", "fs.drivers.0" in S(p, "segmentation")["facts"],
      str(S(p, "segmentation")["facts"]))
check("segment reasons: the plan prompt names the segmentation topic with why the leader leads",
      "why the leading category in each split leads" in prompts.PLAN_USER)

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
inp3 = make_inputs()
inp3.longtails = [
    {"id": "L9", "keyword": "top companies widgets market", "fact": "fs.players", "why": "x"},  # every word sits in Q5
    {"id": "L1", "keyword": "cloud based widget services by region", "fact": "fs.regions.leader", "why": "x"},
    {"id": "L2", "keyword": "widget piracy watch", "fact": "fs.restraints.0", "why": "x"},
]
raw = good_plan()
for s in raw["sections"]:
    s["longtail"] = None
raw["sections"][4]["msq"] = None          # make room for a third question heading
raw["sections"][5]["msq"] = "Q5"          # key_players heads Q5, whose question words L9 would only restate
raw["sections"][5]["longtail"] = "L9"
p = plan_of(raw, inp3)
check("long-tails: a phrase whose words all sit in its section's MSQ question is dropped and valid phrases fill the minimum",
      S(p, "key_players")["longtail"] is None and S(p, "country_us")["longtail"] == "L1" and S(p, "trends")["longtail"] == "L2"
      and any("restates the question heading" in r for r in p["repairs"]),
      str([s["longtail"] for s in p["sections"]]) + "; " + str(p["repairs"]))
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

inp2 = make_inputs()
inp2.points[4]["text"] = "Cloud pricing is metered by credit, so small jobs stay cheap for teams."
inp2.points[5]["text"] = "A quick brown fox jumps over every lazy dog near the river."
raw = good_plan()
raw["sections"][3]["snippet"] = "P5"
raw["sections"][5]["snippet"] = "P6"
p = plan_of(raw, inp2)
check("snippets: one that shares vocabulary with its section is kept, an unrelated one is dropped",
      S(p, "subsegment")["snippet"] == "P5" and S(p, "key_players")["snippet"] is None, str([s["snippet"] for s in p["sections"]]))

# ------------------------------------------------------------------ sub-segments
raw = good_plan()
raw["sections"][3]["sub_segments"] = [{"split": "By Type", "name": "Browser Extension"}, {"split": "By Type", "name": "Cloud Based"}]
p = plan_of(raw)
splits = [x["split"] for x in S(p, "subsegment")["sub_segments"]]
check("sub-segments: two from the same split are accepted (the top two of one split)", splits == ["By Type", "By Type"],
      str(S(p, "subsegment")["sub_segments"]))
raw = good_plan()
raw["sections"][3]["sub_segments"] = [{"split": "By Type", "name": "Cloud Based"}, {"split": "By Type", "name": "Browser Extension"}]
inp_same_split = make_inputs()
inp_same_split.subsegments = [{"split": "By Type", "name": "Cloud Based"}, {"split": "By Type", "name": "Browser Extension"}]
p = plan_of(raw, inp_same_split)
check("sub-segments: a fixed same-split pair stands and is not repaired",
      [(v["split"], v["name"]) for v in S(p, "subsegment")["sub_segments"]] == [("By Type", "Cloud Based"), ("By Type", "Browser Extension")]
      and all("sub-segment" not in r for r in p["repairs"]), str(p["repairs"]))
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

# ------------------------------------------------------------------ trend groups: three trends, each with its own facts
class _TrendInp:
    items = {"fs.drivers.0": {"kind": "drivers"}, "fs.drivers.1": {"kind": "drivers"}, "fs.drivers.2": {"kind": "drivers"},
             "W1": {"kind": "evidence"}, "W2": {"kind": "evidence"}, "W3": {"kind": "evidence"}, "W4": {"kind": "evidence"}}


def _groups(raw_groups, kept):
    notes = []
    return planning._trend_groups(raw_groups, kept, _TrendInp, notes.append), notes


kept = ["fs.drivers.0", "fs.drivers.1", "fs.drivers.2", "W1", "W2", "W3"]
g, notes = _groups([{"trend": "A", "facts": ["fs.drivers.0", "W1"]}, {"trend": "B", "facts": ["fs.drivers.1", "W2"]},
                    {"trend": "C", "facts": ["fs.drivers.2", "W3"]}], kept)
check("trend groups: a valid grouping passes through untouched", [x["facts"] for x in g] == [["fs.drivers.0", "W1"], ["fs.drivers.1", "W2"], ["fs.drivers.2", "W3"]] and notes == [], str(notes))
g, notes = _groups([{"trend": "A", "facts": ["W1", "W2", "W3", "fs.drivers.0"]}, {"trend": "B", "facts": ["fs.drivers.1"]}], kept)
check("trend groups: at most three facts a trend, a missing third group is added, ungrouped facts are given out",
      len(g) == 3 and all(len(x["facts"]) <= 3 for x in g) and sorted(f for x in g for f in x["facts"]) == sorted(kept), str(g))
check("trend groups: a news item is never moved from one trend to another to fill a bare trend (the bare trend stays bare)",
      g[0]["facts"] == ["W1", "W2", "W3"] and not any(f.startswith("W") for f in g[2]["facts"]), str(g))
g, _ = _groups([{"trend": "A", "facts": ["W1", "W1", "ghost"]}, {"trend": "B", "facts": ["W1", "W2"]}, {"trend": "C", "facts": []}], ["W1", "W2", "fs.drivers.0"])
check("trend groups: unknown ids and a fact in two groups are dropped (one fact, one trend)",
      sum(x["facts"].count("W1") for x in g) == 1 and not any("ghost" in x["facts"] for x in g), str(g))
g, _ = _groups(None, [])
check("trend groups: no groups and no facts still gives three empty trends", len(g) == 3 and all(x["facts"] == [] for x in g))
class _TextInp:
    items = {
        "W1": {"kind": "evidence", "text": "Acme launched adaptive testing software for online assessment platforms."},
        "W2": {"kind": "evidence", "text": "Beta Group raised funding to expand cloud proctoring services for universities."},
        "W3": {"kind": "evidence", "text": "A hospital network adopted adaptive testing for nursing assessment."},
        "W4": {"kind": "evidence", "text": "Gamma Labs opened a data centre."},
    }


g = planning._trend_groups(
    [{"trend": "Adaptive testing reshapes online assessment platforms", "facts": ["W1"]},
     {"trend": "Funding for cloud proctoring services at universities", "facts": ["W2"]}, {"trend": "Something else entirely", "facts": []}],
    ["W1", "W2", "W3"], _TextInp, lambda m: None)
check("trend groups: a leftover fact goes to the trend whose name and facts it resembles, not the one with the fewest news items",
      "W3" in g[0]["facts"] and g[2]["facts"] == [], str(g))
g = planning._trend_groups(
    [{"trend": "A", "facts": ["W1", "W2"]}, {"trend": "B", "facts": ["W3"]}, {"trend": "C", "facts": []}], ["W1", "W2", "W3"], _TextInp, lambda m: None)
check("trend groups: a bare trend stays bare even while another holds two news items",
      [x["facts"] for x in g] == [["W1", "W2"], ["W3"], []], str(g))
g = planning._trend_groups(
    [{"trend": "A", "facts": ["W1"]}, {"trend": "B", "facts": ["W2"]}, {"trend": "C", "facts": []}], ["W1", "W2", "W4"], _TextInp, lambda m: None)
check("trend groups: a leftover that resembles no trend goes to the one with the fewest facts",
      g[2]["facts"] == ["W4"], str(g))

# ------------------------------------------------------------------ one event is one fact
def news(nid, fact, source, detail=""):
    return {"id": nid, "fact": fact, "detail": detail, "source": source, "tags": ["trends"]}


ev_a = news("W1", "Apertera Systems launched a screening tool for hospitals.", "Health Wire, 2026-10", "Short.")
ev_b = news("W2", "Apertera announced its new screening tool for hospital buyers.", "Trade Daily, 2026-10", "It covers sixty hospitals across three states and was priced at launch.")
ev_c = news("W3", "Apertera Systems named a new chief executive.", "Health Wire, 2026-09")
ev_d = news("W4", "Borealis Ltd raised funding for diagnostics.", "Health Wire, 2026-10")
check("same event: the same organisation in the same month is one event", planning.same_event(
    {**ev_a, "text": ev_a["fact"]}, {**ev_b, "text": ev_b["fact"]}))
check("same event: another month, another organisation, or no month is not",
      not planning.same_event({**ev_a, "text": ev_a["fact"]}, {**ev_c, "text": ev_c["fact"]})
      and not planning.same_event({**ev_a, "text": ev_a["fact"]}, {**ev_d, "text": ev_d["fact"]})
      and not planning.same_event({**ev_a, "text": ev_a["fact"], "source": "Health Wire"}, {**ev_b, "text": ev_b["fact"], "source": "Trade Daily"}))
kept_ev = planning.dedupe_events([ev_a, ev_b, ev_c, ev_d], LOG)
check("same event: of two items on one event only one is kept, the fuller one, and the rest are untouched, in order",
      [e["id"] for e in kept_ev] == ["W2", "W3", "W4"], str([e["id"] for e in kept_ev]))
check("same event: the publisher's own name is not an organisation of the event",
      len(planning.dedupe_events([news("W5", "Health Wire reported a pilot of cloud billing.", "Health Wire, 2026-10"),
                                  news("W6", "Health Wire interviewed a clinic owner about staffing.", "Health Wire, 2026-10")], LOG)) == 2)
inp_ev = planning.build_inputs(MARKET, facts_mod._empty_fact_sheet(), [ev_a, ev_b, ev_c], {}, {}, 80)
check("same event: build_inputs hands the planner one item per event", sorted(i for i in inp_ev.items if i.startswith("W")) == ["W2", "W3"], str(list(inp_ev.items)))
cl = planning.topic_clusters({
    "W1": {"kind": "evidence", "text": ev_a["fact"], "detail": "", "source": ev_a["source"]},
    "W2": {"kind": "evidence", "text": ev_b["fact"], "detail": "", "source": ev_b["source"]},
    "W3": {"kind": "evidence", "text": ev_c["fact"], "detail": "", "source": ev_c["source"]}})
check("same event: topic_clusters puts the two reports of one event in one cluster, so a plan states it once",
      cl["W1"] == cl["W2"] != cl["W3"], str(cl))

tplan = planning.validate_plan(good_plan(), make_inputs(), LOG)
tsec = next(s for s in tplan["sections"] if s["sop"] == "trends")
check("trend groups: validate_plan always returns three groups and the section's facts are their union",
      len(tsec["trend_groups"]) == 3 and sorted(tsec["facts"]) == sorted(f for x in tsec["trend_groups"] for f in x["facts"]), str(tsec["trend_groups"]))

# ------------------------------------------------------------------ budgets
b = planning.compute_budgets(84)
check("budgets: the target is the raised SOP middle, 1,550", b["target"] == planning.TARGET_WORDS, str(b["target"]))
check("budgets: the intro share is under its cap, the transition inside its band",
      b["intro_total"] / b["target"] <= planning.INTRO_CAP and 0.05 <= b["transition"] / b["target"] <= 0.10, str(b))
total = b["intro_total"] + b["transition"] + b["market_total"]
check("budgets: the blocks add up to the target within rounding", abs(total - b["target"]) <= 8, f"{total} vs {b['target']}")
check("budgets: the market block is at least 70%", b["market_total"] / total >= 0.70, str(b["market_total"] / total))
check("budgets: the U.S. section stays inside the SOP's 100-200 words", 100 <= b["sections"]["country_us"] <= 200, str(b["sections"]))
check("budgets: the intro's own words are its budget minus the retained paragraph", b["intro_own"] == b["intro_total"] - 84)
big = planning.compute_budgets(300)
check("budgets: a very long retained paragraph lengthens the article, within the ceiling, and keeps the intro cap",
      big["target"] > b["target"] and big["target"] <= planning.MAX_WORDS and big["intro_total"] / big["target"] <= planning.INTRO_CAP and big["notes"], str(big))
thin = planning.compute_budgets(84, {}, {"trends": 0, "key_players": 0})
check("budgets: a thin section gets fewer words than a normal one", thin["sections"]["trends"] < b["sections"]["trends"])
few = planning.compute_budgets(84, {}, {"report_context": 1, "trends": 1, "segmentation": 1, "subsegment": 0, "country_us": 1, "key_players": 0})
check("budgets: fewer facts shrink a section inside the same 1,550-word target, and the shares still hold",
      few["target"] == b["target"] and few["sections"]["trends"] < b["sections"]["trends"]
      and few["intro_total"] / few["target"] <= planning.INTRO_CAP and 0.05 <= few["transition"] / few["target"] <= 0.10, str(few))
check("budgets: a section's budget never falls below its floor share of its weight", few["sections"]["subsegment"] > 0)
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

# ------------------------------------------------------------------ the report's main figures always reach the plan
big = make_inputs()
big.items["fs.forecast"] = item("fs.forecast", "forecast", "Market forecast: USD 3.4 billion (2032)", {"report_context"})
big.items["fs.regions.leader"] = item("fs.regions.leader", "regions", "Leading region: North America (42.6% share in 2026)", {"report_context"})
big.items["fs.regions.fastest"] = item("fs.regions.fastest", "regions", "Fastest-growing region: Asia Pacific", {"report_context"})
big.segments = [
    {"id": "fs.segments.0", "type": "By Type", "items": ["Browser Extension", "Cloud Based"],
     "figures": {"Cloud Based": {"figure": "48.2%"}}, "dominant": {"name": "Cloud Based", "basis": "stated"}},
    {"id": "fs.segments.1", "type": "By Application", "items": ["Data Aggregation", "Market Research"],
     "figures": {"Market Research": {"figure": "37%"}}, "dominant": {"name": "Market Research", "basis": "stated"}},
    {"id": "fs.segments.2", "type": "By Mode", "items": ["Online", "Offline"],
     "figures": {"Online": {"figure": "55.9%"}}, "dominant": {"name": "Online", "basis": "stated"}},
]
big.items["fs.segments.2"] = item("fs.segments.2", "segments", "Segmentation By Mode: Online, Offline", {"segmentation", "subsegment"})
for g in big.segments:
    big.items[f"{g['id']}.dominant"] = item(f"{g['id']}.dominant", "segments", f"Dominant category in {g['type']}: {g['dominant']['name']}", {"segmentation", "subsegment"})
    for k, name in enumerate(g["items"]):
        if name in g["figures"]:
            big.items[f"{g['id']}.i{k}"] = item(f"{g['id']}.i{k}", "segment_item", f"{name} ({g['type']}): {g['figures'][name]['figure']}",
                                                {"subsegment", "segmentation"})
raw = good_plan()
raw["sections"][0]["facts"] = ["fs.size"]                 # the planner left the forecast, growth rate and regions out
raw["sections"][2]["facts"] = ["fs.segments.0"]           # and most segment splits
p = plan_of(raw, big)
ctx_facts, seg_facts, sub_facts = S(p, "report_context")["facts"], S(p, "segmentation")["facts"], S(p, "subsegment")["facts"]
check("figures: size, forecast, growth rate and both regions always reach report_context",
      {"fs.size", "fs.forecast", "fs.cagr", "fs.regions.leader", "fs.regions.fastest"} <= set(ctx_facts), str(ctx_facts))
check("figures: every segment split is in segmentation", {"fs.segments.0", "fs.segments.1", "fs.segments.2"} <= set(seg_facts), str(seg_facts))
check("figures: the split with no sub-segment pick has its leader and share in segmentation",
      {"fs.segments.2.dominant", "fs.segments.2.i0"} <= set(seg_facts), str(seg_facts))
check("figures: the two sub-segments keep their leader and share in subsegment, stated once",
      {"fs.segments.0.dominant", "fs.segments.0.i1", "fs.segments.1.dominant", "fs.segments.1.i1"} <= set(sub_facts)
      and not ({"fs.segments.0.i1", "fs.segments.1.i1"} & set(seg_facts)), f"{sub_facts} / {seg_facts}")
check("figures: the facts that were added are logged as repairs", any("fs.forecast" in r for r in p["repairs"]) and any("fs.segments.2.i0" in r for r in p["repairs"]))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all planning tests passed")
