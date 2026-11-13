"""The equivalent forms of a market name.

Many catalog reports name a market with a bracketed abbreviation ("Search Engine Optimization (SEO) Services
Market", "Battery Electric Vehicle (BEV) Market"). Text about the market — a search question, a title, a heading,
a keyword phrase — writes the name either with the full words and no bracket ("Search Engine Optimization Services
Market") or with the abbreviation in place of the words it abbreviates ("SEO Services Market"). Every decision that
asks whether text names the market or carries the primary keyword must therefore accept any equivalent form of the
name. `market_name_forms` builds the forms; a name with no bracketed abbreviation has exactly one form (itself), so
no decision changes for it.
"""
import re

_BRACKET_RE = re.compile(r"\(([^)]+)\)")
_LETTER_RE = re.compile(r"[A-Za-z]")
_WORD_RE = re.compile(r"[A-Za-z0-9]+")


def market_name_forms(market_name: str) -> list[str]:
    """The market name's equivalent forms, original first, deduped. A bracket counts as an abbreviation when it
    spells the initials of the words that stand right before it ("SEO" of "Search Engine Optimization", a plural
    abbreviation's trailing 's' ("EVs") allowed); each such bracket then yields the name with the bracket dropped
    and the name with those words replaced by the abbreviation. A bracket that abbreviates nothing ("(Premium)")
    yields nothing, and the original form is always kept."""
    name = " ".join(str(market_name or "").split())
    if not name:
        return []
    full = short = name
    for m in _BRACKET_RE.finditer(name):
        inner = m.group(1).strip()
        letters = _LETTER_RE.findall(inner)
        if len(letters) < 2:
            continue
        words = _WORD_RE.findall(name[:m.start()])
        if len(letters) == len(words) + 1 and letters[-1].lower() == "s":
            letters = letters[:-1]
        if len(letters) > len(words) or not all(a.lower() == w[0].lower()
                                                for a, w in zip(letters, words[-len(letters):])):
            continue
        bracket_rx = r"\s*\(\s*" + re.escape(inner) + r"\s*\)"
        full = re.sub(bracket_rx, " ", full, count=1)
        run_rx = r"\b" + r"\b\s+".join(re.escape(w) for w in words[-len(letters):]) + r"\b" + bracket_rx
        short = re.sub(run_rx, lambda _: inner, short, count=1)
    out = []
    for form in (name, " ".join(full.split()), " ".join(short.split())):
        if form not in out:
            out.append(form)
    return out
