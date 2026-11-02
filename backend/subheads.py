"""Sub-headings inside a market section (the three trends each carry one).

A section is `{heading, sop, paragraphs}` and has one heading. A trend's own heading travels inside `paragraphs` as an
entry that starts with SUBHEAD_PREFIX, so every step that only moves or rewrites paragraph strings keeps it in place.
Renderers (docx_io, the web page) show such an entry as a smaller heading; the checks never see it, because they run on
`strip_subheads(sections)`, the same sections without those entries.

No imports: checks, docx_io, recheck and writer all use this module.
"""
SUBHEAD_PREFIX = "### "
TRENDS_PER_SECTION = 3
PARAGRAPHS_PER_TREND = 3


def is_subhead(paragraph) -> bool:
    return str(paragraph).startswith(SUBHEAD_PREFIX)


def subhead_text(paragraph) -> str:
    return str(paragraph)[len(SUBHEAD_PREFIX):].strip()


def make_subhead(text: str) -> str:
    return SUBHEAD_PREFIX + " ".join(str(text).split())


def strip_subheads(sections: list[dict]) -> list[dict]:
    """The sections as the checks read them: the same dicts, with every sub-heading entry left out of `paragraphs`."""
    out = []
    for s in sections:
        paras = s.get("paragraphs") or []
        if any(is_subhead(p) for p in paras):
            s = {**s, "paragraphs": [p for p in paras if not is_subhead(p)]}
        out.append(s)
    return out


def split_blocks(paragraphs: list[str]) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(paragraphs before the first sub-heading, [(sub-heading text, [its paragraphs]), ...])."""
    lead: list[str] = []
    blocks: list[tuple[str, list[str]]] = []
    for p in paragraphs:
        if is_subhead(p):
            blocks.append((subhead_text(p), []))
        elif blocks:
            blocks[-1][1].append(p)
        else:
            lead.append(p)
    return lead, blocks
