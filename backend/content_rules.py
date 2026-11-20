"""Content rules judged on the finished draft, in code: no other region inside the U.S. section, no negative wording,
and no point made twice. Each function is pure and returns the problems it found as short sentences for the editor;
checks.run_checks_v2 turns them into hard checks. Nothing here calls a model."""
import re

import polarity
import us_states
import sentsplit

_SENTENCE_SPLIT_RE = re.compile(sentsplit.BOUNDARY + r"(?=[A-Z0-9\"“])|\n+")

# Regions and countries that must not appear in the U.S. section. North America alone is allowed when the same sentence
# also names the U.S. (a sentence that places the U.S. inside its region is still about the U.S.).
_OTHER_REGIONS = (
    "asia-pacific", "asia pacific", "apac", "asia", "europe", "european", "latin america", "middle east", "africa",
    "emea", "gcc", "oceania", "china", "chinese", "india", "indian", "japan", "japanese", "south korea", "germany",
    "german", "france", "french", "united kingdom", "u.k.", "britain", "british", "italy", "spain", "brazil", "mexico",
    "canada", "canadian", "australia", "russia", "saudi arabia", "uae", "singapore", "indonesia", "vietnam",
    "rest of the world",
)
_OTHER_RE = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(r) for r in sorted(_OTHER_REGIONS, key=len, reverse=True)) + r")(?![a-z])", re.I)
_NORTH_AMERICA_RE = re.compile(r"north america", re.I)
_NA_COUNTRIES_RE = re.compile(r"\b(?:canad(?:a|ian)|mexic(?:o|an))\b", re.I)
_US_RE = re.compile(r"\bU\.S\.?|\bUSA\b|\bUS\b|(?i:united states)|\bAmerican\b")

_STOP = frozenset({
    "that", "this", "with", "from", "have", "their", "which", "will", "been", "were", "also", "more", "most", "such",
    "than", "into", "over", "while", "where", "about", "across", "within", "other", "these", "those", "market",
    "markets", "global", "growth", "demand", "industry",
})


def _sentences(paragraph: str) -> list[str]:
    return [s.strip(" -\t") for s in _SENTENCE_SPLIT_RE.split(paragraph or "") if s and s.strip(" -\t")]


def _is_us(section: dict) -> bool:
    return "country_us" in (section.get("sop") or [])


def _is_market(section: dict) -> bool:
    sop = section.get("sop") or []
    return bool(sop) and sop[0] in {"report_context", "trends", "segmentation", "subsegment", "country_us", "key_players", "conclusion"}


def us_only_problems(sections: list[dict]) -> list[str]:
    """Sentences in the U.S. section that name another region or country. A sentence naming North America is fine when
    it also names the U.S."""
    out: list[str] = []
    for s in sections:
        if not _is_us(s):
            continue
        # a North America analysis (the fallback when U.S. data is thin) may name North America and its countries
        north_america = bool(_NORTH_AMERICA_RE.search(s.get("heading") or ""))
        for p in s["paragraphs"]:
            for sentence in _sentences(p):
                if north_america:
                    sentence = _NA_COUNTRIES_RE.sub(" ", _NORTH_AMERICA_RE.sub(" ", sentence))
                # a U.S. state is the U.S. ("New Mexico" is not Mexico)
                names = {m.group(0).lower() for m in _OTHER_RE.finditer(us_states.STATE_RE.sub(" ", sentence))}
                if _NORTH_AMERICA_RE.search(sentence) and not _US_RE.search(re.sub(_NORTH_AMERICA_RE, " ", sentence)):
                    names.add("north america")
                if names:
                    out.append(f"U.S. section names {', '.join(sorted(names))}: {sentence[:90]!r}")
    return out


def negative_wording_problems(sections: list[dict], faq: list[dict] = (), market_name: str = "") -> list[str]:
    """Sentences in the market sections and the FAQ whose wording is negative (setbacks, restraints, risks)."""
    out: list[str] = []
    ignore = polarity.market_words(market_name)
    for s in sections:
        if not _is_market(s):
            continue
        label = (s.get("heading") or "").strip() or s["sop"][0]
        for p in s["paragraphs"]:
            for sentence in _sentences(p):
                hits = polarity.negative_hits(sentence, ignore)
                if hits:
                    out.append(f"{label}: negative wording {hits}: {sentence[:90]!r}")
    for item in faq:
        for key in ("question", "answer"):
            hits = polarity.negative_hits(str(item.get(key) or ""), ignore)
            if hits:
                out.append(f"FAQ {key}: negative wording {hits}: {str(item.get(key))[:90]!r}")
    return out


