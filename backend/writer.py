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

import config
import content_rules
import llm
import clients
import sentsplit
import subheads
import planning
import prompts
from docx_io import Paragraph
from pipeline import (
    _clean_sections, _evidence_used_ids, _force_sponsor_placeholder, _link_placement_problems, _market_core, _plain,
    _sponsor_location_problem, _sponsor_problems, _sponsor_rule, _starts_sentence, sponsor_mode,
)
from pipeline_log import section
import reports

LINK_WORDS = 100        # the SOP wants the report link inside the first 100 words (200 is the hard limit the check uses)
_DETAIL_CHARS = 300
_SHAPES = {
    "report_context": 'One or two paragraphs. It states every listed headline figure as its fact gives it: the market size with its year, the forecast value with its year and the growth rate with its period, then the leading and the fastest-growing region where they are listed. It says where the market is heading only as far as the listed facts show.',
    "trends": 'A list "paragraphs" of exactly 13 plain-text entries, in this order. Entry 1: a lead-in paragraph of 30 to 45 words that names the market by its full name, states the three trends in one sentence and says in one sentence what they change together. Then, for each of three distinct trends, four entries: first a sub-heading entry, the string "### " followed by the trend\'s heading (6 to 12 words that state the consequence for this market, for example what is raising demand for what; never a bare label, never a question); then three paragraphs of 35 to 55 words each. Paragraph one: the development itself, anchored in a listed fact: name the organisation, study, launch or event, give its date, and state its figure and its limits (scope, sample, stage) as the fact states them. Paragraph two: the mechanism: how the development changes demand, adoption or competition in this market, tied to a listed driver or segment where one is given. Paragraph three: the commercial implication: what providers, buyers or investors in this market can do or should expect, framed as inference from the facts above and adding no new fact. A trend with few listed facts keeps all three paragraphs by reasoning more fully from what its facts show, never by adding a fact. The section ends with the third trend\'s third paragraph: no closing or summary paragraph, and no bullets anywhere in the section.',
    "segmentation": 'One paragraph string: a lead-in sentence that uses the word "segmentation" or "segments", names the market in that same sentence as the thing the segmentation divides, and states that segmentation directly, with the report as its actor, saying how many segment types the report uses, in your own words, then one line starting with "\\n- " per segment type. Each line gives one segment type and its categories, with your own connective words between the categories and never the report\'s own list wording. Where a listed fact names the category that leads that type, or gives a category\'s share or value, the same line states that leading category with its figure, as the fact gives it, and adds one clause on why it leads (the driver, the shift in demand or the adoption behind it) where a listed fact supports that reason; where no listed fact does, the line adds no reason and no figure. The lead-in does not say what the segmentation lets anyone see or do. Nothing else.',
    "subsegment": 'Two entries in "paragraphs", each an object {{"segment": "<split>", "sub_segment": "<category>", "paragraph": "<text>"}}, for the two sub-segments named below, in that order. Each paragraph looks at its one sub-segment: it states the sub-segment\'s listed share or value with its year and says that it leads its split where a fact says so, then explains the growth drivers behind it, the demand pattern, the adoption trend and why the sub-segment dominates or grows, reasoning from the listed facts (the mechanism, the buyer, the use) and, where the facts say little about this sub-segment, from the market\'s own drivers that apply to it; no figure or company detail is added that no listed fact states. The section names the market\'s full name at least once, and every paragraph ties its sub-segment back to the market.',
    "country_us": 'Prose in one or two short paragraphs, or a lead-in sentence followed by "\\n- " lines when each line rests on its own listed fact. It covers the United States and only the United States: its demand, adoption, drivers and developments as the listed facts give them, in 100 to 200 words in all. Name no other region or country anywhere in the section, not even for comparison; fewer than 100 words is a miss, so carry the U.S. picture with the listed facts plus your own analytical sentences about what it means for providers and buyers in the U.S.',
    "us_from_report": 'Prose in one or two short paragraphs, 100 to 200 words in all, about the United States and only the United States. The listed facts are the report\'s own statements, drivers and opportunities, so the section opens with the report\'s U.S. statements where listed, then turns each driver, opportunity or leading segment into what it means for U.S. demand, adoption, buyers and providers, as inference from that fact. Put every fact in your own words, completely: a different sentence structure and different wording, never a phrase copied from the fact. A figure appears only where a listed fact states it, and no U.S. figure is ever added. Name no other region or country anywhere in the section, not even for comparison, and never say that U.S. data is missing or was not found.',
    "us_missing": 'The single line [EDITOR: no verified U.S. data was found for this market; add the U.S. analysis here.] and nothing else: no prose, no word count, and no mention of any other region or country.',
    "key_players": 'Two entries in "paragraphs", both prose with no bullet lines. First: a short passage that names all of the report\'s companies together in its opening sentence and says how crowded or concentrated the field is. Second: for each company that has a listed fact, one or two complete sentences naming that company and saying what it did through its own action and what that changes for buyers and for the rest of the market; a company with no listed fact appears only in the first entry\'s naming sentence and is never given a detail the facts do not supply. Every company appears inside a complete sentence that names it: no label fragment that splits a company\'s name away from its sentence, and no bullet lines for companies.',
    "key_players_names_only": 'Two entries in "paragraphs", both prose with no bullets. First: a short passage on how crowded or concentrated the field is, naming the companies the facts name together in its opening sentence, using only what the listed facts say. Second: here no company has a listed fact, so the companies stay named together in one or two complete sentences; never give a company a detail the facts do not supply.',
    "conclusion": 'One paragraph that pulls the post together using only points already made. It contains no digits at all: it refers back to earlier numbers in words (the forecast above, the growth rate stated earlier) and puts no label of its own on the pace ("modest", "rapid", "steady", "strong"), because the figures already say it. It uses the market\'s full name once, inside a sentence about the market itself, never a sentence about the name. It restates no fact, region, segment or company from the sections above, not even in other words: it looks ahead. Its last sentence is the CMI analyst\'s view.',
}

# SOPs whose SHAPE text asks for one lead-in sentence followed by "\n- " bullet lines: the bullet lines
# are reserved for the section's listed facts, so a REQUIRED secondary keyword can only be covered
# inside the lead-in sentence (see the extra instruction _keyword_lines appends for these sections).
_LIST_SHAPED = frozenset({"segmentation"})


# ------------------------------------------------------------------------------------------------- prompt

def _trim_detail(detail: str) -> str:
    """The detail cut at the last sentence end (else the last whole word) within _DETAIL_CHARS, so the writer is never handed
    a half-word to repeat as if it were a name."""
    detail = " ".join(str(detail).split())
    if len(detail) <= _DETAIL_CHARS:
        return detail
    cut = detail[:_DETAIL_CHARS]
    end = max(cut.rfind(". "), cut.rfind("; "))
    return cut[:end + 1] if end > _DETAIL_CHARS // 2 else cut[:cut.rfind(" ")].rstrip(",;: ")


REQUIRED_KINDS = frozenset({"size", "forecast", "cagr", "segment_item"})     # the figures every post must state (checks.v2_report_figure_problems)


