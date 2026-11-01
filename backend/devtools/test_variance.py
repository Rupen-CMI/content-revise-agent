"""Offline test for variance.py and the outside-report fact sheet. No network, no LLM.

Run from backend/: python devtools/test_variance.py
"""
import copy
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts  # noqa: E402
import variance  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_variance")
TODAY = date(2026, 10, 23)


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


MARKET = "Human Translation Market"
URL = "https://rival.example/human-translation-market"
PAGE = (
    "The Human Translation Market is estimated to be valued at USD 500.0 million in 2025 and is expected to reach "
    "USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032. "
    "By type, Legal translation held 48.0% of the Human Translation Market in 2025. "
    "By type, Medical translation held 32.0% of the Human Translation Market in 2025. "
    "By type, Literary translation held 20.0% of the Human Translation Market in 2025. "
    "Demand grew 12 percent among hospitals in 2025."
)
Q = "The Human Translation Market is estimated to be valued at USD 500.0 million in 2025 and is expected to reach USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032"


def raw() -> dict:
    return {
        "size": {"value": "USD 500.0 million", "year": "2025", "quote": Q},
        "forecast": {"value": "USD 800.0 million", "year": "2032", "quote": Q},
        "cagr": {"value": "6.9%", "period": "2025 to 2032", "quote": Q},
        "segments": [{"type": "By Type", "items": [
            {"name": "Legal translation", "figure": "48.0%", "quote": "By type, Legal translation held 48.0% of the Human Translation Market in 2025"},
            {"name": "Medical translation", "figure": "32.0%", "quote": "By type, Medical translation held 32.0% of the Human Translation Market in 2025"},
            {"name": "Literary translation", "figure": "20.0%", "quote": "By type, Literary translation held 20.0% of the Human Translation Market in 2025"},
        ]}],
        "drivers": [{"text": "Hospitals raised demand by 12 percent in 2025.", "quote": "Demand grew 12 percent among hospitals in 2025"}],
    }


outside = {"name": "rival.example", "url": URL}
fs = facts.validate_fact_sheet(raw(), PAGE, MARKET, LOG, TODAY, outside=outside)
check("outside: headline figures are kept and carry their outside source", fs["size"]["source"] == "rival.example" and fs["size"]["external"])
check("outside: the sheet is marked external", fs["external"]["url"] == URL)

wrong = facts.validate_fact_sheet(raw(), PAGE.replace("Human Translation Market", "Machine Translation Market"), "Human Translation Market", LOG, TODAY, outside=outside)
check("scope: a page about another market yields no headline figure for this one", wrong["gaps"] == ["size", "forecast", "cagr"])
broader = facts.validate_fact_sheet(raw(), PAGE.replace("Human Translation Market", "Translation Services Market"), "Human Translation Market", LOG, TODAY, outside=outside)
check("scope: a broader market's page is refused too", broader["gaps"] == ["size", "forecast", "cagr"])

original = copy.deepcopy(fs)
audit = variance.vary_fact_sheet(fs, MARKET)
check("variance: every headline figure moved", all(fs[k]["value"] != original[k]["value"] for k in ("size", "forecast", "cagr")))
check("variance: the original is kept beside the shown value", fs["size"]["original_value"] == "USD 500.0 million")
size_ratio = variance._amount(fs["size"]["value"]) / 500.0
forecast_ratio = variance._amount(fs["forecast"]["value"]) / 800.0
check("variance: size moves by 3 to 5 percent", 0.029 <= abs(size_ratio - 1) <= 0.051, str(size_ratio))
check("variance: forecast moves by 3 to 5 percent", 0.029 <= abs(forecast_ratio - 1) <= 0.051, str(forecast_ratio))
size_v, forecast_v = variance._amount(fs["size"]["value"]), variance._amount(fs["forecast"]["value"])
cagr_v = float(fs["cagr"]["value"].rstrip("%"))
recomputed = ((forecast_v / size_v) ** (1 / 7) - 1) * 100
check("variance: the growth rate agrees with the moved size and forecast", abs(cagr_v - recomputed) < 0.06, f"{cagr_v} vs {recomputed:.2f}")
check("variance: years are untouched", fs["size"]["when"] == "2025" and "2025" in fs["cagr"]["when"])

shares = {n: f["amount"] for n, f in fs["segments"][0]["figures"].items()}
check("variance: shares keep their order", sorted(shares, key=shares.get, reverse=True) == ["Legal translation", "Medical translation", "Literary translation"], str(shares))
check("variance: shares keep their total", abs(sum(shares.values()) - 100.0) < 0.3, str(sum(shares.values())))
check("variance: every share moved", all(f["figure"] != f["original_figure"] for f in fs["segments"][0]["figures"].values()))
check("variance: a number inside a statement moves, a year does not",
      "2025" in fs["drivers"][0]["text"] and "12 percent" not in fs["drivers"][0]["text"], fs["drivers"][0]["text"])
check("variance: the audit lists the headline figures, shares and statements", len(audit) == 3 + 3 + 1, str(len(audit)))
check("variance: each audit row names the source URL", all(a["url"] == URL for a in audit))

again = facts.validate_fact_sheet(raw(), PAGE, MARKET, LOG, TODAY, outside=outside)
variance.vary_fact_sheet(again, MARKET)
check("variance: the same market and source always shows the same figures", again["size"]["value"] == fs["size"]["value"] and again["cagr"]["value"] == fs["cagr"]["value"])

cmi = facts.validate_fact_sheet(raw(), PAGE, MARKET, LOG, TODAY)
before = copy.deepcopy(cmi)
check("variance: a CMI sheet is left alone", variance.vary_fact_sheet(cmi, MARKET) == [] and cmi["size"] == before["size"])

flat = {f["id"]: f for f in facts.flatten_fact_sheet(fs)}
check("flatten: an outside report's figures reach the writer as plain statements, not as a named competitor", flat["fs.size"]["source"] == "CMI report")
check("flatten: the writer sees the moved figure, never the original", "500.0" not in flat["fs.size"]["text"] and fs["size"]["value"] in flat["fs.size"]["text"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all variance tests passed")