def _content_stems(sentence: str) -> frozenset[str]:
    # British and American spellings are one word ("organisation" / "organization", "optimise" / "optimize"); the same
    # rewrite on both sides of every comparison, so a word it touches that has no American form ("enterprise") still matches
    text = re.sub(r"is(ation|e|ed|es|ing)\b", r"iz\1", sentence.lower())
    return frozenset(
        w[:-1] if len(w) > 4 and w.endswith("s") else w
        for w in re.findall(r"[a-z0-9]+", text) if len(w) > 3 and w not in _STOP
    )


def repeated_point_problems(
    sections: list[dict], threshold: float = 0.55, min_stems: int = 6, market_name: str = "", own_terms=(),
) -> list[str]:
    """Two sentences in DIFFERENT market sections that make the same point: their content words overlap by at least
    `threshold` of the shorter sentence's. A point is made once in the article.

    `own_terms` are words every article on the topic must carry — the market's own name and each analysed
    sub-segment's category name. The sub-segment analysis has to open by naming its category and the split it leads
    ("Online delivery leads the mode split ... at 55.9%"), and the segmentation list has to name the same category in
    the same split, so that shared vocabulary says nothing about whether the point repeats. Subtracting it (as the
    market's own name already is) lets a real repeat — the same reasoning said twice — still be caught. The subtraction
    is for the overlap only: the min-stems gate and the shorter-sentence denominator use the full stems, because a
    sentence that names its category is not thereby too short to compare (subtracting first let a genuine repeat that
    happens to name the category drop out of the check)."""
    seen: list[tuple[str, frozenset[str], frozenset[str], str]] = []
    out: list[str] = []
    own = _content_stems(market_name)       # the market's own name is in every section and says nothing about the point
    for term in own_terms:
        own |= _content_stems(str(term or ""))
    for s in sections:
        if not _is_market(s):
            continue
        label = (s.get("heading") or "").strip() or s["sop"][0]
        for p in s["paragraphs"]:
            for sentence in _sentences(p):
                full = _content_stems(sentence)
                if len(full) < min_stems:
                    continue
                stems = full - own          # the category name is required wording, so it never counts as overlap
                for other_label, other_stems, other_full, other in seen:
                    if other_label == label:
                        continue
                    shared = len(stems & other_stems)
                    if shared / min(len(full), len(other_full)) >= threshold:
                        out.append(f"{label} repeats {other_label}: {sentence[:70]!r} / {other[:70]!r}")
                        break
                seen.append((label, stems, full, sentence))
    return out


_EVIDENCE_MIN_SHARED_WORDS = 3


def _sentence_citations(
    sentence: str, items: list[dict], facts_of: dict[str, frozenset[str]], own: frozenset[str],
) -> list[str]:
    """The evidence ids one sentence cites: of the items the sentence's matcher identifies (pipeline._evidence_hits: a name
    or figure that tells the item from the others, never a word several items share) those whose fact the sentence also
    shares _EVIDENCE_MIN_SHARED_WORDS content words with, the market's own name excluded from both sides, and of those only
    the best supported: the strongest identification, then the most shared words. A sentence is about one development, so
    the other items it brushes against (the same sector's vocabulary, a shared agency name) are not its citations.
    The market's name is in every section's lead and says nothing about which evidence a sentence restates: counted as
    content, it satisfied the overlap test for any fact that mentions the market's products, and a section that only named
    the market became the fact's "first citation" (found live), whose later true citations the repeat-evidence stripper
    then removed. The same stripper cut true Oregon and Illinois transport-department sentences because a shared
    "Transportation" and five on-topic words made each sentence cite five items (found live)."""
    import pipeline
    stems = _content_stems(sentence) - own
    fact_of = {it["id"]: it["fact"] for it in items}
    scored = []
    for eid, strength in pipeline._evidence_hits(sentence, items).items():
        shared = _shared_words(stems, facts_of[eid], fact_of[eid])
        # a short fact ("Acme launched a new product") has fewer words beyond its names than the minimum: all of them then
        if shared >= min(_EVIDENCE_MIN_SHARED_WORDS, max(1, _shared_words(facts_of[eid], facts_of[eid], fact_of[eid]))):
            scored.append((strength, shared, eid))
    if not scored:
        return []
    best = max((strength, shared) for strength, shared, _ in scored)
    return sorted(eid for strength, shared, eid in scored if (strength, shared) == best)


