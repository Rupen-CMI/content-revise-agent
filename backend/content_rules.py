"""Content rules judged on the finished draft, in code: no other region inside the U.S. section, no negative wording,
and no point made twice. Each function is pure and returns the problems it found as short sentences for the editor;
checks.run_checks_v2 turns them into hard checks. Nothing here calls a model."""
import re

import polarity
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
        for p in s["paragraphs"]:
            for sentence in _sentences(p):
                names = {m.group(0).lower() for m in _OTHER_RE.finditer(sentence)}
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
    return frozenset(
        w[:-1] if len(w) > 4 and w.endswith("s") else w
        for w in re.findall(r"[a-z0-9]+", sentence.lower()) if len(w) > 3 and w not in _STOP
    )


def repeated_point_problems(sections: list[dict], threshold: float = 0.55, min_stems: int = 6, market_name: str = "") -> list[str]:
    """Two sentences in DIFFERENT market sections that make the same point: their content words overlap by at least
    `threshold` of the shorter sentence's. A point is made once in the article."""
    seen: list[tuple[str, frozenset[str], str]] = []
    out: list[str] = []
    own = _content_stems(market_name)       # the market's own name is in every section and says nothing about the point
    for s in sections:
        if not _is_market(s):
            continue
        label = (s.get("heading") or "").strip() or s["sop"][0]
        for p in s["paragraphs"]:
            for sentence in _sentences(p):
                stems = _content_stems(sentence) - own
                if len(stems) < min_stems:
                    continue
                for other_label, other_stems, other in seen:
                    if other_label == label:
                        continue
                    shared = len(stems & other_stems)
                    if shared / min(len(stems), len(other_stems)) >= threshold:
                        out.append(f"{label} repeats {other_label}: {sentence[:70]!r} / {other[:70]!r}")
                        break
                seen.append((label, stems, sentence))
    return out


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
