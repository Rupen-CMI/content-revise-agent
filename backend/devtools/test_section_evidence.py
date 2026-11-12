"""Offline checks for the Evidence step's section layer (no network, no real LLM calls): the section search plan from a
confirmed fact sheet, the two fixed sub-segments, queries grouped by section and the Bing budget allotted per section (no
segmentation section, the U.S. queries inside the one search), hits that carry their sections, pages read while Bing still
runs, the one cleaning pass, SELECT assigning exactly one section per item, the planner accepting the fixed sub-segments, the
per-section minimums as warnings only (no broader round, no top-up search), and the claim search no longer starting in the
market-first chain.
Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_section_evidence.py   (-v prints the pipeline's log)"""
import copy
import logging
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import feeds  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import pipeline  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402
import search  # noqa: E402
import sections  # noqa: E402
import us_search  # noqa: E402

LOG = logging.getLogger("test_section_evidence")
LOG.setLevel(logging.INFO)
LOG.addHandler(logging.StreamHandler(sys.stdout) if "-v" in sys.argv else logging.NullHandler())
LOG.propagate = False
FAILURES: list[str] = []
MARKET = "Widget Services Market"
NOW = datetime.now(timezone.utc)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


@contextmanager
def patched(*specs):
    origs = []
    for obj, name, fn in specs:
        origs.append((obj, name, getattr(obj, name)))
        setattr(obj, name, fn)
    try:
        yield
    finally:
        for obj, name, orig in reversed(origs):
            setattr(obj, name, orig)


def share(amount: float) -> dict:
    return {"figure": f"{amount}%", "kind": "share", "amount": amount, "quote": "q"}


def statement(key: str, n: int, text: str, **extra) -> dict:
    return {"id": f"fs.{key}.{n}", "text": text, "quote": "q", "historical": False, **extra}


FS = {
    "size": None, "forecast": None, "cagr": None, "regions": {"leader": None, "fastest": None}, "gaps": [],
    "segments": [
        {"id": "fs.segments.0", "type": "By Type", "items": ["Software", "Services", "Hardware"],
         "figures": {"Software": share(52.0), "Services": share(30.0)}, "dominant": {"name": "Software", "basis": "stated", "quote": "q"}},
        {"id": "fs.segments.1", "type": "By Region", "items": ["North America", "Europe"], "figures": {},
         "dominant": {"name": "North America", "basis": "stated", "quote": "q"}},
        {"id": "fs.segments.2", "type": "By End User", "items": ["Banks", "Insurers"], "figures": {}, "dominant": None},
        {"id": "fs.segments.3", "type": "By Deployment", "items": ["Cloud", "On-premise", "Hybrid"],
         "figures": {"Cloud": share(61.0), "On-premise": share(25.0)}, "dominant": None},
    ],
    "drivers": [statement("drivers", i, f"Driver {i}: demand for widget automation grows") for i in range(4)],
    "opportunities": [statement("opportunities", i, f"Opportunity {i}: widget platforms enter new regions") for i in range(3)],
    "us": [statement("us", 0, "U.S. banks lead widget adoption"), statement("us", 1, "U.S. federal widget policy expands")],
    "segment_drivers": [statement("segment_drivers", 0, "Banks want audited workflows", split="By Type", name="Software"),
                        statement("segment_drivers", 1, "Elsewhere", split="By Type", name="Hardware")],
    "players": ["Acme Corp", "Monday"],
}
REPORT = SimpleNamespace(name=MARKET, segment_terms=["Software", "Services"])

class _Capture(logging.Handler):
    """Collects the messages the code under test logs, for a check to assert on."""
    def __init__(self):
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())


# ------------------------------------------------------------------------------------------ the sub-segment choice
picks = sections.choose_subsegments(FS, LOG)
check("sub-segments: the split whose stated leader has the highest share, its top two categories, leader first",
      [(p["split"], p["name"], p["basis"]) for p in picks]
      == [("By Type", "Software", "dominant"), ("By Type", "Services", "second_in_split")], str(picks))

charging = {"segments": [{"id": "fs.segments.0", "type": "By Charging Type", "items": ["On-board Chargers", "AC Chargers"],
                          "figures": {"On-board Chargers": share(89.0), "AC Chargers": share(8.0)},
                          "dominant": {"name": "On-board Chargers", "basis": "stated", "quote": "q"}}]}
check("sub-segments: a stated leader with a stated second gives both from that split",
      [(p["split"], p["name"], p["basis"]) for p in sections.choose_subsegments(charging, LOG)]
      == [("By Charging Type", "On-board Chargers", "dominant"), ("By Charging Type", "AC Chargers", "second_in_split")])

one_figured = {"segments": [
    {"id": "fs.segments.0", "type": "By Size", "items": ["Delta", "Epsilon"], "figures": {"Delta": share(70.0)}, "dominant": None},
    {"id": "fs.segments.1", "type": "By Mode", "items": ["Alpha", "Beta"], "figures": {}, "dominant": {"name": "Alpha", "basis": "stated", "quote": "q"}},
]}
check("sub-segments: a split with only one stated figure takes another split's leader as the second",
      [(p["split"], p["name"], p["basis"]) for p in sections.choose_subsegments(one_figured, LOG)]
      == [("By Size", "Delta", "share"), ("By Mode", "Alpha", "other_split_leader")])

region_rich = {"segments": [
    {"id": "fs.segments.0", "type": "By Region", "items": ["Asia", "Europe"], "figures": {"Asia": share(60.0)},
     "dominant": {"name": "Asia", "basis": "stated", "quote": "q"}},
    {"id": "fs.segments.1", "type": "By Type", "items": ["Software", "Services"], "figures": {"Software": share(52.0), "Services": share(30.0)},
     "dominant": {"name": "Software", "basis": "stated", "quote": "q"}},
]}
check("sub-segments: a region split is never the chosen split, whatever its leader's share",
      all(p["split"] != "By Region" for p in sections.choose_subsegments(region_rich, LOG)))

