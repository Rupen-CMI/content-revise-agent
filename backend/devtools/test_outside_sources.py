"""Offline test for the rule that no outside source is named (the product owner's rule: CMI is the only source an article names).

Covers the pattern module (attribution clauses cut from the start, the end and the middle of a sentence; the research-firm name shape
and its false-positive guards; the actor exemption), the SELECT-time checks (a rival's statistic relayed by news is dropped; a figure
must stand in a source sentence that matches the fact, with the same unit), the FAQ and the guest points.
Run from backend/: DEEPSEEK_API_KEY=offline-test python devtools/test_outside_sources.py"""
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import checks  # noqa: E402
import faqs  # noqa: E402
import outside_sources as osrc  # noqa: E402
import pipeline  # noqa: E402
import publisher  # noqa: E402
import search  # noqa: E402
import test_planning  # noqa: E402
from test_planning import make_inputs  # noqa: E402

LOG = logging.getLogger("test_outside_sources")
LOG.setLevel(logging.CRITICAL)
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


ALLOWED = osrc.Allowed(frozenset({"adobe", "netcore unbxd", "netcore", "unbxd", "google analytics", "sprinklr insights", "sprinklr"}),
                       ("customer insights software market",))


def clean(sentence, allowed=ALLOWED):
    text, dropped, names = osrc.clean_sentence(sentence, allowed)
    return (None if dropped else text), names


# ------------------------------------------------------------------ attribution clauses: leading, trailing, middle
check("lead: 'According to <firm>, ...' is cut and the sentence is capitalised again",
      clean("According to Grand View Research, the market grew 5% in 2025.")[0] == "The market grew 5% in 2025.")
check("lead: 'Per <outlet>, ...' is cut", clean("Per Statista, 62% of buyers use it.")[0] == "62% of buyers use it.")
check("lead: 'As reported by <outlet>, ...' is cut", clean("As reported by Reuters, sales rose 5% in 2025.")[0] == "Sales rose 5% in 2025.")
check("lead: 'Data from <outlet> shows ...' keeps the finding", clean("Data from Statista shows sales rose 5% in 2025.")[0] == "Data shows sales rose 5% in 2025.")
check("lead: '<name> found that ...' leaves the statistic plain", clean("Statista found that 40% of firms adopt AI.")[0] == "40% of firms adopt AI.")
check("mid: 'a report cited by <outlet>' drops the outlet, keeps the finding",
      clean("A report cited by Chief Marketer in May 2026 found that 60 percent of consumers enjoy receiving mail.")[0]
      == "A report in May 2026 found that 60 percent of consumers enjoy receiving mail.")
check("lead: '<place>'s <body> said in <Month Year> that X' becomes 'In <Month Year>, X' (the date stays)",
      clean("England's Office for Students said in November 2026 that universities must ensure assessments measure what "
            "students know.")[0] == "In November 2026, universities must ensure assessments measure what students know.")
check("an institution whose name holds 'Research' is no research firm: the actor of its own news stays",
      clean("The Australian Council for Educational Research won the Best International Implementation Award in June 2026.")[0]
      == "The Australian Council for Educational Research won the Best International Implementation Award in June 2026.")
check("trail: ', according to <outlet>' is cut", clean("Sales rose 5% in 2025, according to Statista.")[0] == "Sales rose 5% in 2025.")
check("trail: ', per <outlet>' is cut", clean("Sales rose 5% in 2025, per MarTech Cube.")[0] == "Sales rose 5% in 2025.")
check("trail: ', <outlet> reported' is cut", clean("Sales rose 5% in 2025, Gartner reported.")[0] == "Sales rose 5% in 2025.")
check("trail: a trailing clause keeps a closing quote or bracket intact", clean("Sales rose 5% in 2025 (per Statista).")[0] is not None)
check("mid: ', according to <firm>,' leaves the rest of the sentence",
      clean("Sales rose, according to Mordor Intelligence, driven by AI demand across retail.")[0]
      == "Sales rose, driven by AI demand across retail.")
check("mid: ', <outlet> reports,' leaves the rest", clean("Spend rose 4%, Statista reports, driven by retail demand.")[0]
      == "Spend rose 4%, driven by retail demand.")
