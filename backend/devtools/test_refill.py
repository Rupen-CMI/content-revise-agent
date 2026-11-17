"""Offline test for the no-empty-section rule: the U.S. section is never an editor note, an emptied section is written again
(one targeted call, then a fact-sheet fallback built in code), and the final check rejects empty sections and placeholders.

Run from backend/: python devtools/test_refill.py
"""
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import llm  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402
import writer  # noqa: E402
from test_planning import S, good_plan, item, make_inputs, plan_of  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_refill")
REPORT = SimpleNamespace(name="Zzz Widget Services Market")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def plan_for(inp: planning.PlanInputs) -> dict:
    raw = good_plan()
    raw["sections"][1]["facts"] = ["fs.drivers.0"]
    raw["sections"][4]["facts"] = []
    for s in raw["sections"]:
        s["facts"] = [f for f in s["facts"] if f in inp.items]
    return plan_of(raw, inp)


# ------------------------------------------------------------------ planning never yields us_missing
headline = make_inputs()
for fid in [f for f, it in headline.items.items() if it["kind"] not in ("size", "cagr", "players")]:
    del headline.items[fid]
headline.longtails = []
p = plan_for(headline)
check("planning: a headline-only sheet writes the U.S. section from the headline figures",
      S(p, "country_us")["length"] == "us_from_report" and "fs.size" in S(p, "country_us")["facts"], str(S(p, "country_us")))

empty = make_inputs()
empty.items.clear()
empty.longtails = []
p = plan_for(empty)
check("planning: a totally empty sheet writes the U.S. section as a qualitative analysis with no facts",
      S(p, "country_us")["length"] == "us_from_report" and S(p, "country_us")["facts"] == [] and S(p, "country_us")["words"] >= 100,
      str(S(p, "country_us")))
check("planning: no section anywhere is us_missing", all(s["length"] != "us_missing" for s in p["sections"]))
check("planning: the shape and the editor line are gone", "us_missing" not in writer._SHAPES)
blk = writer._section_block(5, S(p, "country_us"), empty, {})
check("writer: the empty-sheet U.S. block asks for a qualitative analysis, with no placeholder", "[EDITOR" not in blk and "qualitative" in blk, blk)

# ------------------------------------------------------------------ refill: call returns nothing -> deterministic fallback
inp = make_inputs()
plan = plan_for(inp)
written = {"intro": {"paragraphs": ["Intro."]}, "transition": {"paragraphs": ["Transition."]},
           "sections": [{"sop": s["sop"], "heading": "h", "paragraphs": [f"Text of {s['sop']}."]}
                        for s in plan["sections"] if s["sop"] not in ("country_us", "key_players", "conclusion")]
                       + [{"sop": "conclusion", "heading": "h", "paragraphs": []}]}
sections = writer.assemble_sections(written, plan, LOG)
check("assembly: left-out and empty sections stay empty (no placeholder yet)",
      all(s["paragraphs"] == [] for s in sections if s["sop"] and s["sop"][0] in ("country_us", "key_players", "conclusion")))

calls: list[str] = []


def fake_empty(system, user, **kw):
    calls.append(kw.get("call_name", ""))
    return {"paragraphs": []}


orig = llm.chat_json
llm.chat_json = fake_empty
try:
    filled = writer.refill_empty_sections(sections, plan, inp, REPORT, LOG)
finally:
    llm.chat_json = orig
text = {s["sop"][0]: " ".join(s["paragraphs"]) for s in filled if s["sop"] and s["sop"][0] in planning.ORDER}
check("refill: exactly one call per emptied section, none for the others", sorted(calls) == ["refill_conclusion", "refill_country_us", "refill_key_players"], str(calls))
check("refill: every market section has text", all(text.get(s) for s in planning.ORDER), str(text))
check("refill: no placeholder and no 'no data' wording anywhere",
      not any("[EDITOR" in t or "no data" in t.lower() or "not available" in t.lower() for t in text.values()), str(text))
