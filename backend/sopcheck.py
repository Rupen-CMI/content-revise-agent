"""SOP compliance check for a finished draft.

A standalone draft carries none of a run's context (the plan, the fact sheet, the sponsor text, the
keyword assignments), so only the checks that can be judged from the draft's own text and Word
structure run here. Each one reuses the check the revamp chain already applies; the ones that need
a run's context are listed as skipped, with the reason, so a pass never claims more than was measured.
"""
import re
from io import BytesIO
from urllib.parse import urlparse

import docx
from docx.oxml.ns import qn

import checks
from config import CMI_HOST

_FAQ_HEADING_RE = re.compile(r"\bfaqs?\b|frequently asked", re.IGNORECASE)

SKIPPED = [
    ("Every number traces to the report, evidence or guest points", "needs the run's fact sheet"),
    ("Outside-source figures name their source", "needs the run's evidence"),
    ("Secondary keywords used in their own sections", "needs the run's keyword assignment"),
    ("FAQ questions are the planned most-searched questions", "needs the run's search demand"),
    ("Transition is 5-10% of the body", "the transition cannot be told apart from the opening by text alone"),
    ("Two sub-segment analyses, three trends, U.S. section of 100-200 words", "sections cannot be identified by text alone"),
    ("Verbatim overlap with the report is below 5%", "needs the report text"),
    ("Guest brand not mentioned outside the retained paragraph", "needs the sponsor paragraph"),
]


def _style_name(p) -> str:
    try:
        return p.style.name or ""
    except Exception:
        return ""


def _read(raw: bytes) -> dict:
    """The draft as {title, sections, links, headings_found}. A section is {heading, paragraphs}; the
    first section is the unheaded opening. Paragraph links are [(anchor, url)]."""
    document = docx.Document(BytesIO(raw))
    rels = {rid: rel.target_ref for rid, rel in document.part.rels.items() if "hyperlink" in rel.reltype}
    title = ""
    sections: list[dict] = [{"heading": "", "paragraphs": [], "links": []}]
    headings_found = False
    first = True
    for p in document.paragraphs:
        text = "".join(t.text or "" for t in p._p.iter(qn("w:t"))).strip()
        if not text:
            continue
        if first:
            first = False
            title = text
            continue
        style = _style_name(p)
        if style.startswith("Heading") or style == "Title":
            headings_found = True
            sections.append({"heading": text, "paragraphs": [], "links": []})
            continue
        links = []
        for h in p._p.findall(qn("w:hyperlink")):
            url = rels.get(h.get(qn("r:id")))
            if url:
                links.append(("".join(t.text or "" for t in h.iter(qn("w:t"))), url))
        sections[-1]["paragraphs"].append(text)
        sections[-1]["links"].append(links)
    return {"title": title, "sections": sections, "headings_found": headings_found}


def _is_cmi(url: str) -> bool:
    return urlparse(url).netloc.lower().endswith(CMI_HOST)


def _result(name: str, passed: bool, detail: str = "", soft: bool = False) -> dict:
    return {"name": name, "passed": passed, "detail": detail, "soft": soft}


def _cap(items: list[str], n: int = 6) -> str:
    return "; ".join(items[:n]) + (f" (+{len(items) - n} more)" if len(items) > n else "")


