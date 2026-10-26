"""Offline test for claim backing (no network, no LLM calls).

Run from backend/: python devtools/test_claims.py

Covers: ORIENT claim normalisation, the claim-hit filter profile, SearchHit defaults, SELECT's claim_backing
sanitising, the ARTICLE EVIDENCE / CLAIMS block build + parse round-trip, the per-claim writer checks, the market
evidence minimum, and that every prompt template formats with all of its fields.
"""

import logging
import string
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import search  # noqa: E402

LOG = logging.getLogger("test_claims")
LOG.addHandler(logging.NullHandler())
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


def hit(title: str, url: str, snippet: str = "", date=None) -> search.SearchHit:
    return search.SearchHit(title=title, snippet=snippet, url=url, source="", date=date)


# ------------------------------------------------------------------ ORIENT claims
raw = [{"claim": f"Zzz Widget claim number {i} states something checkable", "queries": ["q1", "q2", "q3"], "kind": "comparison"}
       for i in range(12)]
out = pipeline._normalize_claims(raw, "", LOG)
check("normalize: capped at 3 claims", len(out) == 3, str(len(out)))
check("normalize: ids renumbered C1..C3", [c["id"] for c in out] == [f"C{i}" for i in range(1, 4)])
check("normalize: at most 2 queries each", all(len(c["queries"]) <= 2 for c in out))
mixed = [{"claim": "too short"}, "not a dict", {"claim": "Zzz Widget is the simplest option for every beginner"}]
out = pipeline._normalize_claims(mixed, "", LOG)
check("normalize: malformed entries dropped", len(out) == 1 and out[0]["id"] == "C1", str(out))
sponsor = "Zzz Widget is the simplest option for every beginner, as the author says."
out = pipeline._normalize_claims(mixed, sponsor, LOG)
check("normalize: a claim inside the retained paragraph is dropped", out == [], str(out))
check("normalize: non-list input gives []", pipeline._normalize_claims(None, "", LOG) == [] and pipeline._normalize_claims("x", "", LOG) == [])
check("orient_claims: missing key / None give []", pipeline.orient_claims({}) == [] and pipeline.orient_claims(None) == [])

# Claim backing ships disabled by default (config.SEARCH_CLAIMS_ENABLED = False — see config.py):
# the publisher block's evidence was scaled back to just the guest article and the retained
# paragraph. Verified here BEFORE the rest of this file forces it back on: the machinery below is
# still fully here, not deleted, just unused while this flag is off.
check("config: claim backing is disabled by default", config.SEARCH_CLAIMS_ENABLED is False)
check("orient_claims: disabled -> [] even with real claims_to_back present",
      pipeline.orient_claims({"claims_to_back": [{"id": "C1", "claim": "a b c d e"}]}) == [])

# The rest of this file exercises the claim-backing machinery itself (ORIENT normalisation, SELECT's
# claim_backing, the ARTICLE EVIDENCE block, the writer checks) — forcing the flag back on here only
# affects this process, and confirms the dormant path still works for whoever re-enables it.
config.SEARCH_CLAIMS_ENABLED = True
check("orient_claims: returns the stored claims", len(pipeline.orient_claims({"claims_to_back": [{"id": "C1", "claim": "a b c d e"}]})) == 1)

# ------------------------------------------------------------------ SearchHit defaults
h = search.SearchHit(**{"title": "t", "snippet": "s", "url": "https://x.org/a", "source": "", "date": None, "is_news": False})
check("SearchHit: old dicts load, scope defaults to market", h.scope == "market" and h.claim_id == "" and h.excerpt == "")

