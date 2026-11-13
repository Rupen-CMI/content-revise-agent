"""The Evidence step's section layer: which sections the article's evidence serves, what each section searches for, and how
the hits and the selected items are bookkept per section.

Everything here is pure code on the confirmed fact sheet (no model, no network): the section search plan (trends from the
report's top drivers and opportunities, the two fixed sub-segments, the report's own U.S. statements, the key players, the
market as a whole), the Bing query budget and its allotment per section, the section tags every hit and every selected
item carries, the per-section minimums and the count that decides which section is thin. The segmentation section has no
search: it describes the report's own splits and is written from the report's facts alone.

Imports only search (never pipeline, facts or planning), so all three can import it.
"""
import hashlib
import logging
import re

import config
import search

# The sections whose queries run (a hit found by none of the specific ones is "market"), and the sections SELECT may assign.
QUERY_SECTIONS = ("trends", "subsegment", "us", "key_players", "market")
EVIDENCE_SECTIONS = ("trends", "subsegment", "us", "key_players")

# Fewest selected items a section should have; a section below it is warned about as thin (there is no top-up search).
MINIMUMS = {"trends": 6, "subsegment": 1, "us": 2, "key_players": 3}

# THE BING BUDGET. One run spends 35 Bing keyword queries (~2.8 s each, strictly one at a time) plus the 3 site: ones, allotted per section;
# the extra rounds that used to follow (a broader round, a per-section top-up, a separate U.S. search) added next to nothing.
# Raised from 6/3/3/6/4 (25 with two sub-segments) after live runs showed the cut dropping evidence hard (EV charger 33 -> 13
# selected items): the queries themselves were stiff too ("anchor phrase in quotes" + a word or two), so the query-writing
# rules moved to natural news-search phrasing too (prompts._QUERY_RULES_NATURAL). config.QUERY_STYLE picks the row: "natural"
# (default) or "terse", which brings the previous budget, rules and code-built U.S./key-player queries back for an A/B run.
#   trends        the model's trends queries (one per target: three drivers, two opportunities, spares)
#   subsegment    per fixed sub-segment: the evidence-prep call's queries for it, in the category's everyday words;
#                 SUBSEGMENT_WORDS build the stand-in when the model wrote none
#   us            natural: the model's U.S. queries; the code-built templates (us_search.us_news_queries) stand in when it
#                 wrote none (terse: always the templates)
#   key_players   natural: the model's, one per company; the code-built ones (pipeline._player_news_queries) stand in when it
#                 wrote none (terse: always the code-built ones)
#   market        the model's general queries
# plus at most BING_SITE_QUERIES site: queries (feeds._BING_MAX_SITE_QUERIES): the three broadest news hosts.
_BUDGETS = {
    "natural": {"trends": 8, "subsegment": 4, "us": 5, "key_players": 8, "market": 6},
    "terse": {"trends": 6, "subsegment": 3, "us": 3, "key_players": 6, "market": 4},
}
_SUBSEGMENT_WORDS = {"natural": ("demand", "launch", "news", "adoption"), "terse": ("demand", "launch", "news")}
BING_SITE_QUERIES = 3


def natural_style() -> bool:
    """True for config.QUERY_STYLE "natural" (the default; any value but "terse"), False for the previous "terse" behaviour."""
    return config.QUERY_STYLE != "terse"


def allot(section: str) -> int:
    """The keyword queries one section is allotted (per sub-segment for "subsegment") in the active query style."""
    return _BUDGETS["natural" if natural_style() else "terse"][section]


def subsegment_words() -> tuple[str, ...]:
    """The words the code-built sub-segment queries end in, one per allotted query."""
    return _SUBSEGMENT_WORDS["natural" if natural_style() else "terse"]

# A zero-result QUOTED query is retried unquoted only while its section has no hit at all, and at most this often a run.
UNQUOTED_RETRIES = search.UNQUOTED_RETRIES

_SUBSEGMENTS = 2
_QUERIES_PER_SECTION_MAX = 8
_TARGET_CHARS = 220

