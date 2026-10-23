"""Market-first chain, stage 2: SEARCH DEMAND.

One LLM call answers "what do ordinary people type into Google or ask an AI assistant about this market?". It returns
the Most Searched Questions (MSQs, one per kind of need), related terms (the tertiary keywords) and informational
long-tail searches tied to the fact sheet. Code then validates each of them, and later marks each question answerable or
not from the fact sheet and the selected evidence, so a question the sources cannot answer never becomes a heading or an
FAQ entry.

Imports pipeline/facts for shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import re

import checks
import facts
import intent
import llm
import prompts
from pipeline import _faq_tokens, _is_definition_question, _market_core
from pipeline_log import section

# Function words that let a long-tail phrase read as one noun phrase instead of a keyword stack. Imported from
# checks._LONGTAIL_GLUE_WORDS (no circular import: checks imports only freshness and prompts at module level), so
# the demand-side filter and the check layer agree on what a joining word is.
_LONGTAIL_GLUE_WORDS = checks._LONGTAIL_GLUE_WORDS

# Kind of search need -> the SOP section whose job answers it (the section its question may head).
INTENT_SECTION = {
    "size": "report_context", "growth": "report_context",
    "trends": "trends", "drivers": "trends", "challenges": "trends", "technology_impact": "trends",
    "segments": "segmentation",
    "region": "country_us", "us": "country_us",
    "players": "key_players",
    "outlook": "conclusion", "opportunities": "conclusion",
}
INTENTS = tuple(INTENT_SECTION)
_MSQ_MAX = 14
_MSQ_MIN_WORDS, _MSQ_MAX_WORDS = 4, 16
_NEAR_DUPLICATE = 0.7           # overlap of content words above which two questions are the same need reworded
_TERM_MAX = 10
_LONGTAIL_MAX = 8
_LONGTAIL_MIN_WORDS, _LONGTAIL_MAX_WORDS = 3, 8
_FACT_LINE_CHARS = 170
_PLACEHOLDER_RE = re.compile(r"[\[\]<>{}]")


def _format_facts(flat: list[dict]) -> str:
    lines = [f"- {f['id']}: {f['text'][:_FACT_LINE_CHARS]}" for f in flat]
    return "\n".join(lines) or "(the report page states no structured facts)"


def _content_words(text: str, market_name: str) -> set[str]:
    return _faq_tokens(text, market_name)


def normalize_questions(raw, market_name: str, logger: logging.Logger) -> list[dict]:
    """MSQs that survive: a known kind of need, a real market-shaped question that names the market, not a definition,
    4-16 words, and not the same need as an earlier question reworded. Ordered by kind, ids Q1..Qn."""
    core = _market_core(market_name)
    core_tokens = facts._core_tokens(market_name)
    kept: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("intent") or "").strip().lower()
        q = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
        if kind not in INTENT_SECTION or not q:
            logger.info(f"[demand] dropped question (unknown kind {kind!r} or empty): {item}")
            continue
        if not q.endswith("?"):
            q = q.rstrip(".!") + "?"
        words = q.split()
        if not _MSQ_MIN_WORDS <= len(words) <= _MSQ_MAX_WORDS:
            logger.info(f"[demand] dropped question (length {len(words)}): {q!r}")
            continue
        qwords = {facts._stem(w) for w in re.findall(r"[a-z0-9]+", q.lower())}
        if core_tokens and sum(1 for t in core_tokens if t in qwords) < max(1, (len(core_tokens) + 1) // 2):
            logger.info(f"[demand] dropped question (does not name the market): {q!r}")
            continue
        if not intent._is_market_question(q, core):
            logger.info(f"[demand] dropped question (not about the market as a whole): {q!r}")
            continue
        if _is_definition_question(q):
            logger.info(f"[demand] dropped question (a definition): {q!r}")
            continue
        tokens = _content_words(q, market_name)
        twin = next((k for k in kept if tokens and (tokens & k["_tokens"])
                     and len(tokens & k["_tokens"]) / min(len(tokens), len(k["_tokens"])) >= _NEAR_DUPLICATE), None)
        if twin is not None:
            logger.info(f"[demand] dropped question (same need as {twin['question']!r}): {q!r}")
            continue
        if sum(1 for k in kept if k["intent"] == kind) >= 2:
            logger.info(f"[demand] dropped question (kind {kind!r} already has two): {q!r}")
            continue
        kept.append({"question": q, "intent": kind, "section": INTENT_SECTION[kind], "_tokens": tokens})
        if len(kept) >= _MSQ_MAX:
            break
    kept.sort(key=lambda k: INTENTS.index(k["intent"]))
    out = []
    for i, k in enumerate(kept, 1):
        k.pop("_tokens")
        out.append({"id": f"Q{i}", **k})
    return out


def normalize_terms(raw, market_name: str, logger: logging.Logger) -> list[str]:
    """Related terms (tertiary keywords): 1-4 words, lowercase, no placeholders, not the market's own name."""
    core = _market_core(market_name)
    out: list[str] = []
    for t in raw if isinstance(raw, list) else []:
        term = re.sub(r"\s+", " ", str(t or "")).strip().lower().strip(".,;:")
        if not term or _PLACEHOLDER_RE.search(term) or not 1 <= len(term.split()) <= 4:
            continue
        if term in (core, core + " market", market_name.lower()) or term in out:
            continue
        out.append(term)
        if len(out) >= _TERM_MAX:
            break
    logger.info(f"[demand] {len(out)} related term(s): {out}")
    return out