def _shared_words(sentence_stems: frozenset[str], fact_stems: frozenset[str], fact: str) -> int:
    """How much of a fact's content a sentence restates. A name says who the fact is about, not what it states: a company's
    name may reappear in another section (the key players list names a company whose launch backs a trend) as long as the
    fact itself is not restated. So the first of the fact's names the sentence carries counts nothing, and each further one
    counts one (two of the fact's names together, "Asana" and "StackAI", are the event itself), and the fact's other words
    count when the sentence carries the same root ("acquisition" for "acquired", "expansion" for "expand"). Found live:
    "Asana, Atlassian JIRA, Microsoft, Monday and Pivotal Software are the key players ..." cited Asana's StackAI acquisition,
    used in a trend, on the name plus two generic words; earlier a roll of player names, "Epic Systems, Oracle Health, ...",
    cited the FTC's investigation into Epic Systems on the name's own words."""
    import pipeline
    names = [_content_stems(phrase) for _, phrase in pipeline._fact_entities(fact)]
    name_words = frozenset().union(*names)
    present = sum(1 for n in names if n and n & sentence_stems)
    words = sentence_stems - name_words
    content = {w for w in fact_stems - name_words if any(_same_root(w, s) for s in words)}
    return len(content) + max(0, present - 1)


def _same_root(a: str, b: str) -> bool:
    """One word family by its first five letters ("acquired" / "acquisition", "charging" / "charger"). Loose on its own,
    so it only measures overlap with an item the sentence has already been tied to by a name or figure of its own."""
    return a == b or (len(a) >= 5 and len(b) >= 5 and a[:5] == b[:5])


def _fact_stems(items: list[dict], market_name: str = "") -> tuple[dict[str, frozenset[str]], frozenset[str]]:
    """Each fact's content stems and the market's own stems: the two sides of evidence_reuse's overlap test."""
    own = _content_stems(market_name)
    return {it["id"]: _content_stems(it["fact"]) - own for it in items}, own


def evidence_cited_ids(text: str, items: list[dict], market_name: str = "") -> set[str]:
    """Every evidence id `text` cites, by the same standard as evidence_reuse. The repeat-evidence strip uses it
    to tell a section that would lose its every citation from one that keeps some."""
    facts_of, own = _fact_stems(items, market_name)
    out: set[str] = set()
    for line in (text or "").split("\n"):
        for sentence in (x for x in sentsplit.SPLIT_RE.split(line) if x.strip()):
            out |= set(_sentence_citations(sentence, items, facts_of, own))
    return out


def evidence_homes(plan: dict | None) -> dict[str, tuple[str, int | None]]:
    """Where the plan put each evidence id: {id: (section sop, trend block index or None)}. A trends fact sits in the trend
    group the plan gave it (groups are the blocks, in order); any other section's fact sits in that section. An empty dict
    when there is no plan, and evidence_reuse then judges by order alone."""
    homes: dict[str, tuple[str, int | None]] = {}
    for ps in (plan or {}).get("sections") or []:
        sop = ps.get("sop")
        if sop == "trends" and ps.get("trend_groups"):
            for gi, g in enumerate(ps["trend_groups"]):
                for fid in g.get("facts") or []:
                    homes.setdefault(str(fid), (sop, gi))
        for fid in ps.get("facts") or []:
            homes.setdefault(str(fid), (sop, None))
    return homes


def evidence_reuse(sections: list[dict], items: list[dict], market_name: str = "",
                   homes: dict[str, tuple[str, int | None]] | None = None) -> list[dict]:
    """The sentences that cite a piece of news evidence already cited earlier in ANOTHER market section, or (in the trends
    section) in another trend block. `items` are the news evidence ({id, fact}); a sentence cites the best-supported of
    those that pipeline._evidence_hits identifies in it and that it shares at least _EVIDENCE_MIN_SHARED_WORDS content words
    with beyond the market's own name (see _sentence_citations: a sentence that only names the same company is a different
    point; a sentence that only names the market is no citation at all). The trends' lead-in belongs to the section
    but to no trend block. `sections` carry the trend sub-headings (subheads) so the blocks can be told apart.
    `homes` (evidence_homes of the plan) says where each item was planned. A sentence that cites an item planned for the
    place it stands in (its section, for a trend its trend block) is that item's citation there and never a repeat, and
    then cites nothing else: what it shares with other items is vocabulary. Only a sentence that cites none of its own
    place's items and an item already cited elsewhere is a repeat. A cited item's first citation is its planned place
    once it stands there. Each row is {id, first, where, at: (section, paragraph, line, sentence), sentence}; the first
    citation is never a row."""
    import subheads
    facts_of, own = _fact_stems(items, market_name)
    homes = homes or {}
    cited: dict[str, tuple[str, int | None]] = {}
    rows: list[dict] = []
    for si, s in enumerate(sections):
        if not _is_market(s):
            continue
        sop, block = s["sop"][0], None
        for pi, paragraph in enumerate(s["paragraphs"]):
            if subheads.is_subhead(paragraph):
                block = 0 if block is None else block + 1
                continue
            for li, line in enumerate(paragraph.split("\n")):
                for ci, sentence in enumerate(x for x in sentsplit.SPLIT_RE.split(line) if x.strip()):
                    found = _sentence_citations(sentence, items, facts_of, own)
                    here = [e for e in found if homes.get(e) and homes[e][0] == sop and (sop != "trends" or homes[e][1] == block)]
                    if here:
                        for eid in here:
                            cited[eid] = (sop, block)
                        continue
                    for eid in found:
                        first_sop, first_block = cited.setdefault(eid, (sop, block))
                        if first_sop == sop == "trends":
                            if block is None:
                                continue
                            if first_block is None:
                                cited[eid] = (sop, block)
                                continue
                            repeated = block != first_block
                        else:
                            repeated = first_sop != sop
                        if repeated:
                            first = first_sop if first_sop != "trends" else f"trend {first_block + 1}" if first_block is not None else "trends"
                            where = sop if sop != "trends" else f"trend {block + 1}" if block is not None else "trends"
                            rows.append({"id": eid, "first": first, "where": where, "at": (si, pi, li, ci), "sentence": sentence})
    return rows