_REGION_SPLIT_RE = re.compile(r"region|geograph|countr|area|location", re.I)
_SECTION_ALIASES = {
    "trend": "trends", "trends": "trends", "subsegment": "subsegment", "subsegments": "subsegment",
    "sub_segment": "subsegment", "sub_segments": "subsegment", "us": "us", "u_s": "us", "country_us": "us",
    "united_states": "us", "key_players": "key_players", "players": "key_players", "keyplayers": "key_players",
    "market": "market", "general": "market",
}
# a section SELECT may name maps to the evidence tag the rest of the chain already reads ("players" for key_players)
_TAG_OF_SECTION = {"trends": "trends", "us": "us", "key_players": "players"}
_SECTION_OF_TAG = {"trends": "trends", "us": "us", "players": "key_players", "key_players": "key_players"}
_LABELS = {"trends": "Trends", "us": "U.S.", "key_players": "Key players"}


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).split())


def is_region_split(name: str) -> bool:
    """A geographic split ("By Region"): regions are the report-context and U.S. sections' business, never a sub-segment."""
    return bool(_REGION_SPLIT_RE.search(name or ""))


# ---------------------------------------------------------------------------------------------- sub-segments

def _share_leader(seg: dict) -> str | None:
    """The category with the largest stated share, when the split states any share."""
    shares = [(n, f["amount"]) for n, f in (seg.get("figures") or {}).items() if f.get("kind") == "share" and n in seg["items"]]
    return max(shares, key=lambda x: x[1])[0] if shares else None


def _leader_share(seg: dict) -> float | None:
    """The stated share of the split's confirmed leader, when it states one."""
    dom = (seg.get("dominant") or {}).get("name")
    f = (seg.get("figures") or {}).get(dom) if dom else None
    return f["amount"] if f and f.get("kind") == "share" else None


def _best_share(seg: dict) -> float | None:
    """The largest stated share among the split's categories, when it states any."""
    shares = [f["amount"] for f in (seg.get("figures") or {}).values() if f.get("kind") == "share"]
    return max(shares) if shares else None


def _other_leader(seg: dict) -> str | None:
    """The category a second split's sub-segment names: its confirmed leader, else its largest stated share."""
    return (seg.get("dominant") or {}).get("name") or _share_leader(seg)


def _top_two(seg: dict) -> list[dict]:
    """The split's top two categories by what the report states: the confirmed leader first (or its largest stated share
    when the page does not name a leader), then the highest stated figure of the same kind among the rest. Fewer when
    the split names fewer than two such categories."""
    dom = (seg.get("dominant") or {}).get("name")
    first = dom or _share_leader(seg)
    if not first:
        return []
    picks = [{"split": seg["type"], "name": first, "basis": "dominant" if dom else "share", "id": seg.get("id", "")}]
    figures = {n: f for n, f in (seg.get("figures") or {}).items() if n in seg["items"] and n != first}
    for kind in ("share", "value"):
        rest = sorted((n, f["amount"]) for n, f in figures.items() if f.get("kind") == kind)
        if rest:
            picks.append({"split": seg["type"], "name": rest[-1][0], "basis": "second_in_split", "id": seg.get("id", "")})
            break
    return picks


