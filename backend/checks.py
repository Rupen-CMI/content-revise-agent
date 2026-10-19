import re
from dataclasses import dataclass
from datetime import date

import freshness
from prompts import CMI_LINK_PATTERN, SPONSOR_TOKEN

_FIGURE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|percent|bn\b|billion|mn\b|million|tn\b|trillion)", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

# A secondary keyword's words are not frozen in ORDER ("wealth management platform market size" may
# become "the size of the Wealth Management Platform Market" for natural, non-repetitive prose), but
# every one of its own words must still appear — the writer may REORDER, not substitute a synonym or
# paraphrase. So "used" is judged by whether every content word of the phrase appears close together
# in the text, in any order and any inflection — not by an exact substring match (which would force
# the same rigid wording every time it's used), and not by a looser "same idea" match either (which
# was tried and let the model swap "segmentation" for "axes", "size" for "how big", defeating the
# keyword's actual SEO purpose).
_KEYWORD_STOPWORDS = frozenset({
    "the", "a", "an", "of", "in", "for", "and", "is", "to", "this", "that", "its", "on", "at", "as",
})
_KEYWORD_MATCH_WINDOW = 8  # base window; scaled up for longer phrases, see count_keyword_uses


def _keyword_content_words(phrase: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", phrase.lower()) if w not in _KEYWORD_STOPWORDS]


def _stem(word: str) -> str:
    """Light inflection-folding (plural/verb endings only) — not a real stemmer, just enough to
    match "segments"/"segment" or "sized"/"sizing" without a dependency. A trailing silent "e" is
    then also stripped (see below) so a base word and its suffixed forms always land on the same
    stem regardless of whether the base itself ends in "e" ("size") or not ("box") — stemming
    "sizes" to "siz" while "size" stays "size" would otherwise leave the two never matching as the
    same keyword concept, which is what originally happened here."""
    for suf in ("ing", "ed", "es", "s"):
        if word.endswith(suf) and len(word) - len(suf) >= 3:
            word = word[: -len(suf)]
            break
    if word.endswith("e") and len(word) >= 4:
        word = word[:-1]
    return word


def count_keyword_uses(text: str, phrase: str, window: int | None = None) -> int:
    """How many times `phrase`'s meaning appears in `text`: every content word of the phrase
    (stemmed) must appear within a window of `window` words of each other, in any order. Matches
    are non-overlapping (each word position counts toward at most one use), so a paragraph that
    uses the same keyword twice over counts as 2, not more. The window scales with the phrase's own
    length (base + 2 per content word) — a short phrase like "market size" needs little slack, but
    a longer one like "<market> market competitive landscape" (6 content words) needs more room for
    the connecting words a natural rewording inserts between its parts."""
    words = re.findall(r"[a-z0-9']+", text.lower())
    stems = [_stem(w) for w in words]
    target = {_stem(w) for w in _keyword_content_words(phrase)}
    if not target:
        return 0
    if window is None:
        window = _KEYWORD_MATCH_WINDOW + 2 * max(0, len(target) - 2)
    n = len(words)
    count = 0
    i = 0
    while i < n:
        seen: set[str] = set()
        matched_end = None
        for j in range(i, min(i + window, n)):
            if stems[j] in target:
                seen.add(stems[j])
            if seen == target:
                matched_end = j
                break
        if matched_end is not None:
            count += 1
            i = matched_end + 1
        else:
            i += 1
    return count


def keyword_used(text: str, phrase: str, window: int | None = None) -> bool:
    return count_keyword_uses(text, phrase, window) > 0


def secondary_keyword_in_paragraph(paragraph_text: str, market_name: str, suffix: str) -> bool:
    """A secondary keyword is built as "<market core> market <suffix>" (e.g. "Wealth Management
    Platform Market size"). count_keyword_uses' fixed word-window (~12-14 words for a keyword this
    long) is too narrow for how real analysis writing actually reads: a paragraph often names the
    market once, then discusses several sentences' worth of its drivers/share/competitive landscape
    without repeating the market name in every single sentence — exactly the natural variation the
    REORDER rule in WRITE_USER is trying to produce, not a miss. This checks the market's own words
    and the suffix's own words each appear SOMEWHERE in the paragraph (not clustered together),
    which is the right-sized unit: still ties the concept to this market (not a bare "the
    competitive landscape" that could belong to any industry), but doesn't demand the market's full
    name repeat inside every sentence that touches one of its secondary concepts."""
    low = paragraph_text.lower()
    market_words = _keyword_content_words(market_name)
    suffix_words = _keyword_content_words(suffix)
    if not market_words or not suffix_words:
        return False
    text_stems = {_stem(w) for w in re.findall(r"[a-z0-9']+", low)}
    market_ok = all(_stem(w) in text_stems for w in market_words)
    suffix_ok = all(_stem(w) in text_stems for w in suffix_words)
    return market_ok and suffix_ok


def secondary_keyword_spans_in_paragraph(paragraph_text: str, market_name: str, suffix: str) -> list[tuple[int, int]]:
    """Like secondary_keyword_in_paragraph, but returns the character span(s) of the SUFFIX word's
    own occurrence(s) in the paragraph (for UI highlighting) rather than just a yes/no — only
    returns spans at all if the market's own words are ALSO present somewhere in the paragraph
    (same gate as secondary_keyword_in_paragraph), so a bare suffix word that isn't actually tied
    to this market anywhere nearby is never highlighted as if it were a confirmed keyword use."""
    low = paragraph_text.lower()
    market_words = _keyword_content_words(market_name)
    suffix_words = _keyword_content_words(suffix)
    if not market_words or not suffix_words:
        return []
    tokens = list(re.finditer(r"[a-z0-9']+", low))
    stems = [_stem(m.group(0)) for m in tokens]
    text_stems = set(stems)
    if not all(_stem(w) in text_stems for w in market_words):
        return []
    target = {_stem(w) for w in suffix_words}
    if not target.issubset(text_stems):
        return []
    window = _KEYWORD_MATCH_WINDOW + 2 * max(0, len(target) - 2)
    return find_keyword_spans(paragraph_text, suffix, window=window)


def phrase_partially_named(text: str, phrase: str, min_fraction: float = 0.5) -> bool:
    """True if at least `min_fraction` of `phrase`'s own content words (stemmed) appear close
    together in `text`, without requiring every single one — used where a shorter, equally
    specific stand-in should count as naming the same thing (e.g. "customer data" standing in for
    "customer data management systems"), unlike count_keyword_uses/keyword_used which require all
    of a secondary keyword's content words since those are judged as a single SEO phrase."""
    content_words = _keyword_content_words(phrase)
    if not content_words:
        return False
    need = max(1, round(len(content_words) * min_fraction))
    window = _KEYWORD_MATCH_WINDOW + 2 * max(0, len(content_words) - 2)
    return count_keyword_uses(text, " ".join(content_words[:need]), window=window) > 0


def find_keyword_spans(text: str, phrase: str, window: int | None = None) -> list[tuple[int, int]]:
    """Same concept-matching algorithm as count_keyword_uses, but returns each match's own
    (start_char, end_char) span in `text` — from its first matched content word to its last —
    instead of just a count. Used to highlight a reworded keyword's actual words in the UI, since
    the keyword is a concept the writer may reorder/reword freely (see module docstring above), so
    a literal substring search for the phrase would miss almost every real use."""
    tokens = list(re.finditer(r"[a-z0-9']+", text.lower()))
    stems = [_stem(m.group(0)) for m in tokens]
    target = {_stem(w) for w in _keyword_content_words(phrase)}
    if not target:
        return []
    if window is None:
        window = _KEYWORD_MATCH_WINDOW + 2 * max(0, len(target) - 2)
    n = len(tokens)
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        seen: set[str] = set()
        first_idx = None
        matched_end = None
        for j in range(i, min(i + window, n)):
            if stems[j] in target:
                if first_idx is None:
                    first_idx = j
                seen.add(stems[j])
            if seen == target:
                matched_end = j
                break
        if matched_end is not None:
            spans.append((tokens[first_idx].start(), tokens[matched_end].end()))
            i = matched_end + 1
        else:
            i += 1
    return spans


# A paragraph can hold a short lead-in sentence followed by parallel items (3 trends, a
# segmentation breakdown) as "\n- " lines within the SAME paragraph string, instead of as separate
# paragraph objects — this is a presentation convention (docx_io/app.js render it as a real
# bulleted list) that every existing text-based check (word counts, keyword matching, figure
# detection) already tolerates unchanged, since they all operate on plain text where "\n" is just
# more whitespace. The one place that needs to know about the convention is anything that counts
# how many of a repeated item (trends, sub-segments) a section provides — see count_items_in.
_BULLET_LINE_RE = re.compile(r"^-\s+")


def count_items_in(paragraphs: list[str]) -> int:
    """How many 'items' a list of paragraph strings contains, counting a paragraph with bullet
    lines as one item per bullet rather than one item for the whole paragraph. Mirrors
    docx_io._split_bullet_lines's convention without importing the presentation module."""
    total = 0
    for p in paragraphs:
        bullets = sum(1 for line in p.split("\n") if _BULLET_LINE_RE.match(line))
        total += bullets if bullets else 1
    return total

# Mirrors search.py's gate-token approach: strip words too generic to identify a specific
# market, so "market"/"services" matching everywhere doesn't make every section look on-topic.
_GENERIC_MARKET_WORDS = frozenset({
    "market", "markets", "global", "industry", "the", "and", "of", "for", "by", "in", "on", "a", "an",
    "to", "with", "based", "services", "service", "solutions", "solution", "systems", "system", "other",
})


def _market_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _GENERIC_MARKET_WORDS}


