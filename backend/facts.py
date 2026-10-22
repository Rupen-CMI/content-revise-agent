"""Market-first chain, stage 1: the FACT SHEET.

One LLM call reads the confirmed CMI report page into structured facts (size, forecast, CAGR, segments, regions,
U.S., drivers, restraints, opportunities), each with the sentence it came from. Code then checks every entry against
the page text, so nothing the page does not say can survive. A field the page is silent on is left null.

Figure sourcing (the plan's "report, then evidence, then nothing"): the report's own figures are used first. When the
report lacks size, forecast or CAGR, a small web search looks for ONE outside page that states them for exactly the
same market, an LLM call reads it, and code verifies the quote and the scope before the figure enters the fact sheet,
tagged with its source. Otherwise the figure stays null and is never stated anywhere.

Imports pipeline for its shared helpers; pipeline never imports this module (main.py does the wiring).
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone

import feeds
import freshness
import llm
import prompts
import reports
import search
from pipeline import _market_core, _report_text
from pipeline_log import section

HEADLINE_KEYS = ("size", "forecast", "cagr")
STATEMENT_KEYS = ("us", "drivers", "restraints", "opportunities")
_MAX_STATEMENTS = 6               # per statement field: a few strong entries, not everything the page mentions
_MIN_QUOTE_WORDS = 5
_FIGURE_MAX_AGE_YEARS = 2         # an outside figure must be for a year within this many years of today
_FIGURE_MAX_PAGE_AGE = timedelta(days=730)
_FIGURE_HIT_LIMIT = 8
_FIGURE_PER_HOST = 2
_FIGURE_TEXT_CHARS = 1100         # per page shown to the figure-backing call
_FIGURE_FOCUS = "market size valued valuation USD billion million CAGR forecast"
# Sites that must never back a figure: CMI's own pages (its blog posts have been seen contradicting each other and
# the report), so only genuinely outside sources count.
_ALWAYS_EXCLUDED_HOSTS = ("coherentmarketinsights.com",)

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")
_NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


# ------------------------------------------------------------------------------------------ text helpers

def _norm(text: str) -> str:
    """Lowercase, quotes/dashes/punctuation to spaces, whitespace collapsed: the form in which a model's quote is
    compared with the page it claims to be copied from."""
    t = (text or "").lower()
    t = re.sub(r"[^a-z0-9%.]+", " ", t.replace("’", "'").replace("‘", "'"))
    t = re.sub(r"(?<![0-9])\.|\.(?![0-9])", " ", t)  # keep decimal points, drop sentence-ending dots
    return " ".join(t.split())


def _numbers(text: str) -> set[str]:
    """Numeric tokens with thousands commas removed and a trailing dot dropped ('561.7', '6.4', '1200')."""
    return {m.replace(",", "").rstrip(".") for m in _NUM_RE.findall(text or "")}


def _years(text: str) -> list[int]:
    return [int(y) for y in _YEAR_RE.findall(text or "")]


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _core_tokens(market_name: str) -> list[str]:
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", _market_core(market_name)) if len(w) > 2]


def _quote_ok(quote: str, source_norm: str) -> bool:
    q = _norm(quote)
    return len(q.split()) >= _MIN_QUOTE_WORDS and q in source_norm


def _is_historical(text: str, today: date) -> bool:
    """A statement is historical when the newest year it mentions has been over for more than the freshness window."""
    ys = _years(text)
    return bool(ys) and date(max(ys), 12, 31) < today - freshness.STALE_AFTER


# ------------------------------------------------------------------------------------ fact sheet validation

def _empty_fact_sheet() -> dict:
    return {
        "size": None, "forecast": None, "cagr": None, "segments": [],
        "regions": {"leader": None, "fastest": None},
        "us": [], "drivers": [], "restraints": [], "opportunities": [],
        "players": [], "gaps": list(HEADLINE_KEYS), "figure_source": None,
    }


def _headline_entry(
    raw, key: str, source_norm: str, core_tokens: list[str], strict_scope: bool, label: str, logger: logging.Logger,
) -> dict | None:
    """A size / forecast / CAGR entry survives only if its quote is real, carries every number of its value, and
    reads as a figure for the market as a whole."""
    if not isinstance(raw, dict):
        return None
    value, quote = str(raw.get("value") or "").strip(), str(raw.get("quote") or "").strip()
    when = str(raw.get("period" if key == "cagr" else "year") or "").strip()
    if not value or not quote:
        return None
    nums = _numbers(value)
    if not nums:
        logger.info(f"[facts] {label} {key} dropped: value {value!r} has no number")
        return None
    if key == "cagr" and "%" not in value and "percent" not in value.lower():
        logger.info(f"[facts] {label} cagr dropped: {value!r} is not a percentage")
        return None
    if not _quote_ok(quote, source_norm):
        logger.info(f"[facts] {label} {key} dropped: its quote is not in the source text: {quote[:90]!r}")
        return None
    missing = sorted(n for n in nums if n not in _numbers(quote))
    if missing:
        logger.info(f"[facts] {label} {key} dropped: {missing} are not in its quote")
        return None
    qn = set(_norm(quote).split())
    if strict_scope:
        scope_ok = bool(core_tokens) and all(any(w == t or _stem(w) == t for w in qn) for t in core_tokens)
    else:
        # The CMI report page is about this market, so its own sentence only has to read as a figure for a market
        # (a country's industry or a survey has no "market" in it) rather than repeat the full name.
        scope_ok = "market" in qn or "markets" in qn
    if not scope_ok:
        logger.info(f"[facts] {label} {key} dropped: its quote does not state the figure for this market: {quote[:90]!r}")
        return None
    return {"value": value, "when": when, "quote": quote}


def _statement_list(
    raw, key: str, source_norm: str, today: date, logger: logging.Logger,
) -> list[dict]:
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text, quote = " ".join(str(item.get("text") or "").split()), str(item.get("quote") or "").strip()
        if not text or not quote:
            continue
        if not _quote_ok(quote, source_norm):
            logger.info(f"[facts] {key} entry dropped: its quote is not in the report text: {quote[:90]!r}")
            continue
        stray = sorted(n for n in _numbers(text) if n not in _numbers(quote))
        if stray:
            logger.info(f"[facts] {key} entry dropped: {stray} are not in its quote: {text[:90]!r}")
            continue
        out.append({"id": f"fs.{key}.{len(out)}", "text": text, "quote": quote, "historical": _is_historical(quote, today)})
        if len(out) >= _MAX_STATEMENTS:
            break
    return out


def _region_entry(raw, which: str, source_norm: str, today: date, logger: logging.Logger) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name, quote = " ".join(str(raw.get("name") or "").split()), str(raw.get("quote") or "").strip()
    if not name or not quote:
        return None
    if not _quote_ok(quote, source_norm) or _norm(name) not in _norm(quote):
        logger.info(f"[facts] region {which} dropped: the quote is not in the report text or does not name {name!r}")
        return None
    return {
        "id": f"fs.regions.{which}", "name": name, "basis": " ".join(str(raw.get("basis") or "").split()),
        "quote": quote, "historical": _is_historical(quote, today),
    }


def validate_fact_sheet(raw: dict, report_text: str, market_name: str, logger: logging.Logger, today: date | None = None) -> dict:
    """Turn the model's raw answer into a fact sheet in which every entry has been checked against `report_text`.
    Pure (no network, no LLM): the single place the 'nothing the page does not say' rule is enforced."""
    today = today or date.today()
    raw = raw if isinstance(raw, dict) else {}
    source_norm = _norm(report_text)
    core = _core_tokens(market_name)
    fs = _empty_fact_sheet()
    for key in HEADLINE_KEYS:
        entry = _headline_entry(raw.get(key), key, source_norm, core, False, "report", logger)
        if entry:
            fs[key] = {**entry, "id": f"fs.{key}", "source": "CMI report", "stale": _headline_stale(entry["when"], today)}
    segments = []
    for seg in raw.get("segments") if isinstance(raw.get("segments"), list) else []:
        if not isinstance(seg, dict):
            continue
        stype = " ".join(str(seg.get("type") or "").split())
        items = [" ".join(str(i).split()) for i in (seg.get("items") if isinstance(seg.get("items"), list) else [])]
        kept = [i for i in items if i and _norm(i) in source_norm]
        if stype and len(kept) >= 2:
            segments.append({"id": f"fs.segments.{len(segments)}", "type": stype, "items": kept})
        elif stype:
            logger.info(f"[facts] segment {stype!r} dropped: fewer than two of its categories are in the report text")
    fs["segments"] = segments
    regions = raw.get("regions") if isinstance(raw.get("regions"), dict) else {}
    fs["regions"] = {which: _region_entry(regions.get(which), which, source_norm, today, logger) for which in ("leader", "fastest")}
    for key in STATEMENT_KEYS:
        fs[key] = _statement_list(raw.get(key), key, source_norm, today, logger)
    fs["players"] = _players(raw.get("players"), source_norm, logger)
    fs["gaps"] = headline_gaps(fs)
    return fs


def _players(raw, source_norm: str, logger: logging.Logger) -> list[str]:
    """Company names the model read off the page: each must appear in the page text (legal suffixes dropped), unique."""
    out: list[str] = []
    for name in raw if isinstance(raw, list) else []:
        clean = reports._clean_player_final(str(name or "").split(",")[0])  # a name holds no comma; "X B.V., Netherlands" is name + country
        if len(clean) < 3 or clean.lower() in (o.lower() for o in out):
            continue
        if _norm(clean) not in source_norm:
            logger.info(f"[facts] player {clean!r} dropped: not in the report text")
            continue
        out.append(clean)
    return out


def _headline_stale(when: str, today: date) -> bool:
    ys = _years(when)
    return bool(ys) and max(ys) < today.year - _FIGURE_MAX_AGE_YEARS


def headline_gaps(fs: dict) -> list[str]:
    return [k for k in HEADLINE_KEYS if not fs.get(k)]


def run_fact_sheet(market_name: str, report: reports.Report, logger: logging.Logger, today: date | None = None) -> dict:
    """CALL: read the confirmed report page into a verified fact sheet. Best-effort like the other prep calls: a model
    failure returns an empty sheet (every headline figure a gap), logged, never fatal."""
    section(logger, "CALL: FACT SHEET")
    text = _report_text(report)
    try:
        raw = llm.chat_json(
            prompts.FACT_SHEET_SYSTEM,
            prompts.FACT_SHEET_USER.format(market_name=market_name, report_text=text),
            temperature=0.1, call_name="fact_sheet", logger=logger,
        )
    except llm.LLMError:
        logger.warning("[facts] the fact sheet call failed; every headline figure is treated as a gap", exc_info=True)
        raw = {}
    fs = validate_fact_sheet(raw, text, market_name, logger, today)
    # The parser knows two page layouts; the model's names (verified against the page above) cover the rest.
    known = list(reports.key_players(report))
    fs["players"] = known + [n for n in fs["players"] if n.lower() not in (k.lower() for k in known)]
    logger.info(
        "[facts] fact sheet: "
        + ", ".join(f"{k}={'yes' if fs.get(k) else 'GAP'}" for k in HEADLINE_KEYS)
        + f"; {len(fs['segments'])} segment split(s); regions leader={'yes' if fs['regions']['leader'] else 'no'}"
        f" fastest={'yes' if fs['regions']['fastest'] else 'no'}; "
        + ", ".join(f"{k}={len(fs[k])}" for k in STATEMENT_KEYS) + f"; {len(fs['players'])} player(s)"
    )
    return fs


# ------------------------------------------------------------------------------------------ figure gap fill

def figure_gap_queries(market_name: str) -> list[str]:
    """Plain-keyword queries for pages that state the market's size and growth. No quotes and no year: the engines
    that serve sizing pages answer plain keywords best, and a year in the query acts as a filter, not a hint."""
    core = _market_core(market_name)
    return [f"{core} market size and forecast", f"{core} market CAGR USD"]


def _excluded(host: str, exclude_hosts) -> bool:
    return any(host == h or host.endswith("." + h) for h in (*_ALWAYS_EXCLUDED_HOSTS, *exclude_hosts))


def filter_figure_hits(
    hits: list[search.SearchHit], market_name: str, exclude_hosts, logger: logging.Logger, limit: int = _FIGURE_HIT_LIMIT,
) -> list[search.SearchHit]:
    """Candidate pages for the figure search. Deliberately does NOT use the market-news filters (which reject market
    sizing pages, the opposite of what is wanted here). Excludes CMI's own site and the guest article's site, needs the
    market's words in the title or snippet, and takes at most two pages per site."""
    core = _core_tokens(market_name)
    need = min(2, len(core)) if core else 0
    seen: set[str] = set()
    per_site: dict[str, int] = {}
    kept: list[search.SearchHit] = []
    for h in hits:
        if not h.title or not h.url or h.url in seen or search._is_adult(h):
            continue
        seen.add(h.url)
        if _excluded(h.host, exclude_hosts):
            logger.info(f"[figures] DROP (excluded site {h.host}): {h.title!r}")
            continue
        words = {_stem(w) for w in re.findall(r"[a-z0-9]+", f"{h.title} {h.snippet}".lower())}
        if sum(1 for t in core if t in words) < need:
            logger.info(f"[figures] DROP (market words not in title/snippet) [{h.host}]: {h.title!r}")
            continue
        site = search._site_of(h.host)
        if per_site.get(site, 0) >= _FIGURE_PER_HOST:
            continue
        per_site[site] = per_site.get(site, 0) + 1
        kept.append(h)
        if len(kept) >= limit:
            break
    logger.info(f"[figures] kept {len(kept)} candidate page(s): " + "; ".join(h.host for h in kept))
    return kept