bare = {"segments": [
    {"id": "fs.segments.0", "type": "By Mode", "items": ["Alpha", "Beta"], "figures": {}, "dominant": None},
    {"id": "fs.segments.1", "type": "By Size", "items": ["Gamma", "Delta"], "figures": {}, "dominant": None},
]}
cap = _Capture()
LOG.addHandler(cap)
d = sections.choose_subsegments(bare, LOG)
LOG.removeHandler(cap)
check("sub-segments: no leader and no share anywhere falls back to the first split's first two categories, logged",
      [(p["split"], p["name"], p["basis"]) for p in d] == [("By Mode", "Alpha", "fallback"), ("By Mode", "Beta", "fallback")]
      and any("no split states" in m for m in cap.records), str((d, cap.records)))

check("sub-segments: a region split is used only when no other split exists",
      [(p["split"], p["name"], p["basis"]) for p in sections.choose_subsegments(
          {"segments": [{"id": "fs.segments.0", "type": "By Region", "items": ["Asia", "Europe"], "figures": {},
                         "dominant": {"name": "Asia", "basis": "stated", "quote": "q"}}]}, LOG)]
      == [("By Region", "Asia", "dominant")])
check("sub-segments: a one-category split's second is another split's leader",
      [(p["split"], p["name"], p["basis"]) for p in sections.choose_subsegments(
          {"segments": [{"id": "fs.segments.0", "type": "By Type", "items": ["Software"], "figures": {"Software": share(70.0)},
                         "dominant": {"name": "Software", "basis": "stated", "quote": "q"}},
                        {"id": "fs.segments.1", "type": "By Mode", "items": ["Cloud", "On-premise"], "figures": {"Cloud": share(60.0)},
                         "dominant": None}]}, LOG)]
      == [("By Type", "Software", "dominant"), ("By Mode", "Cloud", "other_split_leader")])
check("sub-segments: with nothing to fill the pair, one sub-segment is kept",
      [p["name"] for p in sections.choose_subsegments(
          {"segments": [{"id": "fs.segments.0", "type": "By Size", "items": ["Delta", "Epsilon"], "figures": {"Delta": share(70.0)},
                         "dominant": None}]}, LOG)] == ["Delta"])
check("sub-segments: no splits give none", sections.choose_subsegments({}) == [])
check("sub-segments: the same sheet always gives the same pair", sections.choose_subsegments(FS, LOG) == picks)

# ------------------------------------------------------------------------------------------------ the search plan
plan = pipeline.section_search_plan(FS, REPORT, FS["players"], picks)
check("plan: trends are the top drivers then opportunities, one target each, as many as the trends allotment",
      [t["kind"] for t in plan["trends"]] == ["drivers"] * 3 + ["opportunities"] * 2 + ["drivers"] and len(plan["trends"]) == sections.TREND_QUERIES
      and plan["trends"][0]["text"].startswith("Driver 0"), str(plan["trends"]))
check("plan: the sub-segment targets are the two fixed ones, with the report's own reason the category leads",
      [t["name"] for t in plan["subsegment"]] == ["Software", "Services"]
      and plan["subsegment"][0]["reason"] == "Banks want audited workflows" and plan["subsegment"][1]["reason"] == "",
      str(plan["subsegment"]))
check("plan: there is no segmentation section in the search plan (it is written from the report's facts alone)",
      "segmentation" not in plan and set(plan) == {"trends", "subsegment", "us", "key_players", "market"}, str(sorted(plan)))
check("plan: the U.S. seeds are the report's own U.S. statements", [t["text"] for t in plan["us"]] == [s["text"] for s in FS["us"]])
check("plan: key players are the cleaned names ('Monday' becomes its domain)", [t["text"] for t in plan["key_players"]] == ["Acme", "monday.com"],
      str(plan["key_players"]))
check("plan: the market target is the market's name", plan["market"] == [{"text": MARKET}])
check("plan: with no drivers, opportunities fill the trends",
      len(pipeline.section_search_plan({**FS, "drivers": []}, REPORT, [])["trends"]) == 3)
check("plan: the targets are printed for the query prompt, for the sections the model writes queries for only",
      all(h in sections.format_targets(plan) for h in ("TRENDS", "SUB-SEGMENTS", "MARKET")) and "Driver 0" in sections.format_targets(plan)
      and "Software (split By Type) — the report's reason it leads: Banks want audited workflows" in sections.format_targets(plan)
      and not any(h in sections.format_targets(plan) for h in ("SEGMENTATION", "KEY PLAYERS", "U.S.")))

# ----------------------------------------------------------------------------------------------- grouped queries
parsed = sections.parse_grouped_queries({
    "trends": ["a one", "a one", " b  two "], "Sub-Segment": ["s1"], "key players": ["k1"], "U.S.": ["u1"], "market": ["m1"], "bogus": ["x"],
    "segmentation": ["no longer a section"]})
check("queries: section names are recognised in any spelling, duplicates and unknown sections (segmentation too) dropped",
      parsed == {"trends": ["a one", "b two"], "subsegment": ["s1"], "us": ["u1"], "key_players": ["k1"], "market": ["m1"]},
      str(parsed))
check("queries: a plain list (the old shape) is all 'market'", sections.parse_grouped_queries(["q1", "q2"])["market"] == ["q1", "q2"])
check("queries: nothing usable gives empty groups", not any(sections.parse_grouped_queries(None).values()))
flat, of = sections.flatten_queries({"trends": ["t1", "t2"], "subsegment": ["s1"], "us": ["T1"], "key_players": ["k1"], "market": ["m1"]})
check("queries: one per section in turn, a query two sections wrote is run once and keeps both sections",
      flat == ["t1", "s1", "k1", "m1", "t2"] and of["t1"] == ["trends", "us"], f"{flat} {of}")

# ------------------------------------------------------------------------------------------ the Bing budget, per section
core = pipeline._market_core(MARKET)
us_qs = us_search.us_news_queries(core)
player_qs = pipeline._player_news_queries(MARKET, ["Acme Corp", "Beta Inc", "Gamma Ltd", "Delta Co", "Epsilon LLC", "Zeta"])
wide = {"trends": [f"trend {i}" for i in range(7)], "subsegment": ["model sub 1"], "us": ["model us"], "key_players": ["model player"],
        "market": [f"market {i}" for i in range(5)]}
