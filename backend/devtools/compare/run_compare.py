"""Runs CALL 2 (select) through CALL 4 (revise) + finalize checks against the SAME frozen hit
set (devtools/compare/hits_fixture.json) for whichever provider is configured via env vars
(LLM_BASE_URL/LLM_API_KEY/LLM_MODEL) at process start — so each provider does its OWN real
selection over the identical 30 hits, not a shared pre-selected evidence string. This is what
lets us see which evidence each provider keeps vs. drops, not just how it writes.

NOTE: config.py no longer reads LLM_BASE_URL/LLM_API_KEY/LLM_MODEL (DeepSeek is the only supported
model), so this script now runs against DeepSeek only. Comparing another provider again means
temporarily restoring those env overrides in config.py.

Usage:
    python devtools/compare/run_compare.py <label> [--ollama-prompts]
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


def _hit_from(d: dict):
    import search
    from datetime import datetime
    d = dict(d)
    d["date"] = datetime.fromisoformat(d["date"]) if d["date"] else None
    return search.SearchHit(**d)


def main() -> None:
    args = sys.argv[1:]
    label = args[0] if args else "run"
    use_ollama_prompts = "--ollama-prompts" in args

    if use_ollama_prompts:
        import prompts_ollama
        pipeline.prompts = prompts_ollama
        print("using prompts_ollama (Ollama-tuned WRITE prompt)")

    fixture_name = next((a.split("=", 1)[1] for a in args if a.startswith("--fixture=")), "hits_fixture.json")
    fixture_path = Path(__file__).parent / fixture_name
    f = json.loads(fixture_path.read_text(encoding="utf-8"))

    article = _article_from(f["article"])
    sponsor = _paragraph_from(f["sponsor"])
    report = reports.Report(**f["report"])
    candidates = [reports.CatalogEntry(**c) for c in f["candidates"]]
    orient = f["orient"]
    hits = [_hit_from(h) for h in f["hits"]]

    logger, log_path = new_run_logger(f"compare-wealth-{label}")
    print(f"logging to {log_path}")

    # The frozen set predates the stale-web-result rule in search.filter_hits; re-applying the
    # current filter (deterministic, no network) keeps the frozen set identical for every provider
    # while honouring today's rules.
    import search
    sponsor_hosts = tuple(pipeline._host(l.url) for l in sponsor.links if l.is_external)
    hits = search.filter_hits(
        hits, [report.name], sponsor_hosts, key_players=reports.key_players(report), logger=logger
    )
    print(f"provider label: {label}, {len(hits)} hits (frozen set, current filter applied)")

    print("CALL 2: select (real LLM call, own selection over frozen hits)...")
    selection = pipeline.run_select(article, orient, sponsor, report, hits, logger)
    evidence = pipeline.run_build_evidence(selection, hits, logger)
    kept = selection.get("evidence") or []
    print(f"kept {len(kept)} evidence items out of {len(hits)} hits")
    for e in kept:
        print(f"  KEPT [{e.get('use_for','?')}] {e.get('fact','')[:120]}")

    print("\nCALL 3: write (real LLM call)...")
    written, title, sections, evidence_citations = pipeline.run_write(
        article, orient, sponsor, report, evidence, logger
    )

    print("CALL 4: revise (real LLM call)...")
    title, sections, evidence_citations = pipeline.run_revise(
        title, sections, evidence_citations, report, logger, evidence, sponsor.text
    )

    print(f"\nORIGINAL TITLE: {article.title}")
    print(f"TITLE: {title}\n")
    print("OUTLINE (heading | sop tags | words):")
    for s in sections:
        w = sum(len(p.split()) for p in s["paragraphs"])
        print(f"  {(s['heading'] or '(intro)')[:60]:60s} | {','.join(s['sop']) or '-':15s} | {w}")
    print()
    for s in sections:
        print(f"--- {s['heading'] or '(intro)'} [{', '.join(s['sop']) or 'untagged'}] ---")
        for p in s["paragraphs"]:
            print(p)
        print()
    print(f"evidence citations: {len(evidence_citations)}")
    for c in evidence_citations:
        print(f"  {c['id']}: {c['paragraph'][:100]}...")

    print("\nRunning deterministic SOP checks + writing .docx...")
    result = pipeline.run_finalize(
        article, sponsor, report, candidates, orient, selection, hits,
        title, sections, evidence, f["original_filename"], logger,
        evidence_citations=evidence_citations,
    )
    passed = sum(c.passed for c in result.checks)
    print(f"\nSOP checks: {passed}/{len(result.checks)} passed")
    for c in result.checks:
        mark = "PASS" if c.passed else "FAIL"
        print(f"  {mark}  {c.name} - {c.detail}")
    print(f"\noutput written to: {result.output_path}")

    # Save kept/dropped evidence for the side-by-side report.
    dropped_sources = {h.source or h.host for h in hits} - {
        (kept_item.get("source") or "") for kept_item in kept
    }
    summary_path = Path(__file__).parent / f"result_{label}.json"
    summary_path.write_text(json.dumps({
        "label": label,
        "title": title,
        "word_count": sum(len(p.split()) for s in sections for p in s["paragraphs"]),
        "kept_evidence_count": len(kept),
        "total_hits": len(hits),
        "kept_evidence": kept,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in result.checks],
    }, indent=2), encoding="utf-8")
    print(f"summary saved to {summary_path}")


if __name__ == "__main__":
    main()
