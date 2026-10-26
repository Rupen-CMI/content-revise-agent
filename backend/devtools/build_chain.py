"""Builds (and caches into the fixture) every input stage of the market-first chain from a frozen-inputs fixture:
SELECT evidence, fact sheet (+ figure gap fill), search demand, publisher points. Later stages (plan, write, FAQ) read these
cached values, so replaying them costs one LLM call each. Run it once per fixture; --fresh recomputes the stages.

Shared by the replay tools: `load_chain(path, logger)` returns a dict of ready-made objects.

Usage (from backend/): python devtools/build_chain.py devtools/fixture_scraping.json [--fresh] [--no-gap]
"""
import dataclasses
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand
import facts
import pipeline
import publisher
import reports
import search
from docx_io import Link, Paragraph, SourceArticle
from pipeline_log import new_run_logger


def _paragraph(d: dict) -> Paragraph:
    return Paragraph(index=d["index"], text=d["text"], links=[Link(**l) for l in d["links"]])


def _hit(d: dict) -> search.SearchHit:
    d = dict(d)
    d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
    return search.SearchHit(**d)


def load_chain(path: Path, logger, fresh: bool = False, gap: bool = True) -> dict:
    fixture = json.loads(Path(path).read_text(encoding="utf-8"))
    article = SourceArticle(title=fixture["article"]["title"], paragraphs=[_paragraph(p) for p in fixture["article"]["paragraphs"]])
    sponsor = _paragraph(fixture["sponsor"])
    report = reports.Report(**fixture["report"])
    hits = [_hit(h) for h in fixture["hits"]]
    orient = fixture["orient"]
    changed = False
    if fresh and "plan" in fixture:
        del fixture["plan"]       # a plan cites fact ids of the inputs it was built from, so a rebuild invalidates it
        changed = True

    if fresh or "evidence" not in fixture or "selection" not in fixture:
        selection = pipeline.run_select(article, orient, sponsor, report, hits, logger)
        fixture["selection"] = selection
        fixture["evidence"] = pipeline.run_build_evidence(selection, hits, logger, orient=orient)
        changed = True
    if fresh or "fact_sheet" not in fixture:
        fs = facts.run_fact_sheet(report.name, report, logger)
        if gap and fs["gaps"]:
            hosts = [search.urlparse(l.url).netloc.lower().removeprefix("www.") for l in sponsor.links if l.is_external]
            pages = facts.run_figure_gap_search(report.name, hosts, logger)
            fs = facts.run_figure_backing(report.name, fs, pages, hosts, logger)
        fixture["fact_sheet"] = fs
        changed = True
    fs = fixture["fact_sheet"]
    if fresh or "demand" not in fixture:
        fixture["demand"] = demand.run_search_demand(report.name, fs, logger)
        changed = True
    if fresh or "publisher" not in fixture:
        fixture["publisher"] = publisher.run_publisher_points(article, sponsor, report.name, logger)
        changed = True
    if changed:
        Path(path).write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")

    evidence_items = [it for it in pipeline._parse_evidence(fixture["evidence"]) if it.get("group") != "article"]
    questions = demand.mark_answerability([dict(q) for q in fixture["demand"]["questions"]], fs, evidence_items, logger)
    demand_result = {**fixture["demand"], "questions": questions}
    return {"fixture": fixture, "article": article, "sponsor": sponsor, "report": report, "hits": hits, "orient": orient,
            "fact_sheet": fs, "evidence_items": evidence_items, "demand": demand_result, "publisher": fixture["publisher"]}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")
    for path in args:
        chain = load_chain(Path(path), logger, fresh="--fresh" in sys.argv, gap="--no-gap" not in sys.argv)
        fs = chain["fact_sheet"]
        print(f"{path}: {len(chain['evidence_items'])} evidence item(s); gaps={fs['gaps'] or 'none'}; "
              f"{len(chain['demand']['questions'])} question(s) ({sum(q['answerable'] for q in chain['demand']['questions'])} answerable); "
              f"{len(chain['publisher']['points'])} publisher point(s)")


if __name__ == "__main__":
    main()
