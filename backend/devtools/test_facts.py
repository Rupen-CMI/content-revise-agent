"""Offline test for the fact sheet and the figure gap-fill (no network, no real LLM calls).

Run from backend/: python devtools/test_facts.py

Covers facts.validate_fact_sheet (every entry must be backed by a quote that is in the page and carries its figures),
the report-then-evidence figure rule (facts.apply_figure_source / filter_figure_hits / run_figure_backing with the LLM
stubbed) and flatten_fact_sheet.
"""
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts  # noqa: E402
import llm  # noqa: E402
import search  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_facts")
TODAY = date(2026, 10, 23)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


MARKET = "Zzz Widget Services Market"
PAGE = (
    "The Global Zzz Widget Services Market is estimated to be valued at USD 561.7 million in 2026 and is expected to "
    "reach USD 867.2 million by 2033, growing at a compound annual growth rate (CAGR) of 6.4% from 2026 to 2033. "
    "Rising adoption of digital marketing is expected to drive the growth of the market. "
    "However, the increasing threat of piracy is expected to hamper the growth of the market, with 37% of software pirated in 2018. "
    "North America held a major share in the market in 2018 owing to many players. "
    "In the United States, regulators opened a new review of widget exports. "
    "The Indian IT industry generated revenue of US$ 177 billion in 2019 and is expected to reach US$ 350 billion by 2025 with CAGR 6.1%. "
    "On the basis of type, the market is segmented into: Browser Extension, Installable Software, Cloud Based. "
    "Key players operating in the market include Acme Corp, Beta Ltd, and Gamma Inc."
)


def good_raw() -> dict:
    return {
        "size": {"value": "USD 561.7 million", "year": "2026", "quote":
                 "The Global Zzz Widget Services Market is estimated to be valued at USD 561.7 million in 2026 and is expected to "
                 "reach USD 867.2 million by 2033, growing at a compound annual growth rate (CAGR) of 6.4% from 2026 to 2033"},
        "forecast": {"value": "USD 867.2 million", "year": "2033", "quote":
                     "The Global Zzz Widget Services Market is estimated to be valued at USD 561.7 million in 2026 and is expected to "
                 "reach USD 867.2 million by 2033, growing at a compound annual growth rate (CAGR) of 6.4% from 2026 to 2033"},
        "cagr": {"value": "6.4%", "period": "2026 to 2033", "quote":
                 "The Global Zzz Widget Services Market is estimated to be valued at USD 561.7 million in 2026 and is expected to "
                 "reach USD 867.2 million by 2033, growing at a compound annual growth rate (CAGR) of 6.4% from 2026 to 2033"},
        "segments": [{"type": "By Type", "items": ["Browser Extension", "Installable Software", "Cloud Based"]}],
        "regions": {"leader": {"name": "North America", "basis": "share in 2018", "quote":
                               "North America held a major share in the market in 2018 owing to many players"},
                    "fastest": None},
        "us": [{"text": "Regulators opened a review of widget exports.", "quote":
                "In the United States, regulators opened a new review of widget exports"}],
        "drivers": [{"text": "Digital marketing adoption is rising.", "quote":
                     "Rising adoption of digital marketing is expected to drive the growth of the market"}],
        "restraints": [{"text": "Piracy hampers growth; 37% of software was pirated in 2018.", "quote":
                        "the increasing threat of piracy is expected to hamper the growth of the market, with 37% of software pirated in 2018"}],
        "opportunities": [],
        "players": ["Acme Corp", "Beta Ltd, Freedonia", "Made Up Co", "acme corp"],
    }


# ------------------------------------------------------------------ a fully grounded answer passes through
fs = facts.validate_fact_sheet(good_raw(), PAGE, MARKET, LOG, TODAY)
check("good: size, forecast and CAGR kept", all(fs[k] for k in facts.HEADLINE_KEYS), str(fs["gaps"]))
check("good: no gaps", fs["gaps"] == [])
check("good: headline figures are tagged as CMI report figures", all(fs[k]["source"] == "CMI report" for k in facts.HEADLINE_KEYS))
check("good: segment kept", len(fs["segments"]) == 1 and len(fs["segments"][0]["items"]) == 3)
check("good: regional leader kept with a historical flag (2018 is old)", fs["regions"]["leader"] and fs["regions"]["leader"]["historical"])
check("good: statement ids are positional", fs["drivers"][0]["id"] == "fs.drivers.0" and fs["us"][0]["id"] == "fs.us.0")
check("good: a 2018 restraint is flagged historical", fs["restraints"][0]["historical"])
check("good: a statement without a year is not historical", not fs["drivers"][0]["historical"])

