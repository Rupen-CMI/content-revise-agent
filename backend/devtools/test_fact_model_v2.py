"""Offline test for the fact model's segment figures and dominant category, and for the new positive-only, U.S.-only and
repetition rules. No network, no LLM.

Run from backend/: python devtools/test_fact_model_v2.py
"""
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import content_rules  # noqa: E402
import facts  # noqa: E402
import planning  # noqa: E402
import polarity  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_fact_model_v2")
TODAY = date(2026, 10, 23)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


MARKET = "Zzz Widget Services Market"
PAGE = (
    "The Global Zzz Widget Services Market is estimated to be valued at USD 561.7 million in 2026. "
    "By type, Cloud Based widgets held 61.5% of the Zzz Widget Services Market in 2025. "
    "By type, Installable Software widgets held 28.0% of the Zzz Widget Services Market in 2025. "
    "By type, Browser Extension widgets held 10.5% of the Zzz Widget Services Market in 2025. "
    "By application, Research is the dominant application of the Zzz Widget Services Market. "
    "By application, Reporting and Monitoring are other applications. "
    "By end user, Retail accounted for USD 300 million of the Zzz Widget Services Market. "
    "By end user, Banking accounted for USD 120 million of the Zzz Widget Services Market. "
)


def raw_segments() -> dict:
    return {
        "segments": [
            {"type": "By Type", "items": [
                {"name": "Cloud Based", "figure": "61.5%", "quote": "By type, Cloud Based widgets held 61.5% of the Zzz Widget Services Market in 2025"},
                {"name": "Installable Software", "figure": "28.0%", "quote": "By type, Installable Software widgets held 28.0% of the Zzz Widget Services Market in 2025"},
                {"name": "Browser Extension", "figure": "10.5%", "quote": "By type, Browser Extension widgets held 10.5% of the Zzz Widget Services Market in 2025"},
            ]},
            {"type": "By Application", "items": [{"name": "Research"}, {"name": "Reporting"}, {"name": "Monitoring"}]},
            {"type": "By End User", "items": [
                {"name": "Banking", "figure": "USD 120 million", "quote": "By end user, Banking accounted for USD 120 million of the Zzz Widget Services Market"},
                {"name": "Retail", "figure": "USD 300 million", "quote": "By end user, Retail accounted for USD 300 million of the Zzz Widget Services Market"},
            ]},
        ],
        "dominant": [{"split": "By Application", "name": "Research", "quote": "By application, Research is the dominant application of the Zzz Widget Services Market"}],
    }


def segment(sheet: dict, kind: str) -> dict:
    return next(s for s in sheet["segments"] if s["type"] == kind)


fs = facts.validate_fact_sheet(raw_segments(), PAGE, MARKET, LOG, TODAY)
by_type, by_app, by_user = segment(fs, "By Type"), segment(fs, "By Application"), segment(fs, "By End User")

check("segments: category names stay a plain list of strings", by_type["items"] == ["Cloud Based", "Installable Software", "Browser Extension"])
check("segments: a stated share is kept with its quote", by_type["figures"]["Cloud Based"]["figure"] == "61.5%")
check("dominant: derived from complete shares", by_type["dominant"] and by_type["dominant"]["name"] == "Cloud Based" and by_type["dominant"]["basis"] == "derived")
check("dominant: stated by the page when it says so", by_app["dominant"] and by_app["dominant"]["name"] == "Research" and by_app["dominant"]["basis"] == "stated")
check("dominant: derived from amounts with a unit", by_user["dominant"] and by_user["dominant"]["name"] == "Retail")

raw = raw_segments()
raw["dominant"][0]["quote"] = "By application, Research is the best loved application of the Zzz Widget Services Market"
check("dominant: a quote not on the page is refused",
      segment(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY), "By Application")["dominant"] is None)

raw = raw_segments()
raw["dominant"][0]["name"] = "Monitoring"
raw["dominant"][0]["quote"] = "By application, Reporting and Monitoring are other applications"
check("dominant: a quote with no dominance wording is refused",
      segment(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY), "By Application")["dominant"] is None)

raw = raw_segments()
raw["segments"][0]["items"][0]["figure"] = "71.5%"
check("figures: a figure whose number is not in its quote is dropped",
      "Cloud Based" not in segment(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY), "By Type")["figures"])


def share(amount: float) -> dict:
    return {"kind": "share", "amount": amount, "figure": f"{amount}%", "quote": "q"}


check("derive: an incomplete split with a majority share still settles it",
      facts.derive_dominant(["A", "B", "C"], {"A": share(55.0), "B": share(10.0)})["name"] == "A")
check("derive: an incomplete split whose top share could be beaten settles nothing",
      facts.derive_dominant(["A", "B", "C"], {"A": share(30.0), "B": share(20.0)}) is None)
