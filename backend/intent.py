"""Search-intent question harvesting: real autocomplete completions for the confirmed market, scored
and filtered into subheading candidates — see prompts.INTENT_SYSTEM/INTENT_USER for the LLM step
(pipeline.run_select_questions) that picks the final subheading questions from what's harvested here.
Harvesting itself never invents a question: every candidate is a real completion a search engine's
own autosuggest returned for a seed query built from the market name, so the pool reflects what
people actually type, not a guess. The FAQ block is a separate, later concern — see pipeline.run_faq,
which generates and answers FAQ questions directly from the CMI report text and the finished article,
not from this harvested pool.

Best-effort like the claim-backing search: a harvested pool is an enhancement to the article, never
a requirement the run can fail on, so every network call here is wrapped and degrades to an empty
pool rather than raising."""
import logging
import re
import time

import httpx

_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
_HEADERS = {"User-Agent": _UA, "Accept-Language": "en-US,en;q=0.9"}
_REQUEST_TIMEOUT_S = 6.0
_REQUEST_GAP_S = 0.25  # light pacing between autosuggest calls — low volume, but still polite

# Seed patterns, shaped for MARKET questions, not service/product questions. A real failure
# (Wealth Management Platform Market) harvested seeds like "what is <core>" / "<core> benefits" /
# "<core> pricing", and Google happily completed them into consumer explainers about the SERVICE
# ("What are the benefits of wealth management?") or buying-intent round-ups ("What is the best
# wealth management platform?") — questions a market-analysis article cannot answer with data,
# whose "evidence" was definition pages and vendor blogs. These seeds instead complete into the
# shapes actual market coverage is written in: growth/size/drivers/forecast/trends of the market,
# its leading players, and its regional (esp. U.S.) dynamics. Open question-word seeds ("what is")
# are dropped entirely: autosuggest completes those toward definitions. Seeds starting with a
# question word are still first where kept ("how big is..."), since that is how a real query reads
# and how autosuggest completes (positive seeding, the same rule the old list encoded).
_SEED_PATTERNS = (
    "how big is the {core} market",
    "why is the {core} market",
    "how fast is the {core} market",
    "is the {core} market growing",
    "what is driving the {core} market",
    "who are the key players in the {core}",
    "which companies lead the {core}",
    "{core} market trends",
    "{core} market forecast",
    "{core} market growth",
    "{core} market size",
    "{core} market challenges",
    "{core} market drivers",
    "{core} market outlook",
    "{core} market demand",
    "{core} market opportunities",
    "{core} market news",
    "north america {core} market",
    "united states {core} market",
    "{core} industry trends",
    "{core} vs",
    "{core} companies",
    "{core} trends",
    "{core} growth",
)

_QUESTION_WORDS = frozenset({
    "what", "why", "how", "which", "who", "when", "where",
    "can", "does", "do", "is", "are", "will", "should", "could",
})

_STOPWORDS = frozenset({
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or", "is", "are", "do", "does",
    "how", "what", "why", "which", "who", "when", "where", "will", "should", "could", "can",
    "market", "with", "by", "its", "it", "this", "that", "their", "about",
})

_SIGNAL_WORDS = frozenset({
    # Market-analysis angles only. "benefit(s)", "pricing", "price", "cost(s)" and "compare/-
    # comparison" were REMOVED from the old set: they scored service-level consumer questions
    # ("What are the benefits of wealth management?") as if they were substantive market angles,
    # which is exactly the failure the seeds were reworked to stop. Added: the frame words'
    # company/landscape/leadership angles and the dynamics verbs real market coverage completes to.
    "challenge", "challenges", "trend", "trends", "growth", "growing", "driver", "drivers",
    "demand", "adoption", "impact", "impacts", "region", "regions", "regional", "america", "states",
    "player", "players", "leader", "leaders", "leading", "company", "companies", "competitive",
    "competition", "landscape", "share", "forecast", "outlook", "future", "risk", "risks",
    "opportunity", "opportunities", "investment", "regulation", "regulations", "technology",
    "innovation", "ai", "cloud", "automation", "transforming", "reshaping", "evolving", "vs",
})

# Words the frame words are grouped with: naming "market/industry/sector" is the single strongest
# lexical signal that a question is about the market itself rather than its underlying subject.
_MARKET_FRAME_WORDS = frozenset({"market", "industry", "sector"})

# Buying/consumer intent: questions shaped like "what is the BEST <subject>" or "how much does
# <subject> COST" get answered by vendor round-ups and price listicles. A market-report heading
# must never be one of those (both examples came from one real harvest), so these words cost score.
_BUYING_INTENT_WORDS = frozenset({
    "best", "free", "cheap", "cheapest", "affordable", "budget", "deals", "discount",
    "near", "nearby", "download", "login", "tutorial", "beginner", "beginners",
    "pricing", "price", "worth", "salary",
})


def _looks_like_question(text: str) -> bool:
    text = text.strip()
    if not text:
        return False
    if text.endswith("?"):
        return True
    first = re.split(r"\s+", text, maxsplit=1)[0].lower()
    return first in _QUESTION_WORDS


def is_too_generic(question: str, core: str) -> bool:
    """True for the simplistic "what is <market>" shape the user explicitly asked to exclude from
    subheading candidates — still fine for FAQ, where literal phrasing is the point."""
    q = re.sub(r"[?.!]+\s*$", "", question.strip().lower())
    q = re.sub(r"\s+", " ", q)
    core_l = re.sub(r"\s+", " ", core.strip().lower())
    return bool(re.fullmatch(rf"what (is|are) (the )?{re.escape(core_l)}( market)?", q))


