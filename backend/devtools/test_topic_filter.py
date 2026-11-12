"""Offline checks for the topical gate: the market's topic vocabulary decides what a trade-press or trusted-host
item must show to reach SELECT. Run from backend/: python devtools/test_topic_filter.py"""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import search  # noqa: E402

LOG = logging.getLogger("t")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


NOW = datetime.now(timezone.utc) - timedelta(days=3)


def hit(title: str, url: str, snippet: str = "") -> search.SearchHit:
    return search.SearchHit(title, snippet, url, "", NOW, is_news=True)


MARKET = ["Zzz Widget Services Market"]
SECTOR = frozenset({"sectorpress.example.com"})
TERMS = ["zzz widget", "widget maker", "widget testing"]


def kept(hits, **kw):
    return [h.title for h in search.filter_hits(hits, MARKET, logger=LOG, **kw)]


# stem_hit: a short stem matches whole words only, a long stem also matches compounds
check("stem_hit: short stem is a whole word", search.stem_hit("web", "web") and not search.stem_hit("website", "web"))
check("stem_hit: long stem matches a compound", search.stem_hit("wealthtech", "wealth"))

# sector-source item with no topical signal
off = hit("Critical flaws patched in a popular server product this week", "https://sectorpress.example.com/a1")
on = hit("A widget maker opens a second testing lab for its customers", "https://sectorpress.example.com/a2")
check("sector source: an off-topic item is dropped once topic terms exist",
      kept([off], sector_hosts=SECTOR, topic_terms=TERMS) == [], str(kept([off], sector_hosts=SECTOR, topic_terms=TERMS)))
check("sector source: an on-topic item is kept", len(kept([on], sector_hosts=SECTOR, topic_terms=TERMS)) == 1)
check("sector source: with no topic vocabulary the host-only rule still applies",
      len(kept([off], sector_hosts=SECTOR)) == 1)

# a topic term rescues an item whose headline lacks the market name, from any host
rescue = hit("Quarterly widget testing volumes rise for a regional lab operator", "https://random-news.example.org/b1")
check("topic terms: an on-vocabulary news item from an ordinary host is kept",
      len(kept([rescue], topic_terms=TERMS)) == 1)
check("topic terms: the same item is dropped without the vocabulary", kept([rescue]) == [])

# boilerplate "website" no longer lets a trusted-host item through on a 3-letter stem
boiler = hit("Acme Holdings names a new leadership team for the coming fiscal year", "https://www.prnewswire.com/c1",
             "For more information visit our website at acme.example.com")
mk = ["Web Scraping Services Market"]
check("trusted source: 'website' boilerplate does not satisfy the stem 'web'",
      search.filter_hits([boiler], mk, logger=LOG) == [], "kept")

# with a vocabulary, a generic market word alone no longer passes a trusted host
shells = hit("Hackers use web shells to backdoor servers through a critical flaw", "https://www.thehackernews.com/d1")
mk_terms = ["web scraping", "crawler", "data extraction"]
check("trusted source: a generic market word alone does not pass once a vocabulary exists",
      search.filter_hits([shells], mk, logger=LOG, topic_terms=mk_terms) == [])
scr = hit("A new crawler product speeds up data extraction for AI teams on the web", "https://www.thehackernews.com/d2")
check("trusted source: the same host passes when a topic term is used",
      len(search.filter_hits([scr], mk, logger=LOG, topic_terms=mk_terms)) == 1)

