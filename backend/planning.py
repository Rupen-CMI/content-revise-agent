"""Market-first chain, stage 4: the PLAN.

The planner (one LLM call) decides what the article says before anyone writes it: which 2-3 most-searched questions head
sections, a one-line point per section, which few facts go where, where each keyword sits, which guest-article points open
the post, and the title. Code then enforces everything that is structure rather than judgement: section order, exactly
2-3 question headings, facts only where they belong and only once, keyword caps, snippet limits, the title's shape, the
FAQ list and every block's word budget. Every repair is logged. Nothing here retries the model.

Imports pipeline/facts/demand for shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import math
import re
from dataclasses import dataclass, field

import demand
import facts
import llm
import prompts
from pipeline import _SECONDARY_KEYWORD_SUFFIXES, _market_core
from pipeline_log import section

ORDER = ("report_context", "trends", "segmentation", "subsegment", "country_us", "key_players", "conclusion")
HEADING_QUESTIONS_MIN, HEADING_QUESTIONS_MAX = 2, 3
SECONDARY_MIN, SECONDARY_MAX = 4, 6
LONGTAIL_MIN, LONGTAIL_MAX = 2, 3
TERMS_PER_SECTION, SNIPPETS_MAX, INTRO_POINTS_MIN, INTRO_POINTS_MAX = 3, 3, 2, 3
_FACTS_MAX = {"report_context": 4, "trends": 7, "segmentation": 3, "subsegment": 5, "country_us": 6, "key_players": 6, "conclusion": 3}
_TRANSITION_MAX_WORDS = 40

# Block word budgets (plan section 3.6b): hard caps on the share of the article body, met by construction.
# The article targets 1,350 words whatever the market carries (a short section fills its gap with plain
# explanation), the intro budgets to ~18% (hard-capped 20%, growing-article exception for a long retained
# paragraph), and the transition and market block take fixed shares of that target.
MAX_WORDS = 1500
TARGET_WORDS = 1350               # §3.6b: the SOP's 1,200-1,500 lands the target at its middle
INTRO_SHARE, INTRO_CAP = 0.18, 0.20   # the intro budgets to ~18% of the target, hard-capped at 20%
TRANSITION_SHARE = 0.075          # inside the 5-10% band
MIN_OWN_INTRO_WORDS = 20          # one lead-in sentence carrying the report link, beside the retained paragraph
_SECTION_WEIGHTS = {"report_context": 110, "trends": 190, "segmentation": 70, "subsegment": 190,
                    "country_us": 150, "key_players": 150, "conclusion": 100}
_FACTS_FOR_FULL_BUDGET = {"report_context": 3, "trends": 5, "segmentation": 3, "subsegment": 4, "country_us": 4, "key_players": 4}
_MIN_FACTOR = 0.45                # even a section with no facts keeps this share of its weight (explanation, not padding)
_SECTION_BOUNDS = {"country_us": (110, 165)}  # the U.S. SOP is 100-200 words; the writer overshoots a budget a little
_THIN_MATERIAL_SHARE = 0.6        # below this much of the full weights the sections run short of their SOP shapes

# Which sections a report fact may sit in; headline figures live in report_context only so each is stated once.
_FACT_SECTIONS = {
    "size": {"report_context"}, "forecast": {"report_context"}, "cagr": {"report_context"},
    "segments": {"segmentation", "subsegment"}, "regions": {"country_us"}, "us": {"country_us"},
    "drivers": {"trends", "report_context", "subsegment", "country_us"},
    "restraints": {"trends", "subsegment", "country_us"},
    "opportunities": {"conclusion", "trends", "subsegment"},
    "players": {"key_players"},
}
_EVIDENCE_TAG_SECTIONS = {
    "trends": {"trends", "subsegment", "country_us"}, "us": {"country_us"}, "players": {"key_players", "trends"},
    "context": {"report_context", "segmentation", "subsegment", "trends", "key_players", "country_us"},
}
_SHARED_FACTS = ("fs.segments.",)   # a segment split may serve both segmentation and the sub-segment analysis

_FALLBACK_HEADINGS = {
    "report_context": "{market} Size and Growth",
    "trends": "Trends in the {market}",
    "segmentation": "{market} Segmentation",
    "subsegment": "Sub-Segment Analysis of the {market}",
    "country_us": "U.S. Analysis of the {market}",
    "key_players": "Key Players in the {market}",
    "conclusion": "Conclusion and Outlook for the {market}",
}
_TOPIC_RES = {
    "report_context": re.compile(r"size|growth|forecast|outlook|valuation|value", re.I),
    "trends": re.compile(r"trend", re.I),
    "segmentation": re.compile(r"segment", re.I),
    "subsegment": re.compile(r"sub-?\s?segment", re.I),
    "country_us": re.compile(r"\bu\.s\b|\bus\b|united states|country", re.I),
    "key_players": re.compile(r"key players?|players|participants|companies", re.I),
    "conclusion": re.compile(r"conclusion|outlook|takeaway", re.I),
}
_INTERROGATIVE_RE = re.compile(r"^\s*(what|how|which|who|whom|whose|why|where|when|is|are|was|were|do|does|did|can|could|will|would|should)\b", re.I)
_HEADING_MAX_WORDS = 12
_LABEL_TAIL_RE = re.compile(
    r"[:\-–—|]\s*(?:key\s+|latest\s+|major\s+)?(?:trends?|insights?|analysis|outlook|growth drivers?|competitive landscape|"
    r"market intelligence|forecast|overview|report|guide|explained)\s*[.!?]?\s*$", re.IGNORECASE,
)
# A non-question heading that opens with the SOP's own label words (the stage names a planner may echo) is a label, not a
# topic phrase: the SOP topics are rejected as headings so the heading names what the section covers instead.
_SOP_LABEL_HEAD_RE = re.compile(r"^\s*(?:market\s+intelligence|report\s+context)\b", re.IGNORECASE)
_TITLE_PRIMARY_START_MAX = 40
_TITLE_HARD_MIN, _TITLE_HARD_MAX, _TITLE_SOFT_MIN, _TITLE_SOFT_MAX = 30, 80, 40, 70


# --------------------------------------------------------------------------------------------------- budgets

def compute_budgets(sponsor_words: int, lengths: dict[str, str] | None = None, fact_counts: dict[str, int] | None = None) -> dict:
    """Word budgets per block (§3.6b). The article targets 1,350 words regardless of how much material
    the market carries — a section with fewer facts gets a smaller slice of the same market block and
    fills its gap with plain explanation, not a shorter article. The intro budgets to about 18% of the
    target (never below the retained paragraph plus one lead-in sentence), and the article grows inside
    the 1,500-word ceiling when a long retained paragraph would otherwise break the 20% intro cap; the
    transition takes 7.5%; the market block takes the remainder, split by each SOP section's weight
    scaled by its fact count, with the SOP minimums clamped (U.S. 110-190) and any clamp redistributed
    across the other sections.
    Returns {target, intro_total, intro_own, transition, sections, market_total, notes}."""
    lengths, fact_counts = lengths or {}, fact_counts or {}
    notes: list[str] = []
    eff = {}
    for s in ORDER:
        base = _FACTS_FOR_FULL_BUDGET.get(s)
        factor = 1.0 if base is None else min(1.0, max(_MIN_FACTOR, (fact_counts.get(s, base) + 1) / (base + 1)))
        if lengths.get(s) == "qualitative":
            factor = min(factor, 0.8)
        eff[s] = _SECTION_WEIGHTS[s] * factor
    if sum(eff.values()) < _THIN_MATERIAL_SHARE * sum(_SECTION_WEIGHTS.values()):
        notes.append("the material is thin: several market sections carry few facts; their budgets shrink "
                     "inside the same 1,350-word target, and the writing fills the gap with explanation")
    total = TARGET_WORDS
    need = sponsor_words + MIN_OWN_INTRO_WORDS
    cap_total = math.ceil(need / (INTRO_CAP - 0.01))
    if cap_total > total:
        notes.append(f"the retained paragraph ({sponsor_words} words) is long: the article grows from {round(total)} to {cap_total} words to keep the intro under {INTRO_CAP:.0%}")
        total = min(MAX_WORDS, cap_total)
    intro_total = max(need, round(INTRO_SHARE * TARGET_WORDS))
    transition = round(TRANSITION_SHARE * total)
    market = total - intro_total - transition
    if intro_total > INTRO_CAP * total:
        notes.append(f"the intro cannot fit the {INTRO_CAP:.0%} cap at {round(total)} words ({intro_total} words)")
    words = {s: market * eff[s] / sum(eff.values()) for s in ORDER}
    bound = _SECTION_BOUNDS.get("country_us")
    clamp_note = None
    if bound and words["country_us"] < bound[0]:
        words["country_us"] = bound[0]
        clamp_note = f"the country_us section is lifted to its SOP minimum of {bound[0]} words"
    elif bound and words["country_us"] > bound[1]:
        words["country_us"] = bound[1]
        clamp_note = f"the country_us section is held to its SOP maximum of {bound[1]} words"
    if clamp_note:
        rest = [s for s in ORDER if s != "country_us"]
        rest_total = market - words["country_us"]
        rest_eff = sum(eff[s] for s in rest)
        for s in rest:
            words[s] = rest_total * eff[s] / rest_eff
        notes.append(clamp_note)
    ints = {s: int(round(w)) for s, w in words.items()}
    diff = market - sum(ints.values())
    if diff:
        biggest = max(ints, key=lambda s: ints[s])
        ints[biggest] += diff
    for s in ("trends", "subsegment"):
        if ints[s] < 100:
            notes.append(f"the {s} section's material is thin ({int(fact_counts.get(s, _FACTS_FOR_FULL_BUDGET[s]))} facts): "
                         f"its {ints[s]}-word budget is under its SOP shape")
    return {
        "target": round(total), "intro_total": intro_total, "intro_own": max(0, intro_total - sponsor_words),
        "transition": transition, "sections": ints, "market_total": sum(ints.values()), "notes": notes,
    }


# --------------------------------------------------------------------------------------------------- inputs

@dataclass
class PlanInputs:
    market_name: str
    intro_angle: str
    points: list[dict]                       # [{"id": "P1", "text": ...}]
    questions: list[dict]                    # demand.run_search_demand questions, answerable already marked
    items: dict[str, dict]                   # fact id -> {id, kind, tags, text, detail, source, historical, sections}
    segments: list[dict]                     # fact sheet segment splits
    terms: list[str]
    longtails: list[dict]                    # [{"id": "L1", "keyword", "fact", "why"}]
    secondary_pool: list[tuple[str, str]] = field(default_factory=list)   # (key, default section)
    sponsor_words: int = 0
    has_us_facts: bool = False


def build_inputs(
    market_name: str, fs: dict, evidence_items: list[dict], demand_result: dict, publisher_result: dict, sponsor_words: int,
) -> PlanInputs:
    """Assemble the planner's world: every fact the plan may cite (report facts and selected news evidence) with the
    sections it may sit in, the answerable questions, the keyword pools and the guest points."""
    items: dict[str, dict] = {}
    for f in facts.flatten_fact_sheet(fs):
        sections = set(_FACT_SECTIONS.get(f["kind"], set()))
        items[f["id"]] = {**f, "tags": [], "detail": "", "sections": sections}
    for e in evidence_items:
        if e.get("group") == "article":
            continue
        sections: set[str] = set()
        for tag in e.get("tags") or []:
            sections |= _EVIDENCE_TAG_SECTIONS.get(tag, set())
        sections = sections or set(_EVIDENCE_TAG_SECTIONS["context"])
        items[e["id"]] = {
            "id": e["id"], "kind": "evidence", "tags": list(e.get("tags") or []), "text": e["fact"], "detail": e.get("detail", ""),
            "source": e.get("source", ""), "historical": False, "sections": sections,
        }
    longtails = [{"id": f"L{i}", **lt} for i, lt in enumerate(demand_result.get("long_tail") or [], 1)]
    us = any(i["kind"] in ("us",) or ("us" in i["tags"]) for i in items.values())
    return PlanInputs(
        market_name=market_name, intro_angle=publisher_result.get("intro_angle", ""), points=list(publisher_result.get("points") or []),
        questions=list(demand_result.get("questions") or []), items=items, segments=list(fs.get("segments") or []),
        terms=list(demand_result.get("terms") or []), longtails=longtails,
        secondary_pool=[(suffix, sec) for suffix, sec in _SECONDARY_KEYWORD_SUFFIXES],
        sponsor_words=sponsor_words, has_us_facts=us,
    )


def _fmt_points(inp: PlanInputs) -> str:
    return "\n".join(f"- {p['id']}: {p['text']}" for p in inp.points) or "(none)"


def _fmt_questions(inp: PlanInputs) -> str:
    rows = [f"- {q['id']} [{q['intent']}; can head {q['section']}] {q['question']}" for q in inp.questions if q.get("answerable", True)]
    return "\n".join(rows) or "(none)"


def _fmt_items(inp: PlanInputs, evidence: bool) -> str:
    rows = []
    for i in inp.items.values():
        if (i["kind"] == "evidence") != evidence:
            continue
        if evidence:
            suits = ", ".join(sorted(i["sections"] & set(ORDER))) or "any"
            line = f"- {i['id']} [suits: {suits}] {i['text']} (source: {i['source']})"
            if i["detail"]:
                line += f"\n    detail: {i['detail'][:200]}"
        else:
            note = "; historical: give its year, never present it as current" if i["historical"] else ""
            line = f"- {i['id']}: {i['text']} [{i['source']}{note}]"
        rows.append(line)
    return "\n".join(rows) or "(none)"


def build_prompt(inp: PlanInputs) -> str:
    return prompts.PLAN_USER.format(
        market_name=inp.market_name, intro_angle=inp.intro_angle or "(the title and subject of the guest article)",
        points=_fmt_points(inp), questions=_fmt_questions(inp), report_facts=_fmt_items(inp, False), evidence=_fmt_items(inp, True),
        secondary_pool="\n".join(f"- {k}: {s}" for k, s in inp.secondary_pool),
        terms=", ".join(inp.terms) or "(none)",
        longtails="\n".join(f"- {lt['id']}: {lt['keyword']} (rests on {lt['fact']})" for lt in inp.longtails) or "(none)",
    )


# ------------------------------------------------------------------------------------------------ title checks

def title_problems(title: str, market_name: str, basis: list[str], known_ids) -> tuple[list[str], list[str]]:
    """(hard problems, soft notes) for a planned title. Hard problems trigger the fallback title."""
    hard: list[str] = []
    soft: list[str] = []
    t = (title or "").strip()
    if not t:
        return ["the title is empty"], soft
    pos = t.lower().find(market_name.lower())
    if pos < 0:
        hard.append("it does not contain the full primary keyword")
    elif pos > _TITLE_PRIMARY_START_MAX:
        hard.append(f"the primary keyword starts at character {pos}, after the first {_TITLE_PRIMARY_START_MAX}")
    if re.search(r"\d", t):
        hard.append("it contains a figure (a title states the point in words; figures live in the body with their source)")
    if re.search(rf"\b(?:and|&)\s+(?:the\s+)?{re.escape(market_name)}", t, re.IGNORECASE):
        hard.append("it joins another subject to the market with 'and'")
    if _LABEL_TAIL_RE.search(t):
        hard.append("it ends in a free-standing label after a colon or dash")
    if not _TITLE_HARD_MIN <= len(t) <= _TITLE_HARD_MAX:
        hard.append(f"its length ({len(t)} characters) is far outside the range")
    elif not _TITLE_SOFT_MIN <= len(t) <= _TITLE_SOFT_MAX:
        soft.append(f"length {len(t)} characters is outside {_TITLE_SOFT_MIN}-{_TITLE_SOFT_MAX}")
    if not [b for b in basis if b in known_ids]:
        hard.append("no known fact id backs it")
    return hard, soft


def fallback_title(market_name: str, plan_sections: list[dict]) -> str:
    has_drivers = any(f.startswith("fs.drivers") or f.startswith("W") for s in plan_sections if s["sop"] == "trends" for f in s["facts"])
    return f"What Is Driving the {market_name}?" if has_drivers else f"Where the {market_name} Is Heading"


# --------------------------------------------------------------------------------------------- validate and repair

def _split_key(name: str) -> str:
    """A segment split's name without the words the fact list wraps around it ("Segmentation type", "By type")."""
    return re.sub(r"^(?:segmentation|segment|split|by)\s+", "", facts._norm(name)).strip()


