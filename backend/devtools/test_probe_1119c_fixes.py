"""Offline checks for three defects of the 2026-11-19 live probe: a publication's self-description kept as trend evidence,
a trends-search item filed under the U.S. section so Trends got none, and a key-players opening sentence cut as process wording.
Run from backend/: python devtools/test_probe_1119c_fixes.py"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pipeline  # noqa: E402
import search  # noqa: E402
import writer  # noqa: E402

LOG = logging.getLogger("t")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


# ---- defect 3: a self-description is not an event
NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


def _mk(title, secs, scope, dated):
    h = search.SearchHit(title, "snippet", "https://e.example.org/" + title[:6].replace(" ", ""), "", NOW if dated else None, is_news=True)
    h.sections = list(secs)
    h.scope = scope
    return h


self_desc = "MultiLingual reports on the people and technology driving the language industry through its magazine, podcasts and online news."
real = "RWS completed its acquisition of Acogroup, the parent company of Acolad, in November 2026."
hits = [_mk("MultiLingual magazine", ["trends"], "market", True), _mk("RWS acquires Acogroup", ["key_players"], "market", True)]
sel = {"evidence": [{"id": "W1", "fact": self_desc, "detail": "", "use_for": "trends"},
                    {"id": "W2", "fact": real, "detail": "", "use_for": "players"}]}
pipeline._drop_non_event_facts(sel, hits, LOG)
check("a publication's description of itself is dropped, the real acquisition stays",
      [e["id"] for e in sel["evidence"]] == ["W2"], str(sel))
for fact in ("The Ministry launched a technology rollout in October 2026 and reports on the technology in 1,200 schools.",
             "Acme launched a platform in November 2026 that covers the news and trends of the sector."):
    s2 = {"evidence": [{"id": "W2", "fact": fact, "detail": "", "use_for": "trends"}]}
    pipeline._drop_non_event_facts(s2, hits, LOG)
    check(f"an event that happens to use the same words stays: {fact[:40]!r}", len(s2["evidence"]) == 1, str(s2))

# ---- defect 4: a trends-search item is not taken from trends just because it happens in the U.S.
SUBS = [{"name": "Aptitude Tests"}, {"name": "Information technology (IT)"}]
hits4 = [_mk("UH selects proctoring tool", ["trends"], "market", True),
         _mk("PrepSolution launches courses", ["us"], "us", True),
         _mk("Undated page about exams", ["trends"], "market", False)]
sel4 = {"evidence": [
    {"id": "W1", "fact": "The University of Hawaii is rolling out an online exam proctoring tool this fall.", "detail": "", "use_for": ["us"]},
    {"id": "W2", "fact": "PrepSolution launched animated video courses for HR certification exams.", "detail": "", "use_for": ["us"]},
]}
pipeline._assign_sections(sel4, hits4, SUBS, LOG)
sec = {e["id"]: e["section"] for e in sel4["evidence"]}
check("a dated item the trends search found, filed as U.S., goes back to trends", sec.get("W1") == "trends", str(sec))
check("an item the U.S. search found stays in the U.S. section", sec.get("W2") == "us", str(sec))
w1 = next(e for e in sel4["evidence"] if e["id"] == "W1")
check("the moved item carries the trends tag everywhere", w1["use_for"] == "trends" and w1["use_for_all"] == ["trends"] and w1["subsegment"] == "", str(w1))
many = {"evidence": [{"id": f"W{i}", "fact": f"Firm{i} launched a product.", "detail": "", "use_for": ["trends"]} for i in range(1, 7)]
        + [{"id": "W7", "fact": "A U.S. rollout started.", "detail": "", "use_for": ["us"]}]}
hits_many = [_mk(f"Trend item {i}", ["trends"], "market", True) for i in range(1, 8)]
pipeline._assign_sections(many, hits_many, SUBS, LOG)
check("once trends has its minimum, a U.S. filing stays",
      next(e for e in many["evidence"] if e["id"] == "W7")["section"] == "us")

# ---- defect 5: the key-players list sentence is content
opening = ("The companies CMI identifies as leading the Language Services Market are RWS, LanguageLine Solutions, Appen, translate plus, "
           "Acolad, Welocalize and Hogarth Worldwide. Their presence across translation, interpretation and media work points to a field "
           "that is broad rather than dominated by a single supplier.")
secs = [{"sop": ["key_players"], "paragraphs": [opening + " RWS completed its acquisition of Acogroup in November 2026."]}]
out = writer.strip_meta_sentences(secs, LOG)
check("a sentence listing the companies the report names as leading survives, and so does the sentence after it",
      out[0]["paragraphs"] and "RWS, LanguageLine Solutions" in out[0]["paragraphs"][0] and "Their presence" in out[0]["paragraphs"][0], str(out))
bare = [{"sop": ["key_players"], "paragraphs": [
    "PostPilot and Lob are the key players named in the Direct Mail Retargeting Market. PostPilot and Lob are the providers the market's own account identifies as leading. PostPilot launched a postcard API in 2025."]}]
out = writer.strip_meta_sentences(bare, LOG)
check("a sentence that only describes the list (two names) is still cut", out[0]["paragraphs"] == ["PostPilot launched a postcard API in 2025."], str(out))
proc = [{"sop": ["trends"], "paragraphs": ["The sections below set out how large demand has become. Demand grew in 2026."]}]
out = writer.strip_meta_sentences(proc, LOG)
check("true roadmap process language is still cut", out[0]["paragraphs"] == ["Demand grew in 2026."], str(out))

print()
print("all probe 1119c fix tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
