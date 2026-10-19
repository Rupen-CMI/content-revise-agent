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
print([l for l in log.splitlines() if "source groups chosen" in l][-1].split("revamp: ")[-1])
print([l for l in log.splitlines() if "[filter] kept" in l][-1].split("revamp: ")[-1])
print(f"\n{len(hits)} hits pass the filter. By host:")
for h, n in collections.Counter(x.host for x in hits).most_common():
    print(f"  {n:2d}  {h}")
print("\nHITS:")
for x in hits:
    print(f"  [{x.date:%Y-%m-%d}] " if x.date else "  [undated]  ", x.host, "|", x.title[:110])