# Words that make a title SOUND like market intelligence without saying what the article is about
# (plus ordinary filler), ignored when judging whether a title names the article's real subject.
_GENERIC_TITLE_WORDS = frozenset({
    "intelligence", "outlook", "trends", "trend", "analysis", "insights", "insight", "overview",
    "forecast", "growth", "report", "reports", "research", "guide", "future", "how", "what", "why",
    "when", "your", "you", "like", "from", "with", "into", "that", "this", "are", "can", "new",
    "start", "tips", "best", "top", "key", "behind", "about", "inside", "look", "role", "rise",
    "pro", "year", "ahead", "next", "ultimate", "complete",
})


# Single words from CMI's own offering vocabulary (prompts.CMI_KEYWORDS) that read as market
# intelligence when they appear in a title; "market" itself is excluded since every title has it.
_CMI_TITLE_TERMS = frozenset({
    "trends", "trend", "intelligence", "analysis", "landscape", "forecast", "forecasting", "drivers",
    "research", "segmentation", "insights", "sizing", "competitive", "players",
})


def _title_subject_stems(title: str, market_name: str) -> set[str]:
    """4-letter stems of a title's content words, minus the market's own name and generic
    market-intelligence filler. 4-letter stems let 'teen' match 'teenager' and 'calendars' match
    'calendar' without a stemming library."""
    skip = _market_tokens(market_name) | _GENERIC_TITLE_WORDS | _GENERIC_MARKET_WORDS
    return {w[:4] for w in re.findall(r"[a-z]+", title.lower()) if len(w) >= 4 and w not in skip}