# market sizing is the CMI report's alone
import logging as _lg
import feeds  # noqa: E402
import pipeline  # noqa: E402
sel = {"evidence": [
    {"id": "W1", "fact": "Firecrawl Inc., a provider of web scraping tools for AI agents, raised $75 million in funding.", "detail": ""},
    {"id": "W2", "fact": "Mordor Intelligence projects that the web scraping industry will double from $1.03 billion in 2025 to $2 billion.", "detail": ""},
    {"id": "W3", "fact": "The global web scraping market was valued at approximately $1.34 billion in 2025 and is projected to grow.", "detail": ""},
    {"id": "W4", "fact": "According to MarketsandMarkets, the global AI agents market is projected to grow from USD 7.84 billion.", "detail": ""},
    {"id": "W5", "fact": "Oxylabs offers more than 175 million residential proxies across 195 countries.", "detail": "The market for proxies is crowded, but Oxylabs leads on pool size."},
    {"id": "W6", "fact": "Python accounts for 69.6% of the web scraping developer stack, with proxies at 39.1%.", "detail": ""},
    {"id": "W7", "fact": "Cloudflare will block mixed-use crawlers by default from September 15, 2026.", "detail": "The segment is growing at a 12% CAGR, analysts say."},
]}
pipeline._drop_market_sizing_facts(sel, _lg.getLogger("t"))
ids = [e["id"] for e in sel["evidence"]]
check("market sizing: sizing/forecast facts and rival-firm facts are dropped, event facts kept", ids == ["W1", "W5", "W6", "W7"], str(ids))
details = {e["id"]: e["detail"] for e in sel["evidence"]}
check("market sizing: a kept item's detail that states a CAGR is blanked, an ordinary detail stays",
      details["W7"] == "" and details["W5"].startswith("The market for proxies"), str(details))

# two paragraphs fused into one array entry are restored; bullet lines stay with their lead-in
fused = {"sections": [
    {"heading": "Sub", "sop": ["subsegment"], "paragraphs": ["First analysis sentence one. Two.\nSecond analysis sentence one. Two."]},
    {"heading": "Seg", "sop": ["segmentation"], "paragraphs": ["Lead-in sentence.\n- By type: a, b.\n- By use: c, d."]},
    {"heading": "One", "sop": ["country_us"], "paragraphs": ["Just one paragraph."]},
]}
cleaned = pipeline._clean_sections(fused["sections"])
check("fused paragraphs: a newline between two analyses becomes two paragraphs", len(cleaned[0]["paragraphs"]) == 2, str(cleaned[0]))
check("fused paragraphs: bullet lines stay attached to their lead-in",
      len(cleaned[1]["paragraphs"]) == 1 and cleaned[1]["paragraphs"][0].count("\n- ") == 2, str(cleaned[1]))
check("fused paragraphs: a normal paragraph is untouched", cleaned[2]["paragraphs"] == ["Just one paragraph."])

objs = pipeline._clean_sections([{"heading": "Sub", "sop": ["subsegment"], "paragraphs": [
    {"segment": "By type", "sub_segment": "Cloud", "paragraph": "Cloud analysis. Two. Three."},
    {"segment": "By application", "sub_segment": "Aggregation", "paragraph": "Aggregation analysis. Two. Three."}]}])
check("sub-segment objects: each analysis becomes its own paragraph string",
      objs[0]["paragraphs"] == ["Cloud analysis. Two. Three.", "Aggregation analysis. Two. Three."], str(objs))

# the live report page's taxonomy block supplies the segment names when the database column has none
import reports  # noqa: E402
page = """Global Web Scraping Services Market: Taxonomy
On the basis of type, the global web scraping services market is segmented into:
- Browser Extension
- Installable Software
- Cloud Based
On the basis of application, the global web scraping services market is segmented into:
- Data Aggregation
- Market Research
- Customer Insight
- Other
On the basis of region, the global web scraping services market is segmented into:
- North America
- Europe
Get access to 250+ pages report
"""
terms = reports.taxonomy_segment_terms(page)
check("taxonomy: type and application sub-segments are read, region and Other are not",
      terms == ["Browser Extension", "Installable Software", "Cloud Based", "Data Aggregation", "Market Research", "Customer Insight"], str(terms))
check("taxonomy: merging keeps database terms first and drops case-insensitive duplicates",
      reports._merge_terms(["Cloud based", "Hybrid"], terms)[:3] == ["Cloud based", "Hybrid", "Browser Extension"])

import checks  # noqa: E402
ops = [
    "A CMI analyst expects hosted delivery to matter more than raw crawler speed for most teams.",
    "An analyst at CMI believes consolidation will decide who survives.",
    "CMI's research team suggests buyers will favour managed services.",
    "An expert at Coherent Market Insights argues the bigger risk is compliance.",
    "CMI values the market at USD 1.56 billion in 2026. CMI tracks the market across regions.",
    "Analysts at Zzz Corp expect growth.",
]
counts = [checks.count_cmi_opinions([o]) for o in ops]
check("opinion count: each attribution form counts once, plain CMI statements and other firms' analysts do not",
      counts == [1, 1, 1, 1, 0, 0], str(counts))
