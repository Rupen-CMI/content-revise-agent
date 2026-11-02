"""One spelling variety for the whole post: the guest article's own (British or American), applied in code to the finished
body and FAQ answers. The writer follows the guest article's variety, but the evidence it quotes and the FAQ step write the
other one, so a draft came out with "organisations" in the body and "organizations" in the FAQ (and "localisation" and
"localization" in one section). A prompt rule did not hold; the conversion is a plain word-list replacement.

Only lower-case words and a capitalised word that opens a sentence are converted; a capitalised word elsewhere is a proper
name ("Center for Disease Control") and stays. Link tokens ([[...]]) and any word that belongs to an assigned keyword or the
market name are left alone, so no keyword check can break. Pure; no model call."""
import re

# stem pairs (British, American); each stem is completed by the endings below
_VERB_STEMS = [
    ("organis", "organiz"), ("recognis", "recogniz"), ("specialis", "specializ"), ("optimis", "optimiz"), ("localis", "localiz"),
    ("personalis", "personaliz"), ("digitis", "digitiz"), ("standardis", "standardiz"), ("customis", "customiz"),
    ("categoris", "categoriz"), ("monetis", "monetiz"), ("modernis", "moderniz"), ("finalis", "finaliz"), ("utilis", "utiliz"),
    ("prioritis", "prioritiz"), ("capitalis", "capitaliz"), ("centralis", "centraliz"), ("harmonis", "harmoniz"),
    ("summaris", "summariz"), ("realis", "realiz"), ("minimis", "minimiz"), ("maximis", "maximiz"), ("mobilis", "mobiliz"),
    ("normalis", "normaliz"), ("authoris", "authoriz"), ("characteris", "characteriz"), ("generalis", "generaliz"),
    ("industrialis", "industrializ"), ("internationalis", "internationaliz"), ("visualis", "visualiz"),
    ("synchronis", "synchroniz"), ("commercialis", "commercializ"), ("rationalis", "rationaliz"), ("stabilis", "stabiliz"),
    ("economis", "economiz"), ("socialis", "socializ"), ("globalis", "globaliz"), ("virtualis", "virtualiz"),
    ("operationalis", "operationaliz"), ("monopolis", "monopoliz"), ("emphasis", None),     # "emphasis" is a noun: never converted
]
_VERB_ENDINGS = ("e", "es", "ed", "ing", "ation", "ations", "er", "ers")
_ANALYSE = [("analyse", "analyze"), ("analyses", "analyzes"), ("analysed", "analyzed"), ("analysing", "analyzing")]
_OUR = [("favour", "favor"), ("colour", "color"), ("behaviour", "behavior"), ("labour", "labor"), ("honour", "honor"),
        ("neighbour", "neighbor"), ("harbour", "harbor"), ("flavour", "flavor"), ("endeavour", "endeavor")]
_OUR_ENDINGS = ("", "s", "ed", "ing", "able", "ite", "ites", "ful", "al", "ally")
_RE = [("centre", "center"), ("fibre", "fiber"), ("theatre", "theater"), ("litre", "liter"), ("metre", "meter"),
       ("calibre", "caliber")]
_RE_ENDINGS = ("", "s", "d")
_WORDS = [
    ("catalogue", "catalog"), ("catalogues", "catalogs"), ("defence", "defense"), ("offence", "offense"), ("judgement", "judgment"),
    ("judgements", "judgments"), ("grey", "gray"), ("whilst", "while"), ("fulfil", "fulfill"), ("fulfils", "fulfills"),
    ("enrol", "enroll"), ("enrols", "enrolls"), ("instalment", "installment"), ("instalments", "installments"),
    ("travelling", "traveling"), ("travelled", "traveled"), ("traveller", "traveler"), ("travellers", "travelers"),
    ("modelling", "modeling"), ("modelled", "modeled"), ("labelled", "labeled"), ("labelling", "labeling"),
    ("cancelled", "canceled"), ("cancelling", "canceling"), ("signalling", "signaling"), ("signalled", "signaled"),
    ("levelled", "leveled"), ("channelled", "channeled"), ("artefact", "artifact"), ("artefacts", "artifacts"),
    ("aluminium", "aluminum"), ("sceptical", "skeptical"), ("ageing", "aging"), ("programme", "program"),
    ("programmes", "programs"), ("licence", "license"), ("licences", "licenses"), ("practise", "practice"),
    ("practised", "practiced"), ("tyre", "tire"), ("tyres", "tires"), ("mould", "mold"), ("plough", "plow"),
]


