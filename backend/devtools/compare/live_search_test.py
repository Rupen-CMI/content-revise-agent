"""Runs the REAL search stage (LLM queries + open-web + targeted site queries + filter) for a fixture's
market and prints the funnel, so source-list / filter changes can be judged on live data.

Usage: python devtools/compare/live_search_test.py [fixture.json] [key player, key player, ...]
"""
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pipeline
import search
from docx_io import Link, Paragraph
from pipeline_log import new_run_logger

fixture = json.loads((Path(__file__).parent / (sys.argv[1] if len(sys.argv) > 1 else "mkt_fixture.json")).read_text())
players = [p.strip() for p in " ".join(sys.argv[2:]).split(",") if p.strip()]
sd = fixture["sponsor"]
sponsor = Paragraph(index=sd["index"], text=sd["text"], links=[Link(**l) for l in sd["links"]])
name = fixture["report"]["name"]

logger, log_path = new_run_logger()
print("log:", log_path, "| market:", name, "| key players:", players or "(none)")
hits = pipeline.run_search_evidence(name, sponsor, [], logger, key_players=players, orient=fixture["orient"])

log = Path(log_path).read_text(encoding="utf-8", errors="replace")
for marker in ("source groups chosen", "[gather]", "primary stage kept", "DDGS fallback", "[fulltext]", "full-text excerpts"):
    lines = [l for l in log.splitlines() if marker in l]
    if lines:
        print(lines[-1].split("revamp: ")[-1])
print([l for l in log.splitlines() if "[filter] kept" in l][-1].split("revamp: ")[-1])
print(f"\n{len(hits)} hits pass the filter ({sum(1 for x in hits if x.excerpt)} with full-text excerpts). By host:")
for h, n in collections.Counter(x.host for x in hits).most_common():
    print(f"  {n:2d}  {h}")
print("\nHITS:")
for x in hits:
    print(f"  [{x.date:%Y-%m-%d}] " if x.date else "  [undated]  ", "T" if x.excerpt else "-", x.host, "|", x.title[:110])
if hits and any(x.excerpt for x in hits):
    ex = next(x for x in hits if x.excerpt)
    print(f"\nSAMPLE EXCERPT ({ex.host}, {len(ex.excerpt)} chars):\n{ex.excerpt}")