def choose_subsegments(fs: dict, logger: logging.Logger | None = None) -> list[dict]:
    """The two sub-segments, fixed before any news is searched: the top two categories of ONE split, ranked by the
    report's stated figures with the split's confirmed leader first. The split is the one whose confirmed leader has the
    highest stated share; never a region split; a split with a stated share ranks before one with only a textual leader;
    when the chosen split gives a share or a dominance for only one of its categories, the second sub-segment is the
    leader of another split instead. When no split states a leader or a share, the first non-region split's first two
    categories are used, logged. [{"split", "name", "basis": "dominant"|"share"|"second_in_split"|"other_split_leader"|
    "fallback", "id"}], at most two, fewer when the report has fewer categories to name."""
    log = logger or logging.getLogger(__name__)
    segments = [s for s in (fs.get("segments") or []) if s.get("items")]
    if not segments:
        return []
    ranked = []
    for idx, seg in enumerate(segments):
        leader_share, best_share = _leader_share(seg), _best_share(seg)
        key = (0, -leader_share) if leader_share is not None else (1, -best_share) if best_share is not None \
            else (2, 0) if seg.get("dominant") else (3, 0)
        ranked.append((is_region_split(seg["type"]), key, idx, seg))
    pool = sorted((r for r in ranked if not r[0]), key=lambda r: (r[0], r[1], r[2])) \
        or sorted(ranked, key=lambda r: (r[0], r[1], r[2]))      # a region split only when no other split exists
    seg = pool[0][3]
    picks = _top_two(seg)
    if len(picks) < 2:
        other = next((r[3] for r in pool[1:] if _other_leader(r[3])), None)
        if other:
            picks.append({"split": other["type"], "name": _other_leader(other), "basis": "other_split_leader", "id": other.get("id", "")})
    if not picks:
        first = next((r[3] for r in sorted(pool, key=lambda r: r[2]) if not is_region_split(r[3]["type"])), None) or pool[0][3]
        picks = [{"split": first["type"], "name": n, "basis": "fallback", "id": first.get("id", "")}
                 for n in first["items"][:_SUBSEGMENTS]]
        log.info(f"[sections] no split states a leading category or a share; the first split's first two categories "
                 f"({first['type']!r}: {', '.join(first['items'][:_SUBSEGMENTS])}) are used as the sub-segments")
    return picks


# ------------------------------------------------------------------------------------------- the search plan

def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _TARGET_CHARS else text[:_TARGET_CHARS].rsplit(" ", 1)[0]


def _market_everyday(market: str) -> str:
    """The market's name the way people type it into a search box: no leading "Global", no trailing "Market" ("Home EV
    Charger Market" -> "home ev charger"). The everyday phrasing the query prompt offers the model beside the report name;
    the natural-query rules ask it for more synonyms on its own."""
    words = re.findall(r"[a-z0-9]+", (market or "").lower())
    while words and words[0] == "global":
        words.pop(0)
    while words and words[-1] in ("market", "markets"):
        words.pop()
    return " ".join(words) or (market or "").strip()


def _subsegment_reason(fs: dict, split: str, name: str) -> str:
    """The report's own reason a sub-segment category leads: its segment driver (fs.segment_drivers) when one names this
    split and category, else the split's dominance quote when it names the category, else '' (the prompt then writes the
    queries from the category name alone)."""
    for e in fs.get("segment_drivers") or []:
        if e.get("split") == split and e.get("name") == name and e.get("text"):
            return str(e["text"]).strip()
    for seg in fs.get("segments") or []:
        if seg.get("type") == split and (seg.get("dominant") or {}).get("name") == name:
            return " ".join(str((seg.get("dominant") or {}).get("quote") or "").split())
    return ""


def section_search_plan(fs: dict, report, key_players=(), subsegments: list[dict] | None = None) -> dict[str, list[dict]]:
    """What each section's search looks for, from the confirmed fact sheet alone. Every target is {"text", ...}:
      trends        the top drivers, then opportunities (allot("trends") targets, one query each);
      subsegment    the two fixed sub-segments (subsegments, or choose_subsegments), each with the report's own reason
                    the category leads (_subsegment_reason), so the query prompt can phrase the queries per sub-segment;
      us            the report's own U.S. statements, as seeds;
      key_players   the cleaned player names;
      market        the market as a whole (general recent news)."""
    market = getattr(report, "name", report) or ""
    drivers = [e for e in fs.get("drivers") or [] if e.get("text")]
    opportunities = [e for e in fs.get("opportunities") or [] if e.get("text")]
    ordered = [("drivers", e) for e in drivers[:3]] + [("opportunities", e) for e in opportunities[:2]]
    spare = [("drivers", e) for e in drivers[3:]] + [("opportunities", e) for e in opportunities[2:]]
    ordered = (ordered + spare)[:allot("trends")]
    picks = subsegments if subsegments is not None else choose_subsegments(fs)
    subs = [{"split": p["split"], "name": p["name"], "basis": p.get("basis", ""), "text": f"{p['name']} ({p['split']})"} for p in picks]
    for t in subs:
        t["reason"] = _subsegment_reason(fs, t["split"], t["name"])
    return {
        "trends": [{"kind": kind, "text": _short(e["text"])} for kind, e in ordered],
        "subsegment": subs,
        "us": [{"text": _short(e["text"])} for e in (fs.get("us") or [])[:3] if e.get("text")],
        "key_players": [{"text": n} for n in search.clean_player_names(list(key_players))],
        "market": [{"text": market, "everyday": _market_everyday(market)}],
    }


