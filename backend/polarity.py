"""Positive-only backstop: deterministic detection of negative market wording.

The articles carry favourable trends and evidence only. The prompts ask for that; this module is the code check behind
them, applied to fact-sheet statements, selected evidence, question text and the finished draft. Pure: no network, no model.
"""
import re

# Stems of words that signal a downside. A stem matches the word and its inflections (hamper, hampers, hampering).
NEGATIVE_STEMS = (
    "restraint", "restrain", "hamper", "hinder", "hurdle", "barrier", "challenge", "setback", "drawback", "downside",
    "decline", "decrease", "shrink", "shrunk", "slump", "downturn", "recession", "slowdown", "headwind", "crisis",
    "loss", "losses", "risks", "threat", "concerns", "uncertain", "volatil", "shortage", "scarcity", "disrupt",
    "penalt", "lawsuit", "layoff", "bankrupt", "fraud", "breach", "vulnerab", "delay", "shutdown", "stagnat",
    "weaken", "weakness", "obstacle", "impediment", "bottleneck", "pressures", "burden", "backlash", "recall",
    "fatal", "outage", "cyberattack", "ransomware", "violation", "sanction", "tariff", "inflation",
)
# Whole words that must not match as a stem inside a longer word ("fine" in "refine", "ban" in "bank").
NEGATIVE_WORDS = ("ban", "bans", "banned", "fine", "fines", "fined", "dropped", "falling", "fell", "weak", "struggle",
                  "struggles", "struggling", "fail", "fails", "failed", "failure", "failures", "cost burden", "high cost",
                  "lack of", "limited by", "pose risk", "poses risk", "posing risk", "face risk", "faces risk", "facing risk",
                  "high risk", "rising risk", "growing risk", "increasing risk", "major risk", "serious risk",
                  "significant risk", "concern over", "concern about", "pricing pressure", "margin pressure")

_STEM_RE = re.compile(r"\b(?:" + "|".join(NEGATIVE_STEMS) + r")\w*", re.I)
_WORD_RE = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in NEGATIVE_WORDS) + r")\b", re.I)

# Phrases that use a negative word to say something positive. They are blanked before matching, so "no longer a barrier"
# or "reduces the risk of fraud" never counts as negative wording.
_NEUTRALISING = tuple(re.compile(p, re.I) for p in (
    r"\b(?:no longer|not a|never a|without|free of|free from|overcom\w*|eliminat\w*|remov\w*|lower\w*|reduc\w*|mitigat\w*|"
    r"minimi[sz]\w*|eas\w*|resolv\w*|address\w*|tackl\w*|prevent\w*|cut\w*|curb\w*|combat\w*|offset\w*)\s+(?:the\s+|a\s+|an\s+|its\s+|their\s+)?"
    r"(?:[\w-]+\s+){0,4}?(?:restraints?|hurdles?|barriers?|challenges?|setbacks?|drawbacks?|risks?|threats?|concerns?|"
    r"shortages?|delays?|losses|loss|costs?|burdens?|pressures?|fraud|breaches?|vulnerabilit\w+|bottlenecks?|obstacles?|"
    r"uncertaint\w+|volatilit\w+|disruptions?|inflation|failures?|outages?)\b(?:\s+(?:of|from|in|for)\s+(?:the\s+|a\s+)?\w+)?",
    r"\bresilien\w*\b",
    r"\bturn(?:s|ed|ing)?\s+(?:a\s+)?(?:challenge|setback|risk|threat)s?\s+into\b",
))


def _neutralise(text: str) -> str:
    for pattern in _NEUTRALISING:
        text = pattern.sub(" ", text)
    return text


def market_words(market_name: str) -> frozenset[str]:
    """The words of a market's own name. A risk-management or fraud-detection market names its subject in every sentence,
    so a word the market's name carries is never read as negative wording for that market."""
    return frozenset(re.findall(r"[a-z]{3,}", (market_name or "").lower()))


def negative_hits(text: str, ignore=frozenset()) -> list[str]:
    """The distinct negative words found in `text`, lowercase, in order of first appearance. A hit that starts with (or is
    started by) an `ignore` word is skipped; see market_words."""
    if not text:
        return []
    cleaned = _neutralise(text)
    found: list[str] = []
    for m in sorted([*_STEM_RE.finditer(cleaned), *_WORD_RE.finditer(cleaned)], key=lambda m: m.start()):
        word = m.group(0).lower()
        if any(word.startswith(i) or i.startswith(word) for i in ignore):
            continue
        if word not in found:
            found.append(word)
    return found


def is_negative(text: str, ignore=frozenset()) -> bool:
    return bool(negative_hits(text, ignore))


def positive_only(sentences: list[str], ignore=frozenset()) -> tuple[list[str], list[str]]:
    """(kept, dropped): the sentences with no negative wording, and the ones with some."""
    kept, dropped = [], []
    for s in sentences:
        (dropped if is_negative(s, ignore) else kept).append(s)
    return kept, dropped
