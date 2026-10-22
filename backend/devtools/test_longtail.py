"""Offline test for long-tail keyword generation + integration (no network, no LLM calls).

Run from backend/: python devtools/test_longtail.py

Covers: _normalize_longtails sanitising (placeholder brackets, word-count bounds, the five intent
clusters, duplicate collapse, market-name echo), _format_longtail_lines, _longtail_usage's
literal/freezed-order matching, and that every prompt template the feature touches includes the
long-tail field and formats. Not the actual generation call (pipeline.run_longtail_keywords),
which needs the LLM and is best-effort by design.
"""
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging
import pipeline  # noqa: E402
import prompts  # noqa: E402

LOG = logging.getLogger("test_longtail")
LOG.addHandler(logging.NullHandler())
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


MARKET = "Wealth Management Platform Market"
CORE = pipeline._market_core(MARKET)


def kw(text: str, intent: str = "niche_b2b", why: str = "x") -> dict:
    return {"keyword": text, "intent": intent, "why": why}


# ------------------------------------------------------------------ _normalize_longtails
good = pipeline._normalize_longtails([
    kw("wealth management for high-net-worth families", "niche_b2b"),
    kw("robo-advisory platform vs traditional advisory", "vendor_comparison"),
    kw("cloud-based portfolio management pricing", "commercial_pricing"),
], MARKET, LOG)
check("normalize: 3 valid entries kept", len(good) == 3, str([t["keyword"] for t in good]))
check("normalize: intents carried through", {t["intent"] for t in good} == {"niche_b2b", "vendor_comparison", "commercial_pricing"})
check("normalize: why clauses carried through", good[0]["why"] == "x")

out = pipeline._normalize_longtails([
    kw("wealth management software for <audience>"),          # angle bracket placeholder -> dropped
    kw("wealth management [city]"),                           # square bracket placeholder -> dropped
    kw("best robo-advisor"),                                  # 2 words (< 3) -> dropped
    kw("a very very long tail phrase with far too many words in it"),  # 12 words (> 8) -> dropped
    kw("wealth management for doctors", "made_up_intent"),    # unknown intent -> dropped
    kw("wealth management for doctors"),                      # duplicate (below) collapses
    kw("wealth management for doctors", why="y"),
], MARKET, LOG)
check("normalize: placeholder/short/unknown-intent entries dropped, duplicate collapsed",
      [(t["keyword"], t["why"]) for t in out] == [("wealth management for doctors", "x")], str(out))

echo = pipeline._normalize_longtails([kw("zebra platform market", "research_b2b")], "Zebra Platform Market", LOG)
check("normalize: an exact echo of the market name itself is dropped (the primary keyword's job)", echo == [], str(echo))

capped = pipeline._normalize_longtails(
    [kw(f"phrase number {i} about the zebra market") for i in range(12)], MARKET, LOG)
check("normalize: capped at 8 candidates", len(capped) == pipeline._LONGTAIL_COUNT, str(len(capped)))
check("normalize: non-list input gives []",
      pipeline._normalize_longtails(None, MARKET, LOG) == [] and pipeline._normalize_longtails("x", MARKET, LOG) == [])

# ------------------------------------------------------------------ _format_longtail_lines
check("format: empty list gives the 'none' placeholder line",
      "without long-tail" in pipeline._format_longtail_lines([]) or "none" in pipeline._format_longtail_lines([]).lower())
block = pipeline._format_longtail_lines(good)
check("format: each phrase, intent and why appear", all(s in block for s in
      ('"wealth management for high-net-worth families"', "[intent: niche_b2b]", "reader intent: x")))
check("format: five lines for five candidates, one per line", len(good) > 0 and block.count("\n") == len(good) - 1)

# ------------------------------------------------------------------ _longtail_usage (concept match: reordered uses count)
sections = [
    {"heading": "H1", "sop": ["trends"], "paragraphs": ["Small accounts are served by cloud deployment, and wealth management for high-net-worth families stays the fee engine."]},
    {"heading": "H2", "sop": ["key_players"], "paragraphs": ["Vendor comparison questions come up here, but no phrase appears."]},
]
usage = pipeline._longtail_usage("A Title", sections, good + [kw("never appears anywhere at all", "research_b2b")])
check("usage: a phrase present in the draft is marked used", usage[0]["used"] is True)
check("usage: a whitespace-mismatched phrase still matches (whitespace-normalised)",
      pipeline._longtail_usage("T", [{"heading": "", "sop": [], "paragraphs": ["wealth  management for high-net-worth families."]}], good[:1])[0]["used"] is True)