check("opinion count: the check allows 1 or 2 across the article",
      checks.count_cmi_opinions(ops[:1]) == 1 and checks.count_cmi_opinions(ops[:3]) == 3)

# split_bullet_lines (moved from docx_io into checks.py so checks.long_paragraph_problems and
# checks.count_items_in can share it; docx_io now imports it back)
lead, items = checks.split_bullet_lines("Three factors matter here.\n- First factor explained.\n- Second factor explained.")
check("split_bullet_lines: lead-in and bullet items split apart",
      (lead, items) == ("Three factors matter here.", ["First factor explained.", "Second factor explained."]), str((lead, items)))
check("split_bullet_lines: plain text with no bullet lines returns it whole, no items",
      checks.split_bullet_lines("Just a plain paragraph.") == ("Just a plain paragraph.", []))

# long_paragraph_problems: a single flowing paragraph over the cap is flagged; a bulleted paragraph whose lead-in
# and each item are individually short is NOT flagged even though its concatenated text is long; a bullet ITEM
# that is itself over the cap still is.
long_plain = " ".join(f"w{i}" for i in range(120))
short_bulleted = "A short lead-in.\n" + "\n".join(f"- Bullet {i} stays under the word cap easily." for i in range(4))
long_bullet_item = "A short lead-in.\n- " + " ".join(f"w{i}" for i in range(110))
long_problems = checks.long_paragraph_problems([{"heading": "Trends", "sop": ["trends"], "paragraphs": [long_plain]}])
bulleted_problems = checks.long_paragraph_problems([{"heading": "Country us", "sop": ["country_us"], "paragraphs": [short_bulleted]}])
item_problems = checks.long_paragraph_problems([{"heading": "Players", "sop": ["key_players"], "paragraphs": [long_bullet_item]}])
check("long_paragraph_problems: a 120-word flowing paragraph is flagged", len(long_problems) == 1, str(long_problems))
check("long_paragraph_problems: a bulleted paragraph with short pieces is not flagged even though it's long overall",
      bulleted_problems == [], str(bulleted_problems))
check("long_paragraph_problems: one over-long bullet item inside an otherwise fine bulleted paragraph is still flagged",
      len(item_problems) == 1, str(item_problems))

import logging as _lg2
dup = [{"heading": h, "sop": [t], "paragraphs": ["x"]} for h, t in
       [("A", "report_context"), ("B", "trends"), ("C", "conclusion"), ("A2", "report_context"), ("B2", "trends"), ("Extra", "conclusion")]]
kept = pipeline._drop_repeated_tags(dup, _lg2.getLogger("t"))
check("repeated tags: the first section of each tag is kept, in order", [k["heading"] for k in kept] == ["A", "B", "C"], str(kept))

# the evidence builder is the last freshness gate: an item whose source is older than the window never reaches the writer
old_hit = search.SearchHit("Calastone to join a larger group", "Jul 21, 2025 snippet", "https://example.org/a", "", datetime(2025, 7, 21, tzinfo=timezone.utc), is_news=False)
new_hit = search.SearchHit("A vendor launches a product", "snippet", "https://example.org/b", "", datetime.now(timezone.utc) - timedelta(days=10), is_news=True)
sel_f = {"evidence": [
    {"id": "W1", "fact": "Calastone agreed to join a larger group.", "detail": "", "use_for_all": ["trends"], "use_for": "trends"},
    {"id": "W2", "fact": "A vendor launched a product.", "detail": "", "use_for_all": ["trends"], "use_for": "trends"},
]}
built = pipeline.run_build_evidence(sel_f, [old_hit, new_hit], _lg.getLogger("t"))
text = built[0] if isinstance(built, tuple) else built
check("evidence builder: a source older than the freshness window is dropped, a recent one kept",
      "Calastone" not in str(text) and "A vendor launched a product" in str(text), str(text)[:300])

