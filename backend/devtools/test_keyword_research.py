"""Offline test for keyword_research: ranking real search phrases, reading a keyword's tail, building the secondary-keyword
pool in demand order, and feeding the pool to the planner and the real phrases to the demand prompt. No network, no model.

Run from backend/: python devtools/test_keyword_research.py
"""
import logging
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand  # noqa: E402
import keyword_research as kr  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402
import planning  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_keyword_research")
LOG.addHandler(logging.NullHandler())
LOG.propagate = False
MARKET = "Wealth Management Platform Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


@contextmanager
def patched(obj, name, fn):
    orig = getattr(obj, name)
    setattr(obj, name, fn)
    try:
        yield
    finally:
        setattr(obj, name, orig)


# ------------------------------------------------------------------ names the market
words = kr._core_words(MARKET)
check("core words: the market's own words without 'market'", words == ["wealth", "management", "platform"], str(words))
check("names_market: a plural still names it", kr._names_market("best wealth management platforms", words))
check("names_market: another market does not", not kr._names_market("asset management software", words))
check("names_market: a missing word does not", not kr._names_market("wealth management tools", words))

# ------------------------------------------------------------------ tail and section
check("tail: the words after the market's name, year dropped", kr.tail_of("wealth management platform market size 2026", MARKET) == "size")
check("tail: 'market' itself is not part of it", kr.tail_of("wealth management platform market share", MARKET) == "share")
check("tail: a phrase that names the market in other words has no tail", kr.tail_of("wealth management technology platform", MARKET) == "")
check("tail: a longer tail keeps up to three topic words", kr.tail_of("wealth management platform key players", MARKET) == "key players")
check("section: size -> report_context", kr.section_for("size") == "report_context")
check("section: share -> key_players", kr.section_for("share") == "key_players")
check("section: trends -> trends", kr.section_for("trends") == "trends")
check("section: a U.S. tail -> country_us", kr.section_for("share us") == "country_us")
check("section: no topic -> None", kr.section_for("zebra") is None)

# ------------------------------------------------------------------ collect_suggestions ranks by position, agreement and seed
CORE = "wealth management platform"
script = {
    ("google", f"{CORE} market"): [f"{CORE} market size", f"{CORE} market share", "unrelated phrase about cars"],
    ("bing", f"{CORE} market"): [f"{CORE} market size", f"{CORE} market growth"],
    ("google", f"{CORE} market a"): [f"{CORE} market analysis"],
}


def fake_fetch(url, logger):
    engine = "google" if "suggestqueries" in url else "bing"
    import urllib.parse
    seed = urllib.parse.unquote(url.split("q=")[-1] if engine == "google" else url.split("query=")[-1])
    return list(script.get((engine, seed), []))


with patched(kr, "_fetch", fake_fetch):
    got = kr.collect_suggestions(MARKET, LOG)
by_q = {s.query: s for s in got}
check("collect: only phrases that name the market are kept", "unrelated phrase about cars" not in by_q, str(list(by_q)))
check("collect: a phrase both engines list outranks one only one lists",
      got[0].query == f"{CORE} market size" and by_q[f"{CORE} market size"].engines == {"google", "bing"}, str([(s.query, round(s.score, 2)) for s in got]))
check("collect: a phrase from a letter seed counts half of a phrase from a plain seed",
      by_q[f"{CORE} market analysis"].score < by_q[f"{CORE} market growth"].score + 0.01 + 1.0, str(by_q[f"{CORE} market analysis"].score))
check("collect: the seed itself is never offered back", all(s.query != f"{CORE} market" for s in got))
with patched(kr, "_fetch", lambda url, logger: []):
    check("collect: every request failing gives an empty list, never an error", kr.collect_suggestions(MARKET, LOG) == [])

