"""Measures the LAYOUT of a real CALL 3 (write) result: section order, topic switches, the
publisher/market word split (the sponsor placeholder counted as its real words, which belong to
the publisher), the total word count, whether the one-retry fired, and the rendered text of the
sections around the P -> M bridge (the last publisher section and the report_context section).

Usage (run from backend/):
    python devtools/compare/layout_check.py                       # mkt, ecal and hits fixtures
    python devtools/compare/layout_check.py mkt_fixture.json
    python devtools/compare/layout_check.py --before              # the saved pre-change drafts
    python devtools/compare/layout_check.py --saved result_evidence-v1.json --sponsor hits_fixture.json

A fixture normally carries a frozen `evidence` string, which is passed straight to run_write (no
network, one real LLM write call). `hits_fixture.json` predates the frozen-evidence format: it has
hits but no `evidence`/`selection`, so this script runs one SELECT call over its frozen hits to
build the evidence string first. Other fixtures never make a select call.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

import checks  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import search  # noqa: E402
from docx_io import Link, Paragraph, SourceArticle  # noqa: E402
from pipeline_log import new_run_logger  # noqa: E402

FIXTURES = ("mkt_fixture.json", "ecal_fixture.json", "hits_fixture.json")
TOKEN = prompts.SPONSOR_TOKEN

# Pre-change drafts saved in this directory: (result file, the fixture whose sponsor prose they
# carry). Used by --before to show the same measurements on the old interleaved layout.
SAVED = (
    ("result_evidence-v1.json", "hits_fixture.json"),
    ("result_evidence-v1-mkt.json", "mkt_fixture.json"),
)


def _quiet_console() -> None:
    """new_run_logger attaches an INFO stream handler to the root logger; the full log still goes to
    the run's file, but this script's own summary should not be buried in prompt/response dumps."""
    import logging

    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            handler.setLevel(logging.WARNING)


def _paragraph_from(d: dict) -> Paragraph:
    return Paragraph(index=d["index"], text=d["text"], links=[Link(**l) for l in d["links"]])


def _article_from(d: dict) -> SourceArticle:
    return SourceArticle(title=d["title"], paragraphs=[_paragraph_from(p) for p in d["paragraphs"]])


def _hit_from(d: dict):
    from datetime import datetime

    d = dict(d)
    d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
    return search.SearchHit(**d)


def _sponsor_words(p: str, sponsor_text: str) -> int:
    """A paragraph's words, with the placeholder counted as the retained paragraph's real words."""
    text = checks._sponsor_replacement(p, sponsor_text) if sponsor_text else p
    return checks._words(text.replace(TOKEN, " "))


def measure(sections: list[dict], sponsor_text: str) -> dict:
    """The layout numbers shared by live runs, saved drafts and the deterministic checks."""
    classes = ["M" if s.get("sop") else "P" for s in sections]
    switches = sum(1 for a, b in zip(classes, classes[1:]) if a != b)
    parts: list[str] = []
    publisher = market = 0
    for s, cls in zip(sections, classes):
        heading = (s.get("heading") or "").strip()
        label = heading or "intro"
        if cls == "M":
            label = s["sop"][0]  # the SOP tag is more informative than the bespoke heading
        parts.append(f"{label} {cls}")
        words = sum(_sponsor_words(p, sponsor_text) for p in s.get("paragraphs") or [])
        if cls == "P":
            publisher += words
        else:
            market += words
    total = publisher + market
    return {
        "order": " | ".join(parts),
        "switches": switches,
        "publisher": publisher,
        "market": market,
        "total": total,
        "publisher_pct": (publisher / total * 100) if total else 0.0,
        "sections": sections,
    }


def _bridge_sections(sections: list[dict]) -> tuple[dict | None, dict | None]:
    """(last publisher section before the first market section, report_context section)."""
    first_market = next((i for i, s in enumerate(sections) if s.get("sop")), None)
    last_publisher = None
    if first_market is not None:
        last_publisher = next((s for s in reversed(sections[:first_market]) if not s.get("sop")), None)
    context = next((s for s in sections if "report_context" in s.get("sop", [])), None)
    return last_publisher, context


def _print_report(name: str, sponsor_text: str, result: dict, retry: str) -> None:
    print(f"\n{'=' * 22} {name} {'=' * 22}")
    print(f"SECTIONS: {result['order']}")
    print(f"TOPIC SWITCHES: {result['switches']}")
    print(
        f"SPLIT: publisher {result['publisher_pct']:.0f}% ({result['publisher']} words) / "
        f"market {100 - result['publisher_pct']:.0f}% ({result['market']} words) | "
        f"total {result['total']} words"
    )
    print(f"RETRY FIRED: {retry}")
    print("SECTION WORDS: " + " | ".join(
        f"{(s.get('heading') or 'intro').strip() or 'intro'} {sum(_sponsor_words(p, sponsor_text) for p in s.get('paragraphs') or [])}"
        for s in result["sections"]
    ))
    last_publisher, context = _bridge_sections(result["sections"])
    print(f"\nBRIDGE — last publisher section ({last_publisher.get('heading') if last_publisher else 'n/a'}):")
    for p in (last_publisher or {}).get("paragraphs") or []:
        print(checks._sponsor_replacement(p, sponsor_text) if sponsor_text else p)
    print(f"\nBRIDGE — report_context section ({context.get('heading') if context else 'n/a'}):")
    for p in (context or {}).get("paragraphs") or []:
        print(checks._sponsor_replacement(p, sponsor_text) if sponsor_text else p)
    print()


def run_fixture(fixture_name: str) -> None:
    path = HERE / fixture_name
    f = json.loads(path.read_text(encoding="utf-8"))
    article = _article_from(f["article"])
    sponsor = _paragraph_from(f["sponsor"])
    report = reports.Report(**f["report"])
    orient = f["orient"]

    logger, log_path = new_run_logger(f"layout-{path.stem}")
    _quiet_console()
    print(f"[{fixture_name}] report: {report.name}; logging to {log_path}")

    evidence = f.get("evidence")
    if not evidence:
        hits = [_hit_from(h) for h in f["hits"]]
        sponsor_hosts = tuple(pipeline._host(l.url) for l in sponsor.links if l.is_external)
        hits = search.filter_hits(
            hits, [report.name], sponsor_hosts, key_players=reports.key_players(report), logger=logger,
        )
        if f.get("selection"):
            print(f"[{fixture_name}] no frozen evidence string — rebuilding it from the frozen selection")
            selection = f["selection"]
        else:
            print(f"[{fixture_name}] no frozen evidence/selection — running one SELECT call over {len(hits)} hits")
            selection = pipeline.run_select(article, orient, sponsor, report, hits, logger)
        evidence = pipeline.run_build_evidence(selection, hits, logger)

    written, title, sections, evidence_citations = pipeline.run_write(
        article, orient, sponsor, report, evidence, logger,
    )
    retry = "first draft has problems" in log_path.read_text(encoding="utf-8", errors="replace")
    result = measure(sections, sponsor.text)
    _print_report(f"{fixture_name} (live)", sponsor.text, result, "yes" if retry else "no")
    print(f"TITLE: {title}")
    print(f"evidence citations: {len(evidence_citations)}")
    out = HERE / f"result_layout-{path.stem}.json"
    out.write_text(json.dumps({
        "fixture": fixture_name, "title": title, "sections": sections,
        "evidence_citations": evidence_citations,
    }, indent=2), encoding="utf-8")
    print(f"saved: {out}")

    report_text = pipeline._report_text(report)
    check_results = checks.run_checks(
        title, sections, report_text + "\n" + (evidence or ""), [],
        sponsor_text=sponsor.text, original_body=article.body, report_name=report.name,
        report_text=report_text,
    )
    failed = [r for r in check_results if not r.passed]
    print(f"SOP CHECKS: {len(check_results) - len(failed)}/{len(check_results)} passed")
    for r in failed:
        print(f"  FAIL  {r.name} — {r.detail}")


def run_saved(result_name: str, sponsor_fixture: str | None = None) -> None:
    saved = json.loads((HERE / result_name).read_text(encoding="utf-8"))
    if sponsor_fixture is None:
        sponsor_fixture = next((s for r, s in SAVED if r == result_name), "hits_fixture.json")
    fixture = json.loads((HERE / sponsor_fixture).read_text(encoding="utf-8"))
    sponsor_text = fixture["sponsor"]["text"]
    result = measure(saved["sections"], sponsor_text)
    _print_report(f"{result_name} (BEFORE, saved draft)", sponsor_text, result, "n/a (saved draft)")


def main() -> None:
    args = sys.argv[1:]
    if "--before" in args:
        for result_name, sponsor_fixture in SAVED:
            run_saved(result_name, sponsor_fixture)
        return
    if "--saved" in args:
        i = args.index("--saved")
        result_name = args[i + 1]
        sponsor_fixture = args[args.index("--sponsor") + 1] if "--sponsor" in args else None
        run_saved(result_name, sponsor_fixture)
        return

    names = [a for a in args if a.endswith(".json")] or list(FIXTURES)
    for name in names:
        run_fixture(name)


if __name__ == "__main__":
    main()