# Capitalized words common enough in business writing that matching ONE alone proves nothing (a
# first word like "Digital" or "Bank" recurs across unrelated facts/paragraphs) — only a run of
# TWO OR MORE consecutive capitalized words ("Ping An", "Kinsale Capital") is treated as a real
# proper-noun signature; a lone generic-sounding capitalized word is excluded entirely rather than
# risk a coincidental match.
_FACT_GENERIC_CAPS = frozenset({
    "the", "a", "an", "to", "of", "in", "on", "for", "by", "its", "it", "as", "with", "and", "or",
    "digital", "bank", "banks", "company", "companies", "firm", "firms", "platform", "platforms",
    "management", "technology", "investment", "investments", "financial", "new", "group", "global",
    "market", "wealth", "service", "services", "solutions", "solution", "capital", "fund", "funds",
    "advisory", "advisors", "advisor", "partners", "partnership", "head", "united", "states",
    "research", "ventures", "holdings", "international", "systems", "custom", "model",
    # Nationality/region adjectives — common enough that one coincidentally recurring elsewhere
    # proves nothing (found live: "Indian" from a Zomint fact false-matched a draft sentence about
    # a different, unrelated company, "South Indian Bank").
    "indian", "irish", "nordic", "european", "american", "asian", "chinese", "japanese", "korean",
    "british", "german", "french", "canadian", "australian", "african", "india", "ireland", "asia",
})


