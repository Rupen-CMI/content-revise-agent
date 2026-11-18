"""Outside sources are never named: the one source an article may name is CMI.

No publication, news site, research firm, analyst house, data provider or consultancy is named as the source of anything ("per
MarTech Cube", "Phys.org reported", "according to Grand View Research", "a Statista survey"). A company that is the ACTOR of a
development may still be named ("Netcore Unbxd launched an AI agent in May 2026", "Adobe forecast that its own traffic..."): the
company is acting, not being cited as a research source. The writer prompt is the primary control; this module is the code backstop
and it works on PATTERNS, never on a list of firm names (there are hundreds of rival firms and the list would never be complete):

  - attribution frames: "according to X", "per X", "as reported by X", "X reported / said / found / estimates that", "data from X",
    "a report / study / survey by X", "X's report / data", "(X, 2026)", "(source: X)", where X is a capitalised name or a host;
  - the shape of a research firm's name: a capitalised name ending in Research, Insights, Intelligence, Reports, Consulting,
    Strategies or Analytics Group (Grand View Research, Mordor Intelligence, Fortune Business Insights), or a host/compound whose
    letters hold research / insights / intelligence / marketreport / marketsandmarkets.

A name is never flagged when it is CMI / Coherent Market Insights, or when it is an ACTOR the fact sheet itself carries (a key player,
the company an evidence item is about; see allowed_names): "Google Analytics" as a listed player, "Sprinklr Insights" launched in
May. A subject-verb attribution whose clause is about the speaker itself ("Amazon said it will invest ...") is the speaker acting,
not a cited statistic. Pure; no model call, nothing imported from the pipeline."""
import re
from dataclasses import dataclass, field

import sentsplit

_CMI_RE = re.compile(r"\b(?:CMI|Coherent Market Insights)\b")

# ------------------------------------------------------------------------------------------------ names
_WORD = r"[A-Z][A-Za-z0-9&\-]*"
_NAME = rf"{_WORD}(?:\s+(?:{_WORD}|(?:of|and|&|for)\s+{_WORD})){{0,4}}"
_HOST = r"[A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.(?:com|org|net|io|co|ai|us|info|news|gov|edu)(?:\.[a-z]{2})?"
_ORG = rf"(?:{_HOST}|{_NAME})"
_POSS = r"(?:['’]s)?"
_NOUNS = r"(?:data|figures|estimates|numbers|research|reports?|surveys?|stud(?:y|ies)|analysis|findings|forecasts?)"
_VERBS = (r"(?:reported|reports|said|says|found|finds|estimated|estimates|projected|projects|forecast|forecasts|forecasted|predicted|"
          r"predicts|noted|notes|stated|states|calculated|calculates|valued|values|expects|expected|revealed|reveals|showed|shows|"
          r"surveyed|published|concluded|concludes|analy[sz]ed|ranked|ranks|put|puts)")
_END = r"""(?=\s*[.!?]["')\]]*\s*$)"""
_LEAD_WORDS = (r"(?i:according to|as per|as reported by|as reported in|as cited by|as estimated by|as forecast by|as projected by|"
               r"as published by|per|citing|data from|figures from|estimates from|research from|"
               r"based on (?:data|figures|estimates|numbers|findings) from)")
_TRAIL_WORDS = (r"(?i:according to|as per|as reported by|as reported in|as cited by|as published by|per|citing|"
                r"based on (?:data|figures|estimates) from)")
_ANY_FRAME_WORDS = r"(?i:according to|as reported by|as reported in|as per|as cited by|as published by|as estimated by)"

# A subject that is only a common noun ("Researchers found that ...", "Analysts said ..."): a capitalised sentence opener, not a source.
_GENERIC_SUBJECTS = frozenset("""
researchers analysts experts officials studies surveys reports report data respondents consumers customers buyers users executives
leaders companies firms vendors providers retailers marketers businesses organizations organisations investors industry industries
market markets demand sales spending growth adoption recent new other some many most several both all those these they we it he she
there here this that one two three others developers engineers scientists doctors regulators governments authorities lawmakers
manufacturers suppliers operators partners teams people workers insiders observers commentators economists sources evidence
statistics figures estimates forecasts projections numbers results findings research officials government agencies banks
""".split())
# Capitalised words after "per" / "according to" that are not a named source ("per Capita", "according to Section 4", a month).
_NOT_A_SOURCE = frozenset("""
capita cent unit share level section article title phase class tier type stage rule act chapter fiscal january february march april
may june july august september october november december monday tuesday wednesday thursday friday saturday sunday q1 q2 q3 q4 h1 h2
fy the a an this that these those its their our his her
""".split())
_NAME_LEAD_STOP = frozenset("""
the a an in on at by for with its this that these those it they their as of and to from after new meanwhile however also further
overall still then today recently according per
""".split())

