"""Offline regression for the repeat-figure strip, from the live run that gutted sections (market "Electric Vehicle
Charger Market", log 20261113_131626_run.log lines 3745-3747). The strip matched figures on bare digits, so sentences
whose small integers happened to collide with other facts' unrelated figures were deleted wholesale: the U.S. section
fell to 68 words (its Wisconsin sentence lost to Reuters' "€25 billion" and its Brooklyn Navy Yard sentence to WRAL's
"30 states"), and the sub-segment section's Fort Wayne sentence lost to the intro's "Level 2" and two amps figures.
These tests hold that a figure's identity is its value with its unit — "$25 million" is not "€25 billion", "30
charging stations" is not "30 states", "48 amps" and "Level 2" are counts and labels no fact owns — that a section's
own evidence sentences all survive, that a genuine same-figure repeat is still removed when the section keeps other
evidence, and that a section whose every citation the removal would take keeps them for the attribution check to
report instead (the guard strip_repeat_evidence has).
Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_repeat_figure_strip.py   (-v prints the log)"""
import copy
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import content_rules  # noqa: E402
import writer  # noqa: E402

LOG = logging.getLogger("test_repeat_figure_strip")
LOG.setLevel(logging.INFO)
LOG.addHandler(logging.StreamHandler(sys.stdout) if "-v" in sys.argv else logging.NullHandler())
LOG.propagate = False
FAILURES: list[str] = []
MARKET = "Electric Vehicle Charger Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# The fact-sheet entries the run's Evidence step handed the writer (log [select] PARSED JSON), trimmed to the
# figures the live collision rode on. Sources carry the same ", YYYY-MM" suffix the strip's logging shows.
ITEMS = {
    "W42": {"id": "W42", "kind": "evidence", "source": "Reuters, 2026-11",
            "text": "Europe's cities need almost €25 billion ($27.94 billion) of investment in charging "
                    "infrastructure to cater for the growth of electric vehicles by 2030."},
    "W20": {"id": "W20", "kind": "evidence", "source": "WRAL, 2026-09",
            "text": "IONNA operates about 177 locations across 30 states and expects to surpass 200 by the end of "
                    "the year."},
    "W31": {"id": "W31", "kind": "evidence", "source": "Electrek, 2026-11",
            "text": "Canada's proposed federal budget set aside about $635 million specifically for EV charging "
                    "infrastructure, including $587 million to renew the Zero-Emission Vehicle Infrastructure "
                    "Program."},
    "W30": {"id": "W30", "kind": "evidence", "source": "InsideEVs, 2026-11",
            "text": "During a demonstration, the electric wagon charged from 10% to 80% in six minutes and 32 "
                    "seconds."},
    "W16": {"id": "W16", "kind": "evidence", "source": "TaxiPoint, 2026-10",
            "text": "The consultation closes at 11.59pm on 20 December 2026."},
    "W22": {"id": "W22", "kind": "evidence", "source": "WKOW, 2026-09",
            "text": "Wisconsin Governor Tony Evers and the Wisconsin Department of Transportation announced 42 "
                    "locations across the state will receive grant awards to build fast electric vehicle charging "
                    "stations with $25 million in federal funding."},
    "W12": {"id": "W12", "kind": "evidence", "source": "BKReader, 2026-11",
            "text": "The Brooklyn Navy Yard Development Corporation sought private-sector partners to install and "
                    "operate up to 30 electric vehicle charging stations at its industrial campus."},
    "W3": {"id": "W3", "kind": "evidence", "source": "WBOC, 2026-05",
           "text": "A Fort Wayne electrician reported that most Level 2 residential EV charging stations draw "
                   "between 32 and 48 amps of power, with some high-performance models requiring up to 80 amps."},
    "W2": {"id": "W2", "kind": "evidence", "source": "Bisnow, 2026-09",
           "text": "Operators added 4,382 new charging ports across 806 stations during the second quarter of 2026."},
}

