"""Offline test for the client links (no network, no real LLM calls).

Run from backend/: python devtools/test_clients.py

The post never names or promotes a client: its paid backlink, on the linked words, is its only mention, set inside sentences that
belong to the opening's own explanation. Covers: grouping a blog's links into clients (one link, several links of one client, several
clients, one paragraph holding two clients), the link tokens and their URLs, the client-website brief (validated, never trusted
blindly, and used only for the accuracy of the sentence around the link), the write prompt's rules, the repair of tokens in the
draft, the link and "client not named" checks, and the whole write step with a stub model.
"""
import dataclasses
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import checks  # noqa: E402
import clients  # noqa: E402
import config  # noqa: E402
import external_reports  # noqa: E402
import llm  # noqa: E402
import pipeline  # noqa: E402
import prompts  # noqa: E402
import reports  # noqa: E402
import writer  # noqa: E402
from docx_io import Link, Paragraph, SourceArticle  # noqa: E402

import test_checks_v2 as t  # noqa: E402

FAILURES: list[str] = []
LOG = logging.getLogger("test_clients")


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


# ------------------------------------------------------------------ grouping
ALD = Link(anchor="research paper help", url="https://www.aldwych.example/papers")
ALD2 = Link(anchor="research help", url="https://aldwych.example/help")
BRX = Link(anchor="thesis editing", url="https://brexton.example/")
CMI = Link(anchor="CMI report", url="https://www.coherentmarketinsights.com/market-insight/x-1")


def article(*paras: Paragraph) -> SourceArticle:
    return SourceArticle(title="T", paragraphs=list(paras))


p0 = Paragraph(0, "For research paper help, we recommend Aldwych Writing Studio. Their writers prepare 120 papers a term.", [ALD])
p1 = Paragraph(1, "For quick questions, research help is available from the same team.", [ALD2])
p2 = Paragraph(2, "Brexton Paper Desk also offers thesis editing in the same field.", [BRX])
p3 = Paragraph(3, "Read the CMI report on this field.", [CMI])
p4 = Paragraph(4, "For research paper help and thesis editing, readers have several options.", [ALD, BRX])
p5 = Paragraph(5, "No link here.", [])
art = article(p0, p1, p2, p3, p4, p5)

one, notes = clients.build_clients(art, [0])
check("one link: one client, one link id", len(one) == 1 and one[0].id == "C1" and [l["id"] for l in one[0].links] == ["L1"] and not notes)
check("one link: the host drops 'www'", one[0].host == "aldwych.example", one[0].host)

same, notes = clients.build_clients(art, [0, 1])
check("same client in two paragraphs: one client, the first chosen paragraph is used",
      len(same) == 1 and same[0].paragraph.index == 0 and len(same[0].links) == 1, str([c.to_dict() for c in same]))
check("same client in two paragraphs: the editor is told", any("aldwych.example" in n for n in notes), str(notes))

many, notes = clients.build_clients(art, [0, 2])
check("two different clients: two clients in the order chosen, link ids run on",
      [c.host for c in many] == ["aldwych.example", "brexton.example"] and [l["id"] for c in many for l in c.links] == ["L1", "L2"])

both, _ = clients.build_clients(art, [4])
check("one paragraph linking two clients: each client gets its own link", [c.host for c in both] == ["aldwych.example", "brexton.example"])

only_b, _ = clients.build_clients(art, [4], hosts=["brexton.example"])
check("ticked hosts: a paragraph with two clients keeps only the ticked one", [c.host for c in only_b] == ["brexton.example"] and len(only_b[0].links) == 1)
check("ticked hosts: a full URL or a www name is read as that client's domain",
      [c.host for c in clients.build_clients(art, [4], hosts=["https://www.aldwych.example/x"])[0]] == ["aldwych.example"])

capped, notes = clients.build_clients(art, [0, 2], max_clients=1)
check("more clients than fit: the extra is left out and named", len(capped) == 1 and any("brexton" in n for n in notes), str(notes))

