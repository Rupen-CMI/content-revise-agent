"""Offline test for the search-demand stage (no network, no real LLM calls).

Run from backend/: python devtools/test_demand.py

Covers demand.normalize_questions / normalize_terms / normalize_longtails (what survives of the model's answer),
demand.mark_answerability (which questions the fact sheet and evidence can actually answer) and run_search_demand with
the LLM stubbed. Also checks that the prompt holds no sample questions.
"""
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand  # noqa: E402
import facts  # noqa: E402
import llm  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_demand")
MARKET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def q(intent: str, question: str) -> dict:
    return {"intent": intent, "question": question}


GOOD = [
    q("growth", "How fast is the Zzz Widget Services Market growing?"),
    q("size", "How big is the Zzz Widget Services Market?"),
    q("drivers", "What drives growth in the Zzz Widget Services Market?"),
    q("region", "Which region leads the Zzz Widget Services Market?"),
    q("players", "Who are the top companies in the Zzz Widget Services Market?"),
]
out = demand.normalize_questions(GOOD, MARKET, LOG)
check("questions: good ones all kept", len(out) == 5, str([x["question"] for x in out]))
check("questions: ordered by kind (size before growth) with ids Q1..Qn",
      [x["intent"] for x in out] == ["size", "growth", "drivers", "region", "players"] and out[0]["id"] == "Q1" and out[4]["id"] == "Q5")
check("questions: each carries the section its kind belongs to", out[0]["section"] == "report_context" and out[3]["section"] == "country_us" and out[4]["section"] == "key_players")

check("questions: an unknown kind is dropped", demand.normalize_questions([q("pricing", "How much does the Zzz Widget Services Market cost?")], MARKET, LOG) == [])
check("questions: a question that does not name the market is dropped",
      demand.normalize_questions([q("trends", "What is changing in widgets right now?")], MARKET, LOG) == [])
check("questions: a definition is dropped", demand.normalize_questions([q("size", "What is the Zzz Widget Services Market?")], MARKET, LOG) == [])
check("questions: too short is dropped", demand.normalize_questions([q("size", "Zzz widget services size?")], MARKET, LOG) == [])
check("questions: too long is dropped",
      demand.normalize_questions([q("size", "How big is the Zzz Widget Services Market " + "and so on " * 8 + "?")], MARKET, LOG) == [])
check("questions: a missing question mark is added",
      demand.normalize_questions([q("size", "How big is the Zzz Widget Services Market today")], MARKET, LOG)[0]["question"].endswith("?"))
check("questions: a natural short form of the market's name is accepted",
      len(demand.normalize_questions([q("drivers", "What is driving the widget services market?")], MARKET, LOG)) == 1)

twin = demand.normalize_questions([
    q("drivers", "What drives growth in the Zzz Widget Services Market?"),
    q("drivers", "What drives the growth of the Zzz Widget Services Market?"),
], MARKET, LOG)
check("questions: the same need reworded is dropped", len(twin) == 1, str([x["question"] for x in twin]))

three = demand.normalize_questions([
    q("trends", "What are the latest trends in the Zzz Widget Services Market?"),
    q("trends", "Which technologies are reshaping the Zzz Widget Services Market?"),
    q("trends", "How are buyers changing in the Zzz Widget Services Market?"),
], MARKET, LOG)
check("questions: at most two per kind", len(three) <= 2, str(len(three)))
check("questions: non-list input is survived", demand.normalize_questions(None, MARKET, LOG) == [] and demand.normalize_questions("x", MARKET, LOG) == [])

# ------------------------------------------------------------------ related terms
terms = demand.normalize_terms(
    ["Cloud Based", "browser extension", "zzz widget services market", "browser extension", "<segment>", "a b c d e", "installable software", ""],
    MARKET, LOG)
check("terms: lowercased, deduped, no market name, no placeholders, at most four words",
      terms == ["cloud based", "browser extension", "installable software"], str(terms))
check("terms: capped", len(demand.normalize_terms([f"term{c}" for c in "abcdefghijklmnop"], MARKET, LOG)) == demand._TERM_MAX)

# ------------------------------------------------------------------ long-tail searches
FLAT = [{"id": "fs.segments.0", "text": "Segmentation type: x"}, {"id": "fs.regions.leader", "text": "Leading region: y"}]
lt = demand.normalize_longtails([
    {"keyword": "widget services for cloud based teams by region", "fact": "fs.regions.leader", "why": "regional buyers"},
    {"keyword": "zzz widget services market growth", "fact": "fs.segments.0", "why": "x"},          # market name + one word
    {"keyword": "browser extension widget tools", "fact": "fs.nope", "why": "x"},                  # unknown fact
    {"keyword": "widget [region] outlook", "fact": "fs.segments.0", "why": "x"},                    # placeholder
    {"keyword": "widgets", "fact": "fs.segments.0", "why": "x"},                                    # too short
    {"keyword": "Widget Services For Cloud Based Teams By Region", "fact": "fs.regions.leader", "why": "dup"},  # duplicate
    {"keyword": "installable widget software and segment trends", "fact": "fs.segments.0", "why": "buyers"},
], FLAT, MARKET, LOG)
check("long-tail: only grounded, specific, placeholder-free, unique searches survive",
      [x["keyword"] for x in lt] == ["widget services for cloud based teams by region",
                                      "installable widget software and segment trends"], str([x["keyword"] for x in lt]))