_REFLEXIVE_RE = re.compile(
    r"""^(?:\s+(?:in|on|at|during|via|to|through)\s+[^,.;]{0,40}?)?\s*,?\s*(?:that\s+)?(?:it|its|it's|the\s+company|the\s+firm|the\s+group|we|our|their|the\s+brand)\b""",
    re.IGNORECASE)

_CORP_SUFFIX = frozenset("inc corp corporation ltd llc plc co company group holdings limited".split())


def _norm(name: str) -> str:
    """The lowercase comparison form of a name: possessive, a leading 'The' and corporate suffixes dropped."""
    n = re.sub(r"['’]s\b", "", name or "").strip().strip(".,;:")
    words = [w for w in re.split(r"\s+", n.lower()) if w]
    while words and words[0] == "the":
        words.pop(0)
    while len(words) > 1 and words[-1].strip(".") in _CORP_SUFFIX:
        words.pop()
    return " ".join(words)


def _trim_name(name: str) -> str:
    """The captured name without leading connective words (a sentence's first word, "The", "Meanwhile") and a possessive."""
    words = re.sub(r"['’]s$", "", name.strip()).split()
    while words and words[0].lower() in _NAME_LEAD_STOP:
        words.pop(0)
    return " ".join(words)


# ------------------------------------------------------------------------------------------------ research-firm shape
_FIRM_SUFFIXES = frozenset({"research", "insights", "intelligence", "reports", "consulting", "strategies"})
# A word before the suffix that makes a phrase generic, not a firm ("Artificial Intelligence", "Customer Insights", "Business Intelligence").
_GENERIC_PREFIX = frozenset("""
artificial business competitive market markets customer customers consumer consumers marketing sales data security threat buyer
audience behavioral behavioural predictive actionable real time global industry strategic product brand financial operational
emotional human machine ai new recent further more our early latest ongoing additional independent academic clinical other such this
these current key first second third original primary secondary qualitative quantitative user retail healthcare digital the a an many
some most all and of for & social web mobile cloud customer's employee employees talent
""".split())
_FIRM_COMPACT = ("marketsandmarkets", "researchandmarkets")
_FIRM_HOST_KEYS = ("research", "insights", "intelligence", "marketreport", "marketsand", "marketstudy")
_FIRM_RE = re.compile(
    rf"\b(?P<name>(?:{_WORD}\s+){{1,3}}(?:Market\s+Research|Research|Insights|Intelligence|Reports|Consulting|Strategies|Analytics\s+Group)"
    rf"(?:\s+(?:Future|Engine))?)\b")
_COMPOUND_RE = re.compile(r"\b[A-Z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*\b")


def firm_shaped(name: str) -> bool:
    """Does a name have the shape of a research firm's? (Coherent Market Insights never does.)"""
    n = (name or "").strip()
    if not n or _CMI_RE.search(n):
        return False
    letters = re.sub(r"[^a-z]", "", n.lower())
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.[a-z]{2,}(?:\.[a-z]{2})?", n):
        stem = re.sub(r"[^a-z.]", "", n.lower())
        return stem.startswith("market.us") or any(k in re.sub(r"[^a-z]", "", stem.rsplit(".", 1)[0] if "." in stem else stem)
                                                   for k in _FIRM_HOST_KEYS)
    if any(k in letters for k in _FIRM_COMPACT):
        return True
    words = n.split()
    if len(words) == 1:
        # one compound token with two or more capitals and a firm keyword ("GrandViewResearch", "MordorIntelligence")
        return bool(_COMPOUND_RE.fullmatch(n)) and any(k in n.lower() for k in ("research", "insights", "intelligence"))
    tail = list(words)
    had_tail = tail[-1].lower() in ("future", "engine")
    if had_tail:
        tail.pop()
    low = [w.lower() for w in tail]
    if low[-2:] == ["market", "research"]:
        # "Verified Market Research", "Market Research Future": the bare phrase "Market Research" is only a topic ("Data Aggregation and
        # Market Research applications"), so a firm needs a distinctive word before it or a Future/Engine tail after it
        prefix = tail[:-2]
        if had_tail and not prefix:
            return True
    elif low[-2:] == ["analytics", "group"]:
        prefix = tail[:-2]
    elif low and low[-1] in _FIRM_SUFFIXES:
        prefix = tail[:-1]
    else:
        return False
    return any(w.lower() not in _GENERIC_PREFIX for w in prefix)