# --------------------------------------------------------------------------- round 3 merge fixes
# The real failure this round fixes: market "Agile Project Management Software Market", key players
# Microsoft, Atlassian JIRA, Monday, Asana — 47 hits kept, 46 noise ("Microsoft" carried every Patch
# Tuesday story, "Monday" matched the weekday, a projector review matched "project" by prefix).
AGILE = ["Agile Project Management Software Market"]
AGILE_PLAYERS = ["Microsoft", "Atlassian JIRA", "Monday", "Asana"]
AGILE_TERMS = ["sprint planning", "backlog", "kanban", "retrospective"]


def kept_hits(hits, **kw):
    return search.filter_hits(hits, AGILE, logger=LOG, **kw)


# a key-player name alone never keeps a hit: Patch Tuesday on a trusted host is dropped
patch = hit("Microsoft rolls out November Patch Tuesday fixes for Windows enterprise users",
            "https://www.prnewswire.com/ag1")
check("key player: a Patch Tuesday story naming Microsoft on a trusted host is dropped",
      kept_hits([patch], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS) == [],
      str(kept_hits([patch], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)))

# a player named after a weekday does not match the weekday in an unrelated headline
monday = hit("Boston University misses revenue projections by $42M as Monday budget talks stall",
             "https://www.prnewswire.com/ag2")
check("key player: the weekday 'Monday' in an unrelated headline is not the company Monday",
      kept_hits([monday], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS) == [],
      str(kept_hits([monday], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)))

# a trusted host never keeps an item that shares no market token and no topic term ("projector" is not "project")
proj = hit("Xgimi projector review: a bright picture for a small home cinema",
           "https://www.prnewswire.com/ag3")
check("trusted source: 'projector' no longer matches the market word 'project' by prefix",
      kept_hits([proj], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS) == [],
      str(kept_hits([proj], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)))

# the good cases: a key player plus a topic term, and an unknown vendor plus the market's own words
atl = hit("Atlassian launches Jira AI sprint planning for agile teams", "https://www.reuters.com/ag4")
vaiz = hit("Vaiz introduces agile project management tools for small teams", "https://random-news.example.org/ag5")
kept2 = kept_hits([atl, vaiz], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)
check("key player: an on-topic key-player item is kept", any(h.title == atl.title for h in kept2), str([h.title for h in kept2]))
check("tokens: an unknown vendor using the market's own words is kept", any(h.title == vaiz.title for h in kept2), str([h.title for h in kept2]))

# keep_rules are exposed, and the strong count separates market-naming keeps from host/player keeps
asana = hit("Asana expands its agile project management suite for enterprise buyers", "https://random-news.example.org/ag6")
kept3 = kept_hits([atl, vaiz, asana], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)
rules = {h.title.split()[0]: h.keep_rules for h in kept3}
check("keep rules: the kept hits record the rules that kept them",
      set(rules) == {"Atlassian", "Vaiz", "Asana"} and rules.get("Asana") == ["key_player"], str(rules))
check("strong hits: a key-player-only keep is weak, a token or topic-term keep is strong",
      search.strong_hit_count(kept3) == 2, str(rules))

# the live recall regression this round fixes: with the market "Agile Project Management Software Market"
# the search found only 1 strong hit and the key-player stories below were lost (2 of 3 dropped, and the
# monday.com query never ran under the old 4-query cap).
AGILE_PLAYERS = ["Asana", "Atlassian JIRA", "Microsoft", "Monday", "Pivotal Software"]
AGILE_TERMS = ["agile project management", "agile planning", "scrum board", "sprint planning", "kanban board",
               "backlog management", "work management software", "project tracking", "team collaboration"]
player_stories = [
    hit("Asana completes acquisition of StackAI, a no-code AI workflow platform", "https://www.reuters.com/agp1"),
    hit("monday.com brings voice and AI agents into its work management platform", "https://www.reuters.com/agp2"),
]
dash_story = hit("Asana launches Dash, a personal AI chief of staff", "https://www.reuters.com/agp3")
player_stories.append(dash_story)
kept_stories = kept_hits(player_stories, key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)
check("key player: a player story naming a product word (platform, AI, workflow) is kept for a software market",
      sorted(h.title for h in kept_stories) == sorted(h.title for h in player_stories), str([h.title for h in kept_stories]))
