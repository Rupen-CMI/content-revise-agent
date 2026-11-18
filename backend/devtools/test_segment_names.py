"""Every category of every segment split is named in the segmentation section: writer.ensure_segment_names completes a line that
misses categories and adds a line for a split the writer left out; the report-figures check reads plural and singular alike."""
import logging
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import writer  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        FAILURES.append(name)


SEGMENTS = [{"type": "By Assessment Type", "items": ["Aptitude Tests", "Personality Tests", "Skill Tests"]},
            {"type": "By Organization Size", "items": ["Small and Medium-sized Enterprise (SMEs)", "Large Enterprise"]},
            {"type": "By Industry Vertical", "items": ["Information technology (IT)", "BFSI", "Healthcare"]}]
inp = types.SimpleNamespace(segments=SEGMENTS)
para = ("CMI sets out the segmentation of the market.\n- By assessment type, aptitude tests and personality tests lead.\n"
        "- By organization size, small and medium-sized enterprises (SMEs) and large enterprises are separated.")
out = writer.ensure_segment_names([{"sop": ["segmentation"], "heading": "h", "paragraphs": [para]}], inp, logging.getLogger("t"))
text = out[0]["paragraphs"][0]
lines = text.split("\n")
check("a line missing a category gets it added", lines[1].endswith("the split also covers Skill Tests."), lines[1])
check("plural wording counts ('large enterprises' names 'Large Enterprise'): that line is left alone",
      lines[2] == "- By organization size, small and medium-sized enterprises (SMEs) and large enterprises are separated.", lines[2])
check("a split left out gets its own line naming every category",
      lines[-1] == "- By Industry Vertical: Information technology (IT), BFSI and Healthcare.", lines[-1])
seen = checks._stems(text)
check("after the repair every category reads as named",
      all(checks._category_stems(c) <= seen for seg in SEGMENTS for c in seg["items"]))
same = writer.ensure_segment_names([{"sop": ["segmentation"], "heading": "h", "paragraphs": [text]}], inp, logging.getLogger("t"))
check("a complete section is not touched again", same[0]["paragraphs"][0] == text)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all segment-name tests passed")