for bad, label in (([3], "a paragraph with only a CMI link"), ([5], "a paragraph with no link"), ([99], "a missing paragraph")):
    try:
        clients.build_clients(art, bad)
        check(f"grouping refuses {label}", False)
    except ValueError:
        check(f"grouping refuses {label}", True)

check("registrable host: subdomains are one client", clients.registrable_host("https://shop.acme.com/x") == clients.registrable_host("https://www.acme.com"))
check("registrable host: a co.uk keeps three labels", clients.registrable_host("https://www.acme.co.uk/a") == "acme.co.uk")

# ------------------------------------------------------------------ tokens resolve to their URLs
sponsor = Paragraph(0, p0.text, [ALD, BRX])
clients.attach(sponsor, many)
segs = pipeline.expand_segments("Readers want [[CLIENT_LINK:L1|research paper help]] and [[CLIENT_LINK:L2|thesis editing]] daily.", sponsor, "https://cmi.example")
check("expand: each client token becomes its own link", [(s, u) for s, u in segs if u] == [("research paper help", ALD.url), ("thesis editing", BRX.url)], str(segs))
check("expand: the text between the links is plain", segs[0] == ("Readers want ", None) and segs[-1] == (" daily.", None))
segs = pipeline.expand_segments("See [[CLIENT_LINK:L9|nobody]] and [[CMI_LINK:Zzz Market]].", sponsor, "https://cmi.example")
check("expand: an unknown client link is plain words, the report link still works",
      ("nobody", None) in segs and ("Zzz Market", "https://cmi.example") in segs, str(segs))
check("plain: the checks read the linked words", clients.plain("a [[CLIENT_LINK:L1|b c]] d") == "a b c d")

# ------------------------------------------------------------------ the brief
page = "Aldwych Writing Studio helps graduate students draft and edit research papers. We also format theses."
b = clients.normalize_brief({"name": "Aldwych Writing Studio", "offer": "Drafting and editing of research papers for graduate students.", "category": "academic writing",
                             "anchor_fit": "broader", "anchor_note": "'research paper help' suggests general tutoring.", "market_fit": "distant", "bridge": "Both serve readers who write reports."},
                            page, p0.text)
check("brief: a well-formed answer is kept", b["name"] == "Aldwych Writing Studio" and b["anchor_fit"] == "broader" and b["market_fit"] == "distant" and b["bridge"])
b = clients.normalize_brief({"name": "Invented Corp", "offer": "x " * 80, "anchor_fit": "weird", "anchor_note": "n", "market_fit": "close", "bridge": "b"}, page, p0.text)
check("brief: a name written in neither the page nor the paragraph is dropped", b["name"] == "")
check("brief: a long offer is cut to one short statement", len(b["offer"].split()) <= 45)
check("brief: an unknown label becomes 'unknown'; a note without a mismatch is dropped", b["anchor_fit"] == "unknown" and b["anchor_note"] == "" and b["bridge"] == "")
check("brief: a non-dict answer survives", clients.normalize_brief("nope", page, p0.text)["offer"] == "")

orig_read, orig_chat = external_reports.read_page, llm.chat_json
external_reports.read_page = lambda url, logger: ""
c1, _ = clients.build_clients(art, [0])
clients.run_briefs(c1, "Widget Market", LOG)
check("brief: a page that cannot be read leaves no brief and a note for the editor", c1[0].brief is None and c1[0].notes and "could not be read" in c1[0].notes[0])
external_reports.read_page = lambda url, logger: page
seen_prompts = []
calls = []
BRIEF = {"name": "Aldwych Writing Studio", "offer": "Drafting and editing of research papers for graduate students.",
         "category": "academic writing", "anchor_fit": "matches", "market_fit": "distant", "bridge": "Both serve people who write reports."}


def batched(system, user, **kw):
    calls.append(kw.get("call_name"))
    seen_prompts.append(user)
    return {"briefs": [{"client": "C1", **BRIEF}, {"client": "C2", **BRIEF, "name": "Invented Corp", "market_fit": "close", "bridge": ""}]}