def score_question(question: str, core: str) -> float:
    """Higher is better: rewards specific, substantive, MARKET-SHAPED questions; penalizes the
    too-short/too-simplistic shapes the user said subheadings must avoid, buying-intent phrasings
    ("best", "cost", ...), and run-on completions.

    A question that shares NO word with the market's own name and names no market frame is about
    something else entirely — it can never head a market section, so it gets a hard low score.
    Naming a market frame word ("market"/"industry"/"sector") earns the biggest bonus: it is the
    clearest lexical marker of market-level phrasing vs service-level phrasing."""
    words = re.findall(r"[a-z0-9']+", question.lower())
    n = len(words)
    if n < 4:
        return -5.0
    score = 0.0
    if 6 <= n <= 14:
        score += 2.0
    elif n > 14:
        score -= 0.5 * (n - 14)
    core_words = set(re.findall(r"[a-z0-9']+", core.lower()))
    q_words = set(words)
    content_words = [w for w in words if w not in core_words and w not in _STOPWORDS]
    score += min(len(content_words), 4) * 0.5
    # Capped at 3 signal words: a real question names one or two concrete angles (challenges,
    # growth, regions, ...); beyond that it reads as keyword-stuffed rather than more specific,
    # and must not let an unnaturally long completion outscore a naturally-sized one.
    score += min(sum(1 for w in words if w in _SIGNAL_WORDS), 3) * 1.5
    if q_words & _MARKET_FRAME_WORDS:
        score += 3.0  # names the market itself, not just its subject
    elif not (q_words & core_words):
        return -4.0  # neither market frame nor the subject: a question about something else
    if any(w in _BUYING_INTENT_WORDS for w in words):
        score -= 2.5  # buyer's-guide phrasing, answered by vendor round-ups, not market analysis
    if is_too_generic(question, core):
        score -= 4.0
    return score


def _is_market_question(question: str, core: str) -> bool:
    """The subheading pool's ADMISSION gate, stronger than the score: a candidate must be phrased
    about the MARKET — either it names a market frame word ("market"/"industry"/"sector"), or it
    names the subject's own words PLUS a market-signal angle (growth, drivers, players, regions,
    challenges, ...). "What are the benefits of wealth management?" shares the subject words("wealth
    management") and even clears the score threshold on length alone, yet is a service explainer a
    market-analysis article cannot answer — this gate is what keeps it out."""
    words = re.findall(r"[a-z0-9']+", question.lower())
    q_words = set(words)
    if q_words & _MARKET_FRAME_WORDS:
        return True
    core_words = set(re.findall(r"[a-z0-9']+", core.lower()))
    if not (q_words & core_words):
        return False
    return any(w in _SIGNAL_WORDS for w in words)


def _norm_key(question: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", question.lower()).strip()


def dedupe(questions: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for q in questions:
        k = _norm_key(q)
        if k and k not in seen:
            seen.add(k)
            out.append(q.strip())
    return out


def rank_questions(raw_questions: list[str], core: str, subheading_limit: int = 15) -> dict:
    """{"subheading_candidates": [...]} ranked best-first, deduped, excluding the too-generic
    ("what is X market") shape the user flagged as too simplistic for a section. FAQ questions are
    no longer harvested here — the FAQ block is generated directly at write time, grounded in the
    CMI report text and the finished article itself (see pipeline.run_faq), not in autosuggest."""
    scored = sorted(
        ((q, score_question(q, core)) for q in dedupe(raw_questions)),
        key=lambda t: t[1], reverse=True,
    )
    subheading_candidates = [
        q for q, s in scored
        if s >= 2.0 and not is_too_generic(q, core) and _is_market_question(q, core)
    ][:subheading_limit]
    return {"subheading_candidates": subheading_candidates}


def _suggest(url: str, params: dict, logger: logging.Logger, label: str) -> list[str]:
    try:
        r = httpx.get(url, params=params, headers=_HEADERS, timeout=_REQUEST_TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        return [str(s) for s in (data[1] if isinstance(data, list) and len(data) > 1 else [])]
    except Exception:
        logger.debug(f"[intent] {label} suggest failed for {params!r}", exc_info=True)
        return []


def _google_suggest(query: str, logger: logging.Logger) -> list[str]:
    return _suggest(
        "https://suggestqueries.google.com/complete/search",
        {"client": "firefox", "q": query}, logger, "google",
    )


def _bing_suggest(query: str, logger: logging.Logger) -> list[str]:
    return _suggest("https://api.bing.com/osjson.aspx", {"query": query}, logger, "bing")


def harvest_questions(
    market_name: str, core: str, logger: logging.Logger = _null_logger,
) -> dict:
    """Best-effort: a network failure on every seed still returns empty pools rather than raising —
    callers (pipeline.run_harvest_questions) treat a harvested pool as an enhancement, never a
    requirement the run can fail on, the same policy as claim-backing search."""
    seeds = [p.format(core=core) for p in _SEED_PATTERNS]
    raw: list[str] = []
    for seed in seeds:
        raw += _google_suggest(seed, logger)
        time.sleep(_REQUEST_GAP_S)
        raw += _bing_suggest(seed, logger)
        time.sleep(_REQUEST_GAP_S)
    questions = [q for q in raw if _looks_like_question(q)]
    ranked = rank_questions(questions, core)
    logger.info(
        f"[intent] {market_name!r}: {len(raw)} raw suggestion(s) across {len(seeds)} seed(s), "
        f"{len(questions)} question-shaped, kept {len(ranked['subheading_candidates'])} subheading candidate(s)"
    )
    return ranked
