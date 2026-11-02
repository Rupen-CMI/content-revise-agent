"""Offline test: one spelling variety across the body and the FAQ (no network, no LLM).

Run from backend/: python devtools/test_spelling.py

Real failure: the body said "organisations" and "localisation" while the FAQ and the quoted evidence said "organizations" and
"localization", sometimes inside one section. The guest article's own variety is applied to the finished body and FAQ answers.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import spelling  # noqa: E402

FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f" -- {detail}" if detail and not ok else ""))
    if not ok:
        FAILURES.append(name)


check("detect: a guest article with 'organisations' and 'localisation' is British",
      spelling.detect("Organisations rely on localisation and specialised work.") == "British")
check("detect: 'organizations' and 'localization' is American", spelling.detect("Organizations rely on localization.") == "American")
check("detect: no signal defaults to American", spelling.detect("The market grows each year.") == "American")

mixed = "Organisations buy localisation while organizations in the FAQ say localization. Centre of excellence, colour and behaviour."
check("convert to American: British forms change", spelling.convert(mixed, "American") ==
      "Organizations buy localization while organizations in the FAQ say localization. Center of excellence, color and behavior.",
      spelling.convert(mixed, "American"))
check("convert to British: American forms change", spelling.convert("organizations and localization, analyzed.", "British") ==
      "organisations and localisation, analysed.", spelling.convert("organizations and localization, analyzed.", "British"))
check("convert: a capitalised word in the middle of a sentence is a proper name and stays",
      spelling.convert("He visited the Center for Disease Control and the Defense Ministry.", "British") ==
      "He visited the Center for Disease Control and the Defense Ministry.")
check("convert: link tokens and kept words are untouched",
      spelling.convert("The [[CMI_LINK:Localization Services Market]] covers localization.", "British", spelling.keep_words("localization")) ==
      "The [[CMI_LINK:Localization Services Market]] covers localization.")
check("convert: 'emphasis', 'analysis' and 'rise' (nouns that look like -ise words) are never touched",
      spelling.convert("The emphasis of the analysis is the rise.", "American") == "The emphasis of the analysis is the rise.")
check("convert: 'program' and 'license' (also British) are not converted to British",
      spelling.convert("The program needs a license.", "British") == "The program needs a license.")

check("convert: the word that opens a '### ' heading line is converted too",
      spelling.convert("### Digitisation and online trading", "American") == "### Digitization and online trading",
      spelling.convert("### Digitisation and online trading", "American"))

secs = [{"sop": ["trends"], "heading": "Trends", "paragraphs": ["Organisations localise."]}]
faq = [{"question": "Which organisations lead?", "answer": "Organizations localize."}]
spelling.apply(secs, faq, "American")
check("apply: body and FAQ answers follow one variety; the FAQ question is left as the plan wrote it",
      secs[0]["paragraphs"] == ["Organizations localize."] and faq[0]["answer"] == "Organizations localize." and
      faq[0]["question"] == "Which organisations lead?")

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
    sys.exit(1)
print("all spelling tests passed")
