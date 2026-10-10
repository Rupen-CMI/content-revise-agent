import re
from dataclasses import dataclass

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


def _numbers(text: str) -> set[float]:
    return {float(n.replace(",", "")) for n in _NUMBER.findall(text)}


def _longest_shared_run(a: str, b: str, n: int = 8) -> int:
    """Longest run of shared n-grams (word-level) between two texts — a cheap plagiarism-style
    signal for "the model wrote out the real sponsor paragraph instead of the placeholder"."""
    words_a = re.findall(r"\w+", a.lower())
    words_b = re.findall(r"\w+", b.lower())
    grams_b = {tuple(words_b[i:i + n]) for i in range(len(words_b) - n + 1)}
    return sum(1 for i in range(len(words_a) - n + 1) if tuple(words_a[i:i + n]) in grams_b)


def _verbatim_leaks(paragraphs: list[str], original_body: str, n: int = 10) -> list[str]:
    """Flags any non-sponsor-slot paragraph that shares a long verbatim word-run with the
    ORIGINAL ARTICLE AS A WHOLE — catches the model copying some other original sentence
    through unchanged, not just the designated sponsor paragraph."""
    orig_words = re.findall(r"\w+", original_body.lower())
    orig_grams = {tuple(orig_words[i:i + n]) for i in range(len(orig_words) - n + 1)}
    leaks = []
    for p in paragraphs:
        if p.strip() == SPONSOR_TOKEN:
            continue
        words = re.findall(r"\w+", p.lower())
        if any(tuple(words[i:i + n]) in orig_grams for i in range(len(words) - n + 1)):
            leaks.append(p[:80] + ("…" if len(p) > 80 else ""))
    return leaks


def run_checks(
    title: str, sections: list[dict], source_text: str, sponsor_brand_terms: list[str],
    sponsor_text: str = "", original_body: str = "", report_name: str = "",
    selection: dict | None = None, hits: list | None = None,
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

    if original_body:
        verbatim_leaks = _verbatim_leaks(paragraphs, original_body)
        results.append(CheckResult(
            "No other original sentences copied through verbatim",
            not verbatim_leaks,
            "found in: " + " | ".join(verbatim_leaks) if verbatim_leaks else "",
        ))

    return results