# ------------------------------------------------------------------------------------------------ allowed actors
@dataclass(frozen=True)
class Allowed:
    """Names that may appear: the actors of the fact sheet (key players, the company an evidence item is about) and the market's own name."""
    names: frozenset = field(default_factory=frozenset)
    phrases: tuple = ()

    def covers(self, name: str) -> bool:
        n = _norm(name)
        if not n:
            return False
        return n in self.names or any(n in p for p in self.phrases)


EMPTY = Allowed()


def _names_in(text: str) -> set[str]:
    """Every capitalised name in a text, as whole names and as their single words (a fact says "Netcore Unbxd", the draft may say
    "Netcore"), lowercase and normalised."""
    out: set[str] = set()
    for m in re.finditer(_ORG, text or ""):
        name = _trim_name(m.group(0))
        if not name:
            continue
        out.add(_norm(name))
        # a list joins names with "and" ("Google Analytics, Sprinklr Insights and Brand Watchers"): each piece is a name of its own
        for piece in re.split(r"\s+(?:and|&)\s+", name):
            out.add(_norm(piece))
            out.update(_norm(w) for w in piece.split() if len(w) >= 3 and w.lower() not in _NAME_LEAD_STOP and w.lower() not in _GENERIC_SUBJECTS)
    out.discard("")
    return out


def allowed_names(inp) -> Allowed:
    """The actors the fact sheet carries. A report fact's names (the key players) are all allowed. An evidence item's names are allowed
    except the ones its OWN text attributes a statistic to ("Reuters reported that ...": the publisher is not an actor)."""
    if inp is None:
        return EMPTY
    names: set[str] = set()
    for i in inp.items.values():
        text = f"{i.get('text', '')} {i.get('detail', '') or ''}"
        found = _names_in(text)
        if i.get("kind") == "evidence" or i.get("source") not in ("", "CMI report", None):
            cited = {_norm(h.name) for s in _sentences(text) for h in _scan(s, EMPTY, ())}
            found -= cited
        names |= found
    for p in getattr(inp, "points", None) or []:
        names |= _names_in(p.get("text", ""))
    market = getattr(inp, "market_name", "") or ""
    phrases = (_norm(market),) if market else ()
    return Allowed(frozenset(names), phrases)


def source_names(inp, allowed: Allowed) -> list[str]:
    """The outside sources' own names that are unmistakably a source when they turn up bare in a sentence (a two-word publication, a
    host): a single plain word ("Reuters", "Fortune") is only recognised inside an attribution frame, since it can be an ordinary
    word. A name that is also an allowed actor is left out."""
    out: list[str] = []
    for i in inp.items.values() if inp is not None else ():
        src = re.sub(r",\s*(?:\d{4}-\d{2}|undated)\s*$", "", str(i.get("source") or "")).strip()
        if not src or src in ("CMI report", "web") or _CMI_RE.search(src) or allowed.covers(src):
            continue
        if (" " in src or "." in src) and src not in out:
            out.append(src)
    return out


# ------------------------------------------------------------------------------------------------ scanning
@dataclass
class _Hit:
    name: str
    mode: str           # lead | trail | mid | paren | byfrom | nonsep | source | firm
    match: re.Match | None
    start: int = 0
    end: int = 0
    repl: str = ""
    tier: int = 1
    text: str = ""      # the matched words (the verb of a subject-verb citation)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in sentsplit.split(text or "") if s.strip()]


_compile = re.compile


