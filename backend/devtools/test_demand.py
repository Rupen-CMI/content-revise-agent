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
    {"keyword": "cloud based widget services in north america", "fact": "fs.regions.leader", "why": "regional buyers"},
    {"keyword": "zzz widget services market growth", "fact": "fs.segments.0", "why": "x"},          # market name + one word
    {"keyword": "browser extension widget tools", "fact": "fs.nope", "why": "x"},                  # unknown fact
    {"keyword": "widget [region] outlook", "fact": "fs.segments.0", "why": "x"},                    # placeholder
    {"keyword": "widgets", "fact": "fs.segments.0", "why": "x"},                                    # too short
    {"keyword": "Cloud Based Widget Services In North America", "fact": "fs.regions.leader", "why": "dup"},  # duplicate
    {"keyword": "installable widget software segment trends", "fact": "fs.segments.0", "why": "buyers"},
], FLAT, MARKET, LOG)
check("long-tail: only grounded, specific, placeholder-free, unique searches survive",
      [x["keyword"] for x in lt] == ["cloud based widget services in north america", "installable widget software segment trends"], str([x["keyword"] for x in lt]))
check("long-tail: records keep the older {keyword, intent, why} shape with an informational intent",
      all(x["intent"] == "informational" and {"keyword", "intent", "why"} <= set(x) for x in lt))

# ------------------------------------------------------------------ answerability
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
            "long_tail": [{"keyword": "cloud based widget services in north america", "fact": "fs.regions.leader", "why": "x"}]}


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
