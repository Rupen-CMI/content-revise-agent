"""Offline test for the "field-by-field figures" web fallback: headline figures for a non-CMI sheet (or a run with
no report at all) filled from the market's own figures on one web-searched outside page. No network, no LLM.

Run from backend/: python devtools/test_gap_external.py
"""
import copy
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import facts  # noqa: E402
import llm  # noqa: E402
import search  # noqa: E402
import variance  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_gap_external")
TODAY = date.today()
Y = TODAY.year
Y2 = Y + 7

MARKET = "Human Translation Market"
URL = f"https://sizing.example/{Y}/human-translation-market"
QUOTE = (
    f"The Human Translation Market is estimated to be valued at USD 500.0 million in {Y} and is expected to reach "
    f"USD 850.0 million by {Y2}, growing at a CAGR of 7.2% from {Y} to {Y2}"
)
PAGE_TEXT = QUOTE + ". Analysts expect steady demand."
RAW_SOURCE = {
    "id": "F1",
    "size": {"value": "USD 500.0 million", "year": str(Y), "quote": QUOTE},
    "forecast": {"value": "USD 850.0 million", "year": str(Y2), "quote": QUOTE},
    "cagr": {"value": "7.2%", "period": f"{Y} to {Y2}", "quote": QUOTE},
}


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def hit() -> search.SearchHit:
    return search.SearchHit(
        title="Human translation market size and forecast report",
        snippet=PAGE_TEXT, url=URL, source="Sizing Example", date=datetime.now(timezone.utc),
    )


def empty_sheet() -> dict:
    fs = facts._empty_fact_sheet()
    fs.update({"sources": [], "variance": [], "report_mode": "external"})
    return fs


# ------------------------------------------------------------------- apply_figure_source(external=...)
model = dict(RAW_SOURCE)
for external in (True, False):
    fs = empty_sheet()
    facts.apply_figure_source(fs, model, ["size", "forecast", "cagr"], [hit()], MARKET, [], LOG, TODAY, external)
    check(f"apply_figure_source external={external}: size accepted with its source", fs["figure_source"]["url"] == URL and fs["size"]["url"] == URL)
    check(f"apply_figure_source external={external}: the entry is{' ' if external else ' not '}marked external",
          bool(fs["size"].get("external")) == external)

# ------------------------------------------------------------------------------- vary_headline_entries
def ext_entry(value: str, when: str, **extra) -> dict:
    return {"value": value, "when": when, "external": True, **extra}


SOURCE = {"name": "Sizing Example", "host": "sizing.example", "url": URL, "figures": ["size", "forecast", "cagr"]}

fs = empty_sheet()
fs.update({
    "size": ext_entry("USD 500.0 million", str(Y)),
    "forecast": ext_entry("USD 850.0 million", str(Y2)),
    "cagr": ext_entry("7.2%", f"{Y} to {Y2}"),
})
before = copy.deepcopy({k: fs[k] for k in ("size", "forecast", "cagr")})
audit = variance.vary_headline_entries(fs, MARKET, SOURCE)
size_ratio = variance._amount(fs["size"]["value"]) / 500.0
forecast_ratio = variance._amount(fs["forecast"]["value"]) / 850.0
check("vary_headline_entries: size moves by 3 to 5 percent", 0.029 <= abs(size_ratio - 1) <= 0.051, str(size_ratio))
check("vary_headline_entries: forecast moves by 3 to 5 percent", 0.029 <= abs(forecast_ratio - 1) <= 0.051, str(forecast_ratio))
cagr_v = float(fs["cagr"]["value"].rstrip("%"))
recomputed = ((variance._amount(fs["forecast"]["value"]) / variance._amount(fs["size"]["value"])) ** (1 / 7) - 1) * 100
check("vary_headline_entries: the growth rate agrees with the moved size and forecast", abs(cagr_v - recomputed) < 0.06, f"{cagr_v} vs {recomputed:.2f}")
check("vary_headline_entries: entries are marked varied", all(fs[k].get("varied") for k in ("size", "forecast", "cagr")))
check("vary_headline_entries: the original is kept beside the shown value", fs["size"]["original_value"] == "USD 500.0 million")
check("vary_headline_entries: the audit is appended to fs['variance'] (created when missing) and carries the url",
      fs["variance"] == audit and len(audit) == 3 and all(r["url"] == URL for r in audit))
check("vary_headline_entries: the audit rows keep field, original and shown", audit[0]["field"] == "size" and audit[0]["original"] == "USD 500.0 million")

snapshot = copy.deepcopy({k: fs[k] for k in ("size", "forecast", "cagr")})
again = variance.vary_headline_entries(fs, MARKET, SOURCE)
check("vary_headline_entries: a second call changes nothing and returns no rows",
      again == [] and snapshot == {k: fs[k] for k in ("size", "forecast", "cagr")})
