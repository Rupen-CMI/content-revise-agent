"""The canvas: the editor highlights words in the finished draft, says what to change, and gets a suggestion back.

The page shows DISPLAY text (link markers expanded to their words, the retained-paragraph placeholder to the client paragraph);
the run stores RAW text with the markers. This module maps one to the other, builds the one-call redraft prompt, holds the
returned text to the same guardrails as the original draft, and keeps the edit history (undo, revert) on `run.canvas`.

Nothing here touches FastAPI: failures are CanvasError(status, detail), which main.py turns into HTTP errors. Saving to disk and
the routes live in main.py / run_store.
"""
import copy
import logging
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import date

import checks
import clients as clients_mod
import config
import llm
import planning
import prompt_rules
import prompts
import subheads
import writer
from pipeline import expand_segments, sponsor_segments

SEL_OPEN, SEL_CLOSE = "⟦SEL⟧", "⟦/SEL⟧"
_PLACEHOLDER_RE = re.compile(r"⟦L(\d+)⟧")
# 1 = CMI report name, 2 = client link id, 3 = client link words (the same groups pipeline._LINK_TOKEN_RE has, after the sponsor token)
_MARKER_RE = re.compile(re.escape(prompts.SPONSOR_TOKEN) + "|" + prompts.CMI_LINK_PATTERN + "|" + prompts.CLIENT_LINK_PATTERN)
_REMOVE_LINK_RE = re.compile(r"\b(?:remove|delete|drop|take out|get rid of|without)\b[^.\n]{0,40}\blinks?\b|\bunlink\b|\bno link\b", re.IGNORECASE)
_PENDING_MAX = 20
_LOCK = threading.RLock()       # apply / undo / revert change the draft one at a time (a suggestion's LLM call never holds it)
_RETRY_TEMPERATURE = 0.5
_SENTINEL_LETTERS = "qxz"       # a paragraph's tracking line in the guardrail pass: letters only, because a digit would read as a figure


class CanvasError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


STALE = "The draft changed. Reload the page."


# ------------------------------------------------------------------------------------------------ display <-> raw

@dataclass
class Span:
    d0: int
    d1: int
    r0: int
    r1: int
    kind: str       # "text" (one display character per raw character) or an atomic link: "cmi", "client", "sponsor"
    words: str = ""
    marker: str = ""

    @property
    def atomic(self) -> bool:
        return self.kind != "text"


def display_map(raw: str, sponsor, report_url: str | None = None) -> tuple[str, list[Span]]:
    """(display text, spans) of one raw paragraph: the text the page shows (the concatenation of expand_segments' segment texts) and,
    for every stretch of it, where it sits in the raw text. A link's words form one atomic span tied to its whole raw marker."""
    if raw.strip() == prompts.SPONSOR_TOKEN:
        text = "".join(t for t, _ in sponsor_segments(sponsor))
        return text, [Span(0, len(text), 0, len(raw), "sponsor", text, raw)] if text else []
    spans: list[Span] = []
    out: list[str] = []
    d = 0

    def add_text(r0: int, r1: int) -> None:
        nonlocal d
        if r1 > r0:
            spans.append(Span(d, d + r1 - r0, r0, r1, "text"))
            out.append(raw[r0:r1])
            d += r1 - r0

    def add_atom(kind: str, words: str, r0: int, r1: int) -> None:
        nonlocal d
        if words:
            spans.append(Span(d, d + len(words), r0, r1, kind, words, raw[r0:r1]))
            out.append(words)
            d += len(words)

    pos = 0
    for m in _MARKER_RE.finditer(raw):
        add_text(pos, m.start())
        if m.group(0) == prompts.SPONSOR_TOKEN:
            add_atom("sponsor", "".join(t for t, _ in sponsor_segments(sponsor)), m.start(), m.end())
        elif m.group(1) is not None:
            add_atom("cmi", m.group(1), m.start(), m.end())
        else:
            add_atom("client", m.group(3), m.start(), m.end())
        pos = m.end()
    add_text(pos, len(raw))
    return "".join(out), spans


@dataclass
class RawSelection:
    target: dict
    raw: str                # the whole raw paragraph (or answer, or title)
    display: str
    d_start: int            # the selection in display text, widened over any link it touches
    d_end: int
    r_start: int            # and in raw text
    r_end: int
    atoms: list[Span]       # the links inside the selection

    @property
    def selected_display(self) -> str:
        return self.display[self.d_start:self.d_end]

    @property
    def selected_raw(self) -> str:
        return self.raw[self.r_start:self.r_end]


def _check_target(run, target: dict) -> dict:
    kind = target.get("kind")
    try:
        if kind == "body":
            si, pj = int(target["section"]), int(target["paragraph"])
            if not (0 <= si < len(run.sections) and 0 <= pj < len(run.sections[si]["paragraphs"])):
                raise IndexError
            return {"kind": "body", "section": si, "paragraph": pj}
        if kind == "faq":
            k = int(target["index"])
            if not 0 <= k < len(run.faq):
                raise IndexError
            return {"kind": "faq", "index": k}
    except (KeyError, TypeError, ValueError, IndexError):
        raise CanvasError(400, "That passage does not exist in the draft.")
    if kind == "title":
        return {"kind": "title"}
    raise CanvasError(400, "Unknown target.")


