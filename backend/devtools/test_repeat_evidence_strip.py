"""Offline regression for the repeat-evidence strip, from the live run that lost paragraphs (article "TEST 7 - EV home
charging", market "Electric Vehicle Charger Market", log 20261113_115916_run.log). The model's own write output was whole,
but the size section's sentences that only name the market phantom-"cited" two news facts — the lone word "Electric" out
of "Pacific Gas and Electric", and the region "North America" out of a U.S. fact — so those facts' first citations sat in
a section that never used them, and strip_repeat_evidence then removed every later true citation: the U.S. section fell
to one sentence, the trends lead and trend 1 lost their evidence paragraphs, the sub-segment and key-players sections
lost their lead sentences, and the draft came out ~200 words short. These tests hold that the phantom matches stay dead,
a section that only names the market cites no evidence, the strip keeps every sentence the model wrote for those
sections, a genuine cross-section repeat is still removed, and a section whose every citation the removal would take
keeps them for the evidence-reuse check to report instead.
Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_repeat_evidence_strip.py   (-v prints the log)"""
import copy
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import content_rules  # noqa: E402
import pipeline  # noqa: E402
import writer  # noqa: E402

LOG = logging.getLogger("test_repeat_evidence_strip")
LOG.setLevel(logging.INFO)
LOG.addHandler(logging.StreamHandler(sys.stdout) if "-v" in sys.argv else logging.NullHandler())
LOG.propagate = False
FAILURES: list[str] = []
MARKET = "Electric Vehicle Charger Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# The news facts the run's Evidence step handed the writer, exactly as the log's [select] PARSED JSON gives them.
W_ITEMS = {
    "W3": "Schneider Electric launched Schneider Charge, a residential Level 2 EV charger with built-in SmartAmp dynamic load management, in Canada.",
    "W30": "Infineon partnered with VSPE on a single-stage EV onboard charger design that combines power-conversion functions conventional two-stage systems manage separately.",
    "W28": "Lunar Energy unveiled a 25-kW home DC fast charger that pairs with its next-generation home battery system.",
    "W29": "BYD deployed T-shaped Flash chargers that draw power from grid or on-site storage batteries and can send up to 1,500 kW through one cable.",
    "W31": "Canada's proposed federal budget set aside about $635 million specifically for EV charging infrastructure, including $587 million to renew the Zero-Emission Vehicle Infrastructure Program.",
    "W26": "Public charging networks continue to grow across North America, with new stations and higher-powered DC fast chargers coming online regularly, according to an EV charging infrastructure map covering more than 120,000 stations.",
    "W15": "The Brooklyn Navy Yard Development Corporation sought private-sector partners to install and operate up to 30 electric vehicle charging stations at its industrial campus.",
    "W2": "General Motors and Pacific Gas and Electric launched a promotional bundle offering an electric vehicle charger, a monthly bill credit and support at certain dealers in California.",
    "W43": "Siemens launched the RapidSBx switchboard designed to handle power distribution for EV charging stations and other commercial electrical infrastructure.",
    "W27": "Wallbox expanded its partnership with Codale Electric Supply to accelerate EV charging deployment across the Mountain West.",
    "W6": "Toyota Motor North America partnered with Treehouse to simplify the home EV charger purchase and installation process for Toyota and Lexus customers.",
}