check("players: only names that are in the page survive, legal suffixes dropped, duplicates collapsed",
      fs["players"] == ["Acme", "Beta"], str(fs["players"]))
check("players: a missing or odd players field is survived",
      facts.validate_fact_sheet({"players": "x"}, PAGE, MARKET, LOG, TODAY)["players"] == [])

# ------------------------------------------------------------------ the figure rules
raw = good_raw()
raw["size"]["value"] = "USD 600 million"
check("figure: a value whose numbers are not in its quote is dropped",
      facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["size"] is None)

raw = good_raw()
raw["size"]["quote"] = "The Global Zzz Widget Services Market is believed to be worth USD 561.7 million today in 2026"
check("figure: a quote that is not in the page is dropped",
      facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["size"] is None)

raw = good_raw()
raw["cagr"] = {"value": "6.1%", "period": "2019 to 2025", "quote":
               "The Indian IT industry generated revenue of US$ 177 billion in 2019 and is expected to reach US$ 350 billion by 2025 with CAGR 6.1%"}
check("figure: a country's industry growth rate (no 'market' in the sentence) is not the market's CAGR",
      facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["cagr"] is None)

raw = good_raw()
raw["cagr"]["value"] = "6.4"
check("figure: a CAGR without a percent sign is dropped", facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["cagr"] is None)

raw = good_raw()
raw["size"] = {"value": "about half a billion", "year": "2026", "quote": raw["size"]["quote"]}
check("figure: a value with no number is dropped", facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["size"] is None)

fs = facts.validate_fact_sheet({}, PAGE, MARKET, LOG, TODAY)
check("gaps: an empty answer leaves all three headline figures as gaps", fs["gaps"] == ["size", "forecast", "cagr"])
fs = facts.validate_fact_sheet("not a dict", PAGE, MARKET, LOG, TODAY)
check("gaps: a non-dict answer is survived", fs["gaps"] == ["size", "forecast", "cagr"])

raw = good_raw()
raw["size"] = None
check("gaps: one missing figure is listed alone", facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["gaps"] == ["size"])

# ------------------------------------------------------------------ statements, segments, regions
raw = good_raw()
raw["drivers"].append({"text": "Demand grows 12% a year.", "quote": "Rising adoption of digital marketing is expected to drive the growth of the market"})
check("statement: a figure not in its own quote is dropped", len(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["drivers"]) == 1)

raw = good_raw()
raw["drivers"].append({"text": "Something invented.", "quote": "This sentence is nowhere in the report page at all"})
check("statement: an unquoted invention is dropped", len(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["drivers"]) == 1)

raw = good_raw()
raw["segments"] = [{"type": "By Colour", "items": ["Red", "Blue"]}, {"type": "By Type", "items": ["Browser Extension", "Made Up Item"]}]
check("segments: categories not in the page are dropped and a split with under two left is dropped",
      facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["segments"] == [])

raw = good_raw()
raw["regions"]["leader"]["name"] = "Europe"
check("regions: a leader the quote does not name is dropped", facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["regions"]["leader"] is None)

raw = good_raw()
raw["drivers"] = [{"text": f"Driver {w}.", "quote": "Rising adoption of digital marketing is expected to drive the growth of the market"} for w in "abcdefghij"]
check("statement: capped at a few strong entries", len(facts.validate_fact_sheet(raw, PAGE, MARKET, LOG, TODAY)["drivers"]) == facts._MAX_STATEMENTS)

# ------------------------------------------------------------------ flatten
flat = facts.flatten_fact_sheet(facts.validate_fact_sheet(good_raw(), PAGE, MARKET, LOG, TODAY))
ids = [f["id"] for f in flat]
check("flatten: every fact has a unique id", len(ids) == len(set(ids)) and "fs.size" in ids and "fs.regions.leader" in ids, str(ids))
check("flatten: headline text carries the figure and its year", any("561.7" in f["text"] and "2026" in f["text"] for f in flat))

# ------------------------------------------------------------------ the figure gap-fill (report first, evidence second)
def hit(title, snippet, url, source="", date_=None, excerpt=""):
    return search.SearchHit(title=title, snippet=snippet, url=url, source=source, date=date_, excerpt=excerpt)


GOOD_TEXT = ("The global zzz widget services market was valued at USD 1.2 billion in 2025 and is projected to reach "
             "USD 2.9 billion by 2032, registering a CAGR of 13.1% during the forecast period 2025-2032.")
