"""A trend left one paragraph short (a strip emptied one) gets its longest paragraph split at a sentence break, so every trend
keeps three paragraphs; a trend that cannot be split is left for the check to report."""
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
short = ["AI shortens the interval between a blueprint and an item bank. That volume shifts the scarce resource to review. "
         "Review capacity becomes the constraint.", "Providers that formalise review absorb machine-drafted items."]
sec = {"sop": ["trends"], "heading": "h", "paragraphs": ["Lead-in."] + trend("A", three) + trend("B", three) + trend("C", short)}
out = writer.enforce_trend_shape([sec], logging.getLogger("t"))[0]
lead, blocks = subheads.split_blocks(out["paragraphs"])
check("a trend with two paragraphs is split back to three", [len(p) for _, p in blocks] == [3, 3, 3], str([len(p) for _, p in blocks]))
check("the split keeps every sentence, in order",
      " ".join(blocks[2][1]) == " ".join(short), str(blocks[2][1]))
one = {"sop": ["trends"], "heading": "h", "paragraphs": ["Lead-in."] + trend("A", three) + trend("B", three) + trend("C", ["Only one."])}
out = writer.enforce_trend_shape([one], logging.getLogger("t"))[0]
check("a single one-sentence paragraph cannot be split and is left as written",
      [len(p) for _, p in subheads.split_blocks(out["paragraphs"])[1]] == [3, 3, 1])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all trend-split tests passed")
