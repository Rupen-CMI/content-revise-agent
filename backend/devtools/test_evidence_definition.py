"""Offline test: ONE evidence definition, in the same words, in the query rules, the query intro, the section targets, SELECT's
section rules and SELECT's tests; and the code backstop rejects a person's routine, an endorsement or a recommendation.
Run from backend/: python devtools/test_evidence_definition.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pipeline  # noqa: E402
import prompts  # noqa: E402
import sections  # noqa: E402

FAILED: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


E = prompts.EVIDENCE_DEFINITION
targets = sections.format_targets({"trends": [{"text": "x"}], "subsegment": [], "market": [], "us": [], "key_players": []})
for name, text in (("query rules", prompts._QUERY_RULES_NATURAL), ("query intro", prompts._INTRO_NATURAL), ("section targets", targets),
                   ("SELECT section rules", prompts.SELECT_SECTION_RULES), ("SELECT tests", prompts.SELECT_USER),
                   ("whole query prompt", prompts.SEARCH_SECTION_QUERIES_NATURAL_USER)):
    check(f"definition, same words: {name}", E in text)
check("definition names both kinds and the exclusions", all(w in E for w in ("measured statistic", "dated market development", "celebrity routine",
      "endorsement", "recommendation or ranking list", "advice or how-to", "opinion piece", "describing itself")))
check("no braces in it (it is inserted into formatted templates)", "{" not in E and "}" not in E)
check("human-style query rules are in the natural query rules", "AS AN ANALYST OR A JOURNALIST" in prompts._QUERY_RULES_NATURAL)
prompts.SELECT_SECTION_RULES.format(sub_segment_lines="", min_trends=1, min_sub=1, min_us=1, min_players=1)

BAD = ["Actress Nia Long credits her skincare routine to Estee Lauder, saying the brand keeps her skin glowing.",
       "Plastic surgeons named SkinCeuticals the skincare line they recommend most, according to a 2026 survey.",
       "Influencer says her favourite moisturizer is from Brand X."]
GOOD = ["Target reported beauty sales grew 9% in the first quarter.", "Estee Lauder acquired Tom Ford in 2022 and opened 40 stores.",
        "Operators added 4,382 new charging ports across 806 stations in the second quarter."]
check("backstop: person preference, endorsement and recommendation facts match", all(pipeline._NON_EVENT_RE.search(f) for f in BAD))
check("backstop: a measured figure or a dated development is not caught", not any(pipeline._NON_EVENT_RE.search(f) for f in GOOD))

print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
