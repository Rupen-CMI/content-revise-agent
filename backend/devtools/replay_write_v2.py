"""Plan -> write on a fixture's cached chain inputs, then print the whole draft and objective measurements.
The plan is cached in the fixture (key "plan") so the writer can be tuned on an identical plan; --replan recomputes it.

Usage (from backend/): python devtools/replay_write_v2.py devtools/fixture_scraping.json [--replan] [--quiet]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import build_chain
import checks
import facts
import faqs
import planning
import prompts
import writer
from pipeline import _word_count
from pipeline_log import new_run_logger


def text_of(sections, sponsor):
    return [
        (s, [re.sub(prompts.CMI_LINK_PATTERN, r"\1", p.replace(prompts.SPONSOR_TOKEN, sponsor.text)) for p in s["paragraphs"]])
        for s in sections
    ]


def measure(plan, inp, sections, sponsor, faq=()) -> list[str]:
    out = []
    blocks = {"intro": 0, "transition": 0, "market": 0}
    per_section = {}
    for s, paras in text_of(sections, sponsor):
        w = sum(len(p.split()) for p in paras)
        tag = s["sop"][0] if s["sop"] else "intro"
        if tag == "intro":
            blocks["intro"] += w
        elif tag == "transition":
            blocks["transition"] += w
        else:
            blocks["market"] += w
            per_section[tag] = w
    total = sum(blocks.values())
    out.append(f"WORDS total {total} (target {plan['budgets']['target']}): intro {blocks['intro']} = {blocks['intro']/total:.1%} (cap 20%), "
               f"transition {blocks['transition']} = {blocks['transition']/total:.1%} (5-10%), market {blocks['market']} = {blocks['market']/total:.1%} (>=70%)")
    out.append("SECTION WORDS (actual/budget): " + ", ".join(f"{k} {per_section.get(k, 0)}/{v}" for k, v in
               ((s['sop'], s['words']) for s in plan['sections'])))
    market_name = plan["title"] and inp.market_name
    for s, paras in text_of(sections, sponsor):
        tag = s["sop"][0] if s["sop"] else "intro"
        n = [p.lower().count(market_name.lower()) for p in paras]
        if tag in planning.ORDER and (sum(n) == 0 or max(n) > 1):
            out.append(f"PRIMARY KEYWORD in {tag}: per paragraph {n}")
    body = "\n".join(p for _, ps in text_of(sections, sponsor) for p in ps)
    used_sec = [k for k, _ in inp.secondary_pool if checks.count_keyword_uses(body, f"{facts._market_core(inp.market_name) if hasattr(facts, '_market_core') else ''} market {k}") > 0]
    out.append(f"SECONDARY assigned: {[s['secondary'] for s in plan['sections'] if s['secondary']]}")
    lt_assigned = [next(x for x in inp.longtails if x['id'] == s['longtail'])['keyword'] for s in plan['sections'] if s['longtail']]
    lt_found = [kw for kw in lt_assigned if checks.longtail_spans(body, kw)]
    out.append(f"LONG-TAIL assigned {len(lt_assigned)}, found {len(lt_found)}: missing {[k for k in lt_assigned if k not in lt_found]}")
    allowed = set()
    for it in inp.items.values():
        allowed |= facts._numbers(it["text"] + " " + it.get("detail", ""))
    for pt in inp.points:
        allowed |= facts._numbers(pt["text"])
    allowed |= facts._numbers(sponsor.text) | facts._numbers(inp.market_name)
    stray = sorted(n for n in facts._numbers(body) if n not in allowed and not re.fullmatch(r"20[12]\d", n))
    out.append(f"NUMBERS not in any plan fact: {stray}")
    sentences = [x for x in re.split(r"(?<=[.!?])\s+", body) if len(x.split()) > 30]
    out.append(f"SENTENCES over 30 words: {len(sentences)}")
    out.append(f"CMI OPINIONS: {checks.count_cmi_opinions([p for s in sections for p in s['paragraphs']])}")
    if faq:
        out.append(f"FAQ: {len(faq)} entry(ies) — " + "; ".join(qa["question"] for qa in faq))
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for path in args:
        logger, log_path = new_run_logger()
        print(f"\n########## {path} (log {log_path})")
        chain = build_chain.load_chain(Path(path), logger)
        sponsor, report = chain["sponsor"], chain["report"]
        inp = planning.build_inputs(report.name, chain["fact_sheet"], chain["evidence_items"], chain["demand"], chain["publisher"], _word_count(sponsor.text))
        fixture = chain["fixture"]
        if "--replan" in sys.argv or "plan" not in fixture:
            fixture["plan"] = planning.run_plan(inp, logger)
            Path(path).write_text(json.dumps(fixture, indent=2, ensure_ascii=False), encoding="utf-8")
        plan = fixture["plan"]
        written, title, sections, cites = writer.run_write_v2(plan, inp, sponsor, report, logger)
        if "--no-faq" not in sys.argv:
            faq = faqs.run_faq(plan, inp, sections, logger)
        else:
            faq = []
        print(f"\nTITLE: {title}\n")
        if "--quiet" not in sys.argv:
            for s, paras in text_of(sections, sponsor):
                label = s["heading"] or ("(transition)" if s["sop"] == ["transition"] else "(opening)")
                print(f"--- {label} [{', '.join(s['sop']) or 'untagged'}]")
                for p in paras:
                    print(p)
                print()
        print("MEASUREMENTS")
        for line in measure(plan, inp, sections, sponsor, faq):
            print("  " + line)
        if faq and "--quiet" not in sys.argv:
            print("FAQ")
            for qa in faq:
                print(f"  Q: {qa['question']}\n  A: {qa['answer']}\n")
        print(f"  citations: {[c['id'] for c in cites]}")


if __name__ == "__main__":
    main()
