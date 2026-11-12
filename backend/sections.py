"""The Evidence step's section layer: which sections the article's evidence serves, what each section searches for, and how
the hits and the selected items are bookkept per section.

Everything here is pure code on the confirmed fact sheet (no model, no network): the section search plan (trends from the
report's top drivers and opportunities, the two fixed sub-segments, the reasons the leaders lead, the report's own U.S.
statements, the key players, the market as a whole), the grouped query list, the section tags every hit and every selected
item carries, the per-section minimums and the count that decides which section is thin.

Imports only search (never pipeline, facts or planning), so all three can import it.
"""
import hashlib
import re

import search

# The sections whose queries run (a hit found by none of the specific ones is "market"), and the sections SELECT may assign.
QUERY_SECTIONS = ("trends", "subsegment", "segmentation", "us", "key_players", "market")
EVIDENCE_SECTIONS = ("trends", "subsegment", "segmentation", "us", "key_players")

# Fewest selected items a section should have; below it the section gets ONE top-up search (subsegment: per sub-segment).
MINIMUMS = {"trends": 6, "subsegment": 1, "us": 2, "key_players": 3}

TREND_TARGETS = 5
_SUBSEGMENTS = 2
_QUERIES_PER_SECTION_MAX = 8
_TARGET_CHARS = 220

_REGION_SPLIT_RE = re.compile(r"region|geograph|countr|area|location", re.I)
_SECTION_ALIASES = {
    "trend": "trends", "trends": "trends", "subsegment": "subsegment", "subsegments": "subsegment",
    "sub_segment": "subsegment", "sub_segments": "subsegment", "segmentation": "segmentation", "segment": "segmentation",
    "segments": "segmentation", "us": "us", "u_s": "us", "country_us": "us", "united_states": "us",
    "key_players": "key_players", "players": "key_players", "keyplayers": "key_players", "market": "market",
    "general": "market",
}
# a section SELECT may name maps to the evidence tag the rest of the chain already reads ("players" for key_players)
_TAG_OF_SECTION = {"trends": "trends", "us": "us", "key_players": "players", "segmentation": "segmentation"}
_SECTION_OF_TAG = {"trends": "trends", "us": "us", "players": "key_players", "key_players": "key_players",
                   "segmentation": "segmentation"}
_LABELS = {"trends": "Trends", "segmentation": "Segmentation", "us": "U.S.", "key_players": "Key players"}


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


def choose_subsegments(fs: dict) -> list[dict]:
    """The two sub-segments, fixed before any news is searched: the leading category of two different splits. Splits that are
    not regions come first (regions are not sub-segments); among them a split whose leader the report states, or its figures
    prove, comes before one where only the largest stated share shows a leader, and that before one with no leader at all
    (its first category). [{"split", "name", "basis": "dominant"|"share"|"first", "id"}], at most two, fewer when the
    report has fewer splits."""
    ranked = []
    for idx, seg in enumerate(fs.get("segments") or []):
        if not seg.get("items"):
            continue
        dom = (seg.get("dominant") or {}).get("name")
        share = _share_leader(seg)
        name, basis, rank = (dom, "dominant", 0) if dom else (share, "share", 1) if share else (seg["items"][0], "first", 2)
        ranked.append(((is_region_split(seg["type"]), rank, idx), {"split": seg["type"], "name": name, "basis": basis, "id": seg.get("id", "")}))
    return [pick for _, pick in sorted(ranked, key=lambda r: r[0])[:_SUBSEGMENTS]]


# ------------------------------------------------------------------------------------------- the search plan

def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= _TARGET_CHARS else text[:_TARGET_CHARS].rsplit(" ", 1)[0]


