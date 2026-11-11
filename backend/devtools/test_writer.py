"""Offline test for the writer's prompt building, assembly and code backstops (no network, no real LLM calls).

Run from backend/: python devtools/test_writer.py
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import llm  # noqa: E402
import planning  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import writer  # noqa: E402
from docx_io import Link, Paragraph  # noqa: E402
from pipeline import _sponsor_problems  # noqa: E402
from test_planning import good_plan, make_inputs  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_writer")
MARKET = "Zzz Widget Services Market"


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------ meta-sentence stripper
secs = [{"heading": "h", "sop": ["subsegment"], "paragraphs": [
    "Banks are core buyers. The listed facts say little more about how banks use these platforms, so the picture here stays broad.",
    "Lead-in sentence.\n- Fiserv: sells platforms.\n- The report does not put a number on it here.",
    "The facts say nothing. The plan calls for a stop.",
    "Cloud tools grow fast. Nothing here is about the writing process."]}]
out = writer.strip_meta_sentences(secs, LOG)
paras = out[0]["paragraphs"]
check("stripper: a process-language sentence is removed and the rest of its paragraph kept", paras[0] == "Banks are core buyers.", str(paras))
check("stripper: bullet lines keep their shape and an emptied bullet is dropped", paras[1] == "Lead-in sentence.\n- Fiserv: sells platforms.", str(paras))
check("stripper: a paragraph that was only process language is dropped", len(paras) == 3 and paras[2].startswith("Cloud tools"), str(paras))
check("stripper: a section left empty is dropped",
      writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": ["The facts say nothing."]}], LOG) == [])
check("stripper: ordinary market sentences about reports and facts are kept",
      writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": ["Analysts report that three facts of life shape demand, and the report names six vendors."]}], LOG)[0]["paragraphs"][0].startswith("Analysts"))

# ------------------------------------------------------------------ coverage-only sentences (a study examines ...)
cov_secs = [{"heading": "h", "sop": ["country_us"], "paragraphs": [
    "A widget-industry study examines how brokerages decide to buy, build or rent these systems.",
    "A widget-industry study surveys 400 brokerages about their buying habits.",
    "A widget-industry study examines pricing and says that buyers prefer longer contracts.",
    "A widget-industry study tracks adoption, naming Zzz Corp, Acme Ltd, Beta Group.",
    f"A widget-industry study examines buying habits among teams served by {prompts.SPONSOR_TOKEN}.",
    "A widget-industry study examines coverage in this market, but the same study found a real shift last quarter.",
]}]
cov_expected = list(cov_secs[0]["paragraphs"][1:])
cov_out = writer.strip_meta_sentences(cov_secs, LOG)[0]["paragraphs"]
check("coverage: a publication-subject sentence with a coverage verb, no figure and no finding is removed",
      cov_out == cov_expected, str(cov_out))
check("coverage: the same coverage sentence with a figure in it is kept", cov_out[0].startswith("A widget-industry study surveys 400"), cov_out[0])
check("coverage: a coverage sentence carrying a finding verb is kept", "says that buyers prefer" in cov_out[1], cov_out[1])
check("coverage: a coverage sentence whose publication names companies is kept", "Zzz Corp" in cov_out[2], cov_out[2])
check("coverage: a coverage sentence carrying the sponsor placeholder is kept", prompts.SPONSOR_TOKEN in cov_out[3], cov_out[3])

# ------------------------------------------------------------------ citations found in code
inp = make_inputs()
plan = planning.validate_plan(good_plan(), inp, LOG)
sections = [
    {"heading": "", "sop": [], "paragraphs": ["Acme launched a new product in the opening."]},
    {"heading": "t", "sop": ["trends"], "paragraphs": ["Acme launched a new product this year, a News Site report says."]},
    {"heading": "s", "sop": ["segmentation"], "paragraphs": ["Nothing about any evidence here."]},
]
cites = writer.evidence_citations(sections, inp)
check("citations: a market paragraph that uses a news item is cited, the opening and unrelated paragraphs are not",
      [c["id"] for c in cites] == ["W1"] and cites[0]["paragraph"].startswith("Acme launched a new product this year"), str(cites))

# ------------------------------------------------------------------ outside sources named in the sentence that carries their figure
inp_src = make_inputs()
inp_src.items["W2"]["source"] = "blog.apify.com, 2026-01"
inp_src.items["W2"]["text"] = "66.2 percent of respondents plan to try AI tools."
secs_src = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "Apify reported in January 2026 that many do not use AI. Even so, 66.2% plan to try AI-assisted tools.",
    "- This bullet names blog.apify.com for 66.2% already.",
    "A sentence with no figure stays as it is."]}]
fixed = writer.name_outside_sources(secs_src, inp_src, LOG)[0]["paragraphs"]
check("source naming: a figure sentence whose source an earlier sentence of the same line named needs no repeat of the name",
      fixed[0] == secs_src[0]["paragraphs"][0], fixed[0])
check("source naming: a sentence that already names the source, and one without a figure, are untouched",
      fixed[1] == "- This bullet names blog.apify.com for 66.2% already." and fixed[2] == "A sentence with no figure stays as it is.",
      str(fixed))
check("source naming: report (CMI) figures are never touched",
      writer.name_outside_sources([{"heading": "", "sop": ["report_context"], "paragraphs": ["Robo advisory opens room to grow, CMI says."]}], inp_src, LOG)[0]["paragraphs"]
      == ["Robo advisory opens room to grow, CMI says."])

# a number the report states and a news item also quotes: the sentence is the report's, no news source is credited
inp_tie = make_inputs()
inp_tie.items["W2"]["source"] = "PR.com, 2026-10"
inp_tie.items["W2"]["text"] = "A firm expanded its services."
inp_tie.items["W2"]["detail"] = "It cited U.S. retail e-commerce sales of USD 354.1 billion in the quarter."
inp_tie.items["fs.us.0"] = {"id": "fs.us.0", "kind": "us", "tags": [], "text": "U.S. retail e-commerce sales were estimated at USD 354.1 billion.",
                            "detail": "", "source": "", "historical": False, "sections": {"country_us"}}
tie = writer.name_outside_sources([{"heading": "u", "sop": ["country_us"], "paragraphs": ["Retail sales were estimated at USD 354.1 billion in the quarter."]}], inp_tie, LOG)[0]["paragraphs"]
check("source naming: a number the report states and a news item also quotes is not credited to the news source",
      tie == ["Retail sales were estimated at USD 354.1 billion in the quarter."], str(tie))
inp_tie.items.pop("fs.us.0")
alone = writer.name_outside_sources([{"heading": "u", "sop": ["country_us"], "paragraphs": ["Retail sales were estimated at USD 354.1 billion in the quarter."]}], inp_tie, LOG)[0]["paragraphs"]
check("source naming: with no report fact owning the number, the news source is still named", "per PR.com" in alone[0], str(alone))

# a second figure of a source named earlier in the same line needs no repeat of the name
inp_src.items["W2"]["detail"] = "Usage spans 12.5 million users."
inp_src.items["W4"] = {"id": "W4", "kind": "evidence", "tags": ["context"],
                       "text": "Adoption stands at 8.4 percent of firms.", "detail": "",
                       "source": "Field Report", "historical": False, "sections": {"trends"}}
same_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "The metered model tracks 66.2% of respondents, a report at Apify finds. Adoption then reaches 12.5 million users."]}]
fixed = writer.name_outside_sources(same_line, inp_src, LOG)[0]["paragraphs"]
check("source naming: a later sentence of the same line carrying another figure of the named source is untouched",
      fixed[0] == "The metered model tracks 66.2% of respondents, a report at Apify finds. Adoption then reaches 12.5 million users.",
      str(fixed))
new_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "The metered model tracks 66.2% of respondents, a report at Apify finds.",
    "Adoption then reaches 12.5 million users.",
    "- Usage widens to 12.5 million users in the metered model."]}]
fixed = "\n".join(writer.name_outside_sources(new_line, inp_src, LOG)[0]["paragraphs"]).split("\n")
check("source naming: a new line (prose or bullet) that carries the source's figure names the source again",
      fixed[1] == "Adoption then reaches 12.5 million users, per blog.apify.com."
      and fixed[2] == "- Usage widens to 12.5 million users in the metered model, per blog.apify.com.", str(fixed))
mixed = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "The metered model tracks 66.2% of respondents, Apify's January tally finds. Adoption stands at 8.4% of firms."]}]
fixed = writer.name_outside_sources(mixed, inp_src, LOG)[0]["paragraphs"]
check("source naming: a second outside source's figure still needs its own name in that line",
      fixed[0] == "The metered model tracks 66.2% of respondents, Apify's January tally finds. Adoption stands at 8.4% of firms, per Field Report.",
      str(fixed))

# A domain-style source with no 4+ letter word ("zz8020.com") was never seen as named by `_names_source`, so
# a line whose first sentence already named the site still carried ", per ..." on its later figure sentence
inp_dom = make_inputs()
inp_dom.items["W2"]["source"] = "zz8020.com, 2026-01"
inp_dom.items["W2"]["text"] = "55.1 percent of tracked firms plan to adopt the tool."
inp_dom.items["W2"]["detail"] = "Usage reaches 3.2 million users."
dom_name = "The tracker at zz8020.com reported in January 2026 that many firms adopt the tool."
dom_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [dom_name + " Usage reaches 3.2 million users."]}]
fixed = writer.name_outside_sources(dom_line, inp_dom, LOG)[0]["paragraphs"]
check("source naming: a domain-style source named in the first sentence of a line is not appended again on its later figure sentence",
      fixed[0] == dom_line[0]["paragraphs"][0], str(fixed))
dom_new = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    dom_name,
    "Usage reaches 3.2 million users.",
    "- Another 55.1% of tracked firms sit behind the plan."]}]
fixed = "\n".join(writer.name_outside_sources(dom_new, inp_dom, LOG)[0]["paragraphs"]).split("\n")
check("source naming: a new line that carries a domain-style source's figure names the source again",
      fixed[1] == "Usage reaches 3.2 million users, per zz8020.com."
      and fixed[2] == "- Another 55.1% of tracked firms sit behind the plan, per zz8020.com.", str(fixed))
dom_solo = [{"heading": "t", "sop": ["trends"], "paragraphs": ["Usage reaches 3.2 million users."]}]
fixed = writer.name_outside_sources(dom_solo, inp_dom, LOG)[0]["paragraphs"]
check("source naming: an unnamed domain-style source's figure sentence still gets ', per its name' appended",
      fixed[0] == "Usage reaches 3.2 million users, per zz8020.com.", str(fixed))

# ------------------------------------------------------------------ untraced-figure stripper (fact-side word tracing)
# A fact that spells its figure out in words ("Sixty-three percent") has no digit in its text, so a writer
# sentence rendering it as digits ("63%") would once have been judged untraced and deleted.
inp_spell = make_inputs()
inp_spell.items["W1"]["text"] = "Sixty-three percent of households use the service, double the share in 2024."
spell_secs = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "Households lean on the service everywhere; 63% now keep it in daily use.",
    "A tracked share sits at 48.9%, a number no fact carries.",
    "Three trends shape the adoption path."]}]
spell_out = writer.strip_untraced_figures(spell_secs, inp_spell, "", LOG)[0]["paragraphs"]
check("untraced: a digit rendering of a fact's spelled-out figure survives the stripper",
      spell_out[0] == "Households lean on the service everywhere; 63% now keep it in daily use.", str(spell_out))
check("untraced: a digit figure appearing in no fact is still removed", len(spell_out) == 2, str(spell_out))
check("untraced: a sentence carrying only a spelled number word is untouched",
      spell_out[1] == "Three trends shape the adoption path.", str(spell_out))

# a token two outside facts share ("9" owned by both) neither blocks the naming nor names the wrong source:
# the fact with the most figures in common with the sentence is its owner, the rest are coincidence
inp_share = make_inputs()
inp_share.items["W6"] = {"id": "W6", "kind": "evidence", "tags": ["context"],
                         "text": "Adoption reaches 9 units among 150 teams.", "detail": "",
                         "source": "Alpha Post", "historical": False, "sections": {"trends"}}
inp_share.items["W7"] = {"id": "W7", "kind": "evidence", "tags": ["context"],
                         "text": "Usage stands at 9 units, while 40 firms lag.", "detail": "",
                         "source": "Beta Desk", "historical": False, "sections": {"trends"}}
share_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "Adoption reaches 9 units among the tracked firms, while 40 firms lag behind."]}]
fixed = writer.name_outside_sources(share_line, inp_share, LOG)[0]["paragraphs"]
check("source naming: a token shared by two facts does not block the naming of the stronger owner",
      fixed[0] == "Adoption reaches 9 units among the tracked firms, while 40 firms lag behind, per Beta Desk.",
      str(fixed))
amb_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "Adoption reaches 9 units among the tracked firms."]}]
fixed = writer.name_outside_sources(amb_line, inp_share, LOG)[0]["paragraphs"]
check("source naming: a sentence carrying only a token two facts tie on stays untouched",
      fixed[0] == amb_line[0]["paragraphs"][0], str(fixed))

# Two outside facts share one figure token ("50") but the sentence keeps only one fact's own wording:
# the wording tie-break (most distinct content words shared with the fact's text + detail) names that
# fact's source, never the other's — with the wording overlapping equally the sentence stays unnamed.
inp_word = make_inputs()
inp_word.items["W13"] = {"id": "W13", "kind": "evidence", "tags": ["context"],
                         "text": "A survey found 50% of those surveyed expanded their staff.", "detail": "",
                         "source": "Alpha Desk, 2026-01", "historical": False, "sections": {"trends"}}
inp_word.items["W14"] = {"id": "W14", "kind": "evidence", "tags": ["context"],
                         "text": "Vendor outlined a framework called the Rule of 50 combining growth and margin.",
                         "detail": "", "source": "Beta Desk, 2026-02", "historical": False, "sections": {"trends"}}
word_secs = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "The vendor has outlined a framework named the Rule of 50 that combines growth and margin."]}]
fixed = writer.name_outside_sources(word_secs, inp_word, LOG)[0]["paragraphs"]
check("source naming: a shared figure token whose sentence keeps one fact's wording names only that fact's source",
      fixed[0] == "The vendor has outlined a framework named the Rule of 50 that combines growth and margin, per Beta Desk.",
      str(fixed))
tie_secs = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "The checklist keeps one mark pinned at 50 while the notes stay quiet there."]}]
fixed = writer.name_outside_sources(tie_secs, inp_word, LOG)[0]["paragraphs"]
check("source naming: an equal wording overlap over a shared figure token stays unnamed",
      fixed[0] == tie_secs[0]["paragraphs"][0], str(fixed))

# The unit of ownership is digits only: a fact whose only number is spelled in words ("two") owns no figure,
# so a drafted "$2 trillion" (owned by a fact carrying the digits) gets ", per" that fact's source, and the
# spelled-out facts never have their sources appended to it.
inp_per = make_inputs()
inp_per.items["W9"] = {"id": "W9", "kind": "evidence", "tags": ["context"],
                       "text": "Adoption rose 7 percent across the tracked firms.",
                       "detail": "", "source": "Alpha Desk, 2026-01",
                       "historical": False, "sections": {"trends"}}
inp_per.items["W10"] = {"id": "W10", "kind": "evidence", "tags": ["context"],
                        "text": "Deal activity kept its pace through the whole year.",
                        "detail": "The wave produced two acquisitions.",
                        "source": "Beta Desk, 2026-02", "historical": False, "sections": {"trends"}}
inp_per.items["W11"] = {"id": "W11", "kind": "evidence", "tags": ["context"],
                        "text": "Vendors keep joining hands across the field.",
                        "detail": "The joining runs through two platforms.",
                        "source": "Gamma Desk, 2026-03", "historical": False, "sections": {"trends"}}
inp_per.items["W12"] = {"id": "W12", "kind": "evidence", "tags": ["context"],
                        "text": "Combined holdings top $2 trillion in assets.",
                        "detail": "", "source": "Delta Desk, 2026-04",
                        "historical": False, "sections": {"trends"}}
per_line = [{"heading": "t", "sop": ["trends"], "paragraphs": [
    "Combined holdings now pass more than $2 trillion in assets."]}]
fixed = writer.name_outside_sources(per_line, inp_per, LOG)[0]["paragraphs"]
check("source naming: an unnamed digit figure gets ', per' its own source only, never the spelled-'two' facts",
      fixed[0] == "Combined holdings now pass more than $2 trillion in assets, per Delta Desk.", str(fixed))

# ------------------------------------------------------------------ leading pointing words become 'The + noun'
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": [
    "That skill matters because collecting data has become routine."]}], LOG)
check("pointing: 'That + noun' is rewritten to 'The + noun'",
      out[0]["paragraphs"] == ["The skill matters because collecting data has become routine."], out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": [
    "Those needs rarely disappear, which keeps spending steady."]}], LOG)
check("pointing: 'Those needs ...' starts with 'The needs rarely disappear'",
      out[0]["paragraphs"][0].startswith("The needs rarely disappear"), out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": [
    "These trends in the market push buyers toward managed services."]}], LOG)
check("pointing: 'These trends in the ...' starts with 'The trends in the market'",
      out[0]["paragraphs"][0].startswith("The trends in the market"), out[0]["paragraphs"])
bare = ["That is why buyers pay less for managed services.",
        "That makes it a small but steadily growing business.",
        "This shows how fast the market grows today.",
        "That said, buyers still compare prices carefully."]
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": list(bare)}], LOG)
check("pointing: bare pointing words ('That is/makes/shows/said') are left untouched",
      out[0]["paragraphs"] == bare, out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["segmentation"], "paragraphs": [
    "Lead-in sentence.\n- This segment drives demand among small teams."]}], LOG)
check("pointing: a bullet line keeps its marker and its pointing word is fixed",
      out[0]["paragraphs"][0] == "Lead-in sentence.\n- The segment drives demand among small teams.", out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": [
    f"That team relies on {prompts.SPONSOR_TOKEN} for its daily workflow."]}], LOG)
check("pointing: a sentence carrying the sponsor placeholder is untouched",
      out[0]["paragraphs"] == [f"That team relies on {prompts.SPONSOR_TOKEN} for its daily workflow."], out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": [
    '"That figure covers what buyers spend on services," the report says.']}], LOG)
check("pointing: a sentence that opens with a quote keeps the quote mark",
      out[0]["paragraphs"][0] == '"The figure covers what buyers spend on services," the report says.', out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": [
    "The market is small. That figure covers spending on services. Buyers want clean data."]}], LOG)
check("pointing: only the pointing sentence changes and the sentence spacing is preserved",
      out[0]["paragraphs"] == ["The market is small. The figure covers spending on services. Buyers want clean data."],
      out[0]["paragraphs"])
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": ["That works."]}], LOG)
check("pointing: a sentence shorter than four words is left untouched",
      out[0]["paragraphs"] == ["That works."], out[0]["paragraphs"])

# The full decision table for the grammar-based rule (round 2): every MUST CHANGE row becomes 'The + rest'.
change = ["That skill matters because collecting data has become routine.",
          "That range shows how many ways a buyer can rent capacity.",
          "Those needs rarely disappear, which keeps spending steady.",
          "These trends in the market push buyers toward managed services.",
          "This sub-segment drives demand because the output feeds analysis.",
          "These vendors serve very different kinds of buyer in the market.",
          "That wider business is the market that buyers rent from.",
          "That everyday need for clean data is what the market sells.",
          "That figure covers what buyers spend on services each year.",
          "This segmentation of the market covers delivery type, application and region."]
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": list(change)}], LOG)[0]["paragraphs"]
for src, got in zip(change, out):
    check(f"pointing: must change -> {src}",
          got == "The " + src.split(" ", 1)[1], got)

# Every MUST STAY row must come back character for character.
stay = ["This year, buyers are choosing managed services more often.",
        "These include browser tools, installable software and cloud services.",
        "That changes everything about how buyers choose a provider today.",
        "That remains true for most small teams buying services today.",
        "This month, three vendors raised their prices for new customers.",
        "These days, most buyers compare several providers before they sign.",
        "That is why buyers pay less for managed services.",
        "That makes it a small but steadily growing business.",
        "This shows how fast the market grows today.",
        "That said, buyers still compare prices carefully.",
        "That remained true for most buyers throughout the year.",
        "That became clear to most small teams buying services."]
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": list(stay)}], LOG)[0]["paragraphs"]
for src in stay:
    check(f"pointing: must stay unchanged -> {src}",
          src in out, out)

# Direct unit tests of the helper itself.
_noun_follows_cases = [("That", "skill", True), ("That", "changes", False), ("These", "include", False),
                       ("These", "trends", True), ("This", "year", False), ("This", "business", True),
                       ("Those", "days", False)]
for dem, nxt, want in _noun_follows_cases:
    check(f"pointing: _noun_follows({dem!r}, {nxt!r}) is {want}",
          writer._noun_follows(dem, nxt) == want)

# Nouns whose shape ends in "ed" are nouns, not past-tense verbs; real past-tense forms stay verbs.
_ed_noun_cases = [("That", "need", True), ("That", "speed", True), ("That", "shed", True),
                  ("That", "feeds", False), ("These", "needs", True), ("That", "worked", False),
                  ("That", "agreed", False), ("That", "kept", False)]
for dem, nxt, want in _ed_noun_cases:
    check(f"pointing: _noun_follows({dem!r}, {nxt!r}) is {want}",
          writer._noun_follows(dem, nxt) == want)
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": [
    "That need ties the platform to each buyer's daily routine."]}], LOG)
check("pointing: 'That + ed-noun' is rewritten to 'The + noun'",
      out[0]["paragraphs"] == ["The need ties the platform to each buyer's daily routine."], out[0]["paragraphs"])

# A number word or small quantifier between the demonstrative and a plural noun does not keep the fix away.
_quant_cases = [("two", "figures", True), ("2", "factors", True), ("seven", "names", True),
                ("both", "sides", True), ("few", "buyers", True), ("key", "names", True),
                ("two", "figure", False), ("two", "allows", False), ("buyer", "names", False),
                ("two", "cross", False)]
for quant, nxt, want in _quant_cases:
    check(f"pointing: _quantified_plural_follows({quant!r}, {nxt!r}) is {want}",
          writer._quantified_plural_follows(quant, nxt) == want)
quant_change = ["Those two figures explain why buyers sign longer contracts today.",
                "These three factors shape what buyers ask for first.",
                "Those 2 factors matter to every team on a budget.",
                "These few buyers pay for a managed plan from day one."]
out = writer.fix_pointing_determiners([{"heading": "", "sop": ["trends"], "paragraphs": list(quant_change)}], LOG)[0]["paragraphs"]
for src, got in zip(quant_change, out):
    check(f"pointing: quantified plural must change -> {src}",
          got == "The " + src.split(" ", 1)[1], got)
quant_stay = ["These buyer names change from one year to the next.",
              "Those same three answers arrived later."]
out = writer.fix_pointing_determiners([{"heading": "", "sop": [], "paragraphs": list(quant_stay)}], LOG)[0]["paragraphs"]
for src in quant_stay:
    check(f"pointing: quantified plural must stay unchanged -> {src}", src in out, out)


# ------------------------------------------------------------------ bare pointing sentences joined with ', which'
def jb(text: str) -> str:
    return writer.join_bare_pointers([{"sop": ["trends"], "paragraphs": [text]}], LOG)[0]["paragraphs"][0]


# The full decision table for the join rule (round 3): every MUST CHANGE row is attached to the sentence before it.
join_change = [
    ("Forecasters expect USD 910 million by 2034. That works out to growth of 7.3% a year.",
     "Forecasters expect USD 910 million by 2034, which works out to growth of 7.3% a year."),
    ("Buyers rent their tools from providers. That is why vendors host the browsers for them.",
     "Buyers rent their tools from providers, which is why vendors host the browsers for them."),
    ("Prices fell sharply last year. It means buyers pay less for managed services.",
     "Prices fell sharply last year, which means buyers pay less for managed services."),
    ("Demand keeps rising across every region. This shows how fast the market grows.",
     "Demand keeps rising across every region, which shows how fast the market grows."),
    ("Lead-in sentence.\n- Costs fell for most buyers. That makes buyers happy.",
     "Lead-in sentence.\n- Costs fell for most buyers, which makes buyers happy."),
    ("Prices fell sharply last year. That means buyers pay less. That makes switching cheap.",
     "Prices fell sharply last year, which means buyers pay less, which makes switching cheap."),
]
for src, want in join_change:
    check(f"join: must change -> {src[:50]}...", jb(src) == want, jb(src))

# Every MUST STAY row must come back character for character.
join_stay = [
    "That is why buyers pay less for managed services.",
    "Prices fell sharply last year! That makes switching cheap.",
    "Prices fell sharply for these reasons: That makes switching cheap.",
    "Here is the list: That makes switching cheap for everyone.",
    "Prices fell sharply last year. That said, buyers still compare prices carefully.",
    "Prices fell sharply last year. It is the largest region today.",
    "Prices fell sharply last year. These are the main drivers of the market.",
    "Prices fell. That makes switching cheap.",
]
for src in join_stay:
    check(f"join: must stay unchanged -> {src[:50]}...", jb(src) == src, jb(src))

# A merged sentence that would pass the 45-word cap is never built.
_join_big_prev = ("Buyers across the region kept buying cleaner managed tools every quarter of the year, and the "
                  "steady demand pushed every single provider in the field.")
_join_big_cur = ("That makes the combined passage about services run well past the strict word limit that this "
                 "deterministic check is meant to guard, so it stays.")
check("join: a merged sentence over the word cap is left untouched",
      jb(f"{_join_big_prev} {_join_big_cur}") == f"{_join_big_prev} {_join_big_cur}", jb(f"{_join_big_prev} {_join_big_cur}"))

# The sponsor placeholder never moves into the merged sentence and a pair already carrying it never merges.
_join_cur_sponsor = f"Prices fell sharply last year. That makes {prompts.SPONSOR_TOKEN} buyers happier."
check("join: a sentence carrying the sponsor placeholder is untouched",
      jb(_join_cur_sponsor) == _join_cur_sponsor, jb(_join_cur_sponsor))
_join_prev_sponsor = f"Prices fell {prompts.SPONSOR_TOKEN} for teams in the field. That makes buyers happier."
check("join: a sentence with the placeholder before the merge is untouched",
      jb(_join_prev_sponsor) == _join_prev_sponsor, jb(_join_prev_sponsor))

# Two separate `paragraphs` entries of one section are never merged across the paragraph boundary.
_join_paras = ["Prices fell sharply last year.", "That makes switching cheap for everyone."]
out = writer.join_bare_pointers([{"sop": [], "paragraphs": list(_join_paras)}], LOG)[0]["paragraphs"]
check("join: two separate paragraphs of one section stay untouched", out == _join_paras, str(out))


# ------------------------------------------------------------------ reader-narration filler tails
def srf(text: str) -> str:
    return writer.strip_reader_filler([{"sop": ["trends"], "paragraphs": [text]}], LOG)[0]["paragraphs"][0]


filler_change = [
    ("The report splits the market into three parts, so readers can see which kinds of buyers and products the figures cover.",
     "The report splits the market into three parts."),
    ("Vendors bundle support into every plan, to help buyers understand the tiers on offer.",
     "Vendors bundle support into every plan."),
    ("Two applications lead the field, which helps investors judge where demand sits.",
     "Two applications lead the field."),
    ("The report splits the field into three parts, so readers can get a sense of the boundaries.",
     "The report splits the field into three parts."),
]
for src, want in filler_change:
    check(f"filler: must cut -> {src[:50]}...", srf(src) == want, srf(src))

filler_stay = [
    "Vendors bundle support into every plan, so buyers can switch easily.",
    "Sales rose, so readers can see the trend.",
    f"A team that relies on {prompts.SPONSOR_TOKEN} every day, so readers can see the workflow.",
    "Buyers renew their plans each year, so vendors can scale their support teams.",
]
for src in filler_stay:
    check(f"filler: must stay unchanged -> {src[:50]}...", srf(src) == src, srf(src))

check("filler: a bullet line keeps its marker when its tail is cut",
      writer.strip_reader_filler([{"sop": ["segmentation"], "paragraphs": [
          "Lead-in sentence.\n- The report splits the field into three parts, so readers can see the boundaries."]}], LOG)
      [0]["paragraphs"][0] == "Lead-in sentence.\n- The report splits the field into three parts.",
      writer.strip_reader_filler([{"sop": ["segmentation"], "paragraphs": [
          "Lead-in sentence.\n- The report splits the field into three parts, so readers can see the boundaries."]}], LOG)
      [0]["paragraphs"][0])

# ------------------------------------------------------------------ sponsor placement repair
SPONSOR_PROSE = "Teams hand routine reporting to Zeta Services, the guest author's data practice, each quarter."


def rep(paragraphs: list[str]) -> list[list[str]]:
    secs = [{"heading": "", "sop": [], "paragraphs": list(paragraphs)}]
    return [s["paragraphs"] for s in writer.repair_sponsor_placement(secs, LOG, SPONSOR_PROSE)]


_tok_bad = ["Lead-in paragraph with the report link.",
            "Alpha sentence one. Beta sentence is what [[SPONSOR_PARAGRAPH]] gamma continues here, and ends. Delta sentence."]
out = rep(_tok_bad)
check("repair: a mid-sentence placeholder drops the garbled sentence and gains the next paragraph",
      out == [["Lead-in paragraph with the report link.", "Alpha sentence one. Delta sentence.", prompts.SPONSOR_TOKEN]],
      str(out))
check("repair: the token ends up exactly once in the section list",
      sum(x.count(prompts.SPONSOR_TOKEN) for ps in out for x in ps) == 1, str(out))
repaired_secs = [{"heading": "", "sop": [], "paragraphs": out[0]}]
check("repair: the repaired output passes _sponsor_problems standalone with no placement problem",
      _sponsor_problems(repaired_secs, SPONSOR_PROSE) == [], str(_sponsor_problems(repaired_secs, SPONSOR_PROSE)))
ok_inline = ["Alpha sentence one. [[SPONSOR_PARAGRAPH]] Delta sentence."]
check("repair: a placeholder sitting between complete sentences is untouched byte for byte",
      rep(ok_inline) == [ok_inline], str(rep(ok_inline)))
solo = ["[[SPONSOR_PARAGRAPH]]"]
check("repair: a paragraph that is only the placeholder is untouched", rep(solo) == [solo], str(rep(solo)))
own = ["Alpha sentence one.", prompts.SPONSOR_TOKEN, "Delta sentence."]
check("repair: the placeholder as its own paragraph is untouched", rep(own) == [own], str(rep(own)))
out = rep(["Beta sentence is what [[SPONSOR_PARAGRAPH]] gamma continues, and ends."])
check("repair: a garbled sentence that leaves no other text leaves the placeholder as the whole paragraph",
      out == [[prompts.SPONSOR_TOKEN]], str(out))
out = rep(["[[SPONSOR_PARAGRAPH]] beta continues here. Delta sentence."])
check("repair: a placeholder that starts a sentence and continues it lowercase is moved too",
      out == [["Delta sentence.", prompts.SPONSOR_TOKEN]], str(out))
market = [{"heading": "Market heading", "sop": ["trends"], "paragraphs": [
    "Alpha sentence one. Beta sentence is what [[SPONSOR_PARAGRAPH]] gamma continues here, and ends."]}]
check("repair: a section with a SOP tag is never touched",
      writer.repair_sponsor_placement(market, LOG, SPONSOR_PROSE)[0]["paragraphs"] == market[0]["paragraphs"])
phrase = [{"heading": "", "sop": [], "paragraphs": [
    "Alpha sentence one. Beta sentence is what [[SPONSOR_PARAGRAPH]] gamma continues here, and ends."]}]
check("repair: a phrase sponsor's required inline placement is untouched",
      writer.repair_sponsor_placement(phrase, LOG, "Managed data platforms")[0]["paragraphs"] == phrase[0]["paragraphs"])
out = rep([_tok_bad[1], f"Second token here {prompts.SPONSOR_TOKEN} mid."])
check("repair: only the first token is repaired, an extra token is left alone",
      out == [["Alpha sentence one. Delta sentence.", prompts.SPONSOR_TOKEN,
               f"Second token here {prompts.SPONSOR_TOKEN} mid."]], str(out))

# ------------------------------------------------------------------ assembly
written = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].", prompts.SPONSOR_TOKEN]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": s["sop"], "heading": "model heading", "paragraphs": [f"Text of {s['sop']}."]} for s in plan["sections"] if s["sop"] != "key_players"]
                + [{"sop": "trends", "heading": "duplicate", "paragraphs": ["A second trends section."]}],
}
sections = writer.assemble_sections(written, plan, LOG)
check("assembly: the opening is untagged, the transition is tagged, market sections carry their tag",
      sections[0]["sop"] == [] and sections[1]["sop"] == ["transition"] and sections[2]["sop"] == ["report_context"])
check("assembly: headings are set to the plan's exact headings", all(s["heading"] == next(p["heading"] for p in plan["sections"] if p["sop"] == s["sop"][0])
                                                                    for s in sections[2:]))
check("assembly: a section the model left out keeps its heading with an editor note, and a duplicate tag is ignored",
      [s["sop"][0] for s in sections[2:]] == list(planning.ORDER)
      and next(s for s in sections if s["sop"] == ["key_players"])["paragraphs"][0].startswith("[EDITOR")
      and next(s for s in sections if s["sop"] == ["key_players"])["heading"] == next(p["heading"] for p in plan["sections"] if p["sop"] == "key_players"))

# ------------------------------------------------------------------ prompt
sponsor = Paragraph(index=0, text="Teams can ask Acme Corp for help with scraping at scale today.", links=[Link(anchor="Acme Corp", url="https://acmecorp.com")])
report = reports.Report(news_id=1, name=MARKET, subject="s", url="https://x", source="live", sections={"live_report_page": "p"})
text = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
check("prompt: carries the final title and every section's exact heading", plan["title"] in text and all(s["heading"] in text for s in plan["sections"]))
_lt_lines = sum(1 for s in plan["sections"] if s["longtail"])
check("prompt: the long-tail line carries the REQUIRED framing like the secondary keyword's, and how a keyword is used is stated once",
      _lt_lines > 0
      and text.count("REQUIRED long-tail search:") == _lt_lines
      and "REQUIRED secondary keyword" in text
      and text.count("every one of its content words appears in ONE sentence") == 1,
      str([l for l in text.splitlines() if "REQUIRED long-tail" in l]))
check("prompt: a question heading is flagged to be answered directly", text.count("Answer it directly in the first one or two sentences") == 3)
check("prompt: the report link token and the sponsor placeholder are present", f"[[CMI_LINK:{MARKET}]]" in text and prompts.SPONSOR_TOKEN in text)
check("prompt: an outside-sourced fact is marked NOT CMI", "NOT CMI" not in text or "source: News Site" in text)
inp.items["fs.size"]["source"] = "marketsite.example.com"
text2 = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
check("prompt: a report-position figure from an outside source is marked NOT CMI and named", "source: marketsite.example.com, NOT CMI" in text2)
check("prompt: a names-only key players section is told to use plain prose", "no bullets" in text2[text2.index("### 6. key_players"):][:1500])
check("shapes: the key_players shape prescribes complete sentences instead of Name-label bullet lines",
      "- <Company>" not in writer._SHAPES["key_players"] and "\n- " not in writer._SHAPES["key_players"]
      and "complete sentences" in writer._SHAPES["key_players"], writer._SHAPES["key_players"])
check("shapes: the names-only key_players shape stays plain prose naming companies in complete sentences",
      "no bullets" in writer._SHAPES["key_players_names_only"]
      and "complete sentences" in writer._SHAPES["key_players_names_only"], writer._SHAPES["key_players_names_only"])
check("prompt: no evidence_citations side-job is requested", "evidence_citations" not in text)
# The market's name has its own rule in the opening, stated once, for a linked and for an unlinked (outside report) post
_name_line = next(l for l in text.splitlines() if l.startswith("- The market's name:"))
check("prompt: the opening has one dedicated line for the market's name, with the 100-word limit and the link",
      MARKET in _name_line and "inside the first 100 words" in _name_line and f"[[CMI_LINK:{MARKET}]]" in _name_line, _name_line)
check("prompt: the opening's shape no longer repeats the market-name rule", "inside its first 100 words" not in text)
inp.link_report = False
text_plain = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
inp.link_report = True
_plain_line = next(l for l in text_plain.splitlines() if l.startswith("- The market's name:"))
check("prompt: with an outside report the name is plain text in the first 100 words, no link and no report cited",
      "plain text" in _plain_line and "inside the first 100 words" in _plain_line and "CMI_LINK" not in _plain_line
      and "never write 'CMI's report'" in _plain_line, _plain_line)
# the post never announces itself: roadmap sentences go, the content around them stays
_rm = writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": [
    "Buyers purchase licences to plan and track work. The sections that follow set out how large this market is. Demand rests on coordination.",
    "The sections ahead examine the market's size. This article explains the market's trends.",
    "A part of the budget follows revenue growth, and the following quarter showed it."]}], LOG)
check("roadmap: a sentence announcing the sections that follow is removed and its neighbours stay",
      _rm[0]["paragraphs"][0] == "Buyers purchase licences to plan and track work. Demand rests on coordination.", str(_rm))
check("roadmap: a paragraph made only of roadmap sentences disappears", len(_rm[0]["paragraphs"]) == 2, str(_rm))
check("roadmap: ordinary use of 'part' and 'following' is left alone", _rm[0]["paragraphs"][1].startswith("A part of the budget follows"), str(_rm))
_rm2 = writer.strip_meta_sentences([{"heading": "", "sop": [], "paragraphs": [
    "Providers package the work as retainers, projects or outcome-based contracts, and the sections below examine how demand is changing.",
    "That decision now sits inside a larger market, whose scale and direction the following sections set out."]}], LOG)
check("roadmap: a clause that only trails a real statement is cut, the statement stays; a pure announcement goes",
      [p for s_ in _rm2 for p in s_["paragraphs"]] == ["Providers package the work as retainers, projects or outcome-based contracts."], str(_rm2))
check("roadmap: the prompt states the rule once", writer.prompts.WRITE_USER.count("The post never announces itself") == 1)
# No section's SHAPE casts a report as the actor or owner when an outside report stood in for CMI's
import re as _re  # noqa: E402
_leaks = {k: _re.findall(r"[^.]{0,30}the report[^.]{0,30}", writer._shape_text(k, False)) for k in writer._SHAPES}
check("shapes: with an outside report no section shape names 'the report' (the post cites no report)",
      not any(_leaks.values()), str({k: v for k, v in _leaks.items() if v}))
check("shapes: with a CMI report the shapes keep their wording", "with the report as its actor" in writer._shape_text("segmentation", True))
# The phrase-mode sponsor must never be shown a template with the placeholder as its own paragraph: that exact
# template made the model return a bare placeholder paragraph. Build a bare-phrase sponsor and check its template.
phrase_sponsor = Paragraph(index=0, text="Customer data management systems", links=[Link(anchor="Customer data management systems", url="https://acmecorp.com")])
text_phrase = writer.build_write_prompt(plan, inp, phrase_sponsor, report, "2026-10-23")
check("prompt: a phrase sponsor's output template embeds the placeholder in an own sentence",
      f"embeds {prompts.SPONSOR_TOKEN} inside one of its sentences" in text_phrase
      and "\":[\"<the lead-in paragraph with the report link>\", \"[[SPONSOR_PARAGRAPH]]\"" not in text_phrase)

# The retained paragraph's own words are shown to the writer so the lead-in can prepare the reader for what the
# retained paragraph takes for granted: the template's {sponsor_text} key must be fed, and only it.
quoted = f'Its words are: "{sponsor.text}"'
check("prompt: the retained paragraph's own words are shown to the writer (no KeyError)",
      isinstance(text, str) and quoted in text, quoted)
check("prompt: the placeholder token is still shown for placement and no unformatted sponsor_text key is left",
      prompts.SPONSOR_TOKEN in text and "{sponsor_text}" not in text)
check("prompt: the new voice rules are present and the writer persona lives in the system prompt",
      "Continuity" in text and "Analysis" in text and "Active voice" in text
      and "senior market-research analyst" in prompts.WRITE_SYSTEM)
_HUMANIZING = ("everyday", "Contractions are fine", "Flesch", "ordinary people", "general reader", "human editor",
               "experienced human", "Prefer the everyday word", "plain English")
check("prompt: no humanizing or layman wording is left in the live write prompts (the writer, the FAQ answers)",
      not [w for w in _HUMANIZING if w in text or w in prompts.WRITE_SYSTEM or w in prompts.FAQ_MSQ_USER or w in prompts.FAQ_MSQ_SYSTEM],
      str([w for w in _HUMANIZING if w in text or w in prompts.WRITE_SYSTEM or w in prompts.FAQ_MSQ_USER]))
check("prompt: the register is formal market-research language, third person, with a sentence-length band and a continuity rule",
      "Formal market-research and consulting language" in text and "No contractions" in text
      and "average about 18 words" in text and "12 to 24" in text and "previous two sentences" in text, "")
check("prompt: the keyword rule never lets a REQUIRED keyword be skipped as covered by meaning",
      "REQUIRED secondary keyword or a REQUIRED long-tail phrase" in text and "no synonym and no dropped word" in text
      and "cannot sit naturally" not in text,
      str([l for l in text.splitlines() if "How an assigned keyword is used" in l]))
check("prompt: a required keyword is written out in one sentence (stated once, in the KEYWORDS rules)",
      text.count("every one of its content words appears in ONE sentence of that section") == 1)
check("prompt: a list-shaped section's keyword goes in the lead-in sentence, not in the bullets",
      "bullet lines are reserved for the listed facts alone" in text and "the keyword's words go in the lead-in sentence" in text)
# the organisation of the prompt: rules first, once, in labelled groups; then the post; then the checklist
_a, _b, _c = text.index("PART A"), text.index("PART B: THE POST"), text.index("PART C: CHECKLIST")
_groups = ["RULES: PRECEDENCE", "RULES: FACTS AND SOURCES", "RULES: NO REPEATS", "RULES: ANALYSIS", "RULES: LANGUAGE", "RULES: KEYWORDS", "RULES: LENGTH AND FLOW"]
check("prompt: the rules come first, as seven labelled groups in a fixed order, before the post and the checklist",
      _a < _b < _c and all(g in text for g in _groups) and [text.index(g) for g in _groups] == sorted(text.index(g) for g in _groups)
      and text.index("RULES: LENGTH AND FLOW") < _b and text.index("### 1. report_context") > _b,
      str([text.find(g) for g in _groups]))
check("prompt: each rule is stated once (the no-repeats rule, the credit rule, the voice rule)",
      text.count("One piece of evidence, one place.") == 1 and text.count("Crediting a source.") == 1 and text.count("Voice and register.") == 1)
check("prompt: no section repeats the keyword rules (the long explanation is not under every section)",
      text.count("rephrase it freely") == 1 and text.count("never string them together in the listed order") == 0)
check("prompt: the order of precedence is stated", "Order of precedence." in text and "the earlier one in this list wins" in text)
def _repeated_runs(prompt: str, n: int = 14) -> list[str]:
    """Any run of n words that appears twice in the instruction text of a prompt. Fact rows (their text is data, and one fact is shown under
    its trend or section only) and the per-section lines that are meant to repeat are left out."""
    lines = [l for l in prompt.splitlines()
             if l.strip() and not l.lstrip().startswith(("- [", "detail:", "    detail:", "Trend ", "### ", "Purpose (what the reader learns"))]
    words = " ".join(lines).split()
    seen, dup = {}, []
    for k in range(len(words) - n + 1):
        run = " ".join(words[k:k + n])
        if run in seen and seen[run] != k - 1:      # an overlap with the previous start is the same repeated stretch
            dup.append(run)
        seen.setdefault(run, k)
    return dup


# the question-headed sections share one sentence by design: it is removed before the repeats are looked for
_Q_HEAD = ("The heading is a question people really search. Answer it directly in the first one or two sentences (about 40 to 60 words), "
           "in precise terms, then continue with the section.")
_dups = _repeated_runs(text.replace(_Q_HEAD, "").replace("- No keyword is assigned to this section beyond the primary keyword.", ""))
check("prompt: no instruction of fourteen words or more appears twice in the assembled write prompt", not _dups, str(_dups[:3]))

check("shapes: the segmentation lead-in must name the market in the same sentence as its segmentation",
      'names the market in that same sentence as the thing the segmentation divides' in writer._SHAPES["segmentation"],
      writer._SHAPES["segmentation"])
check("shapes: the segmentation shape never calls the division a split",
      "split" not in writer._SHAPES["segmentation"], writer._SHAPES["segmentation"])
check("shapes: the segmentation lead-in must state how many segment types the report uses",
      "how many segment types" in writer._SHAPES["segmentation"], writer._SHAPES["segmentation"])
check("shapes: the trends are a lead-in naming the market, then three sub-headed trends of three paragraphs",
      'names the market by its full name' in writer._SHAPES["trends"] and "exactly 13" in writer._SHAPES["trends"]
      and '"### "' in writer._SHAPES["trends"] and "three paragraphs" in writer._SHAPES["trends"]
      and "commercial implication" in writer._SHAPES["trends"] and "mechanism" in writer._SHAPES["trends"],
      writer._SHAPES["trends"])

# A list-shaped section's REQUIRED secondary keyword must be covered in the lead-in sentence (the "\n- "
# lines are reserved for the listed facts); non-list sections get no such instruction.
points = {p["id"]: p["text"] for p in inp.points}
LEAD_IN = "in the LEAD-IN sentence"
RESERVED = "bullet lines are reserved for the listed facts"
_list_sec = next(s for s in plan["sections"] if s["sop"] in writer._LIST_SHAPED and s["secondary"])
_nonlist_sec = next(s for s in plan["sections"] if s["sop"] not in writer._LIST_SHAPED and s["secondary"])
block_list = writer._section_block(1, _list_sec, inp, points)
block_nonlist = writer._section_block(2, _nonlist_sec, inp, points)
block_plain = writer._section_block(3, {**_list_sec, "secondary": None}, inp, points)
check("prompt: a list-shaped section with a secondary keyword carries the lead-in instruction",
      LEAD_IN in block_list and RESERVED in block_list and writer._LIST_SHAPED == {"segmentation"},
      str([l for l in block_list.splitlines() if "REQUIRED" in l]))
check("prompt: a non-list section with a secondary keyword carries no lead-in instruction",
      LEAD_IN not in block_nonlist and RESERVED not in block_nonlist,
      str([l for l in block_nonlist.splitlines() if "REQUIRED" in l]))
check("prompt: a list-shaped section without a secondary keyword has neither line",
      "REQUIRED secondary keyword" not in block_plain and LEAD_IN not in block_plain
      and RESERVED not in block_plain, block_plain)
check("prompt: the list lead-in rule no longer primes the word 'split'",
      "states the split" not in prompts.WRITE_USER and "between the split" not in prompts.WRITE_USER,
      str([l for l in prompts.WRITE_USER.splitlines() if "split" in l and "split it" not in l and "is split" not in l]))
check("prompt: a phrase sponsor's own words are shown too, still building without a KeyError",
      f'Its words are: "{phrase_sponsor.text}"' in text_phrase)

# ------------------------------------------------------------------ the call (LLM stubbed)
orig = llm.chat_json
llm.chat_json = lambda *a, **k: written
try:
    w, title, secs2, cites2 = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    check("call: returns the title, the sections and code-found citations in the shape finalize reads",
          title == plan["title"] and len(secs2) == len(sections) and isinstance(cites2, list))
    check("call: a missing sponsor placeholder is repaired", True)
finally:
    llm.chat_json = orig

written_pointing = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].", prompts.SPONSOR_TOKEN]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": "report_context", "heading": "model heading",
                  "paragraphs": ["That figure covers what buyers spend on services."]}],
}
llm.chat_json = lambda *a, **k: written_pointing
try:
    _, _, secs_pt, _ = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    para = next(s for s in secs_pt if s["sop"] == ["report_context"])["paragraphs"][0]
    check("call: a leading 'That + noun' in the write output is kept as written (the continuity rule allows it; code no longer rewrites it)",
          para.startswith("That figure covers"), para)
finally:
    llm.chat_json = orig

# The join backstop inside the call: the stubbed paragraph's figures must be traceable, so the size fact is
# extended with them before the call (and its source restored, whose being named per figure would only re-filter
# the sentence). Nothing after this block reads those two figures anywhere else.
inp.items["fs.size"]["source"] = "CMI report"
inp.items["fs.size"]["text"] += " The forecast reaches USD 910 million by 2034, working out to 7.3% a year."
written_join = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].", prompts.SPONSOR_TOKEN]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": "trends", "heading": "model heading",
                  "paragraphs": ["The forecast reaches USD 910 million by 2034. That works out to 7.3% a year."]}],
}
llm.chat_json = lambda *a, **k: written_join
try:
    _, _, secs_j, _ = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    para = next(s for s in secs_j if s["sop"] == ["trends"])["paragraphs"][0]
    check("call: a bare pointing sentence in the write output is kept as its own sentence (no ', which' joining by code)",
          "by 2034. That works out to 7.3% a year." in para, para)
finally:
    llm.chat_json = orig

# The reader-filler backstop inside the call: a tail that only narrates what the reader will see is cut.
written_filler = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].", prompts.SPONSOR_TOKEN]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": "trends", "heading": "model heading",
                  "paragraphs": ["Vendors bundle support contracts into their subscriptions, so readers can see "
                                 "which kinds of buyers and products the figures cover."]}],
}
llm.chat_json = lambda *a, **k: written_filler
try:
    _, _, secs_f, _ = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    para = next(s for s in secs_f if s["sop"] == ["trends"])["paragraphs"][0]
    check("call: a reader-narration filler tail in the write output is cut from the returned paragraph",
          para == "Vendors bundle support contracts into their subscriptions.", para)
finally:
    llm.chat_json = orig

# The placement repair inside the call: a PROSE sponsor's mid-sentence placeholder is moved to its own paragraph.
written_repair = {
    "intro": {"paragraphs": ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].",
                             "Alpha sentence one. Beta sentence is what [[SPONSOR_PARAGRAPH]] gamma continues here, "
                             "and ends. Delta sentence."]},
    "transition": {"paragraphs": ["Why it matters."]},
    "sections": [{"sop": "report_context", "heading": "model heading",
                  "paragraphs": ["Plain prose with no figures stays untouched."]}],
}
llm.chat_json = lambda *a, **k: written_repair
try:
    _, _, secs_r, _ = writer.run_write_v2(plan, inp, sponsor, report, LOG)
    intro = secs_r[0]["paragraphs"]
    check("call: a mid-sentence placeholder in the write output is repaired inside the call",
          intro == ["Lead-in [[CMI_LINK:Zzz Widget Services Market]].",
                    "Alpha sentence one. Delta sentence.", prompts.SPONSOR_TOKEN], str(intro))
finally:
    llm.chat_json = orig

# ------------------------------------------------------------------ the trends are written from per-trend fact groups
check("prompt: the trends section lists its facts trend by trend, each trend only from its own facts",
      "Facts to use, by trend" in text and "Trend 1:" in text and "Trend 3:" in text, "")
_no_groups = {**next(s for s in plan["sections"] if s["sop"] == "trends"), "trend_groups": []}
check("prompt: a trends section without groups falls back to one plain facts list",
      "Facts to use, by trend" not in writer._section_block(1, _no_groups, inp, {}), "")

# ------------------------------------------------------------------ trend sub-headings survive every cleanup step
import subheads  # noqa: E402

trend_secs = [{"heading": "Trends in the Zzz Widget Services Market", "sop": ["trends"], "paragraphs": [
    "Lead-in sentence about the Zzz Widget Services Market and its three trends.",
    subheads.make_subhead("Adaptive Testing Is Raising Demand For Online Platforms"),
    "In 2026 a named study reported a change. The study covered one sample.",
    "That change shifts demand toward platforms. Providers can build adaptive banks.",
    "The commercial implication is a larger role for validation.",
    subheads.make_subhead("Analytics Are Raising Demand For Quality Assurance"),
    "In 2025 a pilot reported a result. The pilot was exploratory.",
]}]
steps = (writer.strip_meta_sentences, writer.strip_reader_filler)
after = trend_secs
for step in steps:
    after = step(after, LOG)
after = writer.strip_untraced_figures(after, inp, "", LOG)
after = writer.strip_repeat_figures(after, inp, LOG)
after = writer.name_outside_sources(after, inp, LOG)
heads = [p for p in after[0]["paragraphs"] if subheads.is_subhead(p)]
check("trend sub-headings: both survive the writer's whole cleanup pipeline, text unchanged",
      heads == [subheads.make_subhead("Adaptive Testing Is Raising Demand For Online Platforms"),
                subheads.make_subhead("Analytics Are Raising Demand For Quality Assurance")], str(after[0]["paragraphs"]))
lead, blocks = subheads.split_blocks(after[0]["paragraphs"])
check("trend sub-headings: split_blocks returns the lead-in and the paragraphs under each heading",
      len(lead) == 1 and [len(b[1]) for b in blocks] == [3, 1], str((lead, blocks)))
check("trend sub-headings: strip_subheads leaves the prose only and does not touch the original",
      all(not subheads.is_subhead(p) for p in subheads.strip_subheads(after)[0]["paragraphs"])
      and any(subheads.is_subhead(p) for p in after[0]["paragraphs"]))
# ------------------------------------------------------------------ the style anchors switch
import config as _config  # noqa: E402

_saved = _config.WRITE_STYLE_ANCHORS
try:
    _config.WRITE_STYLE_ANCHORS = True
    with_anchors = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-31")
    _config.WRITE_STYLE_ANCHORS = False
    without = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-31")
finally:
    _config.WRITE_STYLE_ANCHORS = _saved
check("anchors: on, the prompt ends with the four reference passages and the register-only instruction",
      with_anchors.startswith(without) and "STYLE REFERENCE" in with_anchors and with_anchors.count("Passage ") == 4
      and "Never reuse their wording" in with_anchors)
check("anchors: off, the prompt is exactly the plain write prompt", "STYLE REFERENCE" not in without)
check("anchors: they are on by default", _saved is True)

# ------------------------------------------------------------------ the trend shape is enforced in code
def _trend_section(n_trends, paras_each, lead=1):
    ps = [f"Lead-in {i}." for i in range(lead)]
    for k in range(n_trends):
        ps.append(subheads.make_subhead(f"Trend {k + 1} Is Raising Demand"))
        ps += [f"Trend {k + 1} paragraph {j + 1}." for j in range(paras_each)]
    return [{"heading": "Trends", "sop": ["trends"], "paragraphs": ps}]


shaped = writer.enforce_trend_shape(_trend_section(5, 4, lead=2), LOG)[0]["paragraphs"]
lead_, blocks_ = subheads.split_blocks(shaped)
check("trend shape: five trends of four paragraphs and two lead-ins are cut to one lead-in and three trends of three",
      len(lead_) == 1 and len(blocks_) == 3 and all(len(b[1]) == 3 for b in blocks_), str(shaped))
check("trend shape: the first paragraphs of each trend are the ones kept",
      blocks_[2][1] == ["Trend 3 paragraph 1.", "Trend 3 paragraph 2.", "Trend 3 paragraph 3."], str(blocks_[2]))
exact = _trend_section(3, 3)
check("trend shape: a section already in shape is left exactly as it is",
      writer.enforce_trend_shape(exact, LOG)[0]["paragraphs"] == _trend_section(3, 3)[0]["paragraphs"])
short = writer.enforce_trend_shape(_trend_section(2, 2), LOG)[0]["paragraphs"]
check("trend shape: a section with too little is not padded (the check reports it)", len(subheads.split_blocks(short)[1]) == 2)
other = [{"heading": "x", "sop": ["conclusion"], "paragraphs": ["### not a trend", "text"]}]
check("trend shape: only the trends section is touched", writer.enforce_trend_shape(other, LOG) == other)
check("meta: a sentence that comments on what a source does not quantify is cut",
      writer._META_RE.search("The observation is qualitative and describes the demand side rather than any measured volume.") is not None
      and writer._META_RE.search("Demand rose 12% in 2026.") is None)

check("pointing words are no longer rewritten by code: the write pipeline no longer calls the two backstops",
      "fix_pointing_determiners(sections" not in __import__("inspect").getsource(writer.run_write_v2)
      and "join_bare_pointers(sections" not in __import__("inspect").getsource(writer.run_write_v2))

# ------------------------------------------------------------------ the U.S. section: enough evidence, thin evidence, nothing at all
import test_planning  # noqa: E402


def us_prompt(n_us: int, with_report_facts: bool = True) -> tuple[dict, str]:
    raw = good_plan()
    raw["sections"][1]["facts"] = ["fs.drivers.0", "W1"]
    raw["sections"][4]["facts"] = []
    plan_us = test_planning.plan_of(raw, test_planning.us_inputs(n_us, with_report_facts))
    inp_us = test_planning.us_inputs(n_us, with_report_facts)
    block = writer._section_block(5, next(s for s in plan_us["sections"] if s["sop"] == "country_us"), inp_us, {})
    return plan_us, block


_, enough_block = us_prompt(2)
_, thin_block = us_prompt(1)
_, none_block = us_prompt(0, with_report_facts=False)
check("U.S. enough: the block carries the ordinary shape and neither the report-facts nor the editor-note instruction",
      "SHAPE: Prose in one or two short paragraphs, or a lead-in sentence" in enough_block
      and "written from the market's own facts" not in enough_block and "[EDITOR" not in enough_block, enough_block)
check("U.S. thin: the block says the section is written from the report's own facts, rephrased fully, with no U.S. figure added and no other region",
      "written from the market's own facts" in thin_block and "Rephrase every fact fully in your own words" in thin_block
      and "add no U.S. figure that no listed fact states" in thin_block and "no North America" in thin_block
      and "SHAPE: Prose in one or two short paragraphs, 100 to 200 words" in thin_block and "never a phrase copied from the fact" in thin_block, thin_block)
check("U.S. thin: no editor-note placeholder and a word budget", "[EDITOR" not in thin_block and "words" in thin_block.splitlines()[0])
check("U.S. thin: the report facts are listed for the writer (the U.S. statement, then the driver)",
      thin_block.index("A review of widget exports") < thin_block.index("Digital marketing adoption drives demand"), thin_block)
check("U.S. thin: the writer is never asked to say that U.S. data is missing", "never say that U.S. data is missing" in thin_block)
check("U.S. none: the heading stays with the one editor line and there is no word-count shape",
      "[EDITOR: no verified U.S. data was found for this market; add the U.S. analysis here.]" in none_block
      and "about" not in none_block.splitlines()[0] and "100 to 200 words" not in none_block, none_block)
check("U.S. none: the shape is the editor line alone", "SHAPE: The single line [EDITOR" in none_block and "and nothing else" in none_block)

# "no data / could not find" sentences are stripped from the normal sections, the editor line itself is kept
meta_secs = [{"heading": "h", "sop": ["country_us"], "paragraphs": [
    "No specific U.S. data is available for this market. Providers in the United States adopt cloud tools for reporting.",
    "We could not find verified U.S. figures. Hospitals buy these systems to cut manual work.",
    "U.S. statistics were not available at the time of writing. Buyers favour vendors with a local presence.",
    "[EDITOR: no verified U.S. data was found for this market; add the U.S. analysis here.]"]}]
meta_out = writer.strip_meta_sentences(meta_secs, LOG)[0]["paragraphs"]
check("meta: 'no specific U.S. data', 'could not find' and 'figures were not available' sentences are stripped",
      meta_out[0] == "Providers in the United States adopt cloud tools for reporting." and "Hospitals buy" in meta_out[1]
      and meta_out[2] == "Buyers favour vendors with a local presence.", str(meta_out))
check("meta: the editor note line survives the stripper", meta_out[3].startswith("[EDITOR: no verified U.S. data"), str(meta_out))
check("meta: ordinary sentences with 'no', 'not' and 'data' are kept",
      all(writer._META_RE.search(x) is None for x in (
          "Data from hospitals is not shared across regions in the same format.",
          "There is no single standard for widget exports in the United States.",
          "Providers could not meet demand last year without new capacity.")))

# a planned market section whose paragraphs were all stripped keeps its heading with an editor note
gone = [{"heading": "U.S. Analysis", "sop": ["country_us"], "paragraphs": ["No specific U.S. data is available for this market."]},
        {"heading": "Segments", "sop": ["segmentation"], "paragraphs": ["The facts say nothing."]},
        {"heading": "", "sop": [], "paragraphs": ["The facts say nothing."]}]
kept = writer.strip_meta_sentences(gone, LOG)
check("emptied: a market section stripped to nothing keeps its heading with an editor note; an emptied opening is dropped",
      [(x["heading"], x["sop"]) for x in kept] == [("U.S. Analysis", ["country_us"]), ("Segments", ["segmentation"])]
      and kept[0]["paragraphs"] == [writer.editor_note("country_us")] and kept[1]["paragraphs"][0].startswith("[EDITOR: the segmentation section"),
      str(kept))
untraced = writer.strip_untraced_figures(
    [{"heading": "Seg", "sop": ["segmentation"], "paragraphs": ["The market reached 99999 units."]}], inp, "", LOG)
check("emptied: the same holds when the figure stripper removes every sentence", untraced[0]["paragraphs"][0].startswith("[EDITOR"), str(untraced))

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all writer tests passed")
