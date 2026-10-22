"""Runs the real fact-sheet call (and, with --gap, the figure gap search + backing) against a frozen fixture's
confirmed report page, and prints what survived validation. Real LLM call; the --gap option also searches the web.

Usage (from backend/):
    python devtools/replay_facts.py devtools/fixture_scraping.json [--gap]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts
import reports
from docx_io import Link
from pipeline_log import new_run_logger


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    do_gap = "--gap" in sys.argv
    fixture = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    report = reports.Report(**fixture["report"])
    logger, log_path = new_run_logger()
    print(f"logging to {log_path}")
    fs = facts.run_fact_sheet(report.name, report, logger)
    if do_gap and fs["gaps"]:
        sponsor_hosts = [facts.search.urlparse(l.url).netloc.lower().removeprefix("www.")
                         for l in (Link(**d) for d in fixture["sponsor"]["links"]) if l.is_external]
        hits = facts.run_figure_gap_search(report.name, sponsor_hosts, logger)
        fs = facts.run_figure_backing(report.name, fs, hits, sponsor_hosts, logger)
    print(json.dumps(fs, indent=2, ensure_ascii=False))
    print("\nFLAT FACTS:")
    for f in facts.flatten_fact_sheet(fs):
        print(f"  {f['id']:<22} [{f['source']}{', historical' if f['historical'] else ''}] {f['text']}")
    print(f"\ngaps: {fs['gaps'] or 'none'}")


if __name__ == "__main__":
    main()