def read_raw(run, target: dict) -> str:
    if target["kind"] == "body":
        return run.sections[target["section"]]["paragraphs"][target["paragraph"]]
    if target["kind"] == "faq":
        return str(run.faq[target["index"]].get("answer") or "")
    return run.title


def _report_url(run) -> str | None:
    return run.report.url if run.report is not None else None


def locate(run, target: dict, start: int, end: int, quote: str) -> RawSelection:
    """The selection `start:end` (display offsets inside the target's display text) as raw offsets. A stale page (the quote is not what
    the draft shows there) is refused with 409. A selection that touches any part of a link widens to the whole link."""
    target = _check_target(run, target)
    raw = read_raw(run, target)
    display, spans = display_map(raw, run.sponsor, _report_url(run))
    if not (isinstance(start, int) and isinstance(end, int)) or start < 0 or end <= start:
        raise CanvasError(400, "Select some words first.")
    if display[start:end] != quote or end > len(display):
        raise CanvasError(409, STALE)
    # the selection covers whole words: a word cut in two ("Or|ganizations") came back with its other half doubled
    while start > 0 and display[start - 1].isalnum() and display[start].isalnum():
        start -= 1
    while end < len(display) and display[end - 1].isalnum() and display[end].isalnum():
        end += 1
    touched = [s for s in spans if s.d0 < end and s.d1 > start]
    atoms = [s for s in touched if s.atomic]
    d_start = min([start] + [s.d0 for s in atoms])
    d_end = max([end] + [s.d1 for s in atoms])
    r_start = next(s.r0 if s.atomic else s.r0 + (d_start - s.d0) for s in spans if s.d0 <= d_start < s.d1)
    r_end = next(s.r1 if s.atomic else s.r0 + (d_end - s.d0) for s in spans if s.d0 < d_end <= s.d1)
    return RawSelection(target, raw, display, d_start, d_end, r_start, r_end, atoms)


def splice(sel: RawSelection, replacement: str) -> str:
    """The raw paragraph with the selected span replaced. The replacement keeps the whitespace the selection had at its edges, so
    two words never run together and a space is never doubled."""
    chosen = sel.selected_raw
    lead = chosen[:len(chosen) - len(chosen.lstrip())]
    trail = chosen[len(chosen.rstrip()):]
    core = replacement.strip()
    return sel.raw[:sel.r_start] + lead + core + (trail if core else "") + sel.raw[sel.r_end:]


# ------------------------------------------------------------------------------------------------ marker checks

def markers(text: str) -> list[str]:
    return [m.group(0) for m in _MARKER_RE.finditer(text)]


def marker_problem(old: str, new: str, allow_cmi_drop: bool = False) -> str | None:
    """Why `new` cannot stand in for `old`, or None. Every link marker of `old` comes back exactly once and unchanged (a CMI report link
    may be dropped only when the editor asked for that), and `new` holds no marker of its own and no broken half of one."""
    before, after = markers(old), markers(new)
    for mk in dict.fromkeys(before):
        n_old, n_new = before.count(mk), after.count(mk)
        if n_new == n_old or (allow_cmi_drop and mk.startswith("[[CMI_LINK:") and n_new < n_old):
            continue
        what = "the report link" if mk.startswith("[[CMI_LINK:") else "a link" if mk != prompts.SPONSOR_TOKEN else "the retained paragraph"
        return (f"{what} {mk} must come back exactly {n_old} time(s), unchanged; it came back {n_new} time(s)"
                if n_new != n_old else f"{mk} must come back unchanged")
    extra = [mk for mk in dict.fromkeys(after) if mk not in before]
    if extra:
        return f"it holds a link marker that was not in the passage: {extra[0]}"
    if "[[" in _MARKER_RE.sub("", new) or "]]" in _MARKER_RE.sub("", new):
        return "it holds a broken link marker (double square brackets)"
    return None


def check_suggestion(old: str, new: str, instruction: str) -> str | None:
    """The marker problem of a suggestion, or None: what the one retry states to the model, and the reason for a 422 when it stays."""
    return marker_problem(old, new, allow_cmi_drop=bool(_REMOVE_LINK_RE.search(instruction or "")))


# ------------------------------------------------------------------------------------------------ the prompt

def _section_of(run, target: dict) -> tuple[dict | None, int]:
    if target["kind"] == "body":
        return run.sections[target["section"]], target["section"]
    return None, -1


def _plan_section(run, s: dict | None) -> dict | None:
    if s is None or not s.get("sop") or not run.plan:
        return None
    return next((ps for ps in run.plan.get("sections", []) if ps["sop"] == s["sop"][0]), None)