llm.chat_json = batched
c2, _ = clients.build_clients(art, [0, 2])
clients.run_briefs(c2, "Widget Market", LOG)
check("brief: ONE call briefs every client, each given its own brief", all(c.brief and c.brief["offer"] for c in c2) and len(seen_prompts) == 1 and calls == ["client_briefs"],
      f"{len(seen_prompts)} {calls}")
check("brief: each client's entry is validated on its own (a name the page never wrote is dropped for that client only)",
      c2[0].brief["name"] == "Aldwych Writing Studio" and c2[1].brief["name"] == "" and c2[1].brief["market_fit"] == "close")
check("brief: the prompt carries the market, every client's paragraph with its marked links and page text, and asks for one entry per client",
      "Widget Market" in seen_prompts[0] and "<<L1: research paper help>>" in seen_prompts[0] and "<<L2: thesis editing>>" in seen_prompts[0]
      and "CLIENT C1" in seen_prompts[0] and "CLIENT C2" in seen_prompts[0] and "graduate students" in seen_prompts[0]
      and '"briefs"' in seen_prompts[0] and '"client"' in seen_prompts[0] and "TESTS BEFORE ANSWERING" in seen_prompts[0], seen_prompts[0][:300])
check("brief: a distant offer is reported to the editor with its bridge", any("far from this blog's market" in n for n in clients.brief_notes(c2[0])), str(clients.brief_notes(c2[0])))

llm.chat_json = lambda system, user, **kw: {"briefs": [{"client": "C2", **BRIEF}, {"client": "C2", **BRIEF}, {"client": "C9", **BRIEF}, "junk"]}
c3, _ = clients.build_clients(art, [0, 2])
clients.run_briefs(c3, "Widget Market", LOG)
check("brief: a client the answer leaves out gets no brief and a note; an unknown or repeated client entry is ignored",
      c3[0].brief is None and any("could not be worked out" in n for n in c3[0].notes) and c3[1].brief and not c3[1].notes, str([c.notes for c in c3]))
llm.chat_json = lambda system, user, **kw: dict(BRIEF)
c4, _ = clients.build_clients(art, [0])
clients.run_briefs(c4, "Widget Market", LOG)
check("brief: an old-shape answer (no 'briefs' list) leaves the client without a brief, with a note", c4[0].brief is None and c4[0].notes)
llm.chat_json = lambda system, user, **kw: {"briefs": [dict(BRIEF)]}
c5, _ = clients.build_clients(art, [0])
clients.run_briefs(c5, "Widget Market", LOG)
check("brief: with one client an entry that names no client is that client's", c5[0].brief and c5[0].brief["offer"])


def failing(system, user, **kw):
    raise llm.LLMError("down")


llm.chat_json = failing
c6, _ = clients.build_clients(art, [0, 2])
clients.run_briefs(c6, "Widget Market", LOG)
check("brief: a failed call leaves every client without a brief and each with a note, and never raises",
      all(c.brief is None and any("could not be worked out" in n for n in c.notes) for c in c6))
no_call = []
llm.chat_json = lambda system, user, **kw: no_call.append(1) or {}
external_reports.read_page = lambda url, logger: ""
c7, _ = clients.build_clients(art, [0, 2])
clients.run_briefs(c7, "Widget Market", LOG)
check("brief: when no client's page can be read no call is made at all", not no_call and all(c.brief is None and c.notes for c in c7))
llm.chat_json, external_reports.read_page = orig_chat, orig_read

# ------------------------------------------------------------------ the write prompt
c_one, _ = clients.build_clients(art, [0])
c_one[0].brief = {"name": "Aldwych Writing Studio", "offer": "Drafting and editing of research papers for graduate students.", "category": "academic writing",
                  "anchor_fit": "broader", "anchor_note": "'research paper help' suggests general tutoring, the page sells paper drafting.", "market_fit": "distant",
                  "bridge": "Both serve readers who write reports."}
inp_one = dataclasses.replace(t.INP, clients=c_one)
inp_two = dataclasses.replace(t.INP, clients=clients.build_clients(art, [0, 2])[0])
sponsor_para = Paragraph(0, p0.text, [ALD])
report = reports.Report(news_id=1, name=t.MARKET, subject="s", url="https://x", source="live", sections={"live_report_page": "p"})
text = writer.build_write_prompt(t.PLAN, inp_one, sponsor_para, report, "2026-11-01")
check("prompt: the client links are woven in, not a client paragraph restated",
      "THE CLIENT LINKS" in text and "THE CLIENT PARAGRAPH" not in text and "placed by code" not in text)