# With an outside report standing in for CMI's, no report is cited anywhere in the post, so a shape never casts "the report" as the
# actor or the owner of a list.
_UNLINKED_SHAPE_WORDS = (
    ("with the report as its actor, saying how many segment types the report uses", "saying how many segment types the market is divided by"),
    ("never the report's own list wording", "never the source's own list wording"),
    ("all of the report's companies", "all of the market's companies"),
    ("are the report's own statements, drivers and opportunities, so the section opens with the report's U.S. statements",
     "are the market's own statements, drivers and opportunities, so the section opens with its U.S. statements"),
)


def _shape_text(key: str, linked: bool) -> str:
    text = _SHAPES[key]
    if not linked:
        for old, new in _UNLINKED_SHAPE_WORDS:
            text = text.replace(old, new)
    return text


def _fact_text(item: dict) -> str:
    note = " [historical: state its year, never as current]" if item.get("historical") else ""
    if item["kind"] in REQUIRED_KINDS:
        note += " [REQUIRED: state this figure in the section]"
    if item["kind"] == "evidence":
        line = f"- [{item['id']}] {item['text']} (source: {item['source']}){note}"
        if item.get("detail"):
            line += f"\n    detail: {_trim_detail(item['detail'])}"
        return line
    if item["source"] not in ("CMI report", ""):
        return (f"- [{item['id']}] {item['text']} (source: {item['source']}, NOT CMI: the paragraph carrying this "
                f"figure names {item['source']} once, in its first sentence that uses a figure of this source, and "
                f"its following sentences give further figures of this source without repeating the name — a new "
                f"paragraph that uses a figure of this source names it again; never with "
                f"\u201cthe same source\u201d or \u201cthat source\u201d; never credit the figure to CMI){note}")
    if item["source"] == "":
        return f"- [{item['id']}] {item['text']} (stated plainly as the market's own fact: no source is named, and never CMI){note}"
    return f"- [{item['id']}] {item['text']} (CMI report){note}"


def _keyword_lines(s: dict, inp: planning.PlanInputs) -> str:
    """The keywords assigned to one section, as assignments only. How a keyword is used (one sentence, any order and form, no synonym,
    its slot in the sentence, one section only) is stated once in the KEYWORDS rules of the prompt; it used to be repeated in full under
    every section, which is most of why the prompt carried 55 keyword instructions."""
    lines = []
    if s["secondary"]:
        lines.append(f'  - REQUIRED secondary keyword: "{s["secondary"]}".')
        if s["sop"] in _LIST_SHAPED:
            lines.append("  - This section is list-shaped: put the keyword's words in the LEAD-IN sentence with the market's own name; "
                         "its bullet lines are reserved for the listed facts alone.")
    if s["longtail"]:
        lt = next(x for x in inp.longtails if x["id"] == s["longtail"])
        lines.append(f'  - REQUIRED long-tail search: "{lt["keyword"]}" (rests on {lt["fact"]}).')
    if s["terms"]:
        lines.append("  - Related terms (optional): " + ", ".join(s["terms"]) + ".")
    return "\n".join(lines) or "  - No keyword is assigned to this section beyond the primary keyword."


def _section_block(i: int, s: dict, inp: planning.PlanInputs, points: dict[str, str]) -> str:
    msq = next((q for q in inp.questions if q["id"] == s["msq"]), None)
    size = "" if s["length"] == "us_missing" else f'  |  about {s["words"]} words'
    rows = [f'### {i}. {s["sop"]}  |  heading: "{s["heading"]}"{size}']
    rows.append(f'Purpose (what the reader learns here): {s["point"] or "(the section\'s ordinary job)"}')
    if msq:
        rows.append("The heading is a question people really search. Answer it directly in the first one or two sentences (about 40 to 60 words), in precise terms, then continue with the section.")
    if s["length"] == "short" and s["sop"] != "trends":
        rows.append("This section has few facts: state them exactly and add the analysis they support (what they mean for buyers and providers "
                    "in this market) without adding a fact. It may land under the budget.")
    elif s["length"] == "us_missing":
        rows.append("Neither a verified U.S. fact nor a report fact exists for this section. Keep the heading and write only the editor line given in the "
                    "SHAPE below, as the section's one paragraph. Do not write about any other region or country.")
    elif s["length"] == "us_from_report":
        rows.append("Little verified U.S. data was found for this market, so this section is written from the market's own facts listed below "
                    "(its U.S. statements first, then its drivers, opportunities and leading segments), each framed as what it means for U.S. demand, "
                    "adoption, buyers and providers. Rephrase every fact fully in your own words, with a different sentence structure and wording; "
                    "add no U.S. figure that no listed fact states; name no other region or country (no North America, no Europe, no Asia).")
    facts_rows = [_fact_text(inp.items[f]) for f in s["facts"]]
    if s["sop"] == "trends" and s.get("trend_groups"):
        parts = []
        for n, g in enumerate(s["trend_groups"], 1):
            name = g["trend"] or "(the planner gave this trend no name: name it from its facts)"
            body = "\n".join(_fact_text(inp.items[f]) for f in g["facts"]) or "- (no listed fact: reason from the section's purpose without adding a fact)"
            parts.append(f"Trend {n}: {name}\n{body}")
        rows.append("Facts to use, by trend (each trend is written only from its own facts, and its sub-heading states that trend):\n" + "\n".join(parts))
    else:
        rows.append("Facts to use:\n" + ("\n".join(facts_rows) if facts_rows else "- (none)"))
    if s["sop"] == "subsegment" and s.get("sub_segments"):
        rows.append("Sub-segments to cover: " + "; ".join(f'{x["name"]} (from the split "{x["split"]}")' for x in s["sub_segments"]) + ".")
    if s["sop"] == "trends":
        rows.append("The section keeps all three trends with three paragraphs each, however few facts are listed: where facts are few, reason "
                    "more fully from them (what they show, why it matters in this market, what follows commercially) without adding a fact. "
                    "Spread the listed facts across the three trends, two or three to a trend, none used twice.")
    rows.append("Keywords:\n" + _keyword_lines(s, inp))
    if s["snippet"]:
        rows.append(f'Guest example (optional, at most one sentence, only if it makes the point clearer): "{points[s["snippet"]]}"')
    names_only = s["sop"] == "key_players" and not any(inp.items[f]["kind"] == "evidence" for f in s["facts"])
    shape = s["length"] if s["length"] in ("us_missing", "us_from_report") else "key_players_names_only" if names_only else s["sop"]
    rows.append("SHAPE: " + _shape_text(shape, getattr(inp, "link_report", True)))
    return "\n".join(rows)


