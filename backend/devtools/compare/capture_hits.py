"""Freezes a fresh real search-hit set (post run_search_evidence, PRE run_select) for the
wealth-management fixture, reusing article/sponsor/report/candidates/orient already captured in
devtools/fixture.json (the original .docx is gone, but everything needed to reconstruct the
SourceArticle/Paragraph objects is already serialized in that fixture).

Freezing BEFORE select (not after) is deliberate: the whole point of this comparison is to see
which evidence each provider's own CALL 2 (select) keeps vs. drops, so each provider must run
its own real run_select against the identical hit set, not share a pre-selected evidence string.
"""
import dataclasses
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


def _serialize_hit(hit):
    d = dataclasses.asdict(hit)
    d["date"] = hit.date.isoformat() if hit.date else None
    return d


def main() -> None:
    old_fixture_path = Path(__file__).parent.parent / "fixture.json"
    old = json.loads(old_fixture_path.read_text(encoding="utf-8"))

    article = _article_from(old["article"])
    sponsor = _paragraph_from(old["sponsor"])
    report = reports.Report(**old["report"])
    candidates = [reports.CatalogEntry(**c) for c in old["candidates"]]
    orient = old["orient"]

    logger, log_path = new_run_logger("capture-hits-wealth")
    print(f"logging to {log_path}")
    print(f"article: {article.title!r}")
    print(f"report: {report.name!r}")

    print("Running real web search (fresh DDGS results)...")
    hits = pipeline.run_search_evidence(
        report.name, sponsor, candidates, logger,
        key_players=reports.key_players(report), orient=orient,
    )
    print(f"got {len(hits)} hits")
    for h in hits:
        print(f"  - {h.title!r} ({h.source or h.host})")

    out = {
        "article": dataclasses.asdict(article),
        "sponsor": dataclasses.asdict(sponsor),
        "orient": orient,
        "candidates": old["candidates"],
        "report": old["report"],
        "hits": [_serialize_hit(h) for h in hits],
        "original_filename": old["original_filename"],
    }
    out_path = Path(__file__).parent / "hits_fixture.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"saved to {out_path}")


if __name__ == "__main__":
    main()
