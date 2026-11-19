"""Offline checks for the positive-only backstop (polarity.py): legal and regulatory action against a company reads as a setback,
the same words in a market's own products do not.
Run from backend/: python devtools/test_polarity.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polarity  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


NEGATIVE = [
    # live, Riz healthcare IT draft: this item passed the backstop and a canvas "beef it up" wrote it into the U.S. section
    "The U.S. Federal Trade Commission opened an antitrust investigation into health records company Epic Systems.",
    "Reuters reported on August 14, 2026, that Epic Systems is facing the FTC probe.",
    "Regulators began a formal inquiry into the merger.",
    "The firm is under investigation by the state attorney general.",
    "The startup was sued by a rival over patents.",
    "Shareholders filed a class action against the vendor.",
]
NEUTRAL = [
    "Catholic Health and GE HealthCare announced a 10-year strategic partnership valued at approximately $500 million.",
    "Optum Financial closed its acquisition of Alegeus Technologies.",
    "The company launched a clinical investigation of its new glucose sensor.",
    "A new ultrasound probe was cleared by the FDA.",
    "Researchers began a scientific inquiry into battery chemistry.",
]
for t in NEGATIVE:
    check(f"setback: {t[:70]}", polarity.is_negative(t), str(polarity.negative_hits(t)))
for t in NEUTRAL:
    check(f"neutral: {t[:70]}", not polarity.is_negative(t), str(polarity.negative_hits(t)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all polarity tests passed")
