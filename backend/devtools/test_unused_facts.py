"""Offline test: an eligible dated news item left unused fills an evidence-less trend or sub-segment and the writer must state it; a
company's own data ("Kakao Style's Zigzag data showed ...") is cut to its figure, not removed. Run from backend/: python devtools/test_unused_facts.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import outside_sources  # noqa: E402
import planning  # noqa: E402
import writer  # noqa: E402

FAILED: list[str] = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


class Inp:
    items = {
        "fs.drivers.0": {"kind": "drivers", "text": "Rising skin-health awareness drives demand."},
        "fs.drivers.1": {"kind": "drivers", "text": "Retail expansion drives demand."},
        "fs.drivers.2": {"kind": "drivers", "text": "Personalisation drives demand."},
        "W1": {"kind": "evidence", "text": "Alpha launched a serum line."},
        "W2": {"kind": "evidence", "text": "Beta opened a laboratory."},
        "W23": {"kind": "evidence", "text": "Target reported beauty sales grew more than 9% in its third quarter."},
    }


groups = planning._trend_groups(
    [{"trend": "Skin-health awareness", "facts": ["fs.drivers.0"]}, {"trend": "Retail expansion", "facts": ["fs.drivers.1", "W1"]},
     {"trend": "Personalisation", "facts": ["fs.drivers.2", "W2"]}],
    ["fs.drivers.0", "fs.drivers.1", "fs.drivers.2", "W1", "W2"], Inp, lambda m: None, spare=lambda: ["W23"], claim=lambda f: None)
check("a trend with no news takes the free eligible item even when they share no word", "W23" in groups[0]["facts"], str(groups))

picks = [{"split": "By Product", "name": "Face Creams & Moisturizers"}, {"split": "By Gender", "name": "Female"}]
check("sub-segment match tolerates '&' against 'and', case and a plural",
      planning._about_subsegment({"subsegment": "face creams and moisturizer"}, picks[0], picks)
      and not planning._about_subsegment({"subsegment": "face creams and moisturizer"}, picks[1], picks))

ev = {"id": "W23", "kind": "evidence", "text": "Target reported beauty sales grew 9%.", "detail": "", "source": "Cosmetics Business, 2026-11",
      "sections": {"trends"}, "tags": ["trends"], "historical": False}
check("writer: the stated item is marked REQUIRED, an ordinary one is not",
      "REQUIRED" in writer._fact_text(ev, True, must_state=True) and "REQUIRED" not in writer._fact_text(ev, True))

text, dropped, names = outside_sources.clean_sentence(
    "Kakao Style's Zigzag data showed skin mist sales rose 550% year on year during October as colder weather increased dryness.")
check("a company's data attribution is cut, the figure stays", not dropped and "550%" in text and "Zigzag" not in text, text)

print("FAILED: " + ", ".join(FAILED) if FAILED else "all passed")
sys.exit(1 if FAILED else 0)