def evidence_reuse_problems(sections: list[dict], items: list[dict], market_name: str = "",
                            homes: dict[str, tuple[str, int | None]] | None = None) -> list[str]:
    return [f"{r['id']} is cited in {r['first']} and again in {r['where']}: {r['sentence'][:80]!r}"
            for r in evidence_reuse(sections, items, market_name, homes)]


# ------------------------------------------------------------------------------------------- the text reads whole

# A sentence cut off at an abbreviation, a paragraph that opens on a word pointing at something that is not there, or a
# line of the generator's own process wording are all damage to the reader's text, never style. The cleanup steps that remove
# a sentence can leave them behind, so the finished draft is checked for them (nothing is retried; the editor sees the problem).
_OPENS_ON_REFERENCE_RE = re.compile(r"^(?:it|its|they|their|he|she|this|that|these|those|such|on that|in that|from that|at that|by that)\b", re.I)
_CUT_ON_ABBREVIATION_RE = re.compile(r"\b(?:Inc|Ltd|Corp|Co|No|Nos|St|Mt|Ft|Mr|Mrs|Ms|Dr|Prof|Jr|Sr|vs)\.$")
_PROCESS_WORDING_RE = re.compile(
    r"\bfacts?\s+(?:provided|supplied|listed|given)\b|\b(?:provided|supplied|listed)\s+facts?\b|\bmarket's\s+full\s+name\b", re.I)


def _is_heading_line(paragraph: str) -> bool:
    return paragraph.lstrip().startswith("#")


def text_reads_whole_problems(sections: list[dict], faq: list[dict] = ()) -> list[str]:
    """Damaged text in the finished draft and FAQ: a paragraph that ends on an abbreviation (a cut sentence), starts in lower
    case (a fragment), or contains generator process wording; and a paragraph or FAQ answer that is the FIRST under its
    heading or question yet opens on a pronoun or 'that ...' reference with nothing before it to refer to."""
    out: list[str] = []
    for s in sections:
        label = (s.get("heading") or "").strip() or (s["sop"][0] if s.get("sop") else "the opening")
        first_under_heading = True
        for p in s["paragraphs"]:
            if _is_heading_line(p):
                first_under_heading = True
                continue
            text = p.strip()
            if not text:
                continue
            if text[0].islower():
                out.append(f"{label}: a paragraph starts in lower case, a fragment: {text[:70]!r}")
            if _CUT_ON_ABBREVIATION_RE.search(text):
                out.append(f"{label}: a sentence is cut off at an abbreviation: ...{text[-60:]!r}")
            if _PROCESS_WORDING_RE.search(text):
                out.append(f"{label}: process wording reached the text: {_PROCESS_WORDING_RE.search(text).group(0)!r}")
            # the transition follows the opening and is written to refer back to it ("That buying question now sits inside ...")
            if first_under_heading and "transition" not in (s.get("sop") or []) and _OPENS_ON_REFERENCE_RE.match(text):
                out.append(f"{label}: opens on a reference to something not stated: {text[:70]!r}")
            first_under_heading = False
    for item in faq:
        answer = str(item.get("answer") or "").strip()
        label = f"FAQ {str(item.get('question') or '')[:50]!r}"
        if not answer:
            continue
        if answer[0].islower():
            out.append(f"{label}: the answer starts in lower case, a fragment: {answer[:70]!r}")
        if _CUT_ON_ABBREVIATION_RE.search(answer):
            out.append(f"{label}: the answer is cut off at an abbreviation: ...{answer[-60:]!r}")
        if _PROCESS_WORDING_RE.search(answer):
            out.append(f"{label}: process wording reached the answer: {_PROCESS_WORDING_RE.search(answer).group(0)!r}")
        if _OPENS_ON_REFERENCE_RE.match(answer):
            out.append(f"{label}: the answer opens on a reference to something not stated: {answer[:70]!r}")
    return out
