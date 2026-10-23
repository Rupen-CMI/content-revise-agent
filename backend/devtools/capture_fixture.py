"""Runs the real pipeline ONCE, end to end (real LLM calls for orient/select, real DDGS web
search), and saves every intermediate value Step 4 (write) and finalize need into a JSON
fixture file. Use this once to capture a realistic run, then iterate on WRITE_SYSTEM/WRITE_USER
via replay_write.py without re-paying for orient/catalog-match/web-search/select on every edit.

Usage:
    python devtools/capture_fixture.py <path-to-.docx> <sponsor_paragraph_index> [output.json] [--v2]

--v2 captures the market-first chain instead (docs/restructure_plan.md §4 step 2-7): Step 2 builds
the fact sheet (+ figure gap backing), search demand and publisher points (build_chain does those
stages right after the base capture and caches them into the same fixture); Step 3's SELECT runs
with no question/claim backing. Replaying uses replay_write.py --v2.

The sponsor paragraph index is the index (0-based) of the paragraph containing the paid
backlink — same as what the UI's sponsor picker shows. If omitted, the first paragraph with an
external link is used.
"""
import dataclasses
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import build_chain
import pipeline
import reports
from docx_io import read_docx
from pipeline_log import new_run_logger


def _serialize_hit(hit):
    d = dataclasses.asdict(hit)
    d["date"] = hit.date.isoformat() if hit.date else None
    return d


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    v2 = "--v2" in sys.argv
    argv = [a for a in sys.argv[1:] if a != "--v2"]
    docx_path = Path(argv[0])
    sponsor_index = int(argv[1]) if len(argv) > 1 else None
    out_path = Path(argv[2]) if len(argv) > 2 else Path(__file__).parent / "fixture.json"

    article = read_docx(str(docx_path))
    if sponsor_index is not None:
        sponsor = next(p for p in article.paragraphs if p.index == sponsor_index)
    else:
        sponsor = article.sponsor_candidates()[0]

    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")

    print("Step 1: orient + catalog match (real LLM call)...")
    orient = pipeline.run_orient(article, sponsor, logger)
    candidates = pipeline.run_match_catalog(orient, logger)
    chosen = candidates[0]
    print(f"confirmed market (top candidate, same as UI default): {chosen.keyword} ({chosen.news_id})")

    if v2:
        print("Step 2 (market-first): fetch report, prepare search queries, web search (real calls)...")
        report = pipeline.run_fetch_confirmed_report(chosen, logger)
        prepared_queries = pipeline.prepare_search_queries(
            report.name, list(reports.key_players(report)), logger, list(report.segment_terms))
        hits = pipeline.run_search_evidence(
            report.name, sponsor, candidates, logger,
            key_players=reports.key_players(report), orient=orient, segments=report.segment_terms,
            search_queries=prepared_queries,
        )
        print("Step 3 (market-first): select evidence (real LLM call)...")
        selection = pipeline.run_select(article, orient, sponsor, report, hits, logger)
        evidence = pipeline.run_build_evidence(selection, hits, logger, orient=orient)
    else:
        print("Step 2: fetch report, draft questions + report claims, long-tail keywords, web search (real calls)...")
        report = pipeline.run_fetch_confirmed_report(chosen, logger)
        questions = pipeline.run_draft_questions(report.name, orient, report, logger).get("subheading_questions") or []
        longtails = pipeline.resolve_longtails(report.name, report, orient, logger)
        report_claims = pipeline.run_report_claims(report.name, report, logger)
        with ThreadPoolExecutor(2) as pool:
            question_future = pool.submit(pipeline.run_question_evidence, questions, logger)
            report_claim_future = pool.submit(pipeline.run_report_claim_evidence, report_claims, logger)
            hits = pipeline.run_search_evidence(
                report.name, sponsor, candidates, logger,
                key_players=reports.key_players(report), orient=orient, segments=report.segment_terms,
            )
            question_evidence = question_future.result()
            report_claim_evidence = report_claim_future.result()

        print("Step 3: select evidence (real LLM call)...")
        selection = pipeline.run_select(
            article, orient, sponsor, report, hits, logger,
            questions=questions, question_hits=question_evidence,
            report_claims=report_claims, report_claim_hits=report_claim_evidence,
        )
        evidence = pipeline.run_build_evidence(selection, hits, logger, orient=orient, questions=questions)

    fixture = {
        "article": dataclasses.asdict(article),
        "sponsor": dataclasses.asdict(sponsor),
        "orient": orient,
        "candidates": [dataclasses.asdict(c) for c in candidates],
        "report": dataclasses.asdict(report),
        "hits": [_serialize_hit(h) for h in hits],
        "selection": selection,
        "evidence": evidence,
        "original_filename": docx_path.name,
    }
    if not v2:
        fixture.update({"questions": questions, "longtails": longtails, "report_claims": report_claims})
    out_path.write_text(json.dumps(fixture, indent=2))
    print(f"fixture saved to {out_path} ({len(hits)} hits, report={report.name!r})")

    if v2:
        print("Step 2 (market-first): fact sheet, figure gap backing, search demand, publisher points...")
        chain = build_chain.load_chain(out_path, logger, fresh=True)
        fs = chain["fact_sheet"]
        print(f"  gaps={fs['gaps'] or 'none'}; {len(chain['demand']['questions'])} question(s) "
              f"({sum(q['answerable'] for q in chain['demand']['questions'])} marked after Step 3); "
              f"{len(chain['publisher']['points'])} publisher point(s)")
        print("Now use replay_write_v2.py to iterate on PLAN/WRITE/FAQ without re-running steps 1-3.")
    else:
        print("Now use replay_write.py to iterate on Step 4 (write) without re-running steps 1-3.")


if __name__ == "__main__":
    main()
