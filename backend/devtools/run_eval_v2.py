"""Phase 10 evaluation (docs/restructure_plan.md §4 step 10): 3 fixtures x 3 live replays each.

Each replay runs PLAN (the fixture's cached plan on replay 1; a fresh plan call on replays 2 and 3),
WRITE, and FAQ MSQs, then checks.run_checks_v2 on the finished draft — the same thing a real
Step 4 + finalize would run. Output: failures printed per replay, full draft + checks saved under
devtools/eval_v2/<market>_<n>.txt for the read-through and judgement against the plan's five goals.

Usage (from backend/): python devtools/run_eval_v2.py            # scraping, wealth, marketing
                       python devtools/run_eval_v2.py fixture_scraping.json   # one fixture only
"""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_chain
import checks
import faqs
import planning
import prompts
import writer
from pipeline import _word_count
from pipeline_log import new_run_logger

OUT_DIR = Path(__file__).resolve().parent / "eval_v2"
REPLAYS = 3
FIXTURES = ("fixture_scraping.json", "fixture_wealth.json", "fixture_marketing.json")


def draft_text_of(sections: list[dict], sponsor_text: str) -> str:
    return "\n\n".join(
        f"### {s['heading'] or ('(transition)' if s['sop'] == ['transition'] else '(opening)')} "
        f"[{', '.join(s['sop']) or 'untagged'}]\n"
        + "\n\n".join(p.replace(prompts.SPONSOR_TOKEN, sponsor_text) for p in s["paragraphs"])
        for s in sections
    )


def run_one(fixture_path: Path) -> None:
    market = fixture_path.stem.replace("fixture_", "")
    for n in range(1, REPLAYS + 1):
        logger, log_path = new_run_logger()
        chain = build_chain.load_chain(fixture_path, logger)
        fixture = chain["fixture"]
        inp = planning.build_inputs(
            chain["report"].name, chain["fact_sheet"], chain["evidence_items"], chain["demand"],
            chain["publisher"], _word_count(chain["sponsor"].text))
        if n == 1 and "plan" in fixture:
            plan = fixture["plan"]
            logger.info("[eval] replay 1: the fixture's cached plan")
        else:
            plan = planning.run_plan(inp, logger)      # replays 2 and 3 plan afresh, so planner variance is measured too
            if "plan" not in fixture:
                fixture["plan"] = plan
                Path(fixture_path).write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
        if plan.get("repairs"):
            logger.warning("[eval] plan repairs: " + "; ".join(plan["repairs"]))

        _, title, sections, cites = writer.run_write_v2(plan, inp, chain["sponsor"], chain["report"], logger)
        faq = faqs.run_faq(plan, inp, sections, logger)
        results = checks.run_checks_v2(
            title, sections, plan, inp, faq=faq, sponsor_brand_terms=[],
            sponsor_text=chain["sponsor"].text,
            report_text=chain["report"].sections.get("live_report_page", ""),
            report_name=chain["report"].name, selection=fixture.get("selection"), hits=chain["hits"],
        )

        draft_path = OUT_DIR / f"{market}_{n}.txt"
        draft_path.write_text(
            f"TITLE: {title}\n\n{draft_text_of(sections, chain['sponsor'].text)}\n\n"
            + "\n\n".join(f"Q: {qa['question']}\nA: {qa['answer']}" for qa in faq)
            + "\n\ncitations: " + ", ".join(c["id"] for c in cites)
            + "\n\nCHECKS\n" + "\n".join(
                ("PASS " if r.passed else "FAIL ") + r.name + (f" — {r.detail}" if r.detail else "")
                for r in results),
            encoding="utf-8")
        hard_failed = [r for r in results if not r.passed and not "(soft)" in r.name]
        softs = [r for r in results if not r.passed and "(soft)" in r.name]
        print(f"===== {market} replay {n} (log {Path(log_path).name}, draft {draft_path.name}) — "
              f"hard fails: {len(hard_failed)}")
        for r in hard_failed:
            print(f"  FAIL {r.name} — {r.detail[:240]}")
        print("  soft misses: " + ("; ".join(r.name.replace(" (soft)", "") for r in softs) or "(none)"))


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    OUT_DIR.mkdir(exist_ok=True)
    args = [a for a in sys.argv[1:] if a != "--"]
    paths = [Path(a) for a in args] or [Path(__file__).resolve().parent / n for n in FIXTURES]
    for p in paths:
        run_one(p)


if __name__ == "__main__":
    main()
