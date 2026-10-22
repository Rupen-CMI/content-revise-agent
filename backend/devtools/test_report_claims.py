"""Offline test for report-claim evidence (no network, no LLM calls).

Run from backend/: python devtools/test_report_claims.py

Covers: the report-claim pool flattening/formatting helpers, SELECT's report_claim_backing
sanitising (pipeline._normalize_report_claim_backing), and the folding of that backing into the
trends / us / context evidence groups in run_build_evidence — not the LLM extraction
(pipeline.run_report_claims) or the web search (pipeline.run_report_claim_evidence), which need a
model/the network and are best-effort by design.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
import prompts  # noqa: E402
import search  # noqa: E402

LOG = logging.getLogger("test_report_claims")
LOG.addHandler(logging.NullHandler())
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def hit(title: str, url: str, snippet: str = "") -> search.SearchHit:
    h = search.SearchHit(title=title, snippet=snippet, url=url, source="", date=None)
    h.source = h.host
    return h


claims = [
    {"id": "RC1", "claim": "Edge deployment is reshaping Zzz Widget procurement", "section": "trends", "queries": ["q"]},
    {"id": "RC2", "claim": "Industrial widgets lead Zzz Widget sub-segment adoption", "section": "subsegment", "queries": ["q"]},
    {"id": "RC3", "claim": "U.S. federal incentives are pulling Zzz Widget demand", "section": "country_us", "queries": ["q"]},
]
hits = [
    hit("Edge widgets change buying", "https://a.example.org/1", "Edge deployment now drives 41 percent of orders"),
    hit("Industrial widget adoption", "https://b.example.org/2", "Industrial users adopt fastest"),
    hit("U.S. incentives for widgets", "https://c.example.org/3", "Federal incentives expanded this year"),
    hit("Zzz Widget Market size to reach 9.9 billion", "https://d.example.org/4", "The market will reach USD 9.9 billion by 2032"),
]
pool = {"RC1": hits[:1], "RC2": hits[1:2], "RC3": hits[2:3]}

# ------------------------------------------------------------------ format / flatten
flat = pipeline._flatten_report_claim_pool(claims, pool)
check("flatten: PR-numbers are sequential across claims", [p for p, _, _ in flat] == ["PR1", "PR2", "PR3"], str(flat))
check("flatten: each entry keeps its claim id", [c for _, c, _ in flat] == ["RC1", "RC2", "RC3"])
check("flatten: no claims gives []", pipeline._flatten_report_claim_pool([], pool) == [])
block = pipeline._format_report_claims_for_select(claims)
check("format: claims block names id and section", "RC1 [trends]" in block and "RC3 [country_us]" in block, block)
check("format: empty claims gives a placeholder", "none" in pipeline._format_report_claims_for_select([]).lower())
pool_block = pipeline._format_report_claim_pool_for_select(flat, claims)
check("format: pool block tags each entry with its claim", "(for: RC1)" in pool_block and "(for: RC3)" in pool_block)
check("format: claims with no pool gives the 'none found' placeholder",
      "none" in pipeline._format_report_claim_pool_for_select([], claims).lower())

# ------------------------------------------------------------------ SELECT report_claim_backing sanitising
selection = {"report_claim_backing": [
    {"claim_id": "RC1", "id": "PR1", "fact": "Edge deployment now drives 41% of orders."},
    {"claim_id": "RC2", "id": "PR2", "fact": "Industrial users adopt fastest."},
    {"claim_id": "RC1", "id": "PR9", "fact": "Invented pool id."},
    {"claim_id": "RC3", "id": "PR3", "fact": "Federal incentives expanded; 77 states joined."},
    "not a dict",
]}
pipeline._normalize_report_claim_backing(selection, claims, flat, LOG)
kept = selection["report_claim_backing"]
ids = [b["id"] for b in kept]
check("backing: real entries kept, invented pool id dropped", ids == ["PR1", "PR2"], str(ids))
check("backing: an invented figure (77) not in the source is dropped", "PR3" not in ids, str(ids))
check("backing: entries carry no stance", all("stance" not in b for b in kept))
check("backing: entries carry their claim's section",
      {b["id"]: b["section"] for b in kept} == {"PR1": "trends", "PR2": "subsegment"}, str(kept))

# the claim id comes from the pool tag even when SELECT mistypes it
mis = {"report_claim_backing": [{"claim_id": "RC3", "id": "PR1", "fact": "Edge deployment now drives 41% of orders."}]}
pipeline._normalize_report_claim_backing(mis, claims, flat, LOG)
check("backing: a mislabelled claim id is corrected from the pool tag",
      [b["claim_id"] for b in mis["report_claim_backing"]] == ["RC1"], str(mis["report_claim_backing"]))

# market sizing / forecast facts are rejected (a rival figure could contradict the report's own)
sizing = {"report_claim_backing": [{"claim_id": "RC1", "id": "PR4", "fact": "The market will reach USD 9.9 billion by 2032."}]}
pipeline._normalize_report_claim_backing(sizing, claims, [("PR4", "RC1", hits[3])], LOG)
check("backing: a market-sizing fact is dropped", sizing["report_claim_backing"] == [], str(sizing["report_claim_backing"]))

# at most three entries per claim
many_hits = [hit(f"Edge widgets {i}", f"https://e{i}.example.org/", "Edge deployment is spreading") for i in range(5)]
many_flat = [(f"PR{i + 1}", "RC1", h) for i, h in enumerate(many_hits)]
many = {"report_claim_backing": [{"claim_id": "RC1", "id": f"PR{i + 1}", "fact": f"Edge deployment is spreading, point {i}."} for i in range(5)]}
pipeline._normalize_report_claim_backing(many, claims, many_flat, LOG)
check("backing: at most three entries per claim", len(many["report_claim_backing"]) == 3, str(len(many["report_claim_backing"])))

# ------------------------------------------------------------------ run_build_evidence folding
built = pipeline.run_build_evidence({"evidence": [], "report_claim_backing": kept}, [], LOG, orient=None)
check("build: trends backing lands in the trends group", "Edge deployment now drives 41%" in built)
check("build: subsegment backing lands in the context group", "Industrial users adopt fastest" in built)
check("build: backing keeps the claim-id tag", "(RC1)" in built and "(RC2)" in built)

# ------------------------------------------------------------------ prompt formatting sanity
text = prompts.REPORT_CLAIMS_USER.format(market_name="Zzz Widget Market", report_text="x", claim_count=8)
check("prompt: REPORT_CLAIMS_USER formats and carries the count", "up to 8" in text)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all report-claim tests passed")
