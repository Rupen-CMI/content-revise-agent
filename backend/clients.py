"""The blog's clients: the paid backlinks it carries, who is behind each, and what each really offers.

A guest blog can carry one backlink, several backlinks of one client, or backlinks of several clients in the same field.
`build_clients` groups the chosen paragraphs' outside links by client (one paragraph per client). `run_briefs` then
reads each client's own web page and records what the client offers, so the opening can restate the client's paragraph
accurately and link it without a jump: the linked words can be broader than, or different from, what the client sells.

The writer restates the paragraph (config.CLIENT_REWRITE) and writes every link as `[[CLIENT_LINK:L1|linked words]]`;
the URLs stay here, so a model never types one. The checks and the document read the tokens back through `link_table`.
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from urllib.parse import urlparse

import config
import external_reports
import llm
import prompts
from docx_io import Link, Paragraph, SourceArticle
from pipeline_log import section

TOKEN_RE = re.compile(prompts.CLIENT_LINK_PATTERN)
_ANY_LINK_RE = re.compile(prompts.CLIENT_LINK_PATTERN + "|" + prompts.CMI_LINK_PATTERN)
_WORD_RE = re.compile(r"\b\w[\w'-]*\b")
_PAGE_CHARS = 3500
_COMMON_SECOND_LEVEL = frozenset({"co", "com", "org", "net", "ac", "gov", "edu"})
_FITS = ("matches", "broader", "different")
_DISTANCES = ("close", "adjacent", "distant")


@dataclass
class Client:
    id: str                                   # "C1"
    host: str                                 # the client's own domain, "www" removed
    paragraph: Paragraph                      # the guest article's paragraph that carries this client's link(s)
    links: list[dict]                         # [{"id": "L1", "anchor": "...", "url": "..."}]
    brief: dict | None = None                 # run_briefs: what the client's own page says it offers
    notes: list[str] = field(default_factory=list)   # things the editor should see (page unreadable, anchor broader, ...)

    def to_dict(self) -> dict:
        return {"id": self.id, "host": self.host, "paragraph_index": self.paragraph.index, "paragraph": self.paragraph.text,
                "links": [dict(l) for l in self.links], "brief": self.brief, "notes": list(self.notes)}


def registrable_host(url: str) -> str:
    """The client's domain: 'shop.acme.com' and 'www.acme.com' are one client, 'acme.co.uk' keeps its three labels."""
    labels = urlparse(url).netloc.lower().split(":")[0].removeprefix("www.").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _COMMON_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:]) if len(labels) >= 2 else ".".join(labels)


# ------------------------------------------------------------------------------------------------ grouping

def build_clients(article: SourceArticle, indexes: list[int], max_clients: int | None = None,
                  hosts: list[str] | None = None) -> tuple[list[Client], list[str]]:
    """The clients behind the chosen paragraphs, in the order met, and notes for the editor. One paragraph serves one client: a
    second chosen paragraph of a client already met is skipped (the editor picks the most suitable paragraph per client).
    `hosts`, when given, keeps only the links of those clients (a paragraph can carry the links of several clients and the
    editor may have ticked some of them). Raises ValueError for a paragraph that is missing or carries no outside link."""
    cap = max_clients or config.CLIENT_MAX
    wanted = {registrable_host(h if "//" in h else f"https://{h}") for h in hosts or []}
    by_host: dict[str, Client] = {}
    notes: list[str] = []
    link_n = 0
    for idx in dict.fromkeys(indexes):
        para = next((p for p in article.paragraphs if p.index == idx), None)
        if para is None:
            raise ValueError("A selected paragraph was not found in the article.")
        outside = [l for l in para.links if l.is_external]
        if not outside:
            raise ValueError("A selected paragraph has no outside link — pick the paragraphs with the paid backlinks.")
        for link in outside:
            host = registrable_host(link.url)
            if wanted and host not in wanted:
                continue
            client = by_host.get(host)
            if client is None:
                if len(by_host) >= cap:
                    notes.append(f"Only {cap} clients fit in one post: the link to {host} was left out.")
                    continue
                client = Client(id=f"C{len(by_host) + 1}", host=host, paragraph=para, links=[])
                by_host[host] = client
            elif client.paragraph.index != para.index:
                notes.append(f"{host} has more than one chosen paragraph: only the first one chosen is used for that client.")
                continue
            link_n += 1
            client.links.append({"id": f"L{link_n}", "anchor": link.anchor, "url": link.url})
    return list(by_host.values()), notes