def build_write_prompt(plan: dict, inp: planning.PlanInputs, sponsor: Paragraph, report: reports.Report, today: str | None = None) -> str:
    points = {p["id"]: p["text"] for p in inp.points}
    blocks = [_section_block(i, s, inp, points) for i, s in enumerate(plan["sections"], 1)]
    mode = sponsor_mode(sponsor.text)
    linked = getattr(inp, "link_report", True)
    total = plan["budgets"]["target"]
    # The transition band is 5-10% of the body (checks.block_share_problems). A transition that slides
    # under the band floor brings the whole draft down with it, so the prompt carries a floor in words:
    # about three quarters of the planned transition budget, and never below 65.
    transition_words = int(plan["transition"]["words"])
    transition_floor = max(65, int(transition_words * 0.75))
    lead = "the lead-in paragraph with the report link" if linked else "the lead-in paragraph that names the market"
    client_list = list(getattr(inp, "clients", None) or []) if config.CLIENT_REWRITE else []
    if client_list:
        opening_shape = (
            "a lead-in paragraph that sets the scene with the subject; "
            "then one paragraph, of the opening's own explanation, that carries every client link (see THE CLIENT LINKS); then, only "
            "if the budget has room, one short closing paragraph that leads towards the market."
        )
        intro_shape = f'["<{lead}>", "<the paragraph that carries the [[CLIENT_LINK:...]] link(s), without naming any client>", "<an optional short closing paragraph>"]'
    elif mode == "phrase":
        opening_shape = (
            "a lead-in paragraph that sets the scene with the subject; "
            "then a second paragraph of your own in which the placeholder sits INSIDE one of your sentences, with your own words before and after it "
            "(the retained text is a short linked phrase, not a paragraph, so it never stands alone on a line). Nothing else is needed."
        )
        # The output template must MATCH that shape: showing the placeholder as a paragraph of its own here made the
        # model return exactly that, which breaks the phrase-mode placement rule.
        intro_shape = f'["<{lead}>", "<a paragraph of your own that embeds {prompts.SPONSOR_TOKEN} inside one of its sentences>"]'
    else:
        opening_shape = (
            "a lead-in paragraph that sets the scene with the subject; "
            "then the retained paragraph's slot; then, only if the budget has room, one short closing paragraph."
        )
        intro_shape = f'["<{lead}>", "{prompts.SPONSOR_TOKEN}", "<an optional short closing paragraph>"]'
    if client_list:
        client_block = clients.client_block(client_list)
        opening_limit = (f"at most {plan['intro']['own_words']} words of your own lead-in and closing, plus the paragraph that carries "
                         f"the client links, of about {clients.paragraph_words(client_list)} words")
    else:
        client_block = (
            "- The retained paragraph is written by someone else and is placed by code. Never write its words or a paraphrase of them. "
            "Read it, so that your lead-in prepares the reader for whatever it takes for granted (a term, a comparison, something it "
            f'refers back to). Its words are: "{sponsor.text}"\n- {_sponsor_rule(mode, sponsor.text)}')
        opening_limit = f"at most {plan['intro']['own_words']} words of your own, plus the retained text"
    text = _format_write_user(plan, inp, sponsor, report, today, mode, linked, total, transition_words, transition_floor,
                              opening_shape, intro_shape, blocks, points, client_block, opening_limit)
    return text + ("\n\n" + prompts.STYLE_ANCHORS if config.WRITE_STYLE_ANCHORS else "")


def _format_write_user(plan, inp, sponsor, report, today, mode, linked, total, transition_words, transition_floor,
                       opening_shape, intro_shape, blocks, points, client_block, opening_limit) -> str:
    return prompts.WRITE_USER.format(
        title=plan["title"], market_name=report.name, today=today or date.today().isoformat(),
        intro_own=plan["intro"]["own_words"], intro_angle=inp.intro_angle or "(the guest article's title)",
        intro_points="\n".join(f"  - {points[p]}" for p in plan["intro"]["points"]) or "  - (none)",
        sponsor_rule=_sponsor_rule(mode, sponsor.text), opening_shape=opening_shape,
        link_rule=(f'Write it in the lead-in paragraph, inside the first {LINK_WORDS} words of the post (in the first two sentences), exactly once as the link '
                   f"[[CMI_LINK:{report.name}]]. This is the first time the market's name appears anywhere in the post."
                   if linked else f'Write it in plain text in the lead-in paragraph, inside the first {LINK_WORDS} words of the post (in the first two sentences). '
                   "The opening carries no link. No report is cited anywhere in the post: never write 'CMI's report', 'the report', 'the research' "
                   "or 'according to a report' (state each fact plainly, as the market's own fact), and name no research firm."),
        intro_shape=intro_shape,
        transition_words=transition_words,
        transition_floor=transition_floor,
        transition_point=plan["transition"]["point"] or "explain why the guest article's subject belongs to this wider market and what the reader will learn next",
        sections="\n\n".join(blocks), total_words=total, sponsor_token=prompts.SPONSOR_TOKEN,
        sponsor_text=sponsor.text, client_block=client_block, opening_limit=opening_limit,
    )


# ------------------------------------------------------------------------------------------------- code backstops

# Sentences that talk about the writing process or about what the sources lack. A prompt rule against them is not reliable,
# so they are removed in code (the sentence goes; nothing is rewritten).
_META_RE = re.compile(
    r"\b(?:listed|supplied|provided)\s+facts?\b|\bfacts?\s+(?:listed|supplied|provided|given)\b"
    r"|\bthe\s+facts?\s+(?:say|carry|show|give|do not|does not|don't)\b"
    r"|\b(?:according\s+to|per|following|in\s+line\s+with)\s+the\s+(?:plan|brief)\b"
    r"|\bthe\s+(?:plan|brief)\s+(?:says|calls|requires|asks|states|gives|lists|specif\w+)\b"
    r"|\b(?:report|source|sources)\s+(?:does|do)\s+not\s+(?:put|break|give|say|state|list|name)\b"
    r"|\bnot\s+(?:broken\s+out|listed|stated|given|provided)\s+(?:in|by)\b"
    r"|\b(?:says|say)\s+little\s+more\b|\bstays?\s+(?:broad|at\s+the\s+regional\s+level)\b"
    r"|\bno\s+(?:further|more|specific)\s+(?:detail|data|figures?)\s+(?:is|are|was|were)\b"
    r"|\bthe\s+(?:observation|finding|statement|source|figure)\s+(?:is\s+(?:qualitative|descriptive)|does\s+not\s+(?:quantify|state|measure|give))\b"
    r"|\brather\s+than\s+any\s+measured\b|\bdoes\s+not\s+quantify\b"
    # the post saying what its sources lack: "No specific U.S. data is available", "we could not find ...", "figures were not available"
    r"|\bno\s+(?:specific|verified|separate|dedicated|reliable)\s+(?:(?:u\.s\.|us|united\s+states)\s+)?(?:data|figures?|statistics|facts?|numbers)\b"
    r"|\b(?:we|i)\s+(?:could|can|did|were|was)\s*(?:not|n['’]t)\s+(?:able\s+to\s+)?(?:find|locate|verify|identify)\b"
    r"|\b(?:could|can)\s*(?:not|n['’]t)\s+be\s+(?:found|located|verified)\b"
    r"|\b(?:data|figures?|statistics|numbers|facts?|information|details?)\b[^.;]{0,40}\b(?:(?:is|are|was|were)\s+(?:not|n['’]t)\s+available|unavailable|not\s+(?:yet\s+)?(?:published|disclosed))\b"
    # the post announcing itself: "The sections that follow set out...", "this article explains..."
    r"|\b(?:sections?|parts?)\s+(?:that\s+)?(?:follow|ahead|below)\b|\b(?:the\s+)?following\s+(?:sections?|parts?)\b"
    r"|\b(?:this|the)\s+(?:article|post|piece)\s+(?:explains|examines|sets\s+out|covers|outlines|looks\s+at|will)\b",
    re.IGNORECASE,
)
_SENTENCE_SPLIT_RE = sentsplit.SPLIT_RE
# "..., and the sections below examine how demand is changing." : the clause after the last comma only announces the post
_TRAILING_ROADMAP_RE = re.compile(
    r",\s+(?:and\s+|while\s+|so\s+)?(?:the\s+)?(?:(?:sections?|parts?)\s+(?:that\s+)?(?:follow|ahead|below)|following\s+(?:sections?|parts?)"
    r"|(?:this|the)\s+(?:article|post|piece))\b[^,;]*(?P<end>[.!?])\s*$", re.IGNORECASE)