# ------------------------------------------------------------------ claim filter profile
claim = "Zzz Widget apps make money from order routing rather than commissions"
hits = [
    hit("How Zzz Widget apps make money from order routing", "https://en.wikipedia.org/wiki/Order_routing",
        "Explains how Zzz Widget apps earn money from order routing instead of commissions"),
    hit("What is order routing? A plain explanation for Zzz Widget users", "https://www.investopedia.com/terms/o/orderrouting.asp",
        "How order routing pays Zzz Widget apps rather than commissions"),
    hit("Investor bulletin on order routing and commissions", "https://www.sec.gov/investor/bulletin",
        "Order routing and commissions explained for Zzz Widget app users"),
    hit("Zzz Widget apps make money from order routing", "https://www.linkedin.com/posts/abc",
        "Order routing and commissions explained for Zzz Widget app users"),
    hit("Completely unrelated gardening tips for spring", "https://garden.example.com/tips", "Pruning roses in March"),
    hit("How Zzz Widget apps make money from order routing", "https://dup.example.org/other",
        "Order routing and commissions explained for Zzz Widget apps"),
    hit("Zzz Widget", "https://quote.example.com/zzz", "Order routing commissions Zzz Widget apps"),
]
kept = search.filter_claim_hits(hits, claim, LOG, limit=6)
hosts = [k.host for k in kept]
check("claim filter: wikipedia and investopedia explainers are allowed", "en.wikipedia.org" in hosts and "investopedia.com" in hosts, str(hosts))
check("claim filter: a regulator page is allowed", "sec.gov" in hosts, str(hosts))
check("claim filter: social media is dropped", "linkedin.com" not in hosts, str(hosts))
check("claim filter: an off-topic page is dropped", "garden.example.com" not in hosts, str(hosts))
check("claim filter: a duplicate title is dropped", sum(1 for k in kept if "make money from order routing" in k.title.lower()) == 1, str([k.title for k in kept]))
check("claim filter: a bare 1-word quote page is dropped", "quote.example.com" not in hosts, str(hosts))
check("claim filter: market gate would have blocked the encyclopedia host (profiles differ)",
      any(("wikipedia.org" == b) for b in search._BLOCKED_HOSTS) and not any(h == "wikipedia.org" for h in search._CLAIM_BLOCKED_HOSTS))
many = [hit(f"Zzz Widget order routing commissions explainer number {i}", f"https://site{i}.example.org/x{i}",
            "Zzz Widget order routing commissions explained") for i in range(10)]
for i, m in enumerate(many):
    m.url = f"https://site{i}.org/x{i}"
check("claim filter: limit caps the kept hits", len(search.filter_claim_hits(many, claim, LOG, limit=4)) == 4)
one_site = [hit(f"Zzz Widget order routing commissions explainer part {i}", f"https://blog{i}.same-site.org/p{i}",
                "Zzz Widget order routing commissions explained") for i in range(5)]
check("claim filter: one site supplies at most two pages (subdomains count as one site)",
      len(search.filter_claim_hits(one_site, claim, LOG, limit=6)) == 2)
old = hit("Old explainer on order routing for Zzz Widget apps", "https://old.example.org/a", "order routing commissions Zzz Widget apps",
          datetime(2015, 1, 1, tzinfo=timezone.utc))
check("claim filter: no recency cutoff (an old explainer stays)", len(search.filter_claim_hits([old], claim, LOG)) == 1)

# ------------------------------------------------------------------ SELECT claim_backing sanitising
claims = [{"id": "C1", "claim": claim, "kind": "company_attribute", "why": "no source", "queries": []},
          {"id": "C2", "claim": "Second claim about Zzz Widget fees", "kind": "claim", "why": "", "queries": []}]
pool_hits = [
    hit("Order routing explained", "https://a.example.org/1", "Apps earn 40 cents per 100 shares from order routing"),
    hit("More on order routing", "https://b.example.org/2", "Another explainer"),
    hit("Third explainer", "https://c.example.org/3", "Yet another"),
    hit("Fees page", "https://d.example.org/4", "Fee details"),
]
for h_ in pool_hits:
    h_.source = h_.host
