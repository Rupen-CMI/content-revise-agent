"""Each trend is ONE paragraph (the content team's spec, 2026-11-19). A trend the writer split into several paragraphs is joined
back into one, every sentence kept in order; a trend already of one paragraph is left as written."""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import subheads  # noqa: E402
import writer  # noqa: E402

FAILURES = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -- {detail}"))
    if not ok:
        FAILURES.append(name)


def trend(heading, paras):
    return [subheads.make_subhead(heading)] + paras


three = ["One. Two.", "Three. Four.", "Five. Six."]
sec = {"sop": ["trends"], "heading": "h", "paragraphs": ["Lead-in."] + trend("A", three) + trend("B", ["Seven."]) + trend("C", three[:2])}
out = writer.enforce_trend_shape([sec], logging.getLogger("t"))[0]
lead, blocks = subheads.split_blocks(out["paragraphs"])
check("every trend ends as one paragraph", [len(p) for _, p in blocks] == [1, 1, 1], str([len(p) for _, p in blocks]))
check("the join keeps every sentence, in order", blocks[0][1] == ["One. Two. Three. Four. Five. Six."] and blocks[2][1] == ["One. Two. Three. Four."],
      str(blocks))
check("a trend already of one paragraph is left as written", blocks[1][1] == ["Seven."], str(blocks[1]))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all trend-join tests passed")