def section_search_plan(fs: dict, report, key_players=(), subsegments: list[dict] | None = None) -> dict[str, list[dict]]:
    """What each section's search looks for, from the confirmed fact sheet alone. Every target is {"text", ...}:
      trends        the top drivers, then opportunities (3 to 5 targets, one query each);
      subsegment    the two fixed sub-segments (subsegments, or choose_subsegments);
      segmentation  the reason each split's leader leads (the report's segment drivers) where it gives one;
      us            the report's own U.S. statements, as seeds;
      key_players   the cleaned player names;
      market        the market as a whole (general recent news)."""
    market = getattr(report, "name", report) or ""
    drivers = [e for e in fs.get("drivers") or [] if e.get("text")]
    opportunities = [e for e in fs.get("opportunities") or [] if e.get("text")]
    ordered = [("drivers", e) for e in drivers[:3]] + [("opportunities", e) for e in opportunities[:2]]
    spare = [("drivers", e) for e in drivers[3:]] + [("opportunities", e) for e in opportunities[2:]]
    ordered += spare[:max(0, TREND_TARGETS - len(ordered))]
    picks = subsegments if subsegments is not None else choose_subsegments(fs)
    reasons = [e for e in fs.get("segment_drivers") or [] if e.get("text") and e.get("split")]
    segmentation = []
    for seg in fs.get("segments") or []:
        leader = (seg.get("dominant") or {}).get("name")
        mine = [e for e in reasons if _norm(e["split"]) == _norm(seg["type"])]
        why = next((e for e in mine if leader and _norm(e.get("name", "")) == _norm(leader)), None) or (mine[0] if mine else None)
        if why:
            segmentation.append({"split": seg["type"], "name": why.get("name") or leader or "",
                                 "text": f"Why {why.get('name') or leader or 'the leading category'} leads {seg['type']}: {_short(why['text'])}"})
    return {
        "trends": [{"kind": kind, "text": _short(e["text"])} for kind, e in ordered],
        "subsegment": [{"split": p["split"], "name": p["name"], "basis": p.get("basis", ""), "text": f"{p['name']} ({p['split']})"} for p in picks],
        "segmentation": segmentation,
        "us": [{"text": _short(e["text"])} for e in (fs.get("us") or [])[:3] if e.get("text")],
        "key_players": [{"text": n} for n in search.clean_player_names(list(key_players))],
        "market": [{"text": market}],
    }


def format_targets(plan: dict) -> str:
    """The plan as the query-writing prompt reads it: one block per section, one line per target."""
    def lines(key: str, empty: str) -> str:
        return "\n".join(f"- {t['text']}" for t in plan.get(key) or []) or f"- {empty}"

    return "\n".join([
        "TRENDS (one query per target; each target is a driver or opportunity the news should show happening):",
        lines("trends", "(no targets: ask what is changing in the market itself)"),
        "SUB-SEGMENTS (two queries for each: the market's anchor plus the sub-segment's name, then a development in it):",
        lines("subsegment", "(none fixed)"),
        "SEGMENTATION (one query per target: news showing why that category leads its split):",
        lines("segmentation", "(none: ask about demand within one named segment)"),
        "U.S. (two queries; the targets are what the report itself says about the United States):",
        lines("us", "(none: ask about the market in the United States)"),
        "KEY PLAYERS (two queries about the companies' own moves; code adds one query per company):",
        lines("key_players", "(none known)"),
        "MARKET (two general queries on recent developments in the market as a whole):",
        lines("market", "(the market)"),
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
    """The grouped queries when the model gives none: the deterministic market templates, plus short anchored queries from the
    plan's own targets."""
    grouped: dict[str, list[str]] = {s: [] for s in QUERY_SECTIONS}
    grouped["market"] = list(market_templates)
    grouped["trends"] = [f'"{core}" {w}' for w in ("adoption", "investment", "partnership")]
    grouped["subsegment"] = [f"{core} {t['name']}" for t in plan.get("subsegment") or []]
    grouped["segmentation"] = [f"{core} {t['name']}" for t in plan.get("segmentation") or [] if t.get("name")]
    grouped["us"] = [f'"{core}" United States']
    return grouped


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
    """The single tag a selected item carries: "trends", "us", "players", "segmentation" or "subsegment:<category>"."""
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
    """Selected items per section: {"trends", "subsegment:<name>" for each fixed sub-segment, "segmentation", "us",
    "key_players"} in the article's order. Reads each item's `section` and `subsegment`."""
    out: dict[str, int] = {"trends": 0}
    for s in subsegments:
        out[f"subsegment:{s['name']}"] = 0
    out.update({"segmentation": 0, "us": 0, "key_players": 0})
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