budget = sections.budgeted_queries(wide, core, picks, us_qs, player_qs)
check("budget: trends 6, each sub-segment 3, U.S. 3, key players 6, market 4 (25 keyword queries, 28 Bing queries with the site: ones) plus at most 3 site: queries",
      [len(budget[s]) for s in ("trends", "subsegment", "us", "key_players", "market")] == [6, 6, 3, 6, 4]
      and sum(len(v) for v in budget.values()) == sections.keyword_query_budget(picks) == 25 and feeds._BING_MAX_SITE_QUERIES == 3,
      str(budget))
check("budget: the trends and market queries are the model's first ones, in order",
      budget["trends"] == [f"trend {i}" for i in range(6)] and budget["market"] == [f"market {i}" for i in range(4)])
check("budget: with no model sub-segment queries the code-built shape stands in, three per sub-segment",
      budget["subsegment"] == [f"Software {core} demand", f"Software {core} launch", f"Software {core} news",
                               f"Services {core} demand", f"Services {core} launch", f"Services {core} news"], str(budget["subsegment"]))
check("budget: the U.S. queries are the section's own and each names the U.S.; the key-player queries are the code's, not the model's",
      budget["us"] == us_qs[:3] and all("U.S." in q or "United States" in q for q in budget["us"]) and budget["key_players"] == player_qs[:6]
      and "model player" not in budget["key_players"], str(budget))
check("budget: with no known player the model's own key-player queries stand in",
      sections.budgeted_queries(wide, core, picks, us_qs, [])["key_players"] == ["model player"])
check("budget: no sub-segments, no sub-segment queries; the input groups are not changed",
      sections.budgeted_queries(wide, core, [], us_qs, player_qs)["subsegment"] == [] and len(wide["trends"]) == 7
      and sections.keyword_query_budget([]) == 19)
flat_budget, _ = sections.flatten_queries(budget)
check("budget: flattened, every allotted query is there once (no section starves another)", len(flat_budget) == 25, str(len(flat_budget)))
check("budget: the segmentation section is gone from the queries, the evidence sections and the counts",
      "segmentation" not in sections.QUERY_SECTIONS and "segmentation" not in sections.EVIDENCE_SECTIONS
      and "segmentation" not in sections.counts([], picks) and "segmentation" not in budget)

grouped_answer = {"queries": {"trends": ['"widget" automation', '"widget" adoption'], "market": ['"widget" news'],
                              "segmentation": ['"widget" audited']},
                  "source_groups": ["not-a-pack"], "topic_terms": ["Widget Maker", "widget maker", "widget supply"]}
seen_prompts: list[str] = []


def fake_chat(system, user, **kw):
    seen_prompts.append(user)
    return copy.deepcopy(grouped_answer)


with patched((llm, "chat_json", fake_chat)):
    queries, groups, terms = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, ["Software"], plan)
check("queries: one call returns the queries grouped by section (a segmentation answer is ignored)",
      isinstance(queries, dict) and queries["trends"] == ['"widget" automation', '"widget" adoption']
      and queries["market"] == ['"widget" news'] and set(queries) == set(sections.QUERY_SECTIONS), str(queries))
check("queries: topic terms are kept (deduplicated, lower-cased) and an unknown source group is dropped", terms == ["widget maker", "widget supply"] and groups == [],
      f"{terms} {groups}")
check("queries: the prompt carries the trends, sub-segment and market targets, the shared query rules, and asks for no segmentation or U.S. queries",
      "Driver 0" in seen_prompts[0] and "ANCHOR" in seen_prompts[0] and "SOURCE GROUPS" in seen_prompts[0]
      and "Software (split By Type) — the report's reason it leads: Banks want audited workflows" in seen_prompts[0]
      and '"segmentation"' not in seen_prompts[0] and '"us"' not in seen_prompts[0]
      and '"subsegment"' in seen_prompts[0] and "Never the literal" in seen_prompts[0], seen_prompts[0][:200])
check("queries: the flat prompt of the old chain is untouched (still the 9 categories)", "9 categories" in prompts.SEARCH_QUERIES_USER
      and "grouped" not in prompts.SEARCH_QUERIES_USER)

# the model's own sub-segment queries: parsed per sub-segment, the code-built shape standing in for one it wrote none for
sub_answer = {"queries": {"trends": ['"widget" automation'], "market": ['"widget" news'],
                          "subsegment": {"Software": ["audited workflow software for banks launch", "Software widget services demand"],
                                         "Services": [], "Bogus": ["not a fixed sub-segment"]}},
              "source_groups": [], "topic_terms": []}


def fake_sub_chat(system, user, **kw):
    return copy.deepcopy(sub_answer)


with patched((llm, "chat_json", fake_sub_chat)):
    sub_queries, _, _ = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, ["Software"], plan)
check("queries: the model's sub-segment queries come back per sub-segment, capped at three, a category the fixed pair does not name is dropped",
      sub_queries["subsegment"] == {"Software": ["audited workflow software for banks launch", "Software widget services demand"]},
      str(sub_queries["subsegment"]))
check("queries: the sub-segment section label stays 'subsegment' (SELECT assigns the category later)",
      set(sub_queries["subsegment"]) <= {t["name"] for t in plan["subsegment"]} and "subsegment" in sub_queries, str(sub_queries))
sub_budget = sections.budgeted_queries(sub_queries, core, picks, us_qs, player_qs)
check("budget: the model's queries fill their sub-segment's allotment, the code-built shape stands in for one it wrote none for",
      sub_budget["subsegment"] == ["audited workflow software for banks launch", "Software widget services demand",
                                   f"Services {core} demand", f"Services {core} launch", f"Services {core} news"],
      str(sub_budget["subsegment"]))
