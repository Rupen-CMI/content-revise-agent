import logging
import os
import json
import re
import sqlite3
import struct
from contextlib import closing
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import httpx
import trafilatura
from bs4 import BeautifulSoup

from config import CMI_SITE
import freshness


_null_logger = logging.getLogger("revamp.null")
_null_logger.addHandler(logging.NullHandler())
_null_logger.propagate = False

_EMBED_MODEL_NAME = "BAAI/bge-large-en-v1.5"
_embedder = None  # lazy singleton — loading the model (~7s) only happens if semantic match is used

# The shipped report catalog (built by backend/scripts/export_catalog_sqlite.py from the
# Postgres copy) — read-only, no server needed. embeddings are float32 BLOBs; cosine
# similarity is computed here instead of pgvector's <=> operator.
CATALOG_DB = Path(__file__).parent / "catalog.sqlite"

_STOPWORDS = frozenset({
    "market", "markets", "global", "industry", "the", "and", "of", "for", "by",
    "in", "on", "a", "an", "to", "with", "based",
})

# The DB snapshot lags the live site by a report edition and has shown stale, contradictory
# figures — never used as content. A live page shorter than this is treated as a failed fetch
# and raises, rather than silently substituting stale DB data.
_MIN_LIVE_CHARS = 2000
_MAX_LIVE_CHARS = 30000
_BROWSER_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


class LiveFetchError(RuntimeError):
    """The live report page could not be fetched or yielded too little text. Deliberately NOT
    caught by falling back to the database: the DB content is a stale, older report edition
    that has been found to contradict the live page's own figures, so missing data (a clear,
    visible failure) is preferable to silently writing from outdated facts."""


@dataclass(frozen=True)
class CatalogEntry:
    news_id: int
    keyword: str
    slug: str
    fmt: int
    has_summary: bool

    @property
    def url(self) -> str:
        return report_url(self.news_id, self.slug, self.fmt)


@dataclass
class Report:
    news_id: int
    name: str
    subject: str
    url: str
    source: str
    sections: dict[str, str] = field(default_factory=dict)
    segment_terms: list[str] = field(default_factory=list)
    # The report page's OWN FAQ ([{"question", "answer"}, ...], read from its FAQPage markup). Never a content
    # source (_strip_stale_faq drops that block from the text because it can carry stale figures); kept only so
    # the article's FAQ can avoid repeating the questions the report page already answers.
    page_faq: list[dict] = field(default_factory=list)


def report_url(news_id: int, slug: str, fmt: int) -> str:
    # Verified against live canonical tags: format 0 pages live under /market-insight/<slug>-<id>.
    if fmt == 0:
        return f"{CMI_SITE}/market-insight/{slug}-{news_id}"
    return f"{CMI_SITE}/industry-reports/{slug}"


def _sqlite():
    return closing(sqlite3.connect(f"file:{CATALOG_DB}?mode=ro", uri=True))


def _tokens(text: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return frozenset(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in words if w not in _STOPWORDS)


def html_to_text(html: str | None) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img"):
        img.decompose()
    for li in soup.find_all("li"):
        li.insert(0, "- ")
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if not ln:
            continue
        if out and out[-1] == "-":
            out[-1] = f"- {ln}"
        else:
            out.append(ln)
    return "\n".join(out)


@lru_cache(maxsize=1)
def catalog() -> tuple[CatalogEntry, ...]:
    try:
        with _sqlite() as conn:
            cur = conn.cursor()
            cur.execute("SELECT news_id, keyword, name, fmt, has_summary FROM reports")
            rows = cur.fetchall()
        return tuple(CatalogEntry(*row) for row in rows)
    except sqlite3.Error:
        logging.getLogger(__name__).error("failed to load the report catalog from catalog.sqlite", exc_info=True)
        raise


@lru_cache(maxsize=1)
def categories() -> tuple[str, ...]:
    try:
        with _sqlite() as conn:
            cur = conn.cursor()
            cur.execute("SELECT cat_name FROM categories")
            rows = cur.fetchall()
        return tuple(r[0] for r in rows)
    except sqlite3.Error:
        logging.getLogger(__name__).error("failed to load report categories from catalog.sqlite", exc_info=True)
        raise


@lru_cache(maxsize=1)
def _catalog_tokens() -> tuple[tuple[CatalogEntry, frozenset[str]], ...]:
    return tuple((e, _tokens(e.keyword)) for e in catalog())


# Minimum Jaccard for a text match to count. Single-shared-word overlaps ("automation"
# alone matching Healthcare/Logistics/Whistleblowing Automation, typically 0.20-0.25)
# are noise; a real title resemblance shares substantial vocabulary (>= ~0.30).
_TEXT_MIN_JACCARD = 0.30


def match_markets(names: list[str], per_name: int = 5, limit: int = 25) -> list[CatalogEntry]:
    """Fuzzy-match proposed market names to real catalog reports (token Jaccard on report keyword)."""
    best: dict[int, tuple[float, CatalogEntry]] = {}
    for group in match_markets_grouped(names, per_name=per_name):
        for score, entry in group:
            if entry.news_id not in best or score > best[entry.news_id][0]:
                best[entry.news_id] = (score, entry)
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)
    return [entry for _, entry in ranked[:limit]]