def run_figure_gap_search(market_name: str, exclude_hosts, logger: logging.Logger) -> list[search.SearchHit]:
    """Step 2 stage, run only when the fact sheet has a headline gap: find pages that may state the market's size,
    forecast and growth rate, and read them. Uses the text-search engines only: Bing's web feed answers these
    queries with unrelated pages (messaging apps, dictionaries), as measured, so it is not used here."""
    section(logger, "FIGURE GAP SEARCH")
    raw = search.run_text_searches(figure_gap_queries(market_name), logger)
    kept = filter_figure_hits(raw, market_name, exclude_hosts, logger)
    for h in kept:
        h.scope = "figure"
    feeds.attach_excerpts(kept, market_name, logger, focus=_FIGURE_FOCUS)
    return kept


def _page_text(h: search.SearchHit) -> str:
    return " ".join(f"{h.title}. {h.snippet} {h.excerpt}".split())


def _format_figure_pages(hits: list[search.SearchHit]) -> str:
    blocks = []
    for i, h in enumerate(hits, 1):
        when = h.date.strftime("%Y-%m-%d") if h.date else "date unknown"
        blocks.append(f"[F{i}] {h.host} | {when}\n{_page_text(h)[:_FIGURE_TEXT_CHARS]}")
    return "\n\n".join(blocks)