def _fact_signature_tokens(fact: str) -> set[str]:
    """Distinctive signatures for one selected-evidence fact: each non-generic capitalized word
    (company/person name fragments — "Kestrel", "Vanguard", "Paul", "Arnold") plus the first
    two-word slice of each capitalized run ("ping an", "bny pershing") so a paraphrase that keeps
    just the entity's own name, without the generic words following it ("Digital Bank", "Group"),
    still matches — any number/percentage counts too. A single GENERIC capitalized word ("Digital",
    "Bank", "Group") is excluded even alone, since business prose is full of them and matching one
    in isolation proved nothing about whether THIS specific fact was used (an earlier version of
    this check false-positived on exactly that)."""
    runs = re.findall(r"\b[A-Z][\w&.'-]*(?:\s+[A-Z][\w&.'-]*)*\b", fact)
    sigs: set[str] = set()
    for r in runs:
        words = r.split()
        for w in words:
            if w.lower() not in _FACT_GENERIC_CAPS and len(w) > 1:
                sigs.add(w.lower())
        if len(words) >= 2:
            sigs.add(" ".join(words[:2]).lower())
    numbers = set(re.findall(r"\d[\d.,]*%?", fact))
    sigs |= numbers
    if sigs:
        return sigs
    # Fallback for a fact with no proper noun and no number at all (e.g. a plain trend statement
    # like "advisors and record keepers are pushing forward convergence of wealth and retirement")
    # — found live: such a fact can be genuinely, clearly used (even near-verbatim) in the draft,
    # but with an empty signature set the check always reported it as unused regardless of reality,
    # since "bool(sig) and ..." short-circuits to False whenever sig is empty. Fall back to
    # content-word bigrams (two consecutive non-generic words) — distinctive enough to avoid
    # matching unrelated sentences, unlike a single common word, while not requiring a proper noun.
    words = [w for w in re.findall(r"[a-zA-Z']+", fact.lower()) if w not in _FACT_GENERIC_CAPS and len(w) > 2]
    return {f"{a} {b}" for a, b in zip(words, words[1:])}

_SOP_TAGS = (
    ("trends", "Market trends section"),
    ("report_context", "CMI report context"),
    ("segmentation", "Market segmentation"),
    ("country_us", "U.S. country analysis"),
    ("key_players", "Key players section"),
    ("conclusion", "Conclusion section"),
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


def _words(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text))


def _count_syllables(word: str) -> int:
    """Vowel-group heuristic (standard approximation used by Flesch scoring tools): count runs
    of consecutive vowels, drop a silent trailing "e", floor of 1 per word. Not linguistically
    exact, but Flesch Reading Ease itself is only ever an approximation — this avoids adding a
    pip dependency (e.g. pyphen/textstat) that start.bat would have to install on a non-technical
    user's laptop with no admin rights, for a score whose own formula is already a rough proxy."""
    word = word.lower()
    groups = re.findall(r"[aeiouy]+", word)
    count = len(groups)
    if word.endswith("e") and not word.endswith("le") and count > 1:
        count -= 1
    return max(1, count)


def _flesch_reading_ease(text: str) -> float | None:
    """Standard Flesch Reading Ease: 206.835 - 1.015*(words/sentences) - 84.6*(syllables/words).
    Higher = easier to read (90-100 very easy / 5th grade, 60-70 plain English / 8th-9th grade,
    0-30 very difficult / graduate level). Returns None if there's no text to score."""
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"\b[a-zA-Z'-]+\b", text)
    if not sentences or not words:
        return None
    syllables = sum(_count_syllables(w) for w in words)
    return 206.835 - 1.015 * (len(words) / len(sentences)) - 84.6 * (syllables / len(words))


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _NUMBER.findall(text)}


def _longest_shared_run(a: str, b: str, n: int = 8) -> int:
    """Longest run of shared n-grams (word-level) between two texts — a cheap plagiarism-style
    signal for "the model wrote out the real sponsor paragraph instead of the placeholder"."""
    words_a = re.findall(r"\w+", a.lower())
    words_b = re.findall(r"\w+", b.lower())
    grams_b = {tuple(words_b[i:i + n]) for i in range(len(words_b) - n + 1)}
    return sum(1 for i in range(len(words_a) - n + 1) if tuple(words_a[i:i + n]) in grams_b)