pool = [("P1", "C1", pool_hits[0]), ("P2", "C1", pool_hits[1]), ("P3", "C1", pool_hits[2]), ("P4", "C2", pool_hits[3])]
selection = {"claim_backing": [
    {"claim_id": "C1", "id": "P1", "stance": "supports", "fact": "Apps earn 40 cents per 100 shares from order routing."},
    {"claim_id": "C1", "id": "P2", "stance": "qualifies", "fact": "Routing is one of several income sources."},
    {"claim_id": "C1", "id": "P3", "stance": "supports", "fact": "A third entry for the same claim."},
    {"claim_id": "C1", "id": "P9", "stance": "supports", "fact": "Invented pool id."},
    {"claim_id": "C2", "id": "P4", "stance": "maybe", "fact": "Bad stance."},
    {"claim_id": "C2", "id": "P4", "stance": "supports", "fact": "Charges 55 percent more on fees."},
    "not a dict",
]}
pipeline._normalize_claim_backing(selection, claims, pool, LOG)
kept_ids = [(b["id"], b["claim_id"]) for b in selection["claim_backing"]]
check("backing: at most two entries per claim", sum(1 for b in selection["claim_backing"] if b["claim_id"] == "C1") == 2, str(kept_ids))
check("backing: an invented pool id is dropped", all(b["id"] != "P9" for b in selection["claim_backing"]))
check("backing: a bad stance is dropped", not any(b["id"] == "P4" and b["stance"] == "maybe" for b in selection["claim_backing"]))
check("backing: an invented figure (55) not in the source is dropped", all("55" not in b["fact"] for b in selection["claim_backing"]), str(kept_ids))
check("backing: a supported figure (40) is kept", any("40 cents" in b["fact"] for b in selection["claim_backing"]))

# ---- freshness parity: claim backing obeys the same stale-date rule as market evidence
old_hit = hit("Savings rules for minors", "https://old.example.org/rules", "Rules explained for Zzz Widget savers")
old_hit.source = old_hit.host
fresh_hit = hit("Savings rules explained", "https://new.example.org/rules", "Rules explained for Zzz Widget savers. Published in the autumn.")
fresh_hit.source = fresh_hit.host
claims_f = [{"id": "C1", "claim": "Zzz Widget savers can start young", "kind": "claim", "why": "", "queries": []}]
sel_f = {"claim_backing": [
    {"claim_id": "C1", "id": "P1", "stance": "supports", "fact": "Children under 16 qualify for the contribution as of 1 July 2020."},
    {"claim_id": "C1", "id": "P2", "stance": "supports", "fact": "Children under 16 qualify for the government contribution."},
]}
fresh_hit.excerpt = "Rules explained. Updated 1 July 2020 for the old scheme. Children under 16 qualify."
pipeline._normalize_claim_backing(sel_f, claims_f, [("P1", "C1", old_hit), ("P2", "C1", fresh_hit)], LOG)
kept_f = sel_f["claim_backing"]
check("freshness: a backing fact stating a stale date is dropped", [b["id"] for b in kept_f] == ["P2"], str(kept_f))
check("freshness: a stale date in a kept item's detail is blanked", kept_f and kept_f[0]["detail"] == "", str(kept_f))

# ------------------------------------------------------------------ evidence block build + parse
market_hit = hit("Zzz Widget Co launches new platform", "https://news.example.org/n1", "Zzz Widget Co launched a platform",
                 datetime(2026, 10, 10, tzinfo=timezone.utc))
market_hit.source = "News Example"
sel = {"evidence": [{"id": "W1", "fact": "Zzz Widget Co launched a new platform.", "detail": "It targets small teams.",
                     "use_for_all": ["trends"], "use_for": "trends"}],
       "claim_backing": [{"claim_id": "C1", "id": "P1", "stance": "supports", "fact": "Apps earn 40 cents per 100 shares from order routing.",
                          "source": "a.example.org", "url": "https://a.example.org/1", "detail": "The explainer lists the per-share rate."}]}
orient = {"claims_to_back": claims}
block = pipeline.run_build_evidence(sel, [market_hit], LOG, orient=orient)
check("block: ARTICLE EVIDENCE group present", "### ARTICLE EVIDENCE" in block)
check("block: claims list marks C1 BACKED and C2 as no backing",
      'C1' in block and "BACKED" in block and "NO OUTSIDE BACKING FOUND" in block)
