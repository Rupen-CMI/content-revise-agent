"""Freezes the INPUTS of a run (article, sponsor, orient result, confirmed report page, market news hits)
into a JSON fixture, so every stage of the market-first chain can be built and replayed without paying for
orient / catalog match / web search again. Only stages that are not being replaced are run here: later stages
(fact sheet, search demand, plan, write, FAQ) are produced from these inputs and may be added to the same file.

The file has the same keys as capture_fixture.py's output (article, sponsor, orient, candidates, report, hits,
original_filename), so loaders written for either work on both. fixture_wealth.json already holds these keys.

Usage (from backend/):
    python devtools/capture_inputs.py <path-to-.docx> <sponsor_paragraph_index> <output.json>
"""
import dataclasses
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline
import reports
from docx_io import read_docx
from pipeline_log import new_run_logger


def _serialize_hit(hit):
    d = dataclasses.asdict(hit)
    d["date"] = hit.date.isoformat() if hit.date else None
    return d


def main() -> None:
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    docx_path = Path(sys.argv[1])
    sponsor_index = int(sys.argv[2])
    out_path = Path(sys.argv[3])

    article = read_docx(str(docx_path))
    sponsor = next(p for p in article.paragraphs if p.index == sponsor_index)

    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")
    orient = pipeline.run_orient(article, sponsor, logger)
    candidates = pipeline.run_match_catalog(orient, logger)
    chosen = candidates[0]
    print(f"confirmed market (top candidate, same as the UI default): {chosen.keyword} ({chosen.news_id})")
    report = pipeline.run_fetch_confirmed_report(chosen, logger)
    hits = pipeline.run_search_evidence(
        report.name, sponsor, candidates, logger,
        key_players=reports.key_players(report), orient=orient, segments=report.segment_terms,
    )
    fixture = {
        "article": dataclasses.asdict(article),
        "sponsor": dataclasses.asdict(sponsor),
        "orient": orient,
        "candidates": [dataclasses.asdict(c) for c in candidates],
        "report": dataclasses.asdict(report),
        "hits": [_serialize_hit(h) for h in hits],
        "original_filename": docx_path.name,
    }
    out_path.write_text(json.dumps(fixture, indent=2))
    print(f"saved {out_path} ({len(hits)} hits, report={report.name!r})")


if __name__ == "__main__":
    main()
