import re
from dataclasses import dataclass
from datetime import date

import freshness
from prompts import CMI_LINK_PATTERN, SPONSOR_TOKEN

# A sentence that gives a view in the voice of a CMI analyst ("A CMI analyst expects...", "An analyst at CMI believes...",
# "CMI's research team suggests..."). The post carries one or two of them, no more.
_CMI_NAME = r"(?:CMI|Coherent Market Insights)"
_CMI_OPINION_RE = re.compile(
    r"\b(?:an?|one)\s+(?:senior |lead |market |industry )?(?:analyst|expert|researcher|strategist)s?\s+(?:at|from|with)\s+" + _CMI_NAME + r"\b"
    r"|\banalysts?\s+(?:at|from|with)\s+" + _CMI_NAME + r"\b"
    r"|\b" + _CMI_NAME + r"(?:'s|\u2019s)?\s+(?:senior |lead |market )?(?:analysts?|experts?|researchers?|research team|analyst team)\b",
    re.IGNORECASE,
)
CMI_OPINIONS_MIN, CMI_OPINIONS_MAX = 1, 2


def count_cmi_opinions(paragraphs: list[str]) -> int:
    """Sentences that attribute a view to a CMI analyst/expert/research team."""
    n = 0
    for p in paragraphs:
        for sentence in re.split(r"(?<=[.!?])\s+", p):
            if _CMI_OPINION_RE.search(sentence):
                n += 1
    return n


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
    for suf, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suf) and len(word) - len(suf) + len(repl) >= 3:
            word = word[: -len(suf)] + repl
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


def _market_name_in_text(text: str, market_name: str) -> bool:
    """The market's own content words all appear somewhere in `text` — the same market-side gate
    secondary_keyword_in_paragraph uses, kept here for a plain "the market name is missing" verdict."""
    stems = {_stem(w) for w in re.findall(r"[a-z0-9']+", text.lower())}
    words = _keyword_content_words(market_name)
    return bool(words) and all(_stem(w) in stems for w in words)


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


_LONGTAIL_GLUE_WORDS = frozenset({
    "a", "an", "the", "in", "for", "of", "to", "with", "on", "at", "by", "from", "and", "vs", "versus", "or",
    "that", "is", "are", "its", "their", "s",
})


def longtail_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Each use of a long-tail phrase in `text`, as (start_char, end_char). A long-tail phrase is a concept like a
    secondary keyword: the writer may REORDER its words to fit a sentence (and inflect them), but every content
    word of it must be there, close together — so the same concept matcher is used, not a literal substring search
    (the one place highlighting, the used/not-used verdict and the SOP check all agree)."""
    target = {_stem(w) for w in _keyword_content_words(phrase)}
    out: list[tuple[int, int]] = []
    # Tighter than a secondary keyword's window: a long-tail phrase is a specific multi-word search phrase, and its
    # words scattered across a clause ("the <market name>, the software layer that banks ...") are not a use of it.
    window = len(target) + 3
    for start, end in find_keyword_spans(text, phrase, window=window):
        # find_keyword_spans starts a match at the FIRST target word it meets, which can be an earlier,
        # unrelated use of one of the phrase's words; tighten to the shortest stretch that holds all of them
        # (each word's last occurrence inside the match), so the highlight covers just the phrase's own words.
        tokens = [m for m in re.finditer(r"[a-z0-9']+", text[start:end].lower())]
        last = {}
        for m in tokens:
            if _stem(m.group(0)) in target:
                last[_stem(m.group(0))] = m
        if len(last) == len(target):
            start, end = start + min(m.start() for m in last.values()), start + max(m.end() for m in last.values())
        # a use lives in ONE sentence: words scattered across a sentence end or a line break are not the phrase
        # (or a comma/colon/dash: a phrase's own words are not split by a clause boundary)
        if re.search(r"[.!?;:,]\s|\n|\s[-—–]\s", text[start:end]):
            continue
        # and between the phrase's own words only light grammatical glue may sit (an article, a preposition, "vs"):
        # "an AI-driven wealth management platform" is a different thing from "AI in wealth management platforms"
        between = [m.group(0) for m in re.finditer(r"[a-z0-9']+", text[start:end].lower()) if _stem(m.group(0)) not in target]
        if any(w not in _LONGTAIL_GLUE_WORDS for w in between):
            continue
        out.append((start, end))
    return out


LONGTAIL_MIN_USED, LONGTAIL_MAX_USED = 2, 3  # how many long-tail phrases a finished post should use


# A paragraph can hold a short lead-in sentence followed by parallel items (3 trends, a
# segmentation breakdown) as "\n- " lines within the SAME paragraph string, instead of as separate
# paragraph objects — this is a presentation convention (docx_io/app.js render it as a real
# bulleted list) that every existing text-based check (word counts, keyword matching, figure
# detection) already tolerates unchanged, since they all operate on plain text where "\n" is just
# more whitespace. The one place that needs to know about the convention is anything that counts
# how many of a repeated item (trends, sub-segments) a section provides — see count_items_in.
_BULLET_LINE_RE = re.compile(r"^-\s+")


def split_bullet_lines(para_text: str) -> tuple[str, list[str]]:
    """(lead-in text, [bullet item, ...]) if para_text contains "\n- " bullet lines, else
    (para_text, []). The one place that owns this convention — docx_io and app.js both render it,
    and long_paragraph_problems below measures a bulleted paragraph's pieces, not its whole length."""
    lines = para_text.split("\n")
    lead = []
    items = []
    for line in lines:
        if _BULLET_LINE_RE.match(line):
            items.append(_BULLET_LINE_RE.sub("", line, count=1))
        elif items:
            # a non-bullet line after bullets started: treat the whole thing as plain text
            # instead, rather than silently dropping a line that didn't fit the pattern.
            return para_text, []
        else:
            lead.append(line)
    return "\n".join(lead), items


def count_items_in(paragraphs: list[str]) -> int:
    """How many 'items' a list of paragraph strings contains, counting a paragraph with bullet
    lines as one item per bullet rather than one item for the whole paragraph."""
    total = 0
    for p in paragraphs:
        _, items = split_bullet_lines(p)
        total += len(items) if items else 1
    return total


