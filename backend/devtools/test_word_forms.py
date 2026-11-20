"""Offline checks for the shared word-form normaliser (search.word_keys and friends) and the hit gate built on it.
A live Electric Vehicle Charger run dropped 166 hits as "no topical overlap", many on-target ("EV charging demand grew 8%
..."), because every market-word comparison was on exact surface tokens: 'charging' != head 'charger', 'EV' != required
['electric', 'vehicle'], and a topic term 'ev charger' did not match 'EV charging'.
Run from backend/: python devtools/test_word_forms.py"""
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import search  # noqa: E402

LOG = logging.getLogger("t")
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("PASS  " if ok else "FAIL  ") + name + ("" if ok else f"  -> {detail}"))
    if not ok:
        FAILED.append(name)


NOW = datetime.now(timezone.utc) - timedelta(days=3)


def hit(title: str, url: str, snippet: str = "") -> search.SearchHit:
    return search.SearchHit(title, snippet, url, "", NOW, is_news=True)


# --- the normaliser itself ---
for a, b in [("charger", "charging"), ("chargers", "charging"), ("charge", "charging"), ("printer", "printing"),
             ("scanner", "scanning"), ("testing", "tests"), ("translation", "translator"),
             ("translated", "translation"), ("services", "service"), ("utilities", "utility")]:
    check(f"same word: {a} ~ {b}", search.same_word(a, b))
for a, b in [("project", "projector"), ("market", "marketer"), ("charge", "charger"), ("web", "website"),
             ("station", "state"), ("assessment", "assess"), ("string", "stroke")]:
    check(f"different word: {a} != {b}", not search.same_word(a, b))
check("stem_hit: short stem still whole word only", not search.stem_hit("website", "web") and search.stem_hit("webs", "web"))

abbr = search.abbreviations_for(["Electric Vehicle Charger Market"])
check("abbreviation derived from the market name", abbr.get("ev") == ("electric", "vehicle"), str(abbr))
check("abbreviation read only when upper case",
      search.abbreviation_words("EV charging and EVs", abbr) == {"electric", "vehicle"}
      and search.abbreviation_words("ev charging, 5 pm", abbr) == set())

pats = search.compile_topic_terms(["ev charger"])
check("topic term 'ev charger' matches 'EV charging' and 'EV chargers'",
      all(any(rx.search(t) for _, rx in pats) for t in ("ev charging demand", "new ev chargers", "ev smart charging")))
check("topic term 'ev charger' does not match 'ev charter'", not any(rx.search("ev charter") for _, rx in pats))


# --- the EV Charger market gate with its topic terms (as in the live run: no 'ev charging' among them) ---
EV_PHRASES = ["Electric Vehicle Charger Market"]
EV_TERMS = ["ev charger", "charging station", "ev charging network", "dc fast charging", "charge point operator",
            "level 2 charger"]


def ev_kept(*hits):
    return {h.title for h in search.filter_hits(list(hits), EV_PHRASES, logger=LOG, topic_terms=EV_TERMS)}


on_target = [
    hit("EV charging demand grew 8% as new EV sales fell 19% after the credit ended", "https://www.msn.com/a"),
    hit("Paua and Powerverse partner to simplify home EV charging for fleets", "https://www.chargedevs.com/b"),
    hit("Lamppost EV Charging Joins Curbside Charging To Open Urban Charging Bottlenecks", "https://www.chargedevs.com/c"),
    hit("The United States of NEVI: The Current Outlook on the NEVI Program at the State-Level",
        "https://www.chargedevs.com/d", "The NEVI program funds EV charging stations along highway corridors in each state."),
    hit("Senate Republicans Seek to Again Raid States' Coffers & Threaten EV Charging", "https://www.chargedevs.com/e"),
]
got = ev_kept(*on_target)
check("EV charger gate keeps the five on-target headlines", got == {h.title for h in on_target},
      str({h.title for h in on_target} - got))

# A headline that mentions charging and electric vehicles (a credit scheme) is on-topic enough for the gate: the
# word-form rule says it names the charging of an EV. SELECT, the LLM judge, decides whether it is useful.
bmw = hit("BMW Offers $500 in Free Charging Credits With Its 2027 Electric Vehicles", "https://www.chargedevs.com/f")
check("EV charging-credit headline is kept (names charging and electric vehicles)", bmw.title in ev_kept(bmw))
waymo = hit("Waymo pulls driverless taxis from all US freeways", "https://www.chargedevs.com/g")
ev_only = hit("Tesla recalls 40,000 electric vehicles over a seat belt warning light fault", "https://www.reuters.com/h")
check("clearly off-topic headlines are dropped", ev_kept(waymo) == set())
check("an electric-vehicle headline with no charging word is dropped", ev_kept(ev_only) == set(), str(ev_kept(ev_only)))


# --- guards that must survive ---
# 'projector' is not a 'project' word: a project-management market must not keep a projector story.
pm_hits = [hit("Epson unveils a laser projector for classrooms and home theaters", "https://www.theverge.com/p"),
           hit("Epson launches a new projector line for schools this autumn term", "https://www.reuters.com/q")]
pm_kept = search.filter_hits(pm_hits, ["Project Management Software Market"], logger=LOG,
                             topic_terms=["project management", "task tracking", "gantt chart"])
check("projector headline dropped for a project-management market", pm_kept == [], str([h.title for h in pm_kept]))
pm_real = hit("Asana adds AI features to its project management platform", "https://www.techcrunch.com/r")
check("project-management headline still kept",
      len(search.filter_hits([pm_real], ["Project Management Software Market"], logger=LOG,
                             topic_terms=["project management", "task tracking"])) == 1)

# 'marketer' is not 'market' coverage (and 'marketers' likewise).
check("marketer is not market coverage", not search.has_word("market", {"marketers", "marketer"}))
check("marketer headline dropped for a 'market' word market",
      search.filter_hits([hit("Marketers rethink budgets as the holiday season approaches", "https://www.adage.com/m")],
                         ["Market Research Market"], logger=LOG, topic_terms=["survey panel", "consumer insights"]) == [])

# The single-word snippet-only rule: 'assessment' only in the snippet of the Trump headline stays dropped.
trump = hit("Trump Has Grown Angrier With Vance Since Midterms, Report Says", "https://www.forbes.com/t",
            "The report offers an assessment of the vice president's mood.")
check("single-word gate: snippet-only 'assessment' still dropped",
      search.filter_hits([trump], ["Assessment Services Market"], key_players=["Pearson"], logger=LOG,
                         topic_terms=["assessment services", "online testing", "proctoring", "aptitude test"]) == [])
# ... and a headline with a word-form of a topic word ('testing' for 'test') is kept by the shared rule.
testing = hit("Pearson expands online testing centers across Asia", "https://www.reuters.com/s")
check("topic word form kept: 'online testing' headline",
      len(search.filter_hits([testing], ["Assessment Services Market"], key_players=["Pearson"], logger=LOG,
                             topic_terms=["online test", "proctoring"])) == 1)


print()
print("all word form tests passed" if not FAILED else f"{len(FAILED)} FAILED: {FAILED}")
sys.exit(1 if FAILED else 0)