# The sections the model wrote, the log's [write] PARSED JSON with only the untouched trends trimmed. Every
# sentence here is the model's own; the bug is that the strips deleted most of them.
SECTIONS = [
    {"heading": "How big is the electric vehicle charger market?", "sop": ["report_context"], "paragraphs": [
        "CMI values the Electric Vehicle Charger Market at USD 19.9 Bn in 2026, a figure that sets the size of the equipment and infrastructure segment serving electric mobility worldwide. CMI's report forecasts the market to reach USD 117.6 Bn by 2033, a 28.9% compound annual growth rate (CAGR) across 2026 to 2033. That trajectory reflects demand from network operators, utilities and households installing charging equipment at scale. Asia Pacific leads, holding approximately 48.5% of the electric vehicle charger market in Asia Pacific in 2026, while North America grows fastest over 2026 to 2033. The regional split points to Asia Pacific as the volume base for charger production and deployment, and to North America as the market where new capacity is being added most quickly.",
    ]},
    {"heading": "What are the latest trends in the electric vehicle charger market?", "sop": ["trends"], "paragraphs": [
        "Three trends are reshaping the Electric Vehicle Charger Market: charging power is rising toward fast and ultra-fast equipment, public funding and regulation are widening rollout, and hardware and software are becoming smarter and more interoperable. Together they raise the throughput each charger must deliver and the investment confidence behind each site.",
        "### Higher power moves fast charging from highways into homes and commercial sites",
        "Public charging operators, automakers, utilities and fuel retailers are deploying fast and ultra-fast chargers along highways, urban corridors, shopping centres and commercial locations to shorten charging times and raise charger throughput, CMI reports. This deployment shifts demand toward higher-power direct current (DC) equipment and the grid capacity that supports it.",
        "Higher-power equipment changes what a site can earn per hour, because shorter sessions serve more vehicles from the same connection. That mechanism draws utilities and fuel retailers into charging as a commercial activity rather than a courtesy, and it raises the specification buyers set when they procure stations.",
        "Providers can position high-power stations for corridor and retail locations where dwell time is short, and investors can read station power ratings as a signal of site economics. Equipment that manages load alongside on-site storage becomes easier to justify at constrained sites.",
        "### Storage-backed charging lifts the power ceiling at grid-limited sites",
        "In November 2026, InsideEVs reported that BYD deployed T-shaped Flash chargers drawing power from the grid or on-site storage batteries, sending up to 1,500 kW through one cable. Each unit connects to two 185-kilowatt-hour storage batteries, and sites operate in Italy, Germany, France and the United Kingdom.",
        "Pairing chargers with batteries decouples delivered power from the local grid connection, which widens the range of sites that can host ultra-fast charging. Automotive companies and charging operators gain a route to high-power service where grid upgrades would otherwise set the limit.",
        "Providers can market storage-integrated systems to operators facing capacity constraints, and site owners can plan high-power stations around available land rather than substation headroom. Buyers gain a way to specify power levels that match vehicle capability.",
        "### Public funding gives network buildout a firmer pipeline",
        "Canada's proposed federal budget set aside about $635 million for electric vehicle charging infrastructure, including $587 million to renew the Zero-Emission Vehicle Infrastructure Program, Electrek reported in November 2026. The budget proposes more than 55,000 new chargers, with electricity infrastructure expansion running from 2027 through 2036.",
        "Earmarked public funds give charging-network operators, utilities and infrastructure developers visibility on which projects will proceed, which CMI identifies as a driver of investment in the market. Committed budgets convert into equipment orders and installation contracts on a schedule buyers can plan around.",
        "Providers can align product roadmaps and service capacity with funded programmes, and developers can sequence site acquisition against announced disbursement windows. Buyers gain a clearer basis for negotiating multi-site supply agreements.",
    ]},
    {"heading": "What are the main segments of the electric vehicle charger market?", "sop": ["segmentation"], "paragraphs": [
        "CMI's segmentation of the Electric Vehicle Charger Market divides it by vehicle type, charging type and end user, three splits that together describe who the equipment serves and where it sits.\n- By vehicle type, the report separates battery electric vehicles (BEV), plug-in electric vehicles (PIV) and hybrid electric vehicles (HEV); BEVs lead at approximately 57.0% in 2026, because they rely entirely on external charging and carry larger battery capacities.\n- By charging type, it distinguishes on-board chargers from off-board chargers, with on-board chargers the leading category.\n- By end user, it separates residential from commercial buyers, and residential charging leads this split.",
    ]},
    {"heading": "Sub-Segment Analysis of the Electric Vehicle Charger Market", "sop": ["subsegment"], "paragraphs": [
        "On-board chargers hold approximately 89.0% of the Electric Vehicle Charger Market's charging-type split in 2026, and CMI identifies them as the leading category. The share follows from where the conversion happens: on-board alternating current (AC) to direct current (DC) systems sit inside nearly every BEV and plug-in hybrid, and Level 1 and Level 2 charging is widely used at homes, workplaces and destination locations. That placement gives the sub-segment a base in vehicle production rather than in site investment, so its demand tracks electric vehicle output. Component suppliers see the opportunities first, and in November 2026 Just Auto on MSN reported that Infineon partnered with VSPE on a single-stage on-board charger design that combines power-conversion functions that two-stage systems manage separately. Designs of this kind point to on-board chargers in electric vehicles becoming more integrated, which supports cost and packaging gains for automakers specifying them.",
        "Residential buyers account for approximately 74.0% of the Electric Vehicle Charger Market's end-user split in 2026, the leading category in CMI's segmentation. Overnight charging at home is convenient and draws on electricity priced below public fast charging, and rising installation of private wall-mounted chargers and growing ownership of single-family charging systems sustain the category. Equipment suppliers are responding with products built for the household supply: in August 2026, Morningstar, Inc. reported that Schneider Electric launched Schneider Charge, a residential Level 2 charger with built-in SmartAmp dynamic load management, in Canada. Load management of this kind lets a household add charging capacity without a service upgrade, which widens the set of homes that can adopt the sub-segment and keeps residential demand central to the market.",
    ]},
    {"heading": "U.S. Analysis of the Electric Vehicle Charger Market", "sop": ["country_us"], "paragraphs": [
        "The U.S. Electric Vehicle Charger Market is experiencing structural growth, CMI reports, driven by federal infrastructure programs, rising utilisation of publicly funded chargers, more high-power station deployment and continued standardisation of charging hardware and software. Those drivers give providers a predictable basis for capacity planning and give site operators a reason to commit space to charging.\n- In August 2026, Electrical Construction & Maintenance reported that public charging networks continue to grow across North America, with new stations and higher-powered DC fast chargers coming online regularly, citing an infrastructure map covering more than 120,000 stations.\n- In November 2026, BKReader reported that the Brooklyn Navy Yard Development Corporation sought private-sector partners to install and operate up to 30 charging stations at its industrial campus, with up to 20 spaces for tenants and up to 10 for public use.\n- In September 2026, MarketWatch reported that General Motors and Pacific Gas and Electric launched a promotional bundle offering a charger, a monthly bill credit and support at certain dealers in California.\nProviders can target workplace and industrial campuses where tenants need charging, and equipment vendors can work with utilities and automakers on bundled offers that lower the entry cost for buyers.",
    ]},
    {"heading": "Key Players in the Electric Vehicle Charger Market", "sop": ["key_players"], "paragraphs": [
        "CMI names ABB, Robert Bosch, Siemens, Delphi Automotive, Chroma ATE, Aerovironment, Silicon Laboratories, Chargemaster, Schaffner and POD Point among the key players in the Electric Vehicle Charger Market, a field that spans power electronics, components and network operation rather than resting with a handful of suppliers. That breadth means buyers can source hardware and components from several tiers of supplier, and competition turns on integration and site readiness as much as on unit price.",
        "Siemens launched the RapidSBx switchboard in October 2026, Charged EVs reported, a board designed to handle power distribution for charging stations and other commercial electrical infrastructure and built to UL 891 with NEVI compatibility. The product gives site developers a pre-engineered distribution option for airports, shopping malls and grocery stores, which shortens the path from site selection to operating station. Wallbox expanded its partnership with Codale Electric Supply in November 2025 to accelerate charging deployment across the Mountain West, Business Wire reported, extending distribution reach for its equipment. In February 2026, Nasdaq reported that Toyota Motor North America partnered with Treehouse to simplify home charger purchase and installation for Toyota and Lexus customers, which lowers the effort a household faces when adding residential charging.",
    ]},
    {"heading": "Conclusion and Outlook for the Electric Vehicle Charger Market", "sop": ["conclusion"], "paragraphs": [
        "The outlook for the Electric Vehicle Charger Market follows the forces already described: higher-power equipment, funded buildout and standardised, connected hardware. Updated U.S. National Electric Vehicle Infrastructure (NEVI) guidance is expected to speed development and approval, letting states obligate funds and begin charger agreements sooner, which opens work for network providers, charger producers, engineering companies, fuel retailers and commercial site operators. Building regulations that require charger installation in new and substantially renovated properties should raise demand for connected AC wallboxes, energy-management systems and shared charging equipment in residential and commercial buildings. A CMI analyst judges that the market's next phase will reward suppliers who pair compliant hardware with installation and energy management, since buyers increasingly purchase an operating charging capability rather than a standalone unit.",
    ]},
]