check("paren: '(<name>, 2026)' is cut", clean("Adoption reached 66.2% of firms (Statista, 2026).")[0] == "Adoption reached 66.2% of firms.")
check("paren: '(source: <name>)' is cut", clean("Adoption reached 66.2% of firms (source: Precedence Research).")[0] == "Adoption reached 66.2% of firms.")
check("by/from: 'a survey by <name>' keeps the survey", clean("A survey by Forrester found that 40% of firms adopt AI.")[0]
      == "A survey found that 40% of firms adopt AI.")
check("by/from: 'a report from <name>' keeps the report", clean("A report from Technavio says demand rises across retail.")[0]
      == "A report says demand rises across retail.")
check("whole: an uncuttable subject-verb citation drops the sentence", clean("Gartner said 40% of CIOs will buy.")[0] is None)
check("whole: an uncuttable possessive citation drops the sentence", clean("Statista's survey of 500 firms shows rapid adoption.")[0] is None)
check("two clauses: both are cut",
      clean("According to Statista, sales rose 5% in 2025, per Gartner.")[0] == "Sales rose 5% in 2025.")
check("a cut that leaves almost nothing drops the sentence", clean("Per Statista, yes.")[0] is None)
check("a link sentence is cleaned where it can be and never dropped",
      osrc.clean_sentence("Gartner said [[CMI_LINK:Zzz Market]] demand rises.", ALLOWED, keep=True)[1] is False)
text, gone = osrc.clean_text("Demand rises. Gartner said 40% of CIOs will buy. According to Statista, prices fall across the region.", ALLOWED)
check("clean_text: an uncuttable sentence goes, a cuttable one loses its clause", text == "Demand rises. Prices fall across the region.", text)

# ------------------------------------------------------------------ research-firm name shape
for name in ("Grand View Research", "Mordor Intelligence", "Fortune Business Insights", "Allied Market Research", "Verified Market Research",
             "Market Research Future", "MarketsandMarkets", "Deloitte Insights", "grandviewresearch.com", "market.us", "Precedence Research",
             "Acme Consulting", "Zeta Strategies", "Kappa Analytics Group", "GrandViewResearch"):
    check(f"firm shape: {name!r} is a research firm's shape", osrc.firm_shaped(name))
for name in ("Coherent Market Insights", "Google Analytics", "Artificial Intelligence", "Business Intelligence", "Customer Insights",
             "Market Research", "Adobe Analytics", "Insights", "Competitive Intelligence", "Apple", "Netcore Unbxd"):
    check(f"firm shape: {name!r} is not", not osrc.firm_shaped(name))
check("firm shape in a sentence: a firm's name is dropped even with no attribution frame",
      clean("The Grand View Research report values the market at $5 billion.")[0] is None)
check("firm shape: 'Coherent Market Insights' is never stripped",
      clean("Coherent Market Insights estimates the market at USD 5 billion.") == ("Coherent Market Insights estimates the market at USD 5 billion.", []))
check("firm shape: 'Market Research' as a topic is not a firm (a segment category)",
      clean("Data Aggregation and Market Research applications follow the same split in that field.")[0] is not None)
check("firm shape: 'Artificial Intelligence' at the start of a sentence is not a firm",
      clean("Artificial Intelligence tools are spreading across retail.")[0] is not None)

# ------------------------------------------------------------------ false-positive guards: listed players and actors
check("guard: Google Analytics as a listed player is kept",
      clean("Google Analytics and Sprinklr Insights are listed vendors in this market.")[0] is not None)
check("guard: an allowed product that has a firm's shape is kept (Sprinklr Insights launched a tool)",
      clean("Sprinklr Insights launched a new tool in May 2026.")[0] is not None)
check("guard: the same name with no fact sheet entry is stripped", clean("Sprinklr Insights launched a new tool in May 2026.", osrc.EMPTY)[0] is None)
check("guard: an actor reporting its own plan is not a citation", clean("Amazon said it will invest $5 billion in 2026.", osrc.EMPTY)[0] is not None)
check("guard: an actor's own forecast about itself is not a citation",
      clean("Adobe forecast that its own traffic data would rise 5%.", osrc.EMPTY)[0] is not None)
check("guard: 'According to <the actor>' on the actor's own figure is kept (allowed actor)", clean("According to Adobe, traffic grew 5%.")[0] is not None)
check("guard: 'Researchers found that ...' names nobody", clean("Researchers found that 60% of buyers prefer it.")[0] is not None)
check("guard: 'per capita' and 'per U.S. household' are not a source",
      clean("Spend rose 4% in 2025, per capita.")[0] is not None and clean("Retailers pay $5 per U.S. household each month.")[0] is not None)
