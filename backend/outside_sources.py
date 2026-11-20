"""Outside sources are never named: the one source an article may name is CMI.

No publication, news site, research firm, analyst house, data provider or consultancy is named as the source of anything ("per
MarTech Cube", "Phys.org reported", "according to Grand View Research", "a Statista survey"). A company that is the ACTOR of a
development may still be named ("Netcore Unbxd launched an AI agent in May 2026", "Adobe forecast that its own traffic..."): the
company is acting, not being cited as a research source. The writer prompt is the primary control; this module is the code backstop
and it works on PATTERNS, never on a list of firm names (there are hundreds of rival firms and the list would never be complete):

  - attribution frames: "according to X", "per X", "as reported by X", "X reported / said / found / estimates that", "data from X",
    "a report / study / survey by X", "X's report / data", "(X, 2026)", "(source: X)", where X is a capitalised name or a host;
  - the study-noun family, where a journal, magazine, newspaper or collection carries a study: "a study in Nature compared ...",
    "a paper published in the journal Science found ...", "Nature published a study ...", "a Nature Portfolio collection noted
    that ...", "Nature's journal carried ..." (a capitalised PLACE after "in" is a location, not a venue: "a study in China");
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
# a frame's name may follow an article: "according to the U.S. Census Bureau"
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
# Names of public bodies and institutions ("Australian Council for Educational Research", "Office for Students"): never a research firm.
# The capitalised words (joined by for / of / and / the) that run up to a name: the rest of an institution's name.
_FULL_NAME_BEFORE_RE = re.compile(r"(?:(?:[A-Z][\w'’.&-]*|for|of|and|the|on)\s+)*$")
_INSTITUTION_RE = re.compile(r"\b(?:council|university|college|school|academy|association|society|federation|foundation|agency|"
                             r"department|ministry|office|commission|board|bureau|authority|administration|institute of)\b", re.I)
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
    if _INSTITUTION_RE.search(n):
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
    mode: str           # lead | trail | mid | paren | byfrom | pubin | namein | leadpub | namebefore | nonsep | source | firm
    match: re.Match | None
    start: int = 0
    end: int = 0
    repl: str = ""
    tier: int = 1
    text: str = ""      # the matched words (the verb of a subject-verb citation)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in sentsplit.split(text or "") if s.strip()]


_compile = re.compile

# ------------------------------------------------------------------------------------------------ the study-noun family
# A journal, magazine, newspaper or collection is named when it carries a study: "a study in Nature", "a paper published in the
# journal Science", "Nature published a study", "a Nature Portfolio collection", "Nature's journal carried". The noun the venue
# carries stays ("a study in Nature compared X" -> "a study compared X"; "a Nature Portfolio collection noted that Y" -> "a
# collection noted that Y"); only the venue's name goes.
_STUDY_NOUNS = (r"(?:stud(?:y|ies)|papers?|articles?|reviews?|collections?|surveys?|reports?|analysis|analyses|whitepapers?|"
                r"preprints?|editorials?|commentar(?:y|ies)|letters?|research)")
_VENUE_WORDS = r"(?:journal|magazine|newspaper|publication|proceedings|website)"
_VENUE = rf"(?:(?:{_VENUE_WORDS})\s+(?:of\s+)?|pages\s+of\s+)?"
_LAZY_NAME = rf"{_WORD}(?:\s+(?:{_WORD}|(?:of|and|&|for)\s+{_WORD})){{0,3}}?"
# A capitalised place after "in" is where a study happened, not where it was published ("a study in China found ...").
_PLACES = frozenset("""
united states us usa america north america south america latin america europe asia africa oceania middle east gulf southeast asia
china india japan korea south korea north korea taiwan singapore indonesia vietnam thailand malaysia philippines australia
new zealand germany france united kingdom uk britain england scotland wales ireland netherlands belgium spain portugal italy greece
poland austria switzerland sweden norway denmark finland russia turkey israel saudi arabia uae united arab emirates south africa
nigeria egypt brazil mexico argentina chile colombia peru canada
new york california texas florida illinois london tokyo shanghai beijing mumbai delhi bengaluru
""".split())


def _all_generic(name: str) -> bool:
    """A name made only of generic words ("Global Market", "Business Intelligence"): the noun before it is a title, not a venue."""
    words = [w.lower().strip(".,;:") for w in _trim_name(name).split()]
    return bool(words) and all(w in _GENERIC_PREFIX or w in _NAME_LEAD_STOP for w in words)


# (pattern, tier, mode). tier 1: an explicit attribution frame, any capitalised name that is not an allowed actor is flagged.
# tier 2: subject-verb / possessive forms, flagged for a research-firm shape or when the name is not an allowed actor and the clause
# is not about the speaker itself.
_PATTERNS: list[tuple[re.Pattern, int, str]] = [
    (_compile(rf"^\s*{_LEAD_WORDS}\s+(?:(?i:the)\s+)?(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*[,:]\s+"), 1, "lead"),
    (_compile(rf"\s*,?\s*{_TRAIL_WORDS}\s+(?:(?i:the)\s+)?(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?{_END}"), 1, "trail"),
    (_compile(rf"^\s*(?:[A-Z][A-Za-z.]+['’]s\s+)?(?P<name>{_ORG})\s+{_VERBS}\s+"
              rf"(?P<when>(?:in|on|during|earlier in|late in|early in)\s+(?:[A-Z][a-z]+\s+)?(?:\d{{1,2}},?\s+)?\d{{4}})\s+that\s+"), 2, "leadwhen"),
    (_compile(rf"^\s*(?:[A-Z][A-Za-z.]+['’]s\s+)?(?P<name>{_ORG})\s+{_VERBS}\s+that\s+"), 2, "lead"),
    (_compile(rf"\s*,\s*(?P<name>{_ORG})\s+{_VERBS}(?:\s+(?:in|on|this|last|earlier)\s+[A-Za-z]+(?:\s+\d{{4}})?)?{_END}"), 2, "trail"),
    (_compile(rf",\s*{_TRAIL_WORDS}\s+(?:(?i:the)\s+)?(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*,"), 1, "mid"),
    (_compile(rf",\s*(?P<name>{_ORG})\s+{_VERBS}\s*,"), 2, "mid"),
    (_compile(rf"\s*\(\s*(?P<name>{_ORG}),?\s*(?:19|20)\d\d\s*\)"), 1, "paren"),
    (_compile(rf"\s*\(\s*(?i:source|sources|via)\s*:\s*(?P<name>{_ORG})[^)]*\)"), 1, "paren"),
    (_compile(rf"\s*\(\s*{_TRAIL_WORDS}\s+(?:(?i:the)\s+)?(?P<name>{_ORG}){_POSS}(?:\s+{_NOUNS})?\s*\)"), 1, "paren"),
    (_compile(rf"^\s*(?i:in)\s+(?:the\s+)?(?:journal|magazine|newspaper|publication|proceedings|pages)\s+(?:of\s+)?"
              rf"(?P<name>{_ORG})\s*,\s*"), 1, "lead"),
    (_compile(rf"\b(?i:reports?|stud(?:y|ies)|surveys?|analysis|research|polls?|forecasts?|estimates?|index|ranking|whitepaper|data|figures)"
              rf"\s+(?P<span>(?i:by|from|published by|conducted by|compiled by|released by|cited by|quoted by|reported by|covered by|commissioned by|sponsored by|funded by|carried out by|run by|led by|done by|issued by)\s+(?:(?i:the)\s+)?(?P<name>{_ORG}))"), 1, "byfrom"),
    # "the findings were published in Nature": the venue goes, the clause and its date stay
    (_compile(rf"\b(?i:was|were|is|are|has been|have been|had been)\s+(?:published|printed|carried|reported|featured)\s+"
              rf"(?P<span>in\s+(?:(?:a|an|the)\s+)?{_VENUE}(?P<name>{_ORG}))"), 2, "pubin"),
    # "a study in Nature compared ...", "a paper published in the journal Science found ...": the venue goes, the study noun stays
    (_compile(rf"\b(?P<noun>{_STUDY_NOUNS})(?P<span>\s+(?:that\s+)?(?:was\s+|were\s+)?"
              rf"(?:(?:published|appeared|appearing|reported|printed|carried|featured|detailed)\s+)?"
              rf"in\s+(?:(?:a|an|the)\s+)?{_VENUE}(?P<name>{_ORG}))"), 2, "namein"),
    # "Nature published a study on X", "Nature's journal carried a paper on X": the venue and the act go, the study stays
    (_compile(rf"\b(?P<name>{_ORG})(?:['’]s)?\s+(?:{_VENUE_WORDS}\s+)?(?:(?:has|had|have|recently|also|now|then|today)\s+)*"
              rf"(?i:published|released|carried|printed)\s+(?=(?:[A-Za-z0-9'’\-]+\s+){{0,3}}?{_STUDY_NOUNS}\b)"), 2, "leadpub"),
    # "a Nature Portfolio collection ... noted that ...", "a Deloitte survey found ...": the name goes, the article and noun stay
    (_compile(rf"\b(?P<art>[Aa]n?|[Tt]he)\s+(?P<name>{_LAZY_NAME})\s+(?P<noun>{_STUDY_NOUNS})\b"), 2, "namebefore"),
    # "Kakao Style's Zigzag data showed skin mist sales rose 550%": a measured figure from a company's own data is usable, only its
    # name goes ("Skin mist sales rose 550%"), so the sentence that carries the figure is cut, never removed
    (_compile(rf"^\s*(?P<name>{_ORG})['’]s\s+(?:\w+\s+){{0,2}}?(?i:data|figures|numbers|findings|survey|research|analysis)\s+"
              rf"{_VERBS}\s+(?:that\s+)?"), 2, "lead"),
    (_compile(rf"\b{_ANY_FRAME_WORDS}\s+(?:(?i:the)\s+)?(?P<name>{_ORG})"), 1, "nonsep"),
    (_compile(rf"\bper\s+(?:(?i:the)\s+)?(?P<name>{_ORG}){_POSS}(?=\s*(?:[,.;:!?)]|$))"), 1, "nonsep"),
    (_compile(rf"(?P<name>{_ORG})['’]s\s+(?:\w+\s+){{0,2}}?(?i:report|study|survey|analysis|data|research|estimate|forecast|figures|index|"
              rf"ranking|poll|findings|projection|journal|magazine|newspaper|publication|proceedings|article|paper|review|collection|"
              rf"editorial)s?\b"), 2, "nonsep"),
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
            if tier == 2 and mode in ("nonsep", "subjverb", "lead", "leadwhen", "trail", "mid") and _norm(_trim_name(name)) in _PLACES:
                continue    # "A study in China found ...": a place is where something happened, never the one who reports it
            if mode in ("namein", "pubin", "namebefore") and (_norm(name) in _PLACES or
                                                              (mode == "namebefore" and (_all_generic(name) or
                                                                                         firm_shaped(_trim_name(name))))):
                continue    # "a study in China", "A Global Market report": a place or a title, not a venue
            if mode == "lead":
                new = sentence[m.end():]
                hits.append(_Hit(name, "lead", m, 0, m.end(), (new[:1].upper() + new[1:]) if new else "", tier, m.group(0)))
            elif mode == "leadwhen":
                # the statement keeps its date: "<Org> said in November 2026 that X" -> "In November 2026, X"
                when = m.group("when")
                new = f"{when[:1].upper() + when[1:]}, {sentence[m.end():]}"
                hits.append(_Hit(name, "lead", m, 0, m.end(), new, tier, m.group(0)))
            elif mode in ("trail", "paren"):
                hits.append(_Hit(name, mode, m, m.start(), m.end(), "", tier, m.group(0)))
            elif mode == "mid":
                hits.append(_Hit(name, "mid", m, m.start(), m.end(), ",", tier, m.group(0)))
            elif mode in ("byfrom", "pubin", "namein"):
                hits.append(_Hit(name, mode, m, m.start("span"), m.end("span"), "", tier, m.group(0)))
            elif mode == "leadpub":
                # "<Name> published a study on X" -> "A study on X": the venue and the act go, the study and its object stay
                repl = "" if m.start("name") == 0 else " "
                hits.append(_Hit(name, "leadpub", m, m.start("name"), m.end(), repl, tier, m.group(0)))
            elif mode == "namebefore":
                # "a Nature Portfolio collection ..." -> "a collection ...": the article and the study noun stay, agreement kept
                art, noun = m.group("art"), m.group("noun")
                if art.lower() in ("a", "an"):
                    art = ("an" if noun[:1].lower() in "aeiou" else "a")
                    art = art[:1].upper() + art[1:] if m.group("art")[:1].isupper() else art
                hits.append(_Hit(name, "namebefore", m, m.start("art"), m.start("noun"),
                                 ("" if m.start("art") == 0 else " ") + art + " ", tier, m.group(0)))
            else:
                hits.append(_Hit(name, "nonsep", m, 0, 0, "", tier, m.group(0)))
    for src in srcs:
        if re.search(r"(?<![A-Za-z0-9.])" + re.escape(src) + r"(?![A-Za-z0-9])", sentence, re.IGNORECASE):
            hits.append(_Hit(src, "source", None))
    # a research-firm shape that no attribution frame carries (the name simply written into the sentence)
    seen = {_norm(h.name) for h in hits}
    for m in _FIRM_RE.finditer(sentence):
        name = _trim_name(m.group("name"))
        # the full name the match ends ("Australian Council for Educational Research" for "Educational Research"): an institution's
        # name is never a research firm's
        if _INSTITUTION_RE.search(_FULL_NAME_BEFORE_RE.search(sentence[:m.start("name")]).group(0)):
            continue
        if _norm(name) not in seen and firm_shaped(name) and not allowed.covers(name):
            hits.append(_Hit(name, "firm", m))
            seen.add(_norm(name))
    for m in _COMPOUND_RE.finditer(sentence):
        if firm_shaped(m.group(0)) and _norm(m.group(0)) not in seen and not allowed.covers(m.group(0)):
            hits.append(_Hit(m.group(0), "firm", m))
            seen.add(_norm(m.group(0)))
    return hits


def _scan_named(sentence: str, allowed: Allowed, srcs: tuple | list, regulatory_ok: bool) -> list[_Hit]:
    """_scan, or with `regulatory_ok` (the trends and sub-segment sections, which may credit a supporting figure to the public
    regulatory or statistics body that published it, the content team's rule of 2026-11-20) every hit except such a body."""
    hits = _scan(sentence, allowed, srcs)
    if regulatory_ok:
        hits = [h for h in hits if not regulatory_body(h.name)]
    return hits


def attributing_names(sentence: str, allowed: Allowed = EMPTY, srcs: tuple | list = (), regulatory_ok: bool = False) -> list[str]:
    """The outside-source names a sentence carries, for the check (in order, without repeats)."""
    out: list[str] = []
    for h in _scan_named(sentence, allowed, srcs, regulatory_ok):
        if h.name not in out:
            out.append(h.name)
    return out


def clean_sentence(sentence: str, allowed: Allowed = EMPTY, srcs: tuple | list = (), keep: bool = False,
                   regulatory_ok: bool = False) -> tuple[str, bool, list[str]]:
    """(text, dropped, names): the sentence without its separable attribution clauses (a leading "According to X, ", a trailing
    ", per X", a mid-sentence ", X reported,", "(X, 2026)", "a survey by X"), and `dropped` True when a citation remains that cannot be cut
    out cleanly or what is left is no sentence any more. `keep` (a sentence that carries a link) never reports `dropped`."""
    text, names = sentence.strip(), []
    for _ in range(6):
        sep = next((h for h in _scan_named(text, allowed, srcs, regulatory_ok)
                    if h.mode in ("lead", "trail", "mid", "paren", "byfrom", "pubin", "namein", "leadpub", "namebefore")), None)
        if sep is None:
            break
        names.append(sep.name)
        text = sep.repl if sep.mode == "lead" else text[:sep.start].rstrip() + sep.repl + text[sep.end:]
        if sep.mode in ("trail", "mid") and not re.search(r"[.!?][\"')\]]*$", text.rstrip()):
            text = text.rstrip() + "."
        text = re.sub(r"\s+([,.;])", r"\1", text).strip()
        if sep.start == 0 and text:
            text = text[:1].upper() + text[1:]
    rest = _scan_named(text, allowed, srcs, regulatory_ok)
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
    """Rival research firms a fact credits a statistic to (SELECT-time ban, rule (i)): a firm-shaped name (Grand View Research, Mordor
    Intelligence ...), a statistics-portal or analyst-house brand (rival_brand) in any attribution frame, with or without a figure.
    Empty when the fact is clean. A figure relayed from any OTHER named source is a usable measured statistic and is not flagged:
    a measurement panel (NielsenIQ, Circana, Kantar, Paren: they count actual sales and installs), a company or operator, an outlet,
    an association, a government body. The one remaining ban on such a figure is a market size/forecast/CAGR (pipeline._MARKET_SIZING_RE),
    whoever states it. The writer never names these sources (only a regulatory body, outside_sources.regulatory_body)."""
    out: list[str] = []
    for s in _sentences(text):
        for h in _scan(s, EMPTY, ()):
            if h.mode in ("firm", "source"):
                continue    # a bare firm-shaped product name in news is not a citation; an attribution without a figure is cut at write time
            if (firm_shaped(h.name) or rival_brand(h.name)) and h.name not in out:
                out.append(h.name)
    return out


# Analyst houses that sell market sizes and forecasts as their product (they publish no measurement of their own activity),
# so a figure credited to one is a rival's, like a statistics portal's. Panels that measure real activity are not here.
_ANALYST_HOUSES = frozenset("gartner idc forrester emarketer euromonitor ibisworld frostsullivan omdia canalys counterpoint mintel".split())


def mentions_rival(text: str) -> bool:
    """A free-text credit ("CNBC, citing Statista and Morgan Stanley") that names a rival research firm anywhere in it."""
    chunks = re.split(r",|;|\band\b|\bciting\b|\bvia\b|\bas reported by\b|\bby\b|\bfrom\b|\bper\b|\bwith\b", text or "")
    return any(c.strip() and (firm_shaped(c.strip()) or rival_brand(c.strip())) for c in chunks)


def rival_brand(name: str) -> bool:
    """The name is a rival research firm's or statistics portal's own brand ("Statista", "Technavio", "IMARC Group"): its letters
    are the first label of one of the research-firm hosts (external_reports.RESEARCH_HOSTS), which firm_shaped misses because
    the name carries no Research/Insights suffix."""
    import external_reports     # lazy: this module stays importable without the pipeline
    letters = re.sub(r"[^a-z]", "", (name or "").lower())
    labels = {re.sub(r"[^a-z]", "", h.split(".")[0]) for h in external_reports.RESEARCH_HOSTS}
    return bool(letters) and (letters in _ANALYST_HOUSES or letters.removesuffix("group") in _ANALYST_HOUSES
                              or any(letters == lab or (len(lab) >= 6 and letters.startswith(lab)) for lab in labels))


def official_body(name: str) -> bool:
    """A public body or institution by the shape of its name (a department, agency, ministry, regulator, association, council,
    university, institute ...), never a research firm's or a statistics portal's."""
    n = _trim_name(name or "")
    return bool(n) and bool(_INSTITUTION_RE.search(n) or _OFFICIAL_RE.search(n)) and not firm_shaped(n)


def regulatory_body(name: str) -> bool:
    """A public regulatory, governmental or official-statistics body ("U.S. Department of Education", "Federal Communications
    Commission", "Eurostat", "World Health Organization", "FDA"): the only outside source the trends and sub-segment sections
    may name, and only for a supporting figure it published. An association, a university, a company, a news outlet, a research
    firm or a statistics portal is not one."""
    n = _trim_name(name or "")
    if not n or firm_shaped(n) or rival_brand(n) or _COMPANY_SUFFIX_RE.search(n) or _NOT_REGULATORY_RE.search(n):
        return False
    letters = re.sub(r"[^A-Za-z]", "", n)
    return bool(_REGULATORY_RE.search(n)) or letters.upper() in _REGULATORY_ACRONYMS or letters.lower() in _REGULATORY_NAMES


_REGULATORY_RE = re.compile(r"\b(?:department|ministry|agency|commission|bureau|authority|administration|regulator|regulatory|"
                            r"government|parliament|congress|census|statistics|statistical office|office (?:of|for)|federal reserve|"
                            r"central bank|reserve bank|national institutes? of|centers? for disease|united nations|world bank|"
                            r"world health organi[sz]ation|international (?:monetary fund|energy agency|labou?r organi[sz]ation|"
                            r"telecommunication union)|european (?:union|commission|central bank|parliament))\b", re.I)
_NOT_REGULATORY_RE = re.compile(r"\b(?:association|society|federation|foundation|council|alliance|consortium|university|college|"
                                r"school|academy|news|times|journal|magazine|media|post|herald|tribune)\b", re.I)
_COMPANY_SUFFIX_RE = re.compile(r"\b(?:inc|corp|corporation|ltd|limited|llc|plc|gmbh|co|company|group|holdings|technologies|solutions)\b\.?",
                                re.I)
_REGULATORY_ACRONYMS = frozenset("""
FDA EPA FCC SEC FTC CDC NIH NHTSA DOE DOT USDA BLS EIA NCES CMS FAA FERC NRC OSHA HHS USPTO NIST NSF ONS IEA OECD WHO IMF ILO ITU
EMA ECB RBI SEBI TRAI CPCB MHRA CQC OFCOM OFGEM FCA NHS ABS ACMA ASIC CRTC CMA MCA UGC AICTE NCERT CBSE UIDAI NITI
""".split())
_REGULATORY_NAMES = frozenset({"eurostat", "ofcom", "ofgem", "statcan", "statisticscanada", "nitiaayog"})


# Official bodies whose names carry none of _INSTITUTION_RE's words ("Census Bureau" does; "U.S. Energy Information Administration"
# does; "Federal Reserve", "National Institutes of Health", "European Commission" and "Centers for Medicare" need these).
_OFFICIAL_RE = re.compile(r"\b(?:federal|national|government|regulator|regulatory|centers? for|institutes?|organi[sz]ation|"
                          r"union|parliament|congress|senate|ministry|statistics|census|reserve)\b", re.I)