def format_targets(plan: dict) -> str:
    """The plan as the query-writing prompt reads it: one block per section the model writes queries for. In the natural
    style (config.QUERY_STYLE, the default) that is every section — trends, the fixed sub-segments, the U.S. (the report's
    own U.S. statements are the seeds), the key players (the cleaned names) and the market (with its everyday wording); in
    the terse style only the trends, sub-segment and market blocks are printed (the U.S. and key-player queries are built
    in code, see budgeted_queries)."""
    def lines(key: str, empty: str) -> str:
        return "\n".join(f"- {t['text']}" for t in plan.get(key) or []) or f"- {empty}"

    sub_lines = "\n".join(
        f"- {t['name']} (split {t['split']})" + (f" — the report's reason it leads: {t['reason']}" if t.get("reason") else "")
        for t in plan.get("subsegment") or []
    ) or "(no sub-segments fixed: write no subsegment queries)"
    market_lines = lines("market", "(the market)")
    everyday = next((t.get("everyday") for t in plan.get("market") or [] if t.get("everyday")), "")
    if natural_style():
        market_lines = market_lines + (f' (everyday wording: "{everyday}")' if everyday else "")
        us_lines = lines("us", "(no U.S. statements: ask what is happening with the market in the United States)")
        player_lines = lines("key_players", "(no known players: ask about the market's leading companies)")
        return "\n".join([
            f"TRENDS ({allot('trends')} queries, one per target; each target is a driver or opportunity the news should show happening):",
            lines("trends", "(no targets: ask what is changing in the market itself)"),
            f"SUB-SEGMENTS ({allot('subsegment')} queries for EACH sub-segment below, returned under the \"subsegment\" key keyed "
            f"by the sub-segment's name; the reason is the report's own statement of why the category leads):",
            sub_lines,
            f"U.S. ({allot('us')} queries; the report's own U.S. statements below are the seeds — the news you look for shows "
            f"them happening; name the U.S. naturally in every query: \"US\", \"American\", a U.S. state or agency where it fits):",
            us_lines,
            f"KEY PLAYERS ({allot('key_players')} queries, one per company; the company's own news — a launch, an acquisition, "
            f"funding, a partnership, an expansion — is what this section reads):",
            player_lines,
            f"MARKET ({allot('market')} general queries on recent developments in the market as a whole):",
            market_lines,
        ])
    return "\n".join([
        f"TRENDS ({allot('trends')} queries, one per target; each target is a driver or opportunity the news should show happening):",
        lines("trends", "(no targets: ask what is changing in the market itself)"),
        f"SUB-SEGMENTS ({allot('subsegment')} queries for EACH sub-segment below, returned under the \"subsegment\" key keyed "
        f"by the sub-segment's name; the reason is the report's own statement of why the category leads):",
        sub_lines,
        f"MARKET ({allot('market')} general queries on recent developments in the market as a whole):",
        market_lines,
    ])


# ------------------------------------------------------------------------------------------------- queries

def section_key(name: str) -> str | None:
    return _SECTION_ALIASES.get(re.sub(r"[^a-z]+", "_", str(name or "").strip().lower()).strip("_"))


