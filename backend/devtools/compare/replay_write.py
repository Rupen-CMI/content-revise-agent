"""Replays CALL 3 (write) + CALL 4 (revise) against a fixture's OWN FROZEN evidence selection —
unlike run_compare.py, this does NOT re-run CALL 2 (select), so the evidence pool is held
perfectly constant across every rerun. Use this to iterate on WRITE_SYSTEM/WRITE_USER/REVISE_*
as many times as you want without CALL 2 picking different evidence (or paying for it) each time.

Usage:
    python devtools/compare/replay_write.py <label> [--fixture=mkt_fixture.json]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pipeline
import reports
from docx_io import Link, Paragraph, SourceArticle
from pipeline_log import new_run_logger


def _paragraph_from(d: dict) -> Paragraph:
    return Paragraph(index=d["index"], text=d["text"], links=[Link(**l) for l in d["links"]])


def _article_from(d: dict) -> SourceArticle:
    return SourceArticle(title=d["title"], paragraphs=[_paragraph_from(p) for p in d["paragraphs"]])


def main() -> None:
    args = sys.argv[1:]
    label = args[0] if args else "run"
    fixture_name = next((a.split("=", 1)[1] for a in args if a.startswith("--fixture=")), "mkt_fixture.json")
    fixture_path = Path(__file__).parent / fixture_name
    f = json.loads(fixture_path.read_text(encoding="utf-8"))

    article = _article_from(f["article"])
    sponsor = _paragraph_from(f["sponsor"])
    report = reports.Report(**f["report"])
    candidates = [reports.CatalogEntry(**c) for c in f["candidates"]]
    orient = f["orient"]
    selection = f["selection"]
    evidence = f["evidence"]  # frozen output of run_build_evidence — never recomputed here
    hits = f["hits"]  # only needed by run_finalize's soft "evidence actually used" check

    logger, log_path = new_run_logger(f"replay-write-{label}")
    print(f"logging to {log_path}")
    print(f"frozen evidence: {len(selection.get('evidence') or [])} item(s) from {fixture_name}, report={report.name!r}")

    print("\nCALL 3: write (real LLM call)...")
    written, title, sections, evidence_citations = pipeline.run_write(
        article, orient, sponsor, report, evidence, logger
    )

    print("CALL 4: revise (real LLM call)...")
    title, sections, evidence_citations = pipeline.run_revise(
        title, sections, evidence_citations, report, logger, evidence, sponsor.text
    )

    print(f"\nORIGINAL TITLE: {article.title}")
    print(f"TITLE: {title}\n")
    print("OUTLINE (heading | sop tags | words):")
    for s in sections:
        w = sum(len(p.split()) for p in s["paragraphs"])
        print(f"  {(s['heading'] or '(intro)')[:60]:60s} | {','.join(s['sop']) or '-':15s} | {w}")
    print()
    for s in sections:
        print(f"--- {s['heading'] or '(intro)'} [{', '.join(s['sop']) or 'untagged'}] ---")
        for p in s["paragraphs"]:
            print(p)
        print()
    print(f"evidence citations: {len(evidence_citations)}")
    for c in evidence_citations:
        print(f"  {c['id']}: {c['paragraph'][:100]}...")

    print("\nRunning deterministic SOP checks + writing .docx...")
    import search
    from datetime import datetime

    def _hit_from(d: dict):
        d = dict(d)
        d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
        return search.SearchHit(**d)

    hit_objs = [_hit_from(h) for h in hits]
    result = pipeline.run_finalize(
        article, sponsor, report, candidates, orient, selection, hit_objs,
        title, sections, evidence, f["original_filename"], logger,
        evidence_citations=evidence_citations,
    )
    passed = sum(c.passed for c in result.checks)
    print(f"\nSOP checks: {passed}/{len(result.checks)} passed")
    for c in result.checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  {mark}  {c.name} - {c.detail}")
    print(f"\noutput written to: {result.output_path}")

    summary_path = Path(__file__).parent / f"result_{label}.json"
    summary_path.write_text(json.dumps({
        "label": label,
        "title": title,
        "word_count": sum(len(p.split()) for s in sections for p in s["paragraphs"]),
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks],
    }, indent=2), encoding="utf-8")
    print(f"summary saved to {summary_path}")


if __name__ == "__main__":
    main()