# The sections the model wrote (log [write] PARSED JSON), the ones the live strip deleted sentences from.
SECTIONS = [
    {"heading": "What are the latest trends in the electric vehicle charger market?", "sop": ["trends"], "paragraphs": [
        "A study reported by Reuters in November 2026 found that European cities could cut their electric vehicle "
        "grid bill by 40% through smart charging, placing the investment European cities need by 2030 at almost "
        "€25 billion, or $27.94 billion.",
        "Eight of the world's largest automakers gathered in Durham in September 2026 to recognise their shared "
        "IONNA electric vehicle charging network, WRAL reported; IONNA operates about 177 locations across 30 states.",
        "Canada's proposed federal budget set aside about $635 million (CAD) for electric vehicle charging "
        "infrastructure, including $587 million to renew the Zero-Emission Vehicle Infrastructure Program, Electrek "
        "reported in November 2026.",
    ]},
    {"heading": "Sub-Segment Analysis of the Electric Vehicle Charger Market", "sop": ["subsegment"], "paragraphs": [
        "Residential buyers hold approximately 74.0% of the Electric Vehicle Charger Market by end user in 2026 and "
        "lead that split. A Fort Wayne electrician reported in May 2026 that most Level 2 residential stations draw "
        "between 32 and 48 amps, with high-performance models reaching 80 amps, which indicates the electrical "
        "capacity households now plan for. Paua, a UK charging payment platform for businesses, partnered with "
        "Powerverse in November 2026 to give fleet customers access to compatible home chargers.",
    ]},
    {"heading": "U.S. Analysis of the Electric Vehicle Charger Market", "sop": ["country_us"], "paragraphs": [
        "Public money and institutional procurement are widening the U.S. charging base. Wisconsin Governor Tony "
        "Evers and the Wisconsin Department of Transportation announced in September 2026 that 42 locations across "
        "the state would receive grants to build fast charging stations, backed by $25 million in federal funding. "
        "That pattern gives charger suppliers a pipeline of site awards that arrive with funding already attached.",
        "Institutional buyers are also procuring directly. The Brooklyn Navy Yard Development Corporation sought "
        "private-sector partners in November 2026 to install and operate up to 30 charging stations at its "
        "industrial campus, with up to 20 spaces for tenants and up to 10 for public use. The Laguna Beach City "
        "Council approved agreements in October 2026 to install charging infrastructure at two locations for its "
        "municipal fleet.",
    ]},
]


def inp() -> SimpleNamespace:
    return SimpleNamespace(market_name=MARKET, items=copy.deepcopy(ITEMS))


def section(sop: str) -> dict:
    return copy.deepcopy(next(s for s in SECTIONS if s["sop"][0] == sop))


# ---- the figure's identity is its value with its unit ------------------------------------------------

check("figure keys: \"$25 million\" and \"€25 billion\" are different figures (the live U.S. collision)",
      writer._figure_keys("backed by $25 million in federal funding")
      != writer._figure_keys("at almost €25 billion of investment")
      and writer._figure_keys("$25 million") == {("25", "$ million")}
      and writer._figure_keys("€25 billion") == {("25", "€ billion")},
      str((writer._figure_keys("$25 million"), writer._figure_keys("€25 billion"))))

check("figure keys: \"30 states\" and \"30 charging stations\" are not figures at all (bare small integers)",
      not writer._figure_keys("across 30 states") and not writer._figure_keys("up to 30 charging stations"),
      str((writer._figure_keys("across 30 states"), writer._figure_keys("up to 30 charging stations"))))

check("figure keys: \"32 and 48 amps\", \"80 amps\" and \"Level 2\" are counts and labels, not figures",
      not writer._figure_keys("draw between 32 and 48 amps") and not writer._figure_keys("reaching 80 amps")
      and not writer._figure_keys("a Level 2 charger"),
      str((writer._figure_keys("draw between 32 and 48 amps"), writer._figure_keys("a Level 2 charger"))))

check("figure keys: a bare four-digit-plus number is a figure whose unit is the word after it",
      writer._figure_keys("added 4,382 new charging ports") == {("4382", "new")},
      str(writer._figure_keys("added 4,382 new charging ports")))