def parse_grouped_queries(raw) -> dict[str, list[str]]:
    """The query-writing answer as {section: [query, ...]} for every query section. A plain list (the old shape) is all
    "market"; an unknown section name is ignored; each section's list is cleaned, de-duplicated and capped."""
    out: dict[str, list[str]] = {s: [] for s in QUERY_SECTIONS}
    items = raw.items() if isinstance(raw, dict) else [("market", raw)] if isinstance(raw, list) else []
    for key, values in items:
        sec = section_key(key)
        if sec is None or not isinstance(values, list):
            continue
        for q in values:
            q = " ".join(str(q).split())
            if q and q.lower() not in {x.lower() for x in out[sec]} and len(out[sec]) < _QUERIES_PER_SECTION_MAX:
                out[sec].append(q)
    return out


def flatten_queries(grouped: dict[str, list[str]]) -> tuple[list[str], dict[str, list[str]]]:
    """(queries, {lower-cased query: [sections]}): the queries one per section in turn (so a cap on how many run cuts every
    section evenly, never one section whole), each once; a query two sections wrote keeps both sections."""
    queries: list[str] = []
    sections_of: dict[str, list[str]] = {}
    depth = max((len(v) for v in grouped.values()), default=0)
    for i in range(depth):
        for sec in QUERY_SECTIONS:
            vals = grouped.get(sec) or []
            if i >= len(vals):
                continue
            key = vals[i].lower()
            if key not in sections_of:
                queries.append(vals[i])
                sections_of[key] = []
            if sec not in sections_of[key]:
                sections_of[key].append(sec)
    return queries, sections_of


def add_queries(grouped: dict[str, list[str]], section: str, queries: list[str]) -> dict[str, list[str]]:
    """grouped with these queries added to one section (a query already in it is not repeated); the input is not changed."""
    out = {k: list(v) for k, v in grouped.items()}
    for q in queries:
        if q.lower() not in {x.lower() for x in out.setdefault(section, [])}:
            out[section].append(q)
    return out


def fallback_queries(plan: dict, core: str, market_templates: list[str]) -> dict[str, list[str]]:
    """The grouped queries when the model gives none: the deterministic market templates, plus short anchored trends queries.
    (The sub-segment, U.S. and key-player queries are built in code anyway: see budgeted_queries.)"""
    grouped: dict[str, list[str]] = {s: [] for s in QUERY_SECTIONS}
    grouped["market"] = list(market_templates)
    grouped["trends"] = [f'"{core}" {w}' for w in ("adoption", "investment", "partnership", "launch")]
    return grouped


def subsegment_fallback_queries(core: str, subsegments: list[dict]) -> dict[str, list[str]]:
    """Code-built queries per sub-segment, {cleaned category name: [allot('subsegment') queries]}: the category's own name
    plus the market's core noun and one word each ("<category> <market core> demand" / "launch" / "news"). Only the stand-in
    for a sub-segment the model wrote no queries for (see budgeted_queries)."""
    out: dict[str, list[str]] = {}
    for s in subsegments:
        name = " ".join(re.sub(r"[\"“”]", " ", str(s.get("name") or "")).split())
        if name:
            out[name] = [f"{name} {core} {word}".strip() for word in subsegment_words()[:allot("subsegment")]]
    return out


def subsegment_queries(core: str, subsegments: list[dict]) -> list[str]:
    """The code-built sub-segment queries, flat (each category's name and the market's core noun, allot('subsegment') each,
    subsegment_words() one after another). The fallback shape budgeted_queries composes per sub-segment."""
    out: list[str] = []
    for qs in subsegment_fallback_queries(core, subsegments).values():
        for q in qs:
            if q.lower() not in {x.lower() for x in out}:
                out.append(q)
    return out