def link_table(clients: list[Client]) -> dict[str, Link]:
    return {l["id"]: Link(anchor=l["anchor"], url=l["url"]) for c in clients for l in c.links}


def attach(sponsor: Paragraph, clients: list[Client]) -> None:
    """Let the run's sponsor paragraph resolve [[CLIENT_LINK:Ln|...]] tokens (see pipeline.expand_segments)."""
    sponsor.client_links = link_table(clients)


def original_text(clients: list[Client]) -> str:
    return "\n".join(c.paragraph.text for c in clients)


def source_text(clients: list[Client]) -> str:
    """Everything the sentences around a link may draw figures from: the original paragraphs and what each client's own page says it offers."""
    return "\n".join([c.paragraph.text for c in clients] + [(c.brief or {}).get("offer") or "" for c in clients]).strip()


def paragraph_words(clients: list[Client]) -> int:
    """Words the paragraph that carries the links is worth in the opening's budget: the originals' own length, held to a
    short paragraph (one client at most 90 words, several together at most 120, never under 45): it only has to make the
    link part of the opening's explanation."""
    own = sum(len(_WORD_RE.findall(c.paragraph.text)) for c in clients)
    return max(45, min(own, 90 if len(clients) <= 1 else 120))


def host_names(clients: list[Client]) -> list[str]:
    """Every client's own name, as the post must never write it: the domain's first label, and the name its page gave."""
    out: list[str] = []
    for c in clients:
        stem = c.host.split(".")[0]
        out += [stem, stem.replace("-", " ")]
        if c.brief and c.brief.get("name"):
            out.append(c.brief["name"])
    return list(dict.fromkeys(t for t in out if t))


# ------------------------------------------------------------------------------------------------ the client's own page

def _pages(client: Client, logger: logging.Logger) -> str:
    urls = list(dict.fromkeys(l["url"] for l in client.links))
    roots = [f"{urlparse(u).scheme}://{urlparse(u).netloc}/" for u in urls]
    blocks = []
    for u in list(dict.fromkeys(urls + roots))[:3]:
        text = external_reports.read_page(u, logger)
        if text:
            blocks.append(f"PAGE {u}:\n{text[:_PAGE_CHARS]}")
    return "\n\n".join(blocks)


def _text(value) -> str:
    return " ".join(str(value or "").split())


def normalize_brief(raw, page_text: str, paragraph_text: str) -> dict:
    """The model's answer as a brief the writer may rely on: the name must be written in the page or the paragraph, the offer is one
    short statement, every category is one of the allowed words, and what a field cannot support stays empty."""
    raw = raw if isinstance(raw, dict) else {}
    haystack = f"{page_text} {paragraph_text}".lower()
    name = _text(raw.get("name"))
    if name and name.lower() not in haystack:
        name = ""
    offer = _text(raw.get("offer"))
    if len(offer.split()) > 45:
        first = re.split(r"(?<=[.!?])\s+", offer)[0]
        offer = first if len(first.split()) <= 45 else " ".join(offer.split()[:45])
    fit = _text(raw.get("anchor_fit")).lower()
    fit = fit if fit in _FITS else "unknown"
    distance = _text(raw.get("market_fit")).lower()
    distance = distance if distance in _DISTANCES else "unknown"
    return {
        "name": name, "offer": offer, "category": _text(raw.get("category"))[:60],
        "anchor_fit": fit, "anchor_note": _text(raw.get("anchor_note")) if fit in ("broader", "different") else "",
        "market_fit": distance, "bridge": _text(raw.get("bridge")) if distance in ("adjacent", "distant") else "",
    }