flat_sub = {"queries": {"subsegment": ["audited workflow software for banks launch", "widget services rollout", "nothing like either"]}}
check("queries: a flat sub-segment list is matched to the fixed sub-segments by its words, the unmatched dropped",
      sections.parse_subsegment_queries(flat_sub["queries"], plan["subsegment"])
      == {"Software": ["audited workflow software for banks launch"], "Services": ["widget services rollout"]},
      str(sections.parse_subsegment_queries(flat_sub["queries"], plan["subsegment"])))
check("queries: an unusable sub-segment part leaves nothing (the code-built shape covers every sub-segment)",
      sections.parse_subsegment_queries({"subsegment": "nope"}, plan["subsegment"]) == {}
      and sections.parse_subsegment_queries(None, plan["subsegment"]) == {}
      and sections.parse_subsegment_queries({"subsegment": {"Software": ["x"]}}, []) == {})
check("budget: a flat sub-segment list in the grouped queries is ignored in favour of the code-built shape",
      sections.budgeted_queries({**wide, "subsegment": ["model sub 1"]}, core, picks, us_qs, player_qs)["subsegment"]
      == [f"Software {core} demand", f"Software {core} launch", f"Software {core} news",
          f"Services {core} demand", f"Services {core} launch", f"Services {core} news"])


def failing_chat(*a, **kw):
    raise llm.LLMError("down")


with patched((llm, "chat_json", failing_chat)):
    fallback, _, _ = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, [], plan)
check("queries: when the model fails the deterministic templates still come back grouped by section",
      isinstance(fallback, dict) and fallback["market"] and fallback["trends"] and set(fallback) == set(sections.QUERY_SECTIONS), str(fallback))
with patched((llm, "chat_json", lambda *a, **kw: {"queries": ["flat one"], "source_groups": [], "topic_terms": []})):
    old_shape = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, [])
check("queries: without a plan the old chain still gets a flat list", old_shape[0] == ["flat one"])


# -------------------------------------------------------------------------------------------- hits carry sections
def article(title: str, url: str, days: int = 3, secs=()) -> search.SearchHit:
    h = search.SearchHit(title=title, snippet="Widget services vendors expand their widget services platform for banks",
                         url=url, source="Trade Daily", date=NOW - timedelta(days=days), is_news=True)
    h.add_sections(secs)
    return h


class FakeBing(feeds._Bing):
    def __init__(self):
        super().__init__(None, LOG)

    def search(self, query, web=False):
        if '"' in query:
            return []
        return [article(f"Result for {query} widget services", f"https://news.example/{abs(hash(query)) % 9999}")]


old_delay = (feeds._BING_QUERY_DELAY_S, feeds._BING_JITTER_S)
feeds._BING_QUERY_DELAY_S = feeds._BING_JITTER_S = 0
found = FakeBing().run(['"widget" automation', "widget software", "plain"],
                       {'"widget" automation': ["trends"], "widget software": ["subsegment", "us"]}, ("market",))
feeds._BING_QUERY_DELAY_S, feeds._BING_JITTER_S = old_delay
check("hits: each carries the sections of the query that found it (also after the unquoted retry), a query with none gets the default",
      [h.sections for h in found] == [["trends"], ["subsegment", "us"], ["market"]], str([h.sections for h in found]))


def pages() -> list[search.SearchHit]:
    return [
        article("Acme launches widget services platform for banks", "https://news.example/a", secs=["trends"]),
        article("Other headline entirely for widget services buyers", "https://news.example/a", secs=["us"]),
        article("Acme launches widget services platform for banks!", "https://news.example/b", secs=["key_players"]),
        article("Beta raises funds for widget services expansion", "https://news.example/c", secs=["subsegment"]),
        article("Gamma partners with banks on widget services pilots", "https://news.example/d", secs=["market"]),
    ]


merged = sections.merge_duplicate_hits(pages())
check("cross-section dedupe: the same URL or headline is one hit that keeps every section that found it",
      len(merged) == 3 and merged[0].url.endswith("/a") and merged[0].sections == ["trends", "us", "key_players"], str([h.sections for h in merged]))

gather_calls: list[dict] = []
EXCERPTS = {"https://news.example/a": "Banks across the U.S. adopt widget services platforms."}


class StubReader:
    """feeds.ExcerptReader without a network: records which pages were started early and which were read at the end."""
    instances: list = []

    def __init__(self, market_name, logger=None, focus=""):
        self.started, self.finished, self.closed = [], None, False
        StubReader.instances.append(self)

    def start(self, hits):
        self.started += [h.url for h in hits]

    def finish(self, hits):
        self.finished = [h.url for h in hits]
        for h in hits:
            h.excerpt = EXCERPTS.get(h.url, "")

    def close(self):
        self.closed = True


def fake_gather(market_name, core, queries, groups, players, logger, topic_terms, after_keywords, **kw):
    gather_calls.append({"queries": queries, **kw})
    kw["on_hits"](pages()[:4])          # what Bing has found after some of its queries: the pages start being read
    return pages() + [article("Banks elsewhere expand widget services pilots across Europe", "https://news.example/us-only", secs=["us"])]


def no_bing(*a, **kw):
    raise AssertionError("no second Bing round may run after the one search")


old_mode = config.SEARCH_DDGS_MODE
config.SEARCH_DDGS_MODE = "off"
try:
    with patched((feeds, "gather", fake_gather), (feeds, "ExcerptReader", StubReader), (feeds, "bing_search", no_bing)):
        hits = pipeline.run_search_evidence(
            MARKET, SimpleNamespace(links=[]), [], LOG, key_players=["Acme Corp"], orient={}, search_queries=(copy.deepcopy(queries), [], ["widget"]),
            subsegments=picks)
finally:
    config.SEARCH_DDGS_MODE = old_mode
sent = gather_calls[0]
reader = StubReader.instances[0]
check("one search pass: every section's queries go to the one gather call within the budget, the player query under key_players",
      sent["query_sections"].get('"widget" automation') == ["trends"] and "key_players" in sent["query_sections"].get("acme widget", [])
      and sent["max_keyword_queries"] == len(sent["queries"]) <= sections.keyword_query_budget(picks), str(sent["query_sections"]))