def match_markets_grouped(names: list[str], per_name: int = 3) -> list[list[tuple[float, CatalogEntry]]]:
    """Text Jaccard matches grouped per query name, groups in input (LLM-rank) order,
    each group score-desc. Lets callers rank guess #1's neighborhood above guess #2's."""
    groups: list[list[tuple[float, CatalogEntry]]] = []
    for name in names:
        query = _tokens(name)
        if not query:
            groups.append([])
            continue
        scored = [(len(query & toks) / len(query | toks), e) for e, toks in _catalog_tokens() if query & toks]
        scored = [(s, e) for s, e in scored if s >= _TEXT_MIN_JACCARD]
        scored.sort(key=lambda x: x[0], reverse=True)
        groups.append(scored[:per_name])
    return groups


def ensure_embedding_model(model_name: str = _EMBED_MODEL_NAME, say=lambda msg: None) -> bool:
    """Make sure the embedding model is in the local Hugging Face cache. Checks the cache first
    (no network) and only downloads when it is missing or incomplete. Returns True if it had to
    download. start.bat calls this on every launch so a fresh laptop gets the model up front, with
    visible progress, instead of stalling the first Step 1."""
    from huggingface_hub import snapshot_download
    try:
        path = snapshot_download(model_name, local_files_only=True)
        if any(os.path.exists(os.path.join(path, w)) for w in ("model.safetensors", "pytorch_model.bin")):
            return False
    except Exception:
        pass  # not cached yet
    # Let SentenceTransformer do the fetch rather than snapshot_download: the repo also ships
    # ONNX/OpenVINO/TF copies of the weights that snapshot_download would pull too (several GB
    # for bge-large); SentenceTransformer downloads only the files it actually loads.
    say(f"Downloading the report-matching language model ({model_name}) - one time only, this can take a few minutes...")
    from sentence_transformers import SentenceTransformer
    SentenceTransformer(model_name)
    return True


def _get_embedder():
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        # local_files_only: never ask the Hub whether there is a newer copy (on a slow connection
        # that check alone took 100+ seconds); fetch from the network only if the cache is empty.
        try:
            _embedder = SentenceTransformer(_EMBED_MODEL_NAME, local_files_only=True)
        except Exception:
            ensure_embedding_model()
            _embedder = SentenceTransformer(_EMBED_MODEL_NAME, local_files_only=True)
        # fp16 on CPU: halves the resident weights (~0.66 GB vs ~1.3 GB fp32, measured on the
        # 8 GB-RAM end-user class hardware), since only a handful of names get encoded per run.
        # Query vectors are cast back to fp32 for the cosine math against the catalog.
        try:
            _embedder.half()
        except Exception:
            logging.getLogger(__name__).warning("fp16 half() unavailable — keeping the fp32 embedder")
    return _embedder


def match_markets_semantic_grouped(names: list[str], per_name: int = 3) -> list[list[tuple[float, CatalogEntry]]]:
    """Embedding matches grouped per query name, groups in input order, similarity-desc.
    The model stays resident after the first load (fp16 -> ~half the fp32 RAM)."""
    if not names:
        return []
    model = _get_embedder()
    vecs = model.encode(names)
    return _semantic_groups(vecs, per_name)