def _briefs_by_client(raw, clients: list[Client]) -> dict[str, dict]:
    """{client id: that client's raw brief} from the one call's answer ({"briefs": [{"client": "C1", ...}]}). An entry that names
    no asked-for client, or names one twice, is ignored; with a single client an entry that names none is that client's."""
    items = raw.get("briefs") if isinstance(raw, dict) else None
    items = [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []
    ids = {c.id.upper(): c.id for c in clients}
    out: dict[str, dict] = {}
    for item in items:
        cid = ids.get(_text(item.get("client")).upper())
        if cid is None and len(clients) == 1 and not _text(item.get("client")):
            cid = clients[0].id
        if cid is not None and cid not in out:
            out[cid] = item
    return out


def brief_notes(client: Client) -> list[str]:
    """What the editor should know about this client before reading the draft."""
    out = list(client.notes)
    b = client.brief or {}
    if b.get("anchor_fit") in ("broader", "different"):
        out.append(f"The linked words {', '.join(repr(l['anchor']) for l in client.links)} are {b['anchor_fit']} than what the client offers"
                   .replace("different than", "different from") + (f": {b['anchor_note']}" if b.get("anchor_note") else "."))
    if b.get("market_fit") == "distant":
        out.append("The client's offer sits far from this blog's market"
                   + (f"; the opening connects them through: {b['bridge']}" if b.get("bridge") else "; no honest connection was found, so the client paragraph stays short and plain."))
    return out


def run_briefs(clients: list[Client], market_name: str, logger: logging.Logger) -> list[Client]:
    """CALL: what each client's own pages say it offers, set against its linked words and the post's market: the pages are read in
    parallel, then ONE call briefs every client whose page could be read, one entry per client, each validated on its own
    (normalize_brief). Clients keep their order. Best effort: a page that cannot be read, a failed call or a client the answer leaves
    out gives that client no brief and a note for the editor (the writer then has the paragraph alone)."""
    if not clients:
        return clients
    section(logger, "CALL: CLIENT BRIEFS")
    pages: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(clients)), thread_name_prefix="client-brief") as pool:
        futures = [(c, pool.submit(_pages, c, logger)) for c in clients]
        for c, f in futures:
            try:
                pages[c.id] = f.result()
            except Exception:           # a client page can throw anything; the run goes on without its brief
                logger.warning(f"[client] reading the pages of {c.host} raised", exc_info=True)
                pages[c.id] = ""
    readable: list[Client] = []
    for c in clients:
        c.brief = None
        if pages[c.id]:
            readable.append(c)
            continue
        c.notes.append(
            f"{c.host} could not be read, so the opening is written from the client's paragraph alone. "
            "Check what the client really offers before the draft goes out.")
        logger.info(f"[client] {c.id} {c.host}: no readable page")
    if not readable:
        return clients
    blocks = "\n".join(
        prompts.CLIENT_BRIEF_BLOCK.format(
            client_id=c.id, paragraph=marked_text(c), pages=pages[c.id],
            links="\n".join(f'{l["id"]}: "{l["anchor"]}" -> {l["url"]}' for l in c.links))
        for c in readable)

    def unresolved(c: Client) -> None:
        c.notes.append(f"What {c.host} offers could not be worked out; the opening rests on the client's paragraph alone.")

    try:
        raw = llm.chat_json(
            prompts.CLIENT_BRIEF_SYSTEM, prompts.CLIENT_BRIEFS_USER.format(market_name=market_name, clients_block=blocks),
            temperature=0.1, call_name="client_briefs", logger=logger)
    except llm.LLMError:
        logger.warning(f"[client] the brief call for {', '.join(c.host for c in readable)} failed", exc_info=True)
        for c in readable:
            unresolved(c)
        return clients
    found = _briefs_by_client(raw, readable)
    for c in readable:
        if c.id not in found:
            logger.warning(f"[client] the answer holds no brief for {c.id} {c.host}")
            unresolved(c)
            continue
        c.brief = normalize_brief(found[c.id], pages[c.id], c.paragraph.text)
        logger.info(f"[client] {c.id} {c.host}: offer={c.brief['offer'][:90]!r}; anchor {c.brief['anchor_fit']}; market {c.brief['market_fit']}")
    return clients