def items_for(strip: bool = False) -> list | dict:
    """The evidence as content_rules.evidence_reuse ({id, fact}) or as strip_repeat_evidence's PlanInputs ({id, kind, text})."""
    if strip:
        return {wid: {"id": wid, "kind": "evidence", "text": fact} for wid, fact in W_ITEMS.items()}
    return [{"id": wid, "fact": fact} for wid, fact in W_ITEMS.items()]


def inp() -> SimpleNamespace:
    return SimpleNamespace(market_name=MARKET, items=items_for(strip=True))


def section(sop: str) -> dict:
    return next(s for s in SECTIONS if s["sop"][0] == sop)


# ---- the phantom citations stay dead -----------------------------------------------------------------

SIZE_PARAGRAPH = section("report_context")["paragraphs"][0]

check("no phantom citation: the size section that only names the market cites neither W2 nor W26",
      not {"W2", "W26"} & pipeline._evidence_used_ids(SIZE_PARAGRAPH, items_for()),
      str(pipeline._evidence_used_ids(SIZE_PARAGRAPH, items_for())))

check("no phantom citation: 'Electric' out of 'Pacific Gas and Electric' is not an identity entity",
      "W2" not in pipeline._evidence_used_ids("The Electric Vehicle Charger Market grows each year, CMI reports.", items_for()))