def _figure_year_ok(entry: dict, today: date) -> bool:
    ys = _years(entry.get("when", ""))
    return bool(ys) and max(ys) >= today.year - _FIGURE_MAX_AGE_YEARS


def run_figure_backing(
    market_name: str, fs: dict, hits: list[search.SearchHit], exclude_hosts, logger: logging.Logger,
    today: date | None = None,
) -> dict:
    """CALL (only when the fact sheet has a headline gap and the gap search found pages): ask the model whether ONE page
    states the missing figures for exactly this market, then verify its answer in code. Accepted figures go into the
    fact sheet tagged with their source; anything else stays a gap. Mutates and returns `fs`."""
    today = today or date.today()
    missing = headline_gaps(fs)
    if not missing or not hits:
        return fs
    section(logger, "CALL: FIGURE BACKING")
    try:
        result = llm.chat_json(
            prompts.FIGURE_BACKING_SYSTEM,
            prompts.FIGURE_BACKING_USER.format(
                market_name=market_name, missing=", ".join(missing),
                pages=_format_figure_pages(hits), today=today.isoformat(),
            ),
            temperature=0.1, call_name="figure_backing", logger=logger,
        )
    except llm.LLMError:
        logger.warning("[figures] the figure backing call failed; the missing figures stay unstated", exc_info=True)
        return fs
    return apply_figure_source(fs, result.get("source") if isinstance(result, dict) else None, missing, hits, market_name,
                               exclude_hosts, logger, today)