def _verbatim_overlap(
    paragraphs: list[str], sources: list[tuple[str, str]], report_name: str = "", n: int = 10,
) -> tuple[float, list[str]]:
    """Originality check against one or more source texts (the original guest article, the
    CMI report text). Returns (overlap_pct, example_snippets): overlap_pct is the percentage
    of draft words (excluding the sponsor placeholder) that fall inside some run of >= n
    consecutive words also found verbatim in at least one source. A reused NUMBER or a short
    shared phrase does not trip this — numeric facts from the CMI report are allowed reuse —
    only a long run of identical consecutive words does, which is what "copied a line" (not
    "reused a figure") actually looks like. report_name is stripped from both the draft
    paragraph and the source text before comparing: the report's own name is REQUIRED to
    appear verbatim as the CMI_LINK anchor text (see the "CMI link anchor names the confirmed
    report" check above), so that one unavoidable, intentional overlap must not count here."""

    def _strip_name(text: str) -> str:
        return re.sub(re.escape(report_name), " ", text, flags=re.IGNORECASE) if report_name else text

    source_grams: dict[str, set[tuple[str, ...]]] = {}
    for label, text in sources:
        words = re.findall(r"\w+", _strip_name(text).lower())
        source_grams[label] = {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}

    total_words = 0
    covered_words = 0
    examples: list[str] = []
    for p in paragraphs:
        if p.strip() == SPONSOR_TOKEN:
            continue
        words = re.findall(r"\w+", _strip_name(p).lower())
        total_words += len(words)
        covered = [False] * len(words)
        matched_labels: set[str] = set()
        for i in range(len(words) - n + 1):
            gram = tuple(words[i:i + n])
            for label, grams in source_grams.items():
                if gram in grams:
                    for k in range(i, i + n):
                        covered[k] = True
                    matched_labels.add(label)
        covered_words += sum(covered)
        if matched_labels and len(examples) < 5:
            examples.append(f"[{', '.join(sorted(matched_labels))}] " + p[:100] + ("…" if len(p) > 100 else ""))

    overlap_pct = (covered_words / total_words * 100) if total_words else 0.0
    return overlap_pct, examples


MAX_LINK_WORD_OFFSET = 200


def cmi_link_placement(sections: list[dict], sponsor_text: str, market_name: str) -> tuple[int | None, bool, tuple[int, int] | None]:
    """Where the CMI report link sits: (word offset at which it starts counting the retained
    paragraph at its real length, whether it is the FIRST mention of the market's full name in the
    article, (section index, paragraph index) of the paragraph holding it). The offset is None when
    there is no link. The retained paragraph's own text is ignored for the first-mention test: it is
    the guest author's wording and cannot be changed."""
    words_before = 0
    text_before: list[str] = []
    for si, s in enumerate(sections):
        for pi, p in enumerate(s["paragraphs"]):
            if p.strip() == SPONSOR_TOKEN:
                words_before += len(sponsor_text.split())
                continue
            m = re.search(CMI_LINK_PATTERN, p)
            if m:
                prefix = p[:m.start()]
                before = "\n".join(text_before + [prefix])
                first = not market_name or market_name.lower() not in before.lower()
                return words_before + len(prefix.split()), first, (si, pi)
            text_before.append(p)
            words_before += len(p.split())
    return None, True, None


