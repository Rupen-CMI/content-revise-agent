"""Offline checks for the deterministic selection rules around market sizing restatements and the
key-players tag (no network, no real LLM calls). Run from backend/: python devtools/test_select_rules.py"""
import inspect
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import search  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

LOG = logging.getLogger("t")
LOG.setLevel(logging.INFO)  # the run_select messages this test greps are logged at INFO
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

# ------------------------------------------------------------------ test 1: the inside-the-market rule and the category list
check("prompt: test 1 keeps rejecting a different or broader market",
      "a different, related, or broader market is a wrong-market result" in prompts.SELECT_USER
      and "Reject a different or" in prompts.SELECT_USER)
check("prompt: test 1 states that a product category inside the market counts",
      "product category, segment, technology, vendor or use case WITHIN this market" in prompts.SELECT_USER
      and "never reject a product category inside this one" in prompts.SELECT_USER)
check("prompt: test 1 carries the generic inside-the-market examples, not one market's wording",
      "marketing automation or SEO tools are part of digital marketing software" in prompts.SELECT_USER
      and "Jira or sprint-planning tools are part of project management software" in prompts.SELECT_USER
      and "CMO-level story about marketing budgets" in prompts.SELECT_USER)

REPORT = reports.Report(news_id=1, name="Zzz Widget Services Market", subject="", url="u", source="s",
                        sections={}, segment_terms=["Widget Testing", "Widget Supply"])
ORIENT = {"topic_terms": ["widget maker", "widget supply"], "topic_summary": "t", "bridge_angle": "a"}

cats = pipeline._market_category_terms(REPORT, ORIENT)
check("categories: the report's segment names and orient's topic terms are both listed",
      "Widget Testing" in cats and "Widget Supply" in cats and "widget maker" in cats, cats)
check("categories: deduped, lowercase topic terms folded once",
      cats.count("widget supply") + cats.count("Widget Supply") == 1, cats)
check("categories: no segments and no topic terms gives the placeholder",
      pipeline._market_category_terms(
          reports.Report(news_id=1, name="X Market", subject="", url="u", source="s"), {}) == "(none listed)")
check("categories: an explicit topic_terms argument wins over orient's",
      pipeline._market_category_terms(REPORT, ORIENT, topic_terms=["explicit term"]) .count("widget maker") == 0
      and "explicit term" in pipeline._market_category_terms(REPORT, ORIENT, topic_terms=["explicit term"]))

# ------------------------------------------------------------------ run_select end to end with a stubbed llm.chat_json
ARTICLE = SourceArticle(title="A Blog Title", paragraphs=[])
SPONSOR = Paragraph(index=0, text="Sponsor line.")


def mkhit(i: int) -> search.SearchHit:
    return search.SearchHit(title=f"Widget story {i}", snippet="Acme Corp launched a widget testing service for "
                            "widget supply teams.", url=f"https://h{i}.example.org/{i}", source="",
                            date=datetime(2026, 6, 1, tzinfo=timezone.utc))


def ev(w: int, tags: str = "trends") -> dict:
    return {"id": f"W{w}", "fact": "Acme Corp launched a widget testing service.", "detail": "", "use_for": tags}


LOG_CAPTURE: list[str] = []


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_CAPTURE.append(record.getMessage())


_capture = _Capture()
LOG.addHandler(_capture)
_orig_chat = llm.chat_json


def run_with(responses: list[dict], hits: list[search.SearchHit], orient: dict | None = ORIENT) -> tuple[dict, list[str]]:
    """run_select with llm.chat_json stubbed to return the given responses in order; returns
    (selection, the user prompts each call received)."""
    calls: list[str] = []
    llm.chat_json = lambda system, user, **kw: (calls.append(user), responses[len(calls) - 1])[1]
    try:
        sel = pipeline.run_select(ARTICLE, orient or {}, SPONSOR, REPORT, hits, LOG)
    finally:
        llm.chat_json = _orig_chat
    return sel, calls


# a first pass with only 2 kept items triggers the second pass, whose items are merged with unique ids
HITS10 = [mkhit(i) for i in range(1, 11)]
sel, calls = run_with(
    [{"evidence": [ev(1), ev(2)]},
     {"evidence": [ev(1), ev(2), ev(3)]}],  # second pass: rest-list ids 1..3 -> original W3..W5
    HITS10)
ids = [e["id"] for e in sel["evidence"]]
check("second pass: a 2-item first pass runs exactly one more SELECT call", len(calls) == 2, str(len(calls)))
check("second pass: the second pass's items are merged with unique, remapped ids",
      ids == ["W1", "W2", "W3", "W4", "W5"] and len(set(ids)) == len(ids), str(ids))
check("second pass: the merge is logged",
      any(m == "[select] second pass: first kept 2, second added 3" for m in LOG_CAPTURE), str(LOG_CAPTURE[-3:]))
check("second pass: the second prompt carries the SECOND PASS instruction",
      "The first pass kept only 2 results" in calls[1] and "product category, vendor or segment inside this market"
      in calls[1], calls[1][:400])
check("second pass: the second prompt lists only the candidates the first pass did not keep",
      "[W1] Widget story 3" in calls[1] and "[W1] Widget story 1" not in calls[1], "")
check("second pass: the categories block reaches both prompts",
      all("CATEGORIES INSIDE THIS MARKET" in c and "Widget Testing" in c for c in calls))
check("second pass: the first prompt has no SECOND PASS block",
      "SECOND PASS" not in calls[0])
# the remapped ids still map onto the original hits list the way run_build_evidence reads them
built = pipeline.run_build_evidence(sel, HITS10, LOG)
check("second pass: the merged ids survive run_build_evidence (no bad-id drops)",
      all(f"(W{i})" in built for i in range(1, 6)), built[:400])

# a first pass with 6 kept items does NOT trigger the second pass
LOG_CAPTURE.clear()
sel6, calls6 = run_with([{"evidence": [ev(i) for i in range(1, 7)]}], HITS10)
check("second pass: a 6-item first pass runs only one SELECT call",
      len(calls6) == 1 and [e["id"] for e in sel6["evidence"]] == [f"W{i}" for i in range(1, 7)],
      f"{len(calls6)} calls")

# a thin candidate pool never triggers the second pass, however few items the first pass kept
LOG_CAPTURE.clear()
sel4, calls4 = run_with([{"evidence": [ev(1), ev(2)]}], HITS10[:4])
check("second pass: a pool smaller than 8 candidates runs only one SELECT call",
      len(calls4) == 1 and [e["id"] for e in sel4["evidence"]] == ["W1", "W2"], f"{len(calls4)} calls")

# the total stays within config.SEARCH_MAX_SELECTED
LOG_CAPTURE.clear()
_saved_max = config.SEARCH_MAX_SELECTED
config.SEARCH_MAX_SELECTED = 4
try:
    selc, callsc = run_with(
        [{"evidence": [ev(1), ev(2)]},
         {"evidence": [ev(1), ev(2), ev(3), ev(4), ev(5), ev(6)]}],
        HITS10)
finally:
    config.SEARCH_MAX_SELECTED = _saved_max
check("second pass: the merged total respects SEARCH_MAX_SELECTED",
      len(selc["evidence"]) == 4 and [e["id"] for e in selc["evidence"]] == ["W1", "W2", "W3", "W4"],
      str([e["id"] for e in selc["evidence"]]))

LOG.removeHandler(_capture)

print()
print("all select rule tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