def apply_figure_source(
    fs: dict, source, missing: list[str], hits: list[search.SearchHit], market_name: str, exclude_hosts,
    logger: logging.Logger, today: date,
) -> dict:
    """Verify the model's chosen source and copy over only the figures that pass every check. Pure given its inputs."""
    if not isinstance(source, dict):
        logger.info("[figures] no outside page qualified; the missing figures stay unstated")
        return fs
    m = re.fullmatch(r"F(\d+)", str(source.get("id") or "").strip().upper())
    if not m or not 1 <= int(m.group(1)) <= len(hits):
        logger.info(f"[figures] rejected: bad page id {source.get('id')!r}")
        return fs
    hit = hits[int(m.group(1)) - 1]
    if _excluded(hit.host, exclude_hosts):
        logger.info(f"[figures] rejected: {hit.host} is an excluded site")
        return fs
    if hit.date is not None:
        dated = hit.date if hit.date.tzinfo else hit.date.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - dated > _FIGURE_MAX_PAGE_AGE:
            logger.info(f"[figures] rejected: {hit.host} is dated {hit.date:%Y-%m-%d}, too old for a headline figure")
            return fs
    source_norm = _norm(_page_text(hit))
    core = _core_tokens(market_name)
    accepted: dict[str, dict] = {}
    for key in missing:
        entry = _headline_entry(source.get(key), key, source_norm, core, True, f"outside page {hit.host}", logger)
        if not entry:
            continue
        if not _figure_year_ok(entry, today):
            logger.info(f"[figures] {key} from {hit.host} dropped: year {entry['when']!r} is too old")
            continue
        accepted[key] = entry
    if not accepted:
        logger.info(f"[figures] nothing from {hit.host} passed the checks; the missing figures stay unstated")
        return fs
    name = hit.source or hit.host
    had_report = [k for k in HEADLINE_KEYS if fs.get(k)]
    for key, entry in accepted.items():
        fs[key] = {**entry, "id": f"fs.{key}", "source": name, "url": hit.url, "stale": False}
    fs["figure_source"] = {"name": name, "host": hit.host, "url": hit.url, "figures": sorted(accepted)}
    fs["gaps"] = headline_gaps(fs)
    if had_report:
        logger.warning(
            f"[figures] mixed sources: {had_report} come from the CMI report, {sorted(accepted)} from {name}; "
            "the writer names each figure's own source"
        )
    logger.info(f"[figures] accepted {sorted(accepted)} from {name} ({hit.url}); still missing: {fs['gaps'] or 'nothing'}")
    return fs