check("one search pass: the sub-segment queries and the U.S. queries are in it (the U.S. needs no search of its own)",
      sent["query_sections"].get(f"software {core} demand") == ["subsegment"] and sent["query_sections"].get(f'"{core}" united states') == ["us"],
      str(sorted(sent["query_sections"])))
check("one cleaning pass: duplicates merged across sections, each page once",
      len(hits) == 3 and len({h.url for h in hits}) == 3, str([(h.title, h.sections) for h in hits]))
check("one cleaning pass: the merged page carries every section's tag (its pages name the U.S., so 'us' stays)",
      next(h for h in hits if h.url.endswith("/a")).sections == ["trends", "us", "key_players"])
check("pages are read while Bing runs: the hits of the early batch were started, and the final pass read the final hits",
      len(reader.started) >= 1 and set(reader.started) <= set(reader.finished) and {h.url for h in hits} <= set(reader.finished)
      and not reader.closed, f"{reader.started} {reader.finished}")
check("U.S. pages: one that names the U.S. in its text is marked scope 'us'; a page found only for 'us' that does not is dropped",
      next(h for h in hits if h.url.endswith("/a")).scope == "us" and not any(h.url.endswith("us-only") for h in hits))
check("hits: counts per section count a two-section page in both",
      sections.hit_counts(sections.merge_duplicate_hits(pages())) == {"trends": 1, "us": 1, "key_players": 1, "subsegment": 1, "market": 1})
many = [article(f"Trend story number {i} on widget services growth", f"https://news.example/t{i}", secs=["trends"]) for i in range(30)]
few_us = [article(f"United States widget services pilot {i} announced", f"https://news.example/u{i}", secs=["us"]) for i in range(3)]
capped = sections.cap_hits(many + few_us, 20, 3)
check("shortlist: every section keeps its own hits before the prolific one fills the rest",
      len(capped) == 20 and sum(1 for h in capped if h.sections == ["us"]) == 3 and capped[:2] == many[:2])

# ------------------------------------------------------------------ pages read as the hits arrive (feeds.ExcerptReader)
fetched: list[str] = []
PAGE_WORDS = " ".join(["Widget services vendors expand their platforms for banks across several regions this year."] * 14)


class FakePage:
    status_code = 200
    headers = {"content-type": "text/html"}
    text = "<html></html>"


class FakePageClient:
    def __init__(self, *a, **kw):
        pass

    def get(self, url, **kw):
        fetched.append(url)
        return FakePage()

    def close(self):
        pass


def page_hit(i: int, host: str = "news.example") -> search.SearchHit:
    return article(f"Widget services story {i} for banks", f"https://{host}/p{i}")


with patched((feeds.httpx, "Client", FakePageClient), (feeds.trafilatura, "extract", lambda *a, **kw: PAGE_WORDS), (feeds, "_HOST_GAP_S", 0.0)):
    early = [page_hit(i) for i in range(3)]
    early_reader = feeds.ExcerptReader(MARKET, LOG)
    early_reader.start(early)
    early_reader.start(early)                      # the same pages offered again are not read twice
    late = [page_hit(9, "other.example")]
    early_reader.finish(early[:2] + late)
check("reader: a page started early is read once and again offered at the end is not read twice; the late one is read at the end",
      sorted(fetched) == sorted(h.url for h in early) + [late[0].url] and all(h.excerpt for h in early[:2] + late), str(fetched))
fetched.clear()
with patched((feeds.httpx, "Client", FakePageClient), (feeds.trafilatura, "extract", lambda *a, **kw: PAGE_WORDS), (feeds, "_HOST_GAP_S", 0.0)):
    many_one_site = [page_hit(i) for i in range(feeds._MAX_FETCHES_PER_HOST + 3)]
    feeds.attach_excerpts(many_one_site, MARKET, LOG)
check("reader: attach_excerpts keeps its old contract (a site's first pages only, each read once)",
      len(fetched) == feeds._MAX_FETCHES_PER_HOST and len(set(fetched)) == len(fetched)
      and all(h.excerpt for h in many_one_site[:feeds._MAX_FETCHES_PER_HOST]) and not many_one_site[-1].excerpt, str(fetched))

# ----------------------------------------------------- gather: on_hits after every query, the timing line, the site: cap
class CtxClient:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class CountingBing:
    def __init__(self, client, logger):
        self.queries, self.disabled = 0, False

    def run(self, queries, sections_for=None, default=(), on_batch=None):
        out = []
        for q in queries:
            self.queries += 1
            out.append(article(f"Found by {q} widget services", f"https://news.example/{self.queries}"))
            if on_batch:
                on_batch(list(out))
        return out


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


cap_log = logging.getLogger("test_section_evidence.gather")
cap_log.setLevel(logging.INFO)
cap_log.propagate = False
capture = Capture()
cap_log.addHandler(capture)
seen_batches: list[int] = []
with patched((feeds.httpx, "Client", CtxClient), (feeds, "_Bing", CountingBing), (feeds, "_fetch_feeds", lambda *a, **kw: []),
             (feeds, "feeds_for", lambda g: [])):
    gathered = feeds.gather(MARKET, core, [f"q{i}" for i in range(6)], ["not-a-pack"], [], cap_log, [], None,
                            query_sections={}, max_keyword_queries=6, on_hits=lambda hs: seen_batches.append(len(hs)))
site_qs = feeds.site_queries(core, [])
check("gather: on_hits is called after every Bing query with every Bing hit so far (the early batch, then the rest, site: queries included)",
      seen_batches == list(range(1, 6 + len(site_qs) + 1)) and len(gathered) == 6 + len(site_qs), f"{seen_batches} {len(site_qs)}")
check("gather: at most three site: queries (the three broadest news hosts), none from the trade-press packs",
      len(site_qs) == 3 and "reuters.com" in site_qs[0] and "crunchbase" in site_qs[2], str(site_qs))
check("gather: the Bing query count and time are logged as a [timing] line",
      any(l.startswith("[timing] Bing queries: 9 in ") and l.endswith("s") for l in capture.lines), str(capture.lines))