check("derive: mixed kinds settle nothing",
      facts.derive_dominant(["A", "B"], {"A": share(30.0), "B": {"kind": "value", "amount": 2e9, "figure": "USD 2 billion", "quote": "q"}}) is None)
check("derive: a tie settles nothing", facts.derive_dominant(["A", "B"], {"A": share(50.0), "B": share(50.0)}) is None)

flat = {f["id"]: f for f in facts.flatten_fact_sheet(fs)}
check("flatten: the split fact lists names only", flat["fs.segments.0"]["text"] == "Segmentation By Type: Cloud Based, Installable Software, Browser Extension")
check("flatten: the dominant category is its own fact", "Cloud Based" in flat["fs.segments.0.dominant"]["text"])
check("flatten: a category with a figure gets an automatic fact carrying it",
      flat["fs.segments.0.i0"]["text"] == "Cloud Based (By Type): 61.5%" and flat["fs.segments.0.i0"]["auto"])

# ---------------------------------------------------------------------------------------------- polarity
check("polarity: a setback is negative", polarity.is_negative("Piracy hampers the growth of the market."))
check("polarity: a plain positive sentence is not", not polarity.is_negative("Rising demand is accelerating adoption of the platform."))
check("polarity: 'refine', 'define' and 'bank' do not fire", not polarity.is_negative("Banks refine and define their offering."))
check("polarity: reducing a risk is not negative", not polarity.is_negative("The platform reduces the risk of fraud for lenders."))
check("polarity: a market named after a risk is exempt for itself",
      not polarity.is_negative("Risk management software adoption is rising.", polarity.market_words("Risk Management Software Market")))

# --------------------------------------------------------------------------------------------- content rules
us_sections = [{"heading": "U.S. Analysis", "sop": ["country_us"], "paragraphs": [
    "Federal programmes are widening access to widgets in the United States.\n- Asia-Pacific adoption is faster than the U.S. pace."]}]
check("us only: another region inside the U.S. section is flagged", len(content_rules.us_only_problems(us_sections)) == 1)
ok_sections = [{"heading": "U.S. Analysis", "sop": ["country_us"], "paragraphs": [
    "The U.S. holds most of North America's widget demand, and federal programmes are widening access."]}]
check("us only: North America in the same sentence as the U.S. is allowed", content_rules.us_only_problems(ok_sections) == [])
na_only = [{"heading": "U.S. Analysis", "sop": ["country_us"], "paragraphs": ["North America leads widget demand."]}]
check("us only: North America alone is flagged", len(content_rules.us_only_problems(na_only)) == 1)

trend_sections = [
    {"heading": "Trends", "sop": ["trends"], "paragraphs": ["Cloud vendors are launching metered widget plans across enterprise buyers worldwide."]},
    {"heading": "Sub-Segment", "sop": ["subsegment"], "paragraphs": ["Enterprise buyers worldwide are adopting metered widget plans launched by cloud vendors."]},
]
check("repetition: the same point in two sections is flagged", len(content_rules.repeated_point_problems(trend_sections)) == 1)
check("repetition: different points are not",
      content_rules.repeated_point_problems([
          {"heading": "A", "sop": ["trends"], "paragraphs": ["Cloud vendors are launching metered widget plans across enterprise buyers worldwide."]},
          {"heading": "B", "sop": ["key_players"], "paragraphs": ["Acme opened three regional service centres to shorten installation queues for hospitals."]}]) == [])

# ----------------------------------------------------------------------------------- the planner keeps the leader
import test_planning  # noqa: E402

inp = test_planning.make_inputs()
inp.segments[0]["dominant"] = {"name": "Cloud Based", "basis": "derived", "quote": "q"}
inp.segments[0]["figures"] = {"Cloud Based": {"figure": "61.5%"}}
inp.items["fs.segments.0.dominant"] = test_planning.item("fs.segments.0.dominant", "segments", "Dominant category in By Type: Cloud Based", {"segmentation", "subsegment"})
inp.items["fs.segments.0.i1"] = {**test_planning.item("fs.segments.0.i1", "segment_item", "Cloud Based (By Type): 61.5%", {"subsegment"}), "auto": True}
inp.items["fs.segments.0.i1"]["id"] = "fs.segments.0.i1"
raw = test_planning.good_plan()
sub = next(sec for sec in raw["sections"] if sec["sop"] == "subsegment")
sub["sub_segments"] = [{"split": "By Type", "name": "Browser Extension"}, {"split": "By Application", "name": "Market Research"}]
plan = test_planning.plan_of(raw, inp)
sub_plan = next(sec for sec in plan["sections"] if sec["sop"] == "subsegment")
check("planner: a sub-segment pick is replaced by its split's dominant category",
      {"split": "By Type", "name": "Cloud Based"} in sub_plan["sub_segments"], str(sub_plan["sub_segments"]))