def normalize_longtails(raw, flat_facts: list[dict], market_name: str, logger: logging.Logger,
                        market_core: str | None = None) -> list[dict]:
    """Informational long-tail searches, shaped like the older long-tail records ({keyword, intent, why}) so the keyword
    highlighting and checks that already read them keep working. Each must rest on a real fact id, be 3-8 words and hold
    at least two words beyond the market's own name. The phrases are noun-phrase style searches: they fit inside a
    statement, so a question-shaped phrase (one whose first word is an interrogative) is never kept. The market's core
    phrase (`market_core` may be the full market name or the stripped core; it counts as ONE noun unit when the
    keyword-stack check measures runs, because a multi-word market name is not a pile by itself)."""
    fact_ids = {f["id"] for f in flat_facts}
    core_words = set(re.findall(r"[a-z0-9]+", _market_core(market_name))) | {"market", "global", "the", "of", "in", "for", "and"}
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kw = re.sub(r"\s+", " ", str(item.get("keyword") or "")).strip().lower().rstrip("?.")
        fid = str(item.get("fact") or "").strip()
        if not kw or _PLACEHOLDER_RE.search(kw) or kw in seen:
            continue
        if re.search(r"\d", kw):
            logger.info(f"[demand] dropped long-tail (it holds a year or number, which cannot sit naturally in prose): {kw!r}")
            continue
        words = re.findall(r"[a-z]+", kw)
        repeats = {w for w in words if len(w) >= 4 and words.count(w) > 1}
        if repeats:
            logger.info(f"[demand] dropped long-tail (a word repeats, so it cannot read as one phrase): {kw!r}")
            continue
        # a long-tail search phrase is a noun-phrase style search that a writer can sit inside a statement, so any
        # phrase that opens with an interrogative word (a question) is rejected, whatever follows it
        if kw.split() and kw.split()[0] in ("what", "why", "how", "who", "whom", "whose", "where", "when", "which"):
            logger.info(f"[demand] dropped long-tail (read as a question, and a long-tail must fit inside a statement): {kw!r}")
            continue
        # and it must read as ONE phrase: a run of five or more consecutive words with no joining word (an article,
        # a preposition, a conjunction, is/are, like) is a keyword stack, not a phrase a person would say. The market's
        # own core phrase is one compound noun, so each occurrence inside the phrase collapses to a single unit first —
        # a 3-word market core plus two surrounding nouns is a natural phrase, not a pile.
        core_units = re.findall(r"[a-z]+", _market_core(market_core)) if market_core else None
        units = re.findall(r"[a-z]+", kw)
        if core_units:
            collapsed: list[str] = []
            i = 0
            while i < len(units):
                if units[i:i + len(core_units)] == core_units:
                    collapsed.append("coreunit")
                    i += len(core_units)
                else:
                    collapsed.append(units[i])
                    i += 1
            units = collapsed
        run = 0
        piled = False
        for w in units:
            run = 0 if w in _LONGTAIL_GLUE_WORDS else run + 1
            if run >= 5:
                piled = True
                break
        if piled:
            logger.info(f"[demand] dropped long-tail (five or more words in a row with no joining word, so it reads "
                        f"as a stack of keywords, not a phrase): {kw!r}")
            continue
        n = len(kw.split())
        if not _LONGTAIL_MIN_WORDS <= n <= _LONGTAIL_MAX_WORDS:
            logger.info(f"[demand] dropped long-tail (length {n}): {kw!r}")
            continue
        if fid not in fact_ids:
            logger.info(f"[demand] dropped long-tail (unknown fact id {fid!r}): {kw!r}")
            continue
        extra = [w for w in re.findall(r"[a-z0-9]+", kw) if w not in core_words]
        if len(extra) < 2:
            logger.info(f"[demand] dropped long-tail (the market's name plus too little): {kw!r}")
            continue
        seen.add(kw)
        out.append({"keyword": kw, "intent": "informational", "why": " ".join(str(item.get("why") or "").split()), "fact": fid})
        if len(out) >= _LONGTAIL_MAX:
            break
    logger.info(f"[demand] {len(out)} long-tail search(es): {[o['keyword'] for o in out]}")
    return out