# ------------------------------------------------------------------------------------------------ what the writer sees

def marked_text(client: Client) -> str:
    """The client's paragraph with each link's words marked <<L1: words>>, so the writer sees which words carry which link."""
    text, pos, out = client.paragraph.text, 0, []
    for link in client.links:
        i = text.find(link["anchor"], pos) if link["anchor"] else -1
        if i < 0:
            continue
        out += [text[pos:i], f'<<{link["id"]}: {link["anchor"]}>>']
        pos = i + len(link["anchor"])
    out.append(text[pos:])
    return "".join(out)


def client_block(clients: list[Client]) -> str:
    """The text of the write prompt's client link rules, with every client's source paragraph, links and brief. The writer is told
    each client's name only to keep it out of the post: the link is the client's one mention."""
    rows = []
    for c in clients:
        b = c.brief or {}
        keep_out = ", ".join(dict.fromkeys([x for x in [(b.get("name") or ""), c.host.split(".")[0], c.host] if x]))
        rows.append(f'CLIENT {c.id} (never written in the post: {keep_out}); links: ' + "; ".join(f'{l["id"]} "{l["anchor"]}"' for l in c.links))
        rows.append(f'  The guest article\'s paragraph that holds the link (linked words marked <<id: words>>; its idea is carried, its wording and its client are not): "{marked_text(c)}"')
        if b.get("category") or b.get("offer"):
            # Only the kind of business reaches the writer, never the page's offer sentence: with the full sentence in view the
            # writer reused its wording and described what the client sells ("without a large upfront commitment", "resourcing,
            # financial control and forecasting in one view"). feature_problems reports any such wording that still gets through.
            rows.append(f'  The kind of business the linked page belongs to, for accuracy only (never written in the post, and none of the client\'s features, benefits or services are described): {b.get("category") or "see the linked words"}')
        else:
            rows.append("  What the linked page offers: not available (the page could not be read). Use the linked words only as the guest article's paragraph uses them.")
        if b.get("anchor_fit") in ("broader", "different"):
            rows.append(f'  The linked words are {b["anchor_fit"]} than what the page offers'.replace("different than", "different from")
                        + (f': {b["anchor_note"]}' if b.get("anchor_note") else ".")
                        + " Use them only in a way the page supports.")
        if b.get("market_fit") in ("adjacent", "distant"):
            rows.append(f'  Distance of the page from the post\'s market: {b["market_fit"]}.'
                        + (f' The connection that holds: {b["bridge"]}' if b.get("bridge") else " No connection beyond the shared reader is supported: say no more than that."))
    return prompts.CLIENT_BLOCK.format(
        client_count=len(clients),
        combined_rule=("The paragraph carries this one link." if len(clients) == 1 else
                       "The clients work in the same field, so the links sit together in ONE paragraph, in sentences that speak of that kind of "
                       "service in general; no link gets more space than another and none is set against another."),
        client_words=paragraph_words(clients), clients_text="\n".join(rows))


# ------------------------------------------------------------------------------------------------ the draft side

def intro_paragraphs(sections: list[dict]) -> list[str]:
    """The opening's paragraphs: the first untagged section."""
    for s in sections:
        if not s.get("sop"):
            return list(s["paragraphs"])
    return []


