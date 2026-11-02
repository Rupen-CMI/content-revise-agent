"""The instruction library for the generation prompts (PLAN, WRITE, FAQ answers).

Every instruction lives here ONCE, in the group it belongs to; a prompt picks the groups (and, for the FAQ, the individual rules) it
needs and prints them under a labelled heading. Before this module the same instruction was stated in several places inside one prompt
(the write prompt told the model about keywords 55 times, repeated one 70-word keyword paragraph under every section, and spread
"do not repeat" over 24 places), and the rules sat after all the data. Scattered, partly overlapping instructions are what the model
follows unevenly; one statement per rule, grouped by topic, in a fixed order, is the fix.

The groups, in the order a prompt prints them:
  PRECEDENCE         what wins when two instructions pull against each other
  FACTS AND SOURCES  what may be stated, how a source is credited, how a figure is written
  NO REPEATS         one piece of evidence appears once in the whole article
  ANALYSIS           what a fact is made to mean, and the one opinion
  LANGUAGE           register, sentences, grammar, continuity
  KEYWORDS           the primary keyword, secondary keywords, long-tail phrases
  LENGTH AND FLOW    word budgets, paragraph size, how sections follow one another

A rule is a short title and a text; a prompt that needs a rule in a different strength (the FAQ) takes the id of the rule, not a copy
of its words. Rule texts may carry {placeholders} that the prompt's own .format fills (only {total_words} here). Pure; no model call."""