BROADER = ("The global zzz widget market was valued at USD 4.1 billion in 2025 and is projected to reach "
           "USD 9.0 billion by 2032, registering a CAGR of 11.9% during the forecast period 2025-2032.")
RECENT = datetime(2026, 8, 1, tzinfo=timezone.utc)

hits = [
    hit("Zzz Widget Services Market Size Report", "Sizing", "https://marketsite.example.com/zzz", "MarketSite", RECENT, GOOD_TEXT),
    hit("Zzz Widget Market Size", "Broader market", "https://broad.example.org/zzz", "Broad Org", RECENT, BROADER),
    hit("Zzz Widget Services Market blog", "CMI blog", "https://www.coherentmarketinsights.com/blog/zzz", "CMI", RECENT, GOOD_TEXT),
]
SENT = ("The global zzz widget services market was valued at USD 1.2 billion in 2025 and is projected to reach "
        "USD 2.9 billion by 2032, registering a CAGR of 13.1% during the forecast period 2025-2032")
fullq = {
    "id": "F1",
    "size": {"value": "USD 1.2 billion", "year": "2025", "quote": SENT},
    "forecast": {"value": "USD 2.9 billion", "year": "2032", "quote": SENT},
    "cagr": {"value": "13.1%", "period": "2025-2032", "quote": SENT},
}


def empty_fs():
    return facts.validate_fact_sheet({}, PAGE, MARKET, LOG, TODAY)