def _facts_block(run, s: dict | None) -> str:
    inp = run.plan_inputs
    ps = _plan_section(run, s)
    if inp is None or ps is None:
        return ("THIS PART'S FACTS\nThis part (the opening, the transition or a FAQ answer) is not built from a fact list of its own: "
                "use the facts listed below for anything beyond the editor's instruction.")
    ps = {**ps, "heading": s["heading"]}
    try:
        block, extra = writer._refill_block(ps, inp, writer._refill_item_ids(ps, inp))
    except (KeyError, IndexError, TypeError):
        return "THIS SECTION'S FACTS\n(none listed)"
    return f"THIS SECTION'S FACTS AND SHAPE (its facts, evidence, keywords and word limit)\n{block}{extra}"


def _other_facts(run, shown: str) -> str:
    inp = run.plan_inputs
    if inp is None:
        return "- (none)"
    rows = []
    for fid, it in inp.items.items():
        if f"[{fid}]" in shown:
            continue
        text = " ".join(str(it.get("text", "")).split())[:300]
        rows.append(f"[{fid}] {text} ({it.get('source') or 'no source named'})")
    return "\n".join(rows) or "- (none)"


def _whole_article(run, si: int) -> str:
    """Every section as the reader sees it (links as their words), so the model can tell what the article already states and where;
    the section being edited is named only, since it is printed in full below with the marks."""
    out = []
    for i, s in enumerate(run.sections):
        head = s.get("heading") or ("(the opening)" if not s.get("sop") else "(the transition)")
        if i == si:
            out.append(f"## {head}\n(the part being edited, shown in full below)")
            continue
        report_url = _report_url(run)
        paras = [display_map(p, run.sponsor, report_url)[0] for p in s["paragraphs"]]
        out.append(f"## {head}\n" + "\n".join(paras))
    if run.faq:
        out.append("## FAQ\n" + "\n".join(f'Q: {f.get("question", "")} A: {f.get("answer", "")}' for f in run.faq))
    return "\n\n".join(out)


def _raw_with_selection(sel: RawSelection) -> str:
    return sel.raw[:sel.r_start] + SEL_OPEN + sel.selected_raw + SEL_CLOSE + sel.raw[sel.r_end:]


def build_prompt(run, sel: RawSelection, instruction: str, scope: str, retry_replacement: str = "", problem: str = "") -> str:
    """The user prompt of the one redraft call (see prompts.CANVAS_USER): the article's outline, the part being edited with the selection
    marked, the target section's own facts block, every other fact compactly, the guest article's points, the client rules when the
    opening is the target, the editor's instruction and the shared rules."""
    inp = run.plan_inputs
    target = sel.target
    s, si = _section_of(run, target)
    marked = _raw_with_selection(sel)
    neighbours = ""
    if target["kind"] == "body":
        paras = list(s["paragraphs"])
        paras[target["paragraph"]] = marked
        section_text = "\n\n".join(paras)
        before = next((x["paragraphs"][-1] for x in reversed(run.sections[:si]) if x["paragraphs"]), "")
        after = next((x["paragraphs"][0] for x in run.sections[si + 1:] if x["paragraphs"]), "")
        label = f'the section "{s["heading"]}"' if s.get("heading") else ("the transition" if s.get("sop") == ["transition"] else "the opening")
        neighbours = ("THE PARAGRAPHS RIGHT BEFORE AND AFTER THIS PART (context only; not yours to change)\n"
                      f"Before: {before or '(none)'}\nAfter: {after or '(none)'}")
    else:
        k = target["index"]
        section_text = f'Question: {run.faq[k].get("question", "")}\nAnswer: {marked}'
        label = "a FAQ answer (a direct, standalone answer of about 40 to 60 words)"
    selection_note = f"The words to change are marked {SEL_OPEN}...{SEL_CLOSE} in the text below (the marks are not part of the article)."
    headings = "; ".join(f'"{x["heading"]}"' for x in run.sections if x.get("heading")) or "(none)"
    shown = _facts_block(run, s)
    opening = target["kind"] == "body" and s is not None and not s.get("sop")
    client = ""
    if opening and inp is not None and getattr(inp, "clients", None) and config.CLIENT_REWRITE:
        client = "\nCLIENT LINK RULES (the opening restates a client's paragraph)\n" + clients_mod.client_block(list(inp.clients)) + "\n"
    retry = ""
    if retry_replacement:
        retry += f'\nThe editor rejected this earlier answer, so do not give it again, give a different one: "{retry_replacement}"\n'
    if problem:
        retry += f"\nYour previous answer was refused because {problem}. Answer again and fix exactly that.\n"
    rules = prompt_rules.render(
        groups=("FACTS AND SOURCES", "ANALYSIS", "LANGUAGE"),
        only=("credit", "figures", "positive", "opinion", "voice", "grammar", "continuity", "active", "filler", "sentences"))
    return prompts.CANVAS_USER.format(
        title=run.title, market_name=inp.market_name if inp is not None else run.report.name, today=date.today().isoformat(),
        headings=headings, article=_whole_article(run, si if target["kind"] == "body" else -1), section_label=label, selection_note=selection_note, section_text=section_text, neighbours=neighbours,
        block=shown, other_facts=_other_facts(run, shown),
        points="\n".join(f'[{p["id"]}] {p["text"]}' for p in (inp.points if inp is not None else [])) or "- (none)",
        client=client, instruction=instruction.strip(),
        scope_rule={"selection": prompts.CANVAS_RULE_SELECTION, "paragraph": prompts.CANVAS_RULE_PARAGRAPH,
                    "section": prompts.CANVAS_RULE_SECTION}[scope],
        retry=retry, rules=rules)


