"""The Evidence step's preparation as ONE model call.

The publisher points, the search demand (the most searched questions, related terms, long-tail searches) and the market search
queries used to be three calls (publisher.run_publisher_points, demand.run_search_demand, pipeline.prepare_search_queries). They
are one call here, answered as {"publisher": ..., "demand": ..., "queries": ...}: each part is the answer its own prompt always
asked for (prompts.evidence_prep_user() holds the three prompts verbatim as three jobs) and each part goes through the code that
always validated it (publisher.points_from_raw, demand.demand_from_raw, pipeline.search_queries_from_result). The keyword research
(real searches, no model) still runs first and feeds the demand job. The three old functions stay for the old chain and for
config.EVIDENCE_ONE_CALL=false.

Best-effort like the three calls were: a failed call gives no points, empty demand pools and the deterministic search queries;
a part the model leaves out or garbles is treated the same way for that part alone.
"""
import logging

import demand
import llm
import pipeline
import prompts
import publisher
import sections
from docx_io import Paragraph, SourceArticle
from pipeline_log import section

_MAX_TOKENS = 8000      # three answers in one: the points, up to 14 questions and 12 long-tail searches, the grouped queries and terms


def run_evidence_prep(
    article: SourceArticle, sponsor: Paragraph, market_name: str, fs: dict, key_players: list[str], segments: list[str],
    plan: dict, real_queries: list[str], logger: logging.Logger,
) -> dict:
    """CALL: {"publisher": {intro_angle, points}, "demand": {questions, terms, long_tail}, "queries": (queries, groups, topic_terms)}.
    `queries` is exactly what pipeline.prepare_search_queries returns for a section plan — in the natural query style
    (config.QUERY_STYLE, the default) the model writes every section's queries there: trends, the market, each fixed
    sub-segment, the U.S. and the key players, each from that section's targets (sections.format_targets)."""
    section(logger, "CALL: EVIDENCE PREP (publisher points + search demand + search queries)")
    body = publisher.article_text(article, logger)
    fields = {
        **publisher.prompt_fields(article, sponsor, market_name, body),
        **demand.prompt_fields(market_name, fs, real_queries),
        **pipeline.search_query_fields(market_name, list(key_players), list(segments)),
        "section_targets": sections.format_targets(plan),
    }
    try:
        raw = llm.chat_json(
            prompts.EVIDENCE_PREP_SYSTEM, prompts.evidence_prep_user().format(**fields),
            temperature=0.3, call_name="evidence_prep", logger=logger, max_tokens=_MAX_TOKENS,
        )
    except llm.LLMError:
        logger.warning("[evidence-prep] the call failed: no publisher points, no search demand, deterministic search queries", exc_info=True)
        raw = None
    return parts_from_raw(raw, sponsor, market_name, fs, body, plan, logger)


def parts_from_raw(
    raw, sponsor: Paragraph, market_name: str, fs: dict, body: str, plan: dict, logger: logging.Logger,
) -> dict:
    """The three parts of the model's answer, each through its own validation (a missing part is an empty answer for it)."""
    raw = raw if isinstance(raw, dict) else {}
    for key in ("publisher", "demand", "queries"):
        if raw and not isinstance(raw.get(key), dict):
            logger.warning(f"[evidence-prep] the answer holds no usable {key!r} part")
    return {
        "publisher": publisher.points_from_raw(raw.get("publisher"), body, sponsor, logger),
        "demand": demand.demand_from_raw(raw.get("demand"), market_name, fs, logger),
        "queries": pipeline.search_queries_from_result(raw.get("queries"), market_name, logger, plan),
    }