check("usage: a REORDERED use (words moved, an article added) still counts as used",
      pipeline._longtail_usage("T", [{"heading": "", "sop": [], "paragraphs": ["Wealth management for families of high net worth is a core use."]}], good[:1])[0]["used"] is True)
check("usage: the phrase's words split across two sentences do not count",
      pipeline._longtail_usage("T", [{"heading": "", "sop": [], "paragraphs": ["Wealth is rising. Management of high-net-worth families is hard."]}], good[:1])[0]["used"] is False)
check("usage: a phrase absent from the draft is marked unused", usage[-1]["used"] is False)
check("usage: every candidate in, none out", len(usage) == len(good) + 1)

# ------------------------------------------------------------------ prompt templates
class _Blank(dict):
    def __missing__(self, key):
        return "x"


for name in ("LONGTAIL_USER", "MARKET_USER", "REVISE_USER"):
    tpl = getattr(prompts, name)
    fields = {f for _, f, _, _ in string.Formatter().parse(tpl) if f}
    try:
        tpl.format_map(_Blank())
        ok, detail = True, ""
    except Exception as e:
        ok, detail = False, repr(e)
    check(f"prompt {name} formats with no stray brace ({len(fields)} fields)", ok, detail)

check("prompt LONGTAIL_USER has the count field", "count" in {f for _, f, _, _ in string.Formatter().parse(prompts.LONGTAIL_USER)})
check("prompt MARKET_USER has the longtail field", "longtail_keywords" in {f for _, f, _, _ in string.Formatter().parse(prompts.MARKET_USER)})
check("prompt REVISE_USER has the longtail field", "longtail_keywords" in {f for _, f, _, _ in string.Formatter().parse(prompts.REVISE_USER)})
# The five intent clusters named in the generator prompt must be exactly what _normalize_longtails accepts.
prompt_intents = set(string.Formatter().parse(prompts.LONGTAIL_USER))
listed = set()
import re as _re  # noqa: E402
for m in _re.finditer(r"commercial_review|niche_b2b|research_b2b|commercial_pricing|vendor_comparison", prompts.LONGTAIL_USER):
    listed.add(m.group(0))
check("the five intents in LONGTAIL_USER match _LONGTAIL_INTENTS", listed == set(pipeline._LONGTAIL_INTENTS), str(listed))

# ------------------------------------------------------------------ the wealth-management examples must NOT anchor the prompt
banned = ("wealth management platform market analysis",)
for phrase in banned:
    check(f"prompt LONGTAIL_USER does not contain the example phrase {phrase!r}", phrase not in prompts.LONGTAIL_USER)

# ------------------------------------------------------------------ the two generation places
# Place 1 - orient (Step 1): run_orient stores RAW candidates + the top-guess market they belong to.
raw_orient = {
    "longtail_candidates": [
        {"keyword": "wealth management for high-net-worth families", "intent": "niche_b2b", "why": "audience"},
        {"keyword": "cloud-based portfolio management pricing", "intent": "commercial_pricing", "why": "buyer"},
        {"keyword": "inventory robots for warehouses", "intent": "niche_b2b", "why": "other market"},  # names a different market
    ],
    "longtail_market": MARKET,
}
check("resolve place 1: candidates ride the orient call (raw stored, guessed market recorded)",
      pipeline.longtail_seeds_match(raw_orient, "Global " + MARKET) is True)
check("resolve place 1: unseeded orient (reload/old run) never matches",
      pipeline.longtail_seeds_match({}, MARKET) is False and pipeline.longtail_seeds_match(None, MARKET) is False)
check("resolve place 2: a different confirmed market does NOT reuse the orient candidates",
      pipeline.longtail_seeds_match(raw_orient, "Digital Marketing Software Market") is False)
resolved = pipeline.longtail_candidates_from_orient(raw_orient, MARKET, LOG)
check("resolve place 1: candidates sanitised + the off-market one filtered out",
      [t["keyword"] for t in resolved] == ["wealth management for high-net-worth families",
                                           "cloud-based portfolio management pricing"], str(resolved))
check("resolve place 1: non-dict/missing shapes give []",
      pipeline.longtail_candidates_from_orient({}, MARKET, LOG) == []
      and pipeline.longtail_candidates_from_orient({"longtail_candidates": "x"}, MARKET, LOG) == [])

if FAILURES:
    print()
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print()
print("all long-tail tests passed")