check("figure keys: \"48.5%\", \"$635 million\" and \"1,500 kW\" keep their units and years never count",
      writer._figure_keys("holding approximately 48.5% of value") == {("48.5", "%")}
      and writer._figure_keys("set aside about $635 million") == {("635", "$ million")}
      and writer._figure_keys("up to 1,500 kW through one cable") == {("1500", "kw")}
      and not writer._figure_keys("announced in September 2026"),
      str((writer._figure_keys("holding approximately 48.5% of value"), writer._figure_keys("up to 1,500 kW"))))

# ---- the live shapes: the strip removes nothing ------------------------------------------------------

kept = writer.strip_repeat_figures(copy.deepcopy(SECTIONS), inp(), LOG)
check("the strip changes no paragraph of any section",
      [s["paragraphs"] for s in kept] == [s["paragraphs"] for s in SECTIONS])
check("the U.S. section keeps its Wisconsin sentence (its $25 million met only Reuters' €25 billion)",
      any("Wisconsin Governor Tony Evers" in p for p in next(s for s in kept if s["sop"] == ["country_us"])["paragraphs"]))
check("the U.S. section keeps the sentence that followed it",
      any("That pattern gives charger suppliers" in p for p in next(s for s in kept if s["sop"] == ["country_us"])["paragraphs"]))
check("the U.S. section keeps its Brooklyn Navy Yard sentence (its 30 stations met only WRAL's 30 states)",
      any("Brooklyn Navy Yard Development Corporation sought private-sector partners" in p
          for p in next(s for s in kept if s["sop"] == ["country_us"])["paragraphs"]))
check("the U.S. section keeps the Laguna Beach sentence that followed it",
      any("Laguna Beach City Council approved agreements" in p
          for p in next(s for s in kept if s["sop"] == ["country_us"])["paragraphs"]))
check("the sub-segment section keeps its Fort Wayne sentence (its Level 2, 32-48 and 80 amps met nothing)",
      any("Fort Wayne electrician reported" in p
          for p in next(s for s in kept if s["sop"] == ["subsegment"])["paragraphs"]))

# ---- a genuine same-figure repeat is still removed when the section keeps other evidence --------------

REPEAT_SECTIONS = SECTIONS + [
    {"heading": "Key Players in the Electric Vehicle Charger Market", "sop": ["key_players"], "paragraphs": [
        "Canada set aside $635 million for charging infrastructure in its proposed budget.",
        "Retail operators added 4,382 new charging ports across 806 stations during the second quarter of 2026, "
        "Bisnow reported.",
    ]},
]
stripped = writer.strip_repeat_figures(copy.deepcopy(REPEAT_SECTIONS), inp(), LOG)
key_paragraphs = next(s for s in stripped if s["sop"] == ["key_players"])["paragraphs"]
check("a genuine repeat (the same $635 million printed earlier, its source unnamed) is removed",
      key_paragraphs == ["Retail operators added 4,382 new charging ports across 806 stations during the second "
                         "quarter of 2026, Bisnow reported."], str(key_paragraphs))

# ---- the wholesale guard: a section whose every citation is the repeat keeps it -----------------------

solo_sections = SECTIONS + [
    {"heading": "Key Players in the Electric Vehicle Charger Market", "sop": ["key_players"], "paragraphs": [
        "Canada set aside $635 million for charging infrastructure in its proposed budget.",
    ]},
]
kept_solo = writer.strip_repeat_figures(copy.deepcopy(solo_sections), inp(), LOG)
solo_paragraphs = next(s for s in kept_solo if s["sop"] == ["key_players"])["paragraphs"]
check("the wholesale guard: a section whose only citation is a figure repeat keeps it, for the check to report",
      solo_paragraphs == solo_sections[-1]["paragraphs"], str(solo_paragraphs))

# ---- the strips agree with the checks: the kept sentences carry the citations the guard saw ------------

kept_ids = content_rules.evidence_cited_ids(
    "\n".join(p for s in kept for p in s["paragraphs"]),
    [{"id": i["id"], "fact": i["text"]} for i in ITEMS.values()], MARKET)
check("the kept sentences still cite their own evidence (W22, W12, W3 among them)",
      {"W22", "W12", "W3"} <= kept_ids, str(sorted(kept_ids)))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all repeat-figure strip tests passed")
