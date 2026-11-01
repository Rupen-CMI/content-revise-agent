"""Content rules judged on the finished draft, in code: no other region inside the U.S. section, no negative wording,
and no point made twice. Each function is pure and returns the problems it found as short sentences for the editor;
checks.run_checks_v2 turns them into hard checks. Nothing here calls a model."""
import re

import polarity

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])(?<!U\.S\.)(?<!U\.K\.)(?<!Inc\.)(?<!Ltd\.)\s+(?=[A-Z0-9\"“])|\n+")

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