def parse_subsegment_queries(raw, subsegments: list[dict]) -> dict[str, list[str]]:
    """The model's sub-segment queries as {sub-segment name: [query, ...]}, at most allot('subsegment') each, from the
    query answer's "subsegment" part: a dict keyed by sub-segment name (the asked-for shape) or a flat list, whose queries
    are matched to a fixed sub-segment by the category words they share (match_subsegment). A query no sub-segment claims,
    and a sub-segment the model wrote nothing for, are left out — the code-built shape stands in for that one
    (budgeted_queries)."""
    part = raw.get("subsegment") if isinstance(raw, dict) else None
    out: dict[str, list[str]] = {}

    def add(name: str | None, q: str) -> None:
        if not name:
            return
        bucket = out.setdefault(name, [])
        if q and q.lower() not in {x.lower() for x in bucket} and len(bucket) < allot("subsegment"):
            bucket.append(q)

    if isinstance(part, dict):
        for key, values in part.items():
            if isinstance(values, list):
                for q in values:
                    add(match_subsegment(key, subsegments), " ".join(str(q).split()))
    elif isinstance(part, list):
        for q in part:
            q = " ".join(str(q).split())
            add(match_subsegment(q, subsegments), q)
    return {name: qs for name, qs in out.items() if qs}


def budgeted_queries(
    grouped: dict[str, list[str]], core: str, subsegments: list[dict], us_queries: list[str], player_queries: list[str],
) -> dict[str, list[str]]:
    """The one Bing budget, allotted per section (see _BUDGETS): the model's queries cut to each
    section's allotment, the sub-segment queries the model wrote for the fixed sub-segments (grouped["subsegment"] as the
    parsed {sub-segment: [queries]} map of parse_subsegment_queries; a sub-segment the model wrote none for keeps the
    code-built shape). In the natural style (config.QUERY_STYLE, the default) the U.S. and key-player queries are the
    model's own too, and the code-built templates (us_search.us_news_queries, pipeline._player_news_queries) stand in only
    for a section the model wrote none for; in the terse style the code-built shapes always run, as before. The input is
    not changed."""
    def cut(values, n: int) -> list[str]:
        return list(dict.fromkeys(" ".join(str(q).split()) for q in values or [] if str(q).strip()))[:n]

    natural = natural_style()
    model_us = cut(grouped.get("us"), allot("us")) if natural else []
    model_players = cut(grouped.get("key_players"), allot("key_players")) if natural else []
    model_map = grouped.get("subsegment") if isinstance(grouped.get("subsegment"), dict) else {}
    sub_q: list[str] = []
    for name, fallback in subsegment_fallback_queries(core, subsegments).items():
        model = cut(next((v for k, v in model_map.items() if isinstance(v, list) and _norm(k) == _norm(name)), []), allot("subsegment"))
        for q in model or fallback:
            if q.lower() not in {x.lower() for x in sub_q}:
                sub_q.append(q)
    return {
        "trends": cut(grouped.get("trends"), allot("trends")),
        "subsegment": sub_q,
        "us": model_us or cut(us_queries, allot("us")),
        "key_players": model_players or cut(player_queries or grouped.get("key_players"), allot("key_players")),
        "market": cut(grouped.get("market"), allot("market")),
    }


def keyword_query_budget(subsegments: list[dict]) -> int:
    """The most keyword queries one run spends on Bing (the site: queries, BING_SITE_QUERIES, come on top)."""
    return (allot("trends") + allot("subsegment") * min(len(subsegments), _SUBSEGMENTS) + allot("us") + allot("key_players")
            + allot("market"))


# --------------------------------------------------------------------------------------------------- hits

def _title_key(title: str) -> str:
    return hashlib.sha256(_norm(title).encode()).hexdigest()


def merge_duplicate_hits(hits: list) -> list:
    """The hits with each page once (same URL or same headline), best-first order kept. A page two sections' queries found
    stays one hit that carries both sections; it is the first copy that is kept."""
    seen: dict[str, object] = {}
    out = []
    for h in hits:
        keys = (h.url, _title_key(h.title))
        first = next((seen[k] for k in keys if k in seen), None)
        if first is not None:
            first.add_sections(h.sections)
            for k in keys:
                seen.setdefault(k, first)
            continue
        for k in keys:
            seen[k] = h
        out.append(h)
    return out