check("no phantom citation: the region 'North America' out of the U.S. fact is not an identity entity",
      "W26" not in pipeline._evidence_used_ids(
          "North America grows fastest over 2026 to 2033, with public funding behind the buildout, CMI reports.", items_for()))

# ---- the true citations are still found --------------------------------------------------------------

us_line = next(line for p in section("country_us")["paragraphs"] for line in p.split("\n") if line.startswith("- In September"))
check("a true citation is found: the U.S. bullet that names General Motors and Pacific Gas and Electric cites W2",
      "W2" in pipeline._evidence_used_ids(us_line, items_for()))
us_line_navy = next(line for p in section("country_us")["paragraphs"] for line in p.split("\n") if line.startswith("- In November"))
check("a true citation is found: the Brooklyn Navy Yard bullet cites W15",
      "W15" in pipeline._evidence_used_ids(us_line_navy, items_for()))
trend_line = next(p for p in section("trends")["paragraphs"] if p.startswith("In November 2026, InsideEVs"))
check("a true citation is found: the BYD trend paragraph cites W29",
      "W29" in pipeline._evidence_used_ids(trend_line, items_for()))
check("a true citation is found: two figures of one fact still identify it (Canada's $635 million and $587 million)",
      "W31" in pipeline._evidence_used_ids(
          "Canada's proposed federal budget set aside about $635 million for charging infrastructure, including $587 million to renew the programme.", items_for()))
check("a true citation is found: a lone distinctive company name still identifies its fact (Siemens)",
      "W43" in pipeline._evidence_used_ids(
          "Siemens launched the RapidSBx switchboard to handle power distribution for charging stations.", items_for()))

# ---- the live shapes produce no repeat rows, and the strip removes nothing ---------------------------

rows = content_rules.evidence_reuse(copy.deepcopy(SECTIONS), items_for(), MARKET)
check("evidence_reuse: the live sections give no repeat rows", not rows, str(rows[:3]))

