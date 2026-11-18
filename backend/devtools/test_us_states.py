"""U.S. state-level evidence: a page about one state counts for the U.S. section, the extra state queries are built from the
U.S. queries, and a state name never reads as another country in the U.S.-only check."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import content_rules  # noqa: E402
import feeds  # noqa: E402
import search  # noqa: E402
import us_search  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        FAILURES.append(name)


def hit(title):
    return search.SearchHit(title=title, snippet="", url="https://example.com/a", source="", date=None)


check("a page about one state is a U.S. page", us_search.names_the_us(hit("Texas retailers expand postcard retargeting")))
check("a two-word state counts", us_search.names_the_us(hit("New York brands test mailed follow-ups")))
check("Georgia alone is not taken for the state (it is also a country)", not us_search.names_the_us(hit("Georgia mail volumes rise")))
check("a page naming neither the U.S. nor a state is not a U.S. page", not us_search.names_the_us(hit("Mail volumes rise in Europe")))

qs = feeds.us_state_queries(['"direct mail retargeting" United States', "direct mail retargeting U.S. adoption demand"])
check("state queries: the market words without the U.S. words, then states joined by OR",
      qs and qs[0] == 'direct mail retargeting Texas OR California OR Florida OR "New York"' and len(qs) == 2, str(qs))
check("state queries: nothing to build from -> none", feeds.us_state_queries(["United States"]) == [])

sec = [{"heading": "Direct mail retargeting in the United States", "sop": ["country_us"],
        "paragraphs": ["Brands in New Mexico and Texas mailed more postcards in 2026.", "Demand in Mexico grew."]}]
problems = content_rules.us_only_problems(sec)
check("U.S.-only check: New Mexico is a state, plain Mexico is still another country",
      len(problems) == 1 and "Demand in Mexico" in problems[0], str(problems))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all U.S. state tests passed")