def match_markets_semantic_all(
    names: list[str], keywords: list[str], per_name: int = 3, kw_limit: int = 12
) -> tuple[list[list[tuple[float, CatalogEntry]]], list[CatalogEntry]]:
    """One encode session for both the orient name-matching and the keyword pool, so the
    model (resident once loaded) encodes both lists in a single pass. Returns
    (name_groups, keyword_matches)."""
    names_or_kw = bool(names) or bool(keywords)
    model = _get_embedder() if names_or_kw else None
    vecs = list(model.encode(list(names) + list(keywords))) if names_or_kw else []
    name_vecs = vecs[:len(names)]
    kw_vecs = vecs[len(names):]
    name_groups = _semantic_groups(name_vecs, per_name)
    merged: dict[int, float] = {}
    for per_vector in _semantic_groups(kw_vecs if kw_vecs else [], per_name):
        for sim, entry in per_vector:
            if entry.news_id not in merged or sim > merged[entry.news_id]:
                merged[entry.news_id] = sim
    ranked = sorted(merged.items(), key=lambda x: x[1], reverse=True)[:kw_limit]
    by_id = {e.news_id: e for e in catalog()}
    keyword_matches = [by_id[nid] for nid, _ in ranked if nid in by_id]
    return name_groups, keyword_matches


def _semantic_groups(vecs, per_name: int) -> list[list[tuple[float, CatalogEntry]]]:
    """Cosine-similarity top-per_name over the catalog embeddings, grouped per vector."""
    if len(vecs) == 0:
        return []
    by_id: dict[int, CatalogEntry] = {e.news_id: e for e in catalog()}
    import numpy as np  # sentence-transformers always ships numpy as a torch dep
    try:
        with _sqlite() as conn:
            cur = conn.cursor()
            cur.execute("SELECT news_id, embedding FROM reports WHERE embedding IS NOT NULL")
            rows = cur.fetchall()
        if not rows:
            raise RuntimeError("catalog.sqlite has no stored embeddings — re-run the export script")
        emb = np.vstack([np.frombuffer(blob, dtype="<f4") for _, blob in rows])
        emb_ids = [nid for nid, _ in rows]
        emb_norm = np.linalg.norm(emb, axis=1)
        groups: list[list[tuple[float, CatalogEntry]]] = []
        for vec in vecs:
            v = np.asarray(vec, dtype=emb.dtype)
            sims = (emb @ v) / np.maximum(emb_norm * np.linalg.norm(v), 1e-9)
            top = np.argsort(-sims)[:per_name]
            groups.append([(float(sims[i]), by_id[emb_ids[i]]) for i in top if emb_ids[i] in by_id])
    except sqlite3.Error:
        logging.getLogger(__name__).error("semantic report match failed", exc_info=True)
        raise
    return groups


def match_markets_semantic(names: list[str], per_name: int = 5, limit: int = 25) -> list[CatalogEntry]:
    """Embedding-similarity match: embeds each proposed market name with the same model used to
    embed cmi_reports.keyword, and finds the nearest reports by cosine distance. Complements
    match_markets() (literal token overlap) by catching cases where the LLM's phrasing is a close
    semantic match but shares too few exact words with the real report title."""
    if not names:
        return []
    best: dict[int, float] = {}
    by_id: dict[int, CatalogEntry] = {e.news_id: e for e in catalog()}
    for group in match_markets_semantic_grouped(names, per_name=per_name):
        for similarity, entry in group:
            if entry.news_id not in best or similarity > best[entry.news_id]:
                best[entry.news_id] = similarity

    ranked = sorted(best.items(), key=lambda x: x[1], reverse=True)
    return [by_id[news_id] for news_id, _ in ranked[:limit] if news_id in by_id]


def _segment_terms(segmentation_text: str) -> list[str]:
    """Sub-segment names from the report's segmentation list, excluding the geography breakdown."""
    terms = []
    in_geography = False
    for line in segmentation_text.splitlines():
        if not line.startswith("- "):
            continue
        term = re.sub(r"\(.*?\)", "", line[2:]).strip(" :")
        lowered = term.lower()
        if any(w in lowered for w in ("region", "geograph")):
            in_geography = True
            continue
        if lowered.startswith(("by ", "global")) or " by " in lowered:
            in_geography = False
            continue
        if not in_geography and re.search(r"[a-z0-9]", lowered) and len(term) <= 60 and lowered != "others":
            terms.append(term)
    return list(dict.fromkeys(terms))


