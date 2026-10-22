"""Runs the real fact-sheet and search-demand calls against frozen fixtures and prints the questions, related terms and
long-tail searches that survive validation. Real LLM calls, no web search.

Usage (from backend/):
    python devtools/replay_demand.py devtools/fixture_scraping.json [more fixtures ...]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import demand
import facts
import reports
from pipeline_log import new_run_logger


def main() -> None:
    for path in sys.argv[1:]:
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
        report = reports.Report(**fixture["report"])
        logger, log_path = new_run_logger()
        print(f"\n===== {report.name}  (log {log_path})")
        fs = facts.run_fact_sheet(report.name, report, logger)
        result = demand.run_search_demand(report.name, fs, logger)
        print("\nMOST SEARCHED QUESTIONS")
        for q in result["questions"]:
            print(f"  {q['id']:<4} [{q['intent']:<17} -> {q['section']:<14}] {q['question']}")
        print("\nRELATED TERMS:", ", ".join(result["terms"]))
        print("\nLONG-TAIL SEARCHES")
        for lt in result["long_tail"]:
            print(f"  {lt['keyword']}   (fact {lt['fact']}; {lt['why']})")
        evidence = []
        demand.mark_answerability(result["questions"], fs, evidence, logger)
        print("\nANSWERABLE FROM THE REPORT ALONE:", [q["id"] for q in result["questions"] if q["answerable"]])


if __name__ == "__main__":
    main()
