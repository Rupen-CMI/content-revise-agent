"""End-to-end run of the real API (FastAPI TestClient, live LLM and web search): upload a guest .docx, then
/step1 -> /step2 -> /step3 -> /step4 -> /finalize, exactly the sequence the frontend drives. Prints what each step returned
(shapes only), the run's timings, the final title, the check results and the output file. This is the wiring test that the
offline unit tests and the plan/write replays cannot give.

Usage (from backend/): python devtools/e2e_api.py "../input_docs/Top Python Libraries for Web Scraping in AI Pipelines.docx" 61
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

import main


def main_() -> None:
    docx = Path(sys.argv[1])
    # one paragraph number, or several separated by commas when the post carries several clients ("3,8")
    sponsor_indexes = [int(x) for x in sys.argv[2].split(",")]
    sponsor_index = sponsor_indexes[0]
    c = TestClient(main.app)
    t0 = time.time()

    up = c.post("/upload", files={"file": (docx.name, docx.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert up.status_code == 200, up.text
    article = up.json()["article"]
    # E2E_EXTRA_LINKS="idx|anchor|url;idx|anchor|url": give a paragraph one more outside link (its anchor must be words already in the
    # paragraph), to try a post that carries several clients
    for spec in filter(None, os.environ.get("E2E_EXTRA_LINKS", "").split(";")):
        idx, anchor, url = spec.split("|")
        para = next(p for p in article["paragraphs"] if p["index"] == int(idx))
        assert anchor in para["text"], f"{anchor!r} is not in paragraph {idx}"
        para["links"].append({"anchor": anchor, "url": url, "isExternal": True})
    s1 = c.post("/step1", json={"filename": docx.name, "article": article, "sponsor_index": sponsor_index,
                                         "sponsor_indexes": sponsor_indexes})
    assert s1.status_code == 200, s1.text
    d1 = s1.json()
    token, cands = d1["run_token"], d1["candidates"]
    print(f"step1 ok ({time.time() - t0:.0f}s): top candidate {cands[0]['keyword']} ({cands[0]['news_id']})")

    # E2E_MODE=external: tick nothing, so the agent finds the market's report on the internet (outside-report mode).
    mode = os.environ.get("E2E_MODE", "cmi")
    external = mode in ("external", "picker")
    print("  suggested markets:", json.dumps(d1["orient"].get("suggested_markets")))
    print("  CMI candidates by tier:", [(c_["tier"], c_["keyword"]) for c_ in cands[:12]])
    body = {"run_token": token, "report_ids": [] if external else [cands[0]["news_id"]], "report_urls": []}
    if mode == "picker":
        # the new flow: tick the agent's Tier 1 market, look at the competitor reports found, tick two of them
        market = (d1["orient"]["suggested_markets"].get("tier1") or [""])[0]
        fr = c.post("/step2/find_reports", json={"run_token": token, "markets": [market]})
        assert fr.status_code == 200, fr.text
        found = fr.json()["reports"]
        print(f"find_reports ok ({time.time() - t0:.0f}s): {len(found)} candidate(s) for {market!r}:", [(f_["host"], f_["title"][:50]) for f_ in found])
        body.update({"markets": [market], "report_urls": [f_["url"] for f_ in found[:2]], "url_markets": {f_["url"]: f_["market"] for f_ in found[:2]}})
    s2 = c.post("/step2", json=body)
    assert s2.status_code == 200, s2.text
    d2 = s2.json()
    fs = d2.get("fact_sheet") or {}
    print(f"step2 ok ({time.time() - t0:.0f}s): keys {sorted(d2)}; hits {len(d2['hits'])}; "
          f"fact sheet gaps {fs.get('gaps')}; figure source {(fs.get('figure_source') or {}).get('name')}; "
          f"{len((d2.get('demand') or {}).get('questions') or [])} MSQs; {len((d2.get('publisher') or {}).get('points') or [])} publisher points")
    print("  timings:", json.dumps((d2.get("timings") or {}).get("stages") or d2.get("timings"))[:400])
    for cl in d2.get("clients") or []:
        b = cl.get("brief") or {}
        print(f"  client {cl['id']} {cl['host']}: links {[l['anchor'] for l in cl['links']]}; offer: {b.get('offer')!r}; "
              f"anchor {b.get('anchor_fit')}; market {b.get('market_fit')}; notes {cl.get('notes')}")

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
    opening = d4["sections"][0]["paragraphs"]
    print("  OPENING (links shown as [words -> url]):")
    for para in opening:
        print("   ", "".join(f"[{seg['text']} -> {seg['url']}]" if seg.get("url") else seg["text"] for seg in para))

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
    print(f"  report mode: {run.report_mode}; warnings: {run.warnings}")
    dump = Path(__file__).resolve().parent.parent / "output" / f"e2e_{mode}.txt"
    dump.parent.mkdir(exist_ok=True)
    lines = [r.get("title", "")]
    for sec in r.get("sections") or []:
        lines.append("\n## " + str(sec.get("heading", "")))
        lines += [str(p.get("text") if isinstance(p, dict) else p) for p in sec.get("paragraphs") or []]
    dump.write_text("\n".join(lines), encoding="utf-8")
    print(f"  draft text written to {dump}")
    print(f"  plan repairs: {run.plan.get('repairs') if run.plan else None}")


if __name__ == "__main__":
    main_()