check("key player: the product-word rule keeps the Patch Tuesday and the weekday drops in force",
      kept_hits([patch, monday], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS) == [],
      str(kept_hits([patch, monday], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS)))
non_software = search.filter_hits(
    [hit("Acme launches Dash, a personal AI chief of staff", "https://www.reuters.com/agp4")],
    ["Widget Consulting Services Market"], logger=LOG, key_players=["Acme"], topic_terms=["widget consulting", "advisory services"])
check("key player: a product word is a market signal for a software market only (a services market keeps nothing)",
      kept_hits([dash_story], key_players=AGILE_PLAYERS, topic_terms=AGILE_TERMS) != [] and non_software == [], str(non_software))

# the player-name cleaner: one helper feeds both the filter and the query builder
check("player cleaner: a multi-word name matches the vendor and the product separately",
      set(search.player_name_variants("Atlassian JIRA")) >= {"Atlassian JIRA", "Atlassian", "JIRA"},
      str(search.player_name_variants("Atlassian JIRA")))
check("player cleaner: 'Pivotal Software' does not gain a bare 'Software' matcher",
      "Software" not in search.player_name_variants("Pivotal Software"), str(search.player_name_variants("Pivotal Software")))
mv = search.player_name_variants("Monday")
check("player cleaner: a weekday-named player gains its unambiguous domain form",
      "Monday" in mv and "monday.com" in mv, str(mv))
check("player cleaner: the query form of a weekday-named player is its domain",
      search.clean_player_names(["Monday", "Atlassian JIRA", "Microsoft", "Monday"]) == ["monday.com", "Atlassian JIRA", "Microsoft"],
      str(search.clean_player_names(["Monday", "Atlassian JIRA", "Microsoft", "Monday"])))

# a common-word player name is matched only with a company marker after it
pats = search._player_patterns(["Monday"], {"project", "management", "software"})
weekday_text = "boston university misses revenue projections as Monday budget talks stall"
domain_text = "monday.com launches a new work management suite"
possessive_text = "Monday's agile project management plan for enterprise buyers"
check("common-word player: the bare weekday never matches, the domain and the marked form do",
      not any(rx.search(weekday_text) for _, rx in pats)
      and any(rx.search(domain_text) for _, rx in pats)
      and any(rx.search(possessive_text) for _, rx in pats))

# the in-code key-player news queries: cleaned names, market anchor, at most six (the key-players Bing allotment) —
# with the cap raised, the Asana, Atlassian/Jira and monday.com news queries all fit one run
pq = pipeline._player_news_queries("Agile Project Management Software Market", AGILE_PLAYERS + ["Pivotal Software"])
check("player queries: one per cleaned player, capped at six, anchored to the market's core, with the Asana, Atlassian and monday.com news queries in",
      len(pq) == 5 and pq[0] == "Asana agile project management"
      and any(q.startswith("Atlassian JIRA ") for q in pq) and any(q.startswith("monday.com ") for q in pq)
      and any(q.startswith("Pivotal Software ") for q in pq) and "Microsoft agile project management" in pq, str(pq))
capped = pipeline._player_news_queries("Agile Project Management Software Market",
                                       ["Acme Corp", "Beta Inc", "Gamma Ltd", "Delta Co", "Epsilon LLC", "Zeta", "Eta BV"])
check("player queries: seven players are cut to the six-query allotment", len(capped) == 6, str(capped))

# ---- a quoted Bing keyword query that returns 0 results is retried once unquoted
feeds._BING_QUERY_DELAY_S = feeds._BING_JITTER_S = 0
EMPTY_RSS = b"<rss><channel/></rss>"
ITEM_RSS = (b"<rss><channel><item><title>Atlassian launches a Jira AI assistant for sprint planning</title>"
            b"<link>https://example.org/ag7</link><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>"
            b"<summary>Agile project management news for buyers</summary></item></channel></rss>")


