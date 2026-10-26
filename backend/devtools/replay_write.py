"""Replays a fixture captured by capture_fixture.py through Step 4 (write) and finalize —
real LLM call for the write step only, NO re-run of orient/catalog-match/web-search/select.
Use this to iterate quickly on WRITE_SYSTEM/WRITE_USER/the SOP text without paying for (or
waiting on) the earlier steps every time.

Usage:
    python devtools/replay_write.py [fixture.json] [--finalize] [--v2]

--v2 replays a market-first fixture captured with capture_fixture.py --v2: PLAN -> WRITE -> FAQ
MSQs -> finalize, and finalize runs the market-first checks (checks.run_checks_v2). Without it,
the fixture replays the original chain (write + revise + old checks).

--finalize also runs the deterministic SOP checks and writes a .docx, same as a real /finalize.
Without it, only the write call runs and the raw title/sections are printed.
"""
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_chain
import checks
import faqs
import freshness
import planning
import pipeline
import reports
import search
import writer
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
    v2 = "--v2" in args
    args = [a for a in args if a not in ("--finalize", "--v2")]
    fixture_path = Path(args[0]) if args else Path(__file__).parent / "fixture.json"

    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")

    if v2:
        return _main_v2(fixture_path, do_finalize, logger)
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
    longtails = fixture.get("longtails") or []  # captured by capture_fixture.py (absent from the old fixture.json)

    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")
    print(f"replaying against report={report.name!r}, {len(hits)} hits, fixture={fixture_path.name}")

    print("Step 4: write (real LLM call)...")
    written, title, sections, evidence_citations = pipeline.run_write(article, orient, sponsor, report, evidence, logger, longtails=longtails)

    print("Step 4b: revise for SEO/readability (real LLM call)...")
    title, sections, evidence_citations = pipeline.run_revise(title, sections, evidence_citations, report, logger, evidence, sponsor.text, longtails=longtails)

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
        evidence_citations=evidence_citations, longtails=longtails,
    )
    passed = sum(c.passed for c in result.checks)
    print(f"\nSOP checks: {passed}/{len(result.checks)} passed")
    for c in result.checks:
        if not c.passed:
            print(f"  FAIL  {c.name} — {c.detail}")
    print(f"\noutput written to: {result.output_path}")


def _main_v2(fixture_path: Path, do_finalize: bool, logger) -> None:
    """Replay a market-first fixture: the chain stages come from the fixture (build_chain), PLAN and
    WRITE and FAQ answer run live, and --finalize finishes with the market-first checks."""
    chain = build_chain.load_chain(fixture_path, logger)
    article, sponsor, report = chain["article"], chain["sponsor"], chain["report"]
    fixture = chain["fixture"]
    inp = planning.build_inputs(
        report.name, chain["fact_sheet"], chain["evidence_items"], chain["demand"], chain["publisher"],
        pipeline._word_count(sponsor.text))

    print("Step 4 (market-first): plan (real LLM call, validated + repaired in code)...")
    plan = planning.run_plan(inp, logger)
    print(f"PLAN: {plan['title']} — {len(plan['sections'])} sections, {len(plan['faq'])} FAQ MSQ(s)")
    if plan.get("repairs"):
        print("  plan repairs: " + "; ".join(plan["repairs"]))

    print("Step 4 (market-first): write (real LLM call)...")
    _, title, sections, evidence_citations = writer.run_write_v2(plan, inp, sponsor, report, logger)

    print(f"\nTITLE: {title}\n")
    for s in sections:
        print(f"--- {s['heading'] or '(intro)'} [{', '.join(s['sop']) or 'untagged'}] ---")
        for p in s["paragraphs"]:
            print(p[:200] + ("..." if len(p) > 200 else ""))
        print()
    print(f"evidence citations: {[c['id'] for c in evidence_citations]}")

    print("FAQ (real LLM call)...")
    faq = faqs.run_faq(plan, inp, sections, logger)
    for qa in faq:
        print(f"  Q: {qa['question']}\n  A: {qa['answer'][:200]}...")

    if not do_finalize:
        return

    print("Running the market-first checks + writing .docx...")
    result = pipeline.run_finalize(
        article, sponsor, report, [reports.CatalogEntry(**c) for c in fixture["candidates"]],
        chain["orient"], fixture["selection"], chain["hits"],
        title, sections, fixture["evidence"], fixture["original_filename"], logger,
        evidence_citations=evidence_citations, faq=faq, plan=plan, plan_inputs=inp,
    )
    passed = sum(c.passed for c in result.checks)
    print(f"\nmarket-first checks: {passed}/{len(result.checks)} passed")
    for c in result.checks:
        if not c.passed:
            print(f"  FAIL  {c.name} — {c.detail}")
    print(f"\noutput written to: {result.output_path}")


if __name__ == "__main__":
    main()