check("vary_headline_entries: the audit was not doubled", len(fs["variance"]) == 3)

cmi = {"size": {"value": "USD 500.0 million", "when": str(Y)}, "forecast": {"value": "USD 850.0 million", "when": str(Y2)},
       "cagr": {"value": "7.2%", "when": f"{Y} to {Y2}"}, "variance": []}
check("vary_headline_entries: entries without the external mark are left alone", variance.vary_headline_entries(cmi, MARKET, SOURCE) == [] and cmi["size"]["value"] == "USD 500.0 million")

mixed = {"size": ext_entry("USD 500.0 million", str(Y), varied=True, original_value="USD 500.0 million"),
         "forecast": ext_entry("USD 850.0 million", str(Y2)), "cagr": None}
mixed_audit = variance.vary_headline_entries(mixed, MARKET, SOURCE)
check("vary_headline_entries: an already-varied entry is skipped, unvaried external ones still move",
      [r["field"] for r in mixed_audit] == ["forecast"] and mixed["size"]["value"] == "USD 500.0 million")
check("vary_headline_entries: no source url means no rows", variance.vary_headline_entries(mixed, MARKET, {"name": "x"}) == [])

# ------------------------------------------------------------------------------ fill_headline_from_web
real_search, real_chat = facts.run_figure_gap_search, llm.chat_json


def stub_off():
    facts.run_figure_gap_search, llm.chat_json = real_search, real_chat


# (a) never mix: one headline figure already present -> nothing is called, the sheet is unchanged
def refuse(*a, **k):
    raise AssertionError("gap search must not run when a headline figure exists")


facts.run_figure_gap_search, llm.chat_json = refuse, refuse
try:
    fs = empty_sheet()
    fs["size"] = ext_entry("USD 400.0 million", str(Y))
    out = facts.fill_headline_from_web(fs, MARKET, [], LOG)
finally:
    stub_off()
check("fill: a sheet that already states one headline figure is never mixed with a second page",
      out == [] and fs["forecast"] is None and fs["cagr"] is None and fs["size"]["value"] == "USD 400.0 million" and fs["figure_source"] is None and fs["sources"] == [])

# (b) the search finds nothing: the facts stay unstated, no warning
facts.run_figure_gap_search, llm.chat_json = lambda *a, **k: [], real_chat
try:
    fs = empty_sheet()
    out = facts.fill_headline_from_web(fs, MARKET, [], LOG)
finally:
    stub_off()
check("fill: no candidate page leaves the sheet untouched and warns of nothing",
      out == [] and fs["gaps"] == ["size", "forecast", "cagr"] and fs["sources"] == [] and fs["figure_source"] is None)

# (c) all three missing: one page is searched, read, accepted, marked external and varied
gapped = empty_sheet()
call_log: list[str] = []


def fake_chat_json(system, user, **kwargs):
    call_log.append(user[:120])
    return {"source": dict(RAW_SOURCE)}


facts.run_figure_gap_search, llm.chat_json = lambda *a, **k: [hit()], fake_chat_json
try:
    out = facts.fill_headline_from_web(gapped, MARKET, [], LOG)
finally:
    stub_off()
check("fill: the sheet states all three headline figures after the web fill",
      gapped["size"] and gapped["forecast"] and gapped["cagr"] and not gapped["gaps"])
check("fill: the accepted figures are marked external AND varied", gapped["size"]["external"] and gapped["size"]["varied"])
size_ratio = variance._amount(gapped["size"]["value"]) / 500.0
forecast_ratio = variance._amount(gapped["forecast"]["value"]) / 850.0
check("fill: size and forecast were varied by 3 to 5 percent",
      0.029 <= abs(size_ratio - 1) <= 0.051 and 0.029 <= abs(forecast_ratio - 1) <= 0.051, f"{size_ratio}, {forecast_ratio}")
cagr_v = float(gapped["cagr"]["value"].rstrip("%"))
recomputed = ((variance._amount(gapped["forecast"]["value"]) / variance._amount(gapped["size"]["value"])) ** (1 / 7) - 1) * 100
check("fill: the recomputed growth rate agrees with the moved pair", abs(cagr_v - recomputed) < 0.06, f"{cagr_v} vs {recomputed:.2f}")
check("fill: the outside page is appended to the sources once, marked external",
      gapped["sources"] == [{"name": "sizing.example", "url": URL, "kind": "external"}] and gapped["figure_source"]["url"] == URL)
check("fill: one warning tells the editor where the figures came from and that they moved",
      len(out) == 1 and "web search" in out[0] and "3-5%" in out[0], str(out))