check("long-tail: records keep the older {keyword, intent, why} shape with an informational intent",
      all(x["intent"] == "informational" and {"keyword", "intent", "why"} <= set(x) for x in lt))

lt2 = demand.normalize_longtails([
    {"keyword": "web scraping for market research market share", "fact": "fs.segments.0", "why": "x"},  # "market" repeats
    {"keyword": "web scraping services in north america", "fact": "fs.segments.0", "why": "regional buyers"},
    {"keyword": "what is driving growth in web scraping services", "fact": "fs.segments.0", "why": "x"},  # reads as a question
    {"keyword": "how are buyers choosing web scraping services", "fact": "fs.regions.leader", "why": "x"},  # reads as a question
    {"keyword": "why banks are adopting wealth management platforms", "fact": "fs.regions.leader", "why": "buyers"},  # opens with an interrogative
    {"keyword": "cloud versus on premises in wealth management platforms", "fact": "fs.segments.0", "why": "buyers"},
], FLAT, MARKET, LOG)
check("long-tail: a repeating word or a question-shaped start is dropped; natural phrases even with no repeated word are kept",
      [x["keyword"] for x in lt2] == ["web scraping services in north america",
                                      "cloud versus on premises in wealth management platforms"], str([x["keyword"] for x in lt2]))

stack = demand.normalize_longtails([
    {"keyword": "cloud based widget services marketplace pricing", "fact": "fs.segments.0", "why": "x"},   # six words in a row with no joining word
    {"keyword": "widget services for cloud based teams", "fact": "fs.segments.0", "why": "x"},             # the same idea, joined
    {"keyword": "top companies widgets and services market", "fact": "fs.segments.0", "why": "x"},         # a join inside the run saves it
], FLAT, MARKET, LOG)
check("long-tail: a run of five or more words with no joining word is rejected; a joined phrase is kept",
      [x["keyword"] for x in stack] == ["widget services for cloud based teams", "top companies widgets and services market"],
      str([x["keyword"] for x in stack]))

lt3 = demand.normalize_longtails([
    {"keyword": "how vendors price plans", "fact": "fs.segments.0", "why": "x"},     # opens with an interrogative + noun phrase
    {"keyword": "why buyers switch tools", "fact": "fs.segments.0", "why": "x"},     # opens with an interrogative + noun phrase
    {"keyword": "which vendors sell widgets", "fact": "fs.segments.0", "why": "x"},  # opens with an interrogative + noun phrase
    {"keyword": "software for small teams", "fact": "fs.segments.0", "why": "x"},    # a noun-phrase search that fits a statement
], FLAT, MARKET, LOG)
check("long-tail: any phrase that opens with an interrogative word is dropped; a noun-phrase search is kept",
      [x["keyword"] for x in lt3] == ["software for small teams"], str([x["keyword"] for x in lt3]))

CORE_MARKET = "Global Widget Management Platform Market"
CORE = "widget management platform"
lt4 = demand.normalize_longtails([
    {"keyword": "widget management platform used by banks", "fact": "fs.regions.leader", "why": "buyers"},
    {"keyword": "cost of maintaining widget management platform", "fact": "fs.segments.0", "why": "costs"},
    {"keyword": "widget management platform size in north america", "fact": "fs.regions.leader", "why": "regions"},
    {"keyword": "high net worth clients digital widget management platform us", "fact": "fs.segments.0", "why": "x"},
    {"keyword": "cloud vendors enterprise adoption today", "fact": "fs.segments.0", "why": "x"},
    {"keyword": "enterprise adoption rates forecast by region", "fact": "fs.segments.0", "why": "adoption"},
], FLAT, CORE_MARKET, LOG, market_core=CORE_MARKET)
check("long-tail: the market's core phrase is ONE noun unit, so phrases built on it are kept while a five-word pile "
      "(with or without the core) is rejected and a four-word run without the core is kept",
      [x["keyword"] for x in lt4] == ["widget management platform used by banks",
                                      "cost of maintaining widget management platform",
                                      "widget management platform size in north america",
                                      "enterprise adoption rates forecast by region"],
      str([x["keyword"] for x in lt4]))

# --- the kept pool caps at 8, preferring fact diversity when it must cut
FIVE_FACTS = [{"id": f"fs.f{i}", "text": f"Fact {i}"} for i in range(5)]
# 10 valid candidates: four rest on fs.f0, the other six spread over fs.f1..fs.f4
TEN = [{"keyword": f"widget services for banks in item {letter} growth",
        "fact": ["fs.f0", "fs.f0", "fs.f0", "fs.f0", "fs.f1", "fs.f1", "fs.f2", "fs.f2", "fs.f3", "fs.f4"][i],
        "why": "buyers"}
       for i, letter in enumerate("abcdefghij")]