def plain(text: str) -> str:
    """The text with every client link token reduced to its linked words, the form the content checks read."""
    return TOKEN_RE.sub(lambda m: m.group(2), text)


def plain_sections(sections: list[dict]) -> list[dict]:
    return [{**s, "paragraphs": [plain(p) for p in s["paragraphs"]]} for s in sections]


def normalize_tokens(sections: list[dict], clients: list[Client], logger: logging.Logger) -> list[dict]:
    """Make every client link in the draft one correct token, in the opening. The linked words are the registered ones, never the
    writer's rewording; a token for an unknown link, or a second token for a link, is reduced to plain words; a link whose words
    stand in the opening without a token is turned into its token. A link the writer left out stays missing (the check reports it)."""
    table = {l["id"]: l for c in clients for l in c.links}
    seen: set[str] = set()
    for s in sections:
        for pi, p in enumerate(s["paragraphs"]):
            def fix(m: re.Match) -> str:
                lid, words = m.group(1), m.group(2)
                link = table.get(lid)
                if link is None:
                    logger.info(f"[write] client link {lid} is unknown: left as plain words")
                    return words
                if lid in seen:
                    logger.info(f"[write] client link {lid} was written twice: the second is plain words")
                    return words
                seen.add(lid)
                if words != link["anchor"]:
                    logger.info(f"[write] client link {lid}: the linked words {words!r} were set back to {link['anchor']!r}")
                return f"[[CLIENT_LINK:{lid}|{link['anchor']}]]"
            s["paragraphs"][pi] = TOKEN_RE.sub(fix, p)
    intro = next((s for s in sections if not s.get("sop")), None)
    for lid, link in table.items():
        if lid in seen or intro is None or not link["anchor"]:
            continue
        pattern = re.compile(r"(?<![\w\[|])" + re.escape(link["anchor"]) + r"(?![\w\]])", re.IGNORECASE)
        for pi, p in enumerate(intro["paragraphs"]):
            # the words may stand inside the report link ("[[CMI_LINK:Agile Project Management Software Market]]"): never there
            masked = _ANY_LINK_RE.sub(lambda t: "\x00" * len(t.group(0)), p)
            m = pattern.search(masked)
            if m:
                intro["paragraphs"][pi] = p[:m.start()] + f"[[CLIENT_LINK:{lid}|{link['anchor']}]]" + p[m.end():]
                logger.info(f"[write] client link {lid} had no token: its words in the opening were linked")
                break
    return sections


# ------------------------------------------------------------------------------------------------ checks

def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def link_problems(raw_sections: list[dict], clients: list[Client]) -> list[str]:
    """Every client link is in the draft exactly once, with its own words, inside the opening."""
    intro_text = "\n".join(intro_paragraphs(raw_sections))
    all_text = "\n".join(p for s in raw_sections for p in s["paragraphs"])
    out: list[str] = []
    for c in clients:
        for l in c.links:
            tokens = [m for m in TOKEN_RE.finditer(all_text) if m.group(1) == l["id"]]
            if not tokens:
                out.append(f'the link "{l["anchor"]}" of {c.host} is missing from the draft')
                continue
            if len(tokens) > 1:
                out.append(f'the link "{l["anchor"]}" of {c.host} appears {len(tokens)} times')
            if tokens[0].group(2) != l["anchor"]:
                out.append(f'the linked words of {c.host} were changed to "{tokens[0].group(2)}"')
            if not any(m.group(1) == l["id"] for m in TOKEN_RE.finditer(intro_text)):
                out.append(f'the link "{l["anchor"]}" of {c.host} is not in the opening')
    return out


