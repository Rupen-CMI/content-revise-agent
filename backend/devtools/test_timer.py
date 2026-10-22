"""Offline test for main.StageTimer (Step 2's evidence-collection timing). Run from backend/: python devtools/test_timer.py"""
import logging
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


records: list[str] = []


class _H(logging.Handler):
    def emit(self, record):
        records.append(record.getMessage())


log = logging.getLogger("test_timer")
log.setLevel(logging.INFO)
log.addHandler(_H())

timer = main.StageTimer(log, "Step 2")


def work(name: str, secs: float) -> None:
    with timer.stage(name):
        time.sleep(secs)


threads = [threading.Thread(target=work, args=("A", 0.3)), threading.Thread(target=work, args=("B", 0.2))]
for t in threads:
    t.start()
for t in threads:
    t.join()
try:
    with timer.stage("C"):
        raise ValueError("boom")
except ValueError:
    pass
s = timer.summary()
names = [x["name"] for x in s["stages"]]
check("all three stages recorded, including the one that raised", sorted(names) == ["A", "B", "C"], str(names))
check("overlapping stages: total is wall time, not the sum", s["total_s"] < 0.45, str(s))
check("each stage carries seconds and a start offset", all("seconds" in x and "start_s" in x for x in s["stages"]))
check("each stage and the total are written to the log",
      sum("[timing] Step 2 /" in r for r in records) == 3 and any("TOTAL" in r for r in records), str(records))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all timer tests passed")