# (pattern, tier, mode). tier 1: an explicit attribution frame, any capitalised name that is not an allowed actor is flagged.
# tier 2: subject-verb / possessive forms, flagged for a research-firm shape or when the name is not an allowed actor and the clause
# is not about the speaker itself.
_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    (_compile(rf"^\s*{_LEAD_WORDS}\s+(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*[,:]\s+"), 1, "lead"),
    (_compile(rf"\s*,?\s*{_TRAIL_WORDS}\s+(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?{_END}"), 1, "trail"),
    (_compile(rf"^\s*(?P<name>{_ORG})\s+{_VERBS}\s+that\s+"), 2, "lead"),
    (_compile(rf"\s*,\s*(?P<name>{_ORG})\s+{_VERBS}(?:\s+(?:in|on|this|last|earlier)\s+[A-Za-z]+(?:\s+\d{{4}})?)?{_END}"), 2, "trail"),
    (_compile(rf",\s*{_TRAIL_WORDS}\s+(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*,"), 1, "mid"),
    (_compile(rf",\s*(?P<name>{_ORG})\s+{_VERBS}\s*,"), 2, "mid"),
    (_compile(rf"\s*\(\s*(?P<name>{_ORG}),?\s*(?:19|20)\d\d\s*\)"), 1, "paren"),
    (_compile(rf"\s*\(\s*(?i:source|sources|via)\s*:\s*(?P<name>{_ORG})[^)]*\)"), 1, "paren"),
    (_compile(rf"\s*\(\s*{_TRAIL_WORDS}\s+(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*\)"), 1, "paren"),
    (_compile(rf"\b(?i:reports?|stud(?:y|ies)|surveys?|analysis|research|polls?|forecasts?|estimates?|index|ranking|whitepaper|data|figures)"
              rf"\s+(?P<span>(?i:by|from|published by|conducted by|compiled by|released by)\s+(?P<name>{_ORG}))"), 1, "byfrom"),
    (_compile(rf"\b{_ANY_FRAME_WORDS}\s+(?P<name>{_ORG})"), 1, "nonsep"),
    (_compile(rf"\bper\s+(?P<name>{_ORG}){_POSS}(?=\s*(?:[,.;:!?)]|$))"), 1, "nonsep"),
    (_compile(rf"(?P<name>{_ORG})['’]s\s+(?:\w+\s+){{0,2}}?(?i:report|study|survey|analysis|data|research|estimate|forecast|figures|index|"
              rf"ranking|poll|findings|projection)s?\b"), 2, "nonsep"),
    (_compile(rf"(?P<name>{_ORG})\s+(?i:report|study|survey|analysis|estimates?|forecasts?|projections?)\b"), 2, "nonsep_firm"),
    (_compile(rf"(?P<name>{_ORG})\s+(?:(?:also|then|recently|now|has|have|had|previously|separately)\s+)*{_VERBS}\b"), 2, "subjverb"),
]


def _flagged(name: str, tier: int, allowed: Allowed, rest: str) -> bool:
    name = _trim_name(name)
    n = _norm(name)
    if not n or _CMI_RE.search(name) or len(n) < 3:
        return False
    first = n.split()[0].strip(".")
    if first in _NOT_A_SOURCE:
        return False
    if allowed.covers(name):
        return False
    if firm_shaped(name):
        return True
    if " " not in n and n in _GENERIC_SUBJECTS:
        return False
    if tier == 1:
        return True
    return not _REFLEXIVE_RE.match(rest)


def _scan(sentence: str, allowed: Allowed, srcs: tuple | list) -> list[_Hit]:
    """Every flagged outside-source attribution in one sentence: the separable ones carry their removal span."""
    hits: list[_Hit] = []
    for rx, tier, mode in _PATTERNS:
        for m in rx.finditer(sentence):
            name = m.group("name")
            if not _flagged(name, tier, allowed, sentence[m.end():m.end() + 80]):
                continue
            if mode == "nonsep_firm" and not firm_shaped(_trim_name(name)):
                continue    # "<Name> report" is a citation only when the name is a firm's
            if mode == "lead":
                new = sentence[m.end():]
                hits.append(_Hit(name, "lead", m, 0, m.end(), (new[:1].upper() + new[1:]) if new else "", tier, m.group(0)))
            elif mode in ("trail", "paren"):
                hits.append(_Hit(name, mode, m, m.start(), m.end(), "", tier, m.group(0)))
            elif mode == "mid":
                hits.append(_Hit(name, "mid", m, m.start(), m.end(), ",", tier, m.group(0)))
            elif mode == "byfrom":
                hits.append(_Hit(name, "byfrom", m, m.start("span"), m.end("span"), "", tier, m.group(0)))
            else:
                hits.append(_Hit(name, "nonsep", m, 0, 0, "", tier, m.group(0)))
    for src in srcs:
        if re.search(r"(?<![A-Za-z0-9.])" + re.escape(src) + r"(?![A-Za-z0-9])", sentence, re.IGNORECASE):
            hits.append(_Hit(src, "source", None))
    # a research-firm shape that no attribution frame carries (the name simply written into the sentence)
    seen = {_norm(h.name) for h in hits}
    for m in _FIRM_RE.finditer(sentence):
        name = _trim_name(m.group("name"))
        if _norm(name) not in seen and firm_shaped(name) and not allowed.covers(name):
            hits.append(_Hit(name, "firm", m))
            seen.add(_norm(name))
    for m in _COMPOUND_RE.finditer(sentence):
        if firm_shaped(m.group(0)) and _norm(m.group(0)) not in seen and not allowed.covers(m.group(0)):
            hits.append(_Hit(m.group(0), "firm", m))
            seen.add(_norm(m.group(0)))
    return hits


