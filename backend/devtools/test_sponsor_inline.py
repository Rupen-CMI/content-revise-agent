"""Offline test for inline sponsor-placeholder support (no network, no LLM calls).

Run from backend/: python devtools/test_sponsor_inline.py

Covers:
  * sponsor_mode() detection
  * RevampResult/expand_segments rendering of an inline and a whole-paragraph placeholder,
    for a bare-phrase sponsor and a prose sponsor
  * the mode-aware placement checks used by pipeline._write_problems
  * the missing-token fallback (pipeline._force_sponsor_placeholder)
  * a real .docx round-trip through docx_io.write_docx, re-read with python-docx
"""

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import docx_io  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402

TOKEN = prompts.SPONSOR_TOKEN
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


PHRASE_TEXT = "Customer data management systems"
PHRASE_URL = "https://phrase-sponsor.example/product"
prose_text = "Teams that pick the right link anchor early move faster. That gap widens every year after."
PROSE_TEXT = prose_text
PROSE_URL = "https://prose-sponsor.example/backlink"
PROSE_ANCHOR = "right link anchor"

PHRASE = docx_io.Paragraph(index=0, text=PHRASE_TEXT, links=[docx_io.Link(anchor=PHRASE_TEXT, url=PHRASE_URL)])
PROSE = docx_io.Paragraph(index=0, text=PROSE_TEXT, links=[docx_io.Link(anchor=PROSE_ANCHOR, url=PROSE_URL)])
REPORT_URL = "https://cmi.example/report"


def intro(paragraphs: list[str]) -> list[dict]:
    return [{"heading": "", "sop": [], "paragraphs": paragraphs}]


def body(sections: list[dict]) -> str:
    return [s for s in sections if s["heading"] == ""]


def segments_of(paragraph: str, sponsor: docx_io.Paragraph) -> list[tuple[str, str | None]]:
    return pipeline.expand_segments(paragraph, sponsor, REPORT_URL)


def joined(paragraph: str, sponsor: docx_io.Paragraph) -> str:
    return "".join(t for t, _ in segments_of(paragraph, sponsor))


def urls(paragraph: str, sponsor: docx_io.Paragraph) -> list[tuple[str, str | None]]:
    return [(t, u) for t, u in segments_of(paragraph, sponsor) if u]


# ---------------------------------------------------------------- 1. mode detection
check("sponsor_mode: bare 4-word phrase -> phrase", pipeline.sponsor_mode(PHRASE_TEXT) == "phrase")
check(
    "sponsor_mode: prose with link anchor -> prose",
    pipeline.sponsor_mode(PROSE_TEXT) == "prose",
)
check(
    "sponsor_mode: lifted list item with a period -> prose",
    pipeline.sponsor_mode("Selectolax: Wraps the Lexbor engine and keeps the tree in C memory. Fast.") == "prose",
)
check(
    "sponsor_mode: 8-or-fewer words with sentence end -> prose",
    pipeline.sponsor_mode("customer data management systems are great.") == "prose",
)

# ---------------------------------------------------------------- 2. rendering: phrase sponsor
PHRASE_INLINE = (
    "Getting a clear picture of what a team already runs starts with the " + TOKEN
    + " a team already owns, because every later decision builds on that inventory."
)
PHRASE_ALONE = TOKEN
PHRASE_BETWEEN = "Sentences before the phrase. " + TOKEN + " Sentences after the phrase."

check(
    "segments: phrase inline restores the exact text mid-paragraph",
    joined(PHRASE_INLINE, PHRASE) == PHRASE_INLINE.replace(TOKEN, PHRASE_TEXT),
    f"got {joined(PHRASE_INLINE, PHRASE)!r}",
)
check(
    "segments: phrase inline carries the sponsor link on the exact anchor",
    urls(PHRASE_INLINE, PHRASE) == [(PHRASE_TEXT, PHRASE_URL)],
    f"got {urls(PHRASE_INLINE, PHRASE)!r}",
)
check(
    "segments: whole-paragraph phrase placeholder still renders as before",
    joined(PHRASE_ALONE, PHRASE) == PHRASE_TEXT and urls(PHRASE_ALONE, PHRASE) == [(PHRASE_TEXT, PHRASE_URL)],
)
check(
    "segments: phrase between sentences keeps the surrounding words exactly",
    joined(PHRASE_BETWEEN, PHRASE) == PHRASE_BETWEEN.replace(TOKEN, PHRASE_TEXT),
)

# ---------------------------------------------------------------- 3. rendering: prose sponsor
PROSE_BETWEEN = "Opening sentence. " + TOKEN + " Closing sentence."
check(
    "segments: prose between sentences restores the exact two-sentence text and its link",
    joined(PROSE_BETWEEN, PROSE) == PROSE_BETWEEN.replace(TOKEN, PROSE_TEXT)
    and urls(PROSE_BETWEEN, PROSE) == [(PROSE_ANCHOR, PROSE_URL)],
    f"got {joined(PROSE_BETWEEN, PROSE)!r} / {urls(PROSE_BETWEEN, PROSE)!r}",
)
check(
    "segments: whole-paragraph prose placeholder still renders as before",
    joined(TOKEN, PROSE) == PROSE_TEXT and urls(TOKEN, PROSE) == [(PROSE_ANCHOR, PROSE_URL)],
)