check("guard: the market's own name is not a source", clean("The Customer Insights Software Market grows 5%.")[0] is not None)

# ------------------------------------------------------------------ the study-noun family: a journal, magazine or collection carrying a study
NATURE_STUDY = ("In March 2026, a study in Nature compared translations of Shen Congwen's novel Border Town produced by ChatGPT 4 "
                "and another system using Multidimensional Quality Metrics.")
NATURE_COLLECTION = ("In July 2026, a Nature Portfolio collection on mammalian cell systems for recombinant protein production "
                     "noted that the Chinese hamster ovary cell line is a widely employed system for producing therapeutic proteins.")
check("journal: 'a study in <journal> compared ...' cuts the journal and keeps the study (live sentence)",
      clean(NATURE_STUDY)[0] == "In March 2026, a study compared translations of Shen Congwen's novel Border Town produced by "
                               "ChatGPT 4 and another system using Multidimensional Quality Metrics.")
check("journal: 'a <name> collection ... noted that ...' cuts only the name, the noun stays (live sentence)",
      clean(NATURE_COLLECTION)[0] == "In July 2026, a collection on mammalian cell systems for recombinant protein production "
                                    "noted that the Chinese hamster ovary cell line is a widely employed system for producing "
                                    "therapeutic proteins.")
check("journal: the check names the journal of both live sentences",
      osrc.attributing_names(NATURE_STUDY) == ["Nature"]
      and osrc.attributing_names(NATURE_COLLECTION) == ["Nature Portfolio"])
check("journal: 'a paper published in the journal <name>' keeps the paper",
      clean("A paper published in the journal Science found that 40% of trials fail.")[0]
      == "A paper found that 40% of trials fail.")
check("journal: 'a study appearing in <name>' keeps the study",
      clean("A study appearing in Nature found that the method works.")[0] == "A study found that the method works.")
check("journal: 'was published in <name>' keeps the clause and its date",
      clean("The findings were published in Nature in July 2026.")[0] == "The findings were published in July 2026.")
check("journal: '<name> published a study' leaves the study",
      clean("Nature published a study comparing five translation systems.")[0] == "A study comparing five translation systems.")
check("journal: '<name>'s journal carried a paper' leaves the paper",
      clean("Nature's journal carried a paper on cell-line stability.")[0] == "A paper on cell-line stability.")
check("journal: 'in the journal <name>, ...' cuts the venue lead",
      clean("In the journal Nature, a study found that the method works.")[0] == "A study found that the method works.")
check("journal: 'a <name> article' becomes 'an article'",
      clean("A Science article on the method drew wide attention.")[0] == "An article on the method drew wide attention.")
check("journal: 'a <firm> survey found' keeps the survey",
      clean("A Deloitte survey found that 60% of firms use it.")[0] == "A survey found that 60% of firms use it.")
check("journal: a study in a place names no venue",
      osrc.attributing_names("A study in Europe compared two methods.") == []
      and clean("A study in Europe compared two methods.") == ("A study in Europe compared two methods.", []))
check("journal: 'a report in <month year>' is a date, not a venue",
      clean("A report in May 2026 found that 60 percent of consumers enjoy receiving mail.")[0]
      == "A report in May 2026 found that 60 percent of consumers enjoy receiving mail.")
ACTORS = osrc.Allowed(frozenset({"microsoft", "thermo fisher scientific", "wuxi biologics"}), ())
check("journal: an allowed actor launching a product stays named",
      clean("Thermo Fisher Scientific launched the Gibco CHO-K1 Catalog Panel.", ACTORS)[0]
      == "Thermo Fisher Scientific launched the Gibco CHO-K1 Catalog Panel."
      and clean("Microsoft unveiled Copilot Workspaces.", ACTORS)[0] == "Microsoft unveiled Copilot Workspaces."
      and clean("WuXi Biologics experts described the new platform.", ACTORS)[0]
      == "WuXi Biologics experts described the new platform.")
check("journal: an actor's own study stays named when the fact sheet carries the actor",
      clean("A Microsoft survey found that 60% of firms use it.", ACTORS)[0]
      == "A Microsoft survey found that 60% of firms use it.")