_TAXONOMY_LEAD_RE = re.compile(r"(on the basis of|segmented (into|as|by)|is (divided|classified|categorized) (into|by))", re.IGNORECASE)
_GEOGRAPHY_WORDS = ("region", "geograph", "countr")


def taxonomy_segment_terms(page_text: str) -> list[str]:
    """Sub-segment names from the live report page's own taxonomy block: a lead line such as "On the basis of type, the
    global X market is segmented into:" followed by "- Name" lines. The database `segmentation` column is empty or in an
    older shape for many reports, so the live page is the reliable source (found: a report whose DB column parsed to
    nothing left the query-writing step with "MARKET SEGMENTS: (none listed)"). Geography groups and "Other" are left out."""
    terms: list[str] = []
    lines = page_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.endswith(":") and _TAXONOMY_LEAD_RE.search(line):
            skip = any(w in line.lower() for w in _GEOGRAPHY_WORDS)
            i += 1
            while i < len(lines) and lines[i].lstrip().startswith(("- ", "• ")):
                term = re.sub(r"\(.*?\)", "", lines[i].lstrip()[2:]).strip(" :.")
                if not skip and term and len(term) <= 60 and term.lower() not in ("other", "others"):
                    terms.append(term)
                i += 1
            continue
        i += 1
    return list(dict.fromkeys(terms))


