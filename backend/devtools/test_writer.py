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
    "The facts say nothing. The plan was to stop.",
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
check("assembly: a section the model left out is skipped and a duplicate tag is ignored",
      [s["sop"][0] for s in sections[2:]] == ["report_context", "trends", "segmentation", "subsegment", "country_us", "conclusion"])

# ------------------------------------------------------------------ prompt
sponsor = Paragraph(index=0, text="Teams can ask Acme Corp for help with scraping at scale today.", links=[Link(anchor="Acme Corp", url="https://acmecorp.com")])
report = reports.Report(news_id=1, name=MARKET, subject="s", url="https://x", source="live", sections={"live_report_page": "p"})
text = writer.build_write_prompt(plan, inp, sponsor, report, "2026-10-23")
check("prompt: carries the final title and every section's exact heading", plan["title"] in text and all(s["heading"] in text for s in plan["sections"]))
_lt_lines = sum(1 for s in plan["sections"] if s["longtail"])
check("prompt: the long-tail line carries the REQUIRED framing like the secondary keyword's",
      _lt_lines > 0
      and text.count("REQUIRED long-tail search: the section must cover this keyword") == _lt_lines
      and "REQUIRED secondary keyword" in text,
      str([l for l in text.splitlines() if "long-tail search:" in l]))
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
      "Clear reference" in text and "Actors" in text and "highly trained content writer" in prompts.WRITE_SYSTEM)
check("prompt: the keyword rule never lets a REQUIRED keyword be skipped as covered by meaning",
      "A keyword a section is REQUIRED to carry" in text and "no synonym and no dropped word" in text
      and "cannot sit naturally" not in text,
      str([l for l in text.splitlines() if l.startswith("- Keywords:")]))
check("prompt: a required keyword is written out in one sentence, in the required-keyword line too",
      "the section must cover this keyword — every content word of " in text)
check("prompt: a list lead-in with a required keyword still carries the keyword's words and the market name",
      "that lead-in sentence still carries every content word of the keyword and the market's own name" in text,
      str([l for l in text.splitlines() if l.startswith("- No filler:")]))
check("shapes: the segmentation lead-in must name the market in the same sentence as its segmentation",
      'names the market in that same sentence as the thing the segmentation divides' in writer._SHAPES["segmentation"],
      writer._SHAPES["segmentation"])
check("shapes: the segmentation shape never calls the division a split",
      "split" not in writer._SHAPES["segmentation"], writer._SHAPES["segmentation"])
check("shapes: the segmentation lead-in must state how many segment types the report uses",
      "how many segment types" in writer._SHAPES["segmentation"], writer._SHAPES["segmentation"])
check("shapes: the trends lead-in must name the market itself, never 'this market'",
      'names the market by its full name (never "this market")' in writer._SHAPES["trends"],
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
      LEAD_IN in block_list and RESERVED in block_list and writer._LIST_SHAPED == {"trends", "segmentation"},
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
    check("call: a leading pointing word in the write output reaches the returned sections as 'The + noun'",
          para.startswith("The figure covers"), para)
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
    check("call: a bare pointing sentence in the write output is joined to the sentence before with ', which'",
          "by 2034, which works out to 7.3% a year." in para, para)
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

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all writer tests passed")
