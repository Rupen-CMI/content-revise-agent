"""Offline checks for the deterministic selection rules around market sizing restatements and the
key-players tag (no network, no real LLM calls). Run from backend/: python devtools/test_select_rules.py"""
import inspect
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402

LOG = logging.getLogger("t")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


# ------------------------------------------------------------------ D4: the market-sizing regex shapes
def sized(fact: str) -> bool:
    return pipeline._is_market_sizing(fact)


check("sizing: a sizing verb before the market name at a currency amount matches",
      sized("Acme Corp estimates the widget platform market at $4.59 billion in 2023"))

check("sizing: a values/valued verb before the industry matches a second currency amount",
      not sized("Acme Corp distress-sold nothing and values its customers at roughly 50 staff.")
      and sized("One research house values the widget industry at USD 9 billion today"),
      "the values-verb shape must fire only with a market/industry/sector noun")

check("sizing: forecasts ... near/to/reaching before the amount matches",
      sized("One desk forecasts the widget sector near EUR 8 billion in five years")
      and sized("One desk pegs the widget sector to $8 billion"))
check("sizing: a bare market noun priced with at matches an amount without a sizing verb",
      sized("The widget services market at $480 million remains small"))
check("sizing: the older shapes still match",
      sized("The global widget market was valued at approximately $1.34 billion in 2025 and is projected to grow")
      and sized("The segment is growing at a 12% CAGR, analysts say")
      and sized("An analyst expects the market to reach USD 910 million by 2034"))
check("sizing: a rival research firm's name still matches",
      sized("Mordor Intelligence projects that the scraping industry will double from $1.03 billion in 2025 to $2 billion."))

check("sizing: an ordinary launch fact is missed",
      not sized("Acme Corp launched a widget testing lab for AI teams in 2026"))
check("sizing: a personnel fact is missed",
      not sized("Acme Corp appointed a new chief financial officer"))
check("sizing: a funding fact is missed",
      not sized("Firecrawl Inc., a maker of widget tools, raised $75 million in funding"))
check("sizing: a plain product/value talk without a market noun is missed",
      not sized("Acme Corp values clean data over tidy dashboards and raised its seat count"))
check("sizing: a market word followed by something other than an amount is missed",
      not sized("The market for proxies is crowded, but Oxylabs leads on pool size"))

# run_select keeps headline_figures optional so every old caller keeps working unchanged
sig = inspect.signature(pipeline.run_select)
check("sizing: headline_figures defaults to None on run_select",
      sig.parameters["headline_figures"].default is None)

# ------------------------------------------------------------------ D4: the fact-sheet headline-figure drop
FS = {"size": {"value": "USD 480 million", "when": "2025", "quote": "q"},
      "forecast": None, "cagr": None}
HL = pipeline._headline_figures(FS)
check("headline: the fact sheet's size value is normalised to its number",
      HL == {"raw": {"480"}, "floats": {480.0}}, str(HL))
check("headline: an empty fact sheet yields None", pipeline._headline_figures(None) is None)
check("headline: a fact sheet with no surviving headline figure yields None",
      pipeline._headline_figures({"size": None, "forecast": None, "cagr": None}) is None)

sel = {"evidence": [
    {"id": "W1", "fact": "Revenue for the Zzz Widget Services Market stood at USD 480 million this year, one tracker writes.",
     "detail": "", "use_for_all": ["context"], "use_for": "context"},
    {"id": "W2", "fact": "Revenue for the Zzz Widget Services Market stood at USD 481 million this year, another tracker says.",
     "detail": "", "use_for_all": ["context"], "use_for": "context"},
]}
pipeline._drop_market_sizing_facts(sel, LOG, HL)
ids = [e["id"] for e in sel["evidence"]]
check("headline: a fact restating only the fact sheet's size figure is dropped, a differing figure survives",
      ids == ["W2"], str(ids))

sel2 = {"evidence": [dict(e) for e in (
    {"id": "W1", "fact": "Revenue for the Zzz Widget Services Market stood at USD 480 million this year, one tracker writes.",
     "detail": "", "use_for_all": ["context"], "use_for": "context"},
    {"id": "W2", "fact": "Revenue for the Zzz Widget Services Market stood at USD 481 million this year, another tracker says.",
     "detail": "", "use_for_all": ["context"], "use_for": "context"},
)]}
pipeline._drop_market_sizing_facts(sel2, LOG)
check("headline: without headline_figures both items keep working exactly as before",
      [e["id"] for e in sel2["evidence"]] == ["W1", "W2"], str([e["id"] for e in sel2["evidence"]]))
check("headline: a fact naming no market is never a headline restatement",
      not pipeline._restates_headline_figures("Revenue across Acme Corp's own divisions reached USD 480 million this year", HL))
check("headline: a fact naming a market with no figures is never a headline restatement",
      not pipeline._restates_headline_figures("The Zzz Widget Services Market is crowded, trackers agree", HL))
check("headline: 480 and 480.0 compare equal through the float path",
      pipeline._restates_headline_figures("The Zzz Widget Services Market reached 480.0 this year by one count", HL))

# ------------------------------------------------------------------ D2: the players tag vs recognitions and personnel moves
def run_players(items: list[dict]) -> list[dict]:
    sel = {"evidence": items}
    pipeline._strip_players_tag(sel, LOG)
    return sel["evidence"]


award = "Acme Corp was named one of the platform vendors of the year by a trade journal."
personnel = "Acme Corp hired a new chief financial officer to lead its widget arm."
launch = "Acme Corp launched a widget platform for testing labs and acquired rival Beta Tools."

kept = run_players([
    {"id": "W1", "fact": award, "detail": "", "use_for_all": ["players"], "use_for": "players"},
])
check("players: an award fact whose only tag is players is dropped outright",
      kept == [], str(kept))

kept = run_players([
    {"id": "W2", "fact": personnel, "detail": "", "use_for_all": ["players", "us"], "use_for": "players"},
    {"id": "W3", "fact": "Acme Corp won an industry recognition for its widget catalogue.", "detail": "",
     "use_for_all": ["players", "trends"], "use_for": "players"},
])
check("players: a personnel fact with another tag keeps the item under that tag",
      len(kept) == 2 and kept[0]["id"] == "W2" and kept[0]["use_for_all"] == ["us"] and kept[0]["use_for"] == "us",
      str(kept))
check("players: an industry-recognition fact loses only its players tag, keeping other tags",
      len(kept) == 2 and kept[1]["id"] == "W3" and kept[1]["use_for_all"] == ["trends"] and kept[1]["use_for"] == "trends",
      str(kept))

kept = run_players([
    {"id": "W4", "fact": launch, "detail": "", "use_for_all": ["players"], "use_for": "players"},
])
check("players: a launch/acquisition fact keeps its players tag and the item",
      len(kept) == 1 and kept[0]["use_for_all"] == ["players"], str(kept))

kept = run_players([
    {"id": "W5", "fact": "Acme Corp stepped down from a joint venture it entered last year.", "detail": "",
     "use_for_all": ["players", "context"], "use_for": "players"},
])
check("players: a company exiting its own venture is not wrongly demoted by mere accident of words",
      len(kept) == 1 and "players" in kept[0]["use_for_all"], str(kept))

kept = run_players([
    {"id": "W6", "fact": "Acme Corp launched a widget line; a rival's CEO defects said one report.", "detail": "",
     "use_for_all": ["players"], "use_for": "players"},
])
check("players: a launch fact that mentions a personnel change alongside loses the players tag",
      kept == [], str(kept))

print()
print("all select rule tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
