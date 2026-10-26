"""Offline test for keyword highlighting + legend counts (main._apply_keyword_spans / _display_sections).

Run from backend/: python devtools/test_highlight.py
The legend counts are produced by the same pass that marks the text, so they must agree with the marks.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
import pipeline  # noqa: E402
from docx_io import Paragraph  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


MARKET = "Zzz Widget Market"
secondary_plan = [(f"{MARKET} size", "trends"), (f"{MARKET} share", "trends")]
sponsor = Paragraph(index=0, text="", links=[])
sections = [
    {"heading": "H", "sop": ["trends"], "paragraphs": [
        "The Zzz Widget Market is large. The size of the zzz widget market is rising, and the zzz widget market share is stable. "
        "Buyers search for edge widget pricing models before they commit."]},
    {"heading": "H2", "sop": ["key_players"], "paragraphs": [
        "Another look at the pricing models for edge widgets appears here, and the size of the zzz widget market is not tagged for this section."]},
]
counts: dict = {}
out = main._display_sections(sections, sponsor, None, MARKET, secondary_plan,
                             longtails=["edge widget pricing models", "unused long tail phrase"], counts=counts)

marks = {"primary": 0, "secondary": 0, "longtail": 0}
longtail_text = []
for s in out:
    for p in s["paragraphs"]:
        for seg in p:
            if seg.get("kw"):
                marks[seg["kw"]] += 1
                if seg["kw"] == "longtail":
                    longtail_text.append(seg["text"])

check("long-tail: a literal use AND a reordered use are both highlighted, in every section", len(longtail_text) == 2 and longtail_text[0] == "edge widget pricing models" and "pricing models for edge widgets" in longtail_text[1], str(longtail_text))
check("long-tail: counted once per use, an unused phrase is not counted", counts["longtail"] == {"edge widget pricing models": 2}, str(counts))
check("primary: the count equals the highlighted primary marks", counts["primary"] == marks["primary"] and counts["primary"] >= 2, f"{counts} {marks}")
check("secondary: a reordered keyword ('size of the market') is counted", counts["secondary"].get(f"{MARKET} size", 0) == 1, str(counts))
check("secondary: counted only in the section it is tagged for (not in the untagged one)", counts["secondary"].get(f"{MARKET} size") == 1, str(counts))
check("secondary: the count total equals the highlighted secondary marks", sum(counts["secondary"].values()) == marks["secondary"], f"{counts} {marks}")

# a secondary keyword whose only words sit inside the primary keyword's own text is not a separate use
counts2: dict = {}
main._display_sections(
    [{"heading": "H", "sop": ["trends"], "paragraphs": ["Nothing here but the Zzz Widget Market itself."]}],
    sponsor, None, MARKET, secondary_plan, longtails=[], counts=counts2,
)
check("counts: nothing is invented where only the primary keyword appears", counts2["secondary"] == {} and counts2["longtail"] == {}, str(counts2))

# The market-first chain (CMI_CHAIN_V2) gets tertiary (related-term) records from its search-demand
# pass alongside the long-tail phrases (main._step3_v2 sets run.longtails from plan_inputs.longtails).
# Both must be marked by the same display-sections pass: a long-tail phrase labelled 'longtail' AND a
# related term labelled 'tertiary' when both sit in the same paragraph.
counts3: dict = {}
tertiary_out = main._display_sections(
    [{"heading": "H", "sop": ["trends"], "paragraphs": [
        "The Zzz Widget Market is large. Buyers now prefer widget upgrades, and edge widget pricing models are still a niche search."]}],
    sponsor, None, MARKET, secondary_plan,
    longtails=["edge widget pricing models"], counts=counts3,
    tertiary_terms=["widget upgrades"],
)
tertiary_marks = []
longtail_marks = []
for s in tertiary_out:
    for p in s["paragraphs"]:
        for seg in p:
            if seg.get("kw") == "tertiary":
                tertiary_marks.append(seg["text"])
            elif seg.get("kw") == "longtail":
                longtail_marks.append(seg["text"])
check("tertiary: a related term is labelled 'tertiary' when it appears with a long-tail phrase",
      tertiary_marks == ["widget upgrades"], str(tertiary_marks))
check("tertiary: the long-tail phrase in the same paragraph is still labelled 'longtail'",
      longtail_marks == ["edge widget pricing models"], str(longtail_marks))
check("tertiary: counted per phrase, long-tail counted per phrase, same pass",
      counts3["tertiary"] == {"widget upgrades": 1} and counts3["longtail"] == {"edge widget pricing models": 1}, str(counts3))

# A loosely matched long-tail — its content words spread across one sentence, reordered and with
# ordinary words between them, so no light-glued stretch exists — highlights EACH content word's own
# span, still labelled 'longtail', and counts as ONE use of the phrase (the same loose use
# run_checks_v2's used checks credit).
counts4: dict = {}
loose_out = main._display_sections(
    [{"heading": "H", "sop": ["trends"], "paragraphs": [
        "Widget makers debate pricing models at the edge of the shelf."]}],
    sponsor, None, MARKET, secondary_plan,
    longtails=["edge widget pricing models"], counts=counts4,
)
loose_marks = []
for s in loose_out:
    for p in s["paragraphs"]:
        for seg in p:
            if seg.get("kw") == "longtail":
                loose_marks.append(seg["text"])
check("long-tail loose: a spread, reordered use highlights each content word's own span",
      loose_marks == ["Widget", "pricing", "models", "edge"], str(loose_marks))
check("long-tail loose: the spread use counts once, not once per word",
      counts4["longtail"] == {"edge widget pricing models": 1}, str(counts4))

# Same fallback for a section-scoped secondary keyword: no one-stretch match exists (the suffix's
# words sit more than the strict window apart) but the loose matcher finds all of its content words
# in one sentence, so each word's own span is marked with the same label and counted once.
counts5: dict = {}
SEC_KW = f"{MARKET} competitive landscape"
# The suffix's two words sit more than the strict one-stretch window apart in their sentence (9 tokens
# over a window of 8), so only the loose matcher can certify the use.
sec_loose_out = main._display_sections(
    [{"heading": "H", "sop": ["key_players"], "paragraphs": [
        "The Zzz Widget Market is contested. Rivals keep the competitive edge through daily "
        "price edits while the landscape of vendors widens."]}],
    sponsor, None, MARKET, [(SEC_KW, "key_players")],
    longtails=[], counts=counts5,
)
sec_marks = []
for s in sec_loose_out:
    for p in s["paragraphs"]:
        for seg in p:
            if seg.get("kw") == "secondary":
                sec_marks.append(seg["text"])
check("secondary loose: a spread keyword highlights each content word's own span",
      sec_marks == ["competitive", "landscape"], str(sec_marks))
check("secondary loose: the spread use counts once, not once per word",
      counts5["secondary"] == {SEC_KW: 1}, str(counts5))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all highlight tests passed")