# ------------------------------------------------------------------------------------------ for later stages

def flatten_fact_sheet(fs: dict) -> list[dict]:
    """Every fact as {id, kind, text, source, historical}: the units a planner assigns to sections and a writer expands."""
    out: list[dict] = []
    for key in HEADLINE_KEYS:
        e = fs.get(key)
        if e:
            label = {"size": "Market size", "forecast": "Market forecast", "cagr": "Growth rate (CAGR)"}[key]
            out.append({"id": e["id"], "kind": key, "source": e["source"], "historical": bool(e.get("stale")),
                        "text": f"{label}: {e['value']} ({e['when']})" if e["when"] else f"{label}: {e['value']}"})
    for seg in fs.get("segments") or []:
        out.append({"id": seg["id"], "kind": "segments", "source": "CMI report", "historical": False,
                    "text": f"Segmentation {seg['type']}: " + ", ".join(seg["items"])})
    for which, r in (fs.get("regions") or {}).items():
        if r:
            out.append({"id": r["id"], "kind": "regions", "source": "CMI report", "historical": r["historical"],
                        "text": f"{'Leading' if which == 'leader' else 'Fastest-growing'} region: {r['name']}"
                                + (f" ({r['basis']})" if r["basis"] else "")})
    for key in STATEMENT_KEYS:
        for e in fs.get(key) or []:
            out.append({"id": e["id"], "kind": key, "source": "CMI report", "historical": e["historical"], "text": e["text"]})
    if fs.get("players"):
        out.append({"id": "fs.players", "kind": "players", "source": "CMI report", "historical": False,
                    "text": "Key players the report names: " + ", ".join(fs["players"])})
    return out
