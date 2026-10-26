"""Market-first chain, stage 6: FAQ.

The FAQ is the article's Most Searched Questions: the plan's `faq` list of MSQs, verbatim, each answered by one LLM
call from the fact sheet and the selected evidence (the same facts the writer had), never from the finished draft. A
question the sources cannot answer is dropped. Code then runs the deterministic backstops: sentences carrying a
figure, year or company detail that no fact assigned to that question supports (and the draft does not state) are
removed, so no FAQ entry can contradict the article it sits under.

The old FAQ's page-FAQ dedupe (`_drop_page_faq_repeats`, FAQ_USER rule 3b), the `used_questions` rule, the house-style
shapes and the publisher FAQ group stay in pipeline.py (disconnected, behind the old chain). Imports pipeline for
shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import re
from datetime import date

import facts
import llm
import planning
import prompts
from pipeline import _plain
from pipeline_log import section

# Which fact kinds / evidence tags can answer each MSQ intent (the mirror of demand._answerable_by, at item level,
# from the same intent table the plan §3.3 fixes).
_INTENT_FILTERS = {
    "size": ({"size"}, set()),
    "growth": ({"forecast", "cagr"}, set()),
    "trends": ({"drivers"}, {"trends"}),
    "drivers": ({"drivers"}, set()),
    "challenges": ({"restraints"}, set()),
    "technology_impact": (set(), {"trends"}),
    "segments": ({"segments"}, set()),
    "region": ({"regions"}, {"us"}),
    "us": ({"us"}, {"us"}),
    "players": ({"players"}, {"players"}),
    "outlook": ({"forecast", "cagr", "opportunities"}, set()),
    "opportunities": ({"opportunities"}, set()),
}
_FACT_LINE_CHARS = 200
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

_GENERIC_CAPS = frozenset({
    "the", "market", "global", "report", "analyst", "segment", "segments", "industry", "united", "north", "america",
    "asia", "pacific", "europe", "today", "this", "these", "scale",
})


def sources_for_question(q: dict, inp: planning.PlanInputs) -> list[dict]:
    """The fact items (report and evidence) whose kinds/tags can answer this question's intent."""
    kinds, tags = _INTENT_FILTERS.get(q.get("intent"), (set(), set()))
    out = []
    for i in inp.items.values():
        if i["kind"] == "evidence":
            if tags & set(i["tags"] or []):
                out.append(i)
        elif i["kind"] in kinds:
            out.append(i)
    return out


def _question_block(n: int, q: dict, sources: list[dict]) -> str:
    rows = [f"QUESTION {n} ({q['id']}): {q['question']}", "Facts this answer may use:"]
    lines = []
    for s in sources:
        note = " [historical: give its year, never present it as current]" if s["historical"] else ""
        if s["kind"] == "evidence":
            lines.append(f"- {s['text']} (source: {s['source']}){note}")
            if s.get("detail"):
                lines.append(f"    detail: {s['detail'][:_FACT_LINE_CHARS]}")
        elif s["source"] not in ("CMI report", ""):
            lines.append(f"- {s['text']} (source: {s['source']}, NOT CMI: name this source in the sentence){note}")
        else:
            lines.append(f"- {s['text']}{note}")
    rows.append("\n".join(lines) if lines else "- (none — drop this question)")
    return "\n".join(rows)


def build_prompt(plan: dict, inp: planning.PlanInputs, today: str | None = None) -> str:
    """The FAQ call's user prompt: the plan's faq questions verbatim, each with its own facts."""
    by_id = {q["id"]: q for q in inp.questions}
    blocks, n = [], 0
    for qid in plan.get("faq") or []:
        q = by_id.get(qid)
        if not q:
            continue
        n += 1
        blocks.append(_question_block(n, q, sources_for_question(q, inp)))
    return prompts.FAQ_MSQ_USER.format(
        market_name=inp.market_name, today=today or date.today().isoformat(),
        question_blocks="\n\n".join(blocks) or "(no faq questions were planned)",
    )


# ---------------------------------------------------------------------------------------------- code backstops

def _numbers(text: str) -> set[str]:
    """The same normalized tokens facts._numbers produces ("11.2", "37.94"), so a percent figure in an
    answer is compared against its fact's figure without the %-sign getting in the way ("11.2%" and
    "11.2" are one figure; the old pattern kept "%" and dropped supported percent figures wholesale)."""
    return facts._numbers(text)