def _trim_roadmap_clause(sentence: str) -> str:
    """The sentence without a trailing clause that only announces the post, when what is left is a real statement (six words or more)."""
    m = _TRAILING_ROADMAP_RE.search(sentence)
    if m and len(sentence[:m.start()].split()) >= 6 and not _holds_link(sentence):
        return sentence[:m.start()].rstrip() + m.group("end")
    return sentence


def _holds_link(sentence: str) -> bool:
    """A sentence carrying the retained-paragraph placeholder, a client link token or the CMI report link: the backstops
    never cut, reword or remove it, because a lost link is a lost backlink (the checks report anything wrong with such a
    sentence). Precedent: a sentence "CMI's analysis of the [[CMI_LINK:...]] examines how vendors..." was removed as a
    coverage advert and the post went out with no report link at all."""
    return prompts.SPONSOR_TOKEN in sentence or "[[CLIENT_LINK:" in sentence or "[[CMI_LINK:" in sentence


# A sentence that does nothing but advertise what a study, survey, report or site covers — no figure and no finding of
# its own — tells the reader nothing and is removed (precedent: _META_RE above). A coverage-verb sentence that carries a
# real figure or a real reported result is left alone by design: it states a finding, not mere coverage. The rule also
# stays conservative about sentences that already do name work: one carrying the sponsor placeholder, one naming the
# key players, and one holding a comma-separated list of proper names (the publication naming who or what is in its
# pages) are never removed.
_COVERAGE_SUBJECT_RE = re.compile(
    r"\b(?:report|study|survey|research|publication|analysis|index|website|magazine|whitepaper|white paper)"
    r"s?(?:[\u2019']s)?\b", re.IGNORECASE)
_COVERAGE_VERB_RE = re.compile(
    r"\b(?:tracks?|examines?|studies|studied|covers?|looks? at|explores?|analy[sz]es?|surveys?|charts?|maps?|"
    r"monitors?|follows?)\b", re.IGNORECASE)
_FINDING_VERB_RE = re.compile(
    r"\b(?:found|finds|reported|reports that|shows that|showed|said|says|estimates|estimated|projects|"
    r"projected|expects|expected|concluded)\b", re.IGNORECASE)
_NAME_LIST_RE = re.compile(
    r"\b[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*(?:, [A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*){2,}\b")


def _is_coverage_ad(sent: str) -> bool:
    """Whether a sentence only advertises a publication's coverage: a publication-type subject word inside its first
    8 words (possessives included), a coverage verb, and neither a figure (checks._fact_numbers) nor a finding verb in
    the sentence itself."""
    if _holds_link(sent):
        return False
    lowered = sent.lower()
    if "key players" in lowered or re.search(r"\bnames\b", lowered):
        return False
    if _NAME_LIST_RE.search(sent):
        return False
    if not _COVERAGE_SUBJECT_RE.search(" ".join(sent.split()[:8])):
        return False
    if not _COVERAGE_VERB_RE.search(sent):
        return False
    import checks
    if checks._fact_numbers(sent):
        return False
    return not _FINDING_VERB_RE.search(sent)


# A sentence the backstops remove can be the one a following sentence refers to ("Apertera announced ... ." then "Its chief executive
# observed ..."). Without its antecedent that sentence reads as damage, so it goes with it: a sentence that opens on a reference
# directly after a removed sentence is removed too, and so on down the run (the whole-text check reports any that still get through).
_ORPHAN_OPENER_RE = re.compile(
    r"^(?:(?:it|its|they|their|he|she|this|that|these|those|such|on that|in that|from that|at that|by that)\b"
    r"|the (?:company|firm|provider|vendor|group|deal|acquisition|merger|move|launch|announcement|partnership|result|study|survey|"
    r"finding|figure|plan|system|platform|tool)\b)", re.I)


def _drop_orphans(kept: list[str], removed: set[int]) -> list[str]:
    """`kept` is a line's sentences in order and `removed` the indexes the caller removed: the sentences left, minus any that open
    on a reference directly after a removed one."""
    out: list[str] = []
    gap = False
    for i, sent in enumerate(kept):
        if i in removed:
            gap = True
            continue
        if gap and not _holds_link(sent) and _ORPHAN_OPENER_RE.match(sent.strip()):
            continue
        gap = False
        out.append(sent)
    return out


_US_EDITOR_NOTE = "[EDITOR: no verified U.S. data was found for this market; add the U.S. analysis here.]"


def editor_note(sop: str) -> str:
    """The one line a market section carries when it has no text of its own to keep."""
    if sop == "country_us":
        return _US_EDITOR_NOTE
    return f"[EDITOR: the {sop.replace('_', ' ')} section's text did not survive the checks; write it here.]"