check("prompt: the client is never promoted or named", "The client is never promoted" in text and "Never write the client's name or domain" in text)
check("prompt: the client's name is given only to keep it out of the post", "never written in the post: Aldwych Writing Studio" in text)
check("prompt: the link token format and the link id are given", "[[CLIENT_LINK:<link id>|<the linked words>]]" in text and 'L1 "research paper help"' in text)
check("prompt: what the page offers is for accuracy only, with the broader-anchor note and the distance",
      "for accuracy only" in text and "broader than what the page offers" in text and "Both serve readers who write reports" in text)
check("prompt: the guest paragraph is shown as the source of the idea, with its link marked", "<<L1: research paper help>>" in text and "its idea is carried" in text)
check("prompt: the opening's budget names the paragraph that carries the links", "the paragraph that carries the client links" in text)
check("prompt: the paragraph never refers to the guest article", "never refer to the guest article" in text)
check("prompt: a broader anchor stays a setting, never what the client does", "only in a way the page supports" in text)
text2 = writer.build_write_prompt(t.PLAN, inp_two, sponsor_para, report, "2026-11-01")
check("prompt: several clients' links sit together in ONE paragraph", "ONE paragraph" in text2 and "CLIENT C2 (never written in the post" in text2)
config.CLIENT_REWRITE = False
text3 = writer.build_write_prompt(t.PLAN, inp_one, sponsor_para, report, "2026-11-01")
config.CLIENT_REWRITE = True
check("prompt: with the switch off the word-for-word placement is back", "placed by code" in text3 and "THE CLIENT LINKS" not in text3)

# ------------------------------------------------------------------ the draft's tokens
secs = [{"heading": "", "sop": [], "paragraphs": [
    "Lead-in with the market.",
    "Students look for [[CLIENT_LINK:L1|paper help online]] and for [[CLIENT_LINK:L1|research paper help]] and [[CLIENT_LINK:L7|ghost]].",
]}]
fixed = clients.normalize_tokens([dict(s, paragraphs=list(s["paragraphs"])) for s in secs], c_one, LOG)[0]["paragraphs"][1]
check("tokens: the linked words are set back to the registered ones", "[[CLIENT_LINK:L1|research paper help]]" in fixed and "paper help online" not in fixed, fixed)
check("tokens: a second token for the same link and an unknown link become plain words",
      fixed.count("[[CLIENT_LINK:L1|") == 1 and "[[CLIENT_LINK:L7" not in fixed and "ghost" in fixed, fixed)
plain_only = [{"heading": "", "sop": [], "paragraphs": ["Lead-in.", "Many students search for research paper help every term."]}]
fixed = clients.normalize_tokens(plain_only, c_one, LOG)[0]["paragraphs"][1]
check("tokens: the link's words standing plain in the opening are turned into the link", "[[CLIENT_LINK:L1|research paper help]]" in fixed, fixed)
inside_cmi = [{"heading": "", "sop": [], "paragraphs": ["Lead-in about the [[CMI_LINK:Research Paper Help Market]] today.", "Nothing else here."]}]
fixed = clients.normalize_tokens(inside_cmi, c_one, LOG)[0]["paragraphs"]
check("tokens: the link's words inside the report link are never linked again",
      "[[CLIENT_LINK" not in " ".join(fixed) and "[[CMI_LINK:Research Paper Help Market]]" in fixed[0], str(fixed))
for bad in ("[[CLIENT_LINK:research paper help]]", "[[CLIENT_LINK:L1]]", "[[CLIENT_LINK|Research Paper Help]]", "[[CLIENT_LINK:paper writing]]"):
    malformed = [{"heading": "", "sop": [], "paragraphs": ["Lead-in.", f"Students turn to {bad} every term."]}]
    fixed = clients.normalize_tokens(malformed, c_one, LOG)[0]["paragraphs"][1]
    check(f"tokens: the malformed token {bad} becomes the link (found live: no link id, printed raw)",
          "[[CLIENT_LINK:L1|research paper help]]" in fixed and fixed.count("[[") == 1, fixed)
