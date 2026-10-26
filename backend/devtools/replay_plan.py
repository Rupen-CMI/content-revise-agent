"""Runs the real planner (one LLM call) on a fixture's cached chain inputs and prints the validated plan and every repair
code had to make. Run build_chain.py first (or this script builds the inputs on its first use).

Usage (from backend/): python devtools/replay_plan.py devtools/fixture_scraping.json [more ...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_chain
import planning
from pipeline import _word_count
from pipeline_log import new_run_logger


def show(plan: dict, chain: dict) -> None:
    qtext = {q["id"]: q["question"] for q in chain["demand"]["questions"]}
    print(f"\nTITLE: {plan['title']}   (basis {plan['title_basis']})")
    print(f"INTRO  ({plan['intro']['words']} words, {plan['intro']['own_words']} own): points {plan['intro']['points']}")
    print(f"TRANSITION ({plan['transition']['words']} words): {plan['transition']['point']}")
    for s in plan["sections"]:
        q = f"  <- {s['msq']}" if s["msq"] else ""
        print(f"\n[{s['sop']}] {s['heading']}{q}   ({s['words']} words, {s['length']})")
        print(f"   point: {s['point']}")
        print(f"   facts: {s['facts']}  secondary={s['secondary']} longtail={s['longtail']} terms={s['terms']} snippet={s['snippet']}")
        if s.get("sub_segments"):
            print(f"   sub-segments: {s['sub_segments']}")
    print("\nFAQ:", [f"{i}: {qtext[i]}" for i in plan["faq"]])
    print(f"\nREPAIRS ({len(plan['repairs'])}):")
    for r in plan["repairs"]:
        print("  -", r)


def main() -> None:
    for path in [a for a in sys.argv[1:] if not a.startswith("--")]:
        logger, log_path = new_run_logger()
        print(f"\n===== {path} (log {log_path})")
        chain = build_chain.load_chain(Path(path), logger)
        inp = planning.build_inputs(
            chain["report"].name, chain["fact_sheet"], chain["evidence_items"], chain["demand"], chain["publisher"],
            _word_count(chain["sponsor"].text),
        )
        show(planning.run_plan(inp, logger), chain)


if __name__ == "__main__":
    main()
