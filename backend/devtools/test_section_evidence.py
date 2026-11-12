"""Offline checks for the Evidence step's section layer (no network, no real LLM calls): the section search plan from a
confirmed fact sheet, the two fixed sub-segments, queries grouped by section, hits that carry their sections, the one cleaning
pass, SELECT assigning exactly one section per item, the planner accepting the fixed sub-segments, the per-section minimums
with their ONE top-up, and the claim search no longer starting in the market-first chain.
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

# ------------------------------------------------------------------------------------------ the sub-segment choice
picks = sections.choose_subsegments(FS)
check("sub-segments: two splits, neither the region split, the stated leader first",
      [(p["split"], p["name"], p["basis"]) for p in picks] == [("By Type", "Software", "dominant"), ("By Deployment", "Cloud", "share")],
      str(picks))

only_first = copy.deepcopy(FS)
only_first["segments"] = [
    {"id": "fs.segments.0", "type": "By Mode", "items": ["Alpha", "Beta"], "figures": {}, "dominant": None},
    {"id": "fs.segments.1", "type": "By Size", "items": ["Gamma", "Delta"], "figures": {}, "dominant": None},
    {"id": "fs.segments.2", "type": "By Region", "items": ["Asia", "Europe"], "figures": {}, "dominant": {"name": "Asia", "basis": "stated"}},
]
fb = sections.choose_subsegments(only_first)
check("sub-segments: with no leader and no share the first category of each split, never the region split with its leader",
      [(p["split"], p["name"], p["basis"]) for p in fb] == [("By Mode", "Alpha", "first"), ("By Size", "Gamma", "first")], str(fb))

one_leader = copy.deepcopy(only_first)
one_leader["segments"][1]["figures"] = {"Delta": share(70.0)}
check("sub-segments: a split with a stated share outranks one with nothing",
      [(p["split"], p["name"]) for p in sections.choose_subsegments(one_leader)] == [("By Size", "Delta"), ("By Mode", "Alpha")])

region_last = {"segments": only_first["segments"][2:] + only_first["segments"][:1]}
check("sub-segments: a region split is used only when no other split exists to fill the pair",
      [p["split"] for p in sections.choose_subsegments(region_last)] == ["By Mode", "By Region"])
check("sub-segments: one split gives one sub-segment, no splits give none",
      len(sections.choose_subsegments({"segments": only_first["segments"][:1]})) == 1 and sections.choose_subsegments({}) == [])
check("sub-segments: the same sheet always gives the same pair", sections.choose_subsegments(FS) == picks)

# ------------------------------------------------------------------------------------------------ the search plan
plan = pipeline.section_search_plan(FS, REPORT, FS["players"], picks)
check("plan: trends are the top three drivers then two opportunities, one target each",
      [t["kind"] for t in plan["trends"]] == ["drivers"] * 3 + ["opportunities"] * 2 and plan["trends"][0]["text"].startswith("Driver 0"),
      str(plan["trends"]))
check("plan: the sub-segment targets are the two fixed ones", [t["name"] for t in plan["subsegment"]] == ["Software", "Cloud"])
check("plan: segmentation carries the reason the split's leader leads, not another category's",
      len(plan["segmentation"]) == 1 and "Banks want audited workflows" in plan["segmentation"][0]["text"], str(plan["segmentation"]))
check("plan: the U.S. seeds are the report's own U.S. statements", [t["text"] for t in plan["us"]] == [s["text"] for s in FS["us"]])
check("plan: key players are the cleaned names ('Monday' becomes its domain)", [t["text"] for t in plan["key_players"]] == ["Acme", "monday.com"],
      str(plan["key_players"]))
check("plan: the market target is the market's name", plan["market"] == [{"text": MARKET}])
check("plan: with no drivers, opportunities fill the trends",
      len(pipeline.section_search_plan({**FS, "drivers": []}, REPORT, [])["trends"]) == 3)
check("plan: the targets are printed per section for the query prompt",
      all(h in sections.format_targets(plan) for h in ("TRENDS", "SUB-SEGMENTS", "SEGMENTATION", "U.S.", "KEY PLAYERS", "MARKET"))
      and "Software (By Type)" in sections.format_targets(plan))

# ----------------------------------------------------------------------------------------------- grouped queries
parsed = sections.parse_grouped_queries({
    "trends": ["a one", "a one", " b  two "], "Sub-Segment": ["s1"], "key players": ["k1"], "U.S.": ["u1"], "market": ["m1"], "bogus": ["x"],
    "segmentation": "not a list"})
check("queries: section names are recognised in any spelling, duplicates and unknown sections dropped",
      parsed == {"trends": ["a one", "b two"], "subsegment": ["s1"], "segmentation": [], "us": ["u1"], "key_players": ["k1"], "market": ["m1"]},
      str(parsed))
check("queries: a plain list (the old shape) is all 'market'", sections.parse_grouped_queries(["q1", "q2"])["market"] == ["q1", "q2"])
check("queries: nothing usable gives empty groups", not any(sections.parse_grouped_queries(None).values()))
flat, of = sections.flatten_queries({"trends": ["t1", "t2"], "subsegment": ["s1"], "segmentation": [], "us": ["T1"], "key_players": ["k1"], "market": ["m1"]})
check("queries: one per section in turn, a query two sections wrote is run once and keeps both sections",
      flat == ["t1", "s1", "k1", "m1", "t2"] and of["t1"] == ["trends", "us"], f"{flat} {of}")

grouped_answer = {"queries": {"trends": ['"widget" automation', '"widget" adoption'], "subsegment": ['"widget" software'],
                              "segmentation": ['"widget" audited'], "us": ['"widget" United States'], "key_players": ["Acme launch"],
                              "market": ['"widget" news']},
                  "source_groups": ["not-a-pack"], "topic_terms": ["Widget Maker", "widget maker", "widget supply"]}
seen_prompts: list[str] = []


def fake_chat(system, user, **kw):
    seen_prompts.append(user)
    return copy.deepcopy(grouped_answer)


with patched((llm, "chat_json", fake_chat)):
    queries, groups, terms = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, ["Software"], plan)
check("queries: one call returns the queries grouped by section", isinstance(queries, dict) and queries["trends"] == ['"widget" automation', '"widget" adoption']
      and queries["us"] == ['"widget" United States'] and set(queries) == set(sections.QUERY_SECTIONS), str(queries))
check("queries: topic terms are kept (deduplicated, lower-cased) and an unknown source group is dropped", terms == ["widget maker", "widget supply"] and groups == [],
      f"{terms} {groups}")
check("queries: the prompt carries each section's targets and the shared query rules",
      "Driver 0" in seen_prompts[0] and "Software (By Type)" in seen_prompts[0] and "ANCHOR" in seen_prompts[0]
      and "Banks want audited workflows" in seen_prompts[0] and "SOURCE GROUPS" in seen_prompts[0], seen_prompts[0][:200])
check("queries: the flat prompt of the old chain is untouched (still the 9 categories)", "9 categories" in prompts.SEARCH_QUERIES_USER
      and "grouped" not in prompts.SEARCH_QUERIES_USER)


def failing_chat(*a, **kw):
    raise llm.LLMError("down")


with patched((llm, "chat_json", failing_chat)):
    fallback, _, _ = pipeline.prepare_search_queries(MARKET, FS["players"], LOG, [], plan)
check("queries: when the model fails the deterministic templates still come back grouped by section",
      isinstance(fallback, dict) and fallback["market"] and fallback["subsegment"] and any("Software" in q for q in fallback["subsegment"]), str(fallback))
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
        self._failed_in_a_row, self.disabled, self._logger = 0, False, LOG

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


def fake_gather(market_name, core, queries, groups, players, logger, topic_terms, after_keywords, **kw):
    gather_calls.append({"queries": queries, **kw})
    return pages()


bing_calls: list[list[str]] = []
old_mode = config.SEARCH_DDGS_MODE
config.SEARCH_DDGS_MODE = "off"
try:
    with patched((feeds, "gather", fake_gather), (feeds, "attach_excerpts", lambda *a, **kw: None),
                 (feeds, "bing_search", lambda q, w, logger=None, sections_for=None: bing_calls.append(list(q)) or [])):
        hits = pipeline.run_search_evidence(
            MARKET, SimpleNamespace(links=[]), [], LOG, key_players=["Acme Corp"], orient={}, search_queries=(copy.deepcopy(queries), [], ["widget"]))
finally:
    config.SEARCH_DDGS_MODE = old_mode
sent = gather_calls[0]
check("one search pass: every section's queries go to the one gather call, with the player query under key_players",
      sent["query_sections"].get('"widget" automation') == ["trends"] and "key_players" in sent["query_sections"].get("acme widget", [])
      and sent["max_keyword_queries"] == pipeline._SECTION_BING_QUERIES, str(sent["query_sections"]))
check("one cleaning pass: duplicates merged across sections, each page once", len(hits) == 3 and len({h.url for h in hits}) == 3,
      str([(h.title, h.sections) for h in hits]))
check("one cleaning pass: the merged page carries every section's tag",
      next(h for h in hits if h.url.endswith("/a")).sections == ["trends", "us", "key_players"])
check("one search pass: a thin pool still gets its one broader round", len(bing_calls) == 1, str(bing_calls))

# the U.S. search's pages join the pool with their own section
general = pages()[:1] + pages()[3:4]
us_hit = article("U.S. banks expand widget services pilots", "https://news.example/us1", secs=["us"])
joined = us_search.merge_hits(general, [article("x", general[0].url, secs=["us"]), us_hit])
check("hits: a page the U.S. search shares with the general pool gains 'us'; a new one keeps it",
      joined[0].sections == ["trends", "us"] and joined[0].scope == "us" and joined[-1] is us_hit and joined[-1].sections == ["us"],
      str([h.sections for h in joined]))
check("hits: counts per section count a two-section page in both",
      sections.hit_counts(sections.merge_duplicate_hits(pages())) == {"trends": 1, "us": 1, "key_players": 1, "subsegment": 1, "market": 1})
many = [article(f"Trend story number {i} on widget services growth", f"https://news.example/t{i}", secs=["trends"]) for i in range(30)]
few_us = [article(f"United States widget services pilot {i} announced", f"https://news.example/u{i}", secs=["us"]) for i in range(3)]
capped = sections.cap_hits(many + few_us, 20, 3)
check("shortlist: every section keeps its own hits before the prolific one fills the rest",
      len(capped) == 20 and sum(1 for h in capped if h.sections == ["us"]) == 3 and capped[:2] == many[:2])

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
check("select: with two entries the first one counts", by_id["W3"]["section"] == "segmentation" and by_id["W3"]["use_for_all"] == ["segmentation"])
check("select: a sub-segment that is not one of the two fixed is dropped, never filed elsewhere", "W7" not in by_id)
check("select: a leftover 'context' goes to the section its hit was found for (the general search is trends)",
      by_id["W8"]["section"] == "trends" and by_id["W10"]["section"] == "trends")
check("select: an undated hit is never a trend (its only section was trends, so it is dropped)", "W6" not in by_id)
check("select: a 'us|trends' string takes its first entry", by_id["W9"]["section"] == "us")

ctx_only = {"evidence": [ev(2, ["context"], "Beta launched cloud widget services across Europe in October 2026.")]}
pipeline._assign_sections(ctx_only, pool(), SUBS, LOG)
check("select: a 'context' item whose hit came from the sub-segment search goes to the sub-segment its text names",
      ctx_only["evidence"][0]["section"] == "subsegment" and ctx_only["evidence"][0]["subsegment"] == "Cloud", str(ctx_only))
restricted = {"evidence": [ev(1, ["trends"]), ev(4, ["us"])]}
pipeline._assign_sections(restricted, pool(), SUBS, LOG, only={"us"})
check("select: a top-up look keeps only the sections it needs", [e["id"] for e in restricted["evidence"]] == ["W4"])

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
      and any(i["tags"] == ["segmentation"] and i["group"] == "segmentation" for i in parsed_items), str(parsed_items))
inputs = planning.build_inputs(MARKET, FS, parsed_items, {"questions": []}, {}, 40, subsegments=SUBS)
ev_items = {i["id"]: i for i in inputs.items.values() if i["kind"] == "evidence"}
check("build_inputs: a trends item sits only in trends, a key-players item only in key_players, a U.S. one only in country_us",
      ev_items["W1"]["sections"] == {"trends"} and ev_items["W5"]["sections"] == {"key_players"} and ev_items["W4"]["sections"] == {"country_us"},
      str({k: v["sections"] for k, v in ev_items.items()}))
check("build_inputs: a sub-segment item sits only in the sub-segment section and names its category; segmentation only in segmentation",
      ev_items["W2"]["sections"] == {"subsegment"} and ev_items["W2"]["subsegment"] == "Cloud" and ev_items["W3"]["sections"] == {"segmentation"})
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

# ------------------------------------------------------------------- minimums, ONE top-up, the thin warning
GROUPED = {"trends": ['"widget" automation'], "subsegment": ['"widget" Software', '"widget" Cloud'], "segmentation": [], "us": ['"widget" United States'],
           "key_players": [], "market": ['"widget" news']}


def run_with(first_answer, second_answer, hits_list):
    calls = {"llm": 0, "bing": 0, "bing_queries": [], "prompts": []}

    def chat(system, user, **kw):
        calls["llm"] += 1
        calls["prompts"].append(user)
        return copy.deepcopy(first_answer if calls["llm"] == 1 else second_answer)

    def bing_stub(queries, web, logger=None, sections_for=None):
        calls["bing"] += 1
        calls["bing_queries"] = list(queries)
        out = []
        for i, q in enumerate(queries):
            h = article(f"Fresh widget services development {i} reported for banks", f"https://news.example/new{i}", 2)
            h.add_sections((sections_for or {}).get(q.lower(), []))
            out.append(h)
        return out

    with patched((llm, "chat_json", chat), (feeds, "bing_search", bing_stub), (feeds, "attach_excerpts", lambda *a, **kw: None)):
        result = pipeline.run_sectioned_select(
            ARTICLE, ORIENT, SPONSOR, REPORT, hits_list, LOG, subsegments=SUBS, grouped_queries=GROUPED, plan=plan,
            key_players=["Acme Corp"], search_groups=[], topic_terms=["widget services"])
    return result, calls


first = {"evidence": [ev(1, ["trends"]), ev(2, ["subsegment:Cloud"]), ev(3, ["subsegment:Software"]), ev(9, ["trends"]), ev(10, ["trends"])]}
second = {"evidence": [ev(1, ["us"], "Fresh Corp launched widget services pilots with U.S. banks in October 2026."),
                       ev(2, ["us"], "Other Corp launched a widget services program for U.S. insurers in October 2026."),
                       ev(3, ["trends"], "Third Corp launched a widget services analytics tool in October 2026."),
                       ev(4, ["segmentation"], "Not needed Corp launched widget services in October 2026.")]}
(selection, all_hits, counts, warnings), calls = run_with(first, second, pool())
n = len(pool())
check("minimums: thin sections trigger exactly ONE top-up search and ONE more SELECT", calls["bing"] == 1 and calls["llm"] == 2, f"{calls['llm']} {calls['bing']}")
check("minimums: the top-up's queries are unquoted variants, none repeated from the first pass",
      all('"' not in q for q in calls["bing_queries"]) and not {q.lower() for q in calls["bing_queries"]} & {'"widget" automation'}
      and len(calls["bing_queries"]) == len({q.lower() for q in calls["bing_queries"]}), str(calls["bing_queries"]))
check("minimums: the top-up SELECT is told which sections it needs", "THIS LIST ONLY NEEDS" in calls["prompts"][1]
      and "THIS LIST ONLY NEEDS" not in calls["prompts"][0])
check("minimums: new hits are appended after the first search's and their ids point at them",
      len(all_hits) > n and {e["id"] for e in selection["evidence"] if e["section"] == "us"} == {f"W{n + 1}", f"W{n + 2}"},
      str([(e["id"], e["section"]) for e in selection["evidence"]]))
check("minimums: the top-up keeps only the sections that were thin (segmentation was not)",
      counts["segmentation"] == 0 and counts["us"] == 2 and counts["trends"] == 4, str(counts))
check("minimums: a section still below its minimum is warned about, one that now meets it is not",
      "Thin evidence for Trends: 4 item(s)" in warnings and "Thin evidence for Key players: 0 item(s)" in warnings
      and not any("U.S." in w or "Sub-segment" in w for w in warnings), str(warnings))

extra_us = [article(f"United States widget services pilot {i} announced by banks", f"https://news.example/us{i}", 2, ["us"]) for i in range(2)]
extra_kp = [article(f"Player{i} launches widget services platform for banks", f"https://news.example/kp{i}", 2, ["key_players"]) for i in range(3)]
extra_tr = [article(f"Widget services trend story {i} for banks and insurers", f"https://news.example/tr{i}", 2, ["trends"]) for i in range(4)]
big = pool() + extra_us + extra_kp + extra_tr
full = {"evidence": [ev(1, ["trends"]), ev(2, ["subsegment:Cloud"]), ev(3, ["subsegment:Software"]), ev(9, ["trends"]), ev(10, ["trends"])]
        + [ev(i, ["us"], f"{nm(i)} Corp launched widget services pilots in the U.S. in October 2026.") for i in (11, 12)]
        + [ev(i, ["players"], f"{nm(i)} Corp launched a widget services platform in October 2026.") for i in (13, 14, 15)]
        + [ev(i, ["trends"], f"{nm(i)} Corp launched widget services analytics in October 2026.") for i in (16, 17, 18, 19)]}
(selection_full, _, counts_full, warnings_full), calls_full = run_with(full, {"evidence": []}, big)
check("minimums: when every section meets its minimum there is no top-up and no warning",
      calls_full["bing"] == 0 and calls_full["llm"] == 1 and warnings_full == [], f"{calls_full['bing']} {calls_full['llm']} {warnings_full} {counts_full}")
check("minimums: counts list the sections in the article's order, with the two sub-segments, and the minimums are in one place",
      list(counts_full) == ["trends", "subsegment:Software", "subsegment:Cloud", "segmentation", "us", "key_players"]
      and sections.minimum("subsegment:Cloud") == 1 and sections.minimum("trends") == 6 and sections.minimum("us") == 2
      and sections.minimum("key_players") == 3 and sections.minimum("segmentation") == 0, str(counts_full))
check("minimums: a sub-segment with no item is thin by name",
      sections.thin_sections({"trends": 9, "subsegment:Cloud": 0, "subsegment:Software": 1, "us": 2, "key_players": 3}) == ["subsegment:Cloud"]
      and sections.label("subsegment:Cloud") == "Sub-segment Cloud")

with patched((llm, "chat_json", select_chat), (feeds, "bing_search", lambda *a, **kw: [])):
    unchanged = pipeline.run_sectioned_select(ARTICLE, ORIENT, SPONSOR, REPORT, pool(), LOG, subsegments=SUBS, grouped_queries=GROUPED,
                                              plan=plan, key_players=["Acme Corp"])
check("minimums: a top-up that finds nothing new leaves the hits as they were and still warns",
      len(unchanged[1]) == len(pool()) and any("Thin evidence for" in w for w in unchanged[3]))


def broken_bing(*a, **kw):
    raise llm.LLMError("boom")


with patched((llm, "chat_json", select_chat), (feeds, "bing_search", broken_bing)):
    survived = pipeline.run_sectioned_select(ARTICLE, ORIENT, SPONSOR, REPORT, pool(), LOG, subsegments=SUBS, grouped_queries=GROUPED,
                                             plan=plan, key_players=[])
check("minimums: a failing top-up never fails the step; the first SELECT's evidence stands, flagged thin",
      survived[0]["evidence"] and any("Thin evidence for" in w for w in survived[3]))

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
