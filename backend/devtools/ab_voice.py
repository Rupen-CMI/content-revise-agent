"""A/B run of the writer's voice: the SAME plan and evidence written twice, once with the current prompts (plain) and once
with the style anchors (four short passages from the content team's reference article) added to the write prompt.

Everything up to the plan (upload, Step 1-3, one plan call) runs once, so the only difference between the two drafts is the
anchor block. Prints voice_metrics for both, the SOP checks that fail, and how many consecutive words each draft shares
with the anchors (copying shows up there). The drafts are written to output/ab_plain.txt and output/ab_anchors.txt.

Usage (from backend/): python devtools/ab_voice.py "../input_docs/Blog- Economic Calendar Tactic1.docx" 3
Live LLM and web search; costs about one pipeline run plus two writes.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient

import config
import faqs
import main
import planning
import prompts
import voice_metrics
import writer

OUT = Path(__file__).resolve().parent.parent / "output"


def _text(title: str, sections: list[dict], faq: list[dict]) -> str:
    lines = [title]
    for s in sections:
        lines.append("\n## " + str(s.get("heading", "")))
        lines += [str(p) for p in s["paragraphs"]]
    lines.append("\n## FAQ")
    for qa in faq:
        lines += [str(qa.get("question")), str(qa.get("answer"))]
    return "\n".join(lines)


def main_() -> None:
    docx, sponsor_index = Path(sys.argv[1]), int(sys.argv[2])
    c = TestClient(main.app)
    up = c.post("/upload", files={"file": (docx.name, docx.read_bytes(),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert up.status_code == 200, up.text
    s1 = c.post("/step1", json={"filename": docx.name, "article": up.json()["article"], "sponsor_index": sponsor_index})
    assert s1.status_code == 200, s1.text
    d1 = s1.json()
    token = d1["run_token"]
    print("market:", d1["candidates"][0]["keyword"])
    for step, body in (("/step2", {"run_token": token, "report_ids": [d1["candidates"][0]["news_id"]], "report_urls": []}),
                       ("/step3", {"run_token": token})):
        r = c.post(step, json=body)
        assert r.status_code == 200, (step, r.text)
        print(step, "ok")
    run = main._RUNS[token]
    plan = planning.run_plan(run.plan_inputs, run.logger)
    run.plan = plan
    print("plan repairs:", plan.get("repairs"))
    OUT.mkdir(exist_ok=True)
    drafts = {}
    for variant in ("plain", "anchors"):
        config.WRITE_STYLE_ANCHORS = variant == "anchors"
        _, title, sections, cites = writer.run_write_v2(plan, run.plan_inputs, run.sponsor, run.report, run.logger)
        faq = faqs.run_faq(plan, run.plan_inputs, sections, run.logger)
        run.title, run.sections, run.evidence_citations, run.faq = title, sections, cites, faq
        fin = c.post("/finalize", json={"run_token": token})
        assert fin.status_code == 200, fin.text
        res = fin.json()["result"]
        failed = [ch for ch in res["checks"] if not ch["passed"]]
        text = _text(title, sections, faq)
        (OUT / f"ab_{variant}.txt").write_text(text, encoding="utf-8")
        drafts[variant] = text
        print(f"\n===== {variant}: title {title!r}")
        voice_metrics.report(sections, variant)
        print(f"  checks {len(res['checks']) - len(failed)}/{len(res['checks'])} passed")
        for ch in failed:
            print(f"   FAIL {ch['name']} -- {str(ch['detail'])[:220]}")
        print(f"  longest run of words shared with the anchor passages: {voice_metrics.longest_shared_run(text, prompts.STYLE_ANCHORS)}")
    print("\ndrafts: output/ab_plain.txt, output/ab_anchors.txt")


if __name__ == "__main__":
    main_()
