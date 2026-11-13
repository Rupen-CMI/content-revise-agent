"""Offline test for the equivalent forms of a market name (no network, no real LLM calls).

Run from backend/: python devtools/test_market_names.py

A catalog market may be named with a bracketed abbreviation ("Search Engine Optimization (SEO) Services Market");
text about the market — a search question, a planned title, a heading, a keyword phrase — writes the name with the
full words and no bracket, or with the abbreviation in the words' place. Covers market_names.market_name_forms and
every decision that asks whether text names the market or carries the primary keyword: the demand questions, the
planned title's hard checks and the heading check (planning), the keyword research's phrase admission and the
checks' title/primary-keyword counts — including the live-run regression where 10 of 11 most-searched questions and
the planned title were thrown away because "SEO services market" did not match the full name.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import demand  # noqa: E402
import keyword_research  # noqa: E402
import market_names  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_market_names")
SEO = "Search Engine Optimization (SEO) Services Market"
FULL = "Search Engine Optimization Services Market"
SHORT = "SEO Services Market"
WIDGET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def q(intent: str, question: str) -> dict:
    return {"intent": intent, "question": question}


# ------------------------------------------------------------------ the forms themselves
forms = market_names.market_name_forms(SEO)
check("forms: original first, then the full words without the bracket, then the abbreviation in its place",
      forms == [SEO, FULL, SHORT], str(forms))
check("forms: the original form keeps working unchanged", forms[0] == SEO)
check("forms: a name without a bracketed abbreviation has exactly one form, itself",
      market_names.market_name_forms(WIDGET) == [WIDGET]
      and market_names.market_name_forms(FULL) == [FULL])
check("forms: a bracket that abbreviates nothing yields nothing ((Premium) is a qualifier, not an abbreviation)",
      market_names.market_name_forms("Pet Food (Premium) Market") == ["Pet Food (Premium) Market"])
check("forms: a plural abbreviation keeps its written shape ((EVs) of Electric Vehicles)",
      market_names.market_name_forms("Electric Vehicles (EVs) Charging Market")
      == ["Electric Vehicles (EVs) Charging Market", "Electric Vehicles Charging Market", "EVs Charging Market"],
      str(market_names.market_name_forms("Electric Vehicles (EVs) Charging Market")))
check("forms: a leading Global stays in every form",
      market_names.market_name_forms("Global Artificial Intelligence (AI) in Retail Market")
      == ["Global Artificial Intelligence (AI) in Retail Market", "Global Artificial Intelligence in Retail Market",
          "Global AI in Retail Market"])
check("forms: two-letter abbreviations work too",
      market_names.market_name_forms("Battery Electric Vehicle (BEV) Market")
      == ["Battery Electric Vehicle (BEV) Market", "Battery Electric Vehicle Market", "BEV Market"])
check("forms: a bracket before any word and an empty name yield no forms",
      market_names.market_name_forms("(SEO) Services Market") == ["(SEO) Services Market"]
      and market_names.market_name_forms("") == [] and market_names.market_name_forms(None) == [])
check("forms: a single-letter bracket is no abbreviation",
      market_names.market_name_forms("Widgets (X) Market") == ["Widgets (X) Market"])

# ------------------------------------------------------------------ the demand questions (the live regression)
# the questions the live run dropped, verbatim from its log, each judged on its own (as the model's raw list arrives).
# ("What is the future of the SEO services market?" is not in the list: at 9 words the abbreviation form brings it
# under the separate definition-question rule, which the naming decision never reached before.)
LIVE_DROPPED = [
    ("size", "How big is the SEO services market?"),
    ("trends", "What are the latest trends in SEO services?"),
    ("drivers", "What is driving demand for SEO services?"),
    ("technology_impact", "How is AI changing the SEO services market?"),
    ("region", "Which region leads the SEO services market?"),
    ("us", "How big is the SEO services market in the US?"),
    ("players", "Who are the leading SEO services companies?"),
    ("opportunities", "Where are the biggest opportunities in SEO services?"),
]
kept = [question for intent, question in LIVE_DROPPED if demand.normalize_questions([q(intent, question)], SEO, LOG)]
check(f"questions: the live run's {len(LIVE_DROPPED)} dropped questions all name the market now ({len(kept)} kept)",
      len(kept) == len(LIVE_DROPPED), str([x for x in LIVE_DROPPED if x[1] not in kept]))
check("questions: the full-name question is still kept",
      len(demand.normalize_questions([q("growth", "How fast is the search engine optimization services market growing?")],
                                     SEO, LOG)) == 1)
check("questions: a question that names no form of the market is still dropped",
      demand.normalize_questions([q("trends", "What is changing in search visibility right now?")], SEO, LOG) == [])
check("questions: nothing is loosened for a name without brackets (the short words alone are not the market)",
      demand.normalize_questions([q("size", "How big is the widget market?")], WIDGET, LOG) == []
      and demand.normalize_questions([q("trends", "What is changing in widgets right now?")], WIDGET, LOG) == [])
check("questions: a bracket name's full-words form also passes",
      len(demand.normalize_questions([q("size", "How big is the search engine optimization services market?")],
                                     SEO, LOG)) == 1)

# ------------------------------------------------------------------ the planned title (the live regression)
BASIS, KNOWN = ["fs.drivers.0"], {"fs.drivers.0", "W1"}
LIVE_TITLE = "SEO Services Market Shifts Toward AI-Driven Search Visibility"
hard, soft = planning.title_problems(LIVE_TITLE, SEO, BASIS, KNOWN)
check("title: the live run's replaced title passes the hard checks now", hard == [], str(hard))
hard, _ = planning.title_problems("What Is Driving the Search Engine Optimization (SEO) Services Market?", SEO, BASIS, KNOWN)
check("title: the fallback title (the full name, inside the first 40 characters) still passes", hard == [], str(hard))
hard, _ = planning.title_problems("Search Visibility Shifts Toward AI-Driven Answers", SEO, BASIS, KNOWN)
check("title: a title that names no form of the market is still rejected",
      "it does not contain the full primary keyword" in hard, str(hard))
hard, _ = planning.title_problems("Ad Budgets and the SEO Services Market Converge", SEO, BASIS, KNOWN)
check("title: 'another subject and the (abbreviated) market' is still rejected",
      any("joins another subject" in h for h in hard), str(hard))
hard, _ = planning.title_problems("Widget Market Shifts Toward New Buyers", WIDGET, BASIS, KNOWN)
check("title: nothing is loosened for a name without brackets (an incomplete name still fails)",
      "it does not contain the full primary keyword" in hard, str(hard))
hard, _ = planning.title_problems("Zzz Widget Services Market Shifts Toward New Buyers", WIDGET, BASIS, KNOWN)
check("title: a bracket-less name's own full form still passes", hard == [], str(hard))

# ------------------------------------------------------------------ a section heading
check("heading: 'Key players in SEO' names the market through the abbreviation now",
      planning.heading_problem("Key players in SEO", "key_players", SEO) is None)
check("heading: 'Key players in the field' is still rejected",
      planning.heading_problem("Key players in the field", "key_players", SEO) == "it does not name the market")
check("heading: nothing is loosened for a name without brackets",
      planning.heading_problem("Key players in the field", "key_players", WIDGET) == "it does not name the market"
      and planning.heading_problem("Key players in widget services", "key_players", WIDGET) is None)

# ------------------------------------------------------------------ the keyword research's phrase admission
check("keywords: 'seo services market size' names the market now",
      keyword_research._names_market("seo services market size", SEO))
check("keywords: the full-name phrase still names the market",
      keyword_research._names_market("search engine optimization services market size", SEO))
check("keywords: a phrase about a neighbouring market is still rejected",
      not keyword_research._names_market("digital marketing services market size", SEO))
check("keywords: nothing is loosened for a name without brackets",
      keyword_research._names_market("zzz widget services market size", WIDGET)
      and not keyword_research._names_market("widget market size", WIDGET)
      and not keyword_research._names_market("search optimization services market size", SEO))

# ------------------------------------------------------------------ the checks' title and primary-keyword counts
def run(title: str, paragraphs: list[str], primary: str = SEO) -> list[checks.CheckResult]:
    sections = [{"heading": "", "sop": [], "paragraphs": [prompts.SPONSOR_TOKEN, " ".join(paragraphs)]}]
    return checks.run_checks(title, sections, source_text="irrelevant", sponsor_brand_terms=[],
                             primary_keyword=primary)


def result(results: list[checks.CheckResult], needle: str) -> checks.CheckResult:
    return next(r for r in results if needle.lower() in r.name.lower())


res = run(LIVE_TITLE, [f"The {SHORT} grows as buyers shift spend toward AI-driven visibility."])
check("checks: a title carrying the abbreviated form contains the full primary keyword (soft)",
      result(res, "title contains the full primary keyword").passed)
res = run("Search Visibility Shifts Toward AI-Driven Answers", ["Growth continues across the market."])
check("checks: a title carrying no form of the market still fails the soft check",
      not result(res, "title contains the full primary keyword").passed)
res = run(LIVE_TITLE, [f"The {SHORT} grows fast. The {SHORT} keeps expanding. The {FULL} follows the same path."])
r = result(res, "primary keyword present")
check("checks: every equivalent form counts as a use of the primary keyword (3 uses, none lost)",
      "3x" in r.detail, r.detail)
res = run(LIVE_TITLE, [f"The {SHORT} grows fast."])
r = result(res, "primary keyword present")
check("checks: a single abbreviated use is counted (the check no longer reads 0)",
      "1x" in r.detail, r.detail)
res = run(LIVE_TITLE, ["The widget services market grows fast."], primary=WIDGET)
check("checks: nothing is loosened for a name without brackets (a partial name is neither in the title nor a use)",
      not result(res, "title contains the full primary keyword").passed
      and "0x" in result(res, "primary keyword present").detail,
      result(res, "primary keyword present").detail)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all market-name tests passed")
