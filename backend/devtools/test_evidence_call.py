"""Offline test for the Evidence step's ONE preparation call (no network, no real LLM calls).

Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_evidence_call.py

The publisher points, the search demand and the market search queries are one call answered as
{"publisher": ..., "demand": ..., "queries": ...}. Covers: the prompt holds the three prompts verbatim as three jobs and formats,
the answer's three parts go through the same validation the separate calls always used (so each part equals what the separate call's
code makes of the same answer), a failed or partial answer degrades part by part, and the old functions still work.
"""
import copy
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand  # noqa: E402
import evidence_call  # noqa: E402
import facts  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import publisher  # noqa: E402
import sections  # noqa: E402
from docx_io import Link, Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_evidence_call")
MARKET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


BODY = ("A typical online store sells about 40 products in its first month. "
        "Turning a messy online page into plain readable text helps real shoppers find what they are after. "
        "The global widget market is estimated at USD 1.56 billion in 2026.")
ARTICLE = SourceArticle(title="Tools", paragraphs=[Paragraph(index=0, text=BODY, links=[]),
                                                   Paragraph(index=1, text="Teams can ask Acme Corp.", links=[Link(anchor="Acme Corp", url="https://acmecorp.com/x")])])
SPONSOR = ARTICLE.paragraphs[1]
FS = facts._empty_fact_sheet()
FS["drivers"] = [{"id": "fs.drivers.0", "text": "Demand for widget automation grows", "quote": "q", "historical": False}]
FS["players"] = ["Acme Corp"]
PLAN = sections.section_search_plan(FS, SimpleNamespace(name=MARKET), ["Acme Corp"], [])

PUBLISHER_PART = {"intro_angle": "The article explains how reading messy web pages the way a person does helps stores and their shoppers connect.",
                  "points": ["A typical online store sells about 40 products in its first month, so early wins matter a lot.",
                             "The global widget market is growing quickly across regions."]}
DEMAND_PART = {"questions": [{"intent": "growth", "question": f"How fast is the {MARKET} growing?"},
                             {"intent": "pricing", "question": "How much does it cost?"}],
               "terms": ["cloud based", "Cloud Based"],
               "long_tail": [{"keyword": "widget automation for cloud based teams", "fact": "fs.drivers.0", "why": "x"},
                             {"keyword": "widget automation for cloud based teams", "fact": "fs.nothing", "why": "x"}]}
QUERIES_PART = {"queries": {"trends": ['"zzz widget services" automation', '"zzz widget services" adoption'], "market": ['"zzz widget services" news'],
                            "segmentation": ["ignored"]},
                "source_groups": ["not-a-pack"], "topic_terms": ["Widget Maker", "widget maker"]}
ANSWER = {"publisher": PUBLISHER_PART, "demand": DEMAND_PART, "queries": QUERIES_PART}

# ------------------------------------------------------------------------------------------------ the prompt
check("prompt: each job's own prompt is in the one prompt verbatim, in order",
      prompts.PUBLISHER_POINTS_USER in prompts.EVIDENCE_PREP_USER and prompts.SEARCH_DEMAND_USER in prompts.EVIDENCE_PREP_USER
      and prompts.SEARCH_SECTION_QUERIES_USER in prompts.EVIDENCE_PREP_USER
      and prompts.EVIDENCE_PREP_USER.index(prompts.PUBLISHER_POINTS_USER) < prompts.EVIDENCE_PREP_USER.index(prompts.SEARCH_DEMAND_USER)
      < prompts.EVIDENCE_PREP_USER.index(prompts.SEARCH_SECTION_QUERIES_USER))
check("prompt: each job's own role is in the system prompt verbatim",
      all(s in prompts.EVIDENCE_PREP_SYSTEM for s in (prompts.PUBLISHER_POINTS_SYSTEM, prompts.SEARCH_DEMAND_SYSTEM, prompts.SEARCH_QUERIES_SYSTEM)))
check("prompt: the answer is one object with the three keys", '"publisher"' in prompts.EVIDENCE_PREP_USER and '"demand"' in prompts.EVIDENCE_PREP_USER
      and '"queries"' in prompts.EVIDENCE_PREP_USER)

seen: dict = {}
calls: list[str] = []


def fake_chat(system, user, **kw):
    calls.append(kw.get("call_name"))
    seen.update(system=system, user=user, kw=kw)
    return copy.deepcopy(ANSWER)


orig = llm.chat_json
llm.chat_json = fake_chat
try:
    res = evidence_call.run_evidence_prep(ARTICLE, SPONSOR, MARKET, FS, ["Acme Corp"], ["Software"], PLAN, ["best widget services"], LOG)
finally:
    llm.chat_json = orig