_SNIPPET_STOP = frozenset({
    "about", "after", "again", "because", "being", "between", "could", "every", "from", "have", "into", "makes", "matters",
    "might", "more", "most", "much", "once", "other", "over", "should", "still", "than", "that", "their", "then", "there",
    "these", "they", "this", "those", "through", "under", "until", "very", "what", "when", "where", "which", "while", "with",
    "without", "would", "market", "markets",
})


def _vocab(text: str) -> set[str]:
    return {facts._stem(w) for w in re.findall(r"[a-z]{5,}", (text or "").lower()) if w not in _SNIPPET_STOP}


def _snippet_fits(point_text: str, section: dict, inp: PlanInputs) -> bool:
    """A guest snippet may sit in a market section only when it shares real vocabulary with that section's heading, point
    or facts (two or more content words), so it illustrates what the section says instead of changing the subject."""
    section_text = " ".join([section["heading"], section["point"]] + [inp.items[f]["text"] for f in section["facts"]])
    return len(_vocab(point_text) & _vocab(section_text)) >= 2


def heading_problem(heading: str, sop: str, market_name: str, question_texts=()) -> str | None:
    """Why a NON-question heading is not acceptable, or None when it is."""
    h = (heading or "").strip()
    if not h:
        return "it is empty"
    if h.endswith("?") or _INTERROGATIVE_RE.match(heading):
        return "it is a question"
    if h.lower() in {q.lower() for q in question_texts}:
        return "it repeats a most-searched question"
    if _SOP_LABEL_HEAD_RE.match(h):
        return "it is a section label, not a topic phrase"
    if len(h.split()) > _HEADING_MAX_WORDS:
        return "it is too long"
    if re.search(r"\d", heading):
        return "it holds a figure"
    if not _TOPIC_RES[sop].search(heading):
        return "it does not name its SOP topic"
    core_tokens = facts._core_tokens(market_name)
    if core_tokens:
        heading_stems = {facts._stem(w) for w in re.findall(r"[a-z0-9]+", heading.lower())}
        if not any(t in heading_stems for t in core_tokens):
            return "it does not name the market"
    return None


