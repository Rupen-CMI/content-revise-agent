import re
from dataclasses import dataclass
from datetime import date

import freshness
from prompts import CMI_LINK_PATTERN, SPONSOR_TOKEN

_FIGURE = re.compile(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(%|percent|bn\b|billion|mn\b|million|tn\b|trillion)", re.IGNORECASE)
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")

# Mirrors search.py's gate-token approach: strip words too generic to identify a specific
# market, so "market"/"services" matching everywhere doesn't make every section look on-topic.
_GENERIC_MARKET_WORDS = frozenset({
    "market", "markets", "global", "industry", "the", "and", "of", "for", "by", "in", "on", "a", "an",
    "to", "with", "based", "services", "service", "solutions", "solution", "systems", "system", "other",
})


def _market_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2 and w not in _GENERIC_MARKET_WORDS}


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
    primary_keyword: str = "", secondary_keywords: list[str] = (),
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

    # SELECT is instructed to only tag a fact "trends" when its source hit is a dated NEWS
    # result (see SELECT_USER) — a BACKGROUND (undated, encyclopedic) source has no verified
    # recency and must never be cited as an "ongoing trend". This re-derives the W<n> -> hit
    # link the same way run_build_evidence does, so a model that ignores the prompt rule is
    # still caught here rather than silently shipping an undated claim as current.
    if selection is not None and hits is not None:
        bad_trend_sources = []
        for e in selection.get("evidence") or []:
            if e.get("use_for") != "trends":
                continue
            m = re.fullmatch(r"W(\d+)", str(e.get("id", "")).strip())
            if not m or not 1 <= int(m.group(1)) <= len(hits):
                continue
            hit = hits[int(m.group(1)) - 1]
            if not getattr(hit, "is_news", False):
                bad_trend_sources.append(e.get("id"))
        results.append(CheckResult(
            "\"Trends\" facts are sourced from dated news, not undated background pages",
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

    trend_paras = sum(len(s["paragraphs"]) for s in sections if "trends" in s["sop"])
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

    # Soft targets: title and total length have a preferred range (40-75 chars, 1,400-1,700 words),
    # but small overshoots are a trivial human edit, not a correctness problem — only flag an outlier
    # far outside the range, so this doesn't cry wolf on every normal run.
    results.append(CheckResult("Title length reasonable (~40-75 characters, soft)", 25 <= len(title) <= 100, f"{len(title)} characters"))

    total = _words(full)
    results.append(CheckResult("Length reasonable (~1,400-1,700 words, soft)", 1000 <= total <= 2100, f"{total} words"))

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
        # WRITE_USER deliberately tells the model NOT to chase a specific count for the primary
        # keyword (counting while writing is exactly what produced stuffed-looking paragraphs in
        # testing) — so this is a loose sanity range, not a target: it only flags the two real
        # failure shapes, near-total absence (the market name barely appears at all) or outright
        # stuffing (several uses crammed into every paragraph), not anywhere in between. The floor
        # is a raw count, not a percentage: across repeated real runs on the same ~1,400-2,100 word
        # article, natural (correctly full-phrase, non-stuffed) usage consistently landed at only
        # 2-7 uses regardless of the article's exact length, i.e. 0.1-0.4% — a percentage floor set
        # from one early sample proved tighter than real natural output ever reliably hit.
        primary_count = full.lower().count(primary_keyword.lower())
        primary_pct = primary_count / total * 100
        results.append(CheckResult(
            "Primary keyword present, not stuffed (soft)",
            3 <= primary_count and primary_pct <= 3.0,
            f"{primary_keyword!r} used {primary_count}x ({primary_pct:.1f}% of {total} words)",
        ))

    if secondary_keywords:
        # WRITE_USER deliberately tells the model each secondary keyword is only used where it
        # fits naturally — using well under the full list is an expected, correct outcome, not a
        # miss. This check isn't "did every keyword get used" (that would reward forcing them in),
        # it's "did the category get a genuine effort at all" — a floor catching the case where
        # almost none landed, not a ceiling demanding all of them.
        secondary_counts = {kw: full.lower().count(kw.lower()) for kw in secondary_keywords}
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