# ------------------------------------------------------------------ the pool
sug = [kr.Suggestion(f"{CORE} market size 2026", 5.0, {"google"}, 1), kr.Suggestion(f"{CORE} market share", 3.0, {"bing", "google"}, 2),
       kr.Suggestion(f"{CORE} revenue", 2.0, {"bing"}, 1), kr.Suggestion("wealth management technology platform", 9.0, {"bing"}, 1),
       kr.Suggestion(f"{CORE} market share in the us", 8.0, {"bing"}, 1), kr.Suggestion(f"{CORE} market zebra", 7.0, {"bing"}, 1)]
pool = kr.build_pool(sug, MARKET, LOG)
keys = [r["key"] for r in pool]
check("pool: real keys come first, in score order", keys[:3] == ["size", "share", "revenue"], str(keys[:5]))
check("pool: a phrase that names the market in other words, a U.S. tail and a non-topic tail are not keys",
      not any(k in keys for k in ("zebra", "us", "share us")) and not any("technology" in k for k in keys), str(keys))
check("pool: the standard keys fill in behind, so the planner always has its usual choices",
      {"forecast", "CAGR", "drivers", "trends", "growth"} <= set(keys), str(keys))
check("pool: each key says whether it came from searches or the standard list",
      {r["source"] for r in pool} == {"search", "standard"}, str({r["source"] for r in pool}))
check("pool: no search results at all gives exactly the standard list", [r["key"] for r in kr.build_pool([], MARKET, LOG)] == [k for k, _ in pipeline._SECONDARY_KEYWORD_SUFFIXES])

# ------------------------------------------------------------------ research() joins them and never raises
with patched(kr, "collect_suggestions", lambda m, l, limit=40: sug), patched(kr, "serp_terms", lambda m, l: {"market size": 2.0, "size": 2.0, "forecast": 1.5}):
    res = kr.research(MARKET, LOG)
check("research: ranking-page forms lift their keys (forecast moves ahead of unsearched keys)",
      [r["key"] for r in res["pool"]].index("forecast") < [r["key"] for r in res["pool"]].index("CAGR"), str([r["key"] for r in res["pool"]]))
check("research: the result carries the phrases, the ranking terms, the pool and a plain-words method",
      set(res) == {"suggestions", "ranking_terms", "pool", "method"} and "Google and Bing" in res["method"])


def boom(*a, **k):
    raise RuntimeError("down")


with patched(kr, "collect_suggestions", boom), patched(kr, "serp_terms", lambda m, l: {}):
    res = kr.research(MARKET, LOG)
check("research: a failing search never stops the run and leaves the standard pool",
      res["suggestions"] == [] and [r["key"] for r in res["pool"]] == [k for k, _ in pipeline._SECONDARY_KEYWORD_SUFFIXES])

# ------------------------------------------------------------------ the planner takes the pool; the demand prompt takes the phrases
check("planner: the pool is the research's order and sections",
      planning._secondary_pool({"keyword_research": {"pool": [{"key": "size", "section": "report_context"}, {"key": "share", "section": "key_players"}]}})
      == [("size", "report_context"), ("share", "key_players")])
check("planner: no research falls back to the fixed list", planning._secondary_pool({}) == list(pipeline._SECONDARY_KEYWORD_SUFFIXES))
check("planner: a bad row (unknown section, duplicate key) is skipped",
      planning._secondary_pool({"keyword_research": {"pool": [{"key": "size", "section": "nope"}, {"key": "Size", "section": "report_context"}, {"key": "size", "section": "trends"}]}})
      == [("Size", "report_context")])

seen = {}


def fake_chat(system, user, **kw):
    seen["user"] = user
    return {"questions": [], "terms": [], "long_tail": []}


with patched(llm, "chat_json", fake_chat):
    demand.run_search_demand(MARKET, {}, LOG, real_queries=["best wealth management platforms", "wealth management platform providers"])
check("demand prompt: the real searches are listed and the long-tail step is told to prefer them",
      "- best wealth management platforms" in seen["user"] and "Prefer the REAL SEARCHES" in seen["user"], seen["user"][-300:])
with patched(llm, "chat_json", fake_chat):
    demand.run_search_demand(MARKET, {}, LOG)
check("demand prompt: no real searches says so and still builds", "(none found)" in seen["user"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all keyword research tests passed")