def _fallback_heading(sop: str, market_name: str) -> str:
    return _FALLBACK_HEADINGS[sop].format(market=market_name)


def _longtail_restates_heading(lt: dict, s: dict, msq_by_id: dict) -> bool:
    """True when a long-tail phrase would only restate its section's question heading: the section's heading is an MSQ
    question and every content word of the phrase (stemmed the way checks._stem stems, light glue words ignored) already
    sits in that question text. Such a phrase can only repeat the heading's own words inside the section, so it is never
    assigned there."""
    import checks
    if not lt.get("keyword") or not s.get("msq"):
        return False
    content = [w for w in re.findall(r"[a-z0-9']+", str(lt["keyword"]).lower()) if w not in checks._LONGTAIL_GLUE_WORDS]
    if not content:
        return False
    q = msq_by_id.get(s["msq"]) or {}
    qstems = {checks._stem(m.group(0)) for m in re.finditer(r"[a-z0-9']+", str(q.get("question") or "").lower())}
    return all(checks._stem(w) in qstems for w in content)


def validate_plan(raw, inp: PlanInputs, logger: logging.Logger) -> dict:
    """Turn the planner's raw answer into a plan that satisfies every structural rule. Pure: no network, no model.
    Returns {title, title_basis, intro, transition, sections, faq, budgets, repairs}."""
    repairs: list[str] = []

    def repair(msg: str) -> None:
        repairs.append(msg)
        logger.info(f"[plan] repaired: {msg}")

    raw = raw if isinstance(raw, dict) else {}
    if not isinstance(raw.get("sections"), list):
        repair("the planner returned no sections; every section starts empty")
    raw_by_sop: dict[str, dict] = {}
    for s in raw.get("sections") if isinstance(raw.get("sections"), list) else []:
        if isinstance(s, dict) and s.get("sop") in ORDER and s["sop"] not in raw_by_sop:
            raw_by_sop[s["sop"]] = s
    msq_by_id = {q["id"]: q for q in inp.questions}
    longtail_by_id = {lt["id"]: lt for lt in inp.longtails}
    point_ids = {p["id"] for p in inp.points}
    pool_keys = {k for k, _ in inp.secondary_pool}
    default_section = dict(inp.secondary_pool)

    # ---- facts: only known ids, only where they may sit, each stated in one section only
    used_facts: set[str] = set()
    sections: list[dict] = []
    for sop in ORDER:
        r = raw_by_sop.get(sop, {})
        if sop not in raw_by_sop:
            repair(f"section {sop} was missing and was added empty")
        kept: list[str] = []
        for fid in r.get("facts") if isinstance(r.get("facts"), list) else []:
            fid = str(fid).strip()
            item = inp.items.get(fid)
            if item is None:
                repair(f"{sop}: unknown fact id {fid!r} dropped")
            elif sop not in item["sections"]:
                repair(f"{sop}: fact {fid} does not belong in this section and was dropped")
            elif fid in used_facts and not fid.startswith(_SHARED_FACTS):
                repair(f"{sop}: fact {fid} is already used in another section and was dropped")
            elif fid in kept:
                continue
            elif len(kept) >= _FACTS_MAX[sop]:
                repair(f"{sop}: fact {fid} dropped (at most {_FACTS_MAX[sop]} per section)")
            else:
                kept.append(fid)
                used_facts.add(fid)
        sections.append({
            "sop": sop, "heading": " ".join(str(r.get("heading") or "").split()), "msq": None,
            "point": " ".join(str(r.get("point") or "").split()), "facts": kept,
            "secondary": None, "longtail": None, "terms": [], "snippet": None, "_raw": r,
        })
    by_sop = {s["sop"]: s for s in sections}

    # ---- sub-segments (the subsegment section): two categories from different splits of the report
    sub = by_sop["subsegment"]
    picks = []
    for it in sub["_raw"].get("sub_segments") if isinstance(sub["_raw"].get("sub_segments"), list) else []:
        if isinstance(it, dict):
            picks.append((str(it.get("split") or "").strip(), str(it.get("name") or "").strip()))
    valid: list[dict] = []
    for split, name in picks:
        seg = next((g for g in inp.segments if _split_key(g["type"]) == _split_key(split)), None)
        cat = next((i for i in seg["items"] if facts._norm(i) == facts._norm(name)), None) if seg else None
        if seg and cat and all(v["split"] != seg["type"] for v in valid):
            valid.append({"split": seg["type"], "name": cat})
    if len(valid) < 2 and len(inp.segments) >= 1:
        defaults = [{"split": g["type"], "name": g["items"][0]} for g in inp.segments if g.get("items")]
        for d in defaults:
            if len(valid) >= 2:
                break
            if all(v["split"] != d["split"] for v in valid):
                valid.append(d)
        if valid != [{"split": s, "name": n} for s, n in picks][:len(valid)]:
            repair("the two sub-segments were not valid choices from different splits; defaults from the report were used")
    sub["sub_segments"] = valid[:2]

    # ---- question headings: 2-3, each an answerable question that can head its section
    used_q: set[str] = set()
    for s in sections:
        qid = str(s["_raw"].get("msq") or "").strip()
        if not qid or qid.lower() == "null":
            continue
        q = msq_by_id.get(qid)
        if q is None:
            repair(f"{s['sop']}: unknown question id {qid!r} dropped")
        elif not q.get("answerable", True):
            repair(f"{s['sop']}: question {qid} is not answerable from the facts and was dropped")
        elif q["section"] != s["sop"]:
            repair(f"{s['sop']}: question {qid} can only head {q['section']} and was dropped")
        elif qid in used_q:
            repair(f"{s['sop']}: question {qid} is already a heading and was dropped")
        else:
            s["msq"] = qid
            used_q.add(qid)
    heading_sections = [s for s in sections if s["msq"]]
    if len(heading_sections) > HEADING_QUESTIONS_MAX:
        ranked = sorted(heading_sections, key=lambda s: (-len(s["facts"]), ORDER.index(s["sop"])))
        # The size/growth questions' only possible host is report_context (demand.INTENT_SECTION), so when the cap
        # forces a drop, that host's question is never dropped — otherwise the size section loses its heading and the
        # FAQ loses the article's headline numbers. The existing ranking among the other sections is kept.
        protected = {s["sop"] for s in heading_sections
                     if s["sop"] == "report_context"
                     and demand.INTENT_SECTION.get(msq_by_id[s["msq"]]["intent"]) == s["sop"]}
        droppable = [s for s in ranked if s["sop"] not in protected]
        for s in droppable[len(droppable) - (len(heading_sections) - HEADING_QUESTIONS_MAX):]:
            repair(f"{s['sop']}: more than {HEADING_QUESTIONS_MAX} question headings; {s['msq']} moves to the FAQ")
            used_q.discard(s["msq"])
            s["msq"] = None
    while sum(1 for s in sections if s["msq"]) < HEADING_QUESTIONS_MIN:
        candidates = [q for q in inp.questions
                      if q.get("answerable", True) and q["id"] not in used_q and not by_sop[q["section"]]["msq"]]
        if not candidates:
            repair(f"fewer than {HEADING_QUESTIONS_MIN} answerable questions can head a section; the article has "
                   f"{sum(1 for s in sections if s['msq'])}")
            break
        best = max(candidates, key=lambda q: (len(by_sop[q["section"]]["facts"]), -demand.INTENTS.index(q["intent"])))
        by_sop[best["section"]]["msq"] = best["id"]
        used_q.add(best["id"])
        repair(f"{best['section']}: fewer than {HEADING_QUESTIONS_MIN} question headings; {best['id']} was promoted to a heading")

    # ---- headings
    question_texts = {q["question"].lower() for q in inp.questions}
    seen_headings: set[str] = set()
    for s in sections:
        if s["msq"]:
            s["heading"] = msq_by_id[s["msq"]]["question"]
        else:
            h = s["heading"]
            why = heading_problem(h, s["sop"], inp.market_name, question_texts)
            bad = bool(why) or h.lower() in seen_headings
            if bad:
                if h:
                    repair(f"{s['sop']}: heading {h!r} replaced ({why or 'a duplicate'}) with the plain topic heading")
                s["heading"] = _fallback_heading(s["sop"], inp.market_name)
        seen_headings.add(s["heading"].lower())
    for s in sections:            # last resort: an interrogative heading without the question mark becomes a question
        if s["msq"] or not _INTERROGATIVE_RE.match(s["heading"]) or s["heading"].rstrip().endswith("?"):
            continue
        old = s["heading"]
        s["heading"] = s["heading"].rstrip(" .") + "?"
        repair(f"{s['sop']}: heading {old!r} reads as a question but carries no question mark and was rewritten as {s['heading']!r}")

    # ---- keywords
    used_secondary: list[str] = []
    for s in sections:
        key = str(s["_raw"].get("secondary") or "").strip()
        if key and key.lower() != "null":
            if key not in pool_keys:
                repair(f"{s['sop']}: unknown secondary keyword {key!r} dropped")
            elif key in used_secondary:
                repair(f"{s['sop']}: secondary keyword {key!r} is already used and was dropped")
            elif len(used_secondary) >= SECONDARY_MAX:
                repair(f"{s['sop']}: secondary keyword {key!r} dropped (at most {SECONDARY_MAX})")
            else:
                s["secondary"] = key
                used_secondary.append(key)
    for s in sections:                                    # fill up to the minimum from each keyword's own section
        if len(used_secondary) >= SECONDARY_MIN:
            break
        if s["secondary"] is None:
            fit = next((k for k, d in inp.secondary_pool if d == s["sop"] and k not in used_secondary), None)
            if fit and (s["facts"] or s["sop"] in ("report_context", "conclusion")):
                s["secondary"] = fit
                used_secondary.append(fit)
                repair(f"{s['sop']}: secondary keyword {fit!r} was added to reach {SECONDARY_MIN}")

    used_lt: list[str] = []
    for s in sections:
        lid = str(s["_raw"].get("longtail") or "").strip()
        if lid and lid.lower() != "null":
            if lid not in longtail_by_id:
                repair(f"{s['sop']}: unknown long-tail id {lid!r} dropped")
            elif lid in used_lt:
                repair(f"{s['sop']}: long-tail {lid} is already used and was dropped")
            elif len(used_lt) >= LONGTAIL_MAX:
                repair(f"{s['sop']}: long-tail {lid} dropped (at most {LONGTAIL_MAX})")
            elif s["sop"] == "segmentation":
                repair(f"segmentation: long-tail {lid} dropped (a list leaves no room for a natural sentence)")
            elif longtail_by_id[lid]["fact"] not in s["facts"]:
                repair(f"{s['sop']}: long-tail {lid} rests on {longtail_by_id[lid]['fact']}, which is not in this section, and was dropped")
            elif _longtail_restates_heading(longtail_by_id[lid], s, msq_by_id):
                repair(f"{s['sop']}: long-tail {lid} only restates the question heading and was dropped")
            else:
                s["longtail"] = lid
                used_lt.append(lid)
    if len(used_lt) < LONGTAIL_MIN:
        for lt in inp.longtails:
            if len(used_lt) >= LONGTAIL_MIN:
                break
            host = next((s for s in sections if s["longtail"] is None and s["sop"] != "segmentation"
                         and lt["fact"] in s["facts"] and not _longtail_restates_heading(lt, s, msq_by_id)), None)
            if host and lt["id"] not in used_lt:
                host["longtail"] = lt["id"]
                used_lt.append(lt["id"])
                repair(f"{host['sop']}: long-tail {lt['id']} was added because its fact sits in this section")
        if len(used_lt) < LONGTAIL_MIN:
            repair(f"only {len(used_lt)} long-tail search(es) could be placed (the minimum is {LONGTAIL_MIN})")

    used_terms: set[str] = set()
    pool_terms = {t.lower(): t for t in inp.terms}
    for s in sections:
        for t in s["_raw"].get("terms") if isinstance(s["_raw"].get("terms"), list) else []:
            t = str(t).strip().lower()
            if t in pool_terms and t not in used_terms and len(s["terms"]) < TERMS_PER_SECTION:
                s["terms"].append(t)
                used_terms.add(t)

    # ---- guest article points: the opening, then at most a few later snippets
    intro_ids = [str(p).strip() for p in (raw.get("intro") or {}).get("points", []) if str(p).strip() in point_ids] if isinstance(raw.get("intro"), dict) else []
    intro_ids = list(dict.fromkeys(intro_ids))[:INTRO_POINTS_MAX]
    for p in inp.points:
        if len(intro_ids) >= INTRO_POINTS_MIN:
            break
        if p["id"] not in intro_ids:
            intro_ids.append(p["id"])
            repair(f"the opening had fewer than {INTRO_POINTS_MIN} guest points; {p['id']} was added")
    snippets = 0
    for s in sections:
        pid = str(s["_raw"].get("snippet") or "").strip()
        if not pid or pid.lower() == "null":
            continue
        if s["sop"] == "conclusion":
            repair("conclusion: a guest snippet is not allowed here and was dropped")
        elif pid not in point_ids:
            repair(f"{s['sop']}: unknown guest point {pid!r} dropped")
        elif pid in intro_ids:
            repair(f"{s['sop']}: guest point {pid} already opens the article and was dropped as a snippet")
        elif not _snippet_fits(next(p["text"] for p in inp.points if p["id"] == pid), s, inp):
            repair(f"{s['sop']}: guest point {pid} shares too little with this section to illustrate it and was dropped as a snippet")
        elif snippets >= SNIPPETS_MAX:
            repair(f"{s['sop']}: guest snippet {pid} dropped (at most {SNIPPETS_MAX})")
        else:
            s["snippet"] = pid
            snippets += 1

    transition_raw = raw.get("transition") if isinstance(raw.get("transition"), dict) else {}
    transition_point = " ".join(str(transition_raw.get("point") or "").split())
    if len(transition_point.split()) > _TRANSITION_MAX_WORDS:
        transition_point = " ".join(transition_point.split()[:_TRANSITION_MAX_WORDS]).rstrip(",;:- ")
        repair("the transition point was too long and was shortened")

    # ---- lengths and budgets
    for s in sections:
        if s["sop"] == "conclusion":
            s["length"] = "normal"        # it pulls the post together from what is already there, so it needs no facts of its own
        elif not s["facts"] or (s["sop"] == "key_players" and all(fid == "fs.players" for fid in s["facts"])):
            s["length"] = "short"     # no facts, or only a list of names with no detail to say about any of them
        elif s["sop"] == "country_us" and not any(
                inp.items[f]["kind"] in ("us", "regions") or "us" in inp.items[f]["tags"] for f in s["facts"]):
            s["length"] = "qualitative"
        else:
            s["length"] = "normal"
        if s["sop"] == "country_us" and not inp.has_us_facts and s["length"] == "normal":
            s["length"] = "qualitative"
    budgets = compute_budgets(
        inp.sponsor_words, {s["sop"]: s["length"] for s in sections},
        {s["sop"]: len([f for f in s["facts"] if f != "fs.players"]) + (1 if "fs.players" in s["facts"] else 0) for s in sections})
    for note in budgets["notes"]:
        repair(note)
    for s in sections:
        s["words"] = budgets["sections"][s["sop"]]
        s.pop("_raw")

    # ---- title
    basis = [str(b).strip() for b in raw.get("title_basis") if str(b).strip()] if isinstance(raw.get("title_basis"), list) else []
    title = " ".join(str(raw.get("title") or "").split())
    hard, soft = title_problems(title, inp.market_name, basis, set(inp.items))
    if hard:
        fb = fallback_title(inp.market_name, sections)
        repair(f"title {title!r} replaced ({'; '.join(hard)}) with {fb!r}")
        title, basis = fb, []
    for note in soft:
        logger.info(f"[plan] title note: {note}")

    faq = [q["id"] for q in inp.questions if q.get("answerable", True) and q["id"] not in used_q]
    plan = {
        "title": title, "title_basis": [b for b in basis if b in inp.items],
        "intro": {"points": intro_ids, "words": budgets["intro_total"], "own_words": budgets["intro_own"]},
        "transition": {"point": transition_point, "words": budgets["transition"]},
        "sections": sections, "faq": faq, "budgets": budgets, "repairs": repairs,
    }
    logger.info(
        f"[plan] {len(repairs)} repair(s); {sum(1 for s in sections if s['msq'])} question heading(s); "
        f"{len(used_secondary)} secondary, {len(used_lt)} long-tail, {snippets} snippet(s); {len(faq)} FAQ question(s)"
    )
    return plan


def run_plan(inp: PlanInputs, logger: logging.Logger) -> dict:
    """CALL: plan the article, then enforce the structural rules in code. A model failure propagates (the article cannot be
    written without a plan); an unusable answer falls back to an empty-but-valid plan, which the log makes obvious."""
    section(logger, "CALL: PLAN")
    raw = llm.chat_json(
        prompts.PLAN_SYSTEM, build_prompt(inp), temperature=0.3, call_name="plan", logger=logger, max_tokens=4500,
    )
    return validate_plan(raw, inp, logger)