def _company_names(text: str) -> set[str]:
    """Capitalized words that look like a company or product name; a generic word alone proves nothing."""
    return {w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", text or "") if w.lower() not in _GENERIC_CAPS}


def strip_unsupported(q: dict, sources: list[dict], answer: str, draft_text: str, logger: logging.Logger) -> str:
    """Remove the sentences of `answer` that hold a figure, year or company detail which neither a fact assigned to
    this question supports nor the draft states (the FAQ never contradicts the article it sits under). A sentence left
    empty is dropped; an answer left empty is dropped by the caller."""
    allowed_numbers = facts._numbers(draft_text)
    for s in sources:
        allowed_numbers |= facts._numbers(s["text"]) | facts._numbers(s.get("detail", ""))
    allowed_numbers |= _numbers(q["question"])
    allowed_names = _company_names(draft_text) | set(re.findall(r"[A-Za-z]+", q["question"]))
    for s in sources:
        allowed_names |= _company_names(s["text"])
    kept = []
    for sentence in _SENTENCE_SPLIT_RE.split(answer or ""):
        if not sentence.strip():
            continue
        bad_numbers = [n for n in _numbers(sentence) if n not in allowed_numbers]
        if bad_numbers:
            logger.info(f"[faq] {q['id']}: sentence dropped, a figure no fact supports ({', '.join(bad_numbers)}): "
                        f"{sentence.strip()[:110]}")
            continue
        names = _company_names(sentence) - allowed_names
        if names:
            logger.info(f"[faq] {q['id']}: sentence dropped, it names {', '.join(sorted(names))} which no fact for "
                        "this question names")
            continue
        kept.append(sentence.strip())
    return " ".join(kept)


def normalize_faq(raw, plan: dict, inp: planning.PlanInputs, draft_text: str, logger: logging.Logger) -> list[dict]:
    """The plan's faq questions, verbatim, in plan order. A question that is unanswerable, or whose sources cannot fill
    its answer, is dropped; every kept answer has its unsupported sentences stripped."""
    by_id = {q["id"]: q for q in inp.questions}
    out = []
    for qid in plan.get("faq") or []:
        q = by_id.get(qid)
        if q is None:
            logger.info(f"[faq] unknown planned question {qid!r}")
            continue
        if not q.get("answerable", False):
            logger.info(f"[faq] {qid} is unanswerable and was dropped: {q['question']!r}")
            continue
        answer = next(
            (str(a.get("answer") or "").strip() for a in (raw or [])
             if isinstance(a, dict) and re.sub(r"\s+", " ", str(a.get("question") or "")).strip() == q["question"]),
            "",
        )
        if not answer:
            logger.info(f"[faq] {qid} came back with no answer and was dropped: {q['question']!r}")
            continue
        answer = strip_unsupported(q, sources_for_question(q, inp), _plain(answer), draft_text, logger)
        import writer   # lazy: writer imports pipeline-adjacent modules, so a module-level import here would cycle
        fixed = writer.fix_pointing_determiners([{"sop": [], "paragraphs": [answer]}], logging.getLogger("faq"))
        if fixed:
            answer = fixed[0]["paragraphs"][0]
        joined = writer.join_bare_pointers([{"sop": [], "paragraphs": [answer]}], logging.getLogger("faq"))
        if joined:
            answer = joined[0]["paragraphs"][0]
        plain = writer.strip_reader_filler([{"sop": [], "paragraphs": [answer]}], logging.getLogger("faq"))
        if plain:
            answer = plain[0]["paragraphs"][0]
        if not answer:
            logger.info(f"[faq] {qid}: nothing left after stripping unsupported sentences; question dropped")
            continue
        out.append({"question": q["question"], "answer": answer, "id": qid, "intent": q["intent"]})
    return out


def run_faq(plan: dict, inp: planning.PlanInputs, sections: list[dict], logger: logging.Logger | None = None,
            today: str | None = None) -> list[dict]:
    """CALL: answer the plan's FAQ questions. Best-effort like the old FAQ: a failure means no FAQ block, logged, never
    fatal. Returns [{question, answer, id, intent}], in plan order."""
    logger = logger if logger is not None else logging.getLogger("faq")
    section(logger, "CALL: FAQ")
    draft_text = " ".join(p.replace(prompts.SPONSOR_TOKEN, " ") for s in sections for p in s["paragraphs"])
    try:
        raw = llm.chat_json(
            prompts.FAQ_MSQ_SYSTEM, build_prompt(plan, inp, today),
            temperature=0.3, call_name="faq", logger=logger, max_tokens=4000,
        )
        entries = raw.get("faq") if isinstance(raw, dict) else None
    except llm.LLMError:
        logger.warning("[faq] the FAQ call failed; the draft will carry no FAQ block", exc_info=True)
        return []
    faq = normalize_faq(entries, plan, inp, draft_text, logger)
    logger.info(f"[faq] {len(faq)} faq entry(ies) from {len(plan.get('faq') or [])} planned question(s)")
    return faq