def check_draft(raw: bytes) -> dict:
    draft = _read(raw)
    sections = draft["sections"]
    headed = draft["headings_found"]
    # Section tags for the shared checks: the opening carries none, every headed section carries one.
    tagged = [{"heading": s["heading"], "paragraphs": s["paragraphs"], "sop": ["x"] if s["heading"] else []}
              for s in sections]
    all_paragraphs = [p for s in sections for p in s["paragraphs"]]
    total = sum(checks._words(p) for p in all_paragraphs)
    results: list[dict] = []
    skipped = [{"name": n, "reason": r} for n, r in SKIPPED]

    # ---- links: the CMI report linked once, early; the sponsor's outside link kept in the opening
    cmi_links, words_before, cmi_offset, outside_in_opening, outside_total = 0, 0, None, 0, 0
    for si, s in enumerate(sections):
        for p, links in zip(s["paragraphs"], s["links"]):
            for _, url in links:
                if _is_cmi(url):
                    cmi_links += 1
                    if cmi_offset is None:
                        cmi_offset = words_before
                else:
                    outside_total += 1
                    outside_in_opening += 1 if si == 0 else 0
            words_before += checks._words(p)
    results.append(_result("CMI report linked exactly once", cmi_links == 1, f"found {cmi_links}"))
    results.append(_result(
        f"CMI link sits within the first {checks.MAX_LINK_WORD_OFFSET} words",
        cmi_offset is not None and cmi_offset <= checks.MAX_LINK_WORD_OFFSET,
        "no CMI link found" if cmi_offset is None else f"the link sits after {cmi_offset} words"))
    if headed:
        results.append(_result(
            "The sponsor's outside backlink sits in the opening",
            outside_in_opening >= 1,
            "no outside link found in the draft" if outside_total == 0
            else f"{outside_total} outside link(s), none before the first heading" if outside_in_opening == 0 else ""))

    # ---- structure: question headings, FAQ, opening share
    if headed:
        faq_at = next((i for i, s in enumerate(sections) if i and _FAQ_HEADING_RE.search(s["heading"])), None)
        heads = [s["heading"] for i, s in enumerate(sections) if i and i != faq_at]
        questions = [h for h in heads if h.rstrip().endswith("?")]
        results.append(_result(
            "2-3 headings are questions (most searched questions)",
            2 <= len(questions) <= 3, f"{len(questions)} question heading(s) of {len(heads)}"))
        results.append(_result(
            "FAQ section present", faq_at is not None,
            "" if faq_at is None else f"{sum(1 for p in sections[faq_at]['paragraphs'] if p.rstrip().endswith('?'))} question(s)"))
        opening = sum(checks._words(p) for p in sections[0]["paragraphs"])
        share = opening / total if total else 0
        results.append(_result(
            "Opening is at most 20% of the body", share <= checks._V2_INTRO_CAP,
            f"{opening} words = {share:.0%} of {total}"))
    else:
        for n in ("2-3 headings are questions (most searched questions)", "FAQ section present", "Opening is at most 20% of the body"):
            skipped.append({"name": n, "reason": "no headings found: the draft's headings must use Word's Heading styles"})

    # ---- wording
    # Follows the final screen: a check disconnected there stays out of this tool too.
    if checks.V2_VAGUE_CHECK_NAME not in checks.V2_DISCONNECTED_CHECKS:
        vague = checks.v2_vague_reference_problems(tagged)
        results.append(_result(checks.V2_VAGUE_CHECK_NAME, not vague, _cap(vague)))
    placeholder = checks.v2_placeholder_figure_problems(tagged)
    results.append(_result("No placeholder figures", not placeholder, _cap(placeholder)))
    stale = checks._stale_event_mentions(tagged)
    results.append(_result("Dated events cited are from the last 12 months", not stale, "stale: " + "; ".join(stale) if stale else ""))
    opinions = checks.count_cmi_opinions(all_paragraphs)
    results.append(_result(
        f"CMI analyst opinion: {checks.CMI_OPINIONS_MIN} to {checks.CMI_OPINIONS_MAX} labelled statements",
        checks.CMI_OPINIONS_MIN <= opinions <= checks.CMI_OPINIONS_MAX, f"found {opinions}"))

    # ---- soft
    jargon = checks.v2_opening_jargon_notes(tagged)
    results.append(_result("The opening holds no measurements or units (soft)", not jargon, "; ".join(jargon), soft=True))
    passive = checks.v2_active_voice_notes(tagged)
    results.append(_result("Active voice: at most 1 sentence in 6 is passive (soft)", not passive, "; ".join(passive), soft=True))
    results.append(_result("Length is 1,200-1,500 words (soft)", 1200 <= total <= 1500, f"{total} words", soft=True))

    return {"title": draft["title"], "word_count": total, "checks": results, "skipped": skipped}