_MAX_PARAGRAPH_WORDS = 100  # readability ceiling for a single flowing chunk of prose
# DISCONNECTED (False): the "no paragraph over 100 words" SOP no longer runs — neither in run_checks nor in the
# write-time problem log (pipeline._write_problems). long_paragraph_problems below is kept intact; set True to
# turn the check back on, nothing else needs to change.
LONG_PARAGRAPH_CHECK_ENABLED = False


def long_paragraph_problems(sections: list[dict]) -> list[str]:
    """Flags any paragraph whose own prose runs past _MAX_PARAGRAPH_WORDS. A bulleted paragraph's
    lead-in and each bullet are measured separately, never the whole concatenated string, since a
    real bulleted list is already broken into short, readable pieces by the time it renders."""
    problems = []
    for s in sections:
        for p in s.get("paragraphs") or []:
            lead, items = split_bullet_lines(str(p))
            pieces = ([lead] if lead.strip() else []) + items
            for piece in pieces:
                n = _words(piece)
                if n > _MAX_PARAGRAPH_WORDS:
                    problems.append(
                        f'"{(s.get("heading") or "intro").strip() or "intro"}" has a paragraph of {n} words (over '
                        f"{_MAX_PARAGRAPH_WORDS}) — split it into shorter paragraphs, or turn parallel items into a "
                        "bulleted list"
                    )
    return problems


