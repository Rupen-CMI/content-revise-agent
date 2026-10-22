"""Market-first chain, stage 3: PUBLISHER POINTS.

The guest article is only context for the market article: its subject opens the post as an example, and a later section
may borrow one short point from it. One LLM call boils the article down to an `intro_angle` and a few plain points; code
then drops any point that states a market fact (size, growth, share, segments, vendors), is attributed to CMI or a
research firm, names the guest's brand, or carries a number the article does not itself contain. The writer sees these
points, never the raw article, which is what keeps the article's jargon and its unverified market claims out.

Imports pipeline/facts for shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import re

import facts
import freshness
import llm
import prompts
from docx_io import Paragraph, SourceArticle
from pipeline import _BODY_CHARS, _brand_terms
from pipeline_log import section

_MAX_POINTS = 6
_MIN_POINT_WORDS, _MAX_POINT_WORDS = 4, 40
_MAX_ANGLE_WORDS = 45

# A point about the market itself (rather than the article's subject): the word "market" next to a growth, size or
# structure word, or a named research source. These never belong in a point, whatever numbers they carry.
_MARKET_WORD_RE = re.compile(r"\bmarkets?\b", re.IGNORECASE)
_MARKET_FACT_RE = re.compile(
    r"\b(grow\w*|size[ds]?|valued|valuation|forecast\w*|cagr|share|segments?|segmentation|vendors?|players?|industry|"
    r"worth|billion|million|trillion)\b", re.IGNORECASE,
)
_RESEARCH_SOURCE_RE = re.compile(
    r"\b(CMI|Coherent Market Insights|market research firm|research firm|Gartner|Forrester|IDC|Statista|Mordor|"
    r"Grand View|MarketsandMarkets|Fortune Business)\b", re.IGNORECASE,
)


def point_problem(text: str, article_numbers: set[str], brand_terms: list[str]) -> str | None:
    """Why a point may not be used, or None when it may."""
    words = len(text.split())
    if not _MIN_POINT_WORDS <= words <= _MAX_POINT_WORDS:
        return f"length ({words} words)"
    if _RESEARCH_SOURCE_RE.search(text):
        return "attributed to CMI or a research firm"
    if _MARKET_WORD_RE.search(text) and _MARKET_FACT_RE.search(text):
        return "states a market fact"
    stray = sorted(n for n in facts._numbers(text) if n not in article_numbers)
    if stray:
        return f"numbers {stray} are not in the article"
    low = text.lower()
    if any(t and t.lower() in low for t in brand_terms):
        return "names the guest's brand"
    return None


def normalize_points(raw: dict, article_body: str, brand_terms: list[str], logger: logging.Logger) -> dict:
    """{"intro_angle": str, "points": [{"id": "P1", "text": ...}]} after dropping every point or angle that fails."""
    raw = raw if isinstance(raw, dict) else {}
    nums = facts._numbers(article_body)
    points: list[dict] = []
    for item in raw.get("points") if isinstance(raw.get("points"), list) else []:
        text = " ".join(str(item.get("text") if isinstance(item, dict) else item or "").split())
        if not text:
            continue
        why = point_problem(text, nums, brand_terms)
        if why:
            logger.info(f"[publisher] dropped point ({why}): {text[:110]!r}")
            continue
        if any(text.lower() == p["text"].lower() for p in points):
            continue
        points.append({"id": f"P{len(points) + 1}", "text": text})
        if len(points) >= _MAX_POINTS:
            break
    angle = " ".join(str(raw.get("intro_angle") or "").split())
    if angle:
        why = point_problem(angle, nums, brand_terms) if len(angle.split()) <= _MAX_ANGLE_WORDS else "too long"
        if why and why != f"length ({len(angle.split())} words)":
            logger.info(f"[publisher] dropped intro angle ({why}): {angle[:110]!r}")
            angle = ""
    logger.info(f"[publisher] {len(points)} point(s) kept; angle {'kept' if angle else 'missing'}")
    return {"intro_angle": angle, "points": points}


def run_publisher_points(
    article: SourceArticle, sponsor: Paragraph, market_name: str, logger: logging.Logger,
) -> dict:
    """CALL: boil the guest article down to plain points. Best-effort: a failure returns no points, and the planner then
    builds the intro from the article's title and subject alone."""
    section(logger, "CALL: PUBLISHER POINTS")
    body, removed = freshness.strip_stale_developments(article.body)
    if removed:
        logger.info(f"[publisher] dropped {len(removed)} stale dated development(s) from the article text")
    try:
        raw = llm.chat_json(
            prompts.PUBLISHER_POINTS_SYSTEM,
            prompts.PUBLISHER_POINTS_USER.format(
                market_name=market_name, title=article.title, body=body[:_BODY_CHARS], sponsor=sponsor.text,
            ),
            temperature=0.2, call_name="publisher_points", logger=logger,
        )
    except llm.LLMError:
        logger.warning("[publisher] the publisher points call failed; the intro will rest on the title alone", exc_info=True)
        return {"intro_angle": "", "points": []}
    return normalize_points(raw, body, _brand_terms(sponsor), logger)
