"""Live check of the claim-backing stream on a saved fixture (real LLM + DDGS calls; a few minutes).

Usage (from backend/): python devtools/compare/claims_check.py [fixture.json]   (default hits_fixture.json)

Step 1 (orient) runs for real to extract the claims; the claim search + filter + excerpts run for real; SELECT runs
over the fixture's frozen market hits plus the claim pool; then the evidence block is built and WRITE runs. Prints the
claims, what was found for each, what the writer was given, and the publisher-side prose so a human can judge whether
each claim ended up backed, softened, or still asserted bare.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pipeline  # noqa: E402
import reports  # noqa: E402
import run_compare as rc  # noqa: E402
from pipeline_log import new_run_logger  # noqa: E402

name = sys.argv[1] if len(sys.argv) > 1 else "hits_fixture.json"
f = json.loads((Path(__file__).parent / name).read_text(encoding="utf-8"))
article, sponsor = rc._article_from(f["article"]), rc._paragraph_from(f["sponsor"])
report = reports.Report(**f["report"])
market_hits = [rc._hit_from(h) for h in f["hits"]]
logger, log_path = new_run_logger("claims-check")
print("log:", log_path, "| fixture:", name, "| market:", report.name, "| frozen market hits:", len(market_hits))

orient = pipeline.run_orient(article, sponsor, logger)
claims = pipeline.orient_claims(orient)
print(f"\n=== {len(claims)} CLAIMS EXTRACTED BY STEP 1")
for c in claims:
    print(f"  {c['id']} [{c['kind']}] {c['claim']}\n      why: {c['why']}\n      queries: {c['queries']}")

pool = pipeline.run_claim_evidence(claims, logger)
print("\n=== CLAIM SEARCH: kept pages per claim")
for c in claims:
    hs = pool.get(c["id"]) or []
    print(f"  {c['id']}: {len(hs)} page(s)  " + " | ".join(f"{h.host}{' +text' if h.excerpt else ''}" for h in hs))

selection = pipeline.run_select(article, orient, sponsor, report, market_hits, logger, claim_hits=pool)
evidence = pipeline.run_build_evidence(selection, market_hits, logger, orient=orient)
backing = selection.get("claim_backing") or []
print(f"\n=== SELECT: {len(selection.get('evidence') or [])} market item(s), {len(backing)} claim-backing item(s) across "
      f"{len({b['claim_id'] for b in backing})}/{len(claims)} claim(s)")
for b in backing:
    print(f"  {b['claim_id']} [{b['stance']}] {b['fact'][:150]}  ({b['source']})")
print("\n=== CLAIM LINES GIVEN TO THE WRITER")
for line in evidence.splitlines():
    if line.startswith("* C") or line.startswith("- (C") or line.startswith("### ARTICLE") or line.startswith("### CLAIMS"):
        print("  " + line[:230])

written, title, sections, cites = pipeline.run_write(article, orient, sponsor, report, evidence, logger)
items = pipeline._parse_evidence(evidence)
used_pub = pipeline._evidence_used_ids("\n".join(p for s in sections if not s.get("sop") for p in s["paragraphs"]),
                                       [i for i in items if i["group"] == "article"])
print(f"\n=== WRITE: title={title!r}")
print(f"  article-evidence claims worked into publisher sections: {sorted(used_pub)} of {sorted({i['id'] for i in items if i['group'] == 'article'})}")
print(f"  remaining evidence problems: {pipeline._evidence_problems(sections, items, pipeline._min_evidence_total(len([i for i in items if i['group'] != 'article']))) or 'none'}")
print("\n=== PUBLISHER-SIDE PROSE (read each claim against it)")
for s in sections:
    if not s.get("sop"):
        print(f"\n## {s['heading'] or '(intro)'}")
        for p in s["paragraphs"]:
            print("  ", p)
Path(__file__).parent.joinpath("result_claims_check.json").write_text(
    json.dumps({"claims": claims, "claim_backing": backing, "evidence": evidence, "title": title, "sections": sections},
               indent=1, ensure_ascii=False), encoding="utf-8")
