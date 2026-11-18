"""The competitor tab's "Build my own fact sheet": an empty sheet for the picked market (never another market's report),
which the editor fills in; their figures are used exactly as entered, unnamed, and the sheet cannot be confirmed empty."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import facts  # noqa: E402
import main  # noqa: E402
from docx_io import Paragraph, SourceArticle  # noqa: E402

FAILURES = []
TOKEN = "own-sheet-token"


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        FAILURES.append(name)


main._RUNS[TOKEN] = main.RunState(
    filename="upload.docx", article=SourceArticle(title="A Blog Title", paragraphs=[]),
    sponsor=Paragraph(index=0, text="Sponsor line."),
    orient={"topic_summary": "t", "bridge_angle": "a", "own_market": "Direct Mail Retargeting Market"},
    candidates=[], logger=logging.getLogger("test"), log_path=Path("devtools/never_written.log"),
)
c = TestClient(main.app)

r = c.post("/reports/fetch", json={"run_token": TOKEN, "kind": "competitor", "markets": ["Direct Mail Retargeting Market"],
                                   "own_sheet": True})
d = r.json()
fs = d.get("fact_sheet") or {}
check("own sheet: answered at once, empty, for the picked market, nothing read or linked",
      r.status_code == 200 and fs.get("built_by_editor") and not facts.flatten_fact_sheet(fs) and not fs.get("sources")
      and d["report"]["name"] == "Direct Mail Retargeting Market" and not d["report"]["url"], r.text[:300])
check("own sheet: the editor is told what to do", any("building this fact sheet yourself" in w for w in d.get("warnings", [])))

r = c.post("/reports/confirm", json={"run_token": TOKEN, "kind": "competitor", "fact_sheet_edits": {}})
check("own sheet: cannot be confirmed while empty", r.status_code == 400 and "empty" in r.text, r.text[:200])

edits = {"size": {"value": "$1.2 billion", "when": "2025"}, "cagr": {"value": "8.4%", "when": "2026-2033"},
         "segments": [{"type": "By Industry", "items": [{"name": "E-commerce", "figure": "41%"}, {"name": "Education", "figure": ""}],
                       "dominant": "E-commerce"}],
         "drivers": [{"text": "Rising digital ad costs push brands toward mail that reaches known visitors."}],
         "players": ["PostPilot", "Lob"]}
r = c.post("/reports/confirm", json={"run_token": TOKEN, "kind": "competitor", "fact_sheet_edits": edits})
fs = r.json().get("fact_sheet") or {}
check("own sheet: the editor's figures are kept exactly, unvaried and unnamed",
      r.status_code == 200 and fs["size"]["value"] == "$1.2 billion" and fs["cagr"]["value"] == "8.4%"
      and not fs.get("variance") and fs["size"].get("external") and fs["gaps"] == ["forecast"], r.text[:400])
flat = facts.flatten_fact_sheet(fs)
check("own sheet: every fact reaches the writer with no source to name",
      flat and all(not f["source"] for f in flat if f["kind"] in ("size", "cagr", "drivers")), str(flat)[:400])
check("own sheet: segments and players are there", fs["segments"] and fs["players"] == ["PostPilot", "Lob"], str(fs)[:300])

check("own sheet: the report's segment names follow the sheet's segments (SELECT and long-tail read them)",
      main._RUNS[TOKEN].report.segment_terms == ["E-commerce", "Education"], str(main._RUNS[TOKEN].report.segment_terms))
players_fact = next(f for f in flat if f["kind"] == "players")
check("players fact text says nothing about a report naming them", players_fact["text"] == "Key players: PostPilot, Lob", players_fact["text"])
import logging as _lg, writer  # noqa: E402
meta = [{"sop": ["key_players"], "paragraphs": [
    "PostPilot and Lob are the key players named in the Direct Mail Retargeting Market, which indicates a small group. "
    "PostPilot and Lob are the providers the market's own account identifies as leading. "
    "PostPilot launched a postcard API in 2025."]}]
out = writer.strip_meta_sentences(meta, _lg.getLogger("t"))
check("key players: sentences describing the list are stripped, the real one stays",
      out[0]["paragraphs"] == ["PostPilot launched a postcard API in 2025."], str(out))

main._RUNS.pop(TOKEN, None)
print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all own-sheet tests passed")
