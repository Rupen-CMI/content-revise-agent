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

print()
print("all topic filter tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
