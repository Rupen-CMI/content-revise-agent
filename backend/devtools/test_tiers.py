"""Offline test for the Step 1 tiering feature: pipeline.normalize_suggested_markets (the orient
call's tiered market suggestions) and pipeline.run_tier_catalog's tiering/validation of catalog
candidates against the blog topic. No network, no LLM — llm.chat_json is faked in-process.

Run from backend/: python devtools/test_tiers.py
"""
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
import pipeline  # noqa: E402
import reports  # noqa: E402
from docx_io import SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_tiers")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False
EMPTY = {"tier1": [], "tier2": [], "tier3": []}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------ normalize_suggested_markets

check("normalize: a bare orient dict yields the exact empty shape", pipeline.normalize_suggested_markets({}) == EMPTY)
check("normalize: a non-dict suggested_markets value is tolerated", pipeline.normalize_suggested_markets({"suggested_markets": "junk"}) == EMPTY)
check("normalize: a malformed tier value (not a list) becomes an empty list",
      pipeline.normalize_suggested_markets({"suggested_markets": {"tier1": "A Market", "tier2": None, "tier3": 7}}) == EMPTY)

mixed = {"suggested_markets": {"tier1": ["Alpha Market", 5, None, ["x"], "Beta Market, By End User"]}}
check("normalize: non-string entries are dropped and suffixes stripped",
      pipeline.normalize_suggested_markets(mixed)["tier1"] == ["Alpha Market", "Beta Market"])

cross = {"suggested_markets": {"tier1": ["Alpha Market"], "tier2": ["alpha Market"], "tier3": ["ALPHA MARKET"]}}
check("normalize: a name already used in an earlier tier is dropped case-insensitively",
      pipeline.normalize_suggested_markets(cross) == {"tier1": ["Alpha Market"], "tier2": [], "tier3": []})

own_stray = {"suggested_markets": {"tier1": ["Beta Market", "Alpha Market", "Gamma Market"],
                                   "tier2": ["Delta Market", "Epsilon Market", "Zeta Market"]},
             "own_market": "Alpha Market, By End User"}
check("normalize: own_market (bare-named) leads tier1 even when the model filed it later",
      pipeline.normalize_suggested_markets(own_stray)["tier1"][0] == "Alpha Market")

cap = {"suggested_markets": {"tier2": ["A1 Market", "A2 Market", "A3 Market", "A4 Market"]}}
check("normalize: at most 3 names per tier", pipeline.normalize_suggested_markets(cap)["tier2"] == ["A1 Market", "A2 Market", "A3 Market"])

own_cap = {"suggested_markets": {"tier1": ["A1 Market", "A2 Market", "A3 Market", "A4 Market"]}, "own_market": "Own Market"}
check("normalize: inserting own_market first keeps the tier within the cap",
      pipeline.normalize_suggested_markets(own_cap)["tier1"] == ["Own Market", "A1 Market", "A2 Market"])

fallback = {"suggested_markets": {}, "candidate_markets": ["First Candidate Market", "Second Candidate Market"]}
check("normalize: an empty tier1 falls back to the first candidate_markets name",
      pipeline.normalize_suggested_markets(fallback)["tier1"] == ["First Candidate Market"])
check("normalize: no fallback at all leaves tier1 empty",
      pipeline.normalize_suggested_markets({"suggested_markets": {}}) == EMPTY)

# ------------------------------------------------------------------------- run_tier_catalog

ARTICLE = SourceArticle(title="A Blog Title", paragraphs=[])
ORIENT = {"topic_summary": "the topic", "bridge_angle": "the angle"}


def entry(nid: int, kw: str) -> reports.CatalogEntry:
    return reports.CatalogEntry(news_id=nid, keyword=kw, slug=f"n{nid}", fmt=0, has_summary=True)


def entries(n: int) -> list[reports.CatalogEntry]:
    return [entry(i, f"A{i} Market") for i in range(1, n + 1)]


@contextmanager
def fake_chat_json(fn):
    orig = llm.chat_json
    llm.chat_json = fn
    try:
        yield
    finally:
        llm.chat_json = orig


