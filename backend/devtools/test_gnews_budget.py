"""Offline test for the per-query read budget of the trends/sub-segment Google News hits (feeds.budget_section_hits): every query
keeps its best few on-topic hits ranked by the evidence its kind of query asks for, the ceiling deals pages out round-robin, and
the shortlist cap after it (SEARCH_KEEP_HITS / _SECTION_HIT_FLOOR) was raised in step. Run from backend/: python devtools/test_gnews_budget.py"""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import feeds  # noqa: E402
import pipeline  # noqa: E402
import search  # noqa: E402

FAILED: list[str] = []
LOG = logging.getLogger("t")


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


NOW = datetime.now(timezone.utc)


def hit(title, query, days=1):
    h = search.SearchHit(title, "", "https://x.example/" + str(abs(hash(title))), "", NOW - timedelta(days=days), is_news=True)
    h.query = query
    return h


Q_STAT, Q_INST = "skin care products sales data", "skin care brands launch new products"
stat_hits = [hit(f"Skin care trends story number {i}", Q_STAT, days=i) for i in range(1, 8)] + [hit("Skin care sales rose 9% in the quarter", Q_STAT, days=30)]
inst_hits = [hit(f"Skin care chatter number {i}", Q_INST, days=i) for i in range(1, 6)] + [hit("Beiersdorf launches a new skin care line", Q_INST, days=40)]
out = feeds.budget_section_hits(stat_hits + inst_hits, [Q_STAT, Q_INST], LOG)
by = lambda q: [h.title for h in out if h.query == q]  # noqa: E731
check("per query: each query keeps at most 3", len(by(Q_STAT)) == 3 and len(by(Q_INST)) == 3, f"{by(Q_STAT)} {by(Q_INST)}")
check("statistic query: the headline with a figure outranks newer ones without", "Skin care sales rose 9% in the quarter" in by(Q_STAT), str(by(Q_STAT)))
check("instance query: the named actor with an action verb outranks newer ones without", "Beiersdorf launches a new skin care line" in by(Q_INST), str(by(Q_INST)))
check("query kinds are read from the query words", feeds._query_kind(Q_STAT) == "stat" and feeds._query_kind(Q_INST) == "instance")

# 20 queries x 5 on-topic hits: the ceiling holds and every query still gets a share (round-robin, not the first queries only)
queries = [f"product{n} launch" for n in range(20)]
many = [hit(f"Acme launches product{n} variant {i}", q) for n, q in enumerate(queries) for i in range(5)]
out = feeds.budget_section_hits(many, queries, LOG)
check("ceiling: 48 pages in all", len(out) == 48, str(len(out)))
check("ceiling: no query is starved", all(any(h.query == q for h in out) for q in queries))
check("budget constants: 3 per query, 48 in all", feeds._SECTION_GNEWS_PER_QUERY == 3 and feeds._SECTION_GNEWS_CEILING == 48)
check("a niche market is read in full", len(feeds.budget_section_hits(stat_hits[:2], [Q_STAT], LOG)) == 2)
check("the shortlist cap before SELECT no longer re-truncates the reads",
      config.SEARCH_KEEP_HITS >= 80 and pipeline._SECTION_HIT_FLOOR >= 12, f"{config.SEARCH_KEEP_HITS} {pipeline._SECTION_HIT_FLOOR}")

print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