# ------------------------------------------------------------------------------------------------ guardrails on the changed paragraphs

_SPLIT = writer._SENTENCE_SPLIT_RE


def _sentences(text: str) -> list[str]:
    return [" ".join(x.split()) for line in text.split("\n") for x in _SPLIT.split(line) if x.strip()]


def _norm(text: str) -> str:
    return " ".join(text.split())


def _cut(sentence: str) -> str:
    sentence = sentence.strip()
    for tok in (prompts.SPONSOR_TOKEN,):
        sentence = sentence.replace(tok, "the retained paragraph")
    sentence = clients_mod.plain(re.sub(prompts.CMI_LINK_PATTERN, r"\1", sentence))
    return sentence if len(sentence) <= 90 else sentence[:87].rstrip() + "..."


def guard(run, si: int, j0: int, j1: int, new_paras: list[str], extra_numbers: set[str], logger: logging.Logger,
          faq_index: int | None = None) -> tuple[list[str], list[str]]:
    """(the new paragraphs after the writer's strips, what the strips removed as sentences for the note). The strips run on a COPY of
    all sections with ONLY the changed paragraphs tracked (each carries a letters-only first line), so the repeat checks see the rest
    of the article but nothing else is rewritten. A FAQ answer is checked as a pseudo-section after the article, beside the other
    answers. Figures allowed: the facts' as usual, plus `extra_numbers` (the editor's own)."""
    inp = run.plan_inputs
    secs = copy.deepcopy(run.sections)
    if faq_index is not None:
        for k, qa in enumerate(run.faq):
            secs.append({"heading": qa.get("question", ""), "sop": ["faq"], "paragraphs": [str(qa.get("answer") or "")]})
        si, j0, j1 = len(run.sections) + faq_index, 0, 0
    marks: list[str | None] = []
    tracked: list[str] = []
    for n, p in enumerate(new_paras):
        if subheads.is_subhead(p):
            marks.append(None)
            tracked.append(p)
        else:
            mark = "§" + _SENTINEL_LETTERS[n % len(_SENTINEL_LETTERS)] * (n // len(_SENTINEL_LETTERS) + 1) + "§"
            marks.append(mark)
            tracked.append(f"{mark}\n{p}")
    secs[si]["paragraphs"][j0:j1 + 1] = tracked

    def current() -> list[str | None]:
        found: dict[str, str] = {}
        for s in secs:
            for p in s["paragraphs"]:
                first, _, rest = p.partition("\n")
                if first in marks:
                    found[first] = rest
        return [None if m is None else found.get(m, "") for m in marks]

    state = [(None if m is None else p) for m, p in zip(marks, new_paras)]
    client_list = list(getattr(inp, "clients", None) or []) if (inp is not None and config.CLIENT_REWRITE) else []
    if client_list:
        secs = clients_mod.normalize_tokens(secs, client_list, logger)
        allowed_text = clients_mod.original_text(client_list)
    else:
        allowed_text = run.sponsor.text
    steps = [
        ("it was process language", lambda: writer.strip_meta_sentences(secs, logger)),
        ("it named an outside source", lambda: writer.strip_outside_sources(secs, inp, logger)),
        ("it carried a figure no listed fact or instruction supports",
         lambda: writer.strip_untraced_figures(secs, inp, allowed_text, logger, extra_allowed=extra_numbers)),
        ("it repeated a figure printed earlier", lambda: writer.strip_repeat_figures(secs, inp, logger)),
        ("it cited evidence already used elsewhere", lambda: writer.strip_repeat_evidence(secs, inp, logger)),
    ]
    notes: list[str] = []
    for reason, run_step in steps:
        before = state
        secs = run_step()
        after = current()
        state = [b if (a is None or b is None or _norm(a) == _norm(b)) else a for a, b in zip(after, before)]
        for b, a in zip(before, state):
            if b is None or a == b:
                continue
            gone = [x for x in _sentences(b) if x not in _sentences(a)]
            if gone:
                notes += [f'Dropped "{_cut(x)}": {reason}.' for x in gone]
            else:
                notes.append("Cut an outside source's name from a sentence.")
    final = [p if m is None else (state[i] or "").strip() for i, (m, p) in enumerate(zip(marks, new_paras))]
    final, copied = _drop_copies(run, si if faq_index is None else -1, j0, j1, faq_index, final)
    notes += [f'Dropped "{_cut(x)}": the article already says it.' for x in copied]
    return [p for p in final if p], notes


_COPY_STOP = set("the a an of and or in on to for with by at as is are was were that this these its it from into than which who their be "
                 "has have had will can".split())
_COPY_SHARE = 0.8     # copied sentences measured 0.97-1.0 of their words in the original; two different sentences of one draft <= 0.67
_COPY_MIN_WORDS = 8


def _content_words(sentence: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if w not in _COPY_STOP and len(w) > 2}


def _drop_copies(run, si: int, j0: int, j1: int, faq_index: int | None, paras: list[str]) -> tuple[list[str], list[str]]:
    """A suggestion's sentence that restates one the article already has outside the changed paragraphs goes (live: "back this up" on a
    sub-segment paragraph copied the next paragraph's four sentences into it). The strips only catch news evidence repeated across
    sections; this catches any sentence, also inside the same section. A sentence carrying a link marker is never dropped, and only
    sentences the suggestion adds are checked (the part's own sentences stay)."""
    # a body edit is held against the body and a FAQ answer against the other answers: the FAQ restates the body's points by design
    elsewhere, own = [], set()
    if faq_index is None:
        for i, s in enumerate(run.sections):
            for j, p in enumerate(s["paragraphs"]):
                if not (i == si and j0 <= j <= j1):
                    elsewhere += [w for w in map(_content_words, _sentences(p)) if w]
                else:
                    own.update(_sentences(p))
    else:
        for k, qa in enumerate(run.faq or []):
            answer = _sentences(str(qa.get("answer") or ""))
            if k == faq_index:
                own.update(answer)
            else:
                elsewhere += [w for w in map(_content_words, answer) if w]
    out, copied = [], []
    for p in paras:
        if subheads.is_subhead(p):
            out.append(p)
            continue
        lines = []
        for line in p.split("\n"):
            keep = []
            for x in _SPLIT.split(line):
                if not x.strip():
                    continue
                w = _content_words(x)
                if (len(w) >= _COPY_MIN_WORDS and "[[" not in x and " ".join(x.split()) not in own
                        and any(len(w & o) / len(w) >= _COPY_SHARE for o in elsewhere)):
                    copied.append(x)
                    continue
                keep.append(x)
            if keep:
                lines.append(" ".join(keep))
        out.append("\n".join(lines))
    return out, copied


# ------------------------------------------------------------------------------------------------ the editor state on the run

def state(run) -> dict:
    c = run.canvas
    c.setdefault("pending", {})
    c.setdefault("history", [])
    c.setdefault("editor_numbers", [])
    return c


def _snapshot(run) -> dict:
    return copy.deepcopy({"title": run.title, "sections": run.sections, "faq": run.faq, "evidence_citations": run.evidence_citations})


def _restore(run, snap: dict) -> None:
    snap = copy.deepcopy(snap)
    run.title, run.sections, run.faq, run.evidence_citations = snap["title"], snap["sections"], snap["faq"], snap["evidence_citations"]


def _remember(run) -> None:
    """Before a change: the agent's draft is kept once (the first edit), and the current draft goes on the undo stack."""
    c = state(run)
    if "original" not in c:
        c["original"] = _snapshot(run)
    c["history"].append(_snapshot(run))


def status(run) -> dict:
    c = state(run)
    return {"can_undo": bool(c["history"]), "edits": len(c["history"])}


def _refresh_citations(run) -> None:
    if run.plan_inputs is not None:
        run.evidence_citations = writer.evidence_citations(run.sections, run.plan_inputs)


def _numbers(text: str) -> set[str]:
    return checks._fact_numbers_with_words(text or "")


def _add_editor_numbers(run, text: str) -> None:
    c = state(run)
    c["editor_numbers"] = sorted(set(c["editor_numbers"]) | _numbers(text))


def _words(text: str) -> int:
    return len(text.split())


def _segments(run, paras: list[str]) -> list[dict]:
    out: list[dict] = []
    for n, p in enumerate(paras):
        if n:
            out.append({"text": "\n\n", "url": None})
        out += [{"text": t, "url": u} for t, u in expand_segments(p, run.sponsor, _report_url(run))]
    return out


def _display(run, paras: list[str]) -> str:
    return "\n\n".join(display_map(p, run.sponsor, _report_url(run))[0] for p in paras)


# ------------------------------------------------------------------------------------------------ suggest

def _ask(run, sel, instruction, scope, retry_text, problem, logger) -> tuple[str, str]:
    prompt = build_prompt(run, sel, instruction, scope, retry_text, problem)
    try:
        raw = llm.chat_json(prompts.CANVAS_SYSTEM, prompt, temperature=_RETRY_TEMPERATURE if retry_text else 0.3,
                            call_name="canvas", logger=logger, max_tokens=2500 if scope == "section" else 900)
    except llm.LLMError as e:
        raise CanvasError(502, f"The writing model could not answer ({e}). Please try again.")
    if not isinstance(raw, dict) or not isinstance(raw.get("replacement"), str):
        raise CanvasError(422, "The writing model returned no usable text. Please try again.")
    text = raw["replacement"].replace(SEL_OPEN, "").replace(SEL_CLOSE, "")
    return text, _clean_note(str(raw.get("note") or ""))


# a fact id the editor never sees: fs.drivers.2, fs.us.3, W45 (alone, in a list or in brackets)
_NOTE_ID_RE = re.compile(r"\s*\((?:\s*(?:fs(?:\.[\w-]+)+|W\d+)\s*[,;]?)+\)|\b(?:fs(?:\.[\w-]+)+|W\d+)\b")


def _clean_note(note: str) -> str:
    """The model's note for the editor without the plan's fact ids (they mean nothing on the page)."""
    note = _NOTE_ID_RE.sub("", note)
    return re.sub(r"\s+([,.;:])", r"", re.sub(r"\s{2,}", " ", note)).strip()


_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'%.,-]*")
_SHORTER_RE = re.compile(r"\b(?:shorten|shorter|concise|tighten|tighter|trim|cut|condense|brief)\b", re.I)
_LONGER_RE = re.compile(r"\b(?:expand|longer|elaborate|lengthen|more detail|in more depth)\b", re.I)


def _echoes_context(replacement: str, sel: RawSelection) -> bool:
    """True when a words-scope answer repeats the paragraph around the selection (the model returned the whole paragraph instead of
    the words between the marks): five or more words just before or just after the selection reappear in it, in order."""
    text = " ".join(_WORD_RE.findall(replacement.lower()))
    before = _WORD_RE.findall(sel.raw[:sel.r_start].lower())[-6:]
    after = _WORD_RE.findall(sel.raw[sel.r_end:].lower())[:6]
    return any(len(side) >= 5 and " ".join(side) in text for side in (before, after))


def _tok(word: str) -> str:
    return word.lower().rstrip(".,")


def _absorb_context(replacement: str, sel: RawSelection) -> RawSelection:
    """The selection widened over the words just outside it that the answer repeats at its own edges (a selection that stops mid-
    sentence comes back as the whole sentence: "…valued at USD 13 Mn" -> "…valued at USD 13 Mn in 2026."), so the splice does not
    repeat them. Three or more words must line up (or every word to the paragraph's start or end); fewer leave the selection as it is."""
    reply = [_tok(w) for w in _WORD_RE.findall(replacement)]
    before = list(_WORD_RE.finditer(sel.raw[:sel.r_start]))
    after = list(_WORD_RE.finditer(sel.raw, sel.r_end))
    k_before = max((k for k in range(1, min(len(reply), len(before)) + 1)
                    if (k >= 3 or k == len(before)) and reply[:k] == [_tok(m.group(0)) for m in before[-k:]]), default=0)
    k_after = max((k for k in range(1, min(len(reply), len(after)) + 1)
                   if (k >= 3 or k == len(after)) and reply[-k:] == [_tok(m.group(0)) for m in after[:k]]), default=0)
    if not (k_before or k_after):
        return sel
    r_start = before[-k_before].start() if k_before else sel.r_start
    r_end = after[k_after - 1].end() if k_after else sel.r_end
    return RawSelection(sel.target, sel.raw, sel.display, sel.d_start, sel.d_end, r_start, r_end, sel.atoms)


def _length_problem(instruction: str, old: str, new: str) -> str:
    """A shorten-style instruction that came back no shorter (or an expand-style one no longer) is refused with the reason."""
    n_old, n_new = len(_WORD_RE.findall(old)), len(_WORD_RE.findall(new))
    if _SHORTER_RE.search(instruction) and not _LONGER_RE.search(instruction) and n_new >= n_old:
        return f"the editor asked for it shorter, but your text has {n_new} words against {n_old}; return fewer words than {n_old}"
    if _LONGER_RE.search(instruction) and not _SHORTER_RE.search(instruction) and n_new <= n_old:
        return f"the editor asked for it longer, but your text has {n_new} words against {n_old}; return more words than {n_old}"
    return ""


def _paragraphs_from(replacement: str, scope: str) -> list[str]:
    if scope == "section":
        return [p.strip() for p in re.split(r"\n\s*\n", replacement.strip()) if p.strip()]
    return [re.sub(r"\n\s*\n+", "\n", replacement.strip())] if replacement.strip() else []


def suggest(run, target: dict, start: int, end: int, quote: str, instruction: str, scope: str, retry_of: str | None,
            logger: logging.Logger) -> dict:
    """One redraft suggestion for the selection, stored on the run (never applied). See the module docstring and CANVAS_PLAN 1.2."""
    if not run.title or not run.sections:
        raise CanvasError(400, "No draft yet. Finish Step 4 first.")
    if scope not in ("selection", "paragraph", "section"):
        raise CanvasError(400, "Unknown scope.")
    if not (instruction or "").strip():
        raise CanvasError(400, "Tell the writer what to change.")
    target = _check_target(run, target)
    if target["kind"] == "title":
        raise CanvasError(400, "The title is edited by hand only.")
    sel = locate(run, target, start, end, quote)
    c = state(run)
    previous = c["pending"].get(retry_of or "")
    retry_text = previous["replacement"] if previous else ""
    if target["kind"] == "faq" and scope == "section":
        scope = "paragraph"

    if target["kind"] == "body":
        si, pj = target["section"], target["paragraph"]
        j0, j1 = (0, len(run.sections[si]["paragraphs"]) - 1) if scope == "section" else (pj, pj)
        old_paras = list(run.sections[si]["paragraphs"][j0:j1 + 1])
        faq_index = None
    else:
        si, j0, j1, faq_index = -1, 0, 0, target["index"]
        old_paras = [sel.raw]
    old_text = sel.selected_raw if scope == "selection" else "\n\n".join(old_paras)

    problem = ""
    for attempt in range(2):
        replacement, model_note = _ask(run, sel, instruction, scope, retry_text, problem, logger)
        cut = sel
        whole = False
        if scope == "selection" and _echoes_context(replacement, sel):
            # the answer repeats words around the marks: when they sit at its edges (the marks stopped mid-sentence and the whole
            # sentence came back) the marks widen over them; an answer about as long as the paragraph stands as the paragraph
            cut = _absorb_context(replacement, sel)
            if _echoes_context(replacement, cut):
                cut = sel
                if len(_WORD_RE.findall(replacement)) >= 0.6 * len(_WORD_RE.findall(sel.raw)):
                    logger.info("[canvas] the words-scope answer is the whole paragraph; it is used as the paragraph")
                    whole = True
                else:
                    problem = "your text repeats words from outside the marks; return only the new text for the words between the marks"
                    logger.info(f"[canvas] suggestion refused ({problem})" + (": retrying once" if attempt == 0 else ""))
                    continue
            elif cut is not sel:
                logger.info("[canvas] the words-scope answer covers words beside the marks; the marks widen over them")
        if whole:
            new_paras = _paragraphs_from(replacement, "paragraph")
        else:
            new_paras = [splice(cut, replacement)] if scope == "selection" else _paragraphs_from(replacement, scope)
        if scope == "selection" and not whole:
            problem = check_suggestion(cut.selected_raw, replacement, instruction) or ""
            problem = problem or _length_problem(instruction, cut.selected_raw, replacement)
        elif scope == "selection":
            problem = (check_suggestion(sel.raw, "\n\n".join(new_paras), instruction)
                       or _length_problem(instruction, sel.raw, "\n\n".join(new_paras)) if new_paras else "no text came back")
        elif not new_paras:
            problem = "no text came back"
        else:
            problem = check_suggestion(old_text, "\n\n".join(new_paras), instruction) or ""
            problem = problem or _length_problem(instruction, old_text, "\n\n".join(new_paras))
            sub_old = [p for p in old_paras if subheads.is_subhead(p)]
            if not problem and scope == "section" and sub_old != [p for p in new_paras if subheads.is_subhead(p)]:
                problem = "the sub-headings must come back exactly as they are, in their places"
        if not problem and scope == "paragraph" and subheads.is_subhead(old_paras[0]) and new_paras and not subheads.is_subhead(new_paras[0]):
            new_paras[0] = subheads.SUBHEAD_PREFIX + new_paras[0].lstrip("# ")
        if not problem:
            break
        logger.info(f"[canvas] suggestion refused ({problem})" + (": retrying once" if attempt == 0 else ""))
    if problem:
        if "link" in problem or "marker" in problem:
            raise CanvasError(422, "The link could not be kept in the suggestion, so none was made. Try again, or edit by hand.")
        if "asked for it" in problem:
            raise CanvasError(422, "The writer could not make it " + ("shorter" if "shorter" in problem else "longer")
                                   + " without losing content. Try a smaller selection or a different instruction.")
        raise CanvasError(422, f"The writer gave no usable suggestion ({problem}). Try again.")

    extra = set(c["editor_numbers"]) | _numbers(instruction)
    new_paras, dropped = guard(run, si, j0, j1, new_paras, extra, logger, faq_index=faq_index)
    if not new_paras:
        raise CanvasError(422, "Every sentence of the suggestion was dropped by the guardrails"
                               + (f" ({dropped[0]})" if dropped else "") + ". Rephrase the instruction or edit by hand.")
    if scope == "selection" and target["kind"] == "faq":
        new_paras = [_norm(new_paras[0])]
    old_check = _norm("\n\n".join(old_paras))
    if _norm("\n\n".join(new_paras)) == old_check:
        if dropped:
            raise CanvasError(422, "Nothing new is left to suggest: " + " ".join(dropped) + " Try a different instruction.")
        raise CanvasError(422, "The suggestion changed nothing" + (f" ({model_note})" if model_note else "") + ". Try a different instruction.")
    bad = check_suggestion("\n\n".join(old_paras), "\n\n".join(new_paras), instruction)
    if bad:
        raise CanvasError(422, "The link could not be kept in the suggestion, so none was made. Try again, or edit by hand.")

    sid = secrets.token_hex(6)
    c["pending"][sid] = {"id": sid, "target": target, "si": si, "j0": j0, "j1": j1, "old": old_paras, "new": new_paras,
                         "replacement": replacement.strip(), "instruction": instruction.strip()}
    while len(c["pending"]) > _PENDING_MAX:
        c["pending"].pop(next(iter(c["pending"])))
    note = " ".join(x for x in [model_note] + dropped if x)
    words_before, words_after = _words(_display(run, old_paras)), _words(_display(run, new_paras))
    return {"suggestion_id": sid, "target": target, "before": _segments(run, old_paras), "after": _segments(run, new_paras),
            "note": note, "words_before": words_before, "words_after": words_after}


# ------------------------------------------------------------------------------------------------ apply, manual edit, undo, revert

def apply_suggestion(run, suggestion_id: str) -> None:
    with _LOCK:
        c = state(run)
        sug = c["pending"].get(suggestion_id)
        if sug is None:
            raise CanvasError(409, "The draft changed since this suggestion was made. Select the words again and ask again.")
        t = sug["target"]
        current = ([read_raw(run, t)] if t["kind"] == "faq"
                   else list(run.sections[sug["si"]]["paragraphs"][sug["j0"]:sug["j1"] + 1])) if _in_range(run, sug) else None
        if current != sug["old"]:
            raise CanvasError(409, STALE)
        _remember(run)
        if t["kind"] == "faq":
            run.faq[t["index"]]["answer"] = sug["new"][0]
        else:
            run.sections[sug["si"]]["paragraphs"][sug["j0"]:sug["j1"] + 1] = list(sug["new"])
        _add_editor_numbers(run, sug["instruction"])
        c["pending"].clear()
        _refresh_citations(run)


def _in_range(run, sug: dict) -> bool:
    t = sug["target"]
    if t["kind"] == "faq":
        return t["index"] < len(run.faq)
    return sug["si"] < len(run.sections) and sug["j1"] < len(run.sections[sug["si"]]["paragraphs"])


def source(run, target: dict) -> dict:
    """The target's display text with each link replaced by ⟦L0⟧, ⟦L1⟧... and the links ({"id", "words", "url"}), for the edit box."""
    target = _check_target(run, target)
    raw = read_raw(run, target)
    display, spans = display_map(raw, run.sponsor, _report_url(run))
    parts, links = [], []
    client_links = getattr(run.sponsor, "client_links", None) or {}
    for s in spans:
        if not s.atomic:
            parts.append(display[s.d0:s.d1])
            continue
        lid = f"L{len(links)}"
        url = (_report_url(run) if s.kind == "cmi" else client_links[re.search(r"L\d+", s.marker).group(0)].url
               if s.kind == "client" and re.search(r"L\d+", s.marker).group(0) in client_links else "")
        links.append({"id": lid, "words": s.words, "url": url or ""})
        parts.append(f"⟦{lid}⟧")
    return {"text": "".join(parts), "links": links}


def apply_manual(run, target: dict, new_text: str) -> None:
    """A direct edit: `new_text` carries the links as the ⟦Ln⟧ placeholders /canvas/source gave. Every placeholder must appear exactly
    once, except a CMI report link, which may be dropped. No LLM; the editor's own numbers become allowed figures."""
    target = _check_target(run, target)
    if not isinstance(new_text, str) or not new_text.strip():
        raise CanvasError(400, "The text cannot be empty.")
    with _LOCK:
        raw = read_raw(run, target)
        display, spans = display_map(raw, run.sponsor, _report_url(run))
        atoms = [s for s in spans if s.atomic]
        found = _PLACEHOLDER_RE.findall(new_text)
        for n in set(found):
            if int(n) >= len(atoms):
                raise CanvasError(400, "The text holds a link that is not in this passage.")
        for n, s in enumerate(atoms):
            count = found.count(str(n))
            if count > 1 or (count == 0 and s.kind != "cmi"):
                raise CanvasError(400, f"The link \"{s.words[:40]}\" must stay in the text exactly once.")
        if target["kind"] == "title" and found:
            raise CanvasError(400, "The title holds no links.")
        pieces = _PLACEHOLDER_RE.split(new_text)       # text, id, text, id, ...
        if any("[[" in pieces[i] or "]]" in pieces[i] for i in range(0, len(pieces), 2)):
            raise CanvasError(400, "Link markers cannot be typed. Keep the locked link chips instead.")
        out = []
        for i, piece in enumerate(pieces):
            out.append(atoms[int(piece)].marker if i % 2 else piece)
        new_raw = "".join(out)
        if target["kind"] in ("faq", "title"):
            new_raw = _norm(new_raw)
        if new_raw == raw:
            raise CanvasError(400, "Nothing changed.")
        _remember(run)
        if target["kind"] == "body":
            run.sections[target["section"]]["paragraphs"][target["paragraph"]] = new_raw
        elif target["kind"] == "faq":
            run.faq[target["index"]]["answer"] = new_raw
        else:
            run.title = new_raw
        _add_editor_numbers(run, new_text)
        state(run)["pending"].clear()
        _refresh_citations(run)


def undo(run) -> None:
    with _LOCK:
        c = state(run)
        if not c["history"]:
            raise CanvasError(400, "Nothing to undo.")
        _restore(run, c["history"].pop())
        c["pending"].clear()


def revert(run) -> None:
    """Back to the agent's draft (kept at the first edit). Clears the undo stack, so the editor's work is only kept by saving the docx first."""
    with _LOCK:
        c = state(run)
        if "original" not in c:
            raise CanvasError(400, "The draft has not been edited.")
        _restore(run, c["original"])
        c["history"].clear()
        c["pending"].clear()
