"""Offline test: SELECT drops a rival research firm's figure and any market size/forecast, but keeps a measured figure relayed
from a panel, operator, company, outlet, association or government body (live runs 2026-11-20)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import outside_sources as osrc  # noqa: E402
import pipeline  # noqa: E402

FAILS = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        FAILS.append(name)


def dropped(t):
    return pipeline._is_market_sizing(t)


KEPT = [
    "NielsenIQ data reported by CNBC shows U.S. K-beauty sales reached $2.8 billion in early 2026, a roughly 48% increase from a year earlier.",
    "Operators added 4,382 new charging ports across 806 stations during the second quarter of 2026, bringing the total nationwide to just under 78,000, according to Paren's State of the U.S. Fast EV Charging Industry report.",
    "Paren's Q3 2026 State of the US Fast EV Charging Industry report found that U.S. operators added 4,106 fast-charging ports at 664 stations.",
    "Target reported beauty sales grew more than 9% in the first quarter.",
    "According to the U.S. Energy Information Administration, EV sales rose 12% in 2025.",
    "Circana data shows U.S. prestige beauty unit sales rose 7% in 2025.",
    "Kantar found that 41% of shoppers bought a private-label skincare item in 2025.",
    "According to the National Retail Federation, 62% of shoppers used a mobile app in 2025.",
]
DROPPED = [
    "According to Grand View Research, the market will reach $5 billion by 2030.",
    "Statista estimates 62% of shoppers use AI assistants.",
    "The global skin care market is valued at $150 billion.",
    "A global strategic business report on cell line development states that the U.S. cell line development market was valued at approximately US$1.6 billion in 2025.",
    "NielsenIQ data shows the U.S. skin care market is projected to reach $90 billion by 2030.",   # a market forecast, whoever relays it
    "Gartner estimates 40% of CIOs will buy AI tools.",
    "Mordor Intelligence expects 12% of buyers to switch.",
]
for t in KEPT:
    check("kept: " + t[:70], not dropped(t))
for t in DROPPED:
    check("dropped: " + t[:70], dropped(t))

check("origin: a panel or outlet credit is usable", pipeline._origin_ok("CNBC, citing NielsenIQ and Morgan Stanley") and pipeline._origin_ok("Paren, as reported by Electrek"))
check("origin: a rival firm anywhere in the credit is not", not pipeline._origin_ok("CNBC, citing Statista") and not pipeline._origin_ok("Grand View Research")
      and not pipeline._origin_ok("Forbes via Gartner"))
check("rival_brand: measurement panels are not rivals", not any(osrc.rival_brand(n) for n in ("NielsenIQ", "Circana", "Kantar", "Paren")))

sel = {"evidence": [{"id": "W1", "fact": KEPT[0], "detail": "", "use_for": ["us"]}, {"id": "W2", "fact": DROPPED[2], "detail": "", "use_for": ["us"]}]}
import logging
pipeline._drop_market_sizing_facts(sel, logging.getLogger("t"))
check("drop pass keeps the measured sale, drops the market size", [e["id"] for e in sel["evidence"]] == ["W1"])
if FAILS:
    print(FAILS)
    sys.exit(1)
print("all usable-stats tests passed")