def question_heading_problems(sections: list[dict], questions: list[dict]) -> list[str]:
    """For each chosen subheading question with a section assignment (see pipeline.run_select_questions),
    flags the assigned section (found by its sop tag) if its heading doesn't read as that question —
    doesn't end in "?", or shares too little vocabulary with the question to plausibly be it. A loose,
    soft check: the model may legitimately smooth grammar, so this does not require an exact match."""
    problems = []
    by_tag = {tag: s for s in sections for tag in (s.get("sop") or [])}
    for q in questions or []:
        sec_tag = q.get("section")
        s = by_tag.get(sec_tag)
        if not s:
            continue
        heading = (s.get("heading") or "").strip()
        if not heading.endswith("?"):
            problems.append(
                f'the {sec_tag} section was assigned the question "{q["question"]}" but its heading '
                f'("{heading}") does not end in "?" — the heading must be that question (or a lightly '
                "grammar-smoothed version of it), not an ordinary descriptive heading"
            )
            continue
        q_tokens = _market_tokens(q["question"])
        overlap = q_tokens & _market_tokens(heading)
        if q_tokens and len(overlap) < max(1, len(q_tokens) // 2):
            problems.append(
                f'the {sec_tag} section\'s heading ("{heading}") shares too little wording with its assigned '
                f'question ("{q["question"]}") to plausibly be it'
            )
    return problems

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
        own = p.replace(SPONSOR_TOKEN, " ")
        if not own.strip():
            continue
        words = re.findall(r"\w+", _strip_name(own).lower())
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
            examples.append(f"[{', '.join(sorted(matched_labels))}] " + own[:100] + ("…" if len(own) > 100 else ""))

    overlap_pct = (covered_words / total_words * 100) if total_words else 0.0
    return overlap_pct, examples


MAX_LINK_WORD_OFFSET = 200


def _sponsor_replacement(text: str, sponsor_text: str) -> str:
    """The token stands for the retained paragraph's real words: substituting it back in lets word
    counts account for those real words where they actually sit (inline or as their own paragraph).
    The retained text itself is then blanked for the FIRST-MENTION test, since the guest author's
    wording cannot be changed and must never count as a plain-text mention the article controls."""
    return text.replace(SPONSOR_TOKEN, sponsor_text) if sponsor_text else text


def cmi_link_placement(sections: list[dict], sponsor_text: str, market_name: str) -> tuple[int | None, bool, tuple[int, int] | None]:
    """Where the CMI report link sits: (word offset at which it starts counting the retained
    paragraph at its real length, whether it is the FIRST mention of the market's full name in the
    article, (section index, paragraph index) of the paragraph holding it). The offset is None when
    there is no link. The retained paragraph's own text is ignored for the first-mention test: it is
    the guest author's wording and cannot be changed. The token may be inline in a paragraph or its
    own paragraph; either way its real words count toward the offset where they actually sit."""
    words_before = 0
    text_before: list[str] = []
    for si, s in enumerate(sections):
        for pi, p in enumerate(s["paragraphs"]):
            if not p.replace(SPONSOR_TOKEN, " ").strip():
                words_before += len(sponsor_text.split())
                continue
            m = re.search(CMI_LINK_PATTERN, p)
            if m:
                prefix = p[:m.start()]
                before = "\n".join(text_before + [prefix.replace(SPONSOR_TOKEN, " ")])
                first = not market_name or market_name.lower() not in before.lower()
                return words_before + len(_sponsor_replacement(prefix, sponsor_text).split()), first, (si, pi)
            text_before.append(p.replace(SPONSOR_TOKEN, " "))
            words_before += len(_sponsor_replacement(p, sponsor_text).split())
    return None, True, None


def _stale_event_mentions(sections: list[dict], today: date | None = None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for section in sections:
        for paragraph in section.get("paragraphs", []):
            own = paragraph.replace(SPONSOR_TOKEN, " ")
            if not own.strip():
                continue
            for m in freshness.INLINE_EVENT_DATE_RE.finditer(own):
                d = freshness.event_date(m)
                if d is not None and freshness.is_stale(d, today):
                    s = f"{m.group(0)} ({section.get('heading') or 'intro'})"
                    if s not in seen:
                        seen.add(s)
                        out.append(s)
    return out


def _layout_stats(sections: list[dict], sponsor_text: str = "") -> tuple[str, int, int, int]:
    """(section order string, topic switches, publisher words, total words). A section is P
    (publisher) when it is the unheaded intro or carries no SOP tag; every tagged section is M
    (market), including the conclusion. A topic switch is an adjacent pair whose class differs, so
    the intended layout (intro + publisher block, then market block) has exactly one. The sponsor
    placeholder counts as the retained paragraph's real words, which belong to the publisher."""
    classes = ["M" if s.get("sop") else "P" for s in sections]
    switches = sum(1 for a, b in zip(classes, classes[1:]) if a != b)
    order_parts: list[str] = []
    publisher = total = 0
    for s, cls in zip(sections, classes):
        order_parts.append(f'{(s.get("heading") or "").strip() or "intro"} {cls}')
        for p in s.get("paragraphs") or []:
            words = _words(_sponsor_replacement(p, sponsor_text).replace(SPONSOR_TOKEN, " "))
            total += words
            if cls == "P":
                publisher += words
    return " | ".join(order_parts), switches, publisher, total


def run_checks(
    title: str, sections: list[dict], source_text: str, sponsor_brand_terms: list[str],
    sponsor_text: str = "", original_body: str = "", report_name: str = "",
    selection: dict | None = None, hits: list | None = None, report_text: str = "",
    primary_keyword: str = "", secondary_keywords: list[str] = (), original_title: str = "",
    questions: list[dict] = (), longtails: list[str] = (),
) -> list[CheckResult]:
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    full = "\n".join(paragraphs)
    results: list[CheckResult] = []

    token_count = full.count(SPONSOR_TOKEN)
    placed = sum(p.count(SPONSOR_TOKEN) for p in paragraphs)
    non_token_overlap = _longest_shared_run(full.replace(SPONSOR_TOKEN, ""), sponsor_text) if sponsor_text else 0
    leaked = placed != 1 and non_token_overlap >= 6
    results.append(CheckResult(
        "Retained backlink paragraph placed once, unaltered",
        token_count == 1,
        (f"placed {token_count} time(s)" + (" — the model appears to have written out the retained "
         "paragraph's own words instead of the placeholder; check the intro manually" if leaked else "")),
    ))

    intro = sections[0]["paragraphs"] if sections else []
    in_intro_slot = token_count == 1 and any(SPONSOR_TOKEN in p for p in intro)
    results.append(CheckResult(
        "Retained paragraph sits in the intro, not mid-article",
        in_intro_slot,
        "" if in_intro_slot else "expected the placeholder exactly once in the unheaded intro section, as its own paragraph or inside a sentence",
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
    results.append(CheckResult("Length is 1,200-1,500 words (soft)", 1200 <= total <= 1500, f"{total} words"))

    order, switches, publisher_words, layout_total = _layout_stats(sections, sponsor_text)
    publisher_pct = (publisher_words / layout_total * 100) if layout_total else 0.0
    results.append(CheckResult(
        "Publisher content is about a fifth of the post (soft)",
        15 <= publisher_pct <= 25,
        f"{publisher_pct:.0f}% publisher / {100 - publisher_pct:.0f}% market "
        f"({publisher_words} of {layout_total} words; soft target 15-25% publisher)",
    ))
    results.append(CheckResult(
        "Topics are grouped: publisher block, then market block",
        switches <= 2,
        f"{switches} topic switch(es) — order: {order}",
    ))

    if LONG_PARAGRAPH_CHECK_ENABLED:
        long_paras = long_paragraph_problems(sections)
        results.append(CheckResult(
            f"No paragraph over {_MAX_PARAGRAPH_WORDS} words (soft)",
            not long_paras,
            "; ".join(long_paras) if long_paras else "",
        ))

    if longtails:
        body = "\n".join(" ".join(s["paragraphs"]) for s in sections)
        used_longtails = [kw for kw in longtails if longtail_spans(body, kw)]
        results.append(CheckResult(
            f"Uses {LONGTAIL_MIN_USED}-{LONGTAIL_MAX_USED} long-tail keywords (soft)",
            LONGTAIL_MIN_USED <= len(used_longtails) <= LONGTAIL_MAX_USED,
            f"{len(used_longtails)} of {len(longtails)} used: " + (", ".join(used_longtails) or "none"),
        ))

    if questions:
        question_problems = question_heading_problems(sections, questions)
        results.append(CheckResult(
            "Question-headed sections ask and answer their assigned question (soft)",
            not question_problems,
            "; ".join(question_problems) if question_problems else "",
        ))

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
        needed = 1 + sum(1 for sec in sections if sec.get('sop'))
        primary_count = full.lower().count(primary_keyword.lower())
        primary_pct = primary_count / total * 100
        results.append(CheckResult(
            "Primary keyword present, not stuffed (soft)",
            needed <= primary_count and primary_pct <= 3.0,
            f"{primary_keyword!r} used {primary_count}x ({primary_pct:.1f}% of {total} words); at least {needed} expected "
            "(the intro's link plus every market section)",
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

    non_sponsor = "\n".join(p for p in paragraphs if p.replace(SPONSOR_TOKEN, " ").strip()).lower()
    leaked = [t for t in sponsor_brand_terms if t and t.lower() in non_sponsor]
    results.append(CheckResult(
        "Guest brand not mentioned outside the retained paragraph",
        not leaked,
        ", ".join(leaked),
    ))

    opinions = count_cmi_opinions(paragraphs)
    results.append(CheckResult(
        f"CMI analyst opinion: {CMI_OPINIONS_MIN} to {CMI_OPINIONS_MAX} labelled statements in the article",
        CMI_OPINIONS_MIN <= opinions <= CMI_OPINIONS_MAX,
        f"found {opinions}",
    ))

    sources = []
    # Only the CMI report is a copy risk; the publisher's article is edited, so its wording may be reused.
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
# ===================================================================================================
# Market-first chain (v2) checks — plan sections 3.9, 3.6a and 3.6b. The old run_checks stays intact
# above (the old chain keeps running behind its flag); the v2 chain calls run_checks_v2, which keeps
# the checks the plan keeps (CMI link, sponsor, SOP sections, U.S. length, opinions, report overlap,
# recency, guest brand) and replaces the retired ones ("Title names the article's subject", "Title
# carries CMI vocabulary", the soft publisher-fifth and the grouped-topics pair) with the hard
# block-share and block-order checks. Helper modules that import pipeline (`planning`, `facts`, `faqs`)
# are imported inside the functions: pipeline imports this module, so module-level imports would cycle.
# ===================================================================================================

_V2_INTRO_CAP = 0.20
_V2_TRANSITION_MIN, _V2_TRANSITION_MAX = 0.05, 0.10
_V2_MARKET_MIN = 0.70
_V2_SENTENCE_MAX_WORDS = 30
_V2_FLESCH_MIN = 50

# A sentence that credits a figure to CMI in so many words.
_CMI_NAMED_RE = re.compile(_CMI_NAME, re.IGNORECASE)

# Placeholder figures that must never print (§3.9): "USD 2024", "a stated rate", a bare "%" with no
# number in front of it, and a leftover bracketed sample shape.
_V2_PLACEHOLDER_RES = (
    re.compile(r"\bUSD\s*20[12]\d\b", re.IGNORECASE),
    re.compile(r"\ba\s+stated\s+rate\b", re.IGNORECASE),
    re.compile(r"(?<![\d.,])%"),
    re.compile(r"<[A-Z][a-z]+(?: [A-Za-z]+)*>"),
)


def _is_market_section(s: dict) -> bool:
    import planning
    return bool(s.get("sop")) and s["sop"][0] in planning.ORDER


def _fact_numbers(text: str) -> set[str]:
    """facts._numbers, normalized numeric tokens ('561.7', '6.4') — imported lazily (see block comment).
    A full date's day ("September 15, 2026") is dropped first: a day-of-month is calendar context the
    same way a year is, never a figure whose identity a sentence must carry."""
    import facts
    text = re.sub(r"\b\d{1,2}, 20\d{2}\b", " ", text or "")
    return set(facts._numbers(text))


def _v2_block_words(sections: list[dict], sponsor_text: str = "") -> dict:
    """Words per block, measured on the real text with the sponsor paragraph substituted in (§3.6b):
    {intro, transition, market, per_section, total}."""
    blocks = {"intro": 0, "transition": 0, "market": 0}
    per_section: dict[str, int] = {}
    for s in sections:
        tag = s["sop"][0] if _is_market_section(s) else ("transition" if s.get("sop") == ["transition"] else "intro")
        n = sum(_words(_sponsor_replacement(p, sponsor_text)) for p in s["paragraphs"])
        if tag == "intro":
            blocks["intro"] += n
        elif tag == "transition":
            blocks["transition"] += n
        else:
            blocks["market"] += n
            per_section[tag] = n
    blocks["per_section"] = per_section
    blocks["total"] = blocks["intro"] + blocks["transition"] + blocks["market"]
    return blocks


def block_share_problems(sections: list[dict], sponsor_text: str = "") -> list[str]:
    """Hard caps on each block's share of the body words (§3.6b): intro <= 20%, transition 5-10%,
    market >= 70%. There is no repair call and no retry (user decision, §3.6b): a miss is reported for
    the content team to trim by hand."""
    b = _v2_block_words(sections, sponsor_text)
    total = b["total"]
    out: list[str] = []
    if total > 0:
        if b["intro"] / total > _V2_INTRO_CAP:
            out.append(f"the publisher intro is {b['intro']} words = {b['intro'] / total:.0%} of the body (cap 20%)")
        t_share = b["transition"] / total
        if not (_V2_TRANSITION_MIN <= t_share <= _V2_TRANSITION_MAX):
            out.append(f"the transition is {b['transition']} words = {t_share:.0%} of the body (band 5-10%)")
        if b["market"] / total < _V2_MARKET_MIN:
            out.append(f"the market block is {b['market']} words = {b['market'] / total:.0%} of the body (minimum 70%)")
    return out


def block_order_problems(sections: list[dict]) -> list[str]:
    """The body runs intro -> transition -> market with zero switches back to the publisher's subject
    (§3.6b). The class sequence must be monotone: some P (untagged), then some T, then only M."""
    seen_trans = seen_market = False
    for s in sections:
        cls = "T" if s.get("sop") == ["transition"] else ("P" if not _is_market_section(s) else "M")
        if cls == "M":
            seen_market = True
        elif cls == "T" and seen_market:
            return ["the article returns to the transition after the market block has started"]
        elif cls == "P" and (seen_trans or seen_market):
            return ["the article returns to the publisher's opening after the transition or the market block has started"]
        if cls == "T":
            seen_trans = True
    return []


def v2_structure_problems(title: str, sections: list[dict], plan: dict, inp) -> list[str]:
    """The written draft carries the plan's validated structure: the plan's exact title, every SOP
    section present in the plan's order with the plan's exact heading, and exactly the plan's 2-3 MSQ
    headings, each the MSQ's own text (§3.9 hard check 1)."""
    problems: list[str] = []
    if title.strip() != str(plan.get("title") or "").strip():
        problems.append(f"the title is not the plan's validated title ({title!r} vs {plan.get('title')!r})")
    got_sops = [s["sop"][0] for s in sections if _is_market_section(s)]
    want_sops = [p["sop"] for p in plan["sections"]]
    if got_sops != want_sops:
        problems.append(f"market sections appear as {got_sops}, the plan's order is {want_sops}")
    question_headings: list[str] = []
    for s in sections:
        if not _is_market_section(s):
            continue
        heading = (s.get("heading") or "").strip()
        planned = next((p["heading"] for p in plan["sections"] if p["sop"] == s["sop"][0]), None)
        if planned is not None and heading != planned.strip():
            problems.append(f'the {s["sop"][0]} heading is {heading!r}, the plan\'s was {planned!r}')
        if heading.endswith("?"):
            question_headings.append(heading)
    msqs = {q["question"].strip() for q in inp.questions if q.get("answerable")}
    for h in question_headings:
        if h not in msqs:
            problems.append(f"the question heading {h!r} is not one of the MSQs")
    for p in plan["sections"]:
        if p.get("msq") and p["heading"].strip() not in [h.strip() for h in question_headings]:
            problems.append(f'the planned question heading {p["heading"]!r} is missing from the draft')
    if not 2 <= len(question_headings) <= 3:
        problems.append(f"{len(question_headings)} heading(s) are questions, want 2-3")
    return problems


def v2_keyword_problems(sections: list[dict], plan: dict, market_name: str) -> list[str]:
    """Assigned keyword use in the draft (§3.7/§3.9): 4-6 secondary keywords assigned, each used in its
    section (its own words together with the market's), and at most one per paragraph."""
    assigned = [p["secondary"] for p in plan["sections"] if p.get("secondary")]
    out: list[str] = []
    if not 4 <= len(assigned) <= 6:
        out.append(f"{len(assigned)} secondary keyword(s) assigned, want 4-6")
    for ps in plan["sections"]:
        if not ps.get("secondary"):
            continue
        sec = next((s for s in sections if _is_market_section(s) and s["sop"][0] == ps["sop"]), None)
        if sec and not secondary_keyword_in_paragraph(
                " ".join(" ".join(sec["paragraphs"]).split()), market_name, ps["secondary"]):
            # A keyword use can be missing two ways: the market name it must lean on is absent from
            # the section entirely (the primary keyword rule, §3.7), or the suffix's own words never
            # appear. Report the two plainly so the fix is legible either way.
            if _market_name_in_text(" ".join(" ".join(sec["paragraphs"]).split()), market_name):
                out.append(f'the secondary keyword "{ps["secondary"]}" is not used in its {ps["sop"]} section')
            else:
                out.append(f'the market name appears nowhere in its {ps["sop"]} section, so the keyword '
                           f'"{ps["secondary"]}" has no anchored use in it')
    for s in sections:
        if not _is_market_section(s):
            continue
        for n, p in enumerate(s["paragraphs"], 1):
            low = _sponsor_replacement(p, " ").lower()
            found = [k for k in dict.fromkeys(assigned) if secondary_keyword_in_paragraph(low, market_name, k)]
            if len(found) >= _KEYWORD_STUFFING_AT:
                out.append(f'"{(s.get("heading") or "").strip() or s["sop"][0]}" paragraph {n} carries '
                           f'{len(found)} secondary keywords ({", ".join(found)}); that is keyword stuffing')
    return out


# Everyday words such as "size" and "forecast" sit beside the market's name in any paragraph that states its figures, so a
# keyword assigned to another section turns up there by plain writing. That is not stuffing; three or more in one
# paragraph is (hard). Two is reported as a soft note, so the reader of the checks still sees it.
_KEYWORD_STUFFING_AT = 3


def v2_keyword_overlap_notes(sections: list[dict], plan: dict, market_name: str) -> list[str]:
    assigned = [p["secondary"] for p in plan["sections"] if p.get("secondary")]
    out: list[str] = []
    for s in sections:
        if not _is_market_section(s):
            continue
        for n, p in enumerate(s["paragraphs"], 1):
            low = _sponsor_replacement(p, " ").lower()
            found = [k for k in dict.fromkeys(assigned) if secondary_keyword_in_paragraph(low, market_name, k)]
            if len(found) == _KEYWORD_STUFFING_AT - 1:
                out.append(f'"{(s.get("heading") or "").strip() or s["sop"][0]}" paragraph {n} carries 2 secondary keywords '
                           f'({", ".join(found)})')
    return out


def v2_placeholder_figure_problems(sections: list[dict], faq: list[dict] = ()) -> list[str]:
    """Placeholder shapes must never print (§3.9): "USD <year>", "a stated rate", a bare "%", a leftover
    bracketed sample shape."""
    out: list[str] = []
    for s in sections:
        label = (s.get("heading") or "").strip() or (s["sop"][0] if s.get("sop") else "intro")
        for p in s["paragraphs"]:
            for rx in _V2_PLACEHOLDER_RES:
                m = rx.search(p)
                if m:
                    out.append(f"a placeholder figure {m.group(0)!r} appears in {label}")
    for qa in faq:
        for rx in _V2_PLACEHOLDER_RES:
            m = rx.search(str(qa.get("answer") or ""))
            if m:
                out.append(f"a placeholder figure {m.group(0)!r} appears in the FAQ answer to {str(qa.get('question', ''))[:60]!r}")
    return out


def v2_number_trace_problems(sections: list[dict], faq: list[dict], inp, sponsor_text: str) -> list[str]:
    """Every number in the draft and the FAQ traces to a fact-sheet entry, a selected evidence item, a
    guest publisher point, the sponsor text or the market name (§3.9, per §3.2's "nothing third")."""
    allowed: set[str] = set()
    if inp is not None:
        for i in inp.items.values():
            allowed |= _fact_numbers(i["text"]) | _fact_numbers(i.get("detail", ""))
            allowed |= _fact_numbers(i.get("source", ""))  # a source domain can carry digits ("gtm8020.com")
        for pt in inp.points:
            allowed |= _fact_numbers(pt["text"])
        allowed |= _fact_numbers(sponsor_text) | _fact_numbers(inp.market_name)
    drafts: list[tuple[str, str]] = [
        (str((s.get("heading") or "").strip() or (s["sop"][0] if s.get("sop") else "intro")), p)
        for s in sections for p in s["paragraphs"]
    ]
    drafts += [(f"FAQ: {str(qa.get('question', ''))[:50]}", str(qa.get("answer") or "")) for qa in faq]
    out: list[str] = []
    for label, text in drafts:
        if sponsor_text:
            text = _sponsor_replacement(text, sponsor_text)
        for n in sorted(_fact_numbers(text)):
            if re.fullmatch(r"(?:19|20)\d\d", n):
                continue        # a plain year is context; the figure it dates is checked in its own sentence
            if n not in allowed:
                out.append(f"{label}: the number {n} appears in no fact-sheet entry, evidence item or guest point")
    return out


def v2_evidence_attribution_problems(sections: list[dict], inp) -> list[str]:
    """A figure that came from an outside source (a non-CMI source: a gap-filled fact-sheet headline or a
    selected news item) names that source in the same sentence and is never credited to CMI (§3.2)."""
    out: list[str] = []
    if inp is None:
        return out
    # (fact id, source name, identifying numbers) for every input fact: any of them may "own" a number
    # a sentence carries, so the full registry tells a real misattribution apart from a coincidental
    # token shared by two different facts (Farther's "150 million" against Savvy's "150 advisors").
    # A fact's matching relies on tokens only; the registry then lets a sentence that NAMES the owning
    # source keep its numbers even when another fact shares those tokens.
    registry: list[tuple[str, str, set[str]]] = []
    for i in inp.items.values():
        figs = _fact_numbers(i["text"]) | _fact_numbers(i.get("detail", ""))
        if figs:
            registry.append((i["id"], i.get("source") or "CMI report", figs))
    # Headline gap-fills always count; a news item counts through its own numbers, which is how its
    # fact reaches the draft.
    relevant_kinds = ("size", "forecast", "cagr", "evidence")
    outside: list[tuple[str, str, set[str]]] = [
        (fid, src, figs) for fid, src, figs in registry
        if src not in ("CMI report", "") and fid in {i["id"] for i in inp.items.values() if i["kind"] in relevant_kinds}]
    if not outside:
        return out
    for s in sections:
        label = str((s.get("heading") or "").strip() or (s["sop"][0] if s.get("sop") else "intro"))
        for p in s["paragraphs"]:
            for sentence in re.split(r"(?<=[.!?])\s+", _sponsor_replacement(p, " ")):
                sent_nums = _fact_numbers(sentence)
                if not sent_nums:
                    continue
                low = sentence.lower()
                # A source named in the sentence keeps the numbers its own facts carry: another fact
                # sharing those tokens (a "150 million" raised against "150 advisors") is coincidence,
                # not a figure the writer misattributed. Only true figures cover, never a shared year,
                # and source-name detection mirrors the complaint branch: the source's 4+ letter words.
                sent_strong = {n for n in sent_nums if not re.fullmatch(r"(?:19|20)\d\d", n)}

                def _covered(except_fid: str) -> bool:
                    return any(((src2 not in ("CMI report", "") and _names_source(low, src2))
                                or (src2 == "CMI report" and _CMI_NAMED_RE.search(sentence)))
                               and (sent_strong & figs2)
                               for fid2, src2, figs2 in registry if fid2 != except_fid)

                for fid, source, figs in outside:
                    hit = sent_nums & figs
                    if not hit:
                        continue
                    strong = {n for n in hit if not re.fullmatch(r"(?:19|20)\d\d", n)}
                    if not strong:
                        continue    # only the source's year is shared; the year is context (dates the
                        # figure it sits next to), never the identification of the figure itself
                    if _covered(fid):
                        continue
                    toks = [t for t in re.findall(r"[A-Za-z]{4,}", source)]
                    if toks and not any(t.lower() in low for t in toks):
                        out.append(f"{label}: the figure {', '.join(sorted(strong or hit))} from {source} ({fid}) sits "
                                   "in a sentence that does not name that source")
                    if source.lower() not in ("cmi", "coherent market insights") and _CMI_NAMED_RE.search(sentence):
                        out.append(f"{label}: figures from {source} ({fid}) sit in a sentence that credits CMI")
    return out


def _names_source(low_sentence: str, source: str) -> bool:
    """Does a sentence (lowercased) carry a source's name? Same detection the complaint branch uses:
    the source's 4+ letter words, or a short brand contained whole ("MSN")."""
    toks = [t for t in re.findall(r"[A-Za-z]{4,}", source)]
    if not toks:
        return source.lower() in low_sentence
    return any(t.lower() in low_sentence for t in toks)


def v2_faq_problems(faq: list[dict], plan: dict, inp, sections: list[dict]) -> list[str]:
    """The FAQ is MSQs only (§3.8): every question is the verbatim text of a planned, answerable MSQ from
    the plan's faq list, and every figure in an answer traces to that question's own facts or the draft."""
    out: list[str] = []
    planned_questions: set[str] = set()
    if inp is not None:
        q_by_id = {q["id"]: q for q in inp.questions}
        planned_questions = {q_by_id[qid]["question"].strip() for qid in (plan.get("faq") or []) if qid in q_by_id}
    for qa in faq:
        if str(qa.get("question") or "").strip() not in planned_questions:
            out.append(f"the FAQ question {str(qa.get('question'))[:70]!r} is not a planned MSQ")
    if inp is None:
        return out
    import faqs
    draft_nums: set[str] = set()
    for s in sections:
        for p in s["paragraphs"]:
            draft_nums |= _fact_numbers(_sponsor_replacement(p, " "))
    for qa in faq:
        q = next((q for q in inp.questions if q["id"] == qa.get("id")), None)
        if q is None:
            continue
        src_nums: set[str] = set()
        for i in faqs.sources_for_question(q, inp):
            src_nums |= _fact_numbers(i["text"]) | _fact_numbers(i.get("detail", ""))
            src_nums |= _fact_numbers(i.get("source", ""))  # a source domain can carry digits ("gtm8020.com")
        for n in sorted(_fact_numbers(str(qa.get("answer") or ""))):
            if re.fullmatch(r"(?:19|20)\d\d", n):
                continue
            if n not in src_nums and n not in draft_nums:
                out.append(f"the FAQ answer to {str(qa.get('question'))[:50]!r} states {n}, which neither its facts nor "
                           "the draft carry")
    return out
# ===================================================================================================
# run_checks_v2 — the v2 chain's check list (§3.9): the kept checks from the old run_checks, the new
# hard checks (the plan's validated structure, block shares and order, placeholder figures, number
# traces, outside-source attribution, FAQ = MSQs, planning's title problems) and the new soft checks
# (body length, long-tail, sentence length, Flesch, tertiary terms). A failed hard check is a
# problem report for the content team — there is no repair call and no retry.
# ===================================================================================================


def run_checks_v2(
    title: str, sections: list[dict], plan: dict, inp, faq: list[dict] | None = None,
    sponsor_brand_terms: list[str] = (), sponsor_text: str = "",
    report_text: str = "", report_name: str = "",
    selection: dict | None = None, hits: list | None = None,
) -> list[CheckResult]:
    import planning

    faq = list(faq or [])
    paragraphs = [p for s in sections for p in s["paragraphs"]]
    full = "\n".join(paragraphs)
    market_name = getattr(inp, "market_name", "") or report_name
    results: list[CheckResult] = []

    # ---- kept: the retained sponsor paragraph, placed once, unaltered, in the intro slot
    token_count = full.count(SPONSOR_TOKEN)
    placed = sum(p.count(SPONSOR_TOKEN) for p in paragraphs)
    non_token_overlap = _longest_shared_run(full.replace(SPONSOR_TOKEN, ""), sponsor_text) if sponsor_text else 0
    leaked = placed != 1 and non_token_overlap >= 6
    results.append(CheckResult(
        "Retained backlink paragraph placed once, unaltered",
        token_count == 1,
        (f"placed {token_count} time(s)" + (" — the model appears to have written out the retained "
         "paragraph's own words instead of the placeholder; check the intro manually" if leaked else "")),
    ))
    intro_paragraphs = sections[0]["paragraphs"] if sections else []
    in_intro_slot = token_count == 1 and any(SPONSOR_TOKEN in p for p in intro_paragraphs)
    results.append(CheckResult(
        "Retained paragraph sits in the intro, not mid-article",
        in_intro_slot,
        "" if in_intro_slot else "expected the placeholder exactly once in the unheaded intro section",
    ))

    # ---- kept: the CMI report link — exactly once, early, the market's first mention, correct anchor
    cmi_links = re.findall(CMI_LINK_PATTERN, full)
    results.append(CheckResult("CMI report linked exactly once", len(cmi_links) == 1, f"found {len(cmi_links)}"))
    link_offset, link_is_first_mention, _ = cmi_link_placement(sections, sponsor_text, market_name)
    results.append(CheckResult(
        f"CMI link sits within the first {MAX_LINK_WORD_OFFSET} words",
        link_offset is not None and link_offset <= MAX_LINK_WORD_OFFSET,
        "no CMI link found" if link_offset is None else f"the link starts at word {link_offset}",
    ))
    if link_offset is not None:
        results.append(CheckResult(
            "The first mention of the market name is the CMI link",
            link_is_first_mention,
            "" if link_is_first_mention else f"{market_name!r} appears in plain text before the link",
        ))
    if report_name:
        anchor_matches_report = any(link.strip() == report_name.strip() for link in cmi_links)
        results.append(CheckResult(
            "CMI link anchor names the confirmed report",
            anchor_matches_report,
            "" if anchor_matches_report else f"expected {report_name!r}, found {cmi_links!r}",
        ))

    # ---- hard (§3.6a): the title passes planning's own hard title checks
    title_hard, title_soft = planning.title_problems(
        title, market_name, [b for b in (plan.get("title_basis") or [])], set(inp.items))
    results.append(CheckResult(
        "Title passes the plan's hard title checks",
        not title_hard,
        "; ".join(title_hard) if title_hard else "",
    ))
    if title_soft:
        results.append(CheckResult(
            "Title soft notes (soft)", True, "; ".join(title_soft),
        ))

    # ---- hard (§3.9): the draft carries the plan's validated structure ...
    structure = v2_structure_problems(title, sections, plan, inp)
    results.append(CheckResult(
        "Draft structure matches the validated plan (title, SOP order, 2-3 MSQ headings)",
        not structure,
        "; ".join(structure) if structure else "",
    ))

    # ---- hard (§3.7): secondary keywords — 4-6 assigned, each used in its section, one per paragraph
    keyword_problems = v2_keyword_problems(sections, plan, market_name)
    results.append(CheckResult(
        "Secondary keywords: 4-6 assigned, each used in its own section, no paragraph stuffed with them",
        not keyword_problems,
        "; ".join(keyword_problems) if keyword_problems else "",
    ))
    overlap_notes = v2_keyword_overlap_notes(sections, plan, market_name)
    results.append(CheckResult(
        "No paragraph carries two assigned secondary keywords (soft)",
        not overlap_notes,
        "; ".join(overlap_notes) if overlap_notes else "",
    ))

    # ---- hard (§3.6b): intro -> transition -> market, with no switches back to the publisher's topic
    order_problems = block_order_problems(sections)
    results.append(CheckResult(
        "Block order: publisher intro, transition, then only market sections",
        not order_problems,
        "; ".join(order_problems) if order_problems else "",
    ))

    # ---- hard (§3.6b): intro <= 20%, transition 5-10%, market >= 70% of the body words
    share_problems = block_share_problems(sections, sponsor_text)
    results.append(CheckResult(
        "Block shares: intro <= 20%, transition 5-10%, market >= 70%",
        not share_problems,
        "; ".join(share_problems) if share_problems else "",
    ))

    # ---- hard (§3.9): every number in the draft and the FAQ traces to an allowed source
    trace_problems = v2_number_trace_problems(sections, faq, inp, sponsor_text)
    trace_bad = [p for p in trace_problems]
    results.append(CheckResult(
        "Every number traces to the fact sheet, evidence, guest points or sponsor",
        not trace_bad,
        "; ".join(trace_bad[:5]) + (f" (+{len(trace_bad) - 5} more)" if len(trace_bad) > 5 else "")
        if trace_bad else "",
    ))

    # ---- hard (§3.2): an outside source's figure names that source in the same sentence, never CMI
    attribution_problems = v2_evidence_attribution_problems(sections, inp)
    results.append(CheckResult(
        "Outside-source figures name their source in the same sentence, never CMI",
        not attribution_problems,
        "; ".join(attribution_problems) if attribution_problems else "",
    ))

    # ---- hard (§3.9): placeholder figures ("USD <year>", "a stated rate", bare "%") never print
    placeholder_problems = v2_placeholder_figure_problems(sections, faq)
    results.append(CheckResult(
        "No placeholder figures reached the draft",
        not placeholder_problems,
        "; ".join(placeholder_problems) if placeholder_problems else "",
    ))

    # ---- hard (§3.8): every FAQ question is a planned answerable MSQ, its figures traced
    faq_problems = v2_faq_problems(faq, plan, inp, sections)
    results.append(CheckResult(
        "FAQ questions are planned MSQs and their figures trace",
        not faq_problems,
        "; ".join(faq_problems) if faq_problems else "",
    ))

    # ---- kept: the market sections carry the SOPs' own requirements
    sub_paragraphs = sum(len(s["paragraphs"]) for s in sections if "subsegment" in s["sop"])
    results.append(CheckResult("Two sub-segment analyses", sub_paragraphs >= 2, f"found {sub_paragraphs} sub-segment paragraph(s)"))
    trend_paragraphs = sum(count_items_in(s["paragraphs"]) for s in sections if "trends" in s["sop"])
    results.append(CheckResult("Three trends (at least 3 trend paragraphs)", trend_paragraphs >= 3, f"{trend_paragraphs} paragraph(s)"))
    us_words = sum(_words(p) for s in sections if "country_us" in s["sop"] for p in s["paragraphs"])
    results.append(CheckResult("U.S. analysis is 100-200 words", 100 <= us_words <= 200, f"{us_words} words"))

    # ---- kept: CMI analyst opinion voice, 1-2 labelled statements
    opinions = count_cmi_opinions(paragraphs)
    results.append(CheckResult(
        f"CMI analyst opinion: {CMI_OPINIONS_MIN} to {CMI_OPINIONS_MAX} labelled statements",
        CMI_OPINIONS_MIN <= opinions <= CMI_OPINIONS_MAX,
        f"found {opinions}",
    ))

    # ---- kept: verbatim overlap with the report stays below 5%
    if report_text:
        overlap_pct, overlap_examples = _verbatim_overlap(
            paragraphs, [("CMI report", report_text)], report_name=report_name)
        results.append(CheckResult(
            "Verbatim overlap with source material is below 5%",
            overlap_pct <= 5.0,
            f"{overlap_pct:.1f}% of draft words fall in a run of >= 10 words copied from a source"
            + (" — e.g. " + " | ".join(overlap_examples) if overlap_examples else ""),
        ))

    # ---- kept: recency — stale dated events and undated "trends" sources
    stale = _stale_event_mentions(sections)
    results.append(CheckResult(
        "Dated events cited are from the last 12 months",
        not stale,
        "stale: " + "; ".join(stale) if stale else "",
    ))
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

    # ---- kept: the guest brand is never mentioned outside the retained paragraph
    non_sponsor = "\n".join(p for p in paragraphs if p.replace(SPONSOR_TOKEN, " ").strip()).lower()
    brand_leaks = [t for t in sponsor_brand_terms if t and t.lower() in non_sponsor]
    results.append(CheckResult(
        "Guest brand not mentioned outside the retained paragraph",
        not brand_leaks,
        ", ".join(brand_leaks),
    ))

    # ---- soft (§3.9): body length 1,200-1,500 words
    total = _words(full.replace(SPONSOR_TOKEN, sponsor_text)) if sponsor_text else _words(full)
    results.append(CheckResult("Length is 1,200-1,500 words (soft)", 1200 <= total <= 1500, f"{total} words"))

    # ---- soft (§3.9): 2-3 long-tail keywords used
    longtail_phrases = [lt["keyword"] for lt in getattr(inp, "longtails", [])]
    if longtail_phrases:
        body = full.replace(SPONSOR_TOKEN, " ")
        used_lt = [kw for kw in longtail_phrases if longtail_spans(body, kw)]
        results.append(CheckResult(
            f"Uses {LONGTAIL_MIN_USED}-{LONGTAIL_MAX_USED} long-tail keywords (soft)",
            LONGTAIL_MIN_USED <= len(used_lt) <= LONGTAIL_MAX_USED,
            f"{len(used_lt)} of {len(longtail_phrases)} used: " + (", ".join(used_lt) or "none"),
        ))

    # ---- soft (§3.9): no sentence over the word ceiling
    long_sentences: list[str] = []
    for s in sections:
        label = (s.get("heading") or "").strip() or (s["sop"][0] if s.get("sop") else "intro")
        for p in s["paragraphs"]:
            for sentence in re.split(r"(?<=[.!?])\s+", _sponsor_replacement(p, sponsor_text)):
                n = _words(sentence)
                if n > _V2_SENTENCE_MAX_WORDS:
                    long_sentences.append(f"{label}: a sentence runs {n} words")
    results.append(CheckResult(
        f"No sentence over {_V2_SENTENCE_MAX_WORDS} words (soft)",
        not long_sentences,
        "; ".join(long_sentences[:5]) + (f" (+{len(long_sentences) - 5} more)" if len(long_sentences) > 5 else "")
        if long_sentences else "",
    ))

    # ---- soft (§3.9): readability — plain prose, Flesch at least the v2 floor
    score = _flesch_reading_ease(full.replace(SPONSOR_TOKEN, sponsor_text) if sponsor_text else full)
    if score is None:
        results.append(CheckResult(
            f"Readability (Flesch Reading Ease, soft)", True,
            "draft too short to measure a reading score",
        ))
    else:
        results.append(CheckResult(
            f"Readability Flesch >= {_V2_FLESCH_MIN} (soft)",
            score >= _V2_FLESCH_MIN,
            f"score {score:.0f}/100",
        ))

    # ---- soft (§3.9): the search demand's tertiary terms are used
    terms = [t for t in getattr(inp, "terms", []) if t]
    if terms:
        used_terms = [t for t in terms if keyword_used(full, t)]
        floor = max(1, len(terms) // 2)
        results.append(CheckResult(
            "Tertiary terms used (soft)",
            len(used_terms) >= floor,
            f"used {len(used_terms)} of {len(terms)} (expected at least {floor}); "
            f"not found: {', '.join(t for t in terms if t not in used_terms)}",
        ))

    return results