absent = [{"heading": "", "sop": [], "paragraphs": ["Lead-in.", "Nothing about it here."]}]
fixed = clients.normalize_tokens(absent, c_one, LOG)
check("tokens: a link the writer left out stays missing, for the check to report", clients.link_problems(fixed, c_one))
# A report link the writer merged a client link's words into ("[[CMI_LINK:Language Services Market|converting text
# from one language to another]]", found live: the client's link then went missing and its check failed). The report
# link is cut back to the report's name and the client's token takes the words' place, right after it.
merged = [{"heading": "", "sop": [], "paragraphs": [
    "Lead-in about the market.",
    "It is the work of [[CMI_LINK:Research Paper Help Market|research paper help]], carried out for students."]}]
fixed = clients.normalize_tokens(merged, c_one, LOG)[0]["paragraphs"][1]
check("tokens: a report link's bar suffix that is a client link's words becomes the client's token after the report link",
      "the work of [[CMI_LINK:Research Paper Help Market]] [[CLIENT_LINK:L1|research paper help]], carried out" in fixed,
      fixed)
check("tokens: the repaired links pass the backlink check", not clients.link_problems(
    [{"heading": "", "sop": [], "paragraphs": [fixed]}], c_one),
    str(clients.link_problems([{"heading": "", "sop": [], "paragraphs": [fixed]}], c_one)))
self_named = [{"heading": "", "sop": [], "paragraphs": [
    "The [[CMI_LINK:Research Paper Help Market|Research Paper Help Market]] grows."]}]
fixed = clients.normalize_tokens(self_named, c_one, LOG)[0]["paragraphs"][0]
check("tokens: a report link whose suffix repeats the report's own name is cut to the plain report link",
      fixed == "The [[CMI_LINK:Research Paper Help Market]] grows." and "[[CLIENT_LINK" not in fixed, fixed)
both = [{"heading": "", "sop": [], "paragraphs": [
    "Students look for [[CLIENT_LINK:L1|research paper help]] today, and the work of "
    "[[CMI_LINK:Research Paper Help Market|Research paper help]] is steady."]}]
fixed = clients.normalize_tokens(both, c_one, LOG)[0]["paragraphs"][0]
check("tokens: a suffix naming an already-tokenized client (case-insensitively) is only cut",
      fixed.count("[[CLIENT_LINK:L1|") == 1 and "[[CMI_LINK:Research Paper Help Market]]" in fixed, fixed)

# ------------------------------------------------------------------ every link renders to its own page
import copy  # noqa: E402

DOMAIN_NAME = "Every link goes to its own page"
RPT = "https://www.coherentmarketinsights.com/market-insight/zzz-1"
dom_secs = [{"heading": "", "sop": [], "paragraphs": ["The [[CMI_LINK:Zzz Market]] and [[CLIENT_LINK:L1|research paper help]] grow."]}]
check("link domains: the report link to the report and the client link to its client is clean",
      checks.link_domain_problems(dom_secs, RPT, True, c_one) == [], str(checks.link_domain_problems(dom_secs, RPT, True, c_one)))