kept10 = demand.normalize_longtails(TEN, FIVE_FACTS, MARKET, LOG)
check("long-tail: more than 8 valid candidates are cut to the cap of 8", len(kept10) == 8, str(len(kept10)))
check("long-tail: the cap keeps at most 2 phrases per fact id when other facts have candidates",
      all(sum(1 for x in kept10 if x["fact"] == f"fs.f{i}") <= 2 for i in range(5))
      and all(any(x["fact"] == f"fs.f{i}" for x in kept10) for i in range(5)),
      str([(x["keyword"], x["fact"]) for x in kept10]))
# the diversity preference must never fall below the cap: with only one fact id, up to 8 are still kept
ONE_FACT = [{"id": "fs.f0", "text": "Fact 0"}]
NINE_ONE = [{"keyword": f"widget services for banks in item {letter} growth", "fact": "fs.f0", "why": "buyers"}
            for letter in "abcdefghij"]
kept1 = demand.normalize_longtails(NINE_ONE, ONE_FACT, MARKET, LOG)
check("long-tail: with only one fact id, up to 8 phrases on that sole fact are still kept",
      len(kept1) == 8 and all(x["fact"] == "fs.f0" for x in kept1), str([x["keyword"] for x in kept1]))

def fs_with(**fields):
    fs = facts.validate_fact_sheet({}, "page", MARKET, LOG)
    fs.update(fields)
    return fs


qs = demand.normalize_questions(GOOD, MARKET, LOG)
res = demand.mark_answerability([dict(x) for x in qs], fs_with(), [], LOG)
check("answerable: an empty fact sheet with no evidence answers nothing", not any(x["answerable"] for x in res))

res = demand.mark_answerability([dict(x) for x in qs], fs_with(
    size={"id": "fs.size"}, cagr={"id": "fs.cagr"}, drivers=[{"id": "fs.drivers.0"}],
    regions={"leader": {"id": "fs.regions.leader"}, "fastest": None}, players=["A", "B"]), [], LOG)
check("answerable: size, growth, drivers, region and players follow their fact-sheet fields", all(x["answerable"] for x in res))

res = demand.mark_answerability([dict(x) for x in qs], fs_with(drivers=[{"id": "fs.drivers.0"}]), [], LOG)
by = {x["intent"]: x["answerable"] for x in res}
check("answerable: a missing size or growth figure makes those questions unanswerable", not by["size"] and not by["growth"] and by["drivers"])

tech = demand.normalize_questions([q("technology_impact", "How is AI changing the Zzz Widget Services Market?"),
                                   q("us", "How big is the Zzz Widget Services Market in the United States?")], MARKET, LOG)
res = demand.mark_answerability([dict(x) for x in tech], fs_with(), [{"tags": ["trends"]}], LOG)
by = {x["intent"]: x["answerable"] for x in res}
check("answerable: a technology question needs trends evidence; a U.S. question needs U.S. facts or evidence",
      by["technology_impact"] and not by["us"], str(by))
res = demand.mark_answerability([dict(x) for x in tech], fs_with(), [{"tags": ["us"]}], LOG)
check("answerable: U.S. evidence answers the U.S. question", {x["intent"]: x["answerable"] for x in res}["us"])

# ------------------------------------------------------------------ the call (LLM stubbed)
def fake_chat(system, user, **kw):
    return {"questions": GOOD + [q("pricing", "How much does it cost?")], "terms": ["cloud based"],
            "long_tail": [{"keyword": "widget services for cloud based teams by region", "fact": "fs.regions.leader", "why": "x"}]}


orig = llm.chat_json
llm.chat_json = fake_chat
try:
    fs = fs_with(regions={"leader": {"id": "fs.regions.leader", "name": "North America", "basis": "", "quote": "q", "historical": False}, "fastest": None})
    res = demand.run_search_demand(MARKET, fs, LOG)
    check("call: questions, terms and long-tails come back validated", len(res["questions"]) == 5 and res["terms"] == ["cloud based"] and len(res["long_tail"]) == 1)

    def boom(*a, **k):
        raise llm.LLMError("down")
    llm.chat_json = boom
    res = demand.run_search_demand(MARKET, fs, LOG)
    check("call: a model failure returns empty pools instead of raising", res == {"questions": [], "terms": [], "long_tail": []})
finally:
    llm.chat_json = orig

# ------------------------------------------------------------------ the prompt carries framing, not samples
formatted = prompts.SEARCH_DEMAND_USER.format(market_name=MARKET, facts="- fs.size: x")
check("prompt: formats and names the market", MARKET in formatted)
check("prompt: lists every kind of need", all(f"- {k}:" in formatted for k in demand.INTENTS))
sample_like = [l for l in formatted.splitlines() if re.match(r"\s*(How|What|Which|Who|Where|Why)\b[^.]*\?\s*$", l)]
check("prompt: holds no sample question lines", not sample_like, str(sample_like))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all demand tests passed")