def _stale_event_mentions(sections: list[dict], today: date | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for section in sections:
        for paragraph in section.get("paragraphs", []):
            if paragraph.strip() == SPONSOR_TOKEN:
                continue
            for m in freshness.INLINE_EVENT_DATE_RE.finditer(paragraph):
                d = freshness.event_date(m)
                if d is not None and freshness.is_stale(d, today):
                    s = f"{m.group(0)} ({section.get('heading') or 'intro'})"
                    if s not in seen:
                        seen.add(s)
                        out.append(s)
    return out


def run_checks(
    title: str, sections: list[dict], source_text: str, sponsor_brand_terms: list[str],
    sponsor_text: str = "", original_body: str = "", report_name: str = "",
    selection: dict | None = None, hits: list | None = None, report_text: str = "",
    primary_keyword: str = "", secondary_keywords: list[str] = (), original_title: str = "",
) -> list[CheckResult]:
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    full = "\n".join(paragraphs)
    results: list[CheckResult] = []

    placed = sum(p.strip() == SPONSOR_TOKEN for p in paragraphs)
    non_token_overlap = _longest_shared_run(full.replace(SPONSOR_TOKEN, ""), sponsor_text) if sponsor_text else 0
    leaked = placed != 1 and non_token_overlap >= 6
    results.append(CheckResult(
        "Retained backlink paragraph placed once, unaltered",
        placed == 1 and full.count(SPONSOR_TOKEN) == 1,
        (f"placed {placed} time(s)" + (" — the model appears to have written out the retained "
         "paragraph's own words instead of the placeholder; check the intro manually" if leaked else "")),
    ))

    intro = sections[0]["paragraphs"] if sections else []
    in_intro_slot = len(intro) >= 2 and intro[1].strip() == SPONSOR_TOKEN
    results.append(CheckResult(
        "Retained paragraph sits in the intro, not mid-article",
        in_intro_slot,
        "" if in_intro_slot else "expected it as paragraph 2 of the unheaded intro section",
    ))

    cmi_links = re.findall(CMI_LINK_PATTERN, full)
    results.append(CheckResult("CMI report linked exactly once", len(cmi_links) == 1, f"found {len(cmi_links)}"))

    # The report link belongs at the very start of the article (the first 100-200 words) and the
    # link itself must be the first time the market is named — so the first mention of the primary
    # keyword is the hyperlink back to the CMI report.
    link_offset, link_is_first_mention, _ = cmi_link_placement(sections, sponsor_text, report_name or primary_keyword)
    results.append(CheckResult(
        f"CMI link sits within the first {MAX_LINK_WORD_OFFSET} words",
        link_offset is not None and link_offset <= MAX_LINK_WORD_OFFSET,
        "no CMI link found" if link_offset is None else f"the link starts at word {link_offset}",
    ))
    if link_offset is not None:
        results.append(CheckResult(
            "The first mention of the market name is the CMI link",
            link_is_first_mention,
            "" if link_is_first_mention else
            f"{(report_name or primary_keyword)!r} appears in plain text before the link",
        ))

    # Catches the model citing a DIFFERENT market's name in the anchor text than the report it
    # was actually given and fetched for — a real failure seen in production: correct report
    # content/figures, but an anchor naming an unrelated market mentioned elsewhere in its prompt
    # (e.g. a name the original source article happened to reference). Exact match only, since
    # the WRITE prompt instructs the model to copy report_name verbatim into the anchor.
    if report_name:
        anchor_matches_report = any(link.strip() == report_name.strip() for link in cmi_links)
        results.append(CheckResult(
            "CMI link anchor names the confirmed report",
            anchor_matches_report,
            "" if anchor_matches_report else f"expected {report_name!r}, found {cmi_links!r}",
        ))

        # Catches a whole SECTION drifting onto a different market than the one confirmed —
        # the anchor-text check above only looks at the one CMI_LINK sentence; this looks at
        # every SOP-tagged section's actual prose. "conclusion" is excluded: it may legitimately
        # synthesize in broader language without repeating the market name itself.
        market_tokens = _market_tokens(report_name)
        if market_tokens:
            off_topic_sections = []
            for s in sections:
                section_tags = [t for t in s["sop"] if t != "conclusion"]
                if not section_tags:
                    continue
                section_text = " ".join(s["paragraphs"])
                if not (market_tokens & _market_tokens(section_text)):
                    off_topic_sections.append(s.get("heading") or "/".join(section_tags))
            results.append(CheckResult(
                "Every section discusses the confirmed market",
                not off_topic_sections,
                "" if not off_topic_sections else f"no mention of {report_name!r} in: {', '.join(off_topic_sections)}",
            ))

    # A fact used for "trends" must come from a source with a verified publish date (search.py
    # already drops dated hits older than the freshness window, and run_select strips "trends"
    # from undated ones) — an undated page has no verified recency and must never be cited as an
    # "ongoing trend". This re-derives the W<n> -> hit link the same way run_build_evidence does,
    # as a last line of defence for a selection that bypassed run_select's normalisation.
    if selection is not None and hits is not None:
        bad_trend_sources = []
        for e in selection.get("evidence") or []:
            if "trends" not in (e.get("use_for_all") or [e.get("use_for")]):
                continue
            m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
            if not m or not 1 <= int(m.group(1)) <= len(hits):
                continue
            hit = hits[int(m.group(1)) - 1]
            if getattr(hit, "date", None) is None:
                bad_trend_sources.append(e.get("id"))
        results.append(CheckResult(
            "\"Trends\" facts are sourced from dated results, not undated pages",
            not bad_trend_sources,
            "" if not bad_trend_sources else f"background (undated) source(s) used as trends: {', '.join(bad_trend_sources)}",
        ))

        # Soft check: SELECT can vet a dozen real, specific facts and the writer can still ignore
        # most of them with no visibility into it (found in production: 12 selected, only 4
        # actually used). A fact counts as "used" if at least one of its proper-noun phrases or
        # numbers (see _fact_signature_tokens) appears in the draft — a whole phrase, not just any
        # one word of it, so a generic word two facts happen to share can't cause a false match.
        # This survives paraphrasing without requiring a verbatim sentence match. It does not fail
        # the run — a writer skipping some vetted facts for length/flow reasons is a normal
        # editorial choice — it only surfaces how much of the vetted evidence pool made it in, so a
        # run that uses almost none of it is visible instead of silent.
        evidence_items = [e for e in (selection.get("evidence") or []) if e.get("fact")]
        if evidence_items:
            full_tokens = _market_tokens(full)  # word tokens only — a plain substring check let
            # "ping" match inside "shaping" and "uma" match inside "human" in production testing.
            unused = []
            for e in evidence_items:
                sig = _fact_signature_tokens(e["fact"])
                used = bool(sig) and any(
                    (s in full_tokens) if " " not in s else (s in full.lower()) for s in sig
                )
                if not used:
                    unused.append(e.get("id") or e["fact"][:40])
            used_count = len(evidence_items) - len(unused)
            results.append(CheckResult(
                f"Selected evidence used in the draft ({used_count}/{len(evidence_items)})",
                not unused,
                "" if not unused else f"selected but not found in the draft: {', '.join(unused)}",
            ))

    # Stale report developments are stripped before writing, so this catches a stale date that
    # still reached the draft (e.g. from the original guest article or model memory).
    stale = _stale_event_mentions(sections)
    results.append(CheckResult(
        "Dated events cited are from the last 12 months",
        not stale,
        "stale: " + "; ".join(stale) if stale else "",
    ))

    tags = [t for s in sections for t in s["sop"]]
    for tag, label in _SOP_TAGS:
        results.append(CheckResult(label, tag in tags))

    # A "subsegment" tag marks a paragraph as a sub-segment deep-dive, independent of how many
    # such paragraphs share one section — two tagged paragraphs satisfy the SOP even in one section.
    sub = sum(len(s["paragraphs"]) for s in sections if "subsegment" in s["sop"])
    results.append(CheckResult("Two sub-segment analyses", sub >= 2, f"found {sub} sub-segment paragraph(s)"))

    trend_paras = sum(count_items_in(s["paragraphs"]) for s in sections if "trends" in s["sop"])
    results.append(CheckResult("Three trends (at least 3 trend paragraphs)", trend_paras >= 3, f"{trend_paras} paragraph(s)"))

    conclusion_paras = sum(len(s["paragraphs"]) for s in sections if "conclusion" in s["sop"])
    is_last = bool(sections) and "conclusion" in sections[-1]["sop"]
    results.append(CheckResult(
        "Conclusion is the closing section",
        conclusion_paras >= 1 and is_last,
        f"{conclusion_paras} paragraph(s)" + ("" if is_last else ", not in the final section"),
    ))

    us_words = sum(_words(p) for s in sections if "country_us" in s["sop"] for p in s["paragraphs"])
    results.append(CheckResult("U.S. analysis is 100-200 words", 100 <= us_words <= 200, f"{us_words} words"))

    # Soft target: the title has a preferred range (40-75 chars), but a small overshoot is a
    # trivial human edit, not a correctness problem — only flag an outlier far outside the range, so
    # this doesn't cry wolf on every normal run.
    results.append(CheckResult("Title length reasonable (~40-75 characters, soft)", 25 <= len(title) <= 100, f"{len(title)} characters"))

    # A title like "<Market> Market Intelligence: Growth Outlook" is on-brand but says nothing about
    # what the article covers — it must share at least one subject word with the original title
    # once the market's own name and generic filler are stripped from both.
    if original_title and report_name:
        original_stems = _title_subject_stems(original_title, report_name)
        title_stems = _title_subject_stems(title, report_name)
        shared = original_stems & title_stems
        results.append(CheckResult(
            "Title names the article's subject, not just the market (soft)",
            bool(shared) or not original_stems,
            f"shares {sorted(shared)} with the original title" if shared
            else f"no subject word from the original title {original_title!r} survives in {title!r}",
        ))
    title_cmi_terms = sorted(w for w in re.findall(r"[a-z]+", title.lower()) if w in _CMI_TITLE_TERMS)
    results.append(CheckResult(
        "Title carries CMI market-intelligence vocabulary (soft)",
        bool(title_cmi_terms),
        f"uses {title_cmi_terms}" if title_cmi_terms else f"none of {sorted(_CMI_TITLE_TERMS)} appear in the title",
    ))

    if primary_keyword:
        results.append(CheckResult(
            "Title contains the full primary keyword (soft)",
            primary_keyword.lower() in title.lower(),
            "" if primary_keyword.lower() in title.lower() else f"{primary_keyword!r} not in the title",
        ))

    total = _words(full.replace(SPONSOR_TOKEN, sponsor_text)) if sponsor_text else _words(full)
    results.append(CheckResult("Length is 1,400-1,700 words (soft)", 1400 <= total <= 1700, f"{total} words"))

    # Soft check: flags prose dense enough that a reader could lose the thread, without demanding
    # dumbed-down writing — this is still meant to read as a professional analyst brief, not a
    # children's book. Floor of 30 (~"difficult/graduate-level") is the bar; below that, sentences
    # are long enough and jargon-stacked enough on average that the article likely reads as a wall
    # of report-speak rather than a blog a reader can follow section to section.
    score = _flesch_reading_ease(full)
    if score is not None:
        results.append(CheckResult(
            "Readability not too dense (Flesch Reading Ease, soft)",
            score >= 30,
            f"score {score:.0f}/100 (below 30 = very difficult to read; aim for 40-60)",
        ))

    # Soft checks: verify the SEO keyword plan (see pipeline._seo_keyword_plan) actually landed at
    # roughly the instructed rates, without failing the run — a miss here is a tuning signal for
    # the prompt, not a defect worth blocking a publish over. Counts are case-insensitive substring
    # matches, same approach as _longest_shared_run elsewhere in this file; a keyword is a short,
    # specific phrase so substring counting doesn't have the "ping" inside "shaping" false-positive
    # risk that plain word-level matching caused for single generic words elsewhere.
    if primary_keyword and total:
        # WRITE_USER's minimum target is one full use of the primary keyword per section (the
        # unheaded intro counts as a section too) — len(sections) is this draft's own actual
        # section count, so the floor always matches what was actually asked for regardless of how
        # many original-topic/market-analysis sections this particular run produced. The ceiling
        # stays a loose sanity check, not a target: it only flags outright stuffing (several uses
        # crammed into one paragraph), which the per-paragraph "do NOT stack" rule already forbids.
        primary_count = full.lower().count(primary_keyword.lower())
        primary_pct = primary_count / total * 100
        results.append(CheckResult(
            "Primary keyword present, not stuffed (soft)",
            len(sections) <= primary_count and primary_pct <= 3.0,
            f"{primary_keyword!r} used {primary_count}x ({primary_pct:.1f}% of {total} words) across {len(sections)} section(s)",
        ))

    if secondary_keywords:
        # WRITE_USER deliberately tells the model each secondary keyword is only used where it
        # fits naturally — using well under the full list is an expected, correct outcome, not a
        # miss. This check isn't "did every keyword get used" (that would reward forcing them in),
        # it's "did the category get a genuine effort at all" — a floor catching the case where
        # almost none landed, not a ceiling demanding all of them.
        secondary_counts = {kw: count_keyword_uses(full, kw) for kw in secondary_keywords}
        unused = [kw for kw, c in secondary_counts.items() if c == 0]
        used = len(secondary_keywords) - len(unused)
        min_expected = max(1, len(secondary_keywords) // 3)
        results.append(CheckResult(
            f"Secondary keywords: genuine effort made ({used}/{len(secondary_keywords)}, soft)",
            used >= min_expected,
            f"used {used} of {len(secondary_keywords)} (expected at least {min_expected}); "
            f"not found: {', '.join(unused)}" if unused else f"used {used} of {len(secondary_keywords)}",
        ))

    known = _numbers(source_text)
    unverified = sorted({m.group(0) for m in _FIGURE.finditer(full) if float(m.group(1).replace(",", "")) not in known})
    results.append(CheckResult(
        "All figures traceable to the report or evidence",
        not unverified,
        "unverified: " + ", ".join(unverified) if unverified else "",
    ))

    non_sponsor = "\n".join(p for p in paragraphs if p.strip() != SPONSOR_TOKEN).lower()
    leaked = [t for t in sponsor_brand_terms if t and t.lower() in non_sponsor]
    results.append(CheckResult(
        "Guest brand not mentioned outside the retained paragraph",
        not leaked,
        ", ".join(leaked),
    ))

    sources = []
    if original_body:
        sources.append(("original article", original_body))
    if report_text:
        sources.append(("CMI report", report_text))
    if sources:
        overlap_pct, overlap_examples = _verbatim_overlap(paragraphs, sources, report_name=report_name)
        results.append(CheckResult(
            "Verbatim overlap with source material is below 5%",
            overlap_pct <= 5.0,
            f"{overlap_pct:.1f}% of draft words fall in a run of >= 10 words copied from a source"
            + (" — e.g. " + " | ".join(overlap_examples) if overlap_examples else ""),
        ))

    return results