calls: list[tuple[str, str, dict]] = []


def recorded(result_json, system: str = ""):
    def fn(system_arg, user_arg, **kwargs):
        calls.append((system_arg, user_arg, kwargs))
        return result_json
    return fn


# valid placements: tier order first, original rank order inside each tier
order_json = {"tiers": [{"id": 1, "tier": 2}, {"id": 2, "tier": 1}, {"id": 3, "tier": 2}, {"id": 4, "tier": 1}]}
with fake_chat_json(recorded(order_json)):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(4), LOG)
check("tier catalog: entries ordered by tier then by original rank",
      [(e.keyword, e.tier) for e in out] == [("A2 Market", 1), ("A4 Market", 1), ("A1 Market", 2), ("A3 Market", 2)],
      str([(e.keyword, e.tier) for e in out]))

zero_json = {"tiers": [{"id": 1, "tier": 0}, {"id": 2, "tier": 2}]}
with fake_chat_json(recorded(zero_json)):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: tier 0 placements are dropped, an unanswered report stays untiered after the tiered ones",
      [(e.keyword, e.tier) for e in out] == [("A2 Market", 2), ("A3 Market", None)], str([(e.keyword, e.tier) for e in out]))

bad_json = {"tiers": [{"id": 0, "tier": 1}, {"id": 4, "tier": 1}, {"id": 3, "tier": 9}, {"id": 2, "tier": "3"},
                      {"id": 1, "tier": 1}, {"tier": 1}, {"id": 3, "tier": "x"}]}
with fake_chat_json(recorded(bad_json)):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: out-of-range and invalid ids/tiers are ignored (the report with no valid answer stays untiered)",
      [(e.keyword, e.tier) for e in out] == [("A1 Market", 1), ("A2 Market", 3), ("A3 Market", None)], str([(e.keyword, e.tier) for e in out]))

dup_json = {"tiers": [{"id": 2, "tier": 3}, {"id": 2, "tier": 1}]}
with fake_chat_json(recorded(dup_json)):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: a duplicate id is ignored and only its first placement counts",
      [(e.keyword, e.tier) for e in out] == [("A2 Market", 3), ("A1 Market", None), ("A3 Market", None)], str([(e.keyword, e.tier) for e in out]))


def boom(system_arg, user_arg, **kwargs):
    raise llm.LLMError("down")


with fake_chat_json(boom):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(7), LOG)
check("tier catalog: an LLMError falls back to the first 5 candidates untiered",
      [e.keyword for e in out] == [f"A{i} Market" for i in range(1, 6)] and all(e.tier is None for e in out))

with fake_chat_json(recorded({"tiers": [{"id": 99, "tier": 1}]})):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: nothing valid in the answer falls back to 5 (or fewer) untiered",
      [e.keyword for e in out] == [f"A{i} Market" for i in range(1, 4)] and all(e.tier is None for e in out))

with fake_chat_json(recorded({})):
    out = pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: a missing 'tiers' key falls back to untiered", all(e.tier is None and e.keyword == f"A{i} Market" for i, e in enumerate(out, 1)))


def never(*args, **kwargs):
    raise AssertionError("llm.chat_json must not be called for an empty candidate list")


with fake_chat_json(never):
    check("tier catalog: an empty candidate list makes no LLM call", pipeline.run_tier_catalog(ORIENT, ARTICLE, [], LOG) == [])


def capture(system_arg, user_arg, **kwargs):
    capture.user = user_arg
    return {"tiers": [{"id": 1, "tier": 1}]}


with fake_chat_json(capture):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(3), LOG)
check("tier catalog: candidates reach the prompt as numbered report-name lines",
      "1. A1 Market\n2. A2 Market\n3. A3 Market" in capture.user, capture.user[:200])

with fake_chat_json(capture):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(20), LOG)
check("tier catalog: the whole candidate list is shown to the model",
      "15. A15 Market" in capture.user and "20. A20 Market" in capture.user, capture.user[:400])

with fake_chat_json(capture):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(20), LOG, limit=15)
check("tier catalog: an explicit `limit` still caps what is shown",
      "15. A15 Market" in capture.user and "16." not in capture.user, capture.user[:400])


