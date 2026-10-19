"""Replays a fixture captured by capture_fixture.py through Step 4 (write) and finalize —
real LLM call for the write step only, NO re-run of orient/catalog-match/web-search/select.
Use this to iterate quickly on WRITE_SYSTEM/WRITE_USER/the SOP text without paying for (or
waiting on) the earlier steps every time.

Usage:
    python devtools/replay_write.py [fixture.json] [--finalize]

--finalize also runs the deterministic SOP checks and writes a .docx, same as a real /finalize.
Without it, only the write call runs and the raw title/sections are printed.
"""
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import freshness
import pipeline
import reports
import search
from docx_io import Link, Paragraph, SourceArticle
from pipeline_log import new_run_logger


def _paragraph_from(d: dict) -> Paragraph:
    return Paragraph(index=d["index"], text=d["text"], links=[Link(**l) for l in d["links"]])


def _article_from(d: dict) -> SourceArticle:
    return SourceArticle(title=d["title"], paragraphs=[_paragraph_from(p) for p in d["paragraphs"]])


def _hit_from(d: dict) -> search.SearchHit:
    d = dict(d)
    d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
    return search.SearchHit(**d)


def main() -> None:
    args = sys.argv[1:]
    do_finalize = "--finalize" in args
    args = [a for a in args if a != "--finalize"]
    fixture_path = Path(args[0]) if args else Path(__file__).parent / "fixture.json"

    fixture = json.loads(fixture_path.read_text())
    article = _article_from(fixture["article"])
    sponsor = _paragraph_from(fixture["sponsor"])
    report = reports.Report(**fixture["report"])
    report.sections["live_report_page"] = freshness.strip_stale_developments(report.sections["live_report_page"])[0]
    hits = [_hit_from(h) for h in fixture["hits"]]
    candidates = [reports.CatalogEntry(**c) for c in fixture["candidates"]]
    orient = fixture["orient"]
    selection = fixture["selection"]
    evidence = fixture["evidence"]

    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")
    print(f"replaying against report={report.name!r}, {len(hits)} hits, fixture={fixture_path.name}")

    print("Step 4: write (real LLM call)...")
    written, title, sections, evidence_citations = pipeline.run_write(article, orient, sponsor, report, evidence, logger)

    print("Step 4b: revise for SEO/readability (real LLM call)...")
    title, sections, evidence_citations = pipeline.run_revise(title, sections, evidence_citations, report, logger, evidence, sponsor.text)

    print(f"\nTITLE: {title}\n")
    for s in sections:
        print(f"--- {s['heading'] or '(intro)'} [{', '.join(s['sop']) or 'untagged'}] ---")
        for p in s["paragraphs"]:
            print(p[:200] + ("..." if len(p) > 200 else ""))
        print()
    print(f"evidence citations: {len(evidence_citations)}")
    for c in evidence_citations:
        print(f"  {c['id']}: {c['paragraph'][:100]}...")

    if not do_finalize:
        return

    print("Running deterministic SOP checks + writing .docx...")
    result = pipeline.run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, fixture["original_filename"], logger,
        evidence_citations=evidence_citations,
    )
    passed = sum(c.passed for c in result.checks)
    print(f"\nSOP checks: {passed}/{len(result.checks)} passed")
    for c in result.checks:
        if not c.passed:
            print(f"  FAIL  {c.name} — {c.detail}")
    print(f"\noutput written to: {result.output_path}")


if __name__ == "__main__":
    main()