def _merge_terms(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for g in groups:
        for t in g:
            if t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return out


_FAQ_HEADING_RE = re.compile(r"frequently asked questions", re.IGNORECASE)


def _strip_stale_faq(text: str) -> str:
    """CMI report pages carry a 'Frequently Asked Questions' block that's often stale — a real
    example found live restated the market size/CAGR with DIFFERENT numbers (2025-2032, 7.5%
    CAGR) than the main body of the same page (2026-2033, 7.7% CAGR), i.e. the FAQ wasn't
    updated when the report's forecast was refreshed. Since the FAQ only restates figures the
    main body already states, dropping it loses no unique information while removing the risk
    of the writer picking the stale, contradictory numbers."""
    m = _FAQ_HEADING_RE.search(text)
    return text[:m.start()].rstrip() if m else text


_FAQ_JSONLD_RE = re.compile(
    r'"@type"\s*:\s*"Question"\s*,\s*"name"\s*:\s*"(.*?)"\s*,\s*"acceptedAnswer"\s*:\s*\{\s*"@type"\s*:\s*"Answer"\s*,\s*"text"\s*:\s*"(.*?)"\s*\}',
    re.DOTALL,
)


def extract_page_faq(page_html: str) -> list[dict]:
    """The report page's own FAQ, read from its schema.org FAQPage markup: [{"question", "answer"}, ...]. Empty when
    the page has none (or the markup changes) — callers treat that as 'nothing to avoid'."""
    out = []
    for q, a in _FAQ_JSONLD_RE.findall(page_html or ""):
        try:
            q, a = json.loads(f'"{q}"'), json.loads(f'"{a}"')
        except ValueError:
            pass
        q, a = html_to_text(q).strip(), html_to_text(a).strip()
        if q:
            out.append({"question": q, "answer": a})
    return out


def fetch_live_page(url: str, logger: logging.Logger = _null_logger) -> str:
    return fetch_live_page_with_faq(url, logger)[0]


def fetch_live_page_with_faq(url: str, logger: logging.Logger = _null_logger) -> tuple[str, list[dict]]:
    try:
        resp = httpx.get(url, headers=_BROWSER_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise LiveFetchError(f"live fetch failed for {url}: {e!r}") from e
    try:
        extracted = trafilatura.extract(resp.content, include_tables=True, favor_recall=True) or ""
    except Exception as e:
        raise LiveFetchError(f"trafilatura extraction failed for {url}: {e!r}") from e
    text = _strip_stale_faq(extracted)
    text, removed = freshness.strip_stale_developments(text)
    if removed:
        logger.info(f"[reports] dropped {len(removed)} stale dated development(s) older than {freshness.STALE_AFTER.days} days")
        for line in removed:
            logger.info(f"[reports]   stale: {line[:160]}")
    if len(text) < _MIN_LIVE_CHARS:
        raise LiveFetchError(f"live page at {url} yielded only {len(text)} chars of text (minimum {_MIN_LIVE_CHARS})")
    return text, extract_page_faq(resp.text)


def fetch_report(news_id: int, logger: logging.Logger = _null_logger) -> Report:
    """Report content from the live page only. The DB's report content fields are an older,
    unsynced edition that has been found to contradict the live page's own figures, so they are
    never used as a content source — a live-fetch failure raises LiveFetchError instead of
    silently falling back to that stale data (see LiveFetchError)."""
    try:
        with _sqlite() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT news_id, keyword, subject, name, fmt, segmentation FROM reports WHERE news_id = ?",
                (news_id,),
            )
            r = cur.fetchone()
            if r is None:
                raise LookupError(f"report {news_id} not found in catalog.sqlite")
    except sqlite3.Error as e:
        logger.error(f"[reports] database error fetching report {news_id}: {e!r}", exc_info=True)
        raise

    r = dict(zip(("newsId", "keyword", "newsSubject", "CustomName", "isNewFormat", "segmentation"), r))

    url = report_url(r["newsId"], r["CustomName"], r["isNewFormat"])
    segmentation = html_to_text(r["segmentation"])
    live, page_faq = fetch_live_page_with_faq(url, logger=logger)
    logger.info(f"[reports] the report page's own FAQ has {len(page_faq)} question(s) (used only to avoid repeating them)")

    return Report(
        news_id=r["newsId"],
        name=r["keyword"],
        subject=r["newsSubject"],
        url=url,
        source="live",
        sections={"live_report_page": live[:_MAX_LIVE_CHARS]},
        segment_terms=_merge_terms(_segment_terms(segmentation), taxonomy_segment_terms(live)),
        page_faq=page_faq,
    )


_PLAYER_SUFFIXES = frozenset({
    "inc", "ltd", "limited", "llc", "plc", "sa", "s.a", "s.a.", "ag", "nv",
    "n.v", "n.v.", "gmbh", "co", "corp", "corporation", "company",
    "holdings", "group", "headquarters", "b.v", "bv", "llp", "pvt", "pty", "oy",
})

_KEY_PLAYERS_HEADINGS = ("key players insights", "key players")


def _clean_player_name(name: str) -> str:
    words = name.strip().split()
    while words and words[-1].lower().rstrip(".,") in _PLAYER_SUFFIXES:
        words.pop()
    return " ".join(words).rstrip(".,").strip()


_BULLET_RE = re.compile(r"^\s*-\s+")
_COMPANIES_COVERED = "companies covered:"


def _clean_player_final(raw: str) -> str:
    cleaned = _clean_player_name(raw)
    if cleaned.lower().endswith(".com"):
        cleaned = cleaned[:-4].rstrip(".,").strip()
    return cleaned


def key_players(report: Report) -> list[str]:
    """Company names from the summary table's 'Companies covered' cell plus the Key Players heading list."""
    lines = (report.sections or {}).get("live_report_page", "").splitlines()
    raw: list[str] = []
    for ln in lines:
        if _COMPANIES_COVERED in ln.lower():
            idx = ln.lower().find(_COMPANIES_COVERED)
            bar = ln.find("|", idx)
            cell = ln[bar + 1:] if bar != -1 else ln[idx + len(_COMPANIES_COVERED):]
            end = cell.find("|")
            cell = cell[:end] if end != -1 else cell
            for piece in cell.split(","):
                piece = piece.strip()
                if piece.lower().startswith("and "):
                    piece = piece[4:].strip()
                if piece:
                    raw.append(piece)
            break
    start = None
    for i, ln in enumerate(lines):
        stripped = ln.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:].strip()
        if stripped.lower() in _KEY_PLAYERS_HEADINGS:
            start = i
            break
    if start is not None:
        j = start + 1
        skipped = 0
        while j < len(lines) and skipped < 2:
            s = lines[j].strip()
            if not s:
                j += 1
                continue
            if _BULLET_RE.match(lines[j]):
                break
            if len(s) >= 60:
                break
            skipped += 1
            j += 1
        while j < len(lines):
            if not lines[j].strip():
                j += 1
                continue
            m = _BULLET_RE.match(lines[j])
            if not m:
                break
            raw.append(lines[j][m.end():])
            j += 1
    names: list[str] = []
    seen: set[str] = set()
    for piece in raw:
        cleaned = _clean_player_final(piece)
        if len(cleaned) >= 3 and cleaned.lower() not in seen:
            seen.add(cleaned.lower())
            names.append(cleaned)
    return names