def naming_problems(raw_sections: list[dict], clients: list[Client]) -> list[str]:
    """The link is the client's one mention: its name (as its page gave it, and its domain's first word) is written nowhere in the post.
    The linked words themselves are left out of the reading, so a link whose words ARE the client's name is not a fault."""
    out: list[str] = []
    # A client whose name is also an ordinary word ("teamwork") is not named by that word used as a word: only the name written as a
    # name counts. A capitalised form counts; so does the domain. A capitalised form that merely opens a sentence is the ordinary word
    # when the post also writes it in lower case elsewhere.
    plain_words = {w for s in raw_sections for p in s["paragraphs"] for w in re.findall(r"\b[a-z]+\b", TOKEN_RE.sub(" ", p))}
    for c in clients:
        names = [n for n in dict.fromkeys([c.host.split(".")[0], (c.brief or {}).get("name") or ""]) if n]

        def written_as_name(text: str) -> str | None:
            if c.host.lower() in text.lower():
                return c.host
            for n in names:
                for m in re.finditer(r"(?<![\w])" + re.escape(n) + r"(?![\w])", text, re.IGNORECASE):
                    word = m.group(0)
                    if word == word.lower():
                        continue
                    before = text[:m.start()].rstrip()
                    if (not before or before[-1] in ".!?\n:#-") and word.lower() in plain_words:
                        continue
                    return n
            return None

        for s in raw_sections:
            label = s["sop"][0] if s.get("sop") else "the opening"
            for p in s["paragraphs"]:
                hit = written_as_name(TOKEN_RE.sub(" ", p))
                if hit:
                    out.append(f"{label} names the client ({hit!r}, {c.host}): the link is its only mention")
                    break
    return out


_FEATURE_STOP = frozenset({
    "about", "after", "again", "also", "among", "because", "before", "being", "between", "buyers", "could", "during", "every", "first",
    "found", "given", "other", "their", "there", "these", "those", "through", "under", "until", "which", "while", "would", "within",
    "without", "across", "market", "markets", "global", "where", "whose", "often", "using", "based", "makes", "make", "made",
    "services", "service", "companies", "company", "customers", "customer", "clients", "client", "business", "businesses",
})
_FEATURE_MIN_SHARED = 3


def _stems(text: str) -> set[str]:
    """Five-letter stems of the words of five letters or more, without the filler words (a crude stem: 'manufacturing' and
    'manufacturer' both read 'manuf')."""
    return {w[:5] for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 5 and w not in _FEATURE_STOP}


def feature_problems(raw_sections: list[dict], clients: list[Client], market_name: str = "", subject: str = "") -> list[str]:
    """The post must not describe what a client sells. A paragraph that carries a client's link and repeats three or more
    content words that only the client's own page uses (its offer and its kind of business) is describing the client's offer,
    not the market. Words that the guest article's own paragraph used, its subject (the opening is about it), the linked words
    and the market's name are the post's own to use and are left out. The name check cannot see this: no name is written."""
    out: list[str] = []
    for c in clients:
        b = c.brief or {}
        page = _stems(" ".join([b.get("offer") or "", b.get("category") or ""]))
        if not page:
            continue
        allowed = _stems(c.paragraph.text) | _stems(" ".join(l["anchor"] for l in c.links)) | _stems(market_name) | _stems(subject)
        # One opening paragraph can carry several clients' links: another client's linked words and its own paragraph are as much the
        # post's own as this client's, so words only they bring ("digital marketing agency") are not this client's offer.
        for other in clients:
            if other is not c:
                allowed |= _stems(other.paragraph.text) | _stems(" ".join(l["anchor"] for l in other.links))
        ids = {l["id"] for l in c.links}
        for p in intro_paragraphs(raw_sections):
            if not any(m.group(1) in ids for m in TOKEN_RE.finditer(p)):
                continue
            shared = sorted((_stems(plain(p)) & page) - allowed)
            if len(shared) >= _FEATURE_MIN_SHARED:
                out.append(f"the sentences around the {c.host} link repeat words from the client's own offer ({', '.join(shared[:6])}): "
                           "the post describes what the client sells instead of the market")
    return out
