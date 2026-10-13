SPONSOR_TOKEN = "[[SPONSOR_PARAGRAPH]]"
CMI_LINK_PATTERN = r"\[\[CMI_LINK:(.+?)\]\]"

# What CMI actually DOES — its own offerings/activities, in its own words, pulled from
# coherentmarketinsights.com (/services, /aboutus). Deliberately NOT industry or market names
# (those live in the report catalog and change per run); this is the fixed vocabulary of the
# work CMI itself performs, used to keep a title/framing phrased as market intelligence CMI
# would actually produce, instead of drifting into the original guest post's own topic or voice.
CMI_KEYWORDS = (
    "market research", "market intelligence", "industry analysis", "market monitoring",
    "competitive intelligence", "consulting and advisory", "consumer insights",
    "procurement intelligence", "supplier and demand analysis", "technology research",
    "ESG recommendations", "cross-border expansion", "market forecasting",
    "market sizing", "market trends", "growth drivers", "competitive landscape",
    "key players analysis", "segmentation analysis", "regional market analysis",
    "demand forecasting", "supply chain insights", "syndicated research reports",
    "custom research", "primary research",
)

SOP = """Mandatory requirements for the revised blog post (follow a clear, logical information flow):

1. Market Trends: three key market trends relevant to the market. Explain each clearly, including its impact on the market.
2. Market Intelligence Report Context: relevant context from the Coherent Market Insights (CMI) report for the market, integrated naturally into the discussion, never as a standalone promotional section.
3. Market Segmentation: the key segments and sub-segments covered in the CMI report, presented so they are relevant to the overall discussion.
4. Sub-Segment Analysis: pick one sub-segment from each of two different major segments and explain each in detail: key factors driving its growth/adoption, major applications and end-use cases, and its relevance and contribution to the overall market.
5. Country Analysis: a U.S. analysis of 100-200 words covering key market factors, growth drivers, adoption trends, and relevant industry developments.
6. Market Participants / Key Players: relevant companies operating in the market; where appropriate, briefly connect their presence, products, technologies, or strategies to the market."""


ORIENT_SYSTEM = """You are a senior editor at Coherent Market Insights (CMI), a market research firm. \
Guest posts on CMI's blog are being rewritten into market-intelligence articles anchored on one CMI market report. \
Your job in this step is to identify which CMI market the article belongs to and plan the web research. Respond with JSON only."""

ORIENT_USER = """CMI's report categories: {categories}

ARTICLE TITLE: {title}

ARTICLE BODY:
{body}

PARAGRAPH THAT MUST STAY IN THE FINAL ARTICLE (contains a paid backlink):
{sponsor}

Return JSON with these keys:
- "topic_summary": one sentence describing what the article is about.
- "scope_keywords": 8-12 short phrases (1-4 words each) that define the MARKET or INDUSTRY this article's subject belongs \
to — not a summary of what the article discusses, and not restatements of its topic. Ask yourself: "a market research \
firm publishes reports on industries; which industry/market does this article's actual subject live inside?" Then give \
keywords naming THAT market and its scope — the way that market's own segmentation, vendors, and category terms would be \
named in an industry taxonomy (the kind of terms that appear in a report's table of contents: segment names, product- \
category names, vendor-category names), not generic adjacent concepts the article happens to mention in passing and \
not vague marketing/business-fluff phrases ("digital transformation", "customer experience", "innovation", "growth \
strategy", "best practices") that name no specific industry at all. If the article names its own market explicitly \
(e.g. states a market size, CAGR, or says "the X market"), that named market and its direct synonyms/near-synonyms MUST \
dominate this list — do not let a secondary or incidental detail outrank the article's own stated subject. Favor \
precision over breadth: a wrong-market keyword is worse than a missing one. \
Example (format only, not a real market): for an article about "automated invoice processing for accounts payable \
teams," correct keywords are "invoice automation", "accounts payable software", "AP automation" — NOT "accounting", \
"finance teams", "enterprise software", or "digital transformation" (too generic/adjacent or pure fluff), and NOT \
"automation" alone (names no market).
- "candidate_markets": 6-8 market names derived from scope_keywords, most fitting first, written as the bare \
market name a research firm would title a report: "<Noun Phrase> Market", e.g. the style of "Zzz Widget Market" or \
"Zzz Fastener Market" (placeholder format examples only, not real markets). Do NOT append segmentation qualifiers \
such as ", By Deployment Type (...)" or ", By End User" — the report catalog is searched by the bare name, and extra \
words make the exact report fail to match. Use established, broad industry market names that \
a research firm would publish a report on, not niche or invented ones. Include both the closest specific market and one \
or two broader parent markets. List ONLY the article's own market and its direct parents — never an adjacent or sibling \
market, even if the article mentions it or shares customers and data with it (e.g. for an article about marketing \
automation software, customer relationship management is a different market and does not belong on this list). The \
retained paragraph's niche must not pull guesses toward a different market — fitting that paragraph in is a later \
step's job, not this one. Do not reuse any market name that happens to appear elsewhere in this prompt.
- "bridge_angle": one sentence on how the article's topic naturally connects to the top candidate market."""