_actor_inp = make_inputs()
_actor_inp.items["W9"] = {"id": "W9", "kind": "evidence", "tags": ["context"], "historical": False, "sections": {"trends"},
                          "detail": "", "source": "web",
                          "text": "Thermo Fisher Scientific launched the Gibco CHO-K1 Catalog Panel. Microsoft unveiled Copilot "
                                  "Workspaces. WuXi Biologics experts described the new platform."}
_actor_allowed = osrc.allowed_names(_actor_inp)
check("journal: the actor names a fact item carries (allowed_names) are never sources",
      all(osrc.attributing_names(s, _actor_allowed) == [] for s in (
          "Thermo Fisher Scientific launched the Gibco CHO-K1 Catalog Panel.",
          "Microsoft unveiled Copilot Workspaces.",
          "WuXi Biologics experts described the new platform.")))
check("journal: the same study with no actor in the fact sheet loses the company",
      clean("A Microsoft survey found that 60% of firms use it.", osrc.EMPTY)[0]
      == "A survey found that 60% of firms use it.")
OFFICE = "According to the Office for National Statistics, 18% of retail sales were online in 2025."
check("journal: a regulatory body stays in the trends and sub-segment sections (regulatory_ok), and only there",
      osrc.attributing_names(OFFICE, osrc.EMPTY, (), regulatory_ok=True) == []
      and clean(OFFICE, osrc.EMPTY)[0] == "18% of retail sales were online in 2025.")
_sections = [{"heading": "Trends", "sop": ["trends"], "paragraphs": [NATURE_STUDY]}]
check("checks: the trends section that names a journal is reported",
      any("Nature" in p for p in checks.v2_outside_source_problems(_sections, make_inputs())))

# ------------------------------------------------------------------ the fact sheet decides who is an actor
inp = make_inputs()
inp.items["W8"] = {"id": "W8", "kind": "evidence", "tags": ["context"], "historical": False, "sections": {"trends"}, "detail": "",
                   "text": "Netcore Unbxd launched an AI agent in May 2026. MarTech Cube reported that 40 retailers adopted it.",
                   "source": "MarTech Cube, 2026-05"}
inp.items["fs.players"] = {"id": "fs.players", "kind": "players", "tags": [], "source": "CMI report", "historical": False, "detail": "",
                           "text": "Key players include Google Analytics, Sprinklr Insights and Brand Watchers.", "sections": {"key_players"}}
al = osrc.allowed_names(inp)
check("allowed: the company an evidence item is about is an actor", al.covers("Netcore Unbxd") and al.covers("Netcore"))
check("allowed: a publisher the item's own text cites is not an actor", not al.covers("MarTech Cube"))
check("allowed: every name in the key-players list is allowed, each piece of an 'and' list too",
      al.covers("Google Analytics") and al.covers("Sprinklr Insights") and al.covers("Brand Watchers"))
check("allowed: the market's own name is covered", al.covers("Zzz Widget Services Market") or al.covers("Widget Services"))
check("source_names: a two-word publication is listed for bare detection, a single plain word is not",
      "News Site" in osrc.source_names(inp, al) and "MarTech Cube" in osrc.source_names(inp, al))
check("attributing_names: the cited publication is reported, the actor is not",
      osrc.attributing_names("Netcore Unbxd launched an AI agent, as MarTech Cube noted.", al, osrc.source_names(inp, al)) == ["MarTech Cube"]
      and osrc.attributing_names("Netcore Unbxd launched an AI agent in May 2026.", al, osrc.source_names(inp, al)) == [])

# ------------------------------------------------------------------ SELECT: attribution of a figure to an outside source
check("select: a figure per a research firm is flagged", bool(osrc.attributes_to_outside("Per Statista, 62% of shoppers use AI assistants.")))
# the trends and sub-segment spec (2026-11-19) asks for statistics from government bodies, regulators, associations and institutions
check("select: a figure an association publishes is kept",
      osrc.attributes_to_outside("According to the American Association of Colleges of Nursing, the faculty vacancy rate was 7.2% in 2025.") == [])
check("select: a figure a government agency publishes is kept",
      osrc.attributes_to_outside("According to the U.S. Energy Information Administration, EV sales rose 12% in 2025.") == [])
check("select: a figure a national statistics office publishes is kept",
      osrc.attributes_to_outside("Per the Office for National Statistics, 18% of retail sales were online in 2025.") == [])
check("select: a rival firm's figure is still flagged even with an institution-like word",
      bool(osrc.attributes_to_outside("According to Allied Market Research, adoption reached 40% in 2025.")))
