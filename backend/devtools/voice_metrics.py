"""Measure a draft's register against the content team's reference article (Assessment Services Market).

The reference's measured profile (see memory: content-quality-round2): sentence length mean 18 / median 17 (7% over 30
words), Flesch about 15-20, about 2.0 syllables per word, one named and dated outside source per trend followed by a
"commercial implication" paragraph. This module reports the same numbers for any list of sections, so a prompt change
can be judged on measurements and not on impression.

Usage as a library: voice_metrics.measure(sections) -> dict; voice_metrics.report(sections, label) prints it.
Run from backend/: python devtools/voice_metrics.py <path to a draft text file written by e2e_api.py>
"""
import re
import statistics as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subheads  # noqa: E402

REFERENCE = {"sentence_mean": 18.1, "sentence_median": 17, "share_over_30": 0.07, "flesch": 17, "syll_per_word": 2.03}

_CASUAL = re.compile(r"\b(?:big|huge|boom\w*|skyrocket\w*|a lot|lots of|kind of|sort of|super|really|very|basically|pretty much|"
                     r"game[- ]changer|cutting[- ]edge|dive into|let's|here's|you|your)\b", re.I)
_CONTRACTION = re.compile(r"\b\w+(?:n't|'re|'ve|'ll|'d)\b|\b\w+(?:n’t|’re|’ve|’ll|’d)\b", re.I)
_YEAR = re.compile(r"\b20[12]\d\b")
_POINT_OPEN = re.compile(r"^(?:this|that|these|those|such|it|they)\b", re.I)


def _sents(text: str) -> list[str]:
    text = text.replace("U.S.", "US").replace("Inc.", "Inc").replace("plc.", "plc").replace("Corp.", "Corp")
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z“\"(\[])", text) if s.strip()]


def _syll(w: str) -> int:
    w = re.sub(r"[^a-z]", "", w.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def flesch(text: str) -> float:
    words = re.findall(r"[A-Za-z']+", text)
    sents = _sents(text)
    if not words or not sents:
        return 0.0
    return 206.835 - 1.015 * (len(words) / len(sents)) - 84.6 * (sum(_syll(w) for w in words) / len(words))


def measure(sections: list[dict]) -> dict:
    """Numbers for the market sections' prose (opening, transition and sub-headings excluded; the retained sponsor
    paragraph is a placeholder and is skipped)."""
    prose, per_section = [], {}
    for s in subheads.strip_subheads(sections):
        sop = s.get("sop") or []
        if not sop or sop == ["transition"]:
            continue
        paras = [p for p in s["paragraphs"] if "[[" not in p]
        per_section[sop[0]] = sum(len(p.split()) for p in paras)
        prose += paras
    text = " ".join(prose)
    sents = [x for p in prose for x in _sents(p.replace("\n- ", " "))]
    lens = [len(x.split()) for x in sents]
    words = re.findall(r"[A-Za-z']+", text)
    out = {
        "words": sum(len(p.split()) for p in prose), "sentences": len(sents), "per_section": per_section,
        "sentence_mean": round(st.mean(lens), 1) if lens else 0, "sentence_median": st.median(lens) if lens else 0,
        "share_over_30": round(sum(n > 30 for n in lens) / len(lens), 2) if lens else 0,
        "share_under_8": round(sum(n < 8 for n in lens) / len(lens), 2) if lens else 0,
        "flesch": round(flesch(text)), "syll_per_word": round(sum(_syll(w) for w in words) / max(1, len(words)), 2),
        "casual_hits": sorted({m.group(0).lower() for m in _CASUAL.finditer(text)}),
        "contractions": sorted({m.group(0).lower() for m in _CONTRACTION.finditer(text)}),
        "pointer_openers": sum(bool(_POINT_OPEN.match(x)) for x in sents),
    }
    trends = next((s for s in sections if "trends" in (s.get("sop") or [])), None)
    if trends:
        lead, blocks = subheads.split_blocks(trends["paragraphs"])
        out["trend_blocks"] = [{
            "heading": h, "paragraphs": len(ps), "words": sum(len(p.split()) for p in ps),
            "years_in_first": len(_YEAR.findall(ps[0])) if ps else 0, "figures_in_first": len(re.findall(r"\d", ps[0] if ps else "")) > 0,
        } for h, ps in blocks]
    return out


def report(sections: list[dict], label: str) -> dict:
    m = measure(sections)
    print(f"--- {label}")
    print(f"  prose words {m['words']} | sentences {m['sentences']} | per section {m['per_section']}")
    print(f"  sentence length mean {m['sentence_mean']} (ref {REFERENCE['sentence_mean']}) median {m['sentence_median']} (ref {REFERENCE['sentence_median']}) "
          f"over 30: {m['share_over_30']:.0%} (ref {REFERENCE['share_over_30']:.0%}) under 8: {m['share_under_8']:.0%}")
    print(f"  Flesch {m['flesch']} (ref ~{REFERENCE['flesch']}) | syllables/word {m['syll_per_word']} (ref {REFERENCE['syll_per_word']})")
    print(f"  casual words: {m['casual_hits'] or 'none'} | contractions: {m['contractions'] or 'none'} | sentences opening with a pointing word: {m['pointer_openers']}")
    for i, b in enumerate(m.get("trend_blocks", []), 1):
        print(f"  trend {i}: {b['paragraphs']} paragraphs, {b['words']} words, first paragraph years {b['years_in_first']} figure {b['figures_in_first']} | {b['heading'][:70]}")
    return m


def longest_shared_run(a: str, b: str) -> int:
    """Longest run of consecutive words two texts share: how much of one was copied into the other."""
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    best, index = 0, {}
    for i, w in enumerate(wb):
        index.setdefault(w, []).append(i)
    for i, w in enumerate(wa):
        for j in index.get(w, []):
            k = 0
            while i + k < len(wa) and j + k < len(wb) and wa[i + k] == wb[j + k]:
                k += 1
            best = max(best, k)
    return best


if __name__ == "__main__":
    print("voice_metrics is a library; see ab_voice.py for the comparison run")