check("link domains: a CMI link with no report URL given is not judged", checks.link_domain_problems(dom_secs, None, True, c_one) == [])
off = checks.link_domain_problems(dom_secs, RPT, False, c_one)
check("link domains: with link_report off a CMI link is a problem", len(off) == 1 and "no report is linked" in off[0], str(off))
bad_cmi = copy.deepcopy(c_one)
bad_cmi[0].links[0]["url"] = "https://www.coherentmarketinsights.com/market-insight/other-2"
res = checks.link_domain_problems(dom_secs, RPT, True, bad_cmi)
check("link domains: a client link that resolves to the CMI site is a problem", len(res) == 1 and "aldwych.example" in res[0], str(res))
bad_host = copy.deepcopy(c_one)
bad_host[0].links[0]["url"] = "https://brexton.example/x"
res = checks.link_domain_problems(dom_secs, RPT, True, bad_host)
check("link domains: a client link that resolves to another client's host is a problem", len(res) == 1, str(res))
sub_ok = copy.deepcopy(c_one)
sub_ok[0].links[0]["url"] = "https://shop.aldwych.example/x"
check("link domains: a subdomain of the client's own domain is fine", checks.link_domain_problems(dom_secs, RPT, True, sub_ok) == [])
check("link domains: an unknown link id is left to the backlink check", checks.link_domain_problems(
    [{"heading": "", "sop": [], "paragraphs": ["See [[CLIENT_LINK:L9|nobody]]."]}], RPT, True, c_one) == [])
with_rpt = checks.run_checks_v2(t.TITLE, dom_secs, t.PLAN, inp_one, report_url=RPT)
check("link domains: the v2 checks carry the new check", any(r.name.startswith(DOMAIN_NAME) for r in with_rpt))
bad_inp = dataclasses.replace(inp_one, clients=bad_cmi)
bad_run = checks.run_checks_v2(t.TITLE, dom_secs, t.PLAN, bad_inp, report_url=RPT)
check("link domains: the v2 check fails for a client link that resolves to CMI",
      any(r.name.startswith(DOMAIN_NAME) and not r.passed for r in bad_run))

# ------------------------------------------------------------------ the checks in client mode
RESTATED = ("Readers who must turn research into finished reports can find [[CLIENT_LINK:L1|research paper help]] in many forms today, "
            "from structure guidance to formatting and revision. ")


def client_draft(restated: str = RESTATED, extra_market: str = "") -> list[dict]:
    d = t.good_draft()
    d[0]["paragraphs"][1] = t.pad(110, [restated.strip()]) if restated.strip() else t.filler(110)
    if extra_market:
        d[2]["paragraphs"][0] += " " + extra_market
    return d


def run_client(draft, inp=inp_one):
    return t.run_v2(t.TITLE, draft, inp=inp, sponsor_text="", sponsor_brand_terms=[])


good = run_client(client_draft())
for r in good:
    if r.name in ("No point is made twice across sections", checks.V2_US_EVIDENCE_CHECK_NAME):
        continue        # the fixture's plan carries one U.S. fact, so its thin-evidence warning is expected (tested in test_checks_v2)
    check(f"client mode, good draft: {r.name}", r.passed, r.detail)
names = [r.name for r in good]
check("client mode: the word-for-word placeholder checks are not run", not any(n.startswith("Retained") for n in names), str(names[:6]))
check("client mode: the link and 'client not named' checks are run",
      any(n.startswith("Client backlinks") for n in names) and any(n.startswith("Client not named") for n in names))

r = t.by_name(run_client(client_draft(RESTATED.replace("[[CLIENT_LINK:L1|research paper help]]", "research paper help"))), "Client backlinks")
check("client mode: a missing link fails the link check", not r.passed and "missing" in r.detail, r.detail)
r = t.by_name(run_client(client_draft(RESTATED.replace("|research paper help]]", "|paper help]]"))), "Client backlinks")
check("client mode: changed linked words fail the link check", not r.passed and "changed" in r.detail, r.detail)
moved = client_draft(extra_market="[[CLIENT_LINK:L1|research paper help]] matters."); moved[0]["paragraphs"][1] = t.filler(110)
r = t.by_name(run_client(moved), "Client backlinks")
check("client mode: a link outside the opening fails the link check", not r.passed and "not in the opening" in r.detail, r.detail)
two = t.by_name(run_client(client_draft(RESTATED), inp_two), "Client backlinks")
check("client mode, two clients: a link missing for the second client is named", not two.passed and "brexton" in two.detail, two.detail)

named_in_opening = t.by_name(run_client(client_draft(RESTATED + "Aldwych Writing Studio supports this work.")), "Client not named")
check("client mode: naming the client in the opening fails the 'client not named' check",
      not named_in_opening.passed and "opening" in named_in_opening.detail, named_in_opening.detail)
