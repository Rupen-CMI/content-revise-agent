"""Offline test for the publisher-block / market-block layout rules (no network, no LLM calls).

Run from backend/: python devtools/test_layout.py

Covers:
  * the interleaved 12-section draft the old prompt produced (fails on topic switches)
  * the intended layout: intro, 3 publisher sections, then report_context first in the market
    block, trends, segmentation, subsegment, country_us, key_players, conclusion
  * report_context not coming first in the market block
  * the publisher-share floor (40% fails, 60% passes)
  * the sponsor placeholder's real words counting as publisher words
  * the two new soft checks in checks.run_checks
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402

TOKEN = prompts.SPONSOR_TOKEN
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def words(n: int) -> str:
    """A paragraph of exactly n words (placeholder tokens are never used here)."""
    return " ".join(f"w{i}" for i in range(n))


def section(heading: str, sop: list[str], counts: list[int]) -> dict:
    return {"heading": heading, "sop": sop, "paragraphs": [words(n) for n in counts]}


def interleaved() -> list[dict]:
    """The real 12-section shape of recent drafts: P, P, M, M, P, M, M, P, M, P, M, M."""
    return [
        section("", [], [50, 50, 50]),
        section("Publisher one", [], [90]),
        section("Trends", ["trends"], [150]),
        section("Report context", ["report_context"], [60, 60]),
        section("Publisher two", [], [90]),
        section("Segmentation", ["segmentation"], [50, 50]),
        section("Sub-segments", ["subsegment"], [70, 70]),
        section("Publisher three", [], [90]),
        section("U.S. analysis", ["country_us"], [130]),
        section("Publisher four", [], [90]),
        section("Players", ["key_players"], [60, 70]),
        section("Conclusion", ["conclusion"], [50]),
    ]


def good() -> list[dict]:
    """Intro, 3 publisher sections, then one market block with report_context as the bridge."""
    return [
        {"heading": "", "sop": [], "paragraphs": [words(50), TOKEN, words(50)]},
        section("Publisher one", [], [120, 120]),
        section("Publisher two", [], [120, 120]),
        section("Publisher three", [], [120, 120]),
        section("Report context", ["report_context"], [80]),
        section("Trends", ["trends"], [145]),
        section("Segmentation", ["segmentation"], [80]),
        section("Sub-segments", ["subsegment"], [62, 62]),
        section("U.S. analysis", ["country_us"], [110]),
        section("Players", ["key_players"], [45, 55]),
        section("Conclusion", ["conclusion"], [55]),
    ]


SPONSOR_TEXT = words(50)


def share(sections: list[dict], sponsor_text: str = "") -> tuple[int, int, float]:
    pub, total = pipeline._layout_share(sections, sponsor_text)
    return pub, total, (pub / total * 100) if total else 0.0


# ---------------------------------------------------------------- 1. interleaved draft
inter = interleaved()
inter_problems = pipeline._layout_problems(inter, SPONSOR_TEXT)
print("interleaved order:", checks._layout_stats(inter, SPONSOR_TEXT)[0])
check(
    "interleaved draft: topic-switch check fails and lists the actual order",
    any("changes subject" in p and "Publisher one P" in p for p in inter_problems),
    repr(inter_problems),
)
check(
    "interleaved draft: report_context is not the first market section, so that check fails too",
    any("report_context must be the first market section" in p for p in inter_problems),
    repr(inter_problems),
)
_, inter_switches, _, _ = checks._layout_stats(inter, SPONSOR_TEXT)
check("interleaved draft: switch count is 7", inter_switches == 7, f"got {inter_switches}")

# ---------------------------------------------------------------- 2. intended layout
good_sections = good()
good_problems = pipeline._layout_problems(good_sections, SPONSOR_TEXT)
check("good draft: all layout checks pass", good_problems == [], repr(good_problems))
order, switches, pub_words, total_words = checks._layout_stats(good_sections, SPONSOR_TEXT)
check("good draft: exactly one topic switch", switches == 1, f"{switches} — {order}")
pub, total, pct = share(good_sections, SPONSOR_TEXT)
check(
    "good draft: publisher share is in the soft 55-65% band",
    55 <= pct <= 65,
    f"{pct:.1f}% ({pub} of {total})",
)
check(
    "good draft: section order is P...P then M...M",
    order.startswith("intro P | Publisher one P | Publisher two P | Publisher three P | Report context M"),
    order,
)

# ---------------------------------------------------------------- 3. report_context must come first
late_context = [dict(s) for s in good_sections]
trends_i = next(i for i, s in enumerate(late_context) if "trends" in s["sop"])
context_i = next(i for i, s in enumerate(late_context) if "report_context" in s["sop"])
late_context[trends_i], late_context[context_i] = late_context[context_i], late_context[trends_i]
late_problems = pipeline._layout_problems(late_context, SPONSOR_TEXT)
check(
    "market block starting with trends: report_context check fails",
    any("report_context must be the first market section" in p for p in late_problems),
    repr(late_problems),
)
check(
    "market block starting with trends: switch count still passes (grouping is unchanged)",
    not any("changes subject" in p for p in late_problems),
    repr(late_problems),
)

# ---------------------------------------------------------------- 4. publisher share floor
# 400 publisher words / 1,000 total = 40%
forty = [
    section("", [], [100]),
    section("Publisher one", [], [200, 100]),
    section("Report context", ["report_context"], [90]),
    section("Trends", ["trends"], [90]),
    section("Segmentation", ["segmentation"], [90]),
    section("Sub-segments", ["subsegment"], [45, 45]),
    section("U.S. analysis", ["country_us"], [90]),
    section("Players", ["key_players"], [45, 45]),
    section("Conclusion", ["conclusion"], [60]),
]
forty_problems = pipeline._layout_problems(forty)
_, _, forty_pct = share(forty)
check("40% publisher draft: share check fails", any("publisher content is only" in p for p in forty_problems), repr(forty_problems))
check("40% publisher draft: measured share is 40%", round(forty_pct) == 40, f"{forty_pct:.1f}%")

sixty = [
    section("", [], [150]),
    section("Publisher one", [], [400, 200, 150]),
    section("Report context", ["report_context"], [90]),
    section("Trends", ["trends"], [90]),
    section("Segmentation", ["segmentation"], [90]),
    section("Sub-segments", ["subsegment"], [45, 45]),
    section("U.S. analysis", ["country_us"], [90]),
    section("Players", ["key_players"], [45, 45]),
    section("Conclusion", ["conclusion"], [60]),
]
sixty_problems = pipeline._layout_problems(sixty)
_, _, sixty_pct = share(sixty)
check("60% publisher draft: share check passes", not any("only" in p and "publisher content" in p for p in sixty_problems), repr(sixty_problems))
check("60% publisher draft: measured share is 60%", round(sixty_pct) == 60, f"{sixty_pct:.1f}%")

# ---------------------------------------------------------------- 5. sponsor real words count as publisher
sponsor_case = [
    {"heading": "", "sop": [], "paragraphs": [words(40), TOKEN, words(40)]},
    section("Report context", ["report_context"], [60]),
    section("Market trends", ["trends"], [60]),
    section("Conclusion", ["conclusion"], [0]),
]
pub_without, total_without, pct_without = share(sponsor_case)
pub_with, total_with, pct_with = share(sponsor_case, SPONSOR_TEXT)
check(
    "sponsor words excluded: publisher share is 40% and the share check fails",
    (pub_without, total_without) == (80, 200) and round(pct_without) == 40
    and any("publisher content is only" in p for p in pipeline._layout_problems(sponsor_case)),
    f"{pub_without}/{total_without} = {pct_without:.1f}%",
)
check(
    "sponsor words included: the same draft clears the 50% floor and the share check passes",
    (pub_with, total_with) == (130, 250) and round(pct_with) == 52
    and not any("publisher content is only" in p for p in pipeline._layout_problems(sponsor_case, SPONSOR_TEXT)),
    f"{pub_with}/{total_with} = {pct_with:.1f}%",
)

# ---------------------------------------------------------------- 6. the new soft run_checks results
def run_layout_checks(sections: list[dict], sponsor_text: str) -> dict[str, checks.CheckResult]:
    results = checks.run_checks(
        "A title long enough for the soft check", sections, "", [], sponsor_text=sponsor_text,
    )
    return {r.name: r for r in results}


good_results = run_layout_checks(good_sections, SPONSOR_TEXT)
check(
    "run_checks: publisher-majority soft check passes on the good draft",
    good_results["Publisher content is the majority (~60%, soft)"].passed,
    good_results["Publisher content is the majority (~60%, soft)"].detail,
)
check(
    "run_checks: grouped-topics soft check passes on the good draft",
    good_results["Topics are grouped: publisher block, then market block"].passed,
    good_results["Topics are grouped: publisher block, then market block"].detail,
)
inter_results = run_layout_checks(interleaved(), SPONSOR_TEXT)
check(
    "run_checks: grouped-topics soft check fails on the interleaved draft",
    not inter_results["Topics are grouped: publisher block, then market block"].passed,
    inter_results["Topics are grouped: publisher block, then market block"].detail,
)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
    sys.exit(1)
print("all layout tests passed")
