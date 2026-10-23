"""Offline test for the publisher-points stage (no network, no real LLM calls).

Run from backend/: python devtools/test_publisher.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import llm  # noqa: E402
import prompts  # noqa: E402
import publisher  # noqa: E402
from docx_io import Link, Paragraph, SourceArticle  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_publisher")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


BODY = (
    "A typical online store sells about 40 products in its first month. "
    "Turning a messy online page into plain readable text helps real shoppers find what they are after. "
    "The global widget market is estimated at USD 1.56 billion in 2026."
)
BRANDS = ["acmecorp", "acme corp"]


def run(raw):
    return publisher.normalize_points(raw, BODY, BRANDS, LOG)


good = {"intro_angle": "The article explains how reading messy web pages the way a person does helps stores and their shoppers connect.",
        "points": ["A typical online store sells about 40 products in its first month, so early wins matter a lot.",
                   "Turning a messy online page into plain readable text helps real shoppers find what they are after."]}
out = run(good)
check("good points are kept with ids P1..Pn", [p["id"] for p in out["points"]] == ["P1", "P2"], str(out))
check("a good intro angle is kept", bool(out["intro_angle"]))

check("a point that states a market fact is dropped", run({"points": ["The global widget market is growing quickly across regions."]})["points"] == [])
check("a point carrying a market-size figure is dropped",
      run({"points": ["The widget market is valued at USD 1.56 billion in 2026 and still rising fast."]})["points"] == [])
check("a point attributed to CMI or a research firm is dropped",
      run({"points": ["According to Coherent Market Insights the tools are used by many teams today."]})["points"] == [])
check("a point with a number the article does not contain is dropped",
      run({"points": ["A slow browser can take 90 seconds to load one large page without any tricks."]})["points"] == [])
check("a point naming the guest's brand is dropped", run({"points": ["Acme Corp builds the tools that read pages for AI systems every day."]})["points"] == [])
check("a one-word or overlong point is dropped", run({"points": ["Fast.", "word " * 50]})["points"] == [])
check("duplicates collapse", len(run({"points": [good["points"][0], good["points"][0].upper()]})["points"]) == 1)
check("at most six points", len(run({"points": [f"Plain statement number {w} about how page reading works in practice." for w in "abcdefghij"]})["points"]) == 6)
check("an intro angle that states a market fact is dropped",
      run({"intro_angle": "The widget market is growing fast, so these tools matter more each year.", "points": []})["intro_angle"] == "")
check("non-dict and odd inputs are survived", run(None) == {"intro_angle": "", "points": []} and run({"points": "x"})["points"] == [])

# ------------------------------------------------------------------ technical words are banned
check("a point containing kilobytes is dropped",
      run({"points": ["Some stores have pages that shrink to just a few kilobytes of plain text at times."]})["points"] == [])
check("a point containing scripts is dropped",
      run({"points": ["A browser runs scripts for every page that a real person opens today."]})["points"] == [])
check("a point containing instances is dropped",
      run({"points": ["Each instance of the tool reads a different kind of page in its own way."]})["points"] == [])
check("a point containing parser is dropped",
      run({"points": ["The parser turns every messy page into plain text that people can read later."]})["points"] == [])
check("a plain point with no technical word is kept",
      len(run({"points": ["Every shopper wants a page that loads quickly and says what the store is all about."]})["points"]) == 1)
check("an intro angle containing megabytes is dropped",
      run({"intro_angle": "Reading pages can cost whole megabytes of data, so plain text helps everyone.", "points": []})["intro_angle"] == "")
why = publisher.point_problem("Latency is the reason some pages feel slow for people on older connections today.", set(range(100)), BRANDS)
check("point_problem reports the technical term for a point with latency",
      isinstance(why, str) and why.startswith("uses a technical term"), str(why))

# a number glued to a unit abbreviation is also technical vocabulary (the word-boundary rule misses these)
for label, text in [("300KB", "A page can be 300KB of markup before cleanup for general readers."),
                    ("5MB", "The file is 5MB in size for most readers today."),
                    ("200ms", "It loads in 200ms for most visitors on any device.")]:
    why = publisher.point_problem(text, {"300", "5", "200"}, BRANDS)
    check(f"point_problem drops a unit glued to a number ({label})",
          isinstance(why, str) and why.startswith("uses a technical term"), str(why))
check("a point with a legitimate article number is kept",
      publisher.point_problem("A typical online store sells about 40 products in its first month.", {"40"}, BRANDS) is None)

# ------------------------------------------------------------------ the call (LLM stubbed)
article = SourceArticle(title="Tools", paragraphs=[Paragraph(index=0, text=BODY, links=[]),
                                                   Paragraph(index=1, text="Teams can ask Acme Corp.", links=[Link(anchor="Acme Corp", url="https://acmecorp.com/x")])])
sponsor = article.paragraphs[1]
seen = {}


def fake_chat(system, user, **kw):
    seen["user"] = user
    return good


orig = llm.chat_json
llm.chat_json = fake_chat
try:
    res = publisher.run_publisher_points(article, sponsor, "Widget Market", LOG)
    check("call: returns validated points and shows the model the article and the sponsor paragraph",
          len(res["points"]) == 2 and "40 products" in seen["user"] and "Teams can ask Acme Corp." in seen["user"])

    def boom(*a, **k):
        raise llm.LLMError("down")
    llm.chat_json = boom
    check("call: a model failure returns no points instead of raising", publisher.run_publisher_points(article, sponsor, "Widget Market", LOG) == {"intro_angle": "", "points": []})
finally:
    llm.chat_json = orig

formatted = prompts.PUBLISHER_POINTS_USER.format(market_name="M", title="T", body="B", sponsor="S")
check("prompt: formats", "GUEST ARTICLE" in formatted)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all publisher tests passed")