SELECT_SYSTEM = """You are a senior market-intelligence editor at Coherent Market Insights (CMI). \
The market/report for this article has already been confirmed by a human editor — your only job here is to select \
reliable supporting evidence from web search results. Respond with JSON only."""

SELECT_USER = """ARTICLE TITLE: {title}
ARTICLE TOPIC: {topic_summary}
INTENDED ANGLE: {bridge_angle}

PARAGRAPH THAT MUST STAY IN THE FINAL ARTICLE:
{sponsor}

CONFIRMED CMI REPORT (already chosen by a human editor — not your decision):
{report_name}

WEB SEARCH RESULTS:
{hits}

Task: from the web results, select up to 12 that pass ALL of these tests — a result that fails any one of them must \
be rejected, not merely scored lower:
1. Genuinely about THIS confirmed market specifically, not a result that merely shares a few words with it (a result \
about a different, related, or broader market is a wrong-market result, not a weak match — reject it, do not include \
it as "context"). A single word from the market's name taken on its own does not make a result relevant: for a market named \
"Zzz Widget Platform" (placeholder, not a real market), a page about what a "platform" is, or what a "widget" is, is not \
about that market.
2. Credible: a named publication reporting news, or a named company/analyst making a specific, attributed statement — \
not an anonymous listicle, content-farm roundup, or SEO filler page. Reject any page whose title or framing is itself \
a market-report advertisement — "<Market> Size, Share, Growth, Trends, Forecast [to/by] <year>", "<Market> Analysis \
[2020s year range]", or similar — regardless of which site hosts it: that pattern means the page IS a rival research \
firm's own report listing, a company directory, or an index of other articles, not a reported event, even when the \
host itself is not on a known-blocked list.
3. Concrete, not generic: the result must report something that HAPPENED or a specific figure (a company's product \
launch, an acquisition, a funding round, a named regulation taking effect, a specific adoption statistic) — reject \
vague trend-washing ("the market is growing", "companies are investing in AI") that could apply to almost any market and \
adds nothing a reader doesn't already assume. Reject anything that DEFINES or EXPLAINS a concept instead of reporting \
an event: dictionary or encyclopedia entries, "what is X" / "meaning of X" / "types of X" / glossary / tutorial / \
guide pages, and any page whose first sentence is a definition. Explaining the subject is the article's job, not a \
source's — a source earns a place only by telling the reader what happened.
4. Actually usable in the final draft: ask yourself "would this specific fact, cited by name, strengthen the Market \
Trends, U.S. analysis, or Key Players section of the article a reader is about to receive?" — if a fact is true but \
would not actually improve a single sentence of that article, do not select it just because it is on-topic.
Reject listicles, product promotions, how-to guides, and anything about a different market. It is fine to select fewer \
than 12, or none, if few results pass all four tests — do not pad with weak, generic, or off-topic results to reach a \
higher count; a shorter list of results that each genuinely earn their place beats a longer list padded with filler. \
For each selected result, write one factual sentence stating only what the result says; do not add, infer, or round \
any number it does not contain.

Each result above is tagged NEWS (with a real publish date) or BACKGROUND (an ordinary web page with no verified \
publish date, so nothing about it is known to be current). A result tagged "trends" is a claim about what is CURRENTLY \
happening in this market, so it must come from a NEWS result — a BACKGROUND result must never be assigned use_for \
"trends", no matter how relevant it reads. A BACKGROUND result is selected only if it states a concrete, citable fact \
about THIS market that passes all four tests above (never a definition or explanation), and then only as "context". \
When in doubt about a BACKGROUND result, leave it out.

Return JSON:
{{"evidence": [{{"id": "<W-number>", "fact": "<one factual sentence>", "use_for": "trends|us|players|context"}}]}}"""