check("planner: the dominant fact and the category's own figure travel with the pick",
      "fs.segments.0.dominant" in sub_plan["facts"] and "fs.segments.0.i1" in sub_plan["facts"], str(sub_plan["facts"]))
check("planner: the automatic per-category fact is not shown to the planner", "fs.segments.0.i1" not in planning.build_prompt(inp))

# ------------------------------------------------------------------------------------------------ U.S. search
import search  # noqa: E402
import us_search  # noqa: E402


def hit(url: str, title: str = "Widget services expand", snippet: str = "", scope: str = "market") -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=url, source="Src", date=None, scope=scope)


check("us search: every query names the U.S.", all(("U.S." in q or "United States" in q) for q in us_search.us_news_queries(MARKET)))
check("us search: a page that names the U.S. in its text qualifies", us_search.names_the_us(hit("https://a.example/1", snippet="Demand in the U.S. is rising")))
check("us search: a page that names another place only does not", not us_search.names_the_us(hit("https://a.example/2", snippet="Demand in Europe is rising")))
found_us = hit("https://a.example/1", snippet="Demand in the U.S. is rising")
found_us.add_sections(["us"])
other_us = hit("https://a.example/9", snippet="Demand in Europe is rising")
other_us.add_sections(["us"])
shared = hit("https://a.example/5", snippet="Demand in Europe is rising")
shared.add_sections(["trends", "us"])
plain_hit = hit("https://a.example/3")
plain_hit.add_sections(["trends"])
settled = us_search.mark_us_hits([found_us, other_us, shared, plain_hit])
check("us search: a page found for 'us' that names the U.S. is marked scope 'us'", found_us in settled and found_us.scope == "us")
check("us search: a page found only for 'us' that does not name the U.S. is dropped", other_us not in settled)
check("us search: a page found for 'us' and another section that does not name the U.S. keeps the other section only",
      shared in settled and shared.sections == ["trends"] and shared.scope == "market")
check("us search: a page no 'us' query found is left as it was", plain_hit in settled and plain_hit.scope == "market")

# ------------------------------------------------------------------------------------------- topic clusters
import planning as _planning  # noqa: E402

topic_items = {
    "W1": test_planning.item("W1", "evidence", "Acme launched a metered cloud widget plan for enterprise buyers in the U.S.", {"trends"}, tags=["trends"]),
    "W2": test_planning.item("W2", "evidence", "Enterprise buyers in the U.S. gain a metered cloud widget plan launched by Acme.", {"trends", "country_us"}, tags=["trends", "us"]),
    "W3": test_planning.item("W3", "evidence", "Beta opened three service centres to shorten installation queues for hospitals.", {"trends"}, tags=["trends"]),
    "fs.segments.0": test_planning.item("fs.segments.0", "segments", "Segmentation By Type: Cloud Based, Installable Software", {"segmentation"}),
}
clusters = _planning.topic_clusters(topic_items)
check("topics: two facts that make the same point share a cluster", clusters["W1"] == clusters["W2"])
check("topics: a different point is its own cluster", clusters["W3"] != clusters["W1"])
check("topics: segment facts are never clustered", "fs.segments.0" not in clusters)

inp = test_planning.make_inputs()
inp.items["W9"] = test_planning.item("W9", "evidence", "Acme launched a new product for its customers in the market.", {"trends", "key_players"}, tags=["trends"], source="Dup Site")
inp.items["W1"]["text"] = "Acme launched a new product for its customers in the market."
raw = test_planning.good_plan()
next(sec for sec in raw["sections"] if sec["sop"] == "key_players")["facts"] = ["fs.players", "W9"]
plan = test_planning.plan_of(raw, inp)
used = [f for sec in plan["sections"] for f in sec["facts"]]
check("planner: a fact repeating a point already in the plan is dropped", "W1" in used and "W9" not in used, str(used))

# a geographic split is never a sub-segment
inp = test_planning.make_inputs()
inp.segments.append({"id": "fs.segments.2", "type": "By Region", "items": ["North America", "Europe"], "figures": {},
                     "dominant": {"name": "North America", "basis": "stated", "quote": "q"}})
raw = test_planning.good_plan()
sub = next(sec for sec in raw["sections"] if sec["sop"] == "subsegment")
sub["sub_segments"] = [{"split": "By Region", "name": "North America"}, {"split": "By Type", "name": "Cloud Based"}]
sub_plan = next(sec for sec in test_planning.plan_of(raw, inp)["sections"] if sec["sop"] == "subsegment")
check("planner: a By Region split is never chosen as a sub-segment",
      all(v["split"] != "By Region" for v in sub_plan["sub_segments"]) and len(sub_plan["sub_segments"]) == 2, str(sub_plan["sub_segments"]))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all fact-model-v2 tests passed")