# ------------------------------------------------------------------------------------------------ the rules
# id -> (group, title, text)
_RULES: dict[str, tuple[str, str, str]] = {
    # ---- PRECEDENCE
    "precedence": ("PRECEDENCE", "Order of precedence", (
        "When two instructions pull against each other, the earlier one in this list wins: (1) facts and sources: nothing is stated that "
        "no listed fact supports; (2) links and the shape given for each block; (3) no repeats; (4) language: every sentence complete, "
        "clear and in the register below; (5) keywords; (6) length. A keyword is never placed by breaking a sentence, and a budget is "
        "never kept by dropping a required figure.")),

    # ---- FACTS AND SOURCES
    "facts_only": ("FACTS AND SOURCES", "Only listed facts", (
        "State only the facts listed for a section. Never write a figure, company detail or claim that no listed fact supports; where a "
        "section has few facts, it stays short. The \"Purpose\" line of a section tells you what the reader should learn; it is not a "
        "source of facts. Never talk about the writing process: no mention of \"the facts\", \"the plan\", \"the report not saying\", "
        "\"what is not available\" or what you could not find. Leave out what you cannot support, quietly.")),
    "credit": ("FACTS AND SOURCES", "Crediting a source", (
        "A fact marked (CMI report) may be stated plainly. CMI's own research is written as \"CMI\" or \"CMI's report\", and an outside "
        "source by its own name: \"the report\" never stands for an outside source. A fact marked \"no source is named\" is stated plainly as "
        "the market's own fact: no report, firm or research is named for it, and it is never credited to CMI. A fact whose source is not CMI (news evidence, or a "
        "figure marked NOT CMI) is never credited to CMI. The paragraph is the unit of naming (a bullet counts as its own paragraph): the "
        "first sentence of a paragraph that uses one of that source's figures names the source, together with the month when the fact has "
        "one; a later sentence in the same paragraph that uses a further figure of that same source does not repeat the name; a new "
        "paragraph that uses a figure of an outside source names the source again. \"The same source\", \"That source\" and \"It\" "
        "pointing back at a source are never written. A source's name goes in a short clause beside the fact, never in a frame that makes "
        "a report or a website the subject of a sentence about the market, and there are no chains of \"according to\". A source's name is "
        "written only beside a fact that lists that source: a name is never carried over from another fact, and a fact that names no "
        "source is never given one.")),
    "figures": ("FACTS AND SOURCES", "Writing a figure", (
        "A figure is stated with its unit, its base year and what it measures; a sentence that gives a number says what the number "
        "measures. At most one fact goes in a sentence, and each fact goes in the sentence that makes the section's point. A fact marked "
        "historical carries its year and is never presented as current. Call something recent, new or current only if its source places "
        "it within the 12 months before today's date. A fact's \"detail\" line is optional background: use at most one specific from it, "
        "and only when the section's budget allows.")),
    "own_words": ("FACTS AND SOURCES", "Own words", (
        "Put every fact in your own words and sentence shapes; never reuse a run of eight or more words from a fact or the report as "
        "written. This includes lists of segment categories, which you break up by writing the connective words your own way and keeping "
        "a few of your own words between the segment type and its categories. A fact that arrives as a full sentenced statement (a "
        "driver, an opportunity, a U.S. statement, a company note) is research wording, not a line of prose: say its meaning again in "
        "your own words, keeping only its figures; its sentence shape must not survive into the draft. When a fact lists many companies, "
        "name only the few that matter in that sentence (at most six), grouped or ordered your own way, never the whole list in the order "
        "given.")),

    # ---- NO REPEATS
    "no_repeats": ("NO REPEATS", "One piece of evidence, one place", (
        "Every listed fact, whether a report figure or a single piece of news evidence, is stated in exactly one place in the whole post: "
        "the section that lists it. Once a section has stated it, no other section states it again, not as a passing clause, not in other "
        "words, not as a reminder (the sub-segment analysis, the U.S. section, the key-players section and the conclusion especially "
        "build on what earlier sections said without restating it). A source may supply several separate facts; each of them is stated "
        "once. A section that builds on an earlier fact points back in a few words (\"the leading category above\") and adds new "
        "reasoning; it never repeats the fact's figure, name or finding. A figure appears once in the whole post, the conclusion "
        "included. A category that the segmentation section lists is not described again in the same terms in the sub-segment analysis: "
        "that analysis adds what the list does not say.")),
    "phrase_once": ("NO REPEATS", "Signature phrases", (
        "The phrase \"the commercial implication is\" is used at most once in the whole post; other implications are framed in the "
        "sentence's own words.")),

    # ---- ANALYSIS
    "analysis": ("ANALYSIS", "Analysis after a fact", (
        "After a fact, say what it means for this market: the demand it creates, who benefits, how buyer behaviour or the value chain "
        "shifts and what providers or buyers can do about it. Interpretation is reasoning from the listed facts alone and is phrased as "
        "inference (\"this points to\", \"providers can\"), so a reader can tell the evidence from the reading of it. It adds no figure, "
        "name, date, study, ranking or comparison with other markets or industries that is not in a listed fact, and it never describes "
        "what a source covers, does not cover or does not quantify: when a listed fact states a limit (a pilot, a sample, an exploratory "
        "study), the sentence carries that limit; a limit is never invented. This is how a section with few facts still reads as a full, "
        "substantive section.")),
    "positive": ("ANALYSIS", "Favourable wording only", (
        "Interpretation points in the market's favour: it states demand, opportunity and advantage, and never describes risk, loss, "
        "burden, exclusion, restraint or lagging.")),
    "specificity": ("ANALYSIS", "Specificity", (
        "Every paragraph is about this market and not about the wider industry. Name the segment, buyer type or service line it "
        "concerns, in the facts' own terms; a sentence that would read the same in an article on the wider industry is rewritten to say "
        "what it means for this market's providers or buyers.")),
    "opinion": ("ANALYSIS", "CMI analyst opinion", (
        "One or two sentences in the whole post, no more. The conclusion's last sentence is always one. Attribute it by role (\"a CMI "
        "analyst\", \"CMI's research team\"), never by name or in quotation marks. It is a judgement a reader could disagree with, built "
        "only from facts already in the post, and it adds no new fact. Its comparison is complete: both sides of a contrast inside it are "
        "actions or properties of the same kind, or the contrast is left out.")),

    # ---- LANGUAGE
    "voice": ("LANGUAGE", "Voice and register", (
        "Formal market-research and consulting language, in the third person, professional and consistent from the first sentence to the "
        "last. Precise analytical verbs (indicates, reflects, supports, extends, raises, reduces, creates demand for) replace casual "
        "ones, and market actors (providers, vendors, buyers, regulators, institutions) are the subjects of sentences. No contractions, "
        "no slang, no hype, no calls to action and no direct address (\"you\"). Use the sector's own terminology correctly and write an "
        "abbreviation out in full at its first use. Where a fact gives no figure, the analysis stays qualitative but exact: it names the "
        "segment, the buyer or the mechanism instead of falling back on loose words such as big, huge, boom, a lot or very, or on vague "
        "generalities (\"a major shift\", \"significant growth\", \"plays a key role\"): name what shifts, by how much and for whom. "
        "A bullet is one or two complete sentences, never a fragment.")),
    "sentences": ("LANGUAGE", "Sentence length", (
        "Sentences average about 18 words and most run 12 to 24. At most one sentence in ten passes 30 words and none passes 38. A "
        "sentence carries one main idea and at most two figures: the supporting detail of a fact goes into the next sentence, never into "
        "the first. Two sentences that each read once are better than one that needs two readings. Do not write a run of fragments "
        "either: vary the length, and join two closely tied ideas with one connecting word.")),
    "grammar": ("LANGUAGE", "Grammar", (
        "Every sentence is complete, with a subject and a finite verb, and the verb agrees with its subject. Tense stays consistent "
        "within a paragraph: present for the market now, past with its date for reported events. The items of a list are parallel. A "
        "modifier is never left dangling or misplaced: the word an opening phrase describes must be the subject that follows. Articles, "
        "prepositions, commas and apostrophes are correct, and units and currency are written one way throughout. A comparison names "
        "both sides in full, and the two sides are of the same kind. Every sentence starts with a capital letter, even when it opens with a "
        "company or product whose own spelling is lower case.")),
    "continuity": ("LANGUAGE", "Continuity", (
        "The reader must never wonder what a sentence refers to. Each sentence builds on the previous one or two. A term, company, "
        "study, figure, abbreviation or pointing word (\"this\", \"that\", \"these\", \"such\", \"it\", \"its\") appears only where its "
        "referent was named in the previous two sentences or in the section heading; otherwise the thing is named again in full with a "
        "noun. A pointing word is followed by a noun that names the thing (\"this shift\", \"such platforms\") whenever the bare word "
        "could be mistaken for something else. A paragraph opens with its claim and closes on the implication. A paragraph never opens "
        "on a pointing word.")),
    "active": ("LANGUAGE", "Active voice", (
        "The actor is the subject and a concrete verb carries it. Use a passive verb only when the actor is unknown or unimportant "
        "(target at most one passive sentence in six), and name a firm, institution or group as the actor instead of an abstract noun "
        "acting.")),
    "filler": ("LANGUAGE", "No filler", (
        "Every clause carries information. No sentence exists only to announce, preview, praise or summarise; no trailing clause "
        "explains why a sentence was written; no sentence stacks synonyms; no vague intensifiers pad a point. A word that labels a pace "
        "(\"modest\", \"rapid\", \"steady\", \"strong\", \"double-digit-adjacent\") is never added to a rate: the figure says it. A list "
        "lead-in states the division plainly (who divides what, into how many parts) and nothing about its purpose.")),

    # ---- KEYWORDS
    "primary": ("KEYWORDS", "Primary keyword", (
        "The full market name, written exactly as given. Use it once in every market section, the conclusion included, and never twice "
        "in the same paragraph. The sentence that carries a section's secondary keyword may be that one use. Elsewhere in the section say "
        "\"the market\", \"this market\" or \"it\". It is plain text everywhere except the link in the opening. The market is an industry "
        "category made of many companies' products: never say or imply that it is a product, software or a company.")),
    "keyword_topic": ("KEYWORDS", "How an assigned keyword is used", (
        "A keyword is a topic, not a fixed string. Where a section lists a REQUIRED secondary keyword or a REQUIRED long-tail phrase, "
        "every one of its content words appears in ONE sentence of that section, in whatever order and grammatical form that sentence "
        "needs, with no synonym and no dropped word; the sentence that carries a secondary keyword also carries the words of the market's "
        "name (without the word Market) and the word market itself; "
        "rephrase it freely: reorder its words, inflect them, and spread them across the clause with your own ordinary words between "
        "them, so the sentence reads as ordinary prose about something real. A sentence is never built around the keyword's listed "
        "order. Acronyms and proper nouns are written in their normal capitalisation (AI, US) even though the phrase is listed in "
        "lower case. Covering a keyword by meaning is only for terms the plan does not require any section to carry.")),
    "keyword_slot": ("KEYWORDS", "Where an assigned keyword sits", (
        "The keyword or phrase goes only into an ordinary slot of its sentence: an object of a verb, an object of a preposition, or the "
        "subject whose single head noun is one of its own nouns with its other words as modifiers. It is never the subject of a "
        "sentence about itself, never a label, never a row of the sentence's own topic words, never placed after a colon or comma, and "
        "never pasted in its listed order as a pile of nouns. Where a section's shape is a lead-in sentence followed by bullet lines, "
        "its bullet lines are reserved for the listed facts alone, so the keyword's words go in the lead-in sentence.")),
    "keyword_scope": ("KEYWORDS", "Each keyword belongs to its own section", (
        "A secondary keyword's words sit in one paragraph of its section, never spread over two; when the same idea comes up in other "
        "sections it is said in different words, never in a listed keyword's words. Use no keyword outside the section it is listed "
        "for, so a paragraph never ends up playing host to two sections' keywords. Related terms are optional: use one where it fits a "
        "sentence you would write anyway.")),

    # ---- LENGTH AND FLOW
    "length": ("LENGTH AND FLOW", "Length", (
        "The whole post comes to at most {total_words} words and not less than nine tenths of that. Each section's budget is a ceiling: "
        "a section never runs over its stated words. Where a section lists more facts than its budget can carry in full, state the "
        "strongest in full, give the others a clause each or leave them out; the budget is never exceeded to fit every fact. A "
        "paragraph runs 40 to 100 words unless the section's shape gives its own paragraph length: a longer one is split where the idea "
        "changes. Write each section to its budget and do not hand in sections of half the budget.")),
    "flow": ("LENGTH AND FLOW", "Flow", (
        "Each section's first sentence picks up from the section before it. After the transition the post stays on the market. The "
        "guest article's context belongs to the opening alone: a market section uses a guest point only when that section lists one as a "
        "guest example, and then in one sentence.")),
    "no_roadmap": ("LENGTH AND FLOW", "The post never announces itself", (
        "No sentence says what the sections that follow cover or what the article will do (\"The sections that follow set out...\", "
        "\"This article examines...\", \"the sections ahead\"). A block ends when its own content ends and the next block opens with its "
        "own content. The transition leads into the market by saying what the market is and why it belongs with the opening, never by "
        "listing the sections.")),
}

GROUP_ORDER = ("PRECEDENCE", "FACTS AND SOURCES", "NO REPEATS", "ANALYSIS", "LANGUAGE", "KEYWORDS", "LENGTH AND FLOW")


def rule(rule_id: str) -> str:
    """The text of one rule."""
    return _RULES[rule_id][2]


def render(groups=GROUP_ORDER, only: tuple[str, ...] = (), skip: tuple[str, ...] = (), heading_prefix: str = "RULES") -> str:
    """The rules of the given groups as labelled sections, each rule numbered inside its group, in the library's order. `only` keeps just
    those rule ids (a prompt that needs a smaller set, such as the FAQ); `skip` leaves ids out."""
    out: list[str] = []
    for group in groups:
        rows = [(rid, title, text) for rid, (g, title, text) in _RULES.items()
                if g == group and (not only or rid in only) and rid not in skip]
        if not rows:
            continue
        out.append(f"{heading_prefix}: {group}")
        for n, (_, title, text) in enumerate(rows, 1):
            out.append(f"{n}. {title}. {text}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def ids(group: str) -> list[str]:
    return [rid for rid, (g, _, _) in _RULES.items() if g == group]
