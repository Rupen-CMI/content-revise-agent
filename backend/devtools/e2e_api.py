"""End-to-end run of the real API (FastAPI TestClient, live LLM and web search): upload a guest .docx, then
/step1 -> /step2 -> /step3 -> /step4 -> /finalize, exactly the sequence the frontend drives. Prints what each step returned
(shapes only), the run's timings, the final title, the check results and the output file. This is the wiring test that the
offline unit tests and the plan/write replays cannot give.

Usage (from backend/): python devtools/e2e_api.py "../input_docs/Top Python Libraries for Web Scraping in AI Pipelines.docx" 61
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import main


def main_() -> None:
    docx = Path(sys.argv[1])
    sponsor_index = int(sys.argv[2])
    c = TestClient(main.app)
    t0 = time.time()

    up = c.post("/upload", files={"file": (docx.name, docx.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert up.status_code == 200, up.text
    article = up.json()["article"]
    s1 = c.post("/step1", json={"filename": docx.name, "article": article, "sponsor_index": sponsor_index})
    assert s1.status_code == 200, s1.text
    d1 = s1.json()
    token, cands = d1["run_token"], d1["candidates"]
    print(f"step1 ok ({time.time() - t0:.0f}s): top candidate {cands[0]['keyword']} ({cands[0]['news_id']})")

    s2 = c.post("/step2", json={"run_token": token, "report_id": cands[0]["news_id"]})
    assert s2.status_code == 200, s2.text
    d2 = s2.json()
    fs = d2.get("fact_sheet") or {}
    print(f"step2 ok ({time.time() - t0:.0f}s): keys {sorted(d2)}; hits {len(d2['hits'])}; "
          f"fact sheet gaps {fs.get('gaps')}; figure source {(fs.get('figure_source') or {}).get('name')}; "
          f"{len((d2.get('demand') or {}).get('questions') or [])} MSQs; {len((d2.get('publisher') or {}).get('points') or [])} publisher points")
    print("  timings:", json.dumps((d2.get("timings") or {}).get("stages") or d2.get("timings"))[:400])

    s3 = c.post("/step3", json={"run_token": token})
    assert s3.status_code == 200, s3.text
    d3 = s3.json()
    qs = (d3.get("demand") or {}).get("questions") or []
    print(f"step3 ok ({time.time() - t0:.0f}s): {len(d3['selection'].get('evidence') or [])} evidence item(s); "
          f"{sum(1 for q in qs if q.get('answerable'))}/{len(qs)} MSQs answerable")

    s4 = c.post("/step4", json={"run_token": token})
    assert s4.status_code == 200, s4.text
    d4 = s4.json()
    print(f"step4 ok ({time.time() - t0:.0f}s): title {d4['title']!r}; {len(d4['sections'])} sections; {len(d4['faq'])} FAQ entries")

    fin = c.post("/finalize", json={"run_token": token})
    assert fin.status_code == 200, fin.text
    r = fin.json()["result"]
    print(f"finalize ok ({time.time() - t0:.0f}s): output {r.get('output_filename')}")
    print("  keys:", sorted(r))
    failed = [ch for ch in r["checks"] if not ch["passed"]]
    print(f"  checks: {len(r['checks']) - len(failed)}/{len(r['checks'])} passed")
    for ch in failed:
        print(f"   FAIL {ch['name']} — {str(ch['detail'])[:200]}")
    print("  keyword counts:", json.dumps(r.get("keyword_counts"))[:300])
    print("  tertiary terms:", r.get("tertiary_terms"))
    print("  FAQ questions:", [qa["question"] for qa in r.get("faq") or []])
    run = main._RUNS[token]
    print(f"  plan repairs: {run.plan.get('repairs') if run.plan else None}")


if __name__ == "__main__":
    main_()
