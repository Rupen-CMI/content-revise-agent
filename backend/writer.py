"""Market-first chain, stage 5: WRITE.

One LLM call turns the validated plan into the whole post: the opening (a guest-article example, the retained sponsor
paragraph and the CMI report link), a short transition, and the seven market sections. The writer is handed each
section's exact heading, its one-line point, its word budget, its few facts (already expanded to text with their source),
its keyword assignments and, at most, one guest example. It may state nothing the plan did not assign.

This module only builds the prompt and assembles the result in the section shape the rest of the app already reads
({"heading", "sop", "paragraphs"}): the opening is untagged, the transition is tagged "transition", every market section
carries its SOP tag. Deterministic checks (block shares, keyword caps, figures traced to facts) live in checks.py and only
log problems, never retry.

Imports pipeline/planning for shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import re
from datetime import date

import llm
import planning
import prompts
from docx_io import Paragraph
from pipeline import (
    _clean_sections, _evidence_used_ids, _force_sponsor_placeholder, _link_placement_problems, _market_core, _plain,
    _sponsor_location_problem, _sponsor_problems, _sponsor_rule, sponsor_mode,
)
from pipeline_log import section
import reports

LINK_WORDS = 100        # the SOP wants the report link inside the first 100 words (200 is the hard limit the check uses)
_DETAIL_CHARS = 300
_SHAPES = {
    "report_context": 'One paragraph. It states the market\'s size and where it is heading only as far as the listed facts do.',
    "trends": 'One paragraph string: a single lead-in sentence, then exactly three lines (never two, never four) that each start with "\\n- ", one distinct trend per line (what is changing and what it does to the market, resting on a listed fact where one is given). The lead-in and the three lines are the whole paragraph.',
    "segmentation": 'One paragraph string: a lead-in sentence that uses the word "segmentation" or "segments" and says how many splits the report uses, in your own words, then one line starting with "\\n- " per split. Each line names the split and its categories, with your own connective words between the categories and one short phrase of yours about what the categories describe for the market — never the report\'s own list wording. Nothing else.',
    "subsegment": 'Two entries in "paragraphs", each an object {{"segment": "<split>", "sub_segment": "<category>", "paragraph": "<text>"}}, for the two sub-segments named below, in that order. Each paragraph looks at its one sub-segment: what drives its adoption, where it is used and what it adds to the market, using only the listed facts; where the facts say little, it says little. The section names the market\'s full name at least once, and every paragraph ties its sub-segment back to the market.',
    "country_us": 'Plain prose in one or two short paragraphs, or a lead-in sentence followed by "\\n- " lines when each line rests on its own listed fact. It covers where the market is strongest and, where facts allow, the United States, in 100 to 200 words in all; fewer than 100 words is a miss, so carry the region picture with the listed facts plus your own plain sentences about what that picture means for buyers.',
    "key_players": 'Two entries in "paragraphs". First: a short plain passage on how crowded or concentrated the field is. Second: the companies. A company with a listed fact gets one line "- <Company>: <what the fact says>" under a short lead-in clause; companies with no fact are named together in one plain sentence and are never given a detail the facts do not supply.',
    "key_players_names_only": 'Two entries in "paragraphs", both plain prose with no bullets. First: a short passage on how crowded or concentrated the field is, using only what the listed facts say. Second: one or two sentences naming the companies together; never give a company a detail the facts do not supply.',
    "conclusion": 'One paragraph that pulls the post together using only points already made. Its last sentence is the CMI analyst\'s view.',
}


# ------------------------------------------------------------------------------------------------- prompt

def _fact_text(item: dict) -> str:
    note = " [historical: state its year, never as current]" if item.get("historical") else ""
    if item["kind"] == "evidence":
        line = f"- [{item['id']}] {item['text']} (source: {item['source']}){note}"
        if item.get("detail"):
            line += f"\n    detail: {item['detail'][:_DETAIL_CHARS]}"
        return line
    if item["source"] not in ("CMI report", ""):
        return (f"- [{item['id']}] {item['text']} (source: {item['source']}, NOT CMI: each of this figure's sentences "
                f"repeats the name {item['source']}, and never with \u201cthe same source\u201d or \u201cthat source\u201d; "
                f"never credit the figure to CMI){note}")
    return f"- [{item['id']}] {item['text']} (CMI report){note}"


def _keyword_lines(s: dict, inp: planning.PlanInputs) -> str:
    lines = []
    core = _market_core(inp.market_name)
    if s["secondary"]:
        lines.append(f'  - Secondary keyword: "{s["secondary"]}". The words "{s["secondary"]}", "{core}" and "market" must all appear together in one sentence of this section. Those keyword words belong to this section alone: they sit in exactly one paragraph here (never spread over two), and another section\'s keywords are expressed in different words when they come up in your prose.')
    if s["longtail"]:
        lt = next(x for x in inp.longtails if x["id"] == s["longtail"])
        lines.append(f'  - Long-tail search: "{lt["keyword"]}" (rests on {lt["fact"]}).')
    if s["terms"]:
        lines.append("  - Related terms (optional): " + ", ".join(s["terms"]) + ".")
    return "\n".join(lines) or "  - No keyword is assigned to this section beyond the primary keyword."


def _section_block(i: int, s: dict, inp: planning.PlanInputs, points: dict[str, str]) -> str:
    msq = next((q for q in inp.questions if q["id"] == s["msq"]), None)
    rows = [f'### {i}. {s["sop"]}  |  heading: "{s["heading"]}"  |  about {s["words"]} words']
    rows.append(f'Purpose (what the reader learns here): {s["point"] or "(the section\'s ordinary job)"}')
    if msq:
        rows.append("The heading is a question people really search. Answer it directly in the first one or two sentences (about 40 to 60 words), in plain words, then continue with the section.")
    if s["length"] == "short":
        rows.append("Keep this section short and plain; it is fine to land under the budget.")
    elif s["length"] == "qualitative":
        rows.append("Write about the leading regions only. Do not discuss the United States in particular, and keep to the lower end of the budget.")
    facts_rows = [_fact_text(inp.items[f]) for f in s["facts"]]
    rows.append("Facts to use:\n" + ("\n".join(facts_rows) if facts_rows else "- (none)"))
    if s["sop"] == "subsegment" and s.get("sub_segments"):
        rows.append("Sub-segments to cover: " + "; ".join(f'{x["name"]} (from the split "{x["split"]}")' for x in s["sub_segments"]) + ".")
    rows.append("Keywords:\n" + _keyword_lines(s, inp))
    if s["snippet"]:
        rows.append(f'Guest example (optional, at most one sentence, only if it makes the point clearer): "{points[s["snippet"]]}"')
    names_only = s["sop"] == "key_players" and not any(inp.items[f]["kind"] == "evidence" for f in s["facts"])
    rows.append("SHAPE: " + _SHAPES["key_players_names_only" if names_only else s["sop"]])
    return "\n".join(rows)


def build_write_prompt(plan: dict, inp: planning.PlanInputs, sponsor: Paragraph, report: reports.Report, today: str | None = None) -> str:
    points = {p["id"]: p["text"] for p in inp.points}
    blocks = [_section_block(i, s, inp, points) for i, s in enumerate(plan["sections"], 1)]
    mode = sponsor_mode(sponsor.text)
    total = plan["budgets"]["target"]
    # The transition band is 5-10% of the body (checks.block_share_problems). A transition that slides
    # under the band floor brings the whole draft down with it, so the prompt carries a floor in words:
    # about three quarters of the planned transition budget, and never below 65.
    transition_words = int(plan["transition"]["words"])
    transition_floor = max(65, int(transition_words * 0.75))
    if mode == "phrase":
        opening_shape = (
            f"a lead-in paragraph that sets the scene with the subject and names the market through the report link, inside its first {LINK_WORDS} words; "
            "then a second paragraph of your own in which the placeholder sits INSIDE one of your sentences, with your own words before and after it "
            "(the retained text is a short linked phrase, not a paragraph, so it never stands alone on a line). Nothing else is needed."
        )
        # The output template must MATCH that shape: showing the placeholder as a paragraph of its own here made the
        # model return exactly that, which breaks the phrase-mode placement rule.
        intro_shape = f'["<the lead-in paragraph with the report link>", "<a paragraph of your own that embeds {prompts.SPONSOR_TOKEN} inside one of its sentences>"]'
    else:
        opening_shape = (
            f"a lead-in paragraph that sets the scene with the subject and names the market through the report link, inside its first {LINK_WORDS} words; "
            "then the retained paragraph's slot; then, only if the budget has room, one short closing paragraph."
        )
        intro_shape = f'["<the lead-in paragraph with the report link>", "{prompts.SPONSOR_TOKEN}", "<an optional short closing paragraph>"]'
    return prompts.WRITE_USER.format(
        title=plan["title"], market_name=report.name, today=today or date.today().isoformat(),
        intro_own=plan["intro"]["own_words"], intro_angle=inp.intro_angle or "(the guest article's title)",
        intro_points="\n".join(f"  - {points[p]}" for p in plan["intro"]["points"]) or "  - (none)",
        sponsor_rule=_sponsor_rule(mode, sponsor.text), link_token=f"[[CMI_LINK:{report.name}]]", opening_shape=opening_shape,
        intro_shape=intro_shape,
        transition_words=transition_words,
        transition_floor=transition_floor,
        transition_point=plan["transition"]["point"] or "explain why the guest article's subject belongs to this wider market and what the reader will learn next",
        sections="\n\n".join(blocks), total_words=total, sponsor_token=prompts.SPONSOR_TOKEN,
    )


# ------------------------------------------------------------------------------------------------- code backstops

# Sentences that talk about the writing process or about what the sources lack. A prompt rule against them is not reliable,
# so they are removed in code (the sentence goes; nothing is rewritten).
_META_RE = re.compile(
    r"\b(?:listed|given|supplied|provided|available)\s+(?:facts?|information|details?|data)\b"
    r"|\bthe\s+facts?\s+(?:say|carry|show|give|do not|does not|don't)\b"
    r"|\bthe\s+(?:plan|brief)\b"
    r"|\b(?:report|source|sources)\s+(?:does|do)\s+not\s+(?:put|break|give|say|state|list|name)\b"
    r"|\bnot\s+(?:broken\s+out|listed|stated|given|provided)\s+(?:in|by)\b"
    r"|\b(?:says|say)\s+little\s+more\b|\bstays?\s+(?:broad|at\s+the\s+regional\s+level)\b"
    r"|\bno\s+(?:further|more|specific)\s+(?:detail|data|figures?)\s+(?:is|are|was|were)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def strip_meta_sentences(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """Remove sentences that describe the facts or the plan instead of the market. Works line by line so bullet lists keep
    their shape; a line left empty is dropped, and a paragraph left empty is dropped with a warning."""
    for s in sections:
        paragraphs = []
        for p in s["paragraphs"]:
            lines = []
            for line in p.split("\n"):
                kept = [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                good = [x for x in kept if not _META_RE.search(x)]
                if len(good) != len(kept):
                    logger.info(f"[write] removed {len(kept) - len(good)} process-language sentence(s) from {s['sop'] or 'the opening'}: "
                                + " | ".join(x[:110] for x in kept if _META_RE.search(x)))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return [s for s in sections if s["paragraphs"]]


def strip_untraced_figures(sections: list[dict], inp: planning.PlanInputs, sponsor_text: str, logger: logging.Logger) -> list[dict]:
    """An invented or rounded figure ("near 7%" from a 7.3% CAGR) must never print. The write prompt bans
    rounding and the trace check flags a number that no fact-sheet entry, evidence item, guest point, the
    sponsor text or the market name carries, but a prompt rule alone did not hold in every replay — so the
    sentence goes, in code, exactly like the FAQ answers' own strip_unsupported (§3.8) and like
    strip_meta_sentences above: the whole sentence is removed, nothing is rewritten."""
    if inp is None:
        return sections
    import checks
    allowed: set[str] = set()
    for i in inp.items.values():
        allowed |= checks._fact_numbers(i["text"]) | checks._fact_numbers(i.get("detail", ""))
        allowed |= checks._fact_numbers(i.get("source", ""))
        for pt in inp.points:
            allowed |= checks._fact_numbers(pt["text"])
        allowed |= checks._fact_numbers(sponsor_text) | checks._fact_numbers(inp.market_name)
    for s in sections:
        label = s["sop"][0] if s.get("sop") else "the opening"
        paragraphs: list[str] = []
        for p in s["paragraphs"]:
            lines: list[str] = []
            for line in p.split("\n"):
                kept = [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                good, gone = [], []
                for sent in kept:
                    text = checks._sponsor_replacement(sent, sponsor_text) if sponsor_text else sent
                    bad = sorted(n for n in checks._fact_numbers(text)
                                 if n not in allowed and not re.fullmatch(r"(?:19|20)\d\d", n))
                    if bad:
                        gone.append(sent)
                    else:
                        good.append(sent)
                if gone:
                    logger.info(f"[write] removed {len(gone)} untraced-figure sentence(s) from {label}: "
                                + " | ".join(x[:110] for x in gone))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return [s for s in sections if s["paragraphs"]]


def strip_repeat_figures(sections: list[dict], inp: planning.PlanInputs, logger: logging.Logger) -> list[dict]:
    """The write prompt says a figure is stated in one section only and later sections refer back in words;
    four replay rounds still returned conclusions that reprint a non-CMI figure unnamed — which is exactly
    what the attribution check flags (§3.2). Those sentences are removed in code, like strip_meta_sentences:
    a sentence that repeats a figure already printed in an earlier sentence and does not name its source
    goes; a sentence that carries the source's name stays. Years never count; a figure's repeat only covers
    when the sentence names the figure's own source."""
    if inp is None:
        return sections
    import checks
    owned: dict[str, str] = {}      # figure -> its outside source, from the fact sheet and evidence items
    for i in inp.items.values():
        if i["kind"] not in ("size", "forecast", "cagr", "evidence") or i.get("source") in ("", "CMI report"):
            continue
        src = i.get("source") or ""
        for n in checks._fact_numbers(i["text"]) | checks._fact_numbers(i.get("detail", "")):
            if not re.fullmatch(r"(?:19|20)\d\d", n):
                owned.setdefault(n, src)
    if not owned:
        return sections
    printed: set[str] = set()       # figures printed anywhere in the market block so far
    for s in sections:
        if not s.get("sop") or s["sop"][0] not in planning.ORDER:
            continue    # the opening and the transition state no market figures (write prompt rule)
        label = s["sop"][0]
        paragraphs: list[str] = []
        for p in s["paragraphs"]:
            lines: list[str] = []
            for line in p.split("\n"):
                kept = [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                good, gone = [], []
                for sent in kept:
                    strong = [n for n in checks._fact_numbers(sent) if not re.fullmatch(r"(?:19|20)\d\d", n)]
                    repeats = sorted(n for n in strong if n in printed)
                    if repeats:
                        low = sent.lower()
                        srcs = {owned[n] for n in repeats}
                        if not all(checks._names_source(low, src) for src in srcs):
                            gone.append(sent)
                            continue
                    good.append(sent)
                    printed.update(n for n in strong if n in owned)
                if gone:
                    logged = "; ".join(f"{n} ({owned[n]} printed earlier)" for n in
                                       sorted({n for sent in gone for n in checks._fact_numbers(sent)
                                               if n in owned and not re.fullmatch(r"(?:19|20)\d\d", n)}))
                    logger.info(f"[write] removed {len(gone)} repeat-figure sentence(s) from {label} — {logged}: "
                                + " | ".join(x[:110] for x in gone))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return [s for s in sections if s["paragraphs"]]



def evidence_citations(sections: list[dict], inp: planning.PlanInputs) -> list[dict]:
    """Which news evidence each market paragraph used, found in code with the pipeline's own usage detector."""
    items =[{"id": i["id"], "fact": i["text"]} for i in inp.items.values() if i["kind"] == "evidence"]
    out = []
    for s in sections:
        if not s["sop"] or s["sop"][0] not in planning.ORDER:
            continue
        for p in s["paragraphs"]:
            for eid in sorted(_evidence_used_ids(p, items)):
                out.append({"id": eid, "paragraph": p})
    return out


# ------------------------------------------------------------------------------------------------- assembly

def assemble_sections(written: dict, plan: dict, logger: logging.Logger) -> list[dict]:
    """The model's answer as the app's section list: intro (untagged), transition (tagged "transition"), then the market
    sections in the plan's order with the plan's exact headings. A section the model left out is logged and skipped."""
    intro = _clean_sections([{"heading": "", "sop": [], "paragraphs": (written.get("intro") or {}).get("paragraphs", [])}], force_untagged=True)
    trans_raw = [{"heading": "", "sop": ["transition"], "paragraphs": (written.get("transition") or {}).get("paragraphs", [])}]
    transition = _clean_sections(trans_raw)
    by_sop = {}
    for s in written.get("sections") or []:
        if isinstance(s, dict) and s.get("sop") in planning.ORDER and s["sop"] not in by_sop:
            by_sop[s["sop"]] = s
    market: list[dict] = []
    for ps in plan["sections"]:
        raw = by_sop.get(ps["sop"])
        if raw is None:
            logger.warning(f"[write] the model left out the {ps['sop']} section")
            continue
        cleaned = _clean_sections([{**raw, "heading": ps["heading"], "sop": [ps["sop"]]}])
        if not cleaned:
            logger.warning(f"[write] the {ps['sop']} section came back empty")
            continue
        if str(raw.get("heading") or "").strip() != ps["heading"]:
            logger.info(f"[write] {ps['sop']}: the heading was set to the plan's ({raw.get('heading')!r} -> {ps['heading']!r})")
        market.append(cleaned[0])
    return intro + transition + market


def run_write_v2(
    plan: dict, inp: planning.PlanInputs, sponsor: Paragraph, report: reports.Report, logger: logging.Logger,
) -> tuple[dict, str, list[dict], list[dict]]:
    """CALL: write the post from the validated plan. Returns (raw_json, title, sections, evidence_citations), the same shape
    pipeline.run_write returns, so finalize and the UI read it unchanged. No quality retry: problems are logged."""
    section(logger, "CALL: WRITE")
    prompt = build_write_prompt(plan, inp, sponsor, report)
    written = llm.chat_json(prompts.WRITE_SYSTEM, prompt, temperature=0.3, call_name="write", logger=logger, max_tokens=7000)
    sections = strip_meta_sentences(assemble_sections(written, plan, logger), logger)
    sections = strip_untraced_figures(sections, inp, sponsor.text, logger)
    sections = strip_repeat_figures(sections, inp, logger)
    if not any(s["sop"] and s["sop"][0] in planning.ORDER for s in sections):
        raise RuntimeError("the writer returned no market sections")
    title = _plain(plan["title"])
    problems = _sponsor_problems(sections, sponsor.text) + _link_placement_problems(sections, sponsor.text, report.name)
    if problems:
        logger.warning(f"[write] placement problems found (no retry is run): {'; '.join(problems)}")
    if _sponsor_location_problem(sections):
        logger.warning("[write] inserting the sponsor placeholder as intro paragraph 2 so the paid link is not lost")
        sections = _force_sponsor_placeholder(sections)
    citations = evidence_citations(sections, inp)
    logger.info(f"[write] news evidence used (found in code): {sorted({c['id'] for c in citations})}")
    return written, title, sections, citations