named_in_market = t.by_name(run_client(client_draft(extra_market="Aldwych also appears here by name.")), "Client not named")
check("client mode: naming the client in a market section fails the check too", not named_in_market.passed, named_in_market.detail)
promo = t.by_name(good, "Client not named")
check("client mode: a draft with the link and no name passes the check", promo.passed, promo.detail)
# the offer-description check: no name is written, yet the sentences describe what the client sells
OFFER_ECHO = ("Graduate students who must turn research into finished reports can find [[CLIENT_LINK:L1|research paper help]] in many forms "
              "today, from drafting support to editing of papers. ")
echo = t.by_name(run_client(client_draft(OFFER_ECHO)), "Client's offer not described")
check("client mode: sentences that repeat the client's offer wording fail the 'offer not described' check",
      not echo.passed and "aldwych.example" in echo.detail, echo.detail)
clean_offer = t.by_name(good, "Client's offer not described")
check("client mode: a restatement that uses none of the offer's own words passes it", clean_offer.passed, clean_offer.detail)
text_cat = clients.client_block(c_one)
check("prompt: the writer sees the client's kind of business, never the page's offer sentence",
      "academic writing" in text_cat and "Drafting and editing of research papers" not in text_cat, text_cat[-500:])
check("client mode: only the link's words may be the client's name",
      clients.naming_problems([{"heading": "", "sop": [], "paragraphs": ["See [[CLIENT_LINK:L1|Aldwych Writing Studio]] for more."]}],
                              [dataclasses.replace(c_one[0], links=[{"id": "L1", "anchor": "Aldwych Writing Studio", "url": ALD.url}])]) == [])

# a client whose name is an ordinary word ("teamwork") is named only when the name is written as a name
TW = dataclasses.replace(c_one[0], host="teamwork.com", brief={"name": "Teamwork.com", "offer": "x", "category": "project software"},
                         links=[{"id": "L1", "anchor": "project management software", "url": "https://www.teamwork.com/"}])
word_use = [{"heading": "", "sop": [], "paragraphs": ["Good teamwork needs a shared view. Teamwork improves delivery when plans are clear."]},
            {"heading": "", "sop": ["trends"], "paragraphs": ["Strong teamwork speeds up reviews."]}]
check("naming: the ordinary word 'teamwork' is not the client's name", clients.naming_problems(word_use, [TW]) == [], str(clients.naming_problems(word_use, [TW])))
as_name = [{"heading": "", "sop": [], "paragraphs": ["Many agencies rely on Teamwork for billing."]}]
check("naming: the name written as a name is caught", len(clients.naming_problems(as_name, [TW])) == 1)
by_domain = [{"heading": "", "sop": [], "paragraphs": ["See teamwork.com for more."]}]
check("naming: the domain is caught", len(clients.naming_problems(by_domain, [TW])) == 1)
# the opening is about the guest article's subject: words it shares with the client's page are the post's own
SUB = dataclasses.replace(c_one[0], brief={"name": "Aldwych", "offer": "Drafting and editing of research papers for graduate students.", "category": "academic writing"})
subj_draft = [{"heading": "", "sop": [], "paragraphs": ["Graduate students drafting [[CLIENT_LINK:L1|research paper help]] want clear editing."]}]
check("offer check: words the guest article's own subject uses are not a leak",
      clients.feature_problems(subj_draft, [SUB], "", "Drafting and editing for graduate students") == [])
check("offer check: without that subject the same words are flagged", len(clients.feature_problems(subj_draft, [SUB], "", "")) == 1)

# two clients in one opening paragraph: the other client's linked words are not this client's offer
OTHER = dataclasses.replace(c_one[0], host="other.example",
                            links=[{"id": "L2", "anchor": "full-service digital agency", "url": "https://other.example/"}],
                            paragraph=dataclasses.replace(c_one[0].paragraph, text="Hire a full-service digital agency for specialist work."),
                            brief={"name": "Other", "offer": "Full-service digital agency work.", "category": "digital agency"})