def run_search_demand(market_name: str, fs: dict, logger: logging.Logger) -> dict:
    """CALL: what people search about this market. Best-effort: a failure returns empty pools, logged, never fatal.
    Returns {"questions": [...], "terms": [...], "long_tail": [...]}."""
    section(logger, "CALL: SEARCH DEMAND")
    flat = facts.flatten_fact_sheet(fs)
    try:
        raw = llm.chat_json(
            prompts.SEARCH_DEMAND_SYSTEM,
            prompts.SEARCH_DEMAND_USER.format(market_name=market_name, facts=_format_facts(flat)),
            temperature=0.3, call_name="search_demand", logger=logger,
        )
    except llm.LLMError:
        logger.warning("[demand] the search demand call failed; no questions or keywords were produced", exc_info=True)
        return {"questions": [], "terms": [], "long_tail": []}
    questions = normalize_questions(raw.get("questions"), market_name, logger)
    logger.info(f"[demand] {len(questions)} most-searched question(s): "
                + "; ".join(f"{q['id']}[{q['intent']}]={q['question']}" for q in questions))
    return {
        "questions": questions,
        "terms": normalize_terms(raw.get("terms"), market_name, logger),
        "long_tail": normalize_longtails(raw.get("long_tail"), flat, market_name, logger, market_core=market_name),
    }


# --------------------------------------------------------------------------------------------- answerability

def _answerable_by(intent_kind: str, fs: dict, evidence_groups: set[str]) -> bool:
    """Can the fact sheet or the selected evidence answer this kind of question with a real fact?"""
    has = lambda key: bool(fs.get(key))  # noqa: E731
    regions = fs.get("regions") or {}
    return {
        "size": has("size"),
        "growth": has("forecast") or has("cagr"),
        "trends": "trends" in evidence_groups or has("drivers"),
        "drivers": has("drivers"),
        "challenges": has("restraints"),
        "technology_impact": "trends" in evidence_groups,
        "segments": has("segments"),
        "region": bool(regions.get("leader") or regions.get("fastest")),
        "us": has("us") or "us" in evidence_groups,
        "players": has("players") or "players" in evidence_groups,
        "outlook": has("forecast") or has("cagr") or has("opportunities"),
        "opportunities": has("opportunities"),
    }[intent_kind]


def mark_answerability(questions: list[dict], fs: dict, evidence_items: list[dict], logger: logging.Logger) -> list[dict]:
    """Adds `answerable` to each question. `evidence_items` are the selected market evidence ({tags: [...]}, as produced by
    pipeline._parse_evidence); their section tags say which kinds of question they can support. An unanswerable
    question is kept in the list (the editor sees it) but flagged, and the plan never uses it."""
    groups = {t for it in evidence_items for t in (it.get("tags") or [])}
    for q in questions:
        q["answerable"] = _answerable_by(q["intent"], fs, groups)
        if not q["answerable"]:
            logger.info(f"[demand] {q['id']} [{q['intent']}] is not answerable from the fact sheet or the evidence: {q['question']!r}")
    logger.info(f"[demand] {sum(q['answerable'] for q in questions)} of {len(questions)} question(s) are answerable")
    return questions