WRITE_SYSTEM = """You are a senior analyst at Coherent Market Insights (CMI), a market research firm. You write the way a \
CMI market research report or analyst brief reads: a neutral third-person register, claims grounded in the cited report and \
evidence, section structure that mirrors how a research report is organized (trends, segmentation, regional analysis, \
competitive landscape). This is not blog content, marketing copy, or thought leadership — it has no hook, no calls to action, \
no rhetorical questions, no direct address to the reader ("you"), and no urgency or enthusiasm about the market. State what \
the data shows; do not sell the market or the report. Avoid generic engagement-writing filler such as "in today's fast-paced \
world", "delve", "navigate the landscape", "game-changer", "unlock", "revolutionize" — but the goal is the analyst register \
itself, not merely dodging specific words. \
Your prompt contains several distinct sources — the original guest article, the one confirmed CMI report, and web \
evidence for that same confirmed market — and they must never cross-contaminate: a name, market, figure, or claim that \
belongs to one source must never be written as if it came from another, even when it is a real fact and even when it \
appears to be about a related topic. Before naming any market, report, or figure in your output, check which source it \
actually came from in THIS prompt; if you are not certain, do not use it. Respond with JSON only."""

WRITE_USER = """Rewrite the guest article below into a market-intelligence article that satisfies every requirement.

{sop}

ORIGINAL TITLE: {title}
ORIGINAL TOPIC: {topic_summary}
ANGLE: {bridge_angle}

FULL ORIGINAL ARTICLE (reference only — context for what the reader was promised; see the hard rule below on reusing \
its wording):
\"\"\"
{original_body}
\"\"\"

RETAINED PARAGRAPH (written by the paying guest author; it contains their backlink — this is the ONLY part of the \
original article above that is allowed to survive into your output):
\"\"\"{sponsor}\"\"\"

CMI REPORT: {report_name}
CMI REPORT CONTENT (authoritative source for all market figures, segments, regional data, and companies):
\"\"\"
{report_text}
\"\"\"

WHAT CMI PROVIDES (its own activities/offerings — use this vocabulary to frame the title and the report_context \
section as market intelligence CMI itself produces, not as a restatement of the original article's own angle): \
{cmi_keywords}

SUPPORTING WEB EVIDENCE (already vetted as real and on-topic by a prior editorial pass — your job now is placement, \
not re-vetting):
{evidence}

Rules:
- The FIRST section in "sections" must be an unheaded intro (heading: "") of exactly 3 paragraphs. Your job is to make \
  the retained paragraph (quoted above as RETAINED PARAGRAPH) read as if it belongs exactly where it lands — infused \
  into the intro seamlessly and smoothly, never as a sentence that looks dropped in or out of place. The retained \
  paragraph was originally written as a MID-ARTICLE paragraph in the guest post — it may open with a connective phrase, \
  refer back to something named earlier ("that structure", "these layers", "this approach"), or assume prior context \
  that no longer exists once it's moved here. You must construct paragraphs 1 and 3 specifically around it to close \
  that gap: read exactly what it says and how it opens and closes, then write the paragraph before and after it so \
  together the three read as one continuous, naturally flowing passage — not as an unrelated sentence stitched between \
  two generic ones:
  1. A lead-in paragraph you write, introducing the original topic and building up to whatever context the retained \
  paragraph's own opening words presume the reader already has. If the retained paragraph opens by naming a specific \
  tool, product, company, or comparison (e.g. it starts mid-comparison, mid-list, or with "This approach..." / "That \
  structure..." / a colon-led definition), you MUST name that same specific thing in this lead-in too — introduce it, \
  say what category it belongs to, and set up the comparison or point it's about to make. The retained paragraph must \
  never be the first place a name, tool, or comparison appears; by the time a reader reaches it, they already know what \
  it's talking about. You are not paraphrasing or quoting the retained paragraph's sentences — you are writing new \
  sentences that establish the same subject so its opening phrase lands on a clear antecedent instead of a non sequitur.
  2. The literal placeholder text {sponsor_token} and NOTHING else — do not write out the retained paragraph's actual \
  words here, do not paraphrase it, do not summarize it. Copy only this exact character sequence as the entire paragraph \
  string: {sponsor_token}
  This placeholder will be programmatically replaced with the retained paragraph afterward; whatever you put in its place \
  is what ends up BESIDE the real paragraph in the final article, so writing the real content here would duplicate it.
  3. A paragraph you write that picks up specifically from however the retained paragraph actually ends — not from a \
  generic restatement of paragraph 1 — and pivots smoothly from there to the broader market this article is now about, \
  opening the market-intelligence sections that follow.
  The retained paragraph's actual content (quoted above) must never appear anywhere in your output — not in the intro, \
  not elsewhere, not even partially reworded. The only place it belongs is the placeholder slot, and the placeholder is \
  the literal string {sponsor_token}, never the paragraph's real words. Do not mention the guest author's brand or \
  website anywhere in the article either.
- Every paragraph except the placeholder must be writing you compose fresh. Do not reuse a sentence, or any long run of \
wording, from the full original article shown above — not copied verbatim, not lightly edited, not moved to a different \
section. If the original article makes a point you also want to make (e.g. the same list of features or the same advice), \
express that point in your own words and sentence structure; reusing its actual phrasing is not allowed anywhere outside \
the one retained paragraph. The article should still follow on naturally from the original topic, so a reader arriving \
for that topic finds it addressed, then broadened into market intelligence — but every word of that beyond the retained \
paragraph must be new.
- Every market figure (sizes, CAGR, shares, years, percentages, demographic/segment shares) must come verbatim from the \
CMI REPORT CONTENT or the SUPPORTING WEB EVIDENCE shown above for THIS specific run — copy the number and unit exactly \
as given there, attached to the same segment/scope it was given for (a figure stated for one segment or one year is not \
interchangeable with another). Never invent, estimate, round, infer, or carry over a number from general knowledge, from \
a different market, or from the FULL ORIGINAL ARTICLE's own claims — the original article may cite real figures, but \
they are for whatever its own author wrote, and the CMI REPORT CONTENT for THIS run is the only authoritative source \
for figures in your output. If a section would otherwise need a figure that is not in either source, write the point \
qualitatively instead of inventing a number.
- The report_context section's first paragraph MUST contain exactly one [[CMI_LINK:anchor text]] written inline inside \
a sentence, where the anchor text is the market name given above as CMI REPORT: {report_name} — copy that exact name \
into the anchor text (e.g. "...according to Coherent Market Insights' [[CMI_LINK:{report_name}]] report..." is the \
pattern; do not invent a different anchor phrase). It must read as a citation, not an advertisement. \
The anchor text MUST be {report_name} exactly — never the name of a different market, and never a market name that \
happens to appear in the FULL ORIGINAL ARTICLE above (the original article may itself reference a different CMI \
report; that is irrelevant here — CMI REPORT: {report_name} is the only report this citation may ever name). This is \
a mandatory, structural requirement, not optional phrasing — the article is incomplete without it. Do not place \
[[CMI_LINK:...]] anywhere else, and do not include it more than once.
- Use every piece of SUPPORTING WEB EVIDENCE somewhere in the article, each in whichever section its own content \
actually fits — not wherever is convenient or wherever you happen to be writing when you remember it exists. Each \
evidence line is tagged with the section type it was pre-sorted for ([trends], [us], [players], [context]), but treat \
that tag as a starting hint, not a rule: read what the fact actually says and place it by its real subject matter even \
when that differs from its tag — a fact pre-tagged for one section can belong in a different one if its content is \
really about that other section's subject (for instance, a fact filed under one category may really be describing a \
specific company's move, a regional development, or a segment-level shift, in which case it belongs wherever that \
subject is actually discussed). Only leave a piece of evidence out entirely if using it would require distorting what \
it actually says or forcing it into a section it has no real connection to.
- This is a blog a reader follows start to finish — one continuous piece of writing that fuses the ORIGINAL ARTICLE's \
own idea and voice with the CMI report's factual substance, not a guest intro bolted onto a stitched-together set of \
independent report sections glued on after it. Where a later section can genuinely pick up a thread left by the one \
before it — a fact, an implication, a question it raised — open with that connection instead of a flat, disconnected \
topic sentence ("The market is segmented by..."). Do this only where the connection is real and would read naturally; \
forcing a callback into every single section produces the opposite of what this is asking for — repetitive, \
mechanical transitions are as much a tell as having none at all. Some sections opening on their own subject plainly is \
fine and expected; the goal is a piece that overall reads as one train of thought with a few genuine connective \
threads running through it, not a transition sentence bolted onto every section header.
- Headings must read as blog section headers for THIS specific article's subject, not as labels copied from a market \
research report's table of contents. Every section below still must satisfy its stated requirement (tag it in "sop" \
exactly as before — the heading text itself is unconstrained and separate from that tag), but phrase the heading the \
way a blog covering this exact topic would title that section: specific to what the section actually discusses here, \
in this article, not a generic category name. Do not simply reuse this prompt's own requirement names ("Key Players", \
"U.S. Market Analysis", "Market Segmentation") as headings verbatim — those name the underlying requirement for your \
reference, not the words a reader should see.
- Write in a register a reader can follow section to section without rereading: favor shorter, direct sentences over \
long ones stacking multiple clauses and qualifiers, and when a technical or market-specific term first appears, give \
enough context that an unfamiliar reader can follow what it means without stopping. This applies throughout, including \
the trends, segmentation, and key_players sections where dense jargon-stacking is most tempting — precision and \
plain language are not in conflict; a claim is not more rigorous for being harder to parse.
- Satisfy all six requirements below plus a closing conclusion, each in its own section with a descriptive heading, in \
this order: trends, report_context, segmentation, subsegment, country_us, key_players, conclusion. Tag every paragraph \
in "sop" with every requirement it satisfies (a paragraph may satisfy more than one, or none for connective/intro paragraphs).
  - trends: three distinct trends, each its own paragraph of at least 60 words (3 paragraphs minimum), each naming the \
  trend and explaining its impact on the market, not just describing it.
  - report_context: at least two paragraphs (not one) integrating the CMI report's framing of the market naturally into \
  the discussion — the market's overall size/growth story, then its key dynamics (drivers, demand factors), as two \
  distinct paragraphs rather than compressing both into one. One of these two paragraphs must also explicitly connect \
  this market's trajectory back to ANGLE/ORIGINAL TOPIC given above — not a generic restatement of the market's \
  importance, but the specific implication of this report's figures and dynamics for the original article's own subject \
  and reader (e.g. what this market's growth or dynamics concretely mean for the kind of reader, product, or decision the \
  original article was about). This is the one thing a reader cannot get from the CMI report alone — the report has no \
  knowledge of the original article's subject, so this connection must be written fresh, grounded in the report's actual \
  figures/dynamics, not asserted vaguely.
  - segmentation: at least two paragraphs (not one): one paragraph naming and briefly explaining the report's major \
  segments, and a second paragraph on how the sub-segments within those relate to each other and to the market as a whole.
  - subsegment: exactly two paragraphs of at least 80 words each, each a detailed analysis of one sub-segment from two \
  DIFFERENT major segments (e.g. one from "by type" and one from "by application" — not two sub-segments of the same \
  segment). Each paragraph must cover: growth/adoption drivers, major applications/end-use cases, and its contribution \
  to the overall market.
  - country_us: exactly one section, 100-200 words total across its paragraph(s), on U.S. market factors, growth drivers, \
  adoption trends, and relevant industry developments.
  - key_players: at least two paragraphs (not one): one introducing the competitive landscape generally, and a second \
  naming at least 5 specific companies and connecting each to its products, technology, or strategy.
  - conclusion: exactly one paragraph closing the article: synthesize the market's overall trajectory and what it implies \
  going forward, grounded only in points already made above. Do not introduce new figures, companies, or claims here, and \
  do not address the reader directly or add a call to action — it closes the analysis, it does not sell anything.
- New title: professional and market-intelligence framed, but still clearly connected to the original topic; 40-75 \
characters — count the characters before finalizing and shorten if it runs long. Frame it using the kind of language \
in WHAT CMI PROVIDES below (e.g. as market intelligence/analysis/research on the subject, not as a how-to or opinion \
piece) — do not just reuse the original title's own phrasing or angle.
- Total length: 1,400-1,700 words. This requires roughly 13-15 paragraphs across the sections above at the per-section \
minimums stated — if your draft is shorter, you have not met the per-section minimums; go back and add the missing \
paragraphs rather than reaching the target by padding existing sentences with filler words.

Return JSON:
{{"title": "<new title>",
  "sections": [{{"heading": "<heading, or empty string for the intro>",
                "sop": ["<zero or more of: trends, report_context, segmentation, subsegment, country_us, key_players, conclusion>"],
                "paragraphs": ["<paragraph>", "..."]}}]}}"""
