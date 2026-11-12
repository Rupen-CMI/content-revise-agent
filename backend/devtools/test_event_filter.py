"""Offline checks for the deterministic non-event rules: directory, review and listicle pages are dropped at search
(search.listing_page_reason, filter_hits), and selected facts that report no dated development are dropped after SELECT
(pipeline._drop_non_event_facts, in both SELECT passes). No network, no real LLM calls.
Run from backend/: python devtools/test_event_filter.py"""
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import search  # noqa: E402
import sources  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

LOG = logging.getLogger("t")
LOG.setLevel(logging.INFO)  # the [filter] and [select] messages this test greps are logged at INFO
FAILED: list[str] = []
LOG_CAPTURE: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


class _Capture(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        LOG_CAPTURE.append(record.getMessage())


LOG.addHandler(_Capture())

TODAY = date(2026, 11, 12)
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def hit(title: str, host: str = "news.example.org", is_news: bool = False, snippet: str = "") -> search.SearchHit:
    return search.SearchHit(title, snippet, f"https://{host}/p", "", NOW, is_news=is_news)


# ------------------------------------------------------------------ layer 1: search hits (host and title shapes)
# Every DROP example from the brief, as the hit it would arrive as. Host-listed directories and title-shaped listicles.
LAYER1_DROP_HOST = [
    ("Top Agile Project Management Software for 2026", "www.capterra.com"),
    ("Agile Project Management Software: Compare and Shortlist", "www.capterra.com"),
    ("SourceForge: Compare User Reviews of Agile Project Management Tools", "sourceforge.net"),
    ("Best Agile Project Management Software for 2026", "www.getapp.com"),
    ("100 Top Skin Care Companies", "www.f6s.com"),
    ("Agile Tools Shortlist", "www.trustradius.com"),
]
LAYER1_DROP_TITLE = [
    ("Top 10 Cell Line Development Services in 2026", "www.example-cdmo.com"),
    ("Best Cell Line Development Companies of 2026", "www.example-cdmo.com"),
    ("How to Choose a CDMO Partner for Cell Line Development", "www.example-cdmo.com"),
    ("Webinar: Verifying Clonality in Stable Cell Lines", "www.example-cdmo.com"),
    ("eBook: The Key Phases of Cell Line Development", "www.example-cdmo.com"),
    ("Ultimate Guide to Agile Project Management", "www.example-pm.com"),
    ("Jira vs. Asana for Agile Teams", "www.example-pm.com"),
    ("Jira Alternatives for Agile Teams", "www.example-pm.com"),
    ("Everything you need to know about cell line development", "www.example-cdmo.com"),
    ("Review: Wrike's Agile Tools", "www.example-pm.com"),
    ("A Comparison of Agile Tools", "www.example-pm.com"),
    ("Whitepaper: Choosing a Cell Line Partner", "www.example-cdmo.com"),
    ("What is cell line development?", "www.example-cdmo.com"),
]
# News headlines get the same title shapes: a dated roundup or "vs." piece is not a development either.
LAYER1_DROP_NEWS = [("Top 10 Agile Tools Reviewed for 2026", "www.prnewswire.com")]

# Real events: none of these may be dropped by the host or title rules.
LAYER1_KEEP = [
    ("Galderma acquires Zo Skin Health for $1.5 billion", "www.reuters.com"),
    ("PAI Partners agrees to acquire Elemis", "www.reuters.com"),
    ("Schneider Electric launches 48-amp home Level 2 EV charger", "www.prnewswire.com"),
    ("Asimov announces cell line development partnership with Kaigene", "www.businesswire.com"),
    ("Circana: prestige beauty sales reach $25.5 billion in Q3 2026, up 5%", "www.cosmeticsdesign.com"),
    ("Concentrix launches Live Voice Translation", "www.prnewswire.com"),
    ("Interpreting spend reaches GBP 41.9m in 2025, Ministry of Justice data show", "www.gov.uk"),
    ("Only 34% of organisations complete projects on time, ETCIO survey finds", "www.etcio.com"),
]

for title, host in LAYER1_DROP_HOST:
    reason = search.listing_page_reason(hit(title, host))
    check(f"layer 1 host: {host} listing is dropped ({title[:40]!r})",
          reason == "a directory or review listing site", str(reason))
for title, host in LAYER1_DROP_TITLE + LAYER1_DROP_NEWS:
    reason = search.listing_page_reason(hit(title, host, is_news=(host == "www.prnewswire.com")))
    check(f"layer 1 title: listicle/guide/webinar title is dropped ({title[:40]!r})",
          reason == "a listicle, ranking, guide or webinar title", str(reason))
for title, host in LAYER1_KEEP:
    reason = search.listing_page_reason(hit(title, host, is_news=True))
    check(f"layer 1 keep: a real event is not a listing ({title[:40]!r})", reason is None, str(reason))

check("layer 1 host: a host and its subdomain match, a lookalike does not",
      sources.is_listing("www.capterra.com") and sources.is_listing("capterra.com")
      and not sources.is_listing("capterra.com.example.org") and not sources.is_listing("reuters.com"))

# end to end through filter_hits: a directory hit never reaches SELECT, and the drop is logged with its reason
LOG_CAPTURE.clear()
kept = search.filter_hits([hit("Top Agile Project Management Software for 2026", "www.capterra.com")],
                          ["Agile Project Management Software Market"], logger=LOG)
check("layer 1 filter_hits: a directory hit is dropped before the topical rules",
      kept == [] and any("DROP (a directory or review listing site)" in m for m in LOG_CAPTURE), str(LOG_CAPTURE[-2:]))

# ------------------------------------------------------------------ layer 2: selected facts (fake selections)
# The DROP examples from the brief, as the fact SELECT would write for them. Each must be dropped.
LAYER2_DROP = [
    "Capterra publishes a comparison directory of top Agile Project Management software for 2026",
    "SourceForge lists and compares user reviews of agile project management tools",
    "GetApp publishes a 2026 ranking of top-rated project management software",
    "f6s.com published a listing of 100 top skin care companies",
    "Forbes compiled a list of Black-owned skin care and beauty brands",
    "Sartorius states its CHO cell line development service has completed more than 330 projects",
    "Creative Biolabs describes a mammalian cell line development service covering stable clones",
    "MilliporeSigma states its cell line development capabilities span expression systems and screening",
    "Beckman Coulter published a resource describing the key phases of bioprocess development",
    "STEMCELL Technologies published resources for verifying clonality",
    "KBI Biopharma published guidance on choosing a CDMO partner",
    "The Digital Project Manager reports that Wrike offers a collection of agile tools",
    "Dermatologist-recommended guidance states hyaluronic acid is a humectant",
    "Board-certified dermatologist Dr. Ava Shamban said honey is antibacterial",
    "Esthetician Caroline Hirons published her fourth book",
    "Xtalks announced a webinar on cell line development",
    "BioTechniques published an eBook on bioprocess development",
]
# The KEEP examples from the brief. Each must survive, and the event-word and figure rules are what keep them.
LAYER2_KEEP = [
    "Galderma acquired Zo Skin Health for $1.5 billion",
    "PAI Partners agreed to acquire Elemis, the British skincare brand",
    "Schneider Electric launched a 48-amp home Level 2 EV charger",
    "Asimov announced a cell line development partnership with Kaigene",
    "Circana data show prestige beauty sales reached $25.5 billion in the third quarter of 2026, up 5 percent",
    "Concentrix launched Live Voice Translation",
    "The UK Ministry of Justice's spending on interpreting reached GBP 41.9m in 2025",
    "Only 34% of organisations say they mostly or always complete projects on time (ETCIO survey)",
]


def layer2(fact: str, page: str = "", ident: str = "W1", today: date = TODAY, group: str = "") -> list[str]:
    """Run _drop_non_event_facts over one selected item; returns the ids that survive. page is the hit's snippet."""
    item = {"id": ident, "fact": fact, "detail": "", "use_for": "context"}
    if group:
        item["group"] = group
    sel = {"evidence": [item]}
    pipeline._drop_non_event_facts(sel, [hit("Market story", snippet=page, is_news=True)], LOG, today)
    return [e["id"] for e in sel["evidence"]]


for fact in LAYER2_DROP:
    check(f"layer 2 drop: no dated development ({fact[:50]!r})", layer2(fact) == [], str(layer2(fact)))
for fact in LAYER2_KEEP:
    check(f"layer 2 keep: a dated development ({fact[:50]!r})", layer2(fact) == ["W1"], str(layer2(fact)))

# the old-event rule: a year two or more years back in the fact or the hit's page drops the item, even when it reads as an event
OLD_DROP = [
    ("BP agreed to buy Britain's largest electric vehicle charging firm, Chargemaster",
     "BP agreed to buy Chargemaster in 2018, giving it a network of charge points."),
    ("Acme Corp launched a widget platform", "Acme Corp launched the platform in September 2018."),
    ("Acme Corp launched a widget platform in 2024", ""),
    ("Acme Corp launched a widget platform", "Acme Corp launched the platform in 2024 and grew sales."),
]
for fact, page in OLD_DROP:
    check(f"layer 2 old event: a 2018/2024-dated event is dropped ({fact[:40]!r})", layer2(fact, page) == [],
          str(layer2(fact, page)))

OLD_KEEP = [
    ("Galderma acquired Zo Skin Health for $1.5 billion", "Zo Skin Health, founded in 2008, was acquired this year."),
    ("Acme Corp launched a widget platform in 2025", ""),
    ("Acme Corp launched a widget platform", "Acme's launch was reported in 2024 and again in 2026."),
]
for fact, page in OLD_KEEP:
    check(f"layer 2 old event: a current or founding year does not drop the item ({fact[:40]!r})",
          layer2(fact, page) == ["W1"], str(layer2(fact, page)))
check("layer 2 old event: 2024 is two years back from 2026, 2025 is one year back and kept",
      layer2("Acme Corp launched a widget platform in 2025") == ["W1"]
      and layer2("Acme Corp launched a widget platform in 2024") == [])

# article evidence (C-ids, group "article") is backed by the article's own claims and is never judged here
check("layer 2 article: a C-numbered article item is not filtered",
      layer2("Sartorius states its service has completed more than 330 projects", ident="C1", group="article") == ["C1"])

# the dropped items are logged with the reason, like the other backstops
LOG_CAPTURE.clear()
layer2("Esthetician Caroline Hirons published her fourth book")
layer2("Acme Corp launched a widget platform in 2018.")
check("layer 2 log: a non-event drop names its reason",
      any("reports no dated development" in m and "dropped W1" in m for m in LOG_CAPTURE), str(LOG_CAPTURE))
check("layer 2 log: an old-event drop names its reason",
      any("dates the event two or more years back" in m for m in LOG_CAPTURE), str(LOG_CAPTURE))

# ------------------------------------------------------------------ run_select: both SELECT passes apply the rule
REPORT = reports.Report(news_id=1, name="Zzz Widget Services Market", subject="", url="u", source="s",
                        sections={}, segment_terms=["Widget Testing"])
ORIENT = {"topic_terms": ["widget maker", "widget testing"], "topic_summary": "t", "bridge_angle": "a"}
ARTICLE = SourceArticle(title="A Blog Title", paragraphs=[])
SPONSOR = Paragraph(index=0, text="Sponsor line.")


def mkhit(i: int) -> search.SearchHit:
    return search.SearchHit(title=f"Widget story {i}", snippet="Acme Corp launched a widget testing service for "
                            "widget supply teams.", url=f"https://h{i}.example.org/{i}", source="",
                            date=datetime(2026, 6, 1, tzinfo=timezone.utc))


def ev(w: int, fact: str) -> dict:
    return {"id": f"W{w}", "fact": fact, "detail": "", "use_for": "trends"}


KEEP_FACT = "Acme Corp launched a widget testing service."
NON_EVENT_FACT = "Sartorius states its widget testing service has completed more than 330 projects."
HITS10 = [mkhit(i) for i in range(1, 11)]
_orig_chat = llm.chat_json


def run_with(responses: list[dict], hits: list[search.SearchHit]) -> tuple[dict, list[str]]:
    """run_select with llm.chat_json stubbed to return the given responses in order; returns (selection, user prompts)."""
    calls: list[str] = []
    llm.chat_json = lambda system, user, **kw: (calls.append(user), responses[len(calls) - 1])[1]
    try:
        sel = pipeline.run_select(ARTICLE, ORIENT, SPONSOR, REPORT, hits, LOG)
    finally:
        llm.chat_json = _orig_chat
    return sel, calls


# first pass keeps one real event and one directory-style fact; the second pass (triggered because only one item
# survived) carries one more non-event fact and one real event. Both non-event facts must be gone.
LOG_CAPTURE.clear()
sel, calls = run_with(
    [{"evidence": [ev(1, KEEP_FACT), ev(2, NON_EVENT_FACT)]},
     {"evidence": [ev(1, "Creative Biolabs describes a widget testing service covering stable clones."),
                   ev(2, "Concentrix launched Live Voice Translation.")]}],
    HITS10)
ids = [e["id"] for e in sel["evidence"]]
check("run_select: a non-event fact is dropped from the first pass and one from the second pass",
      len(calls) == 2 and ids == ["W1", "W3"], f"calls={len(calls)} ids={ids}")
check("run_select: the second pass drop is logged with its reason",
      any("dropped W2: its fact reports no dated development" in m for m in LOG_CAPTURE), str(LOG_CAPTURE[-6:]))
check("run_select: the second prompt carries the never-keep sentence",
      "Never keep directories, review or comparison listings" in calls[1], calls[1][-300:])
check("run_select: the second-pass instruction is added to the second-pass prompt only",
      "Never keep directories" in prompts.SELECT_SECOND_PASS and "Never keep directories" not in calls[0])

# a first pass of six real events, one of them a directory-style fact: no second pass runs, and the rule keeps the other five
LOG_CAPTURE.clear()
sel6, calls6 = run_with(
    [{"evidence": [ev(1, KEEP_FACT), ev(2, NON_EVENT_FACT), ev(3, KEEP_FACT), ev(4, KEEP_FACT),
                   ev(5, KEEP_FACT), ev(6, KEEP_FACT)]}],
    HITS10)
check("run_select: a first pass that keeps five real events after the rule does not run a second pass",
      len(calls6) == 1 and [e["id"] for e in sel6["evidence"]] == ["W1", "W3", "W4", "W5", "W6"],
      f"calls={len(calls6)} ids={[e['id'] for e in sel6['evidence']]}")

print()
print("all event filter tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