# ------------------------------------------------------------------------------- SELECT assigns exactly one section
SUBS = [{"split": "By Type", "name": "Software"}, {"split": "By Deployment", "name": "Cloud"}]
ARTICLE = SimpleNamespace(title="A blog")
ORIENT = {"topic_summary": "t", "bridge_angle": "b"}
SPONSOR = SimpleNamespace(text="Client paragraph.", links=[])


def pool() -> list[search.SearchHit]:
    specs = [
        ("Acme launched a widget automation suite for banks", ["trends"], 3),         # W1
        ("Beta launched cloud widget services across Europe", ["subsegment"], 4),     # W2
        ("Gamma launched audited widget workflows for software teams", ["segmentation"], 5),  # W3
        ("Delta launched widget services pilots with U.S. banks", ["us"], 6),         # W4
        ("Epsilon acquired a widget services vendor in Texas", ["key_players"], 7),   # W5
        ("Zeta launched an undated widget services product line", ["trends"], None),  # W6
        ("Eta launched a hardware widget services line", ["subsegment"], 9),          # W7
        ("Theta launched widget services for insurers in Spain", ["market"], 10),     # W8
        ("Iota launched widget services training for banks", ["trends"], 11),         # W9
        ("Kappa launched widget services monitoring for banks", ["trends"], 12),      # W10
    ]
    out = []
    for title, secs, days in specs:
        h = article(title, f"https://news.example/{title.split()[0].lower()}", days or 1, secs)
        if days is None:
            h.date = None
        out.append(h)
    return out


