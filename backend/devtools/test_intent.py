"""Offline test for search-intent question harvesting (no network, no LLM calls).

Run from backend/: python devtools/test_intent.py

Covers: the generic-question filter, the scoring heuristic, dedupe, and the subheading candidate
ranking — not the actual autosuggest HTTP calls (intent.harvest_questions), which need the network
and are best-effort by design (see pipeline.run_harvest_questions). The FAQ block is unrelated to
this module now (see devtools/test_faq.py) — it's generated separately at write time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import intent  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


CORE = "wealth management platform"

# ------------------------------------------------------------------ is_too_generic
check("generic: 'what is <core> market?' flagged", intent.is_too_generic("what is wealth management platform market?", CORE))
check("generic: 'What Are Wealth Management Platform?' flagged (case-insensitive)",
      intent.is_too_generic("What Are Wealth Management Platform?", CORE))
check("not generic: a substantive follow-on is not flagged",
      not intent.is_too_generic("what are the top challenges in wealth management platforms?", CORE))
check("not generic: a differently-shaped question is not flagged",
      not intent.is_too_generic("how much does a wealth management platform cost?", CORE))

# ------------------------------------------------------------------ score_question
too_short = intent.score_question("wealth platform", CORE)
check("score: a too-short completion (< 4 words) scores very low", too_short <= -4)

generic_score = intent.score_question("what is wealth management platform market?", CORE)
specific_score = intent.score_question("what are the top challenges in wealth management platforms?", CORE)
check(
    "score: a specific, signal-word-bearing question outscores the generic 'what is X market' shape",
    specific_score > generic_score,
    f"generic={generic_score}, specific={specific_score}",
)

run_on = intent.score_question(
    "what are the top challenges and opportunities and risks and trends in wealth management platform adoption across every region globally",
    CORE,
)
check("score: a very long run-on completion is penalised relative to a well-sized one", run_on < specific_score)

# ---- the two real failure shapes (Wealth Management Platform Market harvest): scored, gated or both
market_q = intent.score_question("Why is the wealth management platform market growing?", CORE)
service_q = intent.score_question("What are the benefits of wealth management?", CORE)
buyer_q = intent.score_question("What is the best wealth management platform?", CORE)
check("score: a market-growth question outscores the service-level 'benefits of' question", market_q > service_q,
      f"market={market_q}, service={service_q}")
check("score: a buying-intent 'best platform' question is penalised, not just outranked", buyer_q < 3.0, f"buyer={buyer_q}")
check("score: a question naming neither the market nor its subject scores very low",
      intent.score_question("How do I cook a vegetarian lasagna?", CORE) <= -3)

# ------------------------------------------------------------------ the subheading gate (_is_market_question)
check("gate: a service-level 'benefits of' question fails admission even with its subject words present",
      not intent._is_market_question("What are the benefits of wealth management?", CORE))
check("gate: a buying-intent 'best <subject>' question fails admission",
      not intent._is_market_question("What is the best wealth management platform?", CORE))
check("gate: a market-frame question passes without subject words", intent._is_market_question("How big is the market?", "x eth inc market"))
check("gate: subject words alone (no frame, no angle) fail — service-level by construction",
      not intent._is_market_question("Wealth management is what exactly", CORE))
check("gate: subject words plus a market angle pass", intent._is_market_question("Who leads the wealth management platform companies?", CORE))

# ------------------------------------------------------------------ dedupe
deduped = intent.dedupe([
    "What are the top challenges in wealth management platforms?",
    "what are the top challenges in wealth management platforms",  # same question, different case/punctuation
    "How do AI and robo-advisors impact client retention?",
    "",
    "   ",
])
check("dedupe: case/punctuation-only duplicate collapsed", len(deduped) == 2, str(deduped))
check("dedupe: blank entries dropped", all(d.strip() for d in deduped))

# ------------------------------------------------------------------ rank_questions / subheading candidates
raw_questions = [
    "what is wealth management platform market?",            # generic -> excluded
    "what are the top challenges in wealth management platforms?",  # specific -> kept
    "how do ai and robo-advisors impact client retention in wealth management platforms?",
    "which regions show the highest adoption of wealth management platform solutions?",
    "why is the wealth management platform market growing?",  # market-frame -> kept, ranked high
    "why is the wealth management platform market growing fast in 2026 us healthcare settings what does it mean",  # run-on
    "what are the benefits of wealth management?",            # service-level -> gate excludes
    "what is the best wealth management platform?",           # buying-intent -> gate excludes
    "how do I cook a vegetarian lasagna?",                    # unrelated -> excluded
    "wm",  # too short, should be filtered out
]
ranked = intent.rank_questions(raw_questions, CORE)
check("rank: too-short junk never reaches the pool", "wm" not in ranked["subheading_candidates"])
check("rank: the generic 'what is X market' question is excluded from subheading candidates",
      not any(intent.is_too_generic(q, CORE) for q in ranked["subheading_candidates"]))
check("rank: a substantive, specific question reaches the subheading pool",
      any("top challenges" in q.lower() for q in ranked["subheading_candidates"]))
check("rank: the service-level 'benefits of' question is kept OUT of the pool (the real failure)",
      not any("benefits of wealth management" in q.lower() for q in ranked["subheading_candidates"]))
check("rank: the buying-intent 'best platform' question is kept OUT of the pool (the real failure)",
      not any("best wealth management" in q.lower() for q in ranked["subheading_candidates"]))
check("rank: an unrelated question naming neither market nor subject never reaches the pool",
      not any("lasagna" in q.lower() for q in ranked["subheading_candidates"]))
check("rank: a market-frame question reaches the subheading pool",
      any(q.lower().startswith("why is the wealth management platform market") for q in ranked["subheading_candidates"]),
      str(ranked["subheading_candidates"]))
check("rank: the market-frame question outranks the run-on market-frame completion",
      ranked["subheading_candidates"].index(
          next(q for q in ranked["subheading_candidates"] if q.lower().startswith("why is the wealth management platform market")))
      < ranked["subheading_candidates"].index(
          next(q for q in ranked["subheading_candidates"] if "what does it mean" in q.lower())),
      str(ranked["subheading_candidates"]))

# ------------------------------------------------------------------ prompt formatting sanity (no KeyError from the new template)
formatted = prompts.INTENT_USER.format(
    market_name="Wealth Management Platform Market",
    topic_summary="A guest post about choosing a wealth management platform.",
    subheading_candidates="- what are the top challenges in wealth management platforms?",
    subheading_count=2,
)
check("prompt: INTENT_USER formats without a KeyError", isinstance(formatted, str) and "Wealth Management Platform Market" in formatted)

# ------------------------------------------------------------------ LLM-drafted subheading questions (pipeline._normalize_subheading_questions)
import logging
import pipeline as _pl
_drafted = {"subheading_questions": [
    {"question": "What is driving growth in the Zzz Widget Market?", "section": "trends"},
    {"question": "What are the benefits of widgets?", "section": "country_us"},            # product-level: dropped
    {"question": "Zzz Widget Market leaders", "section": "key_players"},                    # not a question: dropped
    {"question": "Who leads the Zzz Widget Market in the U.S.?", "section": "trends"},      # section already used: dropped
    {"question": "Who leads the Zzz Widget Market in the U.S.?", "section": "bogus"},       # invalid section: dropped
]}
_out = _pl._normalize_subheading_questions(_drafted, logging.getLogger("t"), "Zzz Widget Market")["subheading_questions"]
check("drafted: only the market-shaped, well-formed question survives", [q["question"] for q in _out] == ["What is driving growth in the Zzz Widget Market?"], str(_out))
check("drafted: it carries id and section", _out and _out[0]["id"] == "Q1" and _out[0]["section"] == "trends")
import prompts as _pr
check("prompt: QUESTION_DRAFT_USER formats", "Zzz Widget Market" in _pr.QUESTION_DRAFT_USER.format(
    market_name="Zzz Widget Market", topic_summary="x", report_text="x", subheading_count=2, core="zzz widget"))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all intent tests passed")