check("official body: a statistics portal brand is a rival, not an official body",
      osrc.rival_brand("Statista") and not osrc.official_body("Statista"))
check("official body: a research-firm name is never an official body", not osrc.official_body("Fortune Business Insights"))
check("select: a company forecasting from its own data is kept", not osrc.attributes_to_outside("US holiday online sales to grow 6.7% as discounts attract shoppers, Adobe Analytics forecasts"))
check("select: a statistic verb on a research-firm shape is flagged", bool(osrc.attributes_to_outside("Mordor Intelligence estimates 40% of CIOs will buy AI tools.")))
check("select: a firm-shaped attribution is flagged even with no digit", bool(osrc.attributes_to_outside("Mordor Intelligence expects strong growth.")))
check("select: a news speech verb on a company is not a relayed statistic",
      osrc.attributes_to_outside("Reuters reported that Tesla will invest $5 billion in new plants.") == [])
check("select: the speaker's own plan is not a relayed statistic", osrc.attributes_to_outside("Adobe said its traffic rose 4% in 2025.") == [])
check("select: a plain development states no source", osrc.attributes_to_outside("Netcore Unbxd launched an AI agent in May 2026.") == [])
check("select: the market-sizing filter drops a fact that cites a rival firm by shape, with no list of names",
      pipeline._is_market_sizing("Acme Quant Research puts the segment at a high figure.") is True
      and pipeline._is_market_sizing("Netcore Unbxd launched an AI agent in May 2026.") is False)


# ------------------------------------------------------------------ SELECT: a figure stands in a matching source sentence
def hit(title, snippet, excerpt=""):
    return search.SearchHit(title=title, snippet=snippet, url="https://news.example.org/a", source="Example News", date=datetime(2026, 11, 1, tzinfo=timezone.utc),
                            is_news=True, excerpt=excerpt)


def select(fact, h, detail=""):
    sel = {"evidence": [{"id": "W1", "fact": fact, "detail": detail, "use_for": "trends"}]}
    pipeline._verify_selection_figures(sel, [h], LOG)
    return sel["evidence"]


good = hit("Netcore Unbxd launches AI agent", "Netcore Unbxd launched an AI agent in May 2026.",
           "Netcore Unbxd launched an AI agent in May 2026. The agent handles 40% of shopper queries without a human.")
kept = select("Netcore Unbxd launched an AI agent in May 2026 that handles 40% of shopper queries.", good)
check("figures: a good item (the figure in a sentence that matches the fact) is kept", len(kept) == 1)

stray = hit("Retail event roundup", "Netcore Unbxd launched an AI agent in May 2026.",
            "Netcore Unbxd launched an AI agent in May 2026. Separately, the conference drew 40% more visitors than last year.")
check("figures: the figure sits in an unrelated sentence of the page, so the item is dropped",
      select("Netcore Unbxd launched an AI agent in May 2026 that handles 40% of shopper queries.", stray) == [])
unit = hit("Funding news", "Acme raised $5 million for its robo-advisor platform.")
check("figures: the same digits with another scale ($5 million vs $5 billion) are dropped",
      select("Acme raised $5 billion for its robo-advisor platform.", unit) == [])
check("figures: the same digits with the same scale are kept", len(select("Acme raised $5 million for its robo-advisor platform.", unit)) == 1)
pct = hit("Survey news", "Retailers say 40 percent of shopper queries are now handled by AI agents.")
check("figures: '40 percent' in the source backs '40%' in the fact", len(select("40% of shopper queries are handled by AI agents.", pct)) == 1)
check("figures: a '%' fact against a plain-count source is dropped",
      select("40% of shopper queries are handled by AI agents.", hit("Counts", "Retailers say 40 shopper queries are now handled by AI agents.")) == [])