# ---------------------------------------------------------------- 4. mode-aware checks
inline_ok = intro([
    "Data lives everywhere, and the [[CMI_LINK:Zzz Widget Market]] keeps growing.",
    PHRASE_INLINE,
    "That inventory changes how a team talks about risk.",
])
check(
    "checks: phrase inline with 12+ words before and 6+ after is accepted",
    pipeline._sponsor_problems(inline_ok, PHRASE_TEXT) == [],
    repr(pipeline._sponsor_problems(inline_ok, PHRASE_TEXT)),
)
check(
    "checks: phrase alone on its own line is rejected",
    bool(pipeline._sponsor_problems(intro(["Lead-in.", TOKEN, "Pivot."]), PHRASE_TEXT)),
)
check(
    "checks: phrase merely between sentences (not inside a sentence) is rejected",
    bool(pipeline._sponsor_problems(intro(["Lead-in.", PHRASE_BETWEEN, "Pivot."]), PHRASE_TEXT)),
)
dup = intro(["Lead-in.", PHRASE_INLINE + " " + TOKEN, "Pivot."])
dup_problems = pipeline._sponsor_problems(dup, PHRASE_TEXT)
check("checks: duplicated placeholder is rejected", len(dup_problems) == 1 and "exactly once" in dup_problems[0])
phrase_repeated = intro([
    "Lead-in.",
    PHRASE_INLINE + " The phrase Customer data management systems matters.",
    "Pivot.",
])
check(
    "checks: sponsor phrase written out elsewhere in the draft is rejected",
    any("also appears written out" in p for p in pipeline._sponsor_problems(phrase_repeated, PHRASE_TEXT)),
)
check(
    "checks: placeholder outside the intro is rejected",
    bool(pipeline._sponsor_problems(
        intro(["Intro without it."]) + [{"heading": "Headed", "sop": ["trends"], "paragraphs": [PHRASE_INLINE]}],
        PHRASE_TEXT,
    )),
)
prose_between_ok = intro(["Lead-in.", PROSE_BETWEEN, "Pivot."])
check(
    "checks: prose between sentences is accepted",
    pipeline._sponsor_problems(prose_between_ok, PROSE_TEXT) == [],
    repr(pipeline._sponsor_problems(prose_between_ok, PROSE_TEXT)),
)
prose_own_ok = intro(["Lead-in.", TOKEN, "Pivot."])
check(
    "checks: prose as its own paragraph is accepted",
    pipeline._sponsor_problems(prose_own_ok, PROSE_TEXT) == [],
    repr(pipeline._sponsor_problems(prose_own_ok, PROSE_TEXT)),
)
prose_midsentence = intro(["Lead-in.", "This sentence ends with the " + TOKEN + " and keeps going.", "Pivot."])
check(
    "checks: prose embedded mid-sentence is rejected",
    bool(pipeline._sponsor_problems(prose_midsentence, PROSE_TEXT)),
)

# _write_problems (the retry feedback builder) must agree for the good inline case.
report = SimpleNamespace(name="Zzz Widget Market", url=REPORT_URL, sections={})
write_problems = pipeline._write_problems(inline_ok, report, original_body="", report_text="", sponsor_text=PHRASE_TEXT)
check(
    "_write_problems: no sponsor-placement problem for the good inline phrase draft",
    not any(TOKEN in p or "bare linked phrase" in p for p in write_problems),
    repr(write_problems),
)

# ---------------------------------------------------------------- 5. fallback
missing = intro(["Lead-in.", "A paragraph with no placeholder at all.", "Pivot."])
restored = pipeline._force_sponsor_placeholder([dict(s) for s in missing])
rp = restored[0]["paragraphs"]
check(
    "fallback: missing placeholder is inserted as intro paragraph 2",
    len(rp) == 4 and rp[1] == TOKEN,
    repr(rp),
)
check(
    "fallback: restored draft satisfies the location check",
    pipeline._sponsor_location_problem(restored) is None,
)
dup_restored = pipeline._force_sponsor_placeholder(intro(["Lead-in.", TOKEN + " inline " + TOKEN, "Pivot."]))
check(
    "fallback: duplicated placeholder collapses to exactly one at intro paragraph 2",
    dup_restored[0]["paragraphs"][1] == TOKEN
    and sum(p.count(TOKEN) for p in dup_restored[0]["paragraphs"]) == 1,
    repr(dup_restored[0]["paragraphs"]),
)

# ---------------------------------------------------------------- 6. docx round-trip
outline = intro([
    "Data lives everywhere, and the [[CMI_LINK:Zzz Widget Market]] keeps growing.",
    PHRASE_INLINE,
    "That inventory changes how a team talks about risk.",
])
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "out.docx"
    docx_io.write_docx(path, "A Title", outline, lambda text: pipeline.expand_segments(text, PHRASE, REPORT_URL))
    article = docx_io.read_docx(str(path))
    inline_paras = [p for p in article.paragraphs if PHRASE_TEXT in p.text]
    check("docx: the inline paragraph's text contains the sponsor phrase", len(inline_paras) == 1)
    if inline_paras:
        p = inline_paras[0]
        check(
            "docx: the sponsor phrase is a real hyperlink with the original url",
            any(l.anchor == PHRASE_TEXT and l.url == PHRASE_URL for l in p.links),
            repr([(l.anchor, l.url) for l in p.links]),
        )
        check(
            "docx: the writer's own words survive around the inserted phrase",
            p.text == PHRASE_INLINE.replace(TOKEN, PHRASE_TEXT),
            repr(p.text),
        )
    cmi_paras = [p for p in article.paragraphs if "Zzz Widget Market" in p.text]
    check(
        "docx: the CMI link still renders as its own hyperlink",
        bool(cmi_paras) and any(l.url == REPORT_URL for l in cmi_paras[0].links),
    )

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
    sys.exit(1)
print("all sponsor-inline tests passed")