class _FakeResp:
    def __init__(self, content: bytes):
        self.status_code = 200
        self.content = content


class _FakeBingClient:
    def __init__(self, pages: dict[str, bytes]):
        self.pages, self.queries = pages, []

    def get(self, url, params=None, **kw):
        q = (params or {}).get("q", "")
        self.queries.append(q)
        return _FakeResp(self.pages.get(q, EMPTY_RSS))


client = _FakeBingClient({'"agile project management" launch': EMPTY_RSS,
                          "agile project management launch": ITEM_RSS})
bing_hits = feeds._Bing(client, LOG).run(['"agile project management" launch'])
check("bing retry: a quoted keyword query with 0 results is retried once unquoted",
      client.queries == ['"agile project management" launch', "agile project management launch"] and len(bing_hits) == 1,
      str(client.queries))
client2 = _FakeBingClient({'"agile project management" launch': ITEM_RSS})
feeds._Bing(client2, LOG).run(['"agile project management" launch'])
check("bing retry: a quoted query that answers is not retried", client2.queries == ['"agile project management" launch'], str(client2.queries))

# the retry rule: only while the query's section has no hit at all yet, and at most sections.UNQUOTED_RETRIES times a run
import sections  # noqa: E402

q_trend, q_trend2, q_us = '"agile project management" adoption', '"agile project management" funding', '"agile project management" United States'
client3 = _FakeBingClient({q_trend: ITEM_RSS})
feeds._Bing(client3, LOG).run([q_trend, q_trend2], {q_trend.lower(): ["trends"], q_trend2.lower(): ["trends"]})
check("bing retry: a section that already has a hit does not retry its next empty quoted query",
      client3.queries == [q_trend, q_trend2], str(client3.queries))
client4 = _FakeBingClient({q_trend: ITEM_RSS})
feeds._Bing(client4, LOG).run([q_trend, q_us], {q_trend.lower(): ["trends"], q_us.lower(): ["us"]})
check("bing retry: another section with no hit yet still gets its retry",
      client4.queries == [q_trend, q_us, "agile project management United States"], str(client4.queries))
many_empty = [f'"agile project management" word{i}' for i in range(6)]
client5 = _FakeBingClient({})
feeds._Bing(client5, LOG).run(many_empty, {q.lower(): [f"s{i}"] for i, q in enumerate(many_empty)})
check("bing retry: at most three retries a run, whatever the number of empty quoted queries",
      len(client5.queries) == 6 + sections.UNQUOTED_RETRIES == 9 and sum(1 for q in client5.queries if '"' not in q) == 3, str(client5.queries))
bing5 = feeds._Bing(_FakeBingClient({}), LOG)
bing5.run(many_empty[:2], {q.lower(): ["x"] for q in many_empty[:2]})
check("bing retry: the queries sent are counted for the timing line (retries included)", bing5.queries == 4, str(bing5.queries))

# the DDGS lanes follow the same rule: a quoted query is retried only while the lane has found nothing, at most three times
lane_calls: list[str] = []


class _NoDDGS:
    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_attempt(fn, d, query, kind, logger):
    lane_calls.append(query)
    return [search.SearchHit(title="Agile project management news for buyers", snippet="s", url="https://x.example/1", source="x", date=None)] \
        if query == "agile word2" else []


_orig_ddgs, _orig_attempt, _orig_gate = search.DDGS, search._attempt, search._ddgs_gate
search.DDGS, search._attempt = _NoDDGS, fake_attempt
search._ddgs_gate = lambda kind: __import__("contextlib").nullcontext()
try:
    search._lane("news", None, ['"agile" word0', '"agile" word1', '"agile" word2', '"agile" word3', '"agile" word4'], LOG)
finally:
    search.DDGS, search._attempt, search._ddgs_gate = _orig_ddgs, _orig_attempt, _orig_gate
check("ddgs lane: retries stop once the lane has a hit",
      lane_calls == ['"agile" word0', "agile word0", '"agile" word1', "agile word1", '"agile" word2', "agile word2", '"agile" word3', '"agile" word4'],
      str(lane_calls))

print()
print("all topic filter tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