relayed = hit("Shopping study", "According to Statista, 62% of shoppers use AI assistants for gift ideas.")
check("figures: a rival's statistic relayed by the news item is dropped", select("62% of shoppers use AI assistants for gift ideas.", relayed) == [])
relayed2 = hit("Shopping study", "Precedence Research estimates 62% of shoppers use AI assistants for gift ideas.")
check("figures: a research firm's statistic verb relays it too", select("62% of shoppers use AI assistants for gift ideas.", relayed2) == [])
own = hit("Adobe news", "Adobe said its traffic data shows 62% of shoppers use AI assistants for gift ideas.")
check("figures: the actor's own statement of its own figure is kept", len(select("62% of shoppers use AI assistants for gift ideas.", own)) == 1)
mixed = hit("Shopping study", "62% of shoppers use AI assistants for gift ideas.", "According to Statista, 62% of shoppers use AI assistants for gift ideas.")
check("figures: one clean source sentence is enough to keep the item", len(select("62% of shoppers use AI assistants for gift ideas.", mixed)) == 1)
kept = select("Netcore Unbxd launched an AI agent in May 2026.", good, detail="Conference visitors rose 77% year on year.")
check("figures: an unsupported detail is blanked, the item stays", len(kept) == 1 and kept[0]["detail"] == "", str(kept))
check("figures: a fact with no figure needs no source sentence", len(select("Netcore Unbxd launched an AI agent.", good)) == 1)

# claim backing is held to the same test
claims = [{"id": "C1", "claim": "Apps earn money from order routing", "kind": "claim", "why": "", "queries": []}]
pool_hits = [hit("Order routing", "Apps earn 40 cents per 100 shares from order routing.", ""),
             hit("Order routing study", "According to Statista, apps earn 40 cents per 100 shares from order routing."),
             hit("Fees", "The conference drew 40 cents per ticket.", "")]
pool = [("P1", "C1", pool_hits[0]), ("P2", "C1", pool_hits[1]), ("P3", "C1", pool_hits[2])]
sel = {"claim_backing": [{"claim_id": "C1", "id": "P1", "stance": "supports", "fact": "Apps earn 40 cents per 100 shares from order routing."},
                         {"claim_id": "C1", "id": "P2", "stance": "supports", "fact": "Apps earn 40 cents per 100 shares from order routing."},
                         {"claim_id": "C1", "id": "P3", "stance": "supports", "fact": "Apps earn 40 cents per 100 shares from order routing."}]}
pipeline._normalize_claim_backing(sel, claims, pool, LOG)
check("claim backing: the clean entry is kept, the relayed statistic and the unrelated sentence are dropped",
      [b["id"] for b in sel["claim_backing"]] == ["P1"], str(sel["claim_backing"]))

# ------------------------------------------------------------------ the FAQ and the guest points
plan = test_planning.plan_of(test_planning.good_plan())
raw = [{"question": q["question"], "answer": "According to Grand View Research, the segmentation splits it by Browser Extension and Cloud Based."}
       for q in inp.questions if q["id"] == "Q3"]
out = faqs.normalize_faq(raw, plan, inp, "no draft", LOG)
check("faq: an answer that cites a research firm loses the clause and keeps its answer",
      len(out) == 1 and "Grand View" not in out[0]["answer"] and out[0]["answer"].startswith("The segmentation"), str(out))
raw = [{"question": q["question"], "answer": "Gartner said the segmentation splits it by Browser Extension and Cloud Based."}
       for q in inp.questions if q["id"] == "Q3"]
check("faq: an answer that is only an uncuttable citation is dropped", faqs.normalize_faq(raw, plan, inp, "no draft", LOG) == [])

BODY = "Turning a messy online page into plain readable text helps real shoppers find what they are after. Stores sell products."
BRANDS = ["acmecorp"]
pts = publisher.normalize_points({"points": [
    "According to Gartner, turning a messy online page into plain readable text helps real shoppers.",
    "Statista said stores sell products to shoppers in every region.",
    "Turning a messy online page into plain readable text helps real shoppers find what they want."]}, BODY, BRANDS, LOG)["points"]
texts = [p["text"] for p in pts]
check("points: a point that cites an outside source loses the clause, one that cannot be cut is dropped, a clean one is kept",
      texts == ["Turning a messy online page into plain readable text helps real shoppers.",
                "Turning a messy online page into plain readable text helps real shoppers find what they want."], str(texts))

# a place before a speech verb is where something happened, never a source ("A study in China found ...")
for _s in ("A study in China found that adoption rose 12%.", "India said exports rose 12%.", "Adoption rose 12%, Germany reported."):
    check(f"places: {_s!r} keeps its place and is not cut", osrc.clean_sentence(_s)[:2] == (_s, False), str(osrc.clean_sentence(_s)))
check("places: a firm before the same verb is still cut", osrc.clean_sentence("Gartner found that 40% of CIOs plan cuts.")[0] == "40% of CIOs plan cuts.")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILED: {FAILURES}")
    sys.exit(1)
print("all outside-source tests passed")