check("block: market evidence group present", "### MARKET EVIDENCE FOR MARKET TRENDS" in block)
items = pipeline._parse_evidence(block)
art = [i for i in items if i["group"] == "article"]
mkt = [i for i in items if i["group"] != "article"]
check("parse: C-numbered lines become article items", len(art) == 1 and art[0]["id"] == "C1", str(items))
check("parse: market items keep their W id and group", len(mkt) == 1 and mkt[0]["id"] == "W1" and mkt[0]["group"] == "trends")
check("parse: DETAIL lines attach to the right item", art[0]["detail"].startswith("The explainer") and mkt[0]["detail"] == "It targets small teams.")
old_block = "### EVIDENCE FOR MARKET TRENDS\n- (W3) [trends] Old format fact here. (source: Site, 2026-10)\n    DETAIL: old detail"
check("parse: old-format blocks still parse", pipeline._parse_evidence(old_block)[0]["id"] == "W3")
none_block = pipeline.run_build_evidence({"evidence": []}, [], LOG, orient=None)
check("block: nothing gives (none)", none_block == "(none)")

# ------------------------------------------------------------------ writer checks
backed = [{"id": "C1", "tags": ["supports"], "group": "article", "source": "Example Explainer",
           "fact": "Example Explainer says apps earn 40 cents per 100 shares from order routing.", "detail": ""}]
publisher_missing = [{"heading": "", "sop": [], "paragraphs": ["An intro paragraph that never mentions the claim at all."]},
                     {"heading": "A", "sop": [], "paragraphs": ["More publisher prose about something else entirely here."]}]
publisher_used = [{"heading": "", "sop": [], "paragraphs": ["Per Example Explainer, apps earn 40 cents per 100 shares from order routing."]}]
problems = pipeline._evidence_problems(publisher_missing, backed, 0)
check("writer check: a backed claim left unused in the publisher block is flagged", any("C1" in p for p in problems), str(problems))
problems = pipeline._evidence_problems(publisher_used, backed, 0)
check("writer check: a backed claim worked into the publisher block passes", not any("C1" in p for p in problems), str(problems))
market_sections = [{"heading": "M", "sop": ["trends"], "paragraphs": ["Per Example Explainer, apps earn 40 cents per 100 shares from order routing."]}]
problems = pipeline._evidence_problems(publisher_missing + market_sections, backed, 0)
check("writer check: usage in a MARKET section does not count for an article claim", any("C1" in p for p in problems), str(problems))
check("market minimum: caps at 10", pipeline._min_evidence_total(60) == 10 and pipeline._min_evidence_total(0) == 0 and pipeline._min_evidence_total(4) == 4)

# ------------------------------------------------------------------ every prompt template formats
class _Blank(dict):
    def __missing__(self, key):
        return "x"


for name in ("ORIENT_USER", "SELECT_USER", "PUBLISHER_USER", "MARKET_USER", "SEARCH_QUERIES_USER", "REVISE_USER"):
    tpl = getattr(prompts, name)
    fields = {f for _, f, _, _ in string.Formatter().parse(tpl) if f}
    try:
        tpl.format_map(_Blank())
        ok, detail = True, ""
    except Exception as e:  # stray brace / bad field
        ok, detail = False, repr(e)
    check(f"prompt {name} formats ({len(fields)} fields)", ok, detail)
check("SELECT_USER has the claims fields", {"claims_block", "claim_pool"} <= {f for _, f, _, _ in string.Formatter().parse(prompts.SELECT_USER) if f})
check("PUBLISHER_USER has the article minimum field", "article_min" in {f for _, f, _, _ in string.Formatter().parse(prompts.PUBLISHER_USER) if f})
check("MARKET_USER has the market minimum and Part 1 fields", {"min_evidence", "part1"} <= {f for _, f, _, _ in string.Formatter().parse(prompts.MARKET_USER) if f})
check("no real product names in the claim prompts", not any(n in prompts.ORIENT_USER + prompts.SELECT_USER for n in ("Robinhood", "Webull", "WeBull", "Sharesies")))

if FAILURES:
    print(f"\n{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("\nall claims tests passed")