check("fill: varying is seeded from market + url, so the same page always shows the same figures",
      variance.vary_headline_entries(gapped, MARKET, gapped["figure_source"]) == [] and variance._amount(gapped["size"]["value"]) == size_ratio * 500.0)

# a second call is still a no-op (the sheet now has headline figures, so nothing is re-mixed)
facts.run_figure_gap_search, llm.chat_json = refuse, refuse
try:
    out2 = facts.fill_headline_from_web(gapped, MARKET, [], LOG)
finally:
    stub_off()
check("fill: a filled sheet is not touched again", out2 == [] and gapped["sources"] == [{"name": "sizing.example", "url": URL, "kind": "external"}])

# ------------------------------------------------------------------ vary_fact_sheet behaviour unchanged
RAW_PAGE = (
    "The Human Translation Market is estimated to be valued at USD 500.0 million in 2025 and is expected to reach "
    "USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032. Demand grew 12 percent among hospitals in 2025."
)
RAW_QUOTE = ("The Human Translation Market is estimated to be valued at USD 500.0 million in 2025 and is expected to "
             "reach USD 800.0 million by 2032, growing at a CAGR of 6.9% from 2025 to 2032")
RAW_RAW = {
    "size": {"value": "USD 500.0 million", "year": "2025", "quote": RAW_QUOTE},
    "forecast": {"value": "USD 800.0 million", "year": "2032", "quote": RAW_QUOTE},
    "cagr": {"value": "6.9%", "period": "2025 to 2032", "quote": RAW_QUOTE},
}
outside = {"name": "rival.example", "url": "https://rival.example/human-translation-market"}
outsheet = facts.validate_fact_sheet(RAW_RAW, RAW_PAGE, MARKET, LOG, TODAY, outside=outside)
outsheet["drivers"] = []
outsheet["segments"] = []
rows = variance.vary_fact_sheet(outsheet, MARKET)
check("regression: vary_fact_sheet still varies an outside sheet", len(rows) == 3 and outsheet["size"]["value"] != "USD 500.0 million")
check("regression: after vary_fact_sheet, vary_headline_entries does not move the figures again",
      variance.vary_headline_entries(outsheet, MARKET, SOURCE) == []
      and variance._amount(outsheet["size"]["value"]) != 500.0 and len(outsheet["variance"]) == 3)

cmi_sheet = facts.validate_fact_sheet(RAW_RAW, RAW_PAGE, MARKET, LOG, TODAY)
check("regression: vary_fact_sheet still leaves a CMI sheet alone",
      variance.vary_fact_sheet(cmi_sheet, MARKET) == [] and cmi_sheet["size"]["value"] == "USD 500.0 million")
check("regression: a CMI sheet is untouched by vary_headline_entries too",
      variance.vary_headline_entries(cmi_sheet, MARKET, SOURCE) == [] and cmi_sheet["size"]["value"] == "USD 500.0 million")

# --------------------------------------------------------------- gap-fill entries flatten as plain facts
flat = {f["id"]: f for f in facts.flatten_fact_sheet(gapped)}
check("flatten: a gap figure from the web reaches the writer as a plain statement with no source named (never CMI)",
      flat["fs.size"]["source"] == "" and "Market size" in flat["fs.size"]["text"] and "500.0" not in flat["fs.size"]["text"])

# --------------------------------------------------------------- two outside reports: one split is listed once
def _sheet(*splits):
    sh = facts._empty_fact_sheet()
    sh["segments"] = [{"id": f"fs.segments.{i}", "type": ty, "items": items, "figures": {}, "dominant": None} for i, (ty, items) in enumerate(splits)]
    return sh


_a = _sheet(("Organization Size", ["Small and Medium Enterprises", "Large Enterprises"]),
            ("End-User Industry", ["E-commerce and Retail", "IT and Telecom", "Hospitality and Travel", "Real Estate and Construction", "Healthcare and Life-Sciences", "BFSI", "Education"]))
_b = _sheet(("Business Size", ["Small Enterprises", "Medium-Sized Enterprises", "Large Corporations"]),
            ("Industry Vertical", ["E-commerce", "Healthcare", "Education", "Travel and Hospitality", "Finance and Insurance", "Real Estate"]),
            ("Client Type", ["B2B Clients", "B2C Clients", "Government Agencies"]))
_m = facts.merge_fact_sheets([_a, _b], [{"name": "a", "url": "u", "kind": "external"}, {"name": "b", "url": "v", "kind": "external"}])
check("merge: a split the second report lists under another name is not added again, a new split is",
      [s["type"] for s in _m["segments"]] == ["Organization Size", "End-User Industry", "Client Type"], str([s["type"] for s in _m["segments"]]))
check("merge: the kept splits are numbered without gaps", [s["id"] for s in _m["segments"]] == ["fs.segments.0", "fs.segments.1", "fs.segments.2"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all gap-external tests passed")