ME = dataclasses.replace(c_one[0], host="me.example", links=[{"id": "L1", "anchor": "answer engine optimisation", "url": "https://me.example/"}],
                         brief={"name": "Me", "offer": "A digital agency running answer engine campaigns for brands.", "category": "agency"})
two_draft = [{"heading": "", "sop": [], "paragraphs": ["Buyers often hire a [[CLIENT_LINK:L2|full-service digital agency]] or pick [[CLIENT_LINK:L1|answer engine optimisation]] help for brands."]}]
check("offer check: words that only the other client's link brings are not this client's offer",
      clients.feature_problems(two_draft, [ME, OTHER], "", "") == [], str(clients.feature_problems(two_draft, [ME, OTHER], "", "")))
check("offer check: the same words are still flagged when no other client explains them",
      len(clients.feature_problems(two_draft, [ME], "", "")) == 1, str(clients.feature_problems(two_draft, [ME], "", "")))

# figures from the client's own website brief are not a market source: only the guest paragraph's own figures trace
with_figs = RESTATED.replace("revision.", "revision in more than 250 languages.")
no_brief = dataclasses.replace(inp_one, clients=clients.build_clients(art, [0])[0])
trace_off = t.by_name(run_client(client_draft(with_figs), no_brief), "Every number traces")
check("client mode: a figure the client's page never gave fails the number trace", not trace_off.passed and "250" in trace_off.detail, trace_off.detail)
c_site, _ = clients.build_clients(art, [0])
c_site[0].brief = {"name": "Aldwych Writing Studio", "offer": "Drafting for graduate students in 250+ languages.", "category": "", "anchor_fit": "matches", "anchor_note": "", "market_fit": "close", "bridge": ""}
trace_offer = t.by_name(run_client(client_draft(with_figs), dataclasses.replace(inp_one, clients=c_site)), "Every number traces")
check("client mode: a figure that appears only in the client brief offer fails the number trace too",
      not trace_offer.passed and "250" in trace_offer.detail, trace_offer.detail)
with_para = RESTATED.replace("revision.", "revision, as in a studio that prepares 120 papers a term.")
trace_para = t.by_name(run_client(client_draft(with_para), no_brief), "Every number traces")
check("client mode: a figure from the client paragraph itself traces", trace_para.passed, trace_para.detail)

# ------------------------------------------------------------------ the whole write step with a stub model
plan = t.PLAN
draft = client_draft(RESTATED.replace("[[CLIENT_LINK:L1|research paper help]]", "[[CLIENT_LINK:L1|paper help online]]"))
written = {
    "intro": {"paragraphs": draft[0]["paragraphs"]},
    "transition": {"paragraphs": draft[1]["paragraphs"]},
    "sections": [{"sop": s["sop"][0], "heading": s["heading"], "paragraphs": s["paragraphs"]} for s in draft[2:]],
}
orig_chat = llm.chat_json
llm.chat_json = lambda system, user, **kw: written
_, title, out_sections, _ = writer.run_write_v2(plan, inp_one, sponsor_para, report, LOG)
llm.chat_json = orig_chat
intro = " ".join(out_sections[0]["paragraphs"])
check("write step: the linked words come back as the registered words", "[[CLIENT_LINK:L1|research paper help]]" in intro and "paper help online" not in intro, intro[:300])
check("write step: no link problem is left", clients.link_problems(out_sections, c_one) == [], str(clients.link_problems(out_sections, c_one)))
check("write step: the sentence carrying the link survived the clean-up passes", "finished reports" in intro)

# ------------------------------------------------------------------ a model's stray closing bracket does not lose a whole answer
check("json: an answer closed with one bracket too many is read", llm._parse_json('{"a": [1, {"b": 2}]}}') == {"a": [1, {"b": 2}]})
check("json: a fenced answer is still read", llm._parse_json('```json\n{"a": 1}\n```') == {"a": 1})
for bad in ('{"a": 1} and then some text', '{"a": 1}{"b": 2}', '{"a": 1'):
    try:
        llm._parse_json(bad)
        check(f"json: {bad!r} is still refused", False)
    except Exception:
        check(f"json: {bad!r} is still refused", True)

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all client tests passed")