def check_call_kwargs(result_json):
    seen: dict = {}

    def fn(_s, _u, **kwargs):
        seen.update(kwargs)
        return result_json
    return fn, seen


fn, seen = check_call_kwargs({"tiers": []})
with fake_chat_json(fn):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(2), LOG)
check("tier catalog: the call is made as 'tier_catalog' at temperature 0.1",
      seen.get("call_name") == "tier_catalog" and seen.get("temperature") == 0.1, str(seen))

with fake_chat_json(capture):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(2), LOG)
check("tier catalog: the prompt carries the article title, topic summary and bridge angle",
      "A Blog Title" in capture.user and "the topic" in capture.user and "the angle" in capture.user)

with fake_chat_json(capture):
    pipeline.run_tier_catalog(ORIENT, ARTICLE, entries(2), LOG)
check("tier catalog: the prompt embeds all three tier definitions verbatim",
      all(phrase in capture.user for phrase in (
          "Tier 1 - Direct match: the market's core subject is the blog topic, or a market, product or technology named",
          "Tier 2 - Adjacent or structural match: the market is directly upstream, downstream, or a component or input of",
          "Tier 3 - Shared driver or end-use match: the market is a different one, but it is driven by the same underlying",
      )))

# ---------------------------------------------- the report that names the article's own market

OWN = {"topic_summary": "the topic", "bridge_angle": "the angle", "own_market": "Gamma Widget Market"}
pool = [entry(1, "Widget Market"), entry(2, "Delta Widget Market"), entry(3, "Gamma Widget Market"), entry(4, "Other Market")]

with fake_chat_json(recorded({"tiers": [{"id": 1, "tier": 1}, {"id": 2, "tier": 2}, {"id": 3, "tier": 0}, {"id": 4, "tier": 3}]})):
    out = pipeline.run_tier_catalog(OWN, ARTICLE, pool, LOG)
check("own market: the exact-name report is first and tier 1 even when the model gave it tier 0",
      [(e.keyword, e.tier) for e in out][:2] == [("Gamma Widget Market", 1), ("Widget Market", 1)], str([(e.keyword, e.tier) for e in out]))
check("own market: the other reports keep tier-then-rank order after it",
      [e.keyword for e in out] == ["Gamma Widget Market", "Widget Market", "Delta Widget Market", "Other Market"])

with fake_chat_json(recorded({"tiers": [{"id": 3, "tier": 3}]})):
    out = pipeline.run_tier_catalog(OWN, ARTICLE, pool, LOG)
check("own market: tier 3 from the model is lifted to tier 1 and leads",
      (out[0].keyword, out[0].tier) == ("Gamma Widget Market", 1) and len(out) == 4, str([(e.keyword, e.tier) for e in out]))

with fake_chat_json(boom):
    out = pipeline.run_tier_catalog(OWN, ARTICLE, pool + [entry(5, "Z1 Market"), entry(6, "Z2 Market")], LOG)
check("own market: the LLM-failure fallback also leads with the exact-name report",
      out[0].keyword == "Gamma Widget Market" and out[0].tier == 1, str([(e.keyword, e.tier) for e in out]))

GUESS = {"candidate_markets": ["Gamma Widget Market, By Type", "Widget Market"]}
with fake_chat_json(recorded({"tiers": [{"id": 1, "tier": 1}]})):
    out = pipeline.run_tier_catalog(GUESS, ARTICLE, pool, LOG)
check("own market: with no own_market the first candidate guess is the name",
      out[0].keyword == "Gamma Widget Market" and out[0].tier == 1)

check("own market note: empty when a report names the market",
      pipeline.own_market_report_note(OWN, [entry(3, "Global Gamma Widgets Market")], LOG) == "")
note = pipeline.own_market_report_note(OWN, [entry(1, "Widget Market")], LOG)
check("own market note: says no CMI report is named for the market when only broader ones matched",
      note == "No CMI report named Gamma Widget Market is in the catalog; broader reports below are related, not the same market.", note)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all tier tests passed")