def nm(i: int) -> str:
    """A distinct company name with no digit (SELECT's figure check drops a fact whose number is not in its source)."""
    return chr(65 + i % 26) * 3 + chr(97 + i // 26) * 2


def ev(i: int, use_for, fact: str | None = None) -> dict:
    return {"id": f"W{i}", "fact": fact or f"{nm(i)} Corp launched a widget services product in October 2026.", "detail": "", "use_for": use_for}


answer = {"evidence": [
    ev(1, ["trends"]), ev(2, ["subsegment:Cloud"]), ev(3, ["segmentation", "trends"]), ev(4, ["us"]), ev(5, ["players"]),
    ev(6, ["trends"]), ev(7, ["subsegment:Hardware"]), ev(8, ["context"]), ev(9, "us|trends"), ev(10, ["context"]),
], "claim_backing": [], "question_backing": [], "report_claim_backing": []}
prompts_sent: list[str] = []


def select_chat(system, user, **kw):
    prompts_sent.append(user)
    return copy.deepcopy(answer)


with patched((llm, "chat_json", select_chat)):
    sel = pipeline.run_select(ARTICLE, ORIENT, SPONSOR, REPORT, pool(), LOG, subsegments=SUBS)
by_id = {e["id"]: e for e in sel["evidence"]}
check("select: the candidates are shown grouped by the section whose search found them",
      "=== FOUND BY THE TRENDS SEARCH ===" in prompts_sent[0] and "=== FOUND BY THE SUB-SEGMENT SEARCH" in prompts_sent[0]
      and "(Software; Cloud)" in prompts_sent[0] and "[W5]" in prompts_sent[0], prompts_sent[0][:300])
check("select: the prompt names the two sub-segments and drops the 'context' catch-all",
      'subsegment:Software' in prompts_sent[0] and 'subsegment:Cloud' in prompts_sent[0] and "there is no \"context\"" in prompts_sent[0])
check("select: each kept item has exactly one section and one matching tag",
      all(len(e["use_for_all"]) == 1 and e["section"] in sections.EVIDENCE_SECTIONS for e in sel["evidence"]), str(sorted(by_id)))
check("select: trends, us, players and a sub-segment tag map to their sections",
      [by_id.get(k, {}).get("section") for k in ("W1", "W4", "W5", "W2")] == ["trends", "us", "key_players", "subsegment"]
      and by_id.get("W2", {}).get("subsegment") == "Cloud", str({k: v["section"] for k, v in by_id.items()}))
check("select: the key-players section keeps the tag the rest of the chain reads ('players')",
      by_id["W5"]["use_for"] == "players" and by_id["W2"]["use_for"] == "subsegment:Cloud" and by_id["W4"]["use_for_all"] == ["us"])
check("select: the retired 'segmentation' entry names no section, so the next entry counts (here trends)",
      by_id["W3"]["section"] == "trends" and by_id["W3"]["use_for_all"] == ["trends"])
seg_only = {"evidence": [ev(3, ["segmentation"], "Gamma launched audited widget workflows for software teams in October 2026.")]}
pipeline._assign_sections(seg_only, pool(), SUBS, LOG)
check("select: an item that names only the retired 'segmentation' goes to the section its hit was found for (never a segmentation section)",
      seg_only["evidence"] and seg_only["evidence"][0]["section"] == "trends" and '- "segmentation"' not in prompts_sent[0], str(seg_only))
check("select: a sub-segment that is not one of the two fixed is dropped, never filed elsewhere", "W7" not in by_id)
check("select: a leftover 'context' goes to the section its hit was found for (the general search is trends)",
      by_id["W8"]["section"] == "trends" and by_id["W10"]["section"] == "trends")
check("select: an undated hit is never a trend (its only section was trends, so it is dropped)", "W6" not in by_id)
check("select: a 'us|trends' string takes its first entry", by_id["W9"]["section"] == "us")

ctx_only = {"evidence": [ev(2, ["context"], "Beta launched cloud widget services across Europe in October 2026.")]}
pipeline._assign_sections(ctx_only, pool(), SUBS, LOG)
check("select: a 'context' item whose hit came from the sub-segment search goes to the sub-segment its text names",
      ctx_only["evidence"][0]["section"] == "subsegment" and ctx_only["evidence"][0]["subsegment"] == "Cloud", str(ctx_only))

event_hits = [article("Acme launched a widget automation suite", "https://news.example/s1", 5, ["trends"]),
              article("Acme launches a widget automation suite", "https://news.example/s2", 5, ["key_players"])]
event_sel = {"evidence": [
    {"id": "W1", "fact": "Acme Corp launched a widget automation suite for banks in October 2026.", "detail": "", "use_for": ["trends"]},
    {"id": "W2", "fact": "Acme Corp launched a widget automation suite for banks, adding audit trails, in October 2026.", "detail": "More.", "use_for": ["players"]},
]}
pipeline._merge_same_events(event_sel, event_hits, LOG)
check("cross-section dedupe: one event selected for two sections stays once (the fuller item)",
      [e["id"] for e in event_sel["evidence"]] == ["W2"], str(event_sel))

# ------------------------------------------------------------------------- the evidence block and build_inputs
block = pipeline.run_build_evidence(sel, pool(), LOG)
parsed_items = pipeline._parse_evidence(block)
check("evidence block: a sub-segment item carries its category in its tag and parses back to the sub-segment group",
      any(i["tags"] == ["subsegment:Cloud"] and i["group"] == "subsegment" for i in parsed_items)
      and not any(i["group"] == "segmentation" for i in parsed_items), str(parsed_items))
inputs = planning.build_inputs(MARKET, FS, parsed_items, {"questions": []}, {}, 40, subsegments=SUBS)
ev_items = {i["id"]: i for i in inputs.items.values() if i["kind"] == "evidence"}
check("build_inputs: a trends item sits only in trends, a key-players item only in key_players, a U.S. one only in country_us",
      ev_items["W1"]["sections"] == {"trends"} and ev_items["W5"]["sections"] == {"key_players"} and ev_items["W4"]["sections"] == {"country_us"},
      str({k: v["sections"] for k, v in ev_items.items()}))
check("build_inputs: a sub-segment item sits only in the sub-segment section and names its category; no evidence item sits in segmentation",
      ev_items["W2"]["sections"] == {"subsegment"} and ev_items["W2"]["subsegment"] == "Cloud"
      and not any("segmentation" in v["sections"] for v in ev_items.values()))
old_style = planning.build_inputs(MARKET, FS, [{"id": "W1", "tags": ["trends"], "group": "trends", "fact": "Acme launched x.", "detail": "", "source": "S, 2026-10"}],
                                  {"questions": []}, {}, 40)
check("build_inputs: the old chain's tags keep their old reach (trends may also sit in the sub-segment section)",
      old_style.items["W1"]["sections"] == {"trends", "subsegment"})
check("build_inputs: the fixed sub-segments travel on the planner's inputs", inputs.subsegments == SUBS)

# ------------------------------------------------------------------------------- the planner takes the fixed pair
HEAD = {"report_context": "Widget Services Market Size and Growth", "trends": "Trends in the Widget Services Market",
        "segmentation": "Widget Services Market Segmentation", "subsegment": "Sub-Segment Analysis of the Widget Services Market",
        "country_us": "U.S. Analysis of the Widget Services Market", "key_players": "Key Players in the Widget Services Market",
        "conclusion": "Conclusion and Outlook for the Widget Services Market"}


def raw_plan(sub_segments) -> dict:
    secs = [{"sop": s, "heading": HEAD[s], "msq": None, "point": f"point {s}", "facts": [], "secondary": None, "longtail": None,
             "terms": [], "snippet": None} for s in planning.ORDER]
    next(s for s in secs if s["sop"] == "subsegment")["sub_segments"] = sub_segments
    return {"title": "x", "title_basis": [], "intro": {"points": []}, "transition": {"point": "p"}, "sections": secs}


def sub_of(plan) -> list[tuple[str, str]]:
    return [(v["split"], v["name"]) for v in next(s for s in plan["sections"] if s["sop"] == "subsegment")["sub_segments"]]


def inputs_for(subs) -> planning.PlanInputs:
    return planning.build_inputs(MARKET, FS, [], {"questions": []}, {}, 40, subsegments=subs)


p1 = planning.validate_plan(raw_plan([{"split": "By End User", "name": "Banks"}, {"split": "By Deployment", "name": "Hybrid"}]), inputs_for(SUBS), LOG)
check("planner: the fixed pair stands whatever the planner picked, and the replacement is reported",
      sub_of(p1) == [("By Type", "Software"), ("By Deployment", "Cloud")] and any("fixed before the news search" in r for r in p1["repairs"]), str(p1["repairs"]))
p2 = planning.validate_plan(raw_plan([{"split": "By Type", "name": "Software"}, {"split": "By Deployment", "name": "Cloud"}]), inputs_for(SUBS), LOG)
check("planner: the planner copying the fixed pair needs no repair for it", not any("fixed before" in r for r in p2["repairs"]), str(p2["repairs"]))
non_leader = [{"split": "By Type", "name": "Hardware"}, {"split": "By End User", "name": "Insurers"}]
p3 = planning.validate_plan(raw_plan([{"split": "By Type", "name": "Software"}]), inputs_for(non_leader), LOG)
check("planner: a fixed pick that is not its split's leader is not replaced by the leader",
      sub_of(p3) == [("By Type", "Hardware"), ("By End User", "Insurers")], str(sub_of(p3)))
p4 = planning.validate_plan(raw_plan([]), inputs_for([{"split": "By Gone", "name": "Nothing"}]), LOG)
check("planner: a fixed pair that no longer matches the sheet falls back to the old choice (a known leader first, no region split)",
      sub_of(p4) == [("By Type", "Software"), ("By End User", "Banks")], str(sub_of(p4)))
p5 = planning.validate_plan(raw_plan([]), inputs_for([]), LOG)
check("planner: with no fixed pair the old defaults still work", sub_of(p5) == sub_of(p4), str(sub_of(p5)))
check("planner: the prompt says the two sub-segments are given and lists them",
      "you do not choose them" in planning.build_prompt(inputs_for(SUBS)) and "- Software (from the split By Type)" in planning.build_prompt(inputs_for(SUBS))
      and "not fixed" in planning.build_prompt(inputs_for([])))

# ------------------------------------------------------------------- minimums as warnings: ONE SELECT, no top-up, no second search
def run_with(answer, hits_list):
    calls = {"llm": 0, "prompts": []}

    def chat(system, user, **kw):
        calls["llm"] += 1
        calls["prompts"].append(user)
        return copy.deepcopy(answer)

    def no_search(*a, **kw):
        raise AssertionError("SELECT must not start another search (no top-up round)")

    with patched((llm, "chat_json", chat), (feeds, "bing_search", no_search), (feeds, "attach_excerpts", no_search),
                 (feeds, "gather", no_search)):
        result = pipeline.run_sectioned_select(
            ARTICLE, ORIENT, SPONSOR, REPORT, hits_list, LOG, subsegments=SUBS, topic_terms=["widget services"])
    return result, calls


first = {"evidence": [ev(1, ["trends"]), ev(2, ["subsegment:Cloud"]), ev(3, ["subsegment:Software"]), ev(9, ["trends"]), ev(10, ["trends"])]}
(selection, all_hits, counts, warnings), calls = run_with(first, pool())
check("minimums: thin sections get ONE SELECT call and no top-up: no search, no second SELECT", calls["llm"] == 1, str(calls["llm"]))
check("minimums: the hits come back as they went in, so every W(n) still maps to hits[n - 1]", len(all_hits) == len(pool()))
check("minimums: the SELECT prompt has no top-up note (the look is never restricted to some sections)", "THIS LIST ONLY NEEDS" not in calls["prompts"][0])
check("minimums: the counts hold no segmentation section", "segmentation" not in counts and counts["trends"] == 3, str(counts))
check("minimums: every section below its minimum is warned about, one that meets it is not",
      "Thin evidence for Trends: 3 item(s)" in warnings and "Thin evidence for U.S.: 0 item(s)" in warnings
      and "Thin evidence for Key players: 0 item(s)" in warnings and not any("Sub-segment" in w for w in warnings), str(warnings))

extra_us = [article(f"United States widget services pilot {i} announced by banks", f"https://news.example/us{i}", 2, ["us"]) for i in range(2)]
extra_kp = [article(f"Player{i} launches widget services platform for banks", f"https://news.example/kp{i}", 2, ["key_players"]) for i in range(3)]
extra_tr = [article(f"Widget services trend story {i} for banks and insurers", f"https://news.example/tr{i}", 2, ["trends"]) for i in range(4)]
big = pool() + extra_us + extra_kp + extra_tr
full = {"evidence": [ev(1, ["trends"]), ev(2, ["subsegment:Cloud"]), ev(3, ["subsegment:Software"]), ev(9, ["trends"]), ev(10, ["trends"])]
        + [ev(i, ["us"], f"{nm(i)} Corp launched widget services pilots in the U.S. in October 2026.") for i in (11, 12)]
        + [ev(i, ["players"], f"{nm(i)} Corp launched a widget services platform in October 2026.") for i in (13, 14, 15)]
        + [ev(i, ["trends"], f"{nm(i)} Corp launched widget services analytics in October 2026.") for i in (16, 17, 18, 19)]}
(selection_full, _, counts_full, warnings_full), calls_full = run_with(full, big)
check("minimums: when every section meets its minimum there is no warning", calls_full["llm"] == 1 and warnings_full == [],
      f"{calls_full['llm']} {warnings_full} {counts_full}")
check("minimums: counts list the sections in the article's order, with the two sub-segments, and the minimums are in one place",
      list(counts_full) == ["trends", "subsegment:Software", "subsegment:Cloud", "us", "key_players"]
      and sections.minimum("subsegment:Cloud") == 1 and sections.minimum("trends") == 6 and sections.minimum("us") == 2
      and sections.minimum("key_players") == 3 and sections.minimum("segmentation") == 0, str(counts_full))
check("minimums: a sub-segment with no item is thin by name",
      sections.thin_sections({"trends": 9, "subsegment:Cloud": 0, "subsegment:Software": 1, "us": 2, "key_players": 3}) == ["subsegment:Cloud"]
      and sections.label("subsegment:Cloud") == "Sub-segment Cloud")
check("no extra rounds: the pipeline has no broader round, no top-up queries and no separate U.S. search left",
      not any(hasattr(pipeline, n) for n in ("section_topup_queries", "_STRONG_HITS_TARGET", "_TOPUP_QUERIES_MAX", "_SECTION_BING_QUERIES"))
      and not hasattr(us_search, "run_us_search") and not hasattr(us_search, "merge_hits"))
# ----------------------------------------------------------- the claim search is not started in the market-first chain
started: list[bool] = []
step1_stubs = [
    (main, "_start_claim_search", lambda orient, logger: started.append(True)),
    (main, "new_run_logger", lambda: (LOG, Path("never_written.log"))),
    (pipeline, "run_orient", lambda article, sponsor, logger: {"suggested_markets": {"tier1": [], "tier2": [], "tier3": []}}),
    (pipeline, "run_match_catalog", lambda orient, logger: []),
    (pipeline, "run_tier_catalog", lambda orient, article, candidates, logger: candidates),
    (pipeline, "own_market_report_note", lambda orient, candidates, logger: ""),
]
req = main.Step1Request(article=main.ArticleIn(title="Blog", paragraphs=[
    main.ParagraphIn(index=0, text="Intro text about widgets."),
    main.ParagraphIn(index=1, text="Visit our client for widgets.", links=[main.LinkIn(anchor="our client", url="https://client.example/widgets")])]),
    sponsor_index=1)
old_v2 = config.CMI_CHAIN_V2
try:
    config.CMI_CHAIN_V2 = True
    with patched(*step1_stubs):
        out = main.step1(req)
    check("claim search: not started by Step 1 in the market-first chain", started == [] and main._RUNS[out["run_token"]].claim_future is None, str(started))
    config.CMI_CHAIN_V2 = False
    with patched(*step1_stubs):
        main.step1(req)
    check("claim search: the old chain still starts it", started == [True])
finally:
    config.CMI_CHAIN_V2 = old_v2

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all section-evidence tests passed")