user = seen["user"]
check("call: ONE model call, named evidence_prep, with room for three answers", calls == ["evidence_prep"] and seen["kw"].get("max_tokens", 0) >= 4000, str(calls))
check("call: the prompt shows the article and the sponsor paragraph, the report facts and the real searches, and the section targets",
      "40 products" in user and "Teams can ask Acme Corp." in user and "fs.drivers.0" in user and "best widget services" in user
      and "Demand for widget automation grows" in user and "Acme" in user and "SOURCE GROUPS" in user, user[:200])
check("call: no placeholder is left unfilled in the prompt", "{market_name}" not in user and "{section_targets}" not in user and "{facts}" not in user)

# ----------------------------------------------------- each part is what the separate call's own validation makes of it
body = publisher.article_text(ARTICLE, LOG)
check("publisher part: equals the publisher validation of the same answer (a market-fact point is dropped)",
      res["publisher"] == publisher.normalize_points(PUBLISHER_PART, body, pipeline._brand_terms(SPONSOR), LOG)
      and [p["id"] for p in res["publisher"]["points"]] == ["P1"] and res["publisher"]["intro_angle"], str(res["publisher"]))
exp_demand = {"questions": demand.normalize_questions(DEMAND_PART["questions"], MARKET, LOG), "terms": demand.normalize_terms(DEMAND_PART["terms"], MARKET, LOG),
              "long_tail": demand.normalize_longtails(DEMAND_PART["long_tail"], facts.flatten_fact_sheet(FS), MARKET, LOG, market_core=MARKET)}
check("demand part: equals the demand validation of the same answer (an unknown kind, a repeated term and a long-tail on an unknown fact are dropped)",
      res["demand"] == exp_demand and len(res["demand"]["questions"]) == 1 and res["demand"]["terms"] == ["cloud based"]
      and len(res["demand"]["long_tail"]) == 1, str(res["demand"]))
exp_queries = pipeline.search_queries_from_result(QUERIES_PART, MARKET, LOG, PLAN)
check("queries part: equals what prepare_search_queries makes of the same answer (grouped by section, topic terms cleaned, unknown group dropped)",
      res["queries"] == exp_queries and isinstance(res["queries"][0], dict) and res["queries"][0]["trends"] == QUERIES_PART["queries"]["trends"]
      and res["queries"][1] == [] and res["queries"][2] == ["widget maker"] and set(res["queries"][0]) == set(sections.QUERY_SECTIONS), str(res["queries"]))

# ------------------------------------------------------------------------------------------------ degradation
def boom(*a, **k):
    raise llm.LLMError("down")


llm.chat_json = boom
try:
    failed = evidence_call.run_evidence_prep(ARTICLE, SPONSOR, MARKET, FS, ["Acme Corp"], [], PLAN, [], LOG)
finally:
    llm.chat_json = orig
check("failure: no points, empty demand pools and the deterministic queries grouped by section, never an exception",
      failed["publisher"] == {"intro_angle": "", "points": []} and failed["demand"] == {"questions": [], "terms": [], "long_tail": []}
      and isinstance(failed["queries"][0], dict) and failed["queries"][0]["market"] and failed["queries"][1:] == ([], []), str(failed))

partial = evidence_call.parts_from_raw({"publisher": PUBLISHER_PART}, SPONSOR, MARKET, FS, body, PLAN, LOG)
check("partial answer: a part the model left out is empty for that part alone",
      partial["publisher"]["points"] and partial["demand"] == {"questions": [], "terms": [], "long_tail": []}
      and partial["queries"][0]["market"] and partial["queries"][2] == [])
garbled = evidence_call.parts_from_raw({"publisher": "nope", "demand": ["x"], "queries": 3}, SPONSOR, MARKET, FS, body, PLAN, LOG)
check("garbled answer: parts that are not objects behave like missing ones",
      garbled["publisher"] == {"intro_angle": "", "points": []} and garbled["demand"]["questions"] == [] and garbled["queries"][0]["market"])
check("non-object answer: all three parts degrade, nothing raises",
      evidence_call.parts_from_raw(None, SPONSOR, MARKET, FS, body, PLAN, LOG)["publisher"]["points"] == [])

# ---------------------------------------------------------------------------- the three old functions still work
sep: list[str] = []


def separate_chat(system, user, **kw):
    sep.append(kw.get("call_name"))
    return {"search_demand": DEMAND_PART, "publisher_points": PUBLISHER_PART, "search_queries": QUERIES_PART}[kw["call_name"]]


llm.chat_json = separate_chat
try:
    old_pub = publisher.run_publisher_points(ARTICLE, SPONSOR, MARKET, LOG)
    old_demand = demand.run_search_demand(MARKET, FS, LOG, real_queries=["best widget services"])
    old_queries = pipeline.prepare_search_queries(MARKET, ["Acme Corp"], LOG, [], PLAN)
finally:
    llm.chat_json = orig
check("old chain: the three separate calls are still three calls and give the same parts as the one call",
      sorted(sep) == ["publisher_points", "search_demand", "search_queries"] and old_pub == res["publisher"] and old_demand == res["demand"]
      and old_queries == res["queries"], str(sep))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all evidence-call tests passed")