check("refill: key players fall back to the report's company list", "Companies active in the Zzz Widget Services Market include Acme and Beta" in text["key_players"], text["key_players"])
check("refill: the conclusion falls back to an outlook in words, with no digit", text["conclusion"].startswith("Looking ahead") and not any(c.isdigit() for c in text["conclusion"]), text["conclusion"])
check("refill: the U.S. fallback names no other region", "North America" not in text["country_us"] and "U.S." in text["country_us"] or "United States" in text["country_us"], text["country_us"])
check("refill: sections that had text are untouched", text["trends"] == "Text of trends.")

# the call raising is the same as the call returning nothing
sections = writer.assemble_sections(written, plan, LOG)


def fake_boom(system, user, **kw):
    raise RuntimeError("model down")


llm.chat_json = fake_boom
try:
    filled = writer.refill_empty_sections(sections, plan, inp, REPORT, LOG)
finally:
    llm.chat_json = orig
check("refill: a failed call also ends in the fallback, never a placeholder",
      all(s["paragraphs"] and "[EDITOR" not in " ".join(s["paragraphs"]) for s in filled if s["sop"] and s["sop"][0] in planning.ORDER))

# a totally empty sheet: neutral qualitative paragraphs, no figure
sections = writer.assemble_sections(written, plan_for(empty), LOG)
llm.chat_json = fake_empty
try:
    filled = writer.refill_empty_sections(sections, plan_for(empty), empty, REPORT, LOG)
finally:
    llm.chat_json = orig
check("refill: with an empty fact sheet the fallback is a neutral paragraph per section, with no digit",
      all(s["paragraphs"] and not any(c.isdigit() for c in " ".join(s["paragraphs"])) and "[EDITOR" not in " ".join(s["paragraphs"])
          for s in filled if s["sop"] and s["sop"][0] in ("country_us", "key_players", "conclusion")))

# ------------------------------------------------------------------ refill: the call writes the section, the strips run on it
sections = writer.assemble_sections(written, plan, LOG)


def fake_text(system, user, **kw):
    assert "Return JSON" in user and "[EDITOR" not in user
    return {"paragraphs": ["Suppliers in the Zzz Widget Services Market compete on service and reach. We could not find verified figures. "
                           "Demand reached 98765 units last year."]}


llm.chat_json = fake_text
try:
    filled = writer.refill_empty_sections(sections, plan, inp, REPORT, LOG)
finally:
    llm.chat_json = orig
kp = " ".join(next(s for s in filled if s["sop"] == ["key_players"])["paragraphs"])
check("refill: the written text is kept and the meta and untraced-figure sentences are stripped from it",
      kp == "Suppliers in the Zzz Widget Services Market compete on service and reach.", kp)

# the refill prompt builds for every section
for sop in planning.ORDER:
    ps = {**S(plan, sop)}
    block, extra = writer._refill_block(ps, inp, writer._refill_item_ids(ps, inp))
    prompts.REFILL_USER.format(sop=sop, title="t", market_name=REPORT.name, today="2026-11-17", other_headings="x", block=block, extra=extra)
check("refill: the prompt formats for all seven sections", True)

# ------------------------------------------------------------------ the check
ok = [{"heading": "h", "sop": [s], "paragraphs": ["Text."]} for s in planning.ORDER]
check("check: a full article passes", not checks.v2_empty_section_problems(ok))
bad = ok[:2] + [{"heading": "U.S.", "sop": ["country_us"], "paragraphs": []}] \
    + [{"heading": "KP", "sop": ["key_players"], "paragraphs": ["[EDITOR: write it here.]"]}]
problems = checks.v2_empty_section_problems(bad)
check("check: an empty section and an [EDITOR placeholder both fail it", len(problems) == 2, str(problems))
check("check: the opening (no sop) may not carry a placeholder either", bool(checks.v2_empty_section_problems([{"heading": "", "sop": [], "paragraphs": ["[EDITOR: x]"]}])))

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED:\n  " + "\n  ".join(FAILURES))
    sys.exit(1)
print("\nall refill tests passed")