def attributing_names(sentence: str, allowed: Allowed = EMPTY, srcs: tuple | list = ()) -> list[str]:
    """The outside-source names a sentence carries, for the check (in order, without repeats)."""
    out: list[str] = []
    for h in _scan(sentence, allowed, srcs):
        if h.name not in out:
            out.append(h.name)
    return out


def clean_sentence(sentence: str, allowed: Allowed = EMPTY, srcs: tuple | list = (), keep: bool = False) -> tuple[str, bool, list[str]]:
    """(text, dropped, names): the sentence without its separable attribution clauses (a leading "According to X, ", a trailing
    ", per X", a mid-sentence ", X reported,", "(X, 2026)", "a survey by X"), and `dropped` True when a citation remains that cannot be cut
    out cleanly or what is left is no sentence any more. `keep` (a sentence that carries a link) never reports `dropped`."""
    text, names = sentence.strip(), []
    for _ in range(6):
        sep = next((h for h in _scan(text, allowed, srcs) if h.mode in ("lead", "trail", "mid", "paren", "byfrom")), None)
        if sep is None:
            break
        names.append(sep.name)
        text = sep.repl if sep.mode == "lead" else text[:sep.start].rstrip() + sep.repl + text[sep.end:]
        if sep.mode in ("trail", "mid") and not re.search(r"[.!?][\"')\]]*$", text.rstrip()):
            text = text.rstrip() + "."
        text = re.sub(r"\s+([,.;])", r"\1", text).strip()
    rest = _scan(text, allowed, srcs)
    names += [h.name for h in rest]
    dropped = bool(rest) or (bool(names) and len(re.findall(r"[A-Za-z0-9]+", text)) < 4)
    return text, (dropped and not keep), names


def clean_text(text: str, allowed: Allowed = EMPTY, srcs: tuple | list = (), keep=lambda s: False) -> tuple[str, list[str]]:
    """A free text (an FAQ answer, a guest-article point) without outside-source attributions: (cleaned text, what was removed). A
    sentence that cannot be cleaned goes; a sentence `keep` returns True for is cleaned where it can be and never removed."""
    out, gone = [], []
    for s in _sentences(text):
        new, dropped, names = clean_sentence(s, allowed, srcs, keep=keep(s))
        if dropped:
            gone.append(s)
            continue
        out.append(new)
        if names:
            gone.append(f"[{', '.join(names)}]")
    return " ".join(out), gone


# ------------------------------------------------------------------------------------------------ evidence selection
# The verbs of a statistic ("estimated", "forecast", "found"), as against a news item's speech verbs ("said", "reported"): at SELECT a plain
# named speaker is only a relayed statistic when it estimates, forecasts or surveys, so "Reuters reported that Tesla will invest $5 billion"
# keeps its development (the writer then cuts the leading "Reuters reported that"), while "Gartner estimates 40% of CIOs ..." is dropped.
_STAT_VERBS_RE = re.compile(r"\b(?:estimat\w*|project\w*|forecast\w*|predict\w*|surveyed|found|finds|calculat\w*|valued|values|ranked|ranks|"
                            r"concluded|concludes|analy[sz]ed|put|puts|survey|study|poll|index|ranking|report|data|figures|research|analysis)\b", re.I)


def attributes_to_outside(text: str) -> list[str]:
    """Names an evidence fact attributes a statistic to: a research-firm shape in any attribution, or any named source in a sentence that
    carries a figure and an explicit attribution frame or a statistic verb ("per Statista, 62% ...", "Gartner estimates 40% of CIOs
    ..."). A subject-verb clause about the speaker itself ("Adobe said its traffic rose 4%") is not one. Empty when the fact is clean.
    Used where no fact sheet is known yet (SELECT), so no actor is exempt beyond that reflexive clause."""
    out: list[str] = []
    for s in _sentences(text):
        digit = bool(re.search(r"\d", s))
        for h in _scan(s, EMPTY, ()):
            if h.mode in ("firm", "source"):
                continue    # a bare firm-shaped product name in news is not a citation; an attribution without a figure is cut at write time
            # A company announcing its own numbers ("US holiday online sales to grow 6.7%, Adobe Analytics forecasts") is primary
            # data, kept and later written without the name; only a relaying frame ("per Statista, 62% ...") or a research-firm
            # shape marks a statistic borrowed from someone else.
            if not firm_shaped(h.name) and not (digit and h.tier == 1):
                continue
            if h.name not in out:
                out.append(h.name)
    return out
