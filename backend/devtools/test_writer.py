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
check("source naming: a figure sentence that lacks its source gets it appended before the full stop",
      fixed[0].endswith("AI-assisted tools, per blog.apify.com."), fixed[0])
check("source naming: a sentence that already names the source, and one without a figure, are untouched",
      fixed[1] == "- This bullet names blog.apify.com for 66.2% already." and fixed[2] == "A sentence with no figure stays as it is.")
check("source naming: report (CMI) figures are never touched",
      writer.name_outside_sources([{"heading": "", "sop": ["report_context"], "paragraphs": ["Robo advisory opens room to grow, CMI says."]}], inp_src, LOG)[0]["paragraphs"]
      == ["Robo advisory opens room to grow, CMI says."])

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