def cap_hits(hits: list, limit: int, floor: int) -> list:
    """At most `limit` hits, best-first order kept, in which every section keeps up to `floor` of its own before the rest fill
    what is left: a prolific section cannot push another's few hits out of the shortlist."""
    if len(hits) <= limit:
        return list(hits)
    chosen: set[int] = set()
    count: dict[str, int] = {}
    for i, h in enumerate(hits):
        secs = h.sections or ["market"]
        if len(chosen) < limit and any(count.get(s, 0) < floor for s in secs):
            chosen.add(i)
            for s in secs:
                count[s] = count.get(s, 0) + 1
    for i in range(len(hits)):
        if len(chosen) >= limit:
            break
        chosen.add(i)
    return [h for i, h in enumerate(hits) if i in chosen]


def hit_counts(hits: list) -> dict[str, int]:
    """Hits per section (a hit two sections found counts in both)."""
    out: dict[str, int] = {}
    for h in hits:
        for s in h.sections or ["market"]:
            out[s] = out.get(s, 0) + 1
    return out


# ---------------------------------------------------------------------------------------- selected items

def tag_name(name: str) -> str:
    """A category name as it can sit inside an evidence tag (no comma or bracket: tags are a comma list in brackets)."""
    return " ".join(re.sub(r"[,\[\]]", " ", str(name)).split())


def evidence_tag(section: str, subsegment: str = "") -> str:
    """The single tag a selected item carries: "trends", "us", "players" or "subsegment:<category>"."""
    return f"subsegment:{tag_name(subsegment)}" if section == "subsegment" else _TAG_OF_SECTION[section]


def tag_section(tag: str) -> tuple[str, str] | None:
    """(section, category name) of a tag, or None for a tag that names no section (the old "context")."""
    tag = (tag or "").strip()
    if tag.lower().startswith("subsegment"):
        return "subsegment", tag.partition(":")[2].strip()
    sec = _SECTION_OF_TAG.get(tag.lower())
    return (sec, "") if sec else None


def match_subsegment(text: str, subsegments: list[dict]) -> str | None:
    """Which fixed sub-segment this text names: the one whose normalised name equals or sits inside it (or holds it), else the
    one sharing the most words; None when none shares a word."""
    want = _norm(text)
    if not want:
        return None
    best, best_score = None, 0
    for s in subsegments:
        name = _norm(s["name"])
        if not name:
            continue
        if name == want or name in want or want in name:
            return s["name"]
        score = len(set(name.split()) & set(want.split()))
        if score > best_score:
            best, best_score = s["name"], score
    return best


def counts(evidence: list[dict], subsegments: list[dict]) -> dict[str, int]:
    """Selected items per section: {"trends", "subsegment:<name>" for each fixed sub-segment, "us", "key_players"} in the
    article's order. Reads each item's `section` and `subsegment`."""
    out: dict[str, int] = {"trends": 0}
    for s in subsegments:
        out[f"subsegment:{s['name']}"] = 0
    out.update({"us": 0, "key_players": 0})
    for e in evidence:
        sec = e.get("section")
        key = f"subsegment:{e.get('subsegment', '')}" if sec == "subsegment" else sec
        if key in out:
            out[key] += 1
    return out


def minimum(key: str) -> int:
    return MINIMUMS.get(key.partition(":")[0], 0)


def thin_sections(section_counts: dict[str, int]) -> list[str]:
    """The counted sections (keys of counts()) below their minimum."""
    return [k for k, n in section_counts.items() if n < minimum(k)]


def label(key: str) -> str:
    """The editor-facing name of a counted section ("subsegment:Wealth" -> "Sub-segment Wealth")."""
    head, _, name = key.partition(":")
    return f"Sub-segment {name}" if head == "subsegment" else _LABELS.get(head, head)