def _pairs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for b, a in _VERB_STEMS:
        if a is None:
            continue
        out += [(b + e, a + e) for e in _VERB_ENDINGS]
    out += _ANALYSE
    out += [(b + e, a + e) for b, a in _OUR for e in _OUR_ENDINGS]
    out += [(b + e, a + e) for b, a in _RE for e in _RE_ENDINGS]
    out += _WORDS
    return out


_PAIRS = _pairs()
_TO_AMERICAN = {b: a for b, a in _PAIRS}
_TO_BRITISH = {a: b for b, a in _PAIRS if a not in ("program", "programs", "license", "licenses", "practice", "practiced", "while")}
# American -> British is not applied to words British English also uses ("program" for software, "license" as a verb, "practice" as a
# noun, "while"), so only the unambiguous American forms are converted back.
_WORD_RE = re.compile(r"[A-Za-z]+")
_TOKEN_RE = re.compile(r"\[\[.*?\]\]")


def detect(text: str) -> str:
    """'British' or 'American': whichever variety the text uses more; American when it uses neither (the evidence it will be
    mixed with is mostly American)."""
    words = [w.lower() for w in _WORD_RE.findall(text or "")]
    british = sum(1 for w in words if w in _TO_AMERICAN and w not in _TO_BRITISH)
    american = sum(1 for w in words if w in _TO_BRITISH and w not in _TO_AMERICAN)
    return "British" if british > american else "American"


def _convert_words(text: str, table: dict[str, str], skip: frozenset[str]) -> str:
    def swap(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        new = table.get(low)
        if new is None or low in skip:
            return word
        if word.islower():
            return new
        # a capitalised word is a proper name unless it opens the text or follows a sentence end
        before = text[:m.start()].rstrip()
        if word[0].isupper() and word[1:].islower() and (not before or before[-1] in ".!?\n:#-"):
            return new[0].upper() + new[1:]
        return word
    return _WORD_RE.sub(swap, text)


def convert(text: str, variety: str, keep: frozenset[str] = frozenset()) -> str:
    """`text` in the given variety; words in `keep` and [[link tokens]] are untouched."""
    table = _TO_BRITISH if variety == "British" else _TO_AMERICAN
    out, last = [], 0
    for m in _TOKEN_RE.finditer(text or ""):
        out.append(_convert_words(text[last:m.start()], table, keep))
        out.append(m.group(0))
        last = m.end()
    out.append(_convert_words((text or "")[last:], table, keep))
    return "".join(out)


def keep_words(*phrases: str) -> frozenset[str]:
    """The lower-case words of the phrases that must keep their spelling (assigned keywords, the market name)."""
    return frozenset(w.lower() for p in phrases for w in _WORD_RE.findall(p or ""))


def apply(sections: list[dict], faq: list[dict], variety: str, keep: frozenset[str] = frozenset()) -> tuple[list[dict], list[dict]]:
    """The body paragraphs and the FAQ answers in one variety. Headings and FAQ questions are the plan's own and are left as they
    are (the checks compare them with the plan)."""
    for s in sections:
        s["paragraphs"] = [convert(p, variety, keep) for p in s["paragraphs"]]
    for item in faq:
        item["answer"] = convert(str(item.get("answer") or ""), variety, keep)
    return sections, faq