kept = writer.strip_repeat_evidence(copy.deepcopy(SECTIONS), inp(), LOG)
check("the strip changes no paragraph of any section",
      [s["paragraphs"] for s in kept] == [s["paragraphs"] for s in SECTIONS])
check("the trends lead keeps its first sentence (the one naming the three trends)",
      any("Three trends are reshaping the Electric Vehicle Charger Market" in p for p in next(s for s in kept if s["sop"] == ["trends"])["paragraphs"]))
check("trend 1 keeps its evidence paragraph",
      any(p.startswith("Public charging operators, automakers, utilities and fuel retailers are deploying")
          for p in next(s for s in kept if s["sop"] == ["trends"])["paragraphs"]))
check("the U.S. section keeps its lead and all three bullets",
      sum(p.count("\n- ") for p in next(s for s in kept if s["sop"] == ["country_us"])["paragraphs"]) == 3)
check("the sub-segment section keeps both lead sentences",
      all(any(snippet in p for p in next(s for s in kept if s["sop"] == ["subsegment"])["paragraphs"])
          for snippet in ("On-board chargers hold approximately 89.0%", "Residential buyers account for approximately 74.0%")))
check("the key-players section keeps its naming paragraph",
      any("CMI names ABB, Robert Bosch, Siemens" in p for p in next(s for s in kept if s["sop"] == ["key_players"])["paragraphs"]))

# ---- a genuine cross-section repeat is still removed, the first citation stays ------------------------

REPEAT_FACT = "Spin.AI acquired Revyz to extend data resiliency and security posture management to the Atlassian ecosystem."
OTHER_FACT = "Asana acquired StackAI to expand cross-system AI workflow automation."
repeat_sections = [
    {"heading": "What are the latest trends?", "sop": ["trends"], "paragraphs": [
        "In November 2026, Pulse 2.0 reported that Spin.AI acquired Revyz to extend data resiliency and security posture management to the Atlassian ecosystem, which widens the assurance a buyer can attach to Jira deployments.",
    ]},
    {"heading": "Key players", "sop": ["key_players"], "paragraphs": [
        "Asana completed the acquisition of StackAI in May 2026, adding a no-code AI workflow platform for enterprise teams.",
        "The acquisition extends data resiliency and security posture management around Atlassian deployments, which supports buyers weighing governed automation inside a platform they already use.",
    ]},
]
repeat_items = {"W35": {"id": "W35", "kind": "evidence", "text": REPEAT_FACT},
                "W34": {"id": "W34", "kind": "evidence", "text": OTHER_FACT}}
repeat_inp = SimpleNamespace(market_name="Agile Project Management Software Market", items=repeat_items)
stripped = writer.strip_repeat_evidence(copy.deepcopy(repeat_sections), repeat_inp, LOG)
check("a genuine repeat is removed and the first citation stays",
      [s["paragraphs"] for s in stripped] == [repeat_sections[0]["paragraphs"], repeat_sections[1]["paragraphs"][:1]])

# ---- a section whose every citation the removal would take keeps them, for the check to report --------

solo_sections = [
    {"heading": "What are the latest trends?", "sop": ["trends"], "paragraphs": [
        "In November 2026, Pulse 2.0 reported that Spin.AI acquired Revyz to extend data resiliency and security posture management to the Atlassian ecosystem, which widens the assurance a buyer can attach to Jira deployments.",
    ]},
    {"heading": "Key players", "sop": ["key_players"], "paragraphs": [
        "The acquisition strengthens the assurance available around Atlassian deployments, which supports buyers weighing governed automation inside a platform they already use.",
    ]},
]
kept_solo = writer.strip_repeat_evidence(copy.deepcopy(solo_sections), repeat_inp, LOG)
check("the wholesale guard: a section whose only citation is a repeat keeps it",
      kept_solo[1]["paragraphs"] == solo_sections[1]["paragraphs"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all repeat-evidence strip tests passed")