def keep_emptied_sections(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """The sections that still have paragraphs. A planned market section left with none keeps its heading and gets an editor
    note instead of vanishing from the post (the editor sees where the text belongs); the opening and the transition, which
    have no heading to keep, are dropped. Logged as a warning."""
    out = []
    for s in sections:
        if s["paragraphs"]:
            out.append(s)
        elif s.get("sop") and s["sop"][0] in planning.ORDER:
            logger.warning(f"[write] every paragraph of the {s['sop'][0]} section was removed; it keeps its heading with an editor note")
            out.append({**s, "paragraphs": [editor_note(s["sop"][0])]})
    return out


def strip_meta_sentences(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """Remove sentences that describe the facts or the plan instead of the market, or that advertise a publication's
    coverage without reporting any finding from it. Works line by line so bullet lists keep
    their shape; a line left empty is dropped, and a paragraph left empty is dropped with a warning."""
    for s in sections:
        paragraphs = []
        for p in s["paragraphs"]:
            lines = []
            for line in p.split("\n"):
                kept = [_trim_roadmap_clause(x) for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                gone = {i for i, x in enumerate(kept) if not _holds_link(x) and not x.lstrip().startswith("[EDITOR")
                        and (_META_RE.search(x) or _is_coverage_ad(x))}
                good = _drop_orphans(kept, gone)
                if len(good) != len(kept):
                    logger.info(f"[write] removed {len(kept) - len(good)} process-language sentence(s) from {s['sop'] or 'the opening'}: "
                                + " | ".join(x[:110] for x in kept if x not in good))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return keep_emptied_sections(sections, logger)


# The sentence-end bar `_sponsor_problems` applies to the text before an inline placeholder: trailing
# closing quotes and brackets after . ! ? still count as a complete sentence ending.
_ENDS_SENTENCE_RE = re.compile(r"[.!?][\"'”’)\]]*\s*$")


def repair_sponsor_placement(sections: list[dict], logger: logging.Logger, sponsor_text: str = "") -> list[dict]:
    """Move the retained paragraph's placeholder out of a sentence it was written into. There is no
    retry: `_sponsor_problems` reports a mid-sentence placement after the call, but nothing fixes it,
    and straight substitution (checks._sponsor_replacement) then garbles the sentence it was spliced
    into. So the sentence holding the token is dropped here (its words cannot be salvaged
    mechanically) and the token becomes its own paragraph — a placement the write prompt explicitly
    allows ("the placeholder alone and nothing else"). Only the untagged opening sections are
    touched, only the first token occurrence is repaired (extras are left where they are), and valid
    placements come back byte for byte. A PHRASE sponsor is exempt entirely: there the prompt
    REQUIRES the token inside a sentence, so an inline placement is correct, not a fault."""
    if sponsor_text and sponsor_mode(sponsor_text) == "phrase":
        return sections
    for s in sections:
        if s.get("sop"):        # only the untagged opening (the intro) may hold the token
            continue
        for pi, p in enumerate(s["paragraphs"]):
            if prompts.SPONSOR_TOKEN not in p:
                continue
            if p.strip() == prompts.SPONSOR_TOKEN:
                return sections     # valid: the token is the whole paragraph
            cut = p.index(prompts.SPONSOR_TOKEN)
            before, after = p[:cut], p[cut + len(prompts.SPONSOR_TOKEN):]
            before_ok = not before.strip() or bool(_ENDS_SENTENCE_RE.search(before))
            if before_ok and (not after.strip() or _starts_sentence(after)):
                return sections     # valid: the token sits between two complete sentences
            # invalid (mid-sentence): the whole sentence that holds the token goes, the other
            # sentences keep their order, and the token gets its own paragraph right after them.
            sents = [x for x in _SENTENCE_SPLIT_RE.split(p) if x.strip()]
            hi = next(i for i, x in enumerate(sents) if prompts.SPONSOR_TOKEN in x)
            remaining = " ".join(x for i, x in enumerate(sents) if i != hi)
            logger.info(f"[write] moved the sponsor placeholder out of a sentence to its own paragraph: {sents[hi][:80]}")
            s["paragraphs"][pi] = remaining if remaining else prompts.SPONSOR_TOKEN
            if remaining:
                s["paragraphs"].insert(pi + 1, prompts.SPONSOR_TOKEN)
            return sections
    return sections


# A sentence that opens with a pointing word nearly always points straight at a noun that already names the thing
# ("That skill matters..."), and a hard check rejects every such opener. A pointing word that stands alone ("That
# is why...") cannot be fixed mechanically, so the word after the demonstrative decides: only a noun is rewritten.
_DEMONSTRATIVE_RE = re.compile(r"^(?P<lead>[\"'\u201c\u2018(\s]*)(?P<dem>This|That|These|Those)(?P<gap>\s+)(?P<next>[A-Za-z][A-Za-z'-]*)", re.IGNORECASE)
_NOT_A_NOUN = frozenset({
    "is", "are", "was", "were", "be", "been", "being", "will", "would", "can", "could", "may", "might", "must",
    "shall", "should", "has", "have", "had", "does", "did", "do", "means", "mean", "shows", "show", "makes", "make",
    "leads", "lead", "points", "point", "suggests", "suggest", "implies", "imply", "explains", "explain", "matters",
    "matter", "helps", "help", "keeps", "keep", "gives", "give", "adds", "add", "reflects", "reflect", "reveals",
    "reveal", "signals", "signal", "indicates", "indicate", "drives", "drive", "raises", "raise", "pushes", "push",
    "creates", "create", "lets", "let", "allows", "allow", "leaves", "leave", "puts", "put", "brings", "bring",
    "supports", "support", "feeds", "feed", "fits", "fit", "covers", "cover", "said", "also", "only", "alone",
    "then", "now", "too", "as", "because", "which", "who", "when", "where", "while", "and", "but", "so", "if",
    "for", "to", "of", "in", "on", "at", "by", "with", "from", "not", "never", "always", "still", "even", "just",
    "really", "simply", "often",
})
_TIME_WORDS = frozenset({"year", "years", "month", "months", "week", "weeks", "day", "days", "time", "times",
                         "morning", "evening", "quarter", "decade", "season", "way", "moment", "point"})
_ED_NOUNS = frozenset({
    "bed", "beds", "breed", "breeds", "creed", "creeds", "deed", "deeds", "feed", "feeds", "greed", "greeds",
    "need", "needs", "seed", "seeds", "shed", "sheds", "speed", "speeds", "steed", "steeds", "weed", "weeds",
})
_IRREGULAR_PAST = frozenset({"led", "made", "meant", "kept", "gave", "came", "went", "took", "saw", "got", "set",
                             "put", "ran", "began", "grew", "drove", "rose", "fell", "held", "left", "brought",
                             "became", "was", "were", "had", "did", "said", "told", "found", "thought", "knew",
                             "felt", "seemed"})
# These/Those before a number word or a small quantifier are followed by a plural noun one word further on,
# so the word right after the demonstrative is no longer the one that decides: the pair quantifier + plural
# noun does ("Those two figures", "These few buyers"). Same lead/captures as _DEMONSTRATIVE_RE.
_DEMONSTRATIVE_QUANT_RE = re.compile(
    r"^(?P<lead>[\"'\u201c\u2018(\s]*)(?P<dem>These|Those)(?P<gap>\s+)(?P<quant>[A-Za-z0-9][A-Za-z0-9'-]*)(?P<gap2>\s+)(?P<next>[A-Za-z][A-Za-z'-]*)",
    re.IGNORECASE)
_QUANTIFIER_WORDS = frozenset({"one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
                               "both", "several", "few", "many", "other", "same", "first", "last", "main", "key"})


def _quantified_plural_follows(quant: str, nxt: str) -> bool:
    """True when the word after a quantified demonstrative opener is a plural noun-shaped word (ends in s,
    not ss, and not one of the verbs/antecedents _NOT_A_NOUN holds), so 'Those two figures' may become
    'The two figures'. Bare openers never reach here: a number word or quantifier is required."""
    if not (quant.isdigit() or quant.lower() in _QUANTIFIER_WORDS):
        return False
    w = nxt.lower()
    return w.endswith("s") and not w.endswith("ss") and w not in _NOT_A_NOUN


def _noun_follows(dem: str, nxt: str) -> bool:
    """Decide, by grammar shape alone, whether the word after a leading demonstrative is a noun/adjective
    (so 'That skill' may become 'The skill') or a verb ('That changes' stays). A mangled sentence is worse
    than the pointing word, so anything not clearly noun-shaped is left alone. A small set of nouns whose
    shape ends in "ed" (need, speed, deed...) passes before the -ed test, branch by branch: a singular
    noun goes with This/That, its plural with These/Those, so the verb reading ("That feeds...") keeps."""
    w, d = nxt.lower(), dem.lower()
    if w in _ED_NOUNS:
        if d in ("this", "that"):
            return not w.endswith("s") or w.endswith(("ss", "sis", "us"))
        if d in ("these", "those"):
            return w.endswith("s") and not w.endswith("ss")
        return False
    if w in _NOT_A_NOUN or w in _TIME_WORDS or w in _IRREGULAR_PAST or w.endswith("ed"):
        return False
    if d in ("this", "that"):  # a singular verb ends in -s, so noun/adjective only without -s (or ss/sis/us)
        return not w.endswith("s") or w.endswith(("ss", "sis", "us"))
    if d in ("these", "those"):  # a plural noun ends in -s; a verb after a plural subject never does
        return w.endswith("s") and not w.endswith("ss")
    return False


def fix_pointing_determiners(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """Turn a leading 'That/This/These/Those + noun' into 'The + noun' (the noun already names the thing); leave bare pointing words alone."""
    for s in sections:
        n, originals = 0, []
        for pi, p in enumerate(s["paragraphs"]):
            lines = []
            for line in p.split("\n"):
                marker, text = "", line
                if text.startswith("- ") or text.startswith("• "):
                    marker, text = text[:2], text[2:]
                out = []
                for sent in [x for x in _SENTENCE_SPLIT_RE.split(text) if x.strip()]:
                    m = _DEMONSTRATIVE_RE.match(sent)
                    mq = _DEMONSTRATIVE_QUANT_RE.match(sent)
                    noun_plain = m and _noun_follows(m.group("dem"), m.group("next"))
                    noun_after = noun_plain or (mq and _quantified_plural_follows(mq.group("quant"), mq.group("next")))
                    if (noun_after and prompts.SPONSOR_TOKEN not in sent
                            and len(sent.split()) >= 4):
                        opener = m if noun_plain else mq
                        originals.append(sent[:80])
                        sent = sent[:opener.start("dem")] + "The" + sent[opener.end("dem"):]
                        n += 1
                    out.append(sent)
                lines.append(marker + " ".join(out) if out else line)
            if lines:
                s["paragraphs"][pi] = "\n".join(lines)
            else:
                s["paragraphs"][pi] = p
        if n:
            label = s["sop"][0] if s.get("sop") else "the opening"
            logger.info(f"[write] replaced {n} leading pointing word(s) with 'The' in {label}: "
                        + " | ".join(originals))
    return sections


# A sentence that opens with a bare pointing word ("That works out to...", "It suggests that...") points backwards
# across a sentence boundary, which no single-word swap can fix. Such a sentence is attached to the one before it
# with ", which": the pointing word is dropped and the verb continues the previous sentence's thought, so the
# reference lives inside one sentence ("Forecasters expect USD 910 million by 2034, which works out to 7.3% a year.").
_BARE_POINTER_RE = re.compile(r"^(?P<dem>This|That|It)\s+(?P<verb>[A-Za-z']+)\b", re.IGNORECASE)
_BARE_VERBS_THAT = frozenset({"is","was","will","would","can","could","may","might","makes","means","shows","leads","points","suggests","implies","explains","matters","helps","keeps","gives","adds","reflects","reveals","signals","indicates","works","comes","puts","brings","raises","drives","pushes","creates","lets","allows","leaves","supports","fits","covers","remains","becomes","seems","looks","translates","amounts","equates"})
_BARE_VERBS_IT = frozenset({"means","shows","points","suggests","implies","explains","matters","leads","signals","reflects","reveals","indicates","helps","makes","keeps","gives","adds"})
_MERGE_MAX_WORDS = 45


def join_bare_pointers(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """Join a sentence that opens with a bare pointing word (That/This + verb, or It + a pointing verb) to the sentence before it with ', which'."""
    for s in sections:
        n, originals = 0, []
        for pi, p in enumerate(s["paragraphs"]):
            lines = []
            for line in p.split("\n"):
                marker, text = "", line
                if text.startswith("- ") or text.startswith("• "):
                    marker, text = text[:2], text[2:]
                out = []
                for sent in [x for x in _SENTENCE_SPLIT_RE.split(text) if x.strip()]:
                    m = _BARE_POINTER_RE.match(sent)
                    prev = out[-1].rstrip() if out else ""
                    verb_ok = (m and m.group("verb").lower() in (
                        _BARE_VERBS_IT if m.group("dem").lower() == "it" else _BARE_VERBS_THAT))
                    joined = ""
                    if (m and prev and verb_ok and prev.endswith(".") and not prev.endswith("..")
                            and len(prev.split()) >= 4 and prompts.SPONSOR_TOKEN not in prev
                            and prompts.SPONSOR_TOKEN not in sent):
                        joined = prev[:-1] + ", which " + sent[m.start("verb"):]
                        if len(joined.split()) <= _MERGE_MAX_WORDS:
                            originals.append(sent[:80])
                            out[-1] = joined
                            n += 1
                            continue
                    out.append(sent)
                lines.append(marker + " ".join(out) if out else line)
            s["paragraphs"][pi] = "\n".join(lines)
        if n:
            label = s["sop"][0] if s.get("sop") else "the opening"
            logger.info(f"[write] joined {n} sentence(s) that opened with a bare pointing word to the sentence before in {label}: "
                        + " | ".join(originals))
    return sections


# A trailing clause that only narrates what the reader will see, understand or learn ("..., so readers can see ...",
# "..., to help buyers understand ...", "..., which helps investors judge ...") adds no information: it announces why
# the reader is being told the fact instead of stating anything. It is cut, from the comma before it to the end of
# the sentence; the final punctuation is kept. A consequence clause built on any other verb (a clause that says what
# actually happens next) is never touched.
_READER_TAIL_RE = re.compile(
    r",\s+(?:(?:so|so that)\s+(?:\w+\s+){1,3}?(?:can|could|may|might|will|would|get to)\s+(?:\w+ly\s+)?"
    r"(?:see|understand|know|grasp|follow|judge|tell|learn|gauge|appreciate|picture|get a (?:sense|feel|picture|view))\b"
    r"|(?:to help|helping|which helps?|which lets?|letting|which allows?|allowing)\s+(?:\w+\s+){1,3}?"
    r"(?:see|understand|know|grasp|follow|judge|tell|learn|gauge|appreciate|picture)\b)"
    r".*",
    re.IGNORECASE,
)


def strip_reader_filler(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """Cut the earliest trailing reader-narration clause from each sentence, from the comma before it to the end of
    the sentence. Only a sentence that keeps at least five words before that comma is touched, and a sentence
    carrying the sponsor placeholder is never touched."""
    for s in sections:
        n, originals = 0, []
        for pi, p in enumerate(s["paragraphs"]):
            lines = []
            for line in p.split("\n"):
                marker, text = "", line
                if text.startswith("- ") or text.startswith("• "):
                    marker, text = text[:2], text[2:]
                out = []
                for sent in [x for x in _SENTENCE_SPLIT_RE.split(text) if x.strip()]:
                    m = _READER_TAIL_RE.search(sent)
                    if (m and not _holds_link(sent) and len(sent[:m.start()].split()) >= 5):
                        originals.append(m.group(0)[:80])
                        sent = sent[:m.start()].rstrip() + (sent[-1] if sent[-1] in ".!?" else "")
                        n += 1
                    out.append(sent)
                lines.append(marker + " ".join(out) if out else line)
            s["paragraphs"][pi] = "\n".join(lines)
        if n:
            label = s["sop"][0] if s.get("sop") else "the opening"
            logger.info(f"[write] cut {n} reader-narration clause(s) that only announced what the reader "
                        f"would do with the fact from {label}: " + " | ".join(originals))
    return sections


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
        allowed |= checks._fact_numbers_with_words(i["text"]) | checks._fact_numbers_with_words(i.get("detail", ""))
        allowed |= checks._fact_numbers_with_words(i.get("source", ""))
        for pt in inp.points:
            allowed |= checks._fact_numbers_with_words(pt["text"])
        allowed |= checks._fact_numbers_with_words(sponsor_text) | checks._fact_numbers_with_words(inp.market_name)
    for s in sections:
        label = s["sop"][0] if s.get("sop") else "the opening"
        paragraphs: list[str] = []
        for p in s["paragraphs"]:
            lines: list[str] = []
            for line in p.split("\n"):
                kept = [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                gone_ix: set[int] = set()
                for si, sent in enumerate(kept):
                    text = checks._sponsor_replacement(sent, sponsor_text) if sponsor_text else sent
                    bad = sorted(n for n in checks._fact_numbers(text)
                                 if n not in allowed and not re.fullmatch(r"(?:19|20)\d\d", n))
                    if bad and not _holds_link(sent):
                        gone_ix.add(si)
                good = _drop_orphans(kept, gone_ix)
                gone = [x for x in kept if x not in good]
                if gone:
                    logger.info(f"[write] removed {len(gone)} untraced-figure sentence(s) from {label}: "
                                + " | ".join(x[:110] for x in gone))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return keep_emptied_sections(sections, logger)


def strip_repeat_figures(sections: list[dict], inp: planning.PlanInputs, logger: logging.Logger) -> list[dict]:
    """The write prompt says a figure is stated in one section only and later sections refer back in words;
    four replay rounds still returned conclusions that reprint a non-CMI figure unnamed — which is exactly
    what the attribution check flags (§3.2). Those sentences are removed in code, like strip_meta_sentences:
    a sentence that repeats a figure already printed in an earlier sentence and does not name its source
    goes; a sentence that carries the source's name stays, and so does one whose source an earlier sentence
    of the same line named (the unit of naming is one line of a paragraph; the memory resets at every
    line, so a new line that repeats the figure names the source again). Years never count; a figure's
    repeat only covers when the sentence (or an earlier sentence of its line) names the figure's own source."""
    if inp is None:
        return sections
    import checks
    owned: dict[str, str] = {}      # figure -> its outside source, from the fact sheet and evidence items
    for i in inp.items.values():
        if i["kind"] not in ("size", "forecast", "cagr", "evidence") or i.get("source") in ("", "CMI report"):
            continue
        src = i.get("source") or ""
        # Ownership uses digits only: a spelled small number ("two") is prose, not an identifying figure.
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
                named_line: set[str] = set()    # outside sources named in earlier sentences of this line
                good, gone, gone_ix = [], [], set()
                for si, sent in enumerate(kept):
                    low = sent.lower()
                    strong = [n for n in checks._fact_numbers(sent) if not re.fullmatch(r"(?:19|20)\d\d", n)]
                    repeats = sorted(n for n in strong if n in printed)
                    if repeats:
                        srcs = {owned[n] for n in repeats}
                        if not all(checks._names_source(low, src) or src in named_line for src in srcs):
                            gone.append(sent)
                            gone_ix.add(si)
                            continue
                    good.append(sent)
                    named_line |= {src for src in set(owned.values()) if checks._names_source(low, src)}
                    printed.update(n for n in strong if n in owned)
                if gone_ix:       # a sentence that refers to a removed one goes with it
                    kept_after = _drop_orphans(kept, gone_ix)
                    gone += [x for x in good if x not in kept_after]
                    good = [x for x in good if x in kept_after]
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
    return keep_emptied_sections(sections, logger)



def strip_repeat_evidence(sections: list[dict], inp: planning.PlanInputs, logger: logging.Logger) -> list[dict]:
    """A piece of news evidence is cited in one section and in one trend only (the write prompt says so; a prompt rule alone
    did not hold). A sentence that cites one already cited earlier in another section, or in another trend block, goes, with
    the sentences that refer to it, exactly like the other strips; the first citation stays. Found with
    content_rules.evidence_reuse, the same function the check uses."""
    if inp is None:
        return sections
    items = [{"id": i["id"], "fact": i["text"]} for i in inp.items.values() if i["kind"] == "evidence"]
    rows = [r for r in content_rules.evidence_reuse(sections, items) if not _holds_link(r["sentence"])]
    if not rows:
        return sections
    flagged = {r["at"] for r in rows}
    for si, s in enumerate(sections):
        paragraphs = []
        for pi, p in enumerate(s["paragraphs"]):
            lines = []
            for li, line in enumerate(p.split("\n")):
                kept = [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]
                gone = {ci for ci in range(len(kept)) if (si, pi, li, ci) in flagged}
                good = _drop_orphans(kept, gone)
                if len(good) != len(kept):
                    logger.info(f"[write] removed {len(kept) - len(good)} sentence(s) that cite news evidence already cited elsewhere from "
                                f"{s['sop'][0] if s.get('sop') else 'the opening'}: " + " | ".join(x[:110] for x in kept if x not in good))
                if good:
                    lines.append(" ".join(good))
            if lines:
                paragraphs.append("\n".join(lines))
        s["paragraphs"] = paragraphs
    return keep_emptied_sections(sections, logger)


def enforce_trend_shape(sections: list[dict], logger: logging.Logger) -> list[dict]:
    """The trends section is a lead-in and three trends of three paragraphs each (the SOP's shape). A model sometimes writes a
    fourth trend, a fourth paragraph or a closing summary: the extras are cut here and logged. A section with fewer than
    the shape needs is left as written, for the trend check to report."""
    for s in sections:
        if "trends" not in (s.get("sop") or []):
            continue
        lead, blocks = subheads.split_blocks(s["paragraphs"])
        if not blocks:
            continue
        kept = blocks[:subheads.TRENDS_PER_SECTION]
        out = lead[:1]
        cut_paragraphs = 0
        for heading, paras in kept:
            out.append(subheads.make_subhead(heading))
            out += paras[:subheads.PARAGRAPHS_PER_TREND]
            cut_paragraphs += max(0, len(paras) - subheads.PARAGRAPHS_PER_TREND)
        extra_trends, extra_lead = len(blocks) - len(kept), max(0, len(lead) - 1)
        if extra_trends or cut_paragraphs or extra_lead:
            logger.info(f"[write] trends: cut {extra_trends} extra trend(s), {cut_paragraphs} extra paragraph(s) and {extra_lead} extra lead-in "
                        "paragraph(s) so the section keeps its shape of three trends with three paragraphs each")
        s["paragraphs"] = out
    return sections


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


def name_outside_sources(sections: list[dict], inp: planning.PlanInputs, logger: logging.Logger) -> list[dict]:
    """A sentence that carries a figure from an outside source must name that source itself (never CMI). The writer
    sometimes names the source in one sentence and gives its second figure in the next: the unit of naming is one
    line of a paragraph, so where exactly one outside source owns the sentence's figures and neither the sentence
    nor an earlier sentence of the same line named it, the source's name is appended before the closing punctuation.
    Nothing else is rewritten; an ambiguous sentence is left for the check to report."""
    if inp is None:
        return sections
    import checks
    outside = []
    own_figs: list[set[str]] = []      # figures of facts that name no outside source (the report's, or a plain statement)
    wording_by_source: dict[str, tuple[str, str]] = {}   # full source -> (fact text, fact detail)
    for i in inp.items.values():
        if i.get("source") in ("", "CMI report", None):
            own = {n for n in checks._fact_numbers(i["text"]) if not re.fullmatch(r"(?:19|20)\d\d", n)}
            if own:
                own_figs.append(own)
    for i in inp.items.values():
        if i["kind"] in ("size", "forecast", "cagr", "evidence") and i.get("source") not in ("", "CMI report", None):
            # Ownership uses digits only: a spelled small number ("two") is prose, not an identifying figure.
            figs = {n for n in checks._fact_numbers(i["text"]) | checks._fact_numbers(i.get("detail", ""))
                    if not re.fullmatch(r"(?:19|20)\d\d", n)}
            if figs:
                outside.append((re.sub(r",\s*(?:\d{4}-\d{2}|undated)\s*$", "", i["source"]).strip(), i["source"], figs))
                prev = wording_by_source.setdefault(i["source"], ("", ""))
                wording_by_source[i["source"]] = (f"{prev[0]} {i['text']}", f"{prev[1]} {i.get('detail', '') or ''}")
    if not outside:
        return sections
    end_re = re.compile(r"([.!?][\"')\]]*)$")
    for s in sections:
        if not s.get("sop") or s["sop"][0] not in planning.ORDER:
            continue
        label, fixed = s["sop"][0], 0
        paragraphs = []
        for p in s["paragraphs"]:
            lines = []
            for line in p.split("\n"):
                named: set[str] = set()     # outside sources named in earlier sentences of this line
                out = []
                for sent in [x for x in _SENTENCE_SPLIT_RE.split(line) if x.strip()]:
                    nums = {n for n in checks._fact_numbers(sent) if not re.fullmatch(r"(?:19|20)\d\d", n)}
                    # A token shared by two outside facts gives the weaker one a half-claim on the sentence
                    # ("9" owned by {9, 150} and {9, 40} alike): the fact with the MOST figures in common
                    # with the sentence is the real owner, the rest are coincidence.
                    best = max((len(nums & figs) for _, _, figs in outside if nums & figs), default=0)
                    # A number the report itself states (varied or not) can also turn up in a news item; when the report's own fact
                    # shares at least as many figures with the sentence, the sentence is the report's and no news source is credited.
                    if best and max((len(nums & figs) for figs in own_figs), default=0) >= best:
                        best = 0
                    owners = {(name, full) for name, full, figs in outside
                              if nums & figs and len(nums & figs) == best}
                    # A second tie-break, only when candidates from more than one distinct source still
                    # stand: the fact whose own wording the sentence keeps — the same content-word overlap
                    # checks.v2_evidence_attribution_problems applies — owns the sentence; a wording tie
                    # keeps the old behaviour (an ambiguous sentence is left for the check to report).
                    if len({full for _, full in owners}) > 1:
                        best_wording = max((checks._wording_overlap(sent, *wording_by_source[full])
                                            for _, full in owners), default=0)
                        owners = {(name, full) for name, full in owners
                                  if checks._wording_overlap(sent, *wording_by_source[full]) == best_wording}
                    low = sent.lower()
                    if owners and not any(checks._names_source(low, full) for _, full in owners) and len({n for n, _ in owners}) == 1:
                        name, full = next(iter(owners))
                        if not any(o_full in named for _, o_full in owners):
                            m = end_re.search(sent.rstrip())
                            sent = (sent.rstrip()[:m.start()] + f", per {name}" + sent.rstrip()[m.start():]) if m else sent.rstrip() + f", per {name}"
                            named.add(full)
                            fixed += 1
                    named |= {o_full for _, o_full, _figs in outside if checks._names_source(low, o_full)}
                    out.append(sent)
                if out:
                    lines.append(" ".join(out))
            if lines:
                paragraphs.append("\n".join(lines))
        if fixed:
            logger.info(f"[write] named the source in {fixed} sentence(s) of {label} that carried its figure without it")
        s["paragraphs"] = paragraphs
    return sections


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
        cleaned = _clean_sections([{**raw, "heading": ps["heading"], "sop": [ps["sop"]]}]) if raw is not None else []
        if not cleaned:
            # a planned section never vanishes: it keeps its heading with an editor note where its text belongs
            logger.warning(f"[write] the model {'left out' if raw is None else 'returned an empty'} {ps['sop']} section; "
                           "it keeps its heading with an editor note")
            market.append({"heading": ps["heading"], "sop": [ps["sop"]], "paragraphs": [editor_note(ps["sop"])]})
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
    client_list = list(getattr(inp, "clients", None) or []) if config.CLIENT_REWRITE else []
    if client_list:
        # the writer restated the client paragraph: every link is one correct token in the opening (the URLs stay in code)
        sections = clients.normalize_tokens(sections, client_list, logger)
        allowed_text = clients.source_text(client_list)
    else:
        # the placement repair runs before the other strips so they all see the token in the shape the checks expect
        sections = repair_sponsor_placement(sections, logger, sponsor.text)
        allowed_text = sponsor.text
    sections = strip_untraced_figures(sections, inp, allowed_text, logger)
    sections = strip_repeat_figures(sections, inp, logger)
    sections = strip_repeat_evidence(sections, inp, logger)
    sections = strip_reader_filler(sections, logger)
    sections = name_outside_sources(sections, inp, logger)
    sections = enforce_trend_shape(sections, logger)
    if not any(s["sop"] and s["sop"][0] in planning.ORDER for s in sections):
        raise RuntimeError("the writer returned no market sections")
    title = _plain(plan["title"])
    if client_list:
        problems = clients.link_problems(sections, client_list) + (
            _link_placement_problems(clients.plain_sections(sections), "", report.name) if getattr(inp, "link_report", True) else [])
    else:
        problems = _sponsor_problems(sections, sponsor.text) + (
            _link_placement_problems(sections, sponsor.text, report.name) if getattr(inp, "link_report", True) else [])
    if problems:
        logger.warning(f"[write] placement problems found (no retry is run): {'; '.join(problems)}")
    if not client_list and _sponsor_location_problem(sections):
        logger.warning("[write] inserting the sponsor placeholder as intro paragraph 2 so the paid link is not lost")
        sections = _force_sponsor_placeholder(sections)
    citations = evidence_citations(sections, inp)
    logger.info(f"[write] news evidence used (found in code): {sorted({c['id'] for c in citations})}")
    return written, title, sections, citations