out = facts.apply_figure_source(empty_fs(), dict(fullq), ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: a same-market outside page fills all three gaps", out["gaps"] == [] and out["size"]["value"] == "USD 1.2 billion", str(out["gaps"]))
check("fill: filled figures are tagged with the outside source and url, not CMI",
      out["size"]["source"] == "MarketSite" and out["size"]["url"].startswith("https://marketsite"))
check("fill: the figure source is recorded", out["figure_source"] and out["figure_source"]["figures"] == ["cagr", "forecast", "size"])

BSENT = ("The global zzz widget market was valued at USD 4.1 billion in 2025 and is projected to reach "
         "USD 9.0 billion by 2032, registering a CAGR of 11.9% during the forecast period 2025-2032")
out = facts.apply_figure_source(empty_fs(), {"id": "F2",
    "size": {"value": "USD 4.1 billion", "year": "2025", "quote": BSENT},
    "forecast": {"value": "USD 9.0 billion", "year": "2032", "quote": BSENT},
    "cagr": {"value": "11.9%", "period": "2025-2032", "quote": BSENT}},
    ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: a page about a BROADER market is rejected, not relabelled", out["gaps"] == ["size", "forecast", "cagr"], str(out["gaps"]))

out = facts.apply_figure_source(empty_fs(), {**fullq, "id": "F3"}, ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: a CMI blog page is rejected as a source", out["gaps"] == ["size", "forecast", "cagr"])

out = facts.apply_figure_source(empty_fs(), {**fullq, "id": "F1"}, ["size", "forecast", "cagr"], hits, MARKET, ["marketsite.example.com"], LOG, TODAY)
check("fill: the guest article's own site is rejected as a source", out["gaps"] == ["size", "forecast", "cagr"])

bad_quote = {**fullq, "size": {**fullq["size"], "quote": "The global zzz widget services market was worth far more than this in 2025 truly"}}
out = facts.apply_figure_source(empty_fs(), bad_quote, ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: a figure whose quote is not in the page is dropped while the checked ones are kept",
      out["gaps"] == ["size"] and out["forecast"] and out["cagr"], str(out["gaps"]))

old = {**fullq, "size": {**fullq["size"], "year": "2018"}}
out = facts.apply_figure_source(empty_fs(), old, ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: a figure for a year that is too old is dropped", out["gaps"] == ["size"], str(out["gaps"]))

out = facts.apply_figure_source(empty_fs(), dict(fullq), ["size"], hits, MARKET, [], LOG, TODAY)
check("fill: only the figures that are missing are filled", out["size"] and out["forecast"] is None and out["cagr"] is None)

ancient = [hit("Zzz Widget Services Market Size Report", "Sizing", "https://old.example.com/zzz", "Old", datetime(2023, 1, 1, tzinfo=timezone.utc), GOOD_TEXT)]
out = facts.apply_figure_source(empty_fs(), dict(fullq), ["size", "forecast", "cagr"], ancient, MARKET, [], LOG, TODAY)
check("fill: a page dated more than two years ago is rejected", out["gaps"] == ["size", "forecast", "cagr"])

out = facts.apply_figure_source(empty_fs(), None, ["size", "forecast", "cagr"], hits, MARKET, [], LOG, TODAY)
check("fill: 'no page qualifies' leaves every gap open", out["gaps"] == ["size", "forecast", "cagr"])
out = facts.apply_figure_source(empty_fs(), {"id": "F9"}, ["size"], hits, MARKET, [], LOG, TODAY)
check("fill: a bad page id is survived", out["gaps"] == ["size", "forecast", "cagr"])

# ------------------------------------------------------------------ candidate filter and orchestration (LLM stubbed)
cands = facts.filter_figure_hits(
    hits + [hit("Totally unrelated recipe page", "Cooking", "https://food.example.com/r", "Food")], MARKET, ["marketsite.example.com"], LOG)
check("filter: excluded sites, CMI and unrelated pages are dropped", [h.host for h in cands] == ["broad.example.org"], str([h.host for h in cands]))
check("queries: built from the market's quoted name", all("zzz widget services market" in q.lower() for q in facts.figure_gap_queries(MARKET)))

called = {"n": 0}


def fake_chat(system, user, **kw):
    called["n"] += 1
    return {"source": dict(fullq)}


orig = llm.chat_json
llm.chat_json = fake_chat
try:
    out = facts.run_figure_backing(MARKET, empty_fs(), hits[:1], [], LOG, TODAY)
    check("backing: the stubbed call fills the sheet end to end", out["gaps"] == [] and called["n"] == 1)
    full = facts.validate_fact_sheet(good_raw(), PAGE, MARKET, LOG, TODAY)
    facts.run_figure_backing(MARKET, full, hits[:1], [], LOG, TODAY)
    check("backing: no call at all when the report already has every figure", called["n"] == 1)
    facts.run_figure_backing(MARKET, empty_fs(), [], [], LOG, TODAY)
    check("backing: no call when the gap search found no pages", called["n"] == 1)
finally:
    llm.chat_json = orig

# ------------------------------------------------------------------ figures a fact spells in words
check("spelled: hyphenated and spaced compounds both give digits",
      facts.spelled_numbers("Sixty-three percent of firms are using the tool, double the share in 2024") == {"63"},
      str(facts.spelled_numbers("Sixty-three percent of firms are using the tool, double the share in 2024")))
check("spelled: other compound shapes",
      facts.spelled_numbers("Forty-five teams, ninety nine seats, twelve thousand records") == {"45", "99", "12", "12000"},
      str(facts.spelled_numbers("Forty-five teams, ninety nine seats, twelve thousand records")))
check("spelled: hundred builds the full product",
      facts.spelled_numbers("two hundred firms, one hundred and none to spare") == {"200", "100"},
      str(facts.spelled_numbers("two hundred firms, one hundred and none to spare")))
check("spelled: big scales return the coefficient and the product",
      facts.spelled_numbers("three million units, two billion orders, one trillion rows")
      == {"3", "3000000", "2", "2000000000", "1", "1000000000000"},
      str(facts.spelled_numbers("three million units, two billion orders, one trillion rows")))
check("spelled: a bare one is excluded", facts.spelled_numbers("no one, the one, one another, each one, one of the") == set(),
      str(facts.spelled_numbers("no one, the one, one another, each one, one of the")))
check("spelled: one counts when it builds a larger number",
      facts.spelled_numbers("twenty-one users, one hundred seats, one million rows") == {"21", "100", "1", "1000000"},
      str(facts.spelled_numbers("twenty-one users, one hundred seats, one million rows")))
check("spelled: one before percent or in + a number states a share",
      facts.spelled_numbers("one percent of users, one in ten firms, one in 4 teams") == {"1", "10"},
      str(facts.spelled_numbers("one percent of users, one in ten firms, one in 4 teams")))
check("spelled: a bare multiplier with no value before it converts to nothing",
      facts.spelled_numbers("a hundred scattered entries") == set(), str(facts.spelled_numbers("a hundred scattered entries")))

check("spelled: whole words only — no match inside another word",
      facts.spelled_numbers("someone, none, money, stone, often, tent, intent, seventyfold") == set(),
      str(facts.spelled_numbers("someone, none, money, stone, often, tent, intent, seventyfold")))
check("fact_numbers: digits plus spelled cardinals",
      facts.fact_numbers("Sixty-three percent of firms are using the tool, double the share in 2024.")
      == {"63", "2024"},
      str(facts.fact_numbers("Sixty-three percent of firms are using the tool, double the share in 2024.")))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all facts tests passed")
